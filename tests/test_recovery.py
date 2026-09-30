"""Inject lost responses and unavailable services at the HTTP transport boundary."""
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.coordinator import attach_coordinator, Settings
from backend.participants import attach_participant
from backend.storage import Store


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devoss-recovery-")
        self.stores = {}
        self.transports = {}
        self.unavailable = set()
        self.lose_response = set()
        for role in ("reservations", "payments", "confirmations"):
            self.stores[role] = Store(Path(self.temp.name) / f"{role}.db")
            participant = FastAPI()
            attach_participant(participant, role, self.stores[role])
            self.transports[role] = httpx.ASGITransport(app=participant)

        async def transport(request):
            role = request.url.host
            if role in self.unavailable:
                raise httpx.ConnectError("Injected service outage", request=request)
            response = await self.transports[role].handle_async_request(request)
            key = (role, request.url.path)
            if key in self.lose_response:
                self.lose_response.remove(key)
                await response.aread()
                await response.aclose()
                raise httpx.ReadTimeout("Response lost after commit", request=request)
            return response

        self.remote = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.app = FastAPI()
        self.stores["coordinator"] = Store(Path(self.temp.name) / "coordinator.db")
        attach_coordinator(self.app, self.stores["coordinator"])
        self.app.state.client = self.remote
        self.app.state.settings = Settings(payment_delay_ms=0, step_delay_ms=0, rate_limit=False)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://coordinator")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.remote.aclose()
        for transport in self.transports.values():
            await transport.aclose()
        for store in self.stores.values():
            store.db.close()
        self.temp.cleanup()

    async def test_lost_charge_response_does_not_duplicate_payment(self):
        self.lose_response.add(("payments", "/charge"))
        result = await self.client.post("/api/purchases", json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "confirmed")
        self.assertEqual(self.stores["reservations"].catalog()[0]["stock"], 199)
        with self.stores["payments"].connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*),SUM(amount) FROM operations").fetchone()[:], (1, 28000))

    async def test_lost_confirmation_response_does_not_refund_confirmed_purchase(self):
        self.lose_response.add(("confirmations", "/confirm"))
        result = (await self.client.post("/api/purchases", json={})).json()
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.stores["payments"].operation(result["id"])["status"], "charged")
        self.assertFalse(any(e["step"] == "refund" for e in result["events"]))

    async def test_unavailable_confirmation_stays_pending_until_reconciliation(self):
        self.unavailable.add("confirmations")
        result = (await self.client.post("/api/purchases", json={})).json()
        self.assertEqual(result["status"], "compensation_pending")
        self.assertEqual(self.stores["reservations"].catalog()[0]["stock"], 199)
        self.assertEqual(self.stores["payments"].operation(result["id"])["status"], "charged")
        self.unavailable.clear()
        result = (await self.client.post(f"/api/purchases/{result['id']}/retry")).json()
        self.assertEqual(result["status"], "compensated")
        self.assertEqual(self.stores["reservations"].catalog()[0]["stock"], 200)
        # The cancellation decision blocks a delayed confirmation from committing.
        late = await self.remote.post("http://confirmations/confirm", json={"purchase_id": result["id"]})
        self.assertEqual(late.status_code, 409)

    async def test_abort_cannot_cancel_an_already_confirmed_purchase(self):
        result = (await self.client.post("/api/purchases", json={})).json()
        response = await self.remote.post("http://confirmations/abort", json={"purchase_id": result["id"]})
        self.assertEqual(response.json()["status"], "confirmed")

"""Integration tests over real HTTP, isolated databases and four processes."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
import uuid

import httpx

from scripts.local import launch, stop


class SystemTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="devoss-tests-")
        cls.port = int(os.getenv("TEST_BASE_PORT", "18100"))
        cls.processes, cls.logs = launch(cls.port, Path(cls.temp.name) / "data", Path(cls.temp.name) / "logs")

    @classmethod
    def tearDownClass(cls):
        stop(cls.processes, cls.logs)
        cls.temp.cleanup()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.port}", timeout=35)
        response = await self.client.post("/api/reset")
        self.assertEqual(response.status_code, 200)
        response = await self.client.put("/api/settings", json={"payment_delay_ms": 0, "step_delay_ms": 0, "rate_limit": False, "bulkhead": True})
        self.assertEqual(response.status_code, 200)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def participant(self, port_offset, path, body=None):
        url = f"http://127.0.0.1:{self.port + port_offset}{path}"
        return await (self.client.get(url) if body is None else self.client.post(url, json=body))

    async def stock(self):
        return (await self.client.get("/api/catalog")).json()[0]["stock"]

    async def test_success_and_concurrent_idempotency(self):
        payload = {"purchase_id": str(uuid.uuid4()), "event_id": "neon"}
        responses = await asyncio.gather(*(self.client.post("/api/purchases", json=payload) for _ in range(8)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(await self.stock(), 199)
        self.assertEqual(len((await self.client.get("/api/purchases")).json()), 1)
        evidence = (await self.client.get(f"/api/purchases/{payload['purchase_id']}/evidence")).json()
        self.assertEqual(evidence["payments"]["status"], "charged")
        self.assertEqual(evidence["confirmations"]["status"], "confirmed")
        changed = await self.client.post("/api/purchases", json={**payload, "event_id": "indie"})
        self.assertEqual(changed.status_code, 409)

    async def test_confirmation_failure_compensates_in_reverse(self):
        result = (await self.client.post("/api/purchases", json={"fail_confirmation": True})).json()
        self.assertEqual(result["status"], "compensated")
        self.assertEqual(await self.stock(), 200)
        steps = [e["step"] for e in result["events"] if e["status"] == "compensated"]
        self.assertEqual(steps, ["refund", "release"])
        evidence = (await self.client.get(f"/api/purchases/{result['id']}/evidence")).json()
        self.assertEqual(evidence["payments"]["status"], "refunded")
        self.assertEqual(evidence["reservations"]["status"], "cancelled")
        for _ in range(3):
            await self.participant(1, "/release", {"purchase_id": result["id"]})
            await self.participant(2, "/refund", {"purchase_id": result["id"]})
        self.assertEqual(await self.stock(), 200)

    async def test_failed_compensation_can_be_retried(self):
        result = (await self.client.post("/api/purchases", json={"fail_confirmation": True, "fail_refund": True})).json()
        self.assertEqual(result["status"], "compensation_pending")
        self.assertEqual(await self.stock(), 199)
        response = await self.client.post(f"/api/purchases/{result['id']}/retry")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "compensated")
        self.assertEqual(await self.stock(), 200)

    async def test_last_ticket_is_atomic(self):
        for i in range(199):
            response = await self.participant(1, "/reserve", {"purchase_id": f"seed-{i}"})
            self.assertEqual(response.status_code, 200)
        replies = await asyncio.gather(*(self.participant(1, "/reserve", {"purchase_id": f"last-{i}"}) for i in range(12)))
        self.assertEqual(sum(r.status_code == 200 for r in replies), 1)
        self.assertEqual(sum(r.status_code == 409 for r in replies), 11)
        self.assertEqual(await self.stock(), 0)
        purchase = (await self.client.post("/api/purchases", json={})).json()
        self.assertEqual(purchase["status"], "compensated")
        self.assertEqual(await self.stock(), 0)

    async def test_refund_blocks_a_late_charge(self):
        key = str(uuid.uuid4())
        charging = asyncio.create_task(self.participant(2, "/charge", {"purchase_id": key, "delay_ms": 400, "amount": 28000}))
        await asyncio.sleep(.12)
        await self.participant(2, "/refund", {"purchase_id": key})
        response = await charging
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.participant(2, f"/operations/{key}")).json()["status"], "refunded")

    async def test_rate_limit_rejects_with_retry_after(self):
        await self.client.put("/api/settings", json={"payment_delay_ms": 500, "step_delay_ms": 0, "rate_limit": True, "bulkhead": False})
        responses = await asyncio.gather(*(self.client.post("/api/purchases", json={}, headers={"X-Demo-Client": "one-client"}) for _ in range(50)))
        rejected = [r for r in responses if r.status_code == 429]
        self.assertGreater(len(rejected), 0)
        self.assertTrue(all(r.headers["Retry-After"] == "1" for r in rejected))
        self.assertTrue(all(r.status_code in (200, 429) for r in responses))
        await asyncio.sleep(1.1)
        self.assertEqual((await self.client.post("/api/purchases", json={}, headers={"X-Demo-Client": "one-client"})).status_code, 200)

    async def test_bulkhead_queue_and_catalog_isolation(self):
        await self.client.put("/api/settings", json={"payment_delay_ms": 700, "step_delay_ms": 0, "rate_limit": False, "bulkhead": True})
        calls = [asyncio.create_task(self.client.post("/api/purchases", json={})) for _ in range(35)]
        observed = []
        while not all(task.done() for task in calls):
            self.assertEqual((await self.client.get("/api/catalog")).status_code, 200)
            observed.append((await self.client.get("/api/metrics")).json()["payments"])
            await asyncio.sleep(.1)
        replies = await asyncio.gather(*calls)
        self.assertGreater(sum(r.status_code == 503 for r in replies), 0)
        self.assertTrue(all(r.status_code in (200, 503) for r in replies))
        self.assertTrue(all(m["active"] <= 3 and m["waiting"] <= 5 for m in observed))
        final = (await self.client.get("/api/metrics")).json()["payments"]
        self.assertEqual(final["peak_active"], 3)
        self.assertEqual(final["peak_waiting"], 5)
        self.assertEqual(await self.stock(), 200 - sum(r.status_code == 200 for r in replies))

    async def test_reset_and_settings_blocked_during_purchase(self):
        await self.client.put("/api/settings", json={"payment_delay_ms": 500, "step_delay_ms": 0, "rate_limit": False, "bulkhead": True})
        task = asyncio.create_task(self.client.post("/api/purchases", json={}))
        await asyncio.sleep(.12)
        self.assertEqual((await self.client.post("/api/reset")).status_code, 409)
        self.assertEqual((await self.client.put("/api/settings", json={})).status_code, 409)
        await task
        self.assertEqual((await self.client.post("/api/reset")).status_code, 200)

    async def test_load_endpoint_and_checks(self):
        for scenario in ("rate", "bulkhead", "backpressure", "unprotected"):
            with self.subTest(scenario=scenario):
                await self.client.post("/api/reset")
                result = await self.client.post("/api/load", json={"scenario": scenario, "count": 20, "payment_delay_ms": 500})
                self.assertEqual(result.status_code, 202)
                run = result.json()
                for _ in range(150):
                    await asyncio.sleep(.2)
                    run = (await self.client.get(f"/api/load/{run['id']}")).json()
                    if run["status"] != "running":
                        break
                self.assertEqual(run["status"], "completed", run)
                self.assertTrue(all(run["checks"].values()), run)
                self.assertEqual(await self.stock(), 200 - run["confirmed"])


if __name__ == "__main__":
    unittest.main()

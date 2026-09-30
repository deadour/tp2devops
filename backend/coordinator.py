import asyncio
import math
import os
import time
import uuid
from collections import Counter
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.protection import RateLimiter
from backend.storage import EVENTS, now


class Settings(BaseModel):
    payment_delay_ms: Annotated[int, Field(ge=0, le=4000)] = 1200
    step_delay_ms: Annotated[int, Field(ge=0, le=1000)] = 350
    rate_limit: bool = True
    bulkhead: bool = True


class Purchase(BaseModel):
    purchase_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_id: Literal["neon", "indie", "jazz"] = "neon"
    fail_confirmation: bool = False
    fail_refund: bool = False


class LoadRequest(BaseModel):
    scenario: Literal["rate", "bulkhead", "backpressure", "unprotected"] = "rate"
    count: Annotated[int, Field(ge=10, le=60)] = 50
    payment_delay_ms: Annotated[int, Field(ge=500, le=4000)] = 2000


class RemoteFailure(Exception):
    def __init__(self, reason, code=503, uncertain=False):
        self.reason, self.code, self.uncertain = reason, code, uncertain


def attach_coordinator(app: FastAPI, store):
    state = app.state
    state.settings = Settings()
    state.tasks = {}
    state.load_task = None
    state.run = None
    state.resetting = False
    state.limiter = RateLimiter()
    state.counts = Counter()
    urls = {name: os.getenv(f"{name.upper()}_URL", f"http://{name}:8000") for name in ("reservations", "payments", "confirmations")}

    async def get_remote(service, path):
        try:
            response = await state.client.get(urls[service] + path, timeout=2)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteFailure(f"{service}_unavailable") from exc

    async def call(service, action, command):
        # Transport errors are ambiguous: inspect actual state before retrying.
        expected = {"reserve": "reserved", "release": "cancelled", "charge": "charged", "refund": "refunded", "confirm": "confirmed", "abort": "cancelled"}[action]
        for attempt in range(3):
            try:
                response = await state.client.post(urls[service] + "/" + action, json=command)
                if response.is_success:
                    return response.json()
                reason = response.json().get("detail", "remote_error")
                if reason in {"bulkhead_full", "queue_timeout", "sold_out", "confirmation_failed_injected"} or response.status_code < 500:
                    raise RemoteFailure(reason, response.status_code)
                if attempt == 2:
                    raise RemoteFailure(reason, response.status_code)
            except (httpx.HTTPError, ValueError):
                try:
                    actual = await get_remote(service, f"/operations/{command['purchase_id']}")
                    if actual["status"] == expected:
                        return actual
                except RemoteFailure:
                    pass
                if attempt == 2:
                    raise RemoteFailure(f"{service}_unknown", uncertain=True)
            await asyncio.sleep(0.15 * (attempt + 1))

    async def compensate(purchase_id, body, options, retry=False):
        purchase = store.purchase(purchase_id)
        attempted = {e["step"] for e in purchase["events"] if e["status"] == "running"}
        command = {"purchase_id": purchase_id, "event_id": body["event_id"]}
        # If confirmation was attempted, don't refund a possibly confirmed order.
        if "confirmation" in attempted:
            try:
                actual = await get_remote("confirmations", f"/operations/{purchase_id}")
                if actual["status"] != "confirmed":
                    actual = await call("confirmations", "abort", command)
                if actual["status"] == "confirmed":
                    store.log(purchase_id, "confirmation", "success", "Confirmación verificada tras respuesta incierta")
                    store.status(purchase_id, "confirmed")
                    return True
            except RemoteFailure:
                store.log(purchase_id, "compensation", "failed", "No se pudo verificar la confirmación. Requiere reintento.")
                store.status(purchase_id, "compensation_pending", 503)
                return False
        store.status(purchase_id, "compensating", 503)
        for source, step, service, action, message in (
            ("payment", "refund", "payments", "refund", "Pago reembolsado; no hay un cobro vigente"),
            ("reservation", "release", "reservations", "release", "Reserva liberada; stock restaurado"),
        ):
            if source not in attempted:
                continue
            store.log(purchase_id, step, "running", "Ejecutando acción compensatoria")
            await asyncio.sleep(options.step_delay_ms / 1000)
            try:
                await call(service, action, {**command, "fail": action == "refund" and body["fail_refund"] and not retry})
                store.log(purchase_id, step, "compensated", message)
            except RemoteFailure as exc:
                store.log(purchase_id, step, "failed", exc.reason)
                store.status(purchase_id, "compensation_pending", 503)
                return False
        store.status(purchase_id, "compensated", purchase["http_code"] if purchase["http_code"] != 200 else 409)
        return True

    async def saga(purchase_id, body, options):
        event = next(e for e in EVENTS if e["id"] == body["event_id"])
        command = {"purchase_id": purchase_id, "event_id": body["event_id"], "amount": event["price"], "delay_ms": options.payment_delay_ms, "bulkhead": options.bulkhead}
        for step, service, action, message in (
            ("reservation", "reservations", "reserve", "Entrada reservada; stock descontado"),
            ("payment", "payments", "charge", "Pago registrado"),
            ("confirmation", "confirmations", "confirm", "Compra confirmada. ¡Nos vemos en el show!"),
        ):
            store.log(purchase_id, step, "running", "Esperando respuesta del servicio")
            await asyncio.sleep(options.step_delay_ms / 1000)
            try:
                await call(service, action, {**command, "fail": step == "confirmation" and body["fail_confirmation"]})
                store.log(purchase_id, step, "success", message)
            except RemoteFailure as exc:
                store.log(purchase_id, step, "failed", exc.reason)
                store.status(purchase_id, "failed", exc.code)
                await compensate(purchase_id, body, options)
                return store.purchase(purchase_id)
        store.status(purchase_id, "confirmed")
        return store.purchase(purchase_id)

    async def tracked(purchase_id, coroutine):
        try:
            return await coroutine
        except Exception:
            store.log(purchase_id, "compensation", "failed", "Error inesperado: revisar logs y reintentar compensación")
            store.status(purchase_id, "compensation_pending", 503)
            import logging
            logging.exception("Saga %s failed", purchase_id)
            return store.purchase(purchase_id)
        finally:
            state.tasks.pop(purchase_id, None)

    def ensure_idle():
        if state.tasks or (state.load_task and not state.load_task.done()) or state.resetting:
            raise HTTPException(409, "Esperá a que terminen las operaciones activas")

    @app.get("/api/catalog")
    async def catalog():
        try:
            return await get_remote("reservations", "/catalog")
        except RemoteFailure as exc:
            raise HTTPException(503, exc.reason)

    @app.get("/api/purchases")
    async def purchases():
        return store.purchases()

    @app.get("/api/purchases/{purchase_id}")
    async def purchase(purchase_id: uuid.UUID):
        found = store.purchase(str(purchase_id))
        if not found:
            raise HTTPException(404, "Compra inexistente")
        return found

    @app.get("/api/purchases/{purchase_id}/evidence")
    async def evidence(purchase_id: uuid.UUID):
        result = {}
        for service in urls:
            try:
                result[service] = await get_remote(service, f"/operations/{purchase_id}")
            except RemoteFailure:
                result[service] = {"status": "unavailable"}
        return result

    @app.post("/api/purchases")
    async def buy(body: Purchase, request: Request):
        if state.resetting:
            raise HTTPException(503, "Restablecimiento en curso")
        purchase_id = str(body.purchase_id)
        payload = body.model_dump(mode="json")
        previous = store.purchase(purchase_id)
        if previous:
            if previous["body"] != payload:
                raise HTTPException(409, "idempotency_conflict")
            if purchase_id in state.tasks:
                previous = await asyncio.shield(state.tasks[purchase_id])
            return JSONResponse(previous, status_code=previous["http_code"])
        # Explicit demo identity, not an authentication or production security boundary.
        key = request.headers.get("X-Demo-Client", request.client.host if request.client else "demo")[:100]
        if state.settings.rate_limit and not state.limiter.allow(key):
            state.counts["rate_rejected"] += 1
            return JSONResponse({"detail": "rate_limit", "message": "Demasiadas solicitudes; reintentá en 1 segundo"}, status_code=429, headers={"Retry-After": "1"})
        state.counts["admitted"] += 1
        store.create_purchase(purchase_id, payload)
        options = state.settings.model_copy()
        task = asyncio.create_task(tracked(purchase_id, saga(purchase_id, payload, options)))
        state.tasks[purchase_id] = task
        result = await asyncio.shield(task)
        headers = {"Retry-After": "1"} if result["http_code"] == 503 else {}
        return JSONResponse(result, status_code=result["http_code"], headers=headers)

    @app.post("/api/purchases/{purchase_id}/retry")
    async def retry(purchase_id: uuid.UUID):
        if state.resetting:
            raise HTTPException(409, "Restablecimiento en curso")
        key = str(purchase_id)
        found = store.purchase(key)
        if not found:
            raise HTTPException(404, "Compra inexistente")
        if key in state.tasks:
            raise HTTPException(409, "La compra sigue activa")
        if found["status"] != "compensation_pending":
            raise HTTPException(409, "No hay compensaciones pendientes")

        async def run_retry():
            await compensate(key, found["body"], state.settings.model_copy(), retry=True)
            return store.purchase(key)

        state.tasks[key] = asyncio.create_task(tracked(key, run_retry()))
        result = await asyncio.shield(state.tasks[key])
        return JSONResponse(result, status_code=200 if result["status"] in {"compensated", "confirmed"} else 503)

    @app.get("/api/settings")
    async def settings():
        return state.settings

    @app.put("/api/settings")
    async def configure(body: Settings):
        ensure_idle()
        state.settings = body
        state.limiter = RateLimiter()
        return body

    @app.get("/api/metrics")
    async def metrics():
        services = {}
        async def check(name):
            try:
                await get_remote(name, "/health")
                services[name] = "ok"
            except RemoteFailure:
                services[name] = "down"
        await asyncio.gather(*(check(name) for name in urls))
        try:
            payment = await get_remote("payments", "/metrics")
        except RemoteFailure:
            payment = None
        return {"services": {"coordinator": "ok", **services}, "payments": payment, "active_purchases": len(state.tasks), "counts": state.counts, "settings": state.settings.model_dump(), "run": state.run, "resetting": state.resetting}

    @app.post("/api/reset")
    async def reset():
        ensure_idle()
        # Verify reachability before doing a best-effort multi-service demo reset.
        state.resetting = True
        try:
            await asyncio.gather(*(get_remote(name, "/health") for name in urls))
            for name in urls:
                response = await state.client.post(urls[name] + "/reset")
                response.raise_for_status()
            store.reset()
            state.counts.clear()
            state.limiter = RateLimiter()
            state.run = None
            state.settings = Settings()
            return {"ok": True}
        except (RemoteFailure, httpx.HTTPError):
            raise HTTPException(503, "No se pudo restablecer todo; recuperá los servicios y repetí el restablecimiento")
        finally:
            state.resetting = False

    async def run_load(body, run):
        previous_settings = state.settings
        state.settings = Settings(payment_delay_ms=body.payment_delay_ms, step_delay_ms=0, rate_limit=body.scenario == "rate", bulkhead=body.scenario in {"bulkhead", "backpressure"})
        state.limiter = RateLimiter()
        base = os.getenv("SELF_URL", "http://127.0.0.1:8000")
        start = time.monotonic()
        latencies = []
        statuses = Counter()
        finished = asyncio.Event()
        try:
            initial = await get_remote("payments", "/metrics")
            # Per-run peaks are sampled; participant lifetime peaks remain separate.
            run["baseline_rejected"] = initial["rejected"]
            async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=100)) as client:
                async def monitor():
                    while not finished.is_set():
                        tick = time.monotonic()
                        sample = {"second": round(tick - start, 1)}
                        try:
                            response = await client.get(base + "/api/catalog", timeout=3)
                            sample["catalog_ok"] = response.is_success
                            sample["latency_ms"] = round((time.monotonic() - tick) * 1000, 1)
                        except httpx.HTTPError:
                            sample.update(catalog_ok=False, latency_ms=3000)
                        try:
                            metric = await get_remote("payments", "/metrics")
                            sample.update(active=metric["active"], waiting=metric["waiting"])
                            run["peak_active"] = max(run["peak_active"], metric["active"])
                            run["peak_waiting"] = max(run["peak_waiting"], metric["waiting"])
                        except RemoteFailure:
                            sample.update(active=0, waiting=0)
                        run["samples"].append(sample)
                        await asyncio.sleep(0.2)

                async def send(index):
                    begun = time.monotonic()
                    for attempt in range(4 if body.scenario == "backpressure" else 1):
                        try:
                            response = await client.post(base + "/api/purchases", json={"event_id": "neon"}, headers={"X-Demo-Client": run["id"]})
                            code = response.status_code
                            statuses[str(code)] += 1
                            run["http_statuses"] = dict(statuses)
                            run["attempts"] += 1
                            data = response.json()
                            is_overload = code == 503 and any(e["message"] in {"bulkhead_full", "queue_timeout"} for e in data.get("events", []))
                            # A compensated purchase is terminal. A retry starts a NEW purchase.
                            if body.scenario == "backpressure" and is_overload and data.get("status") == "compensated" and attempt < 3:
                                run["retries"] += 1
                                await asyncio.sleep(float(response.headers.get("Retry-After", 1)) * (attempt + 1) + (index % 7) * 0.07)
                                continue
                            if code == 200 and data.get("status") == "confirmed":
                                run["confirmed"] += 1
                            else:
                                run["rejected"] += 1
                        except (httpx.HTTPError, ValueError):
                            run["errors"] += 1
                        break
                    latencies.append((time.monotonic() - begun) * 1000)
                    run["completed"] += 1

                monitor_task = asyncio.create_task(monitor())
                try:
                    await asyncio.gather(*(send(i) for i in range(body.count)))
                finally:
                    finished.set()
                    await monitor_task
            run["status"] = "completed"
            run["duration_seconds"] = round(time.monotonic() - start, 2)
            run["p95_ms"] = round(sorted(latencies)[max(0, math.ceil(len(latencies) * .95) - 1)], 1)
            healthy = bool(run["samples"]) and all(s["catalog_ok"] for s in run["samples"])
            run["checks"] = {"catalog_available": healthy, "all_requests_finished": run["completed"] == body.count and run["errors"] == 0}
            if body.scenario == "rate":
                run["checks"]["http_429_observed"] = statuses["429"] > 0
            if body.scenario in {"bulkhead", "backpressure"}:
                run["checks"].update(concurrency_bounded=0 < run["peak_active"] <= 3, queue_bounded=run["peak_waiting"] <= 5, overload_observed=statuses["503"] > 0)
            if body.scenario == "unprotected":
                run["checks"]["concurrency_exceeds_three"] = run["peak_active"] > 3
        except Exception as exc:
            run.update(status="failed", error=str(exc) or type(exc).__name__)
        finally:
            state.settings = previous_settings
            state.limiter = RateLimiter()

    @app.post("/api/load", status_code=202)
    async def load(body: LoadRequest):
        ensure_idle()
        state.run = {"id": str(uuid.uuid4()), "scenario": body.scenario, "count": body.count, "status": "running", "created_at": now(), "completed": 0, "confirmed": 0, "rejected": 0, "errors": 0, "attempts": 0, "retries": 0, "peak_active": 0, "peak_waiting": 0, "samples": [], "http_statuses": {}, "checks": {}}
        state.load_task = asyncio.create_task(run_load(body, state.run))
        return state.run

    @app.get("/api/load/{run_id}")
    async def load_result(run_id: str):
        if not state.run or state.run["id"] != run_id:
            raise HTTPException(404, "Ejecución inexistente; se conserva la última")
        return state.run

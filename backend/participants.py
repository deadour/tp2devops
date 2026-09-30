import asyncio
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.protection import Bulkhead


class Operation(BaseModel):
    purchase_id: Annotated[str, Field(min_length=1, max_length=80)]
    event_id: str = "neon"
    amount: Annotated[int, Field(ge=0)] = 0
    delay_ms: Annotated[int, Field(ge=0, le=4000)] = 0
    bulkhead: bool = True
    fail: bool = False


def attach_participant(app: FastAPI, role, store):
    bulkhead = Bulkhead()
    app.state.bulkhead = bulkhead

    @app.get("/operations/{purchase_id}")
    async def operation(purchase_id: str):
        return store.operation(purchase_id) or {"id": purchase_id, "status": "absent"}

    @app.get("/metrics")
    async def metrics():
        return bulkhead.metrics()

    @app.post("/reset")
    async def reset():
        if bulkhead.active or bulkhead.waiting:
            raise HTTPException(409, "Hay pagos activos")
        store.reset()
        bulkhead.peak_active = bulkhead.peak_waiting = bulkhead.rejected = 0
        return {"ok": True}

    if role == "reservations":
        @app.get("/catalog")
        async def catalog():
            return store.catalog()

        @app.post("/reserve")
        async def reserve(command: Operation):
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT * FROM operations WHERE id=?", (command.purchase_id,)).fetchone()
                if previous:
                    if previous["status"] == "cancelled":
                        raise HTTPException(409, "reservation_cancelled")
                    if previous["event_id"] != command.event_id:
                        raise HTTPException(409, "idempotency_conflict")
                    return dict(previous)
                updated = db.execute("UPDATE inventory SET stock=stock-1 WHERE id=? AND stock>0", (command.event_id,))
                if not updated.rowcount:
                    raise HTTPException(409, "sold_out")
                db.execute("INSERT INTO operations VALUES (?,?,?,?)", (command.purchase_id, command.event_id, 0, "reserved"))
            return store.operation(command.purchase_id)

        @app.post("/release")
        async def release(command: Operation):
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT * FROM operations WHERE id=?", (command.purchase_id,)).fetchone()
                if previous and previous["status"] == "reserved":
                    db.execute("UPDATE inventory SET stock=stock+1 WHERE id=?", (previous["event_id"],))
                # A tombstone also blocks a late reserve after an ambiguous timeout.
                db.execute("INSERT INTO operations VALUES (?,?,0,'cancelled') ON CONFLICT(id) DO UPDATE SET status='cancelled'", (command.purchase_id, command.event_id))
            return store.operation(command.purchase_id)

    if role == "payments":
        @app.post("/charge")
        async def charge(command: Operation):
            previous = store.operation(command.purchase_id)
            if previous:
                if previous["status"] == "refunded":
                    raise HTTPException(409, "payment_refunded")
                if previous["amount"] != command.amount or previous["event_id"] != command.event_id:
                    raise HTTPException(409, "idempotency_conflict")
                return previous
            async with bulkhead.slot(command.bulkhead):
                await asyncio.sleep(command.delay_ms / 1000)
                with store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    previous = db.execute("SELECT * FROM operations WHERE id=?", (command.purchase_id,)).fetchone()
                    if previous:
                        if previous["status"] == "refunded":
                            raise HTTPException(409, "payment_refunded")
                        if previous["amount"] != command.amount or previous["event_id"] != command.event_id:
                            raise HTTPException(409, "idempotency_conflict")
                        return dict(previous)
                    db.execute("INSERT INTO operations VALUES (?,?,?,'charged')", (command.purchase_id, command.event_id, command.amount))
            return store.operation(command.purchase_id)

        @app.post("/refund")
        async def refund(command: Operation):
            if command.fail:
                raise HTTPException(503, "refund_unavailable_injected")
            # Compensation has independent capacity; it never waits for a charge slot.
            with store.connect() as db:
                db.execute("INSERT INTO operations VALUES (?,?,?,'refunded') ON CONFLICT(id) DO UPDATE SET status='refunded'", (command.purchase_id, command.event_id, 0))
            return store.operation(command.purchase_id)

    if role == "confirmations":
        @app.post("/confirm")
        async def confirm(command: Operation):
            previous = store.operation(command.purchase_id)
            if previous:
                if previous["status"] == "cancelled":
                    raise HTTPException(409, "confirmation_cancelled")
                return previous
            if command.fail:
                raise HTTPException(503, "confirmation_failed_injected")
            with store.connect() as db:
                db.execute("INSERT OR IGNORE INTO operations VALUES (?,?,0,'confirmed')", (command.purchase_id, command.event_id))
            return store.operation(command.purchase_id)

        @app.post("/abort")
        async def abort(command: Operation):
            # Atomic decision: either confirmation already won, or future confirms
            # are blocked. An absent GET alone cannot close the timeout race.
            with store.connect() as db:
                db.execute("INSERT OR IGNORE INTO operations VALUES (?,?,0,'cancelled')", (command.purchase_id, command.event_id))
            return store.operation(command.purchase_id)

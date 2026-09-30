import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.storage import Store
from backend.participants import attach_participant


def create_app(role=None, data_dir=None):
    role = role or os.getenv("SERVICE_ROLE", "coordinator")
    if role not in {"coordinator", "reservations", "payments", "confirmations"}:
        raise ValueError(f"Unknown role: {role}")
    store = Store(Path(data_dir or os.getenv("DATA_DIR", ".local/data")) / f"{role}.db")

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=25, limits=httpx.Limits(max_connections=120)) as client:
            app.state.client = client
            yield
            if role == "coordinator" and hasattr(app.state, "tasks"):
                import asyncio
                pending = list(app.state.tasks.values())
                if app.state.load_task:
                    pending.append(app.state.load_task)
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            store.db.close()

    app = FastAPI(title=f"DevOss Tickets · {role}", lifespan=lifespan)
    app.state.store = store

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": role}

    if role == "coordinator":
        pass  # La orquestación se incorpora en el siguiente paso.
    else:
        attach_participant(app, role, store)
    return app


app = create_app()

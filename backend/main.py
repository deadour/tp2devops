"""Punto de partida del coordinador de DevOss Tickets."""
from fastapi import FastAPI

app = FastAPI(title="DevOss Tickets", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "coordinator"}

"""Reproducible HTTP checks. Resets demo data before each scenario."""
import argparse
import asyncio
import json
import os
import time
import uuid

import httpx


async def check_saga(client, fail):
    response = await client.post("/api/reset")
    response.raise_for_status()
    before = (await client.get("/api/catalog")).json()[0]["stock"]
    payload = {"purchase_id": str(uuid.uuid4()), "event_id": "neon", "fail_confirmation": fail}
    response = await client.post("/api/purchases", json=payload)
    purchase = response.json()
    assert purchase["status"] == ("compensated" if fail else "confirmed"), purchase
    evidence = (await client.get(f"/api/purchases/{purchase['id']}/evidence")).json()
    after = (await client.get("/api/catalog")).json()[0]["stock"]
    assert after == before - (0 if fail else 1), (before, after)
    assert evidence["payments"]["status"] == ("refunded" if fail else "charged"), evidence
    assert evidence["reservations"]["status"] == ("cancelled" if fail else "reserved"), evidence
    repeated = (await client.post("/api/purchases", json=payload)).json()
    assert repeated["id"] == purchase["id"]
    assert (await client.get("/api/catalog")).json()[0]["stock"] == after
    print(json.dumps({"scenario": "failure" if fail else "success", "status": "PASS", "stock_before": before, "stock_after": after, "evidence": evidence}, ensure_ascii=False, indent=2))


async def check_load(client, scenario, count=50, delay=2000):
    response = await client.post("/api/reset")
    response.raise_for_status()
    response = await client.post("/api/load", json={"scenario": scenario, "count": count, "payment_delay_ms": delay})
    response.raise_for_status()
    run = response.json()
    deadline = time.monotonic() + 120
    while run["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(.3)
        response = await client.get(f"/api/load/{run['id']}")
        response.raise_for_status()
        run = response.json()
    assert run["status"] == "completed", run
    assert run["checks"] and all(run["checks"].values()), run
    remaining = (await client.get("/api/catalog")).json()[0]["stock"]
    assert remaining == 200 - run["confirmed"], (remaining, run)
    print(json.dumps({key: value for key, value in run.items() if key != "samples"}, ensure_ascii=False, indent=2))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["all", "success", "failure", "rate", "bulkhead", "backpressure", "unprotected"], default="all", nargs="?")
    parser.add_argument("--url", default=os.getenv("API_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    scenarios = ["success", "failure", "rate", "bulkhead", "backpressure", "unprotected"] if args.scenario == "all" else [args.scenario]
    async with httpx.AsyncClient(base_url=args.url, timeout=60) as client:
        for scenario in scenarios:
            if scenario in {"success", "failure"}:
                await check_saga(client, scenario == "failure")
            else:
                await check_load(client, scenario)
    print("Todas las verificaciones solicitadas pasaron.")


if __name__ == "__main__":
    asyncio.run(main())

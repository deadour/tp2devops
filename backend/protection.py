import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import HTTPException


class RateLimiter:
    """Token bucket per demonstration client; one process / replica only."""
    def __init__(self, rate=10):
        self.rate = rate
        self.clients = {}

    def allow(self, key):
        at = time.monotonic()
        tokens, previous = self.clients.get(key, (self.rate, at))
        tokens = min(self.rate, tokens + (at - previous) * self.rate)
        accepted = tokens >= 1
        self.clients[key] = (tokens - 1 if accepted else tokens, at)
        if len(self.clients) > 1000:
            self.clients = {k: v for k, v in self.clients.items() if at - v[1] < 60}
        return accepted


class Bulkhead:
    def __init__(self, limit=3, capacity=5):
        self.limit = limit
        self.capacity = capacity
        self.active = 0
        self.waiting = 0
        self.peak_active = 0
        self.peak_waiting = 0
        self.rejected = 0
        self.condition = asyncio.Condition()

    def metrics(self):
        return {k: getattr(self, k) for k in ("limit", "capacity", "active", "waiting", "peak_active", "peak_waiting", "rejected")}

    @asynccontextmanager
    async def slot(self, enabled=True):
        admitted = False
        async with self.condition:
            if enabled and self.active >= self.limit:
                if self.waiting >= self.capacity:
                    self.rejected += 1
                    raise HTTPException(503, "bulkhead_full", headers={"Retry-After": "1"})
                self.waiting += 1
                self.peak_waiting = max(self.peak_waiting, self.waiting)
                try:
                    await asyncio.wait_for(self.condition.wait_for(lambda: self.active < self.limit), timeout=20)
                except asyncio.TimeoutError:
                    self.rejected += 1
                    raise HTTPException(503, "queue_timeout", headers={"Retry-After": "1"})
                finally:
                    self.waiting -= 1
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            admitted = True
        try:
            yield
        finally:
            if admitted:
                async with self.condition:
                    self.active -= 1
                    self.condition.notify_all()

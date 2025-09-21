from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from typing import Any, AsyncGenerator, Dict, Optional, Set

# Optional Redis backend for cross-process progress
USE_REDIS = os.getenv("OMEGA_PROGRESS_BACKEND", "memory").lower() == "redis"

if USE_REDIS:
    from backend.app.core.redis_conn import get_async_redis


@dataclass
class ProgressEvent:
    job_id: str
    step: str
    status: str = "running"           # running|ok|fail|info
    progress: Optional[float] = None  # 0.0..1.0
    data: Optional[Dict[str, Any]] = None
    ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d.get("ts"):
            d["ts"] = time.time()
        return d


class ProgressBusBase:
    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        raise NotImplementedError

    async def publish(self, event: ProgressEvent) -> None:
        raise NotImplementedError


class MemoryProgressBus(ProgressBusBase):
    def __init__(self) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subs.add(q)
        try:
            while True:
                item = await q.get()
                yield item
        finally:
            async with self._lock:
                self._subs.discard(q)

    async def publish(self, event: ProgressEvent) -> None:
        payload = event.to_dict()
        async with self._lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


class RedisProgressBus(ProgressBusBase):
    """
    Cross-process bus using Redis Pub/Sub.
    Channel: 'omega:progress'
    """
    CHANNEL = "omega:progress"

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        r = get_async_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(self.CHANNEL)
        try:
            async for message in pubsub.listen():
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    if isinstance(data, dict):
                        yield data
                except Exception:
                    # ignore malformed
                    pass
        finally:
            try:
                await pubsub.unsubscribe(self.CHANNEL)
                await pubsub.close()
            except Exception:
                pass

    async def publish(self, event: ProgressEvent) -> None:
        r = get_async_redis()
        await r.publish(self.CHANNEL, json.dumps(event.to_dict(), ensure_ascii=False))


# Singleton
_bus: Optional[ProgressBusBase] = None

def get_progress_bus() -> ProgressBusBase:
    global _bus
    if _bus is None:
        _bus = RedisProgressBus() if USE_REDIS else MemoryProgressBus()
    return _bus


@asynccontextmanager
async def start_job(step_label: str = "start", data: Optional[Dict[str, Any]] = None):
    """
    Usage:
        async with start_job("generate") as (job_id, publish):
            await publish("fetching", progress=0.1)
            ...
    """
    job_id = str(uuid.uuid4())
    bus = get_progress_bus()

    async def publish(step: str, *, status: str = "running",
                      progress: Optional[float] = None,
                      data: Optional[Dict[str, Any]] = None) -> None:
        await bus.publish(ProgressEvent(
            job_id=job_id, step=step, status=status, progress=progress, data=data
        ))

    # emit start
    await publish(step_label, status="running", progress=0.0, data=(data or {}))
    try:
        yield job_id, publish
        await publish("done", status="ok", progress=1.0)
    except Exception as e:
        await publish("error", status="fail", data={"error": str(e)})
        raise
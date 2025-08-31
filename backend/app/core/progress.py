from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from typing import Any, AsyncGenerator, Dict, Optional, Set


@dataclass
class ProgressEvent:
    """A single progress event pushed to subscribers."""
    job_id: str
    step: str
    status: str = "running"   # running|ok|fail|info
    progress: Optional[float] = None  # 0.0..1.0
    data: Optional[Dict[str, Any]] = None
    ts: float = 0.0  # epoch seconds

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # ensure ts is always set
        if not d.get("ts"):
            d["ts"] = time.time()
        return d


class ProgressBus:
    """
    Simple in-memory pub/sub for job progress.

    - Each subscriber gets its own asyncio.Queue.
    - publish() fan-outs events to all current subscribers.
    - SSE layer can filter by job_id if needed.
    """
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
                # best-effort fanout; don't block on slow consumers
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # drop if a consumer is stuck
                    pass


# Singleton
_bus: Optional[ProgressBus] = None


def get_progress_bus() -> ProgressBus:
    global _bus
    if _bus is None:
        _bus = ProgressBus()
    return _bus


@asynccontextmanager
async def start_job(step_label: str = "start", data: Optional[Dict[str, Any]] = None):
    """
    Convenience context manager to emit start/ok/fail events around an async job.

    Usage:
        async with start_job("generate") as (job_id, publish):
            await publish("fetching", progress=0.1)
            ...
            await publish("writing_files", progress=0.5)
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
        # emit ok at the end
        await publish("done", status="ok", progress=1.0)
    except Exception as e:
        await publish("error", status="fail", data={"error": str(e)})
        raise
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from typing import Any, AsyncGenerator, Dict, Optional, Set


def _clamp01(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except Exception:
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _derive_message(event: str, message: Optional[str], status: str, data: Optional[Dict[str, Any]]) -> str:
    """
    Produce a short, human-friendly label:
    - explicit 'message' if provided
    - otherwise the event name (agent_step_..., agent_tool_result, ...)
    - otherwise a hint from data
    - else fallback to status
    """
    if isinstance(message, str) and message.strip():
        return message.strip()

    if isinstance(event, str) and event.strip():
        return event

    if isinstance(data, dict):
        for k in ("phase", "step", "tool", "path", "desc"):
            v = data.get(k)
            if v:
                return f"{k}: {v}"

    return status or "working..."


@dataclass
class ProgressEvent:
    """A single progress event pushed to subscribers."""
    job_id: str
    event: str                         # primary event name (formerly 'step')
    status: str = "running"            # running|ok|fail|info
    progress: Optional[float] = None   # 0.0..1.0
    message: Optional[str] = None      # short human-readable label
    data: Optional[Dict[str, Any]] = None
    ts: float = 0.0                    # epoch seconds

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # normalize ts
        if not d.get("ts"):
            d["ts"] = time.time()
        # clamp progress and add percent
        d["progress"] = _clamp01(d.get("progress"))
        if d["progress"] is not None:
            try:
                d["percent"] = int(round(d["progress"] * 100))
            except Exception:
                d["percent"] = None
        else:
            d["percent"] = None
        # ensure message present
        d["message"] = _derive_message(d.get("event") or "", d.get("message"), d.get("status") or "", d.get("data"))
        return d


class ProgressBus:
    """
    Simple in-memory pub/sub for job progress.

    - Each subscriber gets its own asyncio.Queue.
    - publish() fan-outs events to all current subscribers.
    - SSE layer can filter by job_id if needed.
    """
    def __init__(self, *, max_queue_size: int = 1024) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
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
                # best-effort fanout; if a consumer is stuck, drop oldest to make room
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    try:
                        _ = q.get_nowait()  # drop one
                        q.put_nowait(payload)
                    except Exception:
                        # if still failing, skip this subscriber
                        pass


# Singleton
_bus: Optional[ProgressBus] = None


def get_progress_bus() -> ProgressBus:
    global _bus
    if _bus is None:
        _bus = ProgressBus()
    return _bus


@asynccontextmanager
async def start_job(event_label: str = "start", data: Optional[Dict[str, Any]] = None):
    """
    Convenience context manager to emit start/ok/fail events around an async job.

    Usage:
        async with start_job("generate") as (job_id, publish):
            await publish("boot", progress=0.02, message="agent boot")
            ...
            await publish("writing_files", progress=0.50)
            ...
            # exit context publishes final ok with progress=1.0
    """
    job_id = str(uuid.uuid4())
    bus = get_progress_bus()

    async def publish(
        event: str,
        *,
        status: str = "running",
        progress: Optional[float] = None,
        message: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        await bus.publish(
            ProgressEvent(
                job_id=job_id,
                event=event,
                status=status,
                progress=_clamp01(progress),
                message=message,
                data=data,
            )
        )

    # emit start
    await publish(event_label, status="running", progress=0.0, message="started", data=(data or {}))
    try:
        yield job_id, publish
        # emit ok at the end
        await publish("done", status="ok", progress=1.0, message="done")
    except Exception as e:
        await publish("error", status="fail", progress=None, message=str(e), data={"error": str(e)})
        raise
from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from backend.app.core.progress import get_progress_bus, start_job

router = APIRouter(prefix="/api", tags=["sse"])


def _coerce_message(event: Dict) -> str:
    """
    Produce a stable, human-readable message for UI/progress bars.
    Prefers:
      - explicit 'message' from the event (if present),
      - else the 'event' name (agent_step_..., agent_tool_result, ...),
      - else a phase hint from data,
      - else a generic fallback.
    """
    # 1) direct message
    msg = event.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg

    # 2) named event
    ev = event.get("event")
    if isinstance(ev, str) and ev.strip():
        return ev

    # 3) from data keys
    data = event.get("data") or {}
    if isinstance(data, dict):
        for k in ("phase", "step", "tool", "path"):
            v = data.get(k)
            if v:
                return f"{k}: {v}"

    # 4) fallback
    return "working..."


@router.get("/stream")
async def stream_progress(job_id: Optional[str] = Query(default=None)):
    """
    Server-Sent Events endpoint.
    - Streams all progress events by default.
    - If ?job_id=... is provided, filters events to that job only.
    - Sends heartbeat comments every ~10s to keep the connection alive.

    Each SSE 'data:' line is JSON that includes:
      {
        "job_id": "<uuid>",
        "event": "<event name>",
        "progress": <0..1>,
        "message": "<short label>",
        "data": { ... original event data ... }
      }
    """
    bus = get_progress_bus()

    async def event_generator() -> AsyncGenerator[Dict, None]:
        last_send = time.monotonic()

        async for raw in bus.subscribe():
            # Filter by job if specified
            if job_id and raw.get("job_id") != job_id:
                continue

            # Normalize payload for progress bars
            progress = raw.get("progress")
            if not isinstance(progress, (int, float)):
                # Treat unknown as "indeterminate" (emit as 0 to keep the bar drawn)
                progress = 0.0

            payload = {
                "job_id": raw.get("job_id"),
                "event": raw.get("event") or "progress",
                "progress": max(0.0, min(1.0, float(progress))),
                "message": _coerce_message(raw),
                # pass-through of original event fields for richer UIs
                "data": raw.get("data") or {},
            }

            last_send = time.monotonic()
            yield {"event": "progress", "data": payload}

            # Heartbeat is also handled by EventSourceResponse(ping=...), but if you
            # ever want manual control, you can uncomment below:
            # now = time.monotonic()
            # if now - last_send > 10.0:
            #     yield {"event": "ping", "data": {"ts": now}}
            #     last_send = now

    # sse-starlette will handle automatic keepalive comments every 10s
    return EventSourceResponse(event_generator(), ping=10.0)


# ---- Optional: tiny demo long task to see streaming in action ----

@router.post("/demo/longtask")
async def demo_longtask():
    """
    Kicks off a short simulated job (5 steps over ~5 seconds).
    Returns a message; subscribe via:
      GET /api/stream?job_id=<id>

    Tip for terminal bar:
      1) Start:  JOB_ID=$(curl -s -X POST http://127.0.0.1:8000/api/generate \
                           -H 'content-type: application/json' \
                           -d '{"brief":"simple todo app with dark theme","validate_only":true}' | jq -r .job_id)
      2) Watch:  ./scripts/progress.sh "$JOB_ID"
    """
    async def run():
        async with start_job("demo") as (job_id, publish):
            await publish("boot", progress=0.02, data={"desc": "demo start"})
            for i in range(1, 6):
                await asyncio.sleep(1.0)
                await publish("step", progress=i / 5.0, data={"i": i, "of": 5})
            await publish("done", progress=0.98, data={"desc": "demo done"})
        return

    asyncio.create_task(run())
    return {"message": "Demo started. Open /api/stream in a browser to watch events."}
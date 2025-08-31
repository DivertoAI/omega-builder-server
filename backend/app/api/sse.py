from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from backend.app.core.progress import get_progress_bus, start_job

router = APIRouter(prefix="/api", tags=["sse"])


@router.get("/stream")
async def stream_progress(job_id: Optional[str] = Query(default=None)):
    """
    Server-Sent Events endpoint.
    - Streams all events by default.
    - If ?job_id=... is provided, filters events to that job.
    - Sends heartbeat comments every ~10s to keep the connection alive.
    """
    bus = get_progress_bus()

    async def event_generator() -> AsyncGenerator[Dict, None]:
        last_heartbeat = 0.0
        async for event in bus.subscribe():
            # Heartbeat every ~10s if no events pass through; implemented via retry/timeout below.
            if job_id and event.get("job_id") != job_id:
                # skip other jobs if a filter is present
                continue
            yield {
                "event": "progress",
                "data": event,
            }

    # sse-starlette will handle keepalive pings; also you can pass ping parameter
    return EventSourceResponse(event_generator(), ping=10.0)


# ---- Optional: tiny demo long task to see streaming in action ----

@router.post("/demo/longtask")
async def demo_longtask():
    """
    Kicks off a short simulated job (5 steps over ~5 seconds).
    Returns the assigned job_id immediately; subscribe via:
      GET /api/stream?job_id=<id>
    """
    async def run():
        async with start_job("demo") as (job_id, publish):
            # Emit a few steps with small delays
            for i in range(1, 6):
                await asyncio.sleep(1)
                await publish(f"step_{i}/5", progress=i / 5.0)
        return

    # Fire-and-forget in background (uvicorn worker)
    asyncio.create_task(run())

    # We can't easily grab the job_id outside the context, so provide a general stream tip.
    # For real jobs you'll create the job_id earlier and return it. This is just a demo.
    return {"message": "Demo started. Open /api/stream in a browser to watch events."}
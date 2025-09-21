# backend/app/api/sse.py
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from backend.app.core.progress import get_progress_bus, start_job

router = APIRouter(prefix="/api", tags=["sse"])


# --------------------------------------------------------------------------------------
# Helpers: coercion, phase mapping, slug extraction
# --------------------------------------------------------------------------------------

_PHASE_HINTS = [
    ("scaffold", ("scaffold", "bootstrap", "create_project")),
    ("deps", ("deps", "pub_get", "install_deps", "pub")),
    ("emit", ("emit", "write", "fs_write", "fs_patch", "codegen")),
    ("analyze", ("analyze", "lint", "flutter_analyze")),
    ("test", ("test", "tests", "flutter_test")),
    ("repair", ("repair", "autofix", "fix", "retry")),
    ("package", ("package", "zip", "bundle", "deliver")),
    ("done", ("done", "complete", "finished", "success")),
]

_SLUG_RE = re.compile(r"/apps/([a-z0-9][a-z0-9\-]*)", re.I)


def _ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_message(event: Dict) -> str:
    """
    Produce a stable, human-readable message for UI/progress bars.
    Prefers:
      - explicit 'message' from the event (if present),
      - else the 'event' name (agent_step_..., agent_tool_result, ...),
      - else a phase hint from data,
      - else a generic fallback.
    """
    msg = event.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg

    ev = event.get("event")
    if isinstance(ev, str) and ev.strip():
        return ev

    data = event.get("data") or {}
    if isinstance(data, dict):
        for k in ("phase", "step", "tool", "path"):
            v = data.get(k)
            if v:
                return f"{k}: {v}"

    return "working..."


def _infer_phase(raw: Dict) -> str:
    ev = (raw.get("event") or "").lower()
    data = raw.get("data") or {}
    if isinstance(data, dict):
        cand = (data.get("phase") or data.get("step") or "").lower()
        if cand:
            for phase, keys in _PHASE_HINTS:
                if cand in keys:
                    return phase

    for phase, keys in _PHASE_HINTS:
        if any(k in ev for k in keys):
            return phase
    return "progress"


def _extract_app_slug(raw: Dict) -> Optional[str]:
    """
    Try to infer the app slug from common fields:
      - data.app_slug
      - data.dir / data.path containing /apps/<slug>
      - data.spec.name (slugified-ish)
    """
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        return None

    slug = data.get("app_slug")
    if isinstance(slug, str) and slug.strip():
        return slug

    for k in ("dir", "path", "app_dir"):
        v = data.get(k)
        if isinstance(v, str):
            m = _SLUG_RE.search(v)
            if m:
                return m.group(1)

    # last resort: a very light slugify for spec.name
    name = data.get("spec_name") or (data.get("spec") or {}).get("name") if isinstance(data.get("spec"), dict) else None
    if isinstance(name, str) and name.strip():
        s = name.lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s or None

    return None


def _float01(x: object, default: float = 0.0) -> float:
    try:
        v = float(x)
    except Exception:
        v = default
    return max(0.0, min(1.0, v))


# --------------------------------------------------------------------------------------
# SSE endpoint
# --------------------------------------------------------------------------------------

@router.get("/stream")
async def stream_progress(
    job_id: Optional[str] = Query(default=None, description="Filter to a specific job id"),
    include_raw: bool = Query(default=False, description="Include raw event payload in 'data.raw'"),
    ping: float = Query(default=10.0, ge=1.0, le=60.0, description="Keepalive ping seconds"),
):
    """
    Server-Sent Events endpoint.
    - Streams all progress events by default.
    - If ?job_id=... is provided, filters events to that job only.
    - Sends heartbeat comments every ~`ping` seconds to keep the connection alive.

    Each SSE 'data:' line is JSON that includes:
      {
        "ts": "<ISO-8601 UTC timestamp>",
        "job_id": "<uuid>",
        "event": "<event name>",
        "phase": "<scaffold|deps|emit|analyze|test|repair|package|done|progress>",
        "progress": <0..1>,
        "message": "<short label>",
        "app_slug": "<apps/<slug>>"?,      # when discovered
        "target": "<flutter|web|...>?",     # when available
        "data": { ... selected original event data ... }
      }
    """
    bus = get_progress_bus()

    async def event_generator() -> AsyncGenerator[Dict, None]:
        async for raw in bus.subscribe():
            try:
                # Filter by job if specified
                if job_id and raw.get("job_id") != job_id:
                    continue

                progress = raw.get("progress", 0.0)
                # Treat None / NaN as indeterminate 0.0 (renderable)
                progress = _float01(progress, 0.0)

                data_in = raw.get("data") or {}
                data_out = {}
                if isinstance(data_in, dict):
                    # Pass through a few commonly useful keys only (avoid huge payloads)
                    for k in ("phase", "step", "tool", "path", "dir", "app_dir", "spec_name", "target"):
                        if k in data_in:
                            data_out[k] = data_in[k]
                    if include_raw:
                        data_out["raw"] = data_in  # opt-in raw blob

                # Enrich with phase + slug
                phase = _infer_phase(raw)
                slug = _extract_app_slug(raw)
                if slug:
                    data_out["app_slug"] = slug

                target = (data_in.get("target") if isinstance(data_in, dict) else None) or raw.get("target")

                payload = {
                    "ts": _ts_iso(),
                    "job_id": raw.get("job_id"),
                    "event": raw.get("event") or "progress",
                    "phase": phase,
                    "progress": progress,
                    "message": _coerce_message(raw),
                    "app_slug": slug,
                    "target": target,
                    "data": data_out,
                }

                # sse-starlette will JSON-serialize dicts; ensure it's serializable
                yield {"event": "progress", "data": payload}
            except Exception as e:
                # Never break the stream on a single bad event
                err_payload = {
                    "ts": _ts_iso(),
                    "job_id": raw.get("job_id"),
                    "event": "stream_error",
                    "phase": "progress",
                    "progress": 0.0,
                    "message": f"stream error: {e}",
                    "data": {},
                }
                yield {"event": "progress", "data": err_payload}

    return EventSourceResponse(event_generator(), ping=ping)


# --------------------------------------------------------------------------------------
# Optional: tiny demo long task to see streaming in action
# --------------------------------------------------------------------------------------

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
            await publish("boot", progress=0.02, data={"desc": "demo start", "phase": "scaffold"})
            for i in range(1, 6):
                await asyncio.sleep(1.0)
                await publish(
                    "step",
                    progress=i / 5.0,
                    data={"i": i, "of": 5, "phase": "emit" if i < 3 else "analyze"},
                )
            await publish("done", progress=0.98, data={"desc": "demo done", "phase": "done"})
        return

    asyncio.create_task(run())
    return {"message": "Demo started. Open /api/stream in a browser to watch events."}
# backend/app/api/routes_debug.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.core.progress import start_job
from backend.app.services.job_store import get_last_run, save_last_run
from backend.app.services.meta_store import compute_workspace_diff_summary  # you already have this

router = APIRouter(prefix="/api", tags=["debug"])

@router.get("/last-run")
def last_run() -> Dict[str, Any]:
    data = get_last_run()
    if not data:
        raise HTTPException(status_code=404, detail="No runs recorded yet")
    return data

@router.post("/debug/force-last-run")
def force_last_run() -> Dict[str, Any]:
    """
    Compute current workspace diff and persist as last run (job_id='manual').
    """
    diff = compute_workspace_diff_summary()
    save_last_run(
        "manual",
        summary=diff.get("summary", "(no summary)"),
        diff_preview=diff.get("preview", ""),
        tool_log=[],
    )
    return {"ok": True, "summary": diff.get("summary")}

# (keep your existing /debug/last-run/html and /debug/progress-demo routes below)


@router.get("/debug/last-run/html")
def last_run_html() -> Response:
    """
    Minimal HTML viewer for the last run (summary + diff).
    Handy when you want to eyeball what the agent did without
    pulling JSON into another tool.
    """
    p = Path("workspace/.omega/last_run.json")
    if not p.exists():
        raise HTTPException(status_code=404, detail="No runs recorded yet")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read last_run.json: {e}")

    summary = data.get("summary", "(no summary)")
    diff = data.get("diff_preview", "(no diff preview)")
    job_id = data.get("job_id", "(unknown)")

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Omega Last Run</title>
  <style>
    body {{ font-family: ui-monospace, Menlo, Consolas, monospace; margin: 24px; color: #0f172a; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    .muted {{ color: #64748b; font-size: 12px; }}
    pre {{ background: #0b1020; color: #e5e7eb; padding: 16px; border-radius: 8px; overflow-x: auto; }}
    .row {{ margin-bottom: 16px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Last Run</h1>
    <div class="row muted">job_id: {job_id}</div>
    <div class="row"><strong>Summary</strong><br/>{summary}</div>
    <div class="row"><strong>Diff Preview</strong></div>
    <pre>{diff}</pre>
  </div>
</body>
</html>"""
    return Response(content=html, media_type="text/html")


@router.post("/debug/progress-demo", status_code=status.HTTP_202_ACCEPTED)
async def progress_demo() -> Dict[str, Any]:
    """
    Kick off a short simulated job that emits SSE progress you can view via:
      - CLI:   curl -iN "http://127.0.0.1:8000/api/stream?job_id=<ID>"
      - HTML:  http://127.0.0.1:8000/api/progress?job_id=<ID>   (from routes_sse.py)
    Returns the job_id immediately.
    """
    job_id_holder: Dict[str, str] = {}

    async def run():
        async with start_job("demo", data={"source": "debug.progress-demo"}) as (job_id, publish):
            job_id_holder["job_id"] = job_id
            # a few quick phases
            await publish("phase_boot", progress=0.05, message="booting")
            await asyncio.sleep(0.5)

            await publish("phase_scan", progress=0.18, message="scanning files")
            await asyncio.sleep(0.5)

            await publish("phase_plan", progress=0.35, message="planning changes")
            await asyncio.sleep(0.6)

            await publish("phase_apply", progress=0.62, message="applying edits")
            await asyncio.sleep(0.7)

            await publish("phase_finalize", progress=0.85, message="finalizing")
            await asyncio.sleep(0.5)
            # context exit will publish done=1.0

    # fire-and-forget
    asyncio.create_task(run())

    # Give the task a brief tick to store job_id
    for _ in range(20):
        await asyncio.sleep(0.01)
        if "job_id" in job_id_holder:
            break

    return {
        "status": "started",
        "job_id": job_id_holder.get("job_id"),
        "tip": "Open /api/progress?job_id=<ID> in a browser or stream /api/stream via curl.",
    }
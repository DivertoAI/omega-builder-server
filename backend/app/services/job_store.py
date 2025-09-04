# backend/app/services/job_store.py
from __future__ import annotations
import json
import time
from typing import Any, Dict, Optional, List
import os
from pathlib import Path

from backend.app.core.redis_conn import get_sync_redis

JOBS_KEY = "omega:jobs"  # Redis hash of job_id -> compact JSON blob

# ---------- existing ----------
def put_job(job_id: str, status: str, payload: Optional[Dict[str, Any]] = None) -> None:
    r = get_sync_redis()
    doc = {
        "status": status,              # queued|running|ok|fail
        "updated_at": time.time(),
    }
    if payload:
        doc.update(payload)
    r.hset(JOBS_KEY, job_id, json.dumps(doc, ensure_ascii=False))

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    r = get_sync_redis()
    raw = r.hget(JOBS_KEY, job_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"status": "corrupt", "raw": raw}

# ---------- new: file-based last-run ----------
STATE_DIR = Path(os.getenv("OMEGA_STATE_DIR", "workspace/.omega"))
LAST_RUN_PATH = STATE_DIR / "last_run.json"

def save_last_run(job_id: str, *, summary: str, diff_preview: str, tool_log: List[Dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id,
        "summary": summary,
        "diff_preview": diff_preview,
        "tool_log": tool_log,
        "validate_only": False,
        "saved_at": time.time(),
    }
    LAST_RUN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def get_last_run() -> Optional[Dict[str, Any]]:
    if not LAST_RUN_PATH.exists():
        return None
    try:
        return json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
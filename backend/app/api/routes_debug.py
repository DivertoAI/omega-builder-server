from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["debug"])


@router.get("/last-run")
def last_run() -> Dict[str, Any]:
    """
    Returns the JSON persisted by the agent at workspace/.omega/last_run.json.

    Shape (best effort, depends on the last run):
    {
      "job_id": "...",
      "summary": "DONE: ...",
      "diff_preview": "...",
      "tool_log": [...],            # trimmed to last ~200 entries by the agent
      "validate_only": false
    }
    """
    p = Path("workspace/.omega/last_run.json")
    if not p.exists():
        raise HTTPException(status_code=404, detail="No runs recorded yet")
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("last_run.json must contain a JSON object")
        return data
    except HTTPException:
        # re-raise FastAPI HTTPException unchanged
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read last_run.json: {e}")
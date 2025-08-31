from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.app.models.spec import validate_spec
import backend.app.services.plan_service as plan_service  # <-- module import so tests can monkeypatch
from backend.app.services.agent_service import adapt_repository_with_agent

router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Adapt the repo to the given OmegaSpec using an OpenAI tool-calling agent.

    Body (one of):
      - { "spec": <OmegaSpec JSON> }  # object OR JSON string
      - { "brief": "replan from scratch ..." }

    Optional fields:
      - dev_instructions: str              # high-priority ad-hoc edit guidance for fast iteration
      - validate_only: bool                # run read/plan/diff path but DO NOT write (skips fs_write/patch/mkdir/delete)
      - wall_clock_budget_sec: float       # hard cap for the entire agent run (defaults applied if omitted)
      - per_call_timeout_sec: float        # timeout per chat-completions turn (defaults applied if omitted)

    Returns:
      {
        "status": "ok",
        "job_id": "<uuid>",
        "result": {
          "summary": "...",
          "tool_log": [...],
          "diff_preview": "<diff-like snapshot>",
          "job_id": "<uuid>"   # echoed here as well for convenience
        }
      }
    """
    spec_obj = payload.get("spec")
    brief: Optional[str] = payload.get("brief")
    dev_instructions: Optional[str] = payload.get("dev_instructions")
    validate_only: bool = bool(payload.get("validate_only", False))
    wall_clock_budget_sec: Optional[float] = payload.get("wall_clock_budget_sec")
    per_call_timeout_sec: Optional[float] = payload.get("per_call_timeout_sec")

    # Accept spec as dict OR as JSON string
    if isinstance(spec_obj, str) and spec_obj.strip():
        try:
            spec_obj = json.loads(spec_obj)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"'spec' is a string but not valid JSON: {e}")

    if isinstance(spec_obj, dict):
        try:
            spec = validate_spec(spec_obj)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid spec: {e}")
    elif brief:
        # Call through the module so tests can monkeypatch plan_and_validate
        spec, _raw = plan_service.plan_and_validate(brief, max_repairs=1)
    else:
        got_keys = list(payload.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Provide either 'spec' (dict or JSON string) or 'brief'. Got keys: {got_keys}",
        )

    result = await adapt_repository_with_agent(
        spec,
        dev_instructions=dev_instructions,
        validate_only=validate_only,
        wall_clock_budget_sec=wall_clock_budget_sec,
        per_call_timeout_sec=per_call_timeout_sec,
    )

    # bubble job_id to top-level for easy /api/stream?job_id=...
    return {"status": "ok", "job_id": result.get("job_id"), "result": result}


# BEGIN OMEGA SECTION (managed)
# Generator notes and TODOs
#
# This /api/generate endpoint adapts the repository to a provided OmegaSpec using
# an agent. Progress is streamed via SSE at /api/stream (see backend.app.api.sse).
#
# Options supported here:
# - dev_instructions (string): ad-hoc guidance for quick edits (e.g., "write workspace/TODO.md; then fs_diff").
# - validate_only (bool): run read/plan/diff without mutating the repo (skips write/patch/mkdir/delete).
# - wall_clock_budget_sec (float): total wall-clock budget for the agent loop.
# - per_call_timeout_sec (float): timeout per chat-completions turn.
#
# Idempotency:
# - The agent is prompted to prefer fs_patch for surgical edits and to avoid duplicating content.
# - Finalization always emits fs_glob/fs_diff so callers can verify changes even if the model omits them mid-run.
#
# Keep this block idempotent; it may be replaced on subsequent runs by Omega Builder.
# END OMEGA SECTION
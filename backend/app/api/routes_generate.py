from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

# --- Spec validation / planning (agent path) ---
from backend.app.models.spec import validate_spec
import backend.app.services.plan_service as plan_service  # module import so tests can monkeypatch
from backend.app.services.agent_service import adapt_repository_with_agent

# --- New lightweight codegen (codegen mode) ---
from backend.app.services.generate_service import plan_files, write_project

router = APIRouter(prefix="/api", tags=["generate"])


def _workspace() -> Path:
    """
    Directory used by codegen for writing artifacts.
    Matches the rest of the app's convention.
    """
    return Path(os.getenv("OMEGA_WORKSPACE", "workspace/.omega"))


@router.post("/generate")
async def generate_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Two modes are supported:

    1) Agent mode (default): Adapt the repo to the given OmegaSpec using an OpenAI tool-calling agent.
       Provide either:
         - {"spec": <OmegaSpec JSON object or JSON string>, ...}
         - {"brief": "replan from scratch ...", ...}
       Optional (agent):
         - dev_instructions: str
         - validate_only: bool
         - wall_clock_budget_sec: float
         - per_call_timeout_sec: float
       Response (agent):
         {
           "status": "ok",
           "job_id": "<uuid>",
           "result": {
             "summary": "...",
             "tool_log": [...],
             "diff_preview": "<diff-like snapshot>",
             "job_id": "<uuid>"
           }
         }

    2) Codegen mode (opt-in): Generate a small runnable app scaffold to the workspace.
       Select with {"mode": "codegen"} and provide either:
         - {"mode":"codegen","brief":"...", "target":"react"?, "dry_run":true?}
         - {"mode":"codegen","spec":{OmegaSpec...}, "target":"react"?, "dry_run":true?}
       The 'target' currently supports "react". 'dry_run' returns a manifest without writing.
       Response (codegen):
         - dry_run -> {"status":"ok","target":"react","dry_run":true,"files":[{"path":..., "bytes":...}, ...]}
         - write   -> {"status":"ok","target":"react","dir":"workspace/builds/<slug>-<ts>","files":[...]}
    """

    mode = (payload.get("mode") or "agent").lower()

    # ---------------------------
    # MODE: CODEGEN
    # ---------------------------
    if mode == "codegen":
        target = (payload.get("target") or "react").lower()
        dry_run = bool(payload.get("dry_run", False))

        # Accept spec as dict OR as JSON string; otherwise try brief
        spec_obj = payload.get("spec")
        brief: Optional[str] = payload.get("brief")

        if isinstance(spec_obj, str) and spec_obj.strip():
            try:
                spec_obj = json.loads(spec_obj)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"'spec' is a string but not valid JSON: {e}")

        if isinstance(spec_obj, dict):
            try:
                spec = validate_spec(spec_obj)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"invalid spec: {e}")
        elif brief:
            # Use the planner to get a validated spec for codegen as well.
            # (Calling through the module so tests can monkeypatch plan_and_validate)
            spec, _raw = plan_service.plan_and_validate(brief, max_repairs=1)
        else:
            got_keys = list(payload.keys())
            raise HTTPException(
                status_code=400,
                detail=f"[codegen] Provide either 'spec' (dict or JSON string) or 'brief'. Got keys: {got_keys}",
            )

        if dry_run:
            files = [{"path": rel, "bytes": len(content.encode("utf-8"))} for rel, content in plan_files(spec, target)]
            return {"status": "ok", "target": target, "dry_run": True, "files": files}

        ws = _workspace()
        return write_project(spec, ws, target)

    # ---------------------------
    # MODE: AGENT (default)
    # ---------------------------
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
# Notes:
# - This endpoint now supports two modes:
#     * "agent" (default): repository adaptation via the tool-using agent.
#     * "codegen": write a runnable scaffold to workspace/builds/... or dry-run a file plan.
# - The codegen path is conservative and avoids I/O when 'dry_run' is true,
#   returning a deterministic manifest suitable for tests.
# - The agent path remains API-compatible with existing tests/clients (no breaking changes).
# - Keep this block idempotent; Omega Builder may update it on future runs.
# END OMEGA SECTION
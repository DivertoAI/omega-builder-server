from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.app.models.spec import validate_spec
from backend.app.services.plan_service import plan_and_validate
from backend.app.services.agent_service import adapt_repository_with_agent

router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Adapt the repo to the given OmegaSpec using an OpenAI tool-calling agent.
    Body (one of):
      - { "spec": <OmegaSpec JSON> }  # object OR JSON string
      - { "brief": "replan from scratch ..." }

    Returns:
      { "status": "ok", "result": { "summary": "...", "tool_log": [...] } }
    """
    spec_obj = payload.get("spec")
    brief: Optional[str] = payload.get("brief")

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
        spec, _raw = plan_and_validate(brief, max_repairs=1)
    else:
        # Helpful diagnostics
        got_keys = list(payload.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Provide either 'spec' (dict or JSON string) or 'brief'. Got keys: {got_keys}"
        )

    result = await adapt_repository_with_agent(spec)
    return {"status": "ok", "result": result}
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.app.models.spec import validate_spec
from backend.app.services.plan_service import plan_and_validate

router = APIRouter(prefix="/api", tags=["plan"])


@router.post("/plan")
async def plan_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Convert a 'brief' into a validated OmegaSpec (no repo writes here).
    """
    spec_obj = payload.get("spec")
    brief: Optional[str] = payload.get("brief")
    max_repairs: int = int(payload.get("max_repairs", 1))

    # Accept spec as dict OR JSON string
    if isinstance(spec_obj, str) and spec_obj.strip():
        try:
            spec_obj = json.loads(spec_obj)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"'spec' is a string but not valid JSON: {e}")

    if isinstance(spec_obj, dict):
        try:
            spec = validate_spec(spec_obj)
            return {"status": "ok", "spec": spec.model_dump(), "raw": spec_obj}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid spec: {e}")

    if not brief:
        got_keys = list(payload.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Provide 'brief' or 'spec'. Got keys: {got_keys}",
        )

    try:
        spec, raw = plan_and_validate(brief, max_repairs=max_repairs)
        return {"status": "ok", "spec": spec.model_dump(), "raw": raw}
    except Exception as e:
        # Surface validation errors as JSON 400 so clients (and jq) can parse them
        raise HTTPException(status_code=400, detail=f"Planning/validation failed: {e}")
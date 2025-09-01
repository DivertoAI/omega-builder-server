from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.app.models.spec import validate_spec
from backend.app.services.plan_service import plan_and_validate

router = APIRouter(prefix="/api", tags=["plan"])


def _auto_repair_spec(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply minimal, safe repairs to an incoming OmegaSpec-like dict.

    Repairs:
      - theme.radius: if number -> [number]; if missing -> [6, 10]
      - navigation: ensure an object with {home: "home", items: []}
      - endpoints -> apis: if apis missing but endpoints is present, copy it
    """
    repaired = dict(obj)

    # --- theme.radius ---
    theme = dict(repaired.get("theme") or {})
    radius = theme.get("radius")
    if isinstance(radius, (int, float)):
        theme["radius"] = [int(radius)]
    elif radius is None:
        theme.setdefault("radius", [6, 10])
    repaired["theme"] = theme

    # --- navigation object shape ---
    nav = repaired.get("navigation")
    if nav is None:
        repaired["navigation"] = {"home": "home", "items": []}
    elif isinstance(nav, list):
        # old/invalid shape; drop to safe default
        repaired["navigation"] = {"home": "home", "items": []}
    elif isinstance(nav, dict):
        nav = dict(nav)
        nav.setdefault("home", "home")
        nav.setdefault("items", [])
        repaired["navigation"] = nav
    else:
        repaired["navigation"] = {"home": "home", "items": []}

    # --- endpoints -> apis bridge (legacy -> current) ---
    if "apis" not in repaired and isinstance(repaired.get("endpoints"), list):
        repaired["apis"] = repaired["endpoints"]

    return repaired


@router.get("/health")
async def health() -> Dict[str, Any]:
    # assuming you already have this elsewhere; included here only if needed for completeness
    return {"status": "ok"}


@router.post("/plan")
async def plan_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Convert a 'brief' into a validated OmegaSpec (no repo writes here).

    Body (one of):
      - { "brief": "..." }                   -> plans from scratch
      - { "spec": <OmegaSpec JSON> }         -> validates & echoes

    Optional:
      - max_repairs: int                     -> simple auto-fix attempts (default 1)

    Returns:
      {
        "status": "ok",
        "spec": <validated OmegaSpec dict>,
        "raw":  <raw dict produced/received>,
      }
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

    # ----- SPEC VALIDATION PATH -----
    if isinstance(spec_obj, dict):
        # 1) try as-is
        try:
            spec = validate_spec(spec_obj)
            return {"status": "ok", "spec": spec.model_dump(), "raw": spec_obj}
        except Exception as first_err:
            # 2) try auto-repair if allowed
            if max_repairs and max_repairs > 0:
                try:
                    repaired = _auto_repair_spec(spec_obj)
                    spec = validate_spec(repaired)
                    return {"status": "ok", "spec": spec.model_dump(), "raw": repaired}
                except Exception as second_err:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Invalid spec after auto-repair. "
                            f"original_error={first_err}; repaired_error={second_err}"
                        ),
                    )
            # 3) otherwise fail
            raise HTTPException(status_code=400, detail=f"Invalid spec: {first_err}")

    # ----- BRIEF PLANNING PATH -----
    if not brief:
        got_keys = list(payload.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Provide 'brief' or 'spec'. Got keys: {got_keys}",
        )

    spec, raw = plan_and_validate(brief, max_repairs=max_repairs)
    return {"status": "ok", "spec": spec.model_dump(), "raw": raw}
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Depends

# ---- Existing OmegaSpec validation/planning ----
from backend.app.models.spec import validate_spec
from backend.app.services.plan_service import plan_and_validate

# ---- Phase 2: packs/adapters/theme monorepo planning ----
from backend.app.models.plan import PlanRequest, PlanResponse, AppSpec
from backend.app.services.blueprints.merge import load_pack, apply_theme
from backend.app.services.adapters.registry import activate

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
    return {"status": "ok"}


# --------------------------------------------------------------------------------------
# V1: OmegaSpec planner/validator (unchanged behavior)
# --------------------------------------------------------------------------------------
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


# --------------------------------------------------------------------------------------
# V2: Monorepo planner (blueprint + adapters + theme) — Phase 2
# --------------------------------------------------------------------------------------
@router.post("/plan/monorepo", response_model=PlanResponse)
async def plan_monorepo(req: PlanRequest = Body(...)) -> PlanResponse:
    """
    Phase 2 planner that merges a blueprint pack, applies theme tokens,
    and activates env-gated adapters. Returns a high-level PlanResponse
    describing the multi-app monorepo to be generated.

    Body:
      {
        "brief": "optional human brief (not used yet)",
        "blueprint": "pharmacy|blank|diary|None",
        "adapters": ["ocr","telemed","payments","logistics","firebase","bluetooth"],
        "theme": { "palette": {...}, "typography": {...}, "radius": [..] },
        "max_repairs": 1
      }
    """
    # 1) Load pack defaults
    plan_dict: Dict[str, Any] = load_pack(req.blueprint)

    # 2) Apply theme if provided
    if req.theme:
        plan_dict = apply_theme(plan_dict, req.theme.model_dump())

    # 3) Activate adapters (env-gated)
    active_adapters: List[str] = activate(req.adapters)

    # 4) Minor auto-repair (radius list)
    design = plan_dict.get("design", {})
    radius = design.get("radius")
    if not isinstance(radius, list):
        design["radius"] = [6, 10, 14]
        plan_dict["design"] = design

    # 5) Build response
    apps = [AppSpec(**a) for a in plan_dict.get("apps", [])]
    return PlanResponse(
        project=plan_dict.get("project", "omega_project"),
        apps=apps,
        design=plan_dict.get("design", {}),
        adapters=active_adapters,
        notes=[
            "blueprint merged",
            f"adapters activated: {active_adapters}",
            "theme applied" if req.theme else "default theme",
        ],
    )
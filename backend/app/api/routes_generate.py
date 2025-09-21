# backend/app/api/routes_generate.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.models.spec import OmegaSpec
from backend.app.services.plan_service import plan_and_validate
from backend.app.services.quality_gate import run_quality_gate

# try to import the generator entrypoint with a few fallbacks (keeps backward compatibility)
do_generate = None
try:
    # preferred: generate_artifacts(spec: OmegaSpec, staging_root: Path) -> manifest(dict|list)
    from backend.app.services.generate_service import generate_artifacts as do_generate  # type: ignore
except Exception:
    try:
        # alt: generate(spec: OmegaSpec, staging_root: Path) -> manifest(dict|list)
        from backend.app.services.generate_service import generate as do_generate  # type: ignore
    except Exception:
        pass

router = APIRouter(prefix="/api/generate", tags=["generate"])


# -------------------------
# Request/Response models
# -------------------------

class PlanRequest(BaseModel):
    brief: str = Field(..., min_length=1, description="Natural-language brief to plan.")


class PlanResponse(BaseModel):
    spec: Dict[str, Any]
    raw_spec: Dict[str, Any]
    notes: Optional[str] = None


class GenerateRequest(BaseModel):
    brief: str = Field(..., min_length=1, description="Natural-language brief to plan and generate artifacts.")
    # optional knobs (future-proof)
    overwrite: bool = Field(default=True, description="Allow generator to overwrite files in staging root.")


class QualityGatePayload(BaseModel):
    passed: bool
    errors: list[str]
    warnings: list[str]
    metrics: Dict[str, Any]
    summary: str


class GenerateResponse(BaseModel):
    spec: Dict[str, Any]
    raw_spec: Dict[str, Any]
    manifest: Union[Dict[str, Any], list]
    quality_gate: QualityGatePayload


# -------------------------
# Endpoints
# -------------------------

@router.post("/spec", response_model=PlanResponse)
def post_spec(req: PlanRequest) -> PlanResponse:
    """
    Plan only (no codegen). Calls O3 (planner) via plan_and_validate().
    """
    try:
        spec_model, raw_spec = plan_and_validate(req.brief, max_repairs=1)
        return PlanResponse(spec=spec_model.model_dump(), raw_spec=raw_spec, notes="planned")
    except Exception as e:
        # Keep messages short and actionable for UI + o3 self-repair loops.
        raise HTTPException(status_code=400, detail=f"Spec planning failed: {e}")


@router.post("", response_model=GenerateResponse)
def post_generate(req: GenerateRequest) -> GenerateResponse:
    """
    Full pipeline:
      1) O3 planner -> OmegaSpec (validated/auto-repaired if needed)
      2) GPT-5 codegen via generate_service (writes into staging_root)
      3) Quality gate over produced manifest
    """
    # 1) Plan
    try:
        spec_model, raw_spec = plan_and_validate(req.brief, max_repairs=1)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Spec planning failed: {e}")

    # 2) Codegen
    if do_generate is None:
        raise HTTPException(
            status_code=500,
            detail="Generator entrypoint not found. Expected generate_artifacts() or generate() in services/generate_service.py",
        )

    try:
        manifest = do_generate(spec_model, staging_root=settings.staging_root)  # type: ignore
    except Exception as e:
        # Bubble concise info; generator logs should carry the rest.
        raise HTTPException(status_code=500, detail=f"Generation failed: {type(e).__name__}: {e}")

    # Normalize a bit (accept dict|list from generator)
    if not isinstance(manifest, (dict, list)):
        manifest = {"files": [], "notes": "generator returned unknown type; wrapped by API"}

    # 3) Quality gate
    try:
        gate = run_quality_gate(spec_model, manifest, staging_root=settings.staging_root)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Quality gate failed: {type(e).__name__}: {e}")

    return GenerateResponse(
        spec=spec_model.model_dump(),
        raw_spec=raw_spec,
        manifest=manifest,
        quality_gate=QualityGatePayload(**gate.to_dict()),
    )
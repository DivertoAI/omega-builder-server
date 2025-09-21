from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter

from backend.app.services.assets_service import enqueue_assets_job

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetGenerateRequest(BaseModel):
    # Optional identifiers/paths
    job_id: Optional[str] = Field(default=None, description="Optional job id (else generated)")
    output_dir: Optional[str] = Field(
        default=None,
        description="Primary output dir (must be inside shared staging root)"
    )
    requested_output_dir: Optional[str] = Field(
        default=None,
        description="Optional mirror directory where assets are also written"
    )
    output_dir_aliases: Optional[List[str]] = Field(
        default=None,
        description="Optional list of mirror directories to also receive assets"
    )

    # Content hints
    spec: Dict[str, Any] = Field(default_factory=dict, description="Your app spec or brand hints")
    brand_name: Optional[str] = Field(default=None, description="Brand/app name for prompts")
    color_hex: Optional[str] = Field(default=None, description="Primary brand color (e.g. #4F46E5)")
    style: Optional[str] = Field(default=None, description="Aesthetic (e.g. 'clean, modern')")

    # Control which assets/sizes
    kinds: Optional[List[str]] = Field(
        default=None,
        description="Subset of assets to render (default: app_icon, hero_home, empty_state)"
    )
    sizes: Optional[Dict[str, str]] = Field(
        default=None,
        description="Per-kind size overrides (e.g. {'app_icon':'1024x1024','hero_home':'1536x1024'})"
    )


@router.get("/kinds")
def list_asset_kinds():
    """Small helper endpoint so the UI knows what's available."""
    return {
        "kinds": ["app_icon", "hero_home", "empty_state"],
        "defaults": {"color_hex": "#4F46E5", "style": "clean, modern, high-contrast, mobile-first"},
    }


@router.post("/generate")
def generate_assets(body: AssetGenerateRequest):
    """
    Enqueue an asset generation job. The AI-VM worker will pull it and
    write PNGs into /workspace/staging/assets/<job_id>/ (and to any mirrors).
    """
    payload = enqueue_assets_job(
        job_id=body.job_id,
        output_dir=body.output_dir,
        requested_output_dir=body.requested_output_dir,
        output_dir_aliases=body.output_dir_aliases,
        spec=body.spec,
        brand_name=body.brand_name,
        color_hex=body.color_hex,
        style=body.style,
        kinds=body.kinds,
        sizes=body.sizes,
    )
    return {"status": "queued", **payload}
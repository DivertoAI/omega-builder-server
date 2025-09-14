# backend/app/api/routes_assets.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter

from backend.app.services.assets_service import enqueue_assets_job

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetGenerateRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict, description="Your app spec or brand hints")
    brand_name: Optional[str] = Field(default=None, description="Brand/app name for prompts")
    color_hex: Optional[str] = Field(default=None, description="Primary brand color (e.g. #4F46E5)")
    style: Optional[str] = Field(default=None, description="Aesthetic (e.g. 'clean, modern')")
    kinds: Optional[List[str]] = Field(
        default=None,
        description="Subset of assets to render (default: app_icon, hero_home, empty_state)"
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
    write PNGs into /workspace/staging/assets/<job_id>/.
    """
    payload = enqueue_assets_job(
        spec=body.spec,
        brand_name=body.brand_name,
        color_hex=body.color_hex,
        style=body.style,
        kinds=body.kinds,
    )
    return {"status": "queued", **payload}
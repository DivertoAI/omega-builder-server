# backend/app/api/routes_assets.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Body, HTTPException

from backend.app.services.assets_service import generate_assets_batch, AssetTask

router = APIRouter(prefix="/api/assets", tags=["assets"])

class AssetTaskIn(BaseModel):
    prompt: str
    filename: str
    size: str = Field(default="1024x1024")

class AssetsRequest(BaseModel):
    style_hint: Optional[str] = None
    tasks: List[AssetTaskIn]

@router.post("/generate")
async def assets_generate(payload: AssetsRequest = Body(...)) -> Dict[str, Any]:
    try:
        tasks = [AssetTask(prompt=t.prompt, filename=t.filename, size=t.size) for t in payload.tasks]
        result = generate_assets_batch(payload.style_hint, tasks)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"asset generation failed: {e}")
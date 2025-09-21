from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, Body, HTTPException
from backend.app.api.routes_build_preview import build_publish_matrix_impl

router = APIRouter(prefix="/api/preview", tags=["preview"])

@router.post("/build-matrix")
def build_matrix(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    CI-friendly: accept {"project": "...", "matrix": [{"app_path":"...","app_name":"..."}, ...]}
    Returns per-app preview_url + publish info.
    """
    project = payload.get("project")
    matrix: List[Dict[str, str]] = payload.get("matrix") or []
    if not project or not matrix:
        raise HTTPException(status_code=400, detail="Provide project and non-empty matrix[]")
    return build_publish_matrix_impl(project, matrix)
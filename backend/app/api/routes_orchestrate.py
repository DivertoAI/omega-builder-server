from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["orchestrate"])

class MatrixItem(BaseModel):
    app_path: str
    app_name: str

class OrchestrateIn(BaseModel):
    project: str = Field(..., description="Project directory name, e.g. 'insta_pharma'")
    brief: str = Field(..., description="High-level brief for plan/generate")
    matrix: List[MatrixItem]

@router.post("/orchestrate")
async def orchestrate(payload: OrchestrateIn):
    """
    1) /api/plan -> write .omega/spec.json
    2) /api/generate
    3) /api/preview/build-matrix
    4) /api/preview/hub-regen
    """
    base = "http://localhost:8000"
    project_dir = f"/workspace/{payload.project}"
    spec_dir = Path(project_dir) / ".omega"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "spec.json"

    async with httpx.AsyncClient(timeout=120) as client:
        # 1) PLAN
        plan_res = await client.post(
            f"{base}/api/plan",
            json={"brief": payload.brief, "max_repairs": 1},
        )
        if plan_res.status_code != 200:
            raise HTTPException(status_code=502, detail={"step": "plan", "body": plan_res.text})

        plan_json = plan_res.json()
        # prefer 'spec' if present, else full body
        to_write = plan_json.get("spec") or plan_json
        spec_path.write_text(
            (to_write if isinstance(to_write, str) else __import__("json").dumps(to_write, indent=2)),
            encoding="utf-8",
        )

        # 2) GENERATE (be sure to include brief to avoid 422)
        gen_res = await client.post(
            f"{base}/api/generate",
            json={"project": payload.project, "brief": payload.brief},
        )
        if gen_res.status_code != 200:
            raise HTTPException(status_code=502, detail={"step": "generate", "body": gen_res.text})

        # 3) BUILD MATRIX (Flutter web -> preview)
        build_res = await client.post(
            f"{base}/api/preview/build-matrix",
            json={"project": payload.project, "matrix": [mi.dict() for mi in payload.matrix]},
        )
        if build_res.status_code != 200:
            raise HTTPException(status_code=502, detail={"step": "build-matrix", "body": build_res.text})

        build_json = build_res.json()
        # 4) HUB REGEN
        await client.post(f"{base}/api/preview/hub-regen")

    previews = [r.get("preview_url") for r in build_json.get("results", []) if r.get("preview_url")]
    return {
        "status": "ok",
        "project": payload.project,
        "spec_path": str(spec_path),
        "previews": previews,
        "hub": "/preview/index.html"
    }
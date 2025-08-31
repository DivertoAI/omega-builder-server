from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.app.core.config import settings
from backend.app.core.progress import start_job
from backend.app.services.plan_service import plan_and_validate

router = APIRouter(prefix="/api", tags=["plan"])


@router.post("/plan")
async def plan_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Create an OmegaSpec from a short product brief.
    Body:
      {
        "brief": "AI marketplace app ...",
        "max_repairs": 1
      }
    """
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OpenAI not configured")

    brief: Optional[str] = payload.get("brief")
    max_repairs: int = int(payload.get("max_repairs", 1))
    if not brief or not isinstance(brief, str):
        raise HTTPException(status_code=400, detail="Missing 'brief' string")

    async with start_job("plan", data={"stage": "planning"}) as (_job_id, publish):
        await publish("deep_research", progress=0.2)
        try:
            spec, raw = plan_and_validate(brief, max_repairs=max_repairs)
        except Exception as e:
            await publish("error", status="fail", data={"error": str(e)})
            raise HTTPException(status_code=500, detail=f"Plan failed: {e}")
        await publish("validated", progress=0.9)

    return {"status": "ok", "spec": spec.model_dump()}
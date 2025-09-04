# backend/app/api/routes_jobs.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.services.job_queue import (
    new_build_job,
    enqueue_build,
    get_job_status as redis_get_job_status,
)

router = APIRouter(prefix="/api", tags=["jobs"])


# ---------- Schemas ----------

class BuildJobRequest(BaseModel):
    project_dir: str = Field(..., description="Absolute path inside the container/VM")
    target: str = Field(
        "analyze",
        description="Build target: analyze | test | apk | ipa | web",
    )
    platform: str = Field(
        "android",
        description="Target platform: android | ios | web",
    )
    commit_msg: Optional[str] = Field(
        None, description="Optional commit message to attach to the job"
    )


class BuildJobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    detail: Optional[str] = None


# ---------- Routes ----------

@router.post(
    "/jobs/build",
    response_model=BuildJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a Flutter build/analyze/test job",
)
async def submit_build_job(body: BuildJobRequest) -> BuildJobResponse:
    """
    Queue a build/test job for the worker running in the AI VM.

    The worker BLPOP's from `queue:build`. We push a JSON-serialized job and
    also seed `job:{id}` hash for status tracking.
    """
    job = new_build_job(
        project_dir=body.project_dir,
        target=body.target,
        platform=body.platform,
        commit_msg=body.commit_msg,
    )
    await enqueue_build(job)
    return BuildJobResponse(job_id=job.id)


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Return the status stored in `job:{job_id}` (hash in Redis).
    """
    doc = await redis_get_job_status(job_id)
    if not doc:
        raise HTTPException(status_code=404, detail="job not found")

    status_val = doc.get("status", "unknown")
    detail = doc.get("detail")
    return JobStatusResponse(job_id=job_id, status=status_val, detail=detail)
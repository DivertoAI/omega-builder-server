# backend/app/services/job_queue.py
from __future__ import annotations

import uuid
import json
from dataclasses import dataclass, asdict
from typing import Any, Dict

from redis.asyncio import Redis  # redis>=5 required
from backend.app.core.redis_conn import get_async_redis

QUEUE_KEY = "queue:build"


@dataclass
class BuildJob:
    """Represents a build/test job for the AI-VM worker."""

    id: str
    kind: str        # e.g. "flutter_build"
    project_dir: str # absolute path inside container/VM
    target: str      # "analyze", "test", "apk", "ipa", "web"
    platform: str    # "android", "ios", "web", etc.
    commit_msg: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def new_build_job(
    project_dir: str,
    target: str = "analyze",
    platform: str = "android",
    commit_msg: str | None = None,
) -> BuildJob:
    """Create a new BuildJob with a unique ID."""
    return BuildJob(
        id=str(uuid.uuid4()),
        kind="flutter_build",
        project_dir=project_dir,
        target=target,
        platform=platform,
        commit_msg=commit_msg,
    )


async def enqueue_build(job: BuildJob) -> str:
    """
    Push a BuildJob onto the Redis list queue.
    Returns the job ID.
    """
    r: Redis = await get_async_redis()
    await r.rpush(QUEUE_KEY, job.to_json())
    # initialize job hash for status tracking
    await r.hset(
        f"job:{job.id}",
        mapping={"status": "queued", "created_at": job.id},
    )
    return job.id


async def update_job_status(job_id: str, status: str, **fields: Any) -> None:
    """Update status or extra fields for a job."""
    r: Redis = await get_async_redis()
    mapping = {"status": status}
    mapping.update({k: str(v) for k, v in fields.items()})
    await r.hset(f"job:{job_id}", mapping=mapping)


async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Fetch the job hash for a given job_id."""
    r: Redis = await get_async_redis()
    data = await r.hgetall(f"job:{job_id}")
    return data or {}
# backend/app/services/job_queue.py
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from redis.asyncio import Redis  # redis>=5
from backend.app.core.redis_conn import get_async_redis
from backend.app.core.config import settings

# -----------------------------------------------------------------------------
# Redis keys / helpers
# -----------------------------------------------------------------------------

QUEUE_KEY: str = settings.job_queue_name or "jobs:generate"
JOB_KEY_PREFIX = "job:"  # final key: job:<id>
EVENTS_CHANNEL = "events:jobs"  # lightweight pub/sub for UI updates


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class BuildJob:
    """
    Represents a build/test job for the worker fleet.
    """

    id: str
    kind: str  # e.g. "flutter_build"
    project_dir: str  # absolute path inside container/VM
    target: str  # "analyze", "test", "apk", "ipa", "web"
    platform: str  # "android", "ios", "web", etc.
    commit_msg: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    # Optional idempotency to avoid dup enqueues (same logical request)
    idem_key: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# -----------------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------------

def new_build_job(
    project_dir: str,
    target: str = "analyze",
    platform: str = "android",
    commit_msg: Optional[str] = None,
    *,
    idem_key: Optional[str] = None,
) -> BuildJob:
    return BuildJob(
        id=str(uuid.uuid4()),
        kind="flutter_build",
        project_dir=project_dir,
        target=target,
        platform=platform,
        commit_msg=commit_msg,
        idem_key=idem_key,
    )


# -----------------------------------------------------------------------------
# Enqueue / Dequeue
# -----------------------------------------------------------------------------

async def enqueue_build(job: BuildJob) -> str:
    """
    Push a BuildJob onto the Redis list queue.
    Initializes a job hash for status tracking.
    Returns the job ID.
    """
    r: Redis = await get_async_redis()

    # Basic idempotency: if idem_key exists and a live job is found, return it
    if job.idem_key:
        existing_id = await r.get(f"idem:{job.idem_key}")
        if existing_id:
            return existing_id.decode() if isinstance(existing_id, (bytes, bytearray)) else str(existing_id)

    # Queue write
    await r.rpush(QUEUE_KEY, job.to_json())

    # Status hash
    meta = {
        "status": "queued",
        "created_at": job.created_at,
        "updated_at": job.created_at,
        "kind": job.kind,
        "project_dir": job.project_dir,
        "target": job.target,
        "platform": job.platform,
        "commit_msg": job.commit_msg or "",
    }
    await r.hset(_job_key(job.id), mapping=meta)
    await r.expire(_job_key(job.id), settings.job_ttl_seconds)

    # Save idempotency pointer (short TTL while job is active)
    if job.idem_key:
        await r.setex(f"idem:{job.idem_key}", settings.job_ttl_seconds, job.id)

    # Notify listeners
    await _publish_event(r, {"type": "job.queued", "job_id": job.id, "data": meta})

    return job.id


async def dequeue_next(timeout_seconds: int = 1) -> Optional[Tuple[str, BuildJob]]:
    """
    Worker API: pop the next job (blocking up to timeout_seconds).
    Returns (redis_list_key, BuildJob) or None if timed out.
    """
    r: Redis = await get_async_redis()
    res = await r.blpop(QUEUE_KEY, timeout=timeout_seconds)
    if not res:
        return None

    _key, raw = res
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")

    try:
        payload = json.loads(raw)
        job = BuildJob(**payload)
    except Exception:
        # If payload corrupt, drop on floor (and continue)
        return None

    # Mark as started
    await update_job_status(job.id, "started", started_at=_now_iso())
    return QUEUE_KEY, job


# -----------------------------------------------------------------------------
# Status helpers
# -----------------------------------------------------------------------------

async def update_job_status(job_id: str, status: str, **fields: Any) -> None:
    """
    Update job status and arbitrary fields.
    Resets TTL so finished jobs persist for observability but still expire.
    """
    r: Redis = await get_async_redis()
    mapping: Dict[str, str] = {"status": status, "updated_at": _now_iso()}
    mapping.update({k: _to_str(v) for k, v in fields.items()})
    await r.hset(_job_key(job_id), mapping=mapping)
    await r.expire(_job_key(job_id), settings.job_ttl_seconds)
    await _publish_event(r, {"type": f"job.{status}", "job_id": job_id, "data": mapping})


async def complete_job(job_id: str, result: Dict[str, Any] | None = None) -> None:
    """
    Mark job completed and store optional result summary.
    """
    r: Redis = await get_async_redis()
    if result:
        await r.hset(_job_key(job_id), mapping={"result": json.dumps(result, ensure_ascii=False)})
    await update_job_status(job_id, "completed", finished_at=_now_iso())


async def fail_job(job_id: str, error: str, logs: str | None = None) -> None:
    """
    Mark job failed and attach error/logs for later inspection.
    """
    r: Redis = await get_async_redis()
    mapping = {"error": error}
    if logs:
        mapping["logs"] = logs[-100_000:]  # cap logs to ~100 KB
    await r.hset(_job_key(job_id), mapping=mapping)
    await update_job_status(job_id, "failed", finished_at=_now_iso())


async def append_job_log(job_id: str, chunk: str) -> None:
    """
    Append a log line/chunk to a Redis string (kept small) and bump TTL.
    """
    r: Redis = await get_async_redis()
    key = f"{_job_key(job_id)}:log"
    await r.append(key, chunk)
    await r.expire(key, settings.job_ttl_seconds)


async def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Fetch the job hash (status/metadata). Returns {} if not found.
    """
    r: Redis = await get_async_redis()
    data = await r.hgetall(_job_key(job_id))
    # redis-py returns bytes -> decode
    return {k.decode() if isinstance(k, (bytes, bytearray)) else k:
            _maybe_decode(v) for k, v in data.items()} if data else {}


async def get_job_logs(job_id: str, start: int = 0, end: int = -1) -> str:
    """
    Fetch aggregated logs for a job. (Single string)
    """
    r: Redis = await get_async_redis()
    key = f"{_job_key(job_id)}:log"
    raw = await r.getrange(key, start, end)
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="ignore")
    return raw or ""


async def get_queue_length() -> int:
    r: Redis = await get_async_redis()
    return int(await r.llen(QUEUE_KEY))


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

async def _publish_event(r: Redis, payload: Dict[str, Any]) -> None:
    try:
        await r.publish(EVENTS_CHANNEL, json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Best-effort; failures shouldn't break job flow
        pass


def _to_str(v: Any) -> str:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _maybe_decode(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return str(v)
    return v
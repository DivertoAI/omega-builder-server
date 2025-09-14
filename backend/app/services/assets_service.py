# backend/app/services/assets_service.py
from __future__ import annotations

import os
import json
import uuid
from typing import Any, Dict, Optional

from backend.app.core.redis_conn import get_redis
from backend.app.core.config import settings

# Queue name must match ai-vm/workers/assets_worker.py default or env
ASSET_QUEUE = os.getenv("AI_VM_QUEUE_ASSETS", "queue:assets")

# Where the AI-VM and backend can both see files (shared volume in compose)
STAGING_ROOT = os.getenv("OMEGA_STAGING_ROOT", "/workspace/staging")


def enqueue_assets_job(
    *,
    spec: Dict[str, Any],
    brand_name: Optional[str] = None,
    color_hex: Optional[str] = None,
    style: Optional[str] = None,
    kinds: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """
    Push an assets job onto Redis for the AI-VM worker.
    Returns a payload you can hand back to the client.
    """
    job_id = str(uuid.uuid4())[:8]
    # Output dir the worker will write into; must be inside the shared staging root
    output_dir = os.path.join(STAGING_ROOT, "assets", job_id)

    job: Dict[str, Any] = {
        "job_id": job_id,
        "output_dir": output_dir,
        "spec": spec or {},
    }
    if brand_name:
        job["brand_name"] = brand_name
    if color_hex:
        job["color_hex"] = color_hex
    if style:
        job["style"] = style
    if kinds:
        # Optional: let caller choose exactly which asset kinds to generate
        job["kinds"] = kinds

    r = get_redis()
    r.rpush(ASSET_QUEUE, json.dumps(job))

    return {
        "job_id": job_id,
        "queue": ASSET_QUEUE,
        "output_dir": output_dir,
        "kinds": kinds or ["app_icon", "hero_home", "empty_state"],
        "staging_root": STAGING_ROOT,
        "meta": {
            "service": settings.service_name or "omega-builder",
            "version": settings.version or "0.1.0",
        },
    }
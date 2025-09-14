# ai-vm/workers/assets_worker.py
# -----------------------------------------------------------------------------
# Simple asset-generation worker:
# - Listens on a Redis queue for asset jobs
# - Calls OpenAI Images (gpt-image-1 by default) to render PNGs
# - Caches by prompt/model hash to avoid re-billing on identical prompts
# - Writes outputs into a job-specified directory mounted at /workspace
#
# Expected job payload (JSON pushed to Redis list `AI_VM_QUEUE_ASSETS`):
# {
#   "job_id": "uuid-or-string",
#   "output_dir": "/workspace/staging/<something>/assets",
#   "brand_name": "My App",                # optional
#   "color_hex": "#4F46E5",                # optional
#   "style": "clean, modern, high-contrast",  # optional
#   "kinds": ["app_icon","hero_home","empty_state"]  # optional; defaults to all
# }
# -----------------------------------------------------------------------------

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import signal
import sys
import time
from typing import Any, Dict, List, Optional

import redis
from openai import OpenAI, APIError, RateLimitError, APIConnectionError

# ------------------------------ Configuration --------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
ASSET_QUEUE: str = os.getenv("AI_VM_QUEUE_ASSETS", "queue:assets")
MODEL: str = os.getenv("OMEGA_IMAGE_MODEL", "gpt-image-1")
WORKSPACE: str = os.getenv("WORKSPACE_DIR", "/workspace")
CACHE_DIR: str = os.path.join(WORKSPACE, "assets_cache")
POLL_TIMEOUT_SEC: int = int(os.getenv("ASSETS_POLL_TIMEOUT_SEC", "5"))  # BLPOP timeout
MAX_RETRIES_PER_IMAGE: int = int(os.getenv("ASSETS_MAX_RETRIES", "3"))
RETRY_BACKOFF_SEC: float = float(os.getenv("ASSETS_RETRY_BACKOFF_SEC", "2.0"))

# Ensure cache directory exists
pathlib.Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

# OpenAI client (requires OPENAI_API_KEY in environment)
client = OpenAI()

# Redis client
r = redis.Redis.from_url(REDIS_URL)

# ------------------------------- Utilities -----------------------------------

def log(s: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[assets] {ts} {s}", flush=True)

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def _prompt_for(kind: str, job: Dict[str, Any]) -> str:
    """
    Returns a short, deterministic prompt for a given asset 'kind'.
    These prompts are intentionally minimal to keep a consistent look.
    """
    spec = job.get("spec") or {}
    app_name = (job.get("brand_name")
                or spec.get("app", {}).get("name")
                or "Omega App")
    color = job.get("color_hex") or "#4F46E5"
    style = job.get("style") or "clean, modern, high-contrast, mobile-first"

    if kind == "app_icon":
        # Transparent background helps us compose on any platform
        return (
            f"Minimal bold app icon for '{app_name}'. "
            f"Flat vector glyph centered, single background in {color}, "
            f"no text, no gradients, sharp edges, export PNG with transparent background."
        )

    if kind == "hero_home":
        return (
            "Hero banner illustration for a mobile app home screen; "
            f"{style}; brand color {color}; airy, friendly, abstract shapes; "
            "no text; export clean PNG suitable for 1200x600."
        )

    if kind == "empty_state":
        return (
            "Empty state illustration for a mobile screen; "
            f"{style}; gentle character and subtle shapes; brand color {color}; "
            "no text; export PNG with soft contrast."
        )

    # Fallback if a custom kind is passed
    return f"Simple illustration for '{kind}'; {style}; brand color {color}; export PNG."

def _size_for(kind: str) -> str:
    if kind == "app_icon":
        # Apple/Play Store friendly base size
        return "1024x1024"
    if kind == "hero_home":
        return "1200x600"
    # Generic default
    return "800x600"

def _transparent_for(kind: str) -> bool:
    # Icons benefit from transparency; the others typically have backgrounds.
    return kind == "app_icon"

def _ensure_dir(p: str) -> None:
    pathlib.Path(p).mkdir(parents=True, exist_ok=True)

def _generate_image_png(prompt: str, size: str, transparent: bool) -> bytes:
    """
    Call OpenAI Images API and return raw PNG bytes.
    Retries on transient errors.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.images.generate(
                model=MODEL,
                prompt=prompt,
                size=size,
                **({"transparent_background": True} if transparent else {}),
            )
            b64 = resp.data[0].b64_json
            return base64.b64decode(b64)
        except (RateLimitError, APIConnectionError, APIError) as e:
            # Transient-ish: retry a few times
            if attempt < MAX_RETRIES_PER_IMAGE:
                log(f"OpenAI error (attempt {attempt}/{MAX_RETRIES_PER_IMAGE}) -> {e}. Retrying in {RETRY_BACKOFF_SEC}s…")
                time.sleep(RETRY_BACKOFF_SEC)
                continue
            raise
        except Exception:
            # Unexpected; bubble up after a single attempt
            raise

def _render_one(kind: str, job: Dict[str, Any], out_dir: str) -> str:
    """
    Resolve cache -> generate -> write file.
    Returns the output path for the PNG.
    """
    prompt = _prompt_for(kind, job)
    size = _size_for(kind)
    transparent = _transparent_for(kind)

    cache_key = _hash(f"{MODEL}|{kind}|{size}|{transparent}|{prompt}")
    cached = os.path.join(CACHE_DIR, f"{cache_key}.png")
    out_path = os.path.join(out_dir, f"{kind}.png")

    # Use cache if available
    if os.path.exists(cached):
        _ensure_dir(out_dir)
        if not os.path.exists(out_path):
            with open(cached, "rb") as src, open(out_path, "wb") as dst:
                dst.write(src.read())
        return out_path

    # Generate new image
    png_bytes = _generate_image_png(prompt=prompt, size=size, transparent=transparent)

    _ensure_dir(out_dir)
    with open(out_path, "wb") as f:
        f.write(png_bytes)

    # Populate cache
    with open(cached, "wb") as f:
        f.write(png_bytes)

    return out_path

def process(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single job. Returns a small summary dict.
    """
    out_dir = job.get("output_dir")
    if not out_dir:
        raise ValueError("job missing 'output_dir'")

    kinds: List[str] = job.get("kinds") or ["app_icon", "hero_home", "empty_state"]

    results: Dict[str, str] = {}
    for kind in kinds:
        try:
            path = _render_one(kind, job, out_dir)
            log(f"wrote {kind}: {path}")
            results[kind] = path
        except Exception as e:
            log(f"{kind} failed: {e}")
            results[kind] = f"ERROR: {e}"

    return {"job_id": job.get("job_id"), "results": results, "output_dir": out_dir}

# ------------------------------- Main Loop -----------------------------------

_SHOULD_EXIT = False

def _graceful_shutdown(signum, frame):
    global _SHOULD_EXIT
    _SHOULD_EXIT = True
    log(f"signal {signum} received; shutting down after current job.")

def main() -> None:
    # Handle Ctrl+C / docker stop
    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    log(f"worker started; redis={REDIS_URL}, queue={ASSET_QUEUE}, model={MODEL}")
    while not _SHOULD_EXIT:
        try:
            payload = r.blpop(ASSET_QUEUE, timeout=POLL_TIMEOUT_SEC)
            if not payload:
                continue
            _, raw = payload
            try:
                job = json.loads(raw)
            except Exception as e:
                log(f"invalid JSON payload: {e}")
                continue

            log(f"job {job.get('job_id') or '<no-id>'} received")
            summary = process(job)
            log(f"job done: {json.dumps(summary, ensure_ascii=False)}")

        except redis.exceptions.RedisError as re:
            log(f"Redis error: {re}")
            time.sleep(1.0)
        except Exception as e:
            log(f"unhandled error: {e}")

    log("worker stopped.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("KeyboardInterrupt -> exit")
        sys.exit(0)
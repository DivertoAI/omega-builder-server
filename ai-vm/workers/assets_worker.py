# ai-vm/workers/assets_worker.py
from __future__ import annotations

import os, json, time, base64, pathlib, traceback
from typing import Dict, Any, Optional, Tuple

import redis  # redis==5.*
from openai import OpenAI  # openai==1.*

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_KEY = os.getenv("AI_VM_QUEUE_ASSETS", "queue:assets")
MODEL = os.getenv("OMEGA_IMAGE_MODEL", "gpt-image-1")

# Allowed sizes per current API
VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}

# Map our asset kinds to a valid default size
KIND_TO_SIZE = {
    "app_icon": "1024x1024",     # square
    "hero_home": "1536x1024",    # landscape
    "empty_state": "1024x1024",  # safe default (could also be 1536x1024)
}

def _ensure_dir(path: str) -> None:
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)

def _choose_size(kind: str, requested: Optional[str]) -> str:
    if requested and requested in VALID_SIZES:
        return requested
    return KIND_TO_SIZE.get(kind, "1024x1024")

def _safe_filename(name: str) -> str:
    keep = "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(c if c in keep else "_" for c in name).strip()

def _compose_prompt(kind: str, brand: str, color_hex: str, style: str) -> str:
    base = f"Brand: {brand}. Primary color: {color_hex}. Style: {style}."
    if kind == "app_icon":
        return (
            f"{base} Minimal, modern mobile app icon on a transparent or solid background, "
            "centered logo mark, crisp edges, flat aesthetic, no text."
        )
    if kind == "hero_home":
        return (
            f"{base} Clean landing/hero illustration for a mobile app home screen, subtle gradients, "
            "ample negative space, high visual polish, no text."
        )
    if kind == "empty_state":
        return (
            f"{base} Friendly empty state illustration suitable for an app placeholder, soft shapes, "
            "balanced composition, no text."
        )
    return f"{base} High-quality UI illustration, no text."

def _save_b64_png(b64: str, outfile: str) -> None:
    with open(outfile, "wb") as f:
        f.write(base64.b64decode(b64))

def _generate_image(client: OpenAI, kind: str, prompt: str, size: str) -> Tuple[bool, str, Optional[str]]:
    """
    Returns (ok, message, saved_path)
    """
    # Never pass transparent_background (not supported). Only valid sizes.
    try:
        resp = client.images.generate(
            model=MODEL,
            prompt=prompt,
            size=size,            # must be one of VALID_SIZES
            n=1,
        )
        if not resp or not resp.data or not resp.data[0].b64_json:
            return False, "Empty response from image API", None
        return True, "ok", resp.data[0].b64_json
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None

def _run_job(client: OpenAI, job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = job.get("job_id") or job.get("id") or f"job-{int(time.time())}"
    out_dir = job.get("output_dir") or f"/workspace/staging/assets/{job_id}"
    kinds = job.get("kinds") or ["app_icon", "hero_home", "empty_state"]
    brand = job.get("brand_name") or job.get("brand") or "Omega"
    color_hex = job.get("color_hex") or "#4F46E5"
    style = job.get("style") or "clean, modern"
    requested_sizes: Dict[str, str] = job.get("sizes") or {}  # optional per-kind override

    _ensure_dir(out_dir)

    results: Dict[str, str] = {}
    for kind in kinds:
        prompt = _compose_prompt(kind, brand, color_hex, style)
        size = _choose_size(kind, requested_sizes.get(kind))

        # Try once with chosen size; if invalid-size error bubbles up, fall back to 1024x1024
        ok, msg, b64 = _generate_image(client, kind, prompt, size)
        if (not ok) and ("Invalid value" in msg and "size" in msg):
            size_fallback = "1024x1024"
            ok, msg, b64 = _generate_image(client, kind, prompt, size_fallback)

        if not ok or not b64:
            results[kind] = f"ERROR: {msg}"
            continue

        # Save as PNG (API returns PNG)
        fname = _safe_filename(f"{kind}.png")
        out_path = str(pathlib.Path(out_dir) / fname)
        try:
            _save_b64_png(b64, out_path)
            results[kind] = out_path
        except Exception as e:
            results[kind] = f"ERROR: saving file failed: {e}"

    return {
        "job_id": job_id,
        "results": results,
        "output_dir": out_dir,
    }

def main() -> None:
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client = OpenAI()

    print(f"[assets] {time.strftime('%Y-%m-%d %H:%M:%S')} worker started; redis={REDIS_URL}, queue={QUEUE_KEY}, model={MODEL}", flush=True)

    while True:
        try:
            item = r.blpop(QUEUE_KEY, timeout=5)
            if not item:
                continue
            _, payload = item
            try:
                job = json.loads(payload)
            except Exception:
                print(f"[assets] bad payload (not JSON): {payload[:200]}...", flush=True)
                continue

            job_id = job.get("job_id") or job.get("id")
            print(f"[assets] {time.strftime('%Y-%m-%d %H:%M:%S')} job {job_id} received", flush=True)

            result = _run_job(client, job)
            # Publish result (if you have a channel), or just log it
            print(f"[assets] {time.strftime('%Y-%m-%d %H:%M:%S')} job done: {json.dumps(result)}", flush=True)

        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            print(f"[assets] ERROR: {e}\n{tb}", flush=True)
            time.sleep(1)

if __name__ == "__main__":
    main()
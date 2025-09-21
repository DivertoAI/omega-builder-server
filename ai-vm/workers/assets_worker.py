# ai-vm/workers/assets_worker.py
from __future__ import annotations

import os, json, time, base64, pathlib, traceback, shutil
from typing import Dict, Any, Optional, Tuple, List

import redis  # redis==5.*
from openai import OpenAI  # openai==1.*

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_KEY = os.getenv("AI_VM_QUEUE_ASSETS", "queue:assets")
MODEL = os.getenv("OMEGA_IMAGE_MODEL", "gpt-image-1")

# Log file (for smoke scripts that tail logs even when the worker runs in foreground)
LOG_PATH = "/tmp/assets_worker.log"

# Allowed sizes per current API
VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}

# Map our asset kinds to a valid default size
KIND_TO_SIZE = {
    "app_icon": "1024x1024",     # square
    "hero_home": "1536x1024",    # landscape
    "empty_state": "1024x1024",  # safe default
}

def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[assets] {ts} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        # never crash on logging
        pass

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

def _decode_b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)

def _write_bytes(data: bytes, path: str) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "wb") as f:
        f.write(data)

def _generate_image(client: OpenAI, prompt: str, size: str) -> Tuple[bool, str, Optional[str]]:
    """
    Returns (ok, message, b64_image)
    """
    try:
        resp = client.images.generate(
            model=MODEL,
            prompt=prompt,
            size=size,   # must be one of VALID_SIZES (or 'auto')
            n=1,
        )
        if not resp or not resp.data or not getattr(resp.data[0], "b64_json", None):
            return False, "Empty response from image API", None
        return True, "ok", resp.data[0].b64_json
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None

def _collect_alias_dirs(job: Dict[str, Any]) -> List[str]:
    """
    Some callers/smoke scripts expect files in a *requested* directory even if the API
    normalized to a different output_dir. We optionally mirror results to:
      - job['requested_output_dir'] if present
      - any paths in job['output_dir_aliases'] (list)
    """
    aliases: List[str] = []
    req = job.get("requested_output_dir")
    if isinstance(req, str) and req.strip():
        aliases.append(req.strip())
    extra = job.get("output_dir_aliases")
    if isinstance(extra, list):
        aliases.extend([str(p) for p in extra if isinstance(p, (str, pathlib.Path))])
    # de-dup
    uniq: List[str] = []
    seen = set()
    for p in aliases:
        p = str(p)
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq

def _save_to_all_targets(png_bytes: bytes, primary_dir: str, aliases: List[str], filename: str) -> Dict[str, str]:
    """
    Write the same file to the primary output dir and to any alias dirs.
    Returns a map of {dir: path or 'ERROR: ...'} for reporting.
    """
    status: Dict[str, str] = {}
    # primary
    primary_path = os.path.join(primary_dir, filename)
    try:
        _write_bytes(png_bytes, primary_path)
        status[primary_dir] = primary_path
    except Exception as e:
        status[primary_dir] = f"ERROR: saving file failed: {e}"

    # aliases
    for alias_dir in aliases:
        alias_path = os.path.join(alias_dir, filename)
        try:
            _write_bytes(png_bytes, alias_path)
            status[alias_dir] = alias_path
        except Exception as e:
            status[alias_dir] = f"ERROR: saving alias failed: {e}"

    return status

def _run_job(client: OpenAI, job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = job.get("job_id") or job.get("id") or f"job-{int(time.time())}"
    # Primary output_dir (API may have normalized this)
    out_dir = job.get("output_dir") or f"/workspace/staging/assets/{job_id}"
    # Optional mirrors for compatibility with smoke scripts or legacy callers
    alias_dirs = _collect_alias_dirs(job)

    kinds = job.get("kinds") or ["app_icon", "hero_home", "empty_state"]
    brand = job.get("brand_name") or job.get("brand") or "Omega"
    color_hex = job.get("color_hex") or "#4F46E5"
    style = job.get("style") or "clean, modern"
    requested_sizes: Dict[str, str] = job.get("sizes") or {}  # optional per-kind override

    _ensure_dir(out_dir)
    for ad in alias_dirs:
        _ensure_dir(ad)

    results: Dict[str, Any] = {}
    for kind in kinds:
        prompt = _compose_prompt(kind, brand, color_hex, style)
        size = _choose_size(kind, requested_sizes.get(kind))

        # Try once with chosen size; if invalid-size error bubbles up, fall back to 1024x1024
        ok, msg, b64 = _generate_image(client, prompt, size)
        if (not ok) and ("Invalid value" in msg and "size" in msg):
            _log(f"size '{size}' rejected; falling back to 1024x1024 for {kind}")
            ok, msg, b64 = _generate_image(client, prompt, "1024x1024")

        if not ok or not b64:
            results[kind] = f"ERROR: {msg}"
            continue

        try:
            png_bytes = _decode_b64_to_bytes(b64)
        except Exception as e:
            results[kind] = f"ERROR: decode failed: {e}"
            continue

        filename = _safe_filename(f"{kind}.png")
        write_report = _save_to_all_targets(png_bytes, out_dir, alias_dirs, filename)

        # Prefer to report the primary path; include aliases for debugging
        results[kind] = {
            "primary": write_report.get(out_dir),
            "aliases": {d: p for d, p in write_report.items() if d != out_dir},
        }

    return {
        "job_id": job_id,
        "results": results,
        "output_dir": out_dir,
        "aliases": alias_dirs,
    }

def main() -> None:
    # ensure log file exists
    try:
        _ensure_dir(os.path.dirname(LOG_PATH))
        with open(LOG_PATH, "a"):
            pass
    except Exception:
        pass

    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client = OpenAI()

    _log(f"worker started; redis={REDIS_URL}, queue={QUEUE_KEY}, model={MODEL}")

    while True:
        try:
            item = r.blpop(QUEUE_KEY, timeout=5)
            if not item:
                continue
            _, payload = item
            try:
                job = json.loads(payload)
            except Exception:
                _log(f"bad payload (not JSON): {payload[:200]}...")
                continue

            job_id = job.get("job_id") or job.get("id")
            _log(f"job {job_id} received")

            result = _run_job(client, job)
            _log(f"job done: {json.dumps(result)}")

        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            _log(f"ERROR: {e}\n{tb}")
            time.sleep(1)

if __name__ == "__main__":
    main()
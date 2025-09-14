# backend/app/services/assets_service.py
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client

ASSET_ROOT = Path("/workspace/staging/assets")

@dataclass
class AssetTask:
    prompt: str
    filename: str
    size: str = "1024x1024"

def _ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)

def _transparent_bg_for(filename: str) -> Optional[str]:
    return "transparent" if filename.lower().endswith(".png") else None

def generate_assets_batch(style_hint: Optional[str], tasks: List[AssetTask]) -> Dict[str, Any]:
    client = get_openai_client()
    if not client.enabled:
        raise RuntimeError("OpenAI client disabled (missing API key or SDK).")

    model = getattr(settings, "omega_image_model", "gpt-image-1") or "gpt-image-1"
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    written: List[str] = []

    for t in tasks:
        out_path = (ASSET_ROOT / t.filename.lstrip("/")).resolve()
        if ASSET_ROOT not in out_path.parents and out_path != ASSET_ROOT:
            results.append({"filename": t.filename, "ok": False, "error": "refusing to write outside assets root"})
            continue

        prompt = t.prompt.strip()
        if style_hint and style_hint.strip():
            prompt = f"{prompt}\n\nStyle: {style_hint.strip()}"

        kwargs: Dict[str, Any] = {"model": model, "prompt": prompt, "size": t.size}
        bg = _transparent_bg_for(t.filename)
        if bg:
            kwargs["background"] = bg

        try:
            # DO NOT PASS response_format. API returns b64_json by default.
            resp = client._client.images.generate(**kwargs)  # type: ignore[attr-defined]
            data = getattr(resp, "data", None) or []
            if not data:
                raise RuntimeError("images.generate returned no data")

            b64 = getattr(data[0], "b64_json", None)
            if b64 is None and isinstance(data[0], dict):
                b64 = data[0].get("b64_json")
            if not isinstance(b64, str) or not b64:
                raise RuntimeError("missing b64_json in response")

            raw = base64.b64decode(b64)
            _ensure_parent(out_path)
            out_path.write_bytes(raw)
            written.append(str(out_path))
            results.append({"filename": t.filename, "size": t.size, "bytes": len(raw), "background": bg or "default", "ok": True})
        except Exception as e:
            results.append({"filename": t.filename, "ok": False, "error": str(e)})

    return {"ok": all(r.get("ok") for r in results) if results else False, "root": str(ASSET_ROOT), "written": written, "results": results, "count": len(results), "model": model}
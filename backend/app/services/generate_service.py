# backend/app/services/generate_service.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import OmegaSpec


_CODEGEN_SYSTEM = """You are the Code Generator for Omega Builder.
You will receive a validated OmegaSpec (JSON). Produce a small, runnable scaffold as a set of files.

Rules:
- Return ONLY a JSON object with the following shape:
  {
    "files": [
      {"path": "<relative path under project>", "language": "<mime or short label>", "contents": "<file text>"},
      ...
    ],
    "notes": "<very brief generation notes>"
  }
- Keep it minimal but runnable; prioritize a README and one simple app shell matching the spec's navigation.home.
- No placeholders like TODO in code; keep it clean and concise.
- Use UTF-8 text files only. No binaries.
- Prefer Flutter if spec hints an app, otherwise a simple web app (HTML+JS) is acceptable.
"""

_CODEGEN_USER_TEMPLATE = """OmegaSpec:
{spec_json}

Output only the JSON manifest described above (no markdown, no code fences)."""


def _safe_write(root: Path, rel_path: str, text: str) -> str:
    rel = rel_path.strip().lstrip("/").replace("\\", "/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError(f"Refusing to write outside staging: {rel_path!r}")
    out_path = root / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return str(out_path.relative_to(root))


def _fallback_scaffold(spec: OmegaSpec, reason: str = "unknown") -> Dict[str, Any]:
    files: List[Dict[str, str]] = []
    readme = f"""# {spec.name}

{spec.description}

This is a minimal scaffold generated without remote codegen (OpenAI disabled or unreachable).

## Contents
- `README.md`
- `web/index.html`

## Run (static)
Open `web/index.html` in a browser.
"""
    files.append({"path": "README.md", "language": "text/markdown", "contents": readme})

    home = spec.navigation.home or "home"
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>{spec.name}</title>
    <style>
      :root {{ --radius: {spec.theme.radius[0] if spec.theme.radius else 8}px; }}
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:0; }}
      header {{ padding:16px; border-bottom:1px solid #ddd; }}
      main {{ padding:24px; }}
      .card {{ border:1px solid #e6e6e6; border-radius: var(--radius); padding:16px; }}
    </style>
  </head>
  <body>
    <header><strong>{spec.name}</strong></header>
    <main>
      <div class="card">
        <h1>{home.title() if isinstance(home, str) else "Home"}</h1>
        <p>{spec.description}</p>
        <p>OpenAI codegen is disabled or failed; this is a fallback static stub.</p>
      </div>
    </main>
  </body>
</html>
"""
    files.append({"path": "web/index.html", "language": "text/html", "contents": html})
    return {"files": files, "notes": f"local fallback scaffold (reason={reason})"}


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("LLM output is not text")
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        return json.loads(s)
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start : i + 1])
    raise ValueError("Unbalanced JSON braces in LLM output")


def _prompt_codegen(spec: OmegaSpec) -> Dict[str, Any]:
    """
    Call OpenAI Responses (GPT-5 by default) to produce a JSON file manifest.
    No 'temperature' param (GPT-5 rejects it in Responses).
    """
    client = get_openai_client()
    if not client.enabled or not settings.openai_enabled:
        return _fallback_scaffold(spec, reason="openai disabled")

    spec_json = json.dumps(spec.model_dump(), ensure_ascii=False, indent=2)
    input_text = _CODEGEN_USER_TEMPLATE.format(spec_json=spec_json)

    model = (
        getattr(settings, "omega_codegen_model", None)
        or getattr(settings, "effective_codegen_model", None)
        or settings.omega_llm_model
    )

    # Build request without unsupported params
    req: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": _CODEGEN_SYSTEM},
            {"role": "user", "content": input_text},
        ],
        "max_output_tokens": 4000,
        # NOTE: Do not include `temperature` or `response_format` here.
    }

    try:
        resp = client._client.responses.create(**req)  # type: ignore[attr-defined]
    except Exception as e:
        return _fallback_scaffold(spec, reason=f"responses.create error: {type(e).__name__}: {e}")

    # Extract text robustly
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
        output_text = resp.output_text
    else:
        chunks: List[str] = []
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", None) == "message":
                for c in getattr(item, "content", []) or []:
                    t = getattr(c, "text", None)
                    if isinstance(t, str):
                        chunks.append(t)
        output_text = "".join(chunks)

    # Parse the model output into a manifest
    try:
        manifest = json.loads(output_text)
    except Exception:
        try:
            manifest = _extract_first_json_object(output_text)
        except Exception as e2:
            return _fallback_scaffold(spec, reason=f"bad manifest: {type(e2).__name__}")

    if not isinstance(manifest, dict) or "files" not in manifest:
        return _fallback_scaffold(spec, reason="invalid manifest shape")

    return manifest


def generate_artifacts(spec: OmegaSpec, staging_root: Path) -> Union[Dict[str, Any], List[Any]]:
    staging_root = Path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    manifest = _prompt_codegen(spec)
    files = manifest.get("files", [])
    written: List[Dict[str, str]] = []

    for f in files:
        path = str(f.get("path", "")).strip() if isinstance(f, dict) else str(f).strip()
        contents = f.get("contents", "") if isinstance(f, dict) else ""
        if not path:
            continue
        try:
            rel_written = _safe_write(staging_root, path, contents)
            written.append({"path": rel_written})
        except Exception as e:
            written.append({"path": path, "error": f"{type(e).__name__}: {e}"})

    return {"files": written, "notes": manifest.get("notes", "")}


def generate(spec: OmegaSpec, staging_root: Path) -> Union[Dict[str, Any], List[Any]]:
    return generate_artifacts(spec, staging_root)
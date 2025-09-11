# backend/app/services/generate_service.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import OmegaSpec

log = logging.getLogger(__name__)

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
- If you include anything other than a single JSON object, your answer will be discarded.
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
    # Try stripping code fences anywhere
    if "```" in s:
        # take the first fenced block if present
        parts = s.split("```")
        # try to find a block that looks like JSON
        for p in parts:
            pj = p.strip()
            if "{" in pj and "}" in pj:
                try:
                    start = pj.index("{")
                    depth = 0
                    for i in range(start, len(pj)):
                        ch = pj[i]
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                return json.loads(pj[start : i + 1])
                except Exception:
                    pass
    # Plain single object path
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
    Call OpenAI Responses (o3 / gpt-5) to produce a JSON file manifest.
    Strategy:
      1) Try JSON mode + broad prompt.
      2) If output truncated or parse fails, retry once with a 'tight' prompt that
         forces EXACTLY three files: README.md, pubspec.yaml, lib/main.dart.
    """
    client = get_openai_client()
    if not client.enabled or not settings.openai_enabled:
        return _fallback_scaffold(spec, reason="openai disabled")

    # ---- Prompts (broad vs tight) -------------------------------------------
    base_rules = """You are the Code Generator for Omega Builder.
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
- Prefer Flutter if spec hints an app; otherwise a simple web app (HTML+JS) is acceptable.
"""

    tight_rules = base_rules + """
IMPORTANT SIZE LIMITS:
- Output EXACTLY THREE files total:
  1) README.md
  2) pubspec.yaml
  3) lib/main.dart
- Keep contents short and minimal, just enough to run a hello-style app that matches navigation.home.
- Do not include any other files. No code fences. No commentary outside the JSON object.
"""

    spec_json = json.dumps(spec.model_dump(), ensure_ascii=False, indent=2)
    user_payload = f"""OmegaSpec:
{spec_json}

Output only the JSON manifest described above (no markdown, no code fences)."""

    model = (
        getattr(settings, "omega_codegen_model", None)
        or getattr(settings, "effective_codegen_model", None)
        or settings.omega_llm_model
    )

    def _mk_messages(system_text: str) -> list[dict]:
        return [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user",   "content": [{"type": "input_text", "text": user_payload}]},
        ]

    def _responses_call(system_text: str, use_json_mode: bool) -> tuple[str, Any]:
        # Build request
        req: Dict[str, Any] = {
            "model": model,
            "input": _mk_messages(system_text),
            "max_output_tokens": 6000,  # roomy but not excessive
        }
        if use_json_mode:
            req["response_format"] = {"type": "json_object"}
        if isinstance(model, str) and model.lower().startswith("gpt-5"):
            req["reasoning"] = {"effort": "medium"}

        # Call Responses.create
        resp = client._client.responses.create(**req)  # type: ignore[attr-defined]

        # Extract text (prefers resp.output_text if available)
        if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
            text = resp.output_text
        else:
            chunks: List[str] = []
            for item in getattr(resp, "output", []) or []:
                if (getattr(item, "type", None) or getattr(item, "get", lambda k, d=None: d)("type")) == "message":
                    content = getattr(item, "content", []) or (item.get("content", []) if isinstance(item, dict) else [])
                    for c in content:
                        if isinstance(c, dict):
                            t = c.get("text")
                            if isinstance(t, str):
                                chunks.append(t)
                            elif isinstance(t, dict):
                                v = t.get("value")
                                if isinstance(v, str):
                                    chunks.append(v)
                        else:
                            t = getattr(c, "text", None)
                            if isinstance(t, str):
                                chunks.append(t)
                            else:
                                v = getattr(getattr(c, "text", None), "value", None)
                                if isinstance(v, str):
                                    chunks.append(v)
            text = "".join(chunks)

        return text, resp

    def _was_truncated(resp: Any) -> bool:
        """
        Detects if any output message finished due to length/incomplete.
        """
        try:
            for item in getattr(resp, "output", []) or []:
                fr = getattr(item, "finish_reason", None)
                if fr is None and isinstance(item, dict):
                    fr = item.get("finish_reason")
                if isinstance(fr, str) and fr.lower() in {"length", "incomplete"}:
                    return True
        except Exception:
            pass
        return False

    def _strip_fences(s: str) -> str:
        s = s.strip()
        if s.startswith("```"):
            # remove surrounding fences crudely, then re-seek first '{'
            s = s.strip("`").strip()
            i = s.find("{")
            if i != -1:
                s = s[i:]
        return s

    # ---- Attempt #1: JSON mode + broad rules --------------------------------
    try:
        raw_text, resp1 = _responses_call(base_rules, use_json_mode=True)
    except Exception as e:
        # Some SDK builds reject response_format on Responses; retry without JSON mode.
        try:
            raw_text, resp1 = _responses_call(base_rules, use_json_mode=False)
        except Exception as e2:
            return _fallback_scaffold(spec, reason=f"responses.create error: {type(e2).__name__}: {e2}")

    text1 = _strip_fences(raw_text or "")
    truncated1 = _was_truncated(resp1)

    # Try to parse manifest
    def _parse_manifest(s: str) -> Optional[Dict[str, Any]]:
        if not isinstance(s, str) or not s.strip():
            return None
        try:
            return json.loads(s)
        except Exception:
            try:
                return _extract_first_json_object(s)
            except Exception:
                return None

    manifest = _parse_manifest(text1)

    # If truncated or parse failed, do one tightened retry
    if truncated1 or not (isinstance(manifest, dict) and "files" in manifest):
        try:
            raw_text2, resp2 = _responses_call(tight_rules, use_json_mode=True)
        except Exception:
            try:
                raw_text2, resp2 = _responses_call(tight_rules, use_json_mode=False)
            except Exception as e3:
                return _fallback_scaffold(spec, reason=f"responses.create error: {type(e3).__name__}: {e3}")

        text2 = _strip_fences(raw_text2 or "")
        manifest2 = _parse_manifest(text2)
        if isinstance(manifest2, dict) and "files" in manifest2:
            return manifest2

        # Still not good → fallback with reason
        reason = "truncated output" if _was_truncated(resp2) else "bad manifest: ValueError"
        return _fallback_scaffold(spec, reason=reason)

    # Success on first attempt
    return manifest  # type: ignore[return-value]

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
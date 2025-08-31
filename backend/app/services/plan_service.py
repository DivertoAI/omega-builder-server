from __future__ import annotations

import json as _json
import os
from typing import Any, Dict, Tuple

from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import OmegaSpec, validate_spec

# --- tiny, safe default so tests & local dev don't depend on OpenAI ---

_DEFAULT_SPEC: Dict[str, Any] = {
    "name": "Omega App",
    "description": "Starter OmegaSpec produced by fallback planner.",
    "theme": {
        "colors": [],
        "typography": [],
        "radius": [6, 10],  # must be a list per model
    },
    # Navigation must be an object with required keys:
    "navigation": {"home": "/", "items": []},
    "endpoints": [],
    "entities": [],
    "acceptance": [
        {
            "description": "Service boots and health endpoint returns ok.",
            "details": "GET /api/health responds with status ok.",
        }
    ],
}


def _fallback_plan(brief: str) -> Dict[str, Any]:
    """
    Produce a tiny but valid OmegaSpec without hitting OpenAI.
    Keeps tests & smoke paths deterministic.
    """
    spec = dict(_DEFAULT_SPEC)
    spec["description"] = f"{_DEFAULT_SPEC['description']} Brief: {brief[:160]}"
    return spec


def _strip_fences(s: str) -> str:
    """Remove common markdown code fences around JSON, if present."""
    if not s:
        return s
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
    if s.endswith("```"):
        s = s.rsplit("\n", 1)[0]
    return s.strip()


def _try_parse_json(text: str) -> Dict[str, Any] | None:
    """Best-effort JSON parse from model output."""
    if not text:
        return None
    text = _strip_fences(text)
    try:
        data = _json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _normalize_common_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize fields the model often gets wrong so validation succeeds more often.
    This is safe and deterministic and runs before final validation.
    """
    out = dict(obj)

    # theme.radius must be a list of numbers
    theme = dict(out.get("theme") or {})
    radius = theme.get("radius")
    if isinstance(radius, (int, float)):
        theme["radius"] = [int(radius)]
    elif radius is None:
        theme["radius"] = [6, 10]
    out["theme"] = theme

    # navigation must be an object with required keys: home (str), items (list)
    nav = out.get("navigation")
    if nav is None or isinstance(nav, list) or not isinstance(nav, dict):
        out["navigation"] = {"home": "/", "items": []}
    else:
        # fill missing required keys
        nav = dict(nav)
        if "home" not in nav or not isinstance(nav.get("home"), str):
            nav["home"] = "/"
        if "items" not in nav or not isinstance(nav.get("items"), list):
            nav["items"] = []
        out["navigation"] = nav

    # endpoints/entities should be arrays; coerce None to []
    if out.get("endpoints") is None:
        out["endpoints"] = []
    if out.get("entities") is None:
        out["entities"] = []

    return out


def _call_openai_for_spec(brief: str) -> Dict[str, Any]:
    """
    Ask the model for a JSON OmegaSpec, using the newest Responses API when available,
    otherwise falling back to Chat Completions for older SDKs.
    This MUST return a dict (valid or not). The caller handles normalization/validation.
    """
    client = get_openai_client()

    system = (
        "You are a planner that outputs ONLY a valid JSON OmegaSpec. "
        "Do not include prose or markdown fences. The JSON must match the OmegaSpec schema. "
        "theme.radius MUST be a list of numbers, not a single number. "
        "navigation MUST be an object with keys 'home' (string) and 'items' (array)."
    )
    user = f"Create an OmegaSpec for this brief:\n\n{brief}\n"

    model = os.getenv("OMEGA_LLM_MODEL", "gpt-5")

    # --- Path A: New Responses API (preferred) ---
    if hasattr(client, "responses"):
        try:
            resp = client.responses.create(
                model=model,
                temperature=0.2,
                reasoning={"effort": "medium"},
                response_format={"type": "json_object"},
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=90,
            )
            if hasattr(resp, "output_parsed") and resp.output_parsed:
                return dict(resp.output_parsed)
            text = getattr(resp, "output_text", "") or ""
            parsed = _try_parse_json(text)
            if parsed is not None:
                return parsed
        except Exception:
            pass

    # --- Path B: Legacy Chat Completions API ---
    if hasattr(client, "chat") and hasattr(getattr(client, "chat"), "completions"):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            text = ""
            try:
                text = resp.choices[0].message.content  # type: ignore[attr-defined]
            except Exception:
                text = ""
            parsed = _try_parse_json(text)
            if parsed is not None:
                return parsed
        except Exception:
            pass

    # If model returned non-JSON or APIs failed, drop to deterministic fallback
    return _fallback_plan(brief)


def plan_and_validate(brief: str, max_repairs: int = 1) -> Tuple[OmegaSpec, Dict[str, Any]]:
    """
    Main entry: turn a brief into a validated OmegaSpec.
    Returns: (spec_model, raw_dict_used)
    """
    use_openai = bool(os.getenv("OPENAI_API_KEY"))
    raw: Dict[str, Any] = _call_openai_for_spec(brief) if use_openai else _fallback_plan(brief)

    # Normalize BEFORE first validation
    first = _normalize_common_fields(raw)

    try:
        spec = validate_spec(first)
        return spec, first
    except Exception:
        # One more normalization pass if allowed (idempotent anyway)
        repaired = _normalize_common_fields(first)
        spec = validate_spec(repaired)  # raise if still invalid
        return spec, repaired
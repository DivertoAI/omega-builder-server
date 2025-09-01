# backend/app/services/plan_service.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import validate_spec


_PLANNER_SYSTEM = """You are the Planner for Omega Builder.

Your job: from a short natural-language brief, emit a *concise, runnable OmegaSpec JSON object*.
Rules:
- Output ONLY a single JSON object (no prose).
- Keep it minimal but useful: name, description, theme, navigation, entities, apis, acceptance.
- theme.radius MUST be an array of integers (e.g., [6,10]).
- navigation MUST have "home" (string route id) and "items" (array)—empty array is ok.
- entities and apis arrays may be empty.
- Provide at least one acceptance item that checks service health (e.g., GET /api/health returns ok).
- No code, no templates, no comments—just the JSON spec.
"""

_PLANNER_USER_TEMPLATE = """Brief:
{brief}

Emit only the OmegaSpec JSON (no backticks)."""


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction of the first top-level JSON object from a text blob.
    """
    if not isinstance(text, str):
        raise ValueError("Planner output is not text")

    # Fast path: looks like pure JSON object already
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return json.loads(stripped)

    # Fallback: find the first {...} block
    start = stripped.find("{")
    if start == -1:
        raise ValueError("No JSON object found in planner output")
    # naive brace matching
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = stripped[start : i + 1]
                return json.loads(obj)
    raise ValueError("Unbalanced JSON braces in planner output")


def _llm_plan(brief: str) -> Dict[str, Any]:
    """
    Ask the LLM to propose an OmegaSpec (as JSON). Raise on failure.
    """
    client = get_openai_client()

    resp = client._client.chat.completions.create(
        model=settings.omega_llm_model,
        messages=[
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": _PLANNER_USER_TEMPLATE.format(brief=brief.strip())},
        ],
        temperature=0.2,
    )
    text = resp.choices[0].message.content or ""
    return _extract_first_json_object(text)


def _force_list_radius(value: Any) -> list:
    if value is None:
        return [8]
    if isinstance(value, list):
        # keep only ints and clamp within a small, safe range
        out = []
        for v in value:
            if isinstance(v, (int, float)):
                out.append(int(max(0, min(64, int(v)))))
        return out or [8]
    if isinstance(value, (int, float)):
        v = int(max(0, min(64, int(value))))
        return [v]
    # anything else -> default
    return [8]


def _ensure_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _ensure_str(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _repair_navigation(nav: Any) -> Dict[str, Any]:
    if not isinstance(nav, dict):
        nav = {}
    home = _ensure_str(nav.get("home"), "home")
    items = nav.get("items")
    if not isinstance(items, list):
        items = []
    # ensure items are strings or simple objects with id/title
    normalized = []
    for it in items:
        if isinstance(it, str):
            normalized.append(it)
        elif isinstance(it, dict):
            # e.g., {"id": "home", "title": "Home"}
            rid = _ensure_str(it.get("id"), "")
            if rid:
                title = _ensure_str(it.get("title"), rid.title())
                normalized.append({"id": rid, "title": title})
    return {"home": home, "items": normalized}


def _repair_acceptance(acc: Any) -> list:
    acc_list = _ensure_list(acc)
    # ensure at least a health check acceptance exists
    has_health = any(
        isinstance(a, dict) and _ensure_str(a.get("description"), "").lower().find("health") != -1
        for a in acc_list
    )
    if not has_health:
        acc_list.append(
            {
                "id": "service-boots-and-health-endpoint-returns-ok",
                "description": "Service boots and health endpoint returns ok.",
            }
        )
    return acc_list


def auto_repair_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Make a best-effort pass to satisfy OmegaSpec validation without inventing code.
    We *only* normalize structure/defaults; no scaffold files are produced here.
    """
    if not isinstance(spec, dict):
        spec = {}

    # name / description
    spec["name"] = _ensure_str(spec.get("name"), "Omega App")
    spec["description"] = _ensure_str(
        spec.get("description"),
        "OmegaSpec derived from brief.",
    )

    # theme
    theme = spec.get("theme")
    if not isinstance(theme, dict):
        theme = {}
    theme["colors"] = _ensure_list(theme.get("colors"))
    theme["typography"] = _ensure_list(theme.get("typography"))
    theme["radius"] = _force_list_radius(theme.get("radius"))
    spec["theme"] = theme

    # navigation
    spec["navigation"] = _repair_navigation(spec.get("navigation"))

    # entities / apis
    spec["entities"] = _ensure_list(spec.get("entities"))
    spec["apis"] = _ensure_list(spec.get("apis"))

    # acceptance
    spec["acceptance"] = _repair_acceptance(spec.get("acceptance"))

    return spec


def plan_and_validate(brief: str, max_repairs: int = 1) -> Tuple[Any, Dict[str, Any]]:
    """
    Plan from a brief using the LLM. Validate into an OmegaSpec.
    If validation fails, apply auto-repair up to max_repairs times.

    Returns:
        (validated_spec_model, raw_spec_dict)
    Raises:
        Exception on repeated validation failure.
    """
    # 1) Ask the model for a spec proposal
    try:
        raw = _llm_plan(brief)
    except Exception:
        # If planner fails entirely, synthesize a *minimal* raw spec from the brief.
        # This is a structural fallback only (no code templates).
        raw = {
            "name": "Omega App",
            "description": f"OmegaSpec derived from brief: {brief.strip()}",
            "theme": {"colors": [], "typography": [], "radius": [8]},
            "navigation": {"home": "home", "items": []},
            "entities": [],
            "apis": [],
            "acceptance": [
                {
                    "id": "service-boots-and-health-endpoint-returns-ok",
                    "description": "Service boots and health endpoint returns ok.",
                }
            ],
        }

    # 2) Validate or repair
    errors: list[str] = []
    attempt = 0
    current = raw
    while True:
        attempt += 1
        try:
            model = validate_spec(current)
            return model, raw  # keep original raw for transparency
        except Exception as e:
            errors.append(str(e))
            if attempt > max_repairs:
                raise ValueError(
                    "Failed to validate OmegaSpec after repairs:\n" + "\n".join(f"- {err}" for err in errors)
                )
            current = auto_repair_spec(current)
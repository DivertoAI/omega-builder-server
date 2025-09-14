# backend/app/services/plan_service.py
from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import validate_spec


# =========================
# Prompts
# =========================

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

# A lightweight “coder pass” that keeps us adaptive (no baked templates) while
# letting GPT-5 tighten or extend the spec strictly within JSON.
_CODER_SYSTEM = """You are the Coder for Omega Builder.

Given an OmegaSpec JSON object, refine it ONLY by editing the JSON to improve usefulness while staying minimal.
Strict rules:
- Output ONLY a single JSON object (no prose).
- Preserve the same top-level schema: name, description, theme{colors,typography,radius}, navigation{home,items}, entities, apis, acceptance.
- theme.radius MUST remain an array of integers.
- navigation MUST have "home" and "items".
- entities/apis can be empty arrays.
- Ensure there is at least one acceptance item for health check.
- Make small, adaptive improvements (e.g., clearer names/descriptions, sensible default nav items) without adding any code or templates.
"""

_CODER_USER_TEMPLATE = """Here is the current OmegaSpec JSON:

{spec_json}

Output ONLY the refined OmegaSpec JSON (no backticks)."""


# =========================
# Utilities
# =========================

def _extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction of the first top-level JSON object from a text blob.
    """
    if not isinstance(text, str):
        raise ValueError("LLM output is not text")

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return json.loads(stripped)

    # Fallback: find the first {...} block via naive brace matching
    start = stripped.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = stripped[start: i + 1]
                return json.loads(obj)
    raise ValueError("Unbalanced JSON braces in LLM output")


# =========================
# LLM calls (via Responses API)
# =========================

def _llm_plan(brief: str) -> Dict[str, Any]:
    """
    Ask the PLANNER model (o3 family or similar) to propose an OmegaSpec (as JSON).
    Uses OpenAI Responses through our client wrapper to avoid /v1/chat.completions 400s.
    """
    client = get_openai_client()
    planner_model = getattr(settings, "omega_planner_model", settings.omega_llm_model)

    text = client.respond(
        model=planner_model,
        messages=[
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": _PLANNER_USER_TEMPLATE.format(brief=brief.strip())},
        ],
        max_output_tokens=2048,
    ) or ""

    # Parse as JSON (with tolerant fallback)
    try:
        return json.loads(text)
    except Exception:
        return _extract_first_json_object(text)


def _llm_coder_refine(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ask the CODER model (GPT-5 by default) to refine the spec, still JSON-only.
    Routes through Responses as well.
    """
    client = get_openai_client()
    spec_json = json.dumps(spec, ensure_ascii=False)

    code_model = (
        getattr(settings, "effective_codegen_model", None)
        or getattr(settings, "omega_coder_model", None)
        or getattr(settings, "omega_llm_model", "gpt-5")
    )

    text = client.respond(
        model=code_model,
        messages=[
            {"role": "system", "content": _CODER_SYSTEM},
            {"role": "user", "content": _CODER_USER_TEMPLATE.format(spec_json=spec_json)},
        ],
        max_output_tokens=2048,
    ) or ""

    try:
        return json.loads(text)
    except Exception:
        return _extract_first_json_object(text)


# =========================
# Normalizers / repairs
# =========================

def _force_list_radius(value: Any) -> list[int]:
    if value is None:
        return [8]
    if isinstance(value, list):
        out: list[int] = []
        for v in value:
            if isinstance(v, (int, float)):
                out.append(int(max(0, min(64, int(v)))))
        return out or [8]
    if isinstance(value, (int, float)):
        v = int(max(0, min(64, int(value))))
        return [v]
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
    normalized = []
    for it in items:
        if isinstance(it, str) and it.strip():
            normalized.append(it.strip())
        elif isinstance(it, dict):
            rid = _ensure_str(it.get("id"), "")
            if rid:
                title = _ensure_str(it.get("title"), rid.title())
                normalized.append({"id": rid, "title": title})
    return {"home": home, "items": normalized}


def _normalize_kv_items(items: Any) -> list[dict]:
    """
    For entities/apis: accept strings or dicts; return [{id, title?}] with valid 'id'.
    Drop invalid entries.
    """
    out: list[dict] = []
    for it in (items if isinstance(items, list) else []):
        if isinstance(it, str) and it.strip():
            rid = it.strip()
            out.append({"id": rid, "title": rid.title()})
        elif isinstance(it, dict):
            rid = _ensure_str(it.get("id"), "")
            if rid:
                title = _ensure_str(it.get("title"), rid.title())
                d = dict(it)
                d["id"] = rid
                d.setdefault("title", title)
                out.append(d)
    return out


def _repair_acceptance(acc: Any) -> list[dict]:
    """
    Ensure at least one health check with id+description exists.
    Normalize existing items to have id/description when possible; drop the rest.
    """
    out: list[dict] = []
    for it in (acc if isinstance(acc, list) else []):
        if isinstance(it, dict):
            did = _ensure_str(it.get("id"), "")
            desc = _ensure_str(it.get("description"), "")
            if did and desc:
                out.append({"id": did, "description": desc})
    # Ensure health check present
    has_health = any("health" in (a.get("description", "").lower()) for a in out)
    if not has_health:
        out.append(
            {
                "id": "service-boots-and-health-endpoint-returns-ok",
                "description": "Service boots and health endpoint returns ok.",
            }
        )
    return out


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

    # entities / apis (force to list of dicts with id)
    spec["entities"] = _normalize_kv_items(spec.get("entities"))
    spec["apis"] = _normalize_kv_items(spec.get("apis"))

    # acceptance (ensure at least one valid health check)
    spec["acceptance"] = _repair_acceptance(spec.get("acceptance"))

    return spec


# =========================
# Orchestration
# =========================

def plan_and_validate(brief: str, max_repairs: int = 1) -> Tuple[Any, Dict[str, Any]]:
    """
    Plan from a brief using the LLMs (Planner -> Coder refine).
    Validate into an OmegaSpec. If validation fails, apply auto-repair up to max_repairs times.

    Returns:
        (validated_spec_model, raw_spec_dict_from_planner)
    Raises:
        Exception on repeated validation failure.
    """
    # 1) Ask the PLANNER for a spec proposal (JSON only)
    try:
        raw = _llm_plan(brief)
    except Exception:
        # Structural fallback if planner fails entirely
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

    # 2) Optional: let the CODER refine the JSON (still no templates)
    try:
        refined = _llm_coder_refine(raw)
    except Exception:
        refined = raw  # if coder pass fails, continue with raw

    # 3) Validate or repair
    errors: list[str] = []
    attempt = 0
    current = refined
    while True:
        attempt += 1
        try:
            model = validate_spec(current)
            # Return the validated model and the *original planner* output for transparency
            return model, raw
        except Exception as e:
            errors.append(str(e))
            if attempt > max_repairs:
                raise ValueError(
                    "Failed to validate OmegaSpec after repairs:\n" + "\n".join(f"- {err}" for err in errors)
                )
            current = auto_repair_spec(current)
from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from pydantic import ValidationError

from backend.app.core.config import settings
from backend.app.integrations.openai.client import get_openai_client
from backend.app.models.spec import OmegaSpec, validate_spec


SYSTEM_PROMPT = """You are Omega Planner, an expert app planner.
Return ONLY strict JSON for an OmegaSpec object that will drive code generation.
Constraints:
- Prefer minimal, production-quality structure.
- Include navigation.home and items with sensible templates.
- Include 1–3 entities with fields.
- Include apis with mock_file when appropriate.
- Theme must contain colors and typography tokens (5–8 colors, 3–5 type tokens).
- acceptance should contain 3+ high-level cases, not code.

NO markdown. NO backticks. Return exactly one JSON object."""
# ^ no response_format — enforce with prompt


def _plan_input(product_brief: str) -> str:
    return f"""Product brief:
{product_brief}

Return a SINGLE JSON object with keys:
["name","description","theme","entities","apis","navigation","acceptance"].
"""


def _extract_json(text: str) -> Dict[str, Any]:
    """
    Try hard to extract a single JSON object from model output.
    1) direct json.loads
    2) first {...} block via regex (handles accidental prose)
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model output")

    # 1) straight parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2) scan for first top-level {...}
    # This is tolerant of stray text before/after.
    brace_iter = [m.start() for m in re.finditer(r"{", text)]
    for start in brace_iter:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        break
    raise ValueError("Could not extract JSON object from model output")


def _responses_text_output(resp) -> str:
    """
    Normalize text from Responses API across SDK versions.
    Prefer resp.output_text; otherwise rebuild from message content.
    """
    raw = getattr(resp, "output_text", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    # Fallback: walk output items
    parts = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if isinstance(t, str):
                    parts.append(t)
    return "".join(parts).strip()


def call_planner(product_brief: str) -> Tuple[Dict[str, Any], str]:
    """
    Call GPT-5 via Responses API and extract a JSON object.
    Returns (parsed_dict, raw_text).
    """
    client = get_openai_client()
    resp = client._client.responses.create(
        model=settings.omega_llm_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _plan_input(product_brief)},
        ],
        # no response_format here; keep compatible with wider SDK versions
    )

    raw_text = _responses_text_output(resp)
    if not raw_text:
        raise RuntimeError("Planner returned empty output")

    data = _extract_json(raw_text)
    return data, raw_text


def plan_and_validate(product_brief: str, max_repairs: int = 1) -> Tuple[OmegaSpec, Dict[str, Any]]:
    """
    Generate a spec and validate it. If validation fails, send a single repair
    request with the validation error to get corrected JSON.
    Returns (spec, raw_dict).
    """
    raw_dict, raw_text = call_planner(product_brief)

    try:
        spec = validate_spec(raw_dict)
        return spec, raw_dict
    except ValidationError as e:
        if max_repairs <= 0:
            raise

        client = get_openai_client()
        repair = client._client.responses.create(
            model=settings.omega_llm_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _plan_input(product_brief)},
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON failed validation.\n\n"
                        f"ValidationError:\n{e}\n\n"
                        "Return ONLY a corrected JSON object. No comments, no markdown."
                    ),
                },
            ],
            # still no response_format
        )
        repaired_text = _responses_text_output(repair)
        fixed = _extract_json(repaired_text)
        spec = validate_spec(fixed)
        return spec, fixed
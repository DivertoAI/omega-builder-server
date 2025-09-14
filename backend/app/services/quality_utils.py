# backend/app/services/quality_utils.py
from __future__ import annotations
from typing import Any, List

def normalize_issues(issues: Any, default_fail_msg: str = "quality check failed") -> List[str]:
    """
    Convert any gate output into a list[str] of human-readable issues.
    Accepted inputs:
      - list[str] : returned as-is
      - str       : wrapped in a single-element list
      - dict      : stringifies to a single-element list
      - bool      : True -> [], False -> [default_fail_msg]
      - None      : []
      - anything else: stringified to a single-element list
    """
    if issues is None:
        return []
    if isinstance(issues, list):
        return [str(x) for x in issues]
    if isinstance(issues, str):
        return [issues]
    if isinstance(issues, bool):
        return [] if issues else [default_fail_msg]
    if isinstance(issues, dict):
        return [str(issues)]
    # fallback
    return [str(issues)]
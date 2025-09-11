# backend/app/services/quality_service.py
from __future__ import annotations
from typing import Dict, List, Any

from backend.app.core.config import settings
from backend.app.services.quality_utils import normalize_issues
# from backend.app.services.gates import web_checks, compile_guard, mvvm_checks  # adjust imports

def run_quality_gates(manifest: Dict[str, Any]) -> List[str]:
    issues: List[str] = []

    if getattr(settings, "gate_enable_compile_guard", False):
        try:
            cg_issues = compile_guard.run(manifest)  # whatever your function is named
            issues += normalize_issues(cg_issues, "compile guard failed")
        except Exception as e:
            issues.append(f"compile guard error: {type(e).__name__}: {e}")

    if getattr(settings, "gate_enable_mvvm_checks", False):
        try:
            mvvm_issues = mvvm_checks.run(manifest)
            issues += normalize_issues(mvvm_issues, "mvvm checks failed")
        except Exception as e:
            issues.append(f"mvvm checks error: {type(e).__name__}: {e}")

    if getattr(settings, "gate_enable_web_checks", False):
        try:
            web_issues = web_checks.run(manifest)
            issues += normalize_issues(web_issues, "web checks failed")
        except Exception as e:
            issues.append(f"web checks error: {type(e).__name__}: {e}")

    return issues
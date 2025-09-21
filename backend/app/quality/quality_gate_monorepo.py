# backend/app/quality/quality_gate_monorepo.py
from __future__ import annotations
import os
from typing import Dict, List

REQUIRED = [
    ("apps/customer", ["pubspec.yaml", "lib/main.dart"]),
    ("design", ["omega_theme.dart", "fonts/"]),
    ("services/api", ["app/main.py", "app/api/routes_health.py"]),
]


def check(staging_root: str) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    for base, need in REQUIRED:
        base_path = os.path.join(staging_root, base)
        if not os.path.exists(base_path):
            errors.append(f"missing dir: {base}")
            continue
        for n in need:
            p = os.path.join(base_path, n)
            if not (os.path.exists(p) or any(x in n for x in ("/",))):
                errors.append(f"missing file: {base}/{n}")
    return {"errors": errors, "warnings": warnings}
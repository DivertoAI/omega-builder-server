# backend/app/services/blueprints/merge.py
from __future__ import annotations
from typing import Dict, Any

import json, os

PACK_DIR = os.path.join(os.path.dirname(__file__), "packs")

_DEFAULT = {
    "project": "omega_project",
    "apps": [
        {"name": "customer", "kind": "flutter_app", "path": "apps/customer"},
        {"name": "doctor_dashboard", "kind": "flutter_dashboard", "path": "dashboards/doctor"},
        {"name": "retailer_dashboard", "kind": "flutter_dashboard", "path": "dashboards/retailer"},
        {"name": "admin_dashboard", "kind": "flutter_dashboard", "path": "dashboards/admin"},
        {"name": "api", "kind": "fastapi_service", "path": "services/api"},
        {"name": "design", "kind": "design_system", "path": "design"},
        {"name": "infra", "kind": "infra", "path": "infra"},
    ],
    "design": {
        "fonts": ["Inter", "Merriweather"],
        "palette": {"primary": "#3B82F6", "secondary": "#64748B"},
        "radius": [6, 10, 14],
    },
}

_DEF_THEME = {"palette": {"primary": "#3B82F6"}, "typography": {}, "radius": [6, 10, 14]}


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_pack(name: str | None) -> Dict[str, Any]:
    if not name:
        return _DEFAULT
    path = os.path.join(PACK_DIR, f"{name}.json")
    if not os.path.exists(path):
        return _DEFAULT
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _deep_merge(_DEFAULT, data)


def apply_theme(plan: Dict[str, Any], theme: Dict[str, Any] | None) -> Dict[str, Any]:
    tokens = theme or _DEF_THEME
    plan = dict(plan)
    plan.setdefault("design", {})
    plan["design"] = _deep_merge(plan["design"], tokens)
    return plan
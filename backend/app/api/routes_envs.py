from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException, Path as PathParam

router = APIRouter(prefix="/api", tags=["environments"])

_OMEGA_DIR = Path("workspace/.omega")
_ENVS_FILE = _OMEGA_DIR / "environments.json"


def _ensure_store() -> None:
    _OMEGA_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    _ensure_store()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _env_ok(s: str) -> bool:
    if not isinstance(s, str) or not (1 <= len(s) <= 40):
        return False
    return all(ch.isalnum() or ch in "-_" for ch in s)


def _load_envs() -> Dict[str, Dict[str, Any]]:
    envs: Dict[str, Dict[str, Any]] = _load_json(_ENVS_FILE, default={})
    if "default" not in envs:
        envs["default"] = {"id": "default", "description": "", "enabled": True}
        _save_json(_ENVS_FILE, envs)
    return envs


def _save_envs(envs: Dict[str, Dict[str, Any]]) -> None:
    _save_json(_ENVS_FILE, envs)


@router.get("/environments")
async def list_environments() -> List[Dict[str, Any]]:
    envs = _load_envs()
    return [envs[k] for k in sorted(envs.keys())]


@router.put("/environments/{env_id}")
async def update_environment(
    env_id: str = PathParam(..., description="Environment id (letters/digits/_-)"),
    payload: Dict[str, Any] = Body(default={}),
) -> Dict[str, Any]:
    if not _env_ok(env_id):
        raise HTTPException(status_code=400, detail="invalid env_id")
    envs = _load_envs()
    env = envs.get(env_id) or {"id": env_id, "description": "", "enabled": True}

    if "description" in payload:
        desc = payload["description"]
        if desc is not None and not isinstance(desc, str):
            raise HTTPException(status_code=400, detail="description must be string or null")
        env["description"] = (desc or "")

    if "enabled" in payload:
        en = payload["enabled"]
        if not isinstance(en, bool):
            raise HTTPException(status_code=400, detail="enabled must be boolean")
        env["enabled"] = en

    envs[env_id] = env
    _save_envs(envs)
    return env
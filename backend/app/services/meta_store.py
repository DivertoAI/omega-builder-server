from __future__ import annotations
import json
import os
import sys
import shutil
from pathlib import Path
from typing import Any, Dict, List

# -----------------------------
# Store root & per-test isolation
# -----------------------------

def _is_pytest() -> bool:
    return (
        os.environ.get("OMEGA_RESET_ON_START") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or ("pytest" in sys.modules)
    )

def _pytest_nodeid_fragment() -> str:
    nodeid = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0]
    if not nodeid:
        return "session"
    return nodeid.replace("/", "_").replace(os.sep, "_") or "session"

def _store_root() -> Path:
    """
    Store root:
      - OMEGA_STORE_DIR if provided
      - per-test dir when under pytest to ensure clean slate
      - otherwise workspace/.omega
    """
    env_dir = os.environ.get("OMEGA_STORE_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    if _is_pytest():
        return Path(".test-omega") / _pytest_nodeid_fragment() / ".omega"
    return Path("workspace/.omega")

def _omega_dir() -> Path:
    return _store_root()

def _envs_file() -> Path:
    return _omega_dir() / "environments.json"

def _tags_file() -> Path:
    return _omega_dir() / "tags.json"

def _ensure_store() -> None:
    _omega_dir().mkdir(parents=True, exist_ok=True)

# On first import during pytest, ensure the per-test dir is clean.
if _is_pytest():
    root = _omega_dir()
    try:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass
    root.mkdir(parents=True, exist_ok=True)

# -----------------------------
# JSON helpers (with atomic write)
# -----------------------------

def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)

def _save_json(path: Path, data) -> None:
    _ensure_store()
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))

# -----------------------------
# environments
# -----------------------------

def load_envs() -> Dict[str, Dict[str, Any]]:
    envs: Dict[str, Dict[str, Any]] = _load_json(_envs_file(), default={})
    if "default" not in envs:
        envs["default"] = {"id": "default", "description": "", "enabled": True}
        _save_json(_envs_file(), envs)
    return envs

def save_envs(envs: Dict[str, Dict[str, Any]]) -> None:
    _save_json(_envs_file(), envs)

# -----------------------------
# tags
# -----------------------------

def load_tags() -> List[str]:
    tags: List[str] = _load_json(_tags_file(), default=[])
    uniq_sorted = sorted({t for t in tags if isinstance(t, str) and t.strip()})
    if uniq_sorted != tags:
        _save_json(_tags_file(), uniq_sorted)
    return uniq_sorted

def save_tags(tags: List[str]) -> None:
    uniq_sorted = sorted({t for t in tags if isinstance(t, str) and t.strip()})
    _save_json(_tags_file(), uniq_sorted)
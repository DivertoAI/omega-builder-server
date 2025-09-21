# backend/app/api/routes_stubs.py
from __future__ import annotations

import json
import os
import sys
import uuid
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Query, Response

router = APIRouter(prefix="/api", tags=["stubs"])

# -----------------------------------------------------------------------------
# Store location & per-test isolation (pytest) - integrates with stubs_store
# -----------------------------------------------------------------------------

# Tracks which omega dirs we've already wiped this test run (prevents repeat deletes)
_WIPED_OMEGA_DIRS: set[Path] = set()

def _is_pytest() -> bool:
    return (
        os.environ.get("OMEGA_RESET_ON_START") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or ("pytest" in sys.modules)
    )

def _pytest_nodeid_fragment() -> str:
    """
    Stable per-test fragment from PYTEST_CURRENT_TEST (nodeid is first token before a space).
    We sanitize path separators; keep other chars (like '::') which are valid on POSIX.
    """
    nodeid = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0]
    if not nodeid:
        return "session"
    frag = nodeid.replace("/", "_").replace(os.sep, "_")
    return frag or "session"

def _workspace_from_stubs_store() -> Optional[Path]:
    """
    Try to get a canonical workspace base path from services.stubs_store.
    """
    try:
        from backend.app.services.stubs_store import _workspace_from_env as _ws  # type: ignore
        return Path(_ws())
    except Exception:
        try:
            from backend.app.services.stubs_store import get_store  # type: ignore
            store = get_store()
            root = getattr(store, "root", None) or getattr(store, "path", None) or getattr(store, "workspace", None)
            if root:
                return Path(root)
        except Exception:
            pass
    return None

def _store_root() -> Path:
    """
    Store root:
      - OMEGA_STORE_DIR if set
      - else stubs_store workspace if available
      - if under pytest, use per-test isolated folder under .test-omega/<nodeid>/.omega
      - else default to workspace/.omega
    """
    env_dir = os.environ.get("OMEGA_STORE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    base = _workspace_from_stubs_store() or Path("workspace/.omega")

    if _is_pytest():
        frag = _pytest_nodeid_fragment()
        return Path(".test-omega") / frag / ".omega"

    return base

def _omega_dir() -> Path:
    return _store_root()

def _stubs_path() -> Path:
    return _omega_dir() / "stubs.json"

def _tags_path() -> Path:
    return _omega_dir() / "tags.json"

def _ensure_store() -> None:
    """
    Ensure the store dir exists. When running under pytest, wipe the *current test's*
    omega dir once on first touch to guarantee a clean slate per test function.
    """
    root = _omega_dir()
    # First-touch wipe for this test's dir
    if _is_pytest() and root not in _WIPED_OMEGA_DIRS:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        _WIPED_OMEGA_DIRS.add(root)
    root.mkdir(parents=True, exist_ok=True)

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

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# stubs.json shape: { "<id>": {id, name, path, env, enabled, tags: []} }

def _load_stubs() -> Dict[str, Dict[str, Any]]:
    _ensure_store()
    return _load_json(_stubs_path(), default={})

def _save_stubs(stubs: Dict[str, Dict[str, Any]]) -> None:
    _save_json(_stubs_path(), stubs)

def _load_tags() -> List[str]:
    _ensure_store()
    tags: List[str] = _load_json(_tags_path(), default=[])
    # de-dup and normalize
    uniq = sorted({t.strip() for t in tags if isinstance(t, str) and t.strip()})
    if uniq != tags:
        _save_json(_tags_path(), uniq)
    return uniq

def _ensure_tags_exist(new_tags: List[str]) -> None:
    if not new_tags:
        return
    existing = set(_load_tags())
    merged = sorted(existing | {t.strip() for t in new_tags if t and isinstance(t, str)})
    _save_json(_tags_path(), merged)

def _slug_ok(s: str) -> bool:
    # Accept letters, digits, '-', '_', '/' for paths; must start with '/'
    return isinstance(s, str) and 1 <= len(s) <= 200 and s.startswith("/") and "\n" not in s and "\r" not in s

def _env_ok(s: str) -> bool:
    # light env id validation: [A-Za-z0-9_-]{1,40}
    if not isinstance(s, str) or not (1 <= len(s) <= 40):
        return False
    for ch in s:
        if not (ch.isalnum() or ch in "-_"):
            return False
    return True

def _validate_tags(tags: Optional[List[str]]) -> List[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags must be an array of strings")
    if len(tags) > 64:
        raise HTTPException(status_code=400, detail="too many tags (max 64)")
    out: List[str] = []
    for t in tags:
        if not isinstance(t, str):
            raise HTTPException(status_code=400, detail="tags must be strings")
        tt = t.strip()
        if tt:
            out.append(tt)
    # de-dup preserve order
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            uniq.append(t)
            seen.add(t)
    return uniq

def _check_unique_path(stubs: Dict[str, Dict[str, Any]], env: str, path: str, exclude_id: Optional[str] = None) -> None:
    for sid, s in stubs.items():
        if exclude_id and sid == exclude_id:
            continue
        if s.get("env") == env and s.get("path") == path:
            raise HTTPException(status_code=409, detail=f"Path '{path}' already exists in env '{env}'")

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@router.get("/stubs")
async def list_stubs(
    q: Optional[str] = Query(default=None, description="Free text in name or path"),
    env: Optional[str] = Query(default=None, description="Environment filter"),
    tag: Optional[str] = Query(default=None, description="Require this tag"),
    enabled: Optional[bool] = Query(default=None, description="Filter by enabled flag"),
    sort: Optional[str] = Query(default=None, description="Sort by 'env','path','name' (comma-separated, prefix '-' for desc)"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> List[Dict[str, Any]]:
    stubs = _load_stubs()
    items = list(stubs.values())

    if q:
        ql = q.lower()
        items = [s for s in items if ql in (s.get("name", "") or "").lower() or ql in (s.get("path", "") or "").lower()]
    if env:
        items = [s for s in items if s.get("env") == env]
    if tag:
        items = [s for s in items if tag in (s.get("tags") or [])]
    if enabled is not None:
        items = [s for s in items if bool(s.get("enabled")) == bool(enabled)]

    if sort:
        for token in reversed([t.strip() for t in sort.split(",") if t.strip()]):
            desc = token.startswith("-")
            key = token[1:] if desc else token
            if key not in {"env", "path", "name"}:
                continue
            items.sort(key=lambda s: s.get(key, ""), reverse=desc)
    else:
        items.sort(key=lambda s: (s.get("env", ""), s.get("path", "")))

    end = offset + limit
    return items[offset:end]

@router.post("/stubs", status_code=201)
async def create_stub(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Create a stub with unique (env, path).
    Body: { "name": str, "path": "/hello", "env": "default", "enabled": true?, "tags": [str]? }
    """
    name = payload.get("name")
    path = payload.get("path")
    env = payload.get("env") or "default"
    enabled = bool(payload.get("enabled", True))
    tags = _validate_tags(payload.get("tags"))

    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not _env_ok(env):
        raise HTTPException(status_code=400, detail="invalid env")
    if not _slug_ok(path):
        raise HTTPException(status_code=400, detail="invalid path (must start with '/')")

    stubs = _load_stubs()
    _check_unique_path(stubs, env, path)

    sid = str(uuid.uuid4())
    stub = {
        "id": sid,
        "name": name.strip(),
        "path": path,
        "env": env,
        "enabled": enabled,
        "tags": tags,
    }
    stubs[sid] = stub
    _save_stubs(stubs)
    _ensure_tags_exist(tags)
    return stub

# -----------------------------------------------------------------------------
# Import / Export
# -----------------------------------------------------------------------------

@router.get("/stubs/export")
async def export_stubs() -> Dict[str, Any]:
    stubs = _load_stubs()
    items = [stubs[k] for k in sorted(stubs.keys())]
    return {"stubs": items, "count": len(items)}

@router.post("/stubs/import")
async def import_stubs(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    incoming = payload.get("stubs")
    mode = (payload.get("mode") or "merge").lower()
    if not isinstance(incoming, list):
        raise HTTPException(status_code=400, detail="'stubs' must be a list")
    if mode not in {"merge", "replace"}:
        raise HTTPException(status_code=400, detail="mode must be 'merge' or 'replace'")

    stubs = {} if mode == "replace" else _load_stubs()

    imported = 0
    skipped_conflicts = 0
    added_tags: List[str] = []
    seen_pairs = {(s["env"], s["path"]) for s in stubs.values() if "env" in s and "path" in s}

    for raw in incoming:
        if not isinstance(raw, dict):
            skipped_conflicts += 1
            continue

        name = raw.get("name")
        path = raw.get("path")
        env = raw.get("env") or "default"
        enabled = bool(raw.get("enabled", True))
        tags = _validate_tags(raw.get("tags"))

        if not isinstance(name, str) or not name.strip():
            skipped_conflicts += 1
            continue
        if not _env_ok(env) or not _slug_ok(path):
            skipped_conflicts += 1
            continue

        if (env, path) in seen_pairs:
            skipped_conflicts += 1
            continue

        sid = str(uuid.uuid4())
        stub = {
            "id": sid,
            "name": name.strip(),
            "path": path,
            "env": env,
            "enabled": enabled,
            "tags": tags,
        }
        stubs[sid] = stub
        seen_pairs.add((env, path))
        added_tags.extend(tags)
        imported += 1

    _save_stubs(stubs)
    _ensure_tags_exist(added_tags)

    return {
        "status": "ok",
        "mode": mode,
        "imported": imported,
        "skipped_conflicts": skipped_conflicts,
        "total": len(stubs),
    }

# -----------------------------------------------------------------------------
# Dynamic item routes
# -----------------------------------------------------------------------------

@router.get("/stubs/{stub_id}")
async def get_stub(stub_id: str = PathParam(...)) -> Dict[str, Any]:
    stubs = _load_stubs()
    stub = stubs.get(stub_id)
    if not stub:
        raise HTTPException(status_code=404, detail="Stub not found")
    return stub

@router.put("/stubs/{stub_id}")
async def update_stub(
    stub_id: str = PathParam(...),
    payload: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    stubs = _load_stubs()
    stub = stubs.get(stub_id)
    if not stub:
        raise HTTPException(status_code=404, detail="Stub not found")

    new_env = stub["env"]
    new_path = stub["path"]
    changed = False

    if "name" in payload:
        nm = payload["name"]
        if not isinstance(nm, str) or not nm.strip():
            raise HTTPException(status_code=400, detail="name must be a non-empty string")
        stub["name"] = nm.strip()
        changed = True

    if "env" in payload:
        ev = payload["env"]
        if not _env_ok(ev):
            raise HTTPException(status_code=400, detail="invalid env")
        new_env = ev

    if "path" in payload:
        pth = payload["path"]
        if not _slug_ok(pth):
            raise HTTPException(status_code=400, detail="invalid path (must start with '/')")
        new_path = pth

    if new_env != stub["env"] or new_path != stub["path"]:
        _check_unique_path(stubs, new_env, new_path, exclude_id=stub_id)
        stub["env"] = new_env
        stub["path"] = new_path
        changed = True

    if "enabled" in payload:
        en = payload["enabled"]
        if not isinstance(en, bool):
            raise HTTPException(status_code=400, detail="enabled must be boolean")
        stub["enabled"] = en
        changed = True

    if "tags" in payload:
        tags = _validate_tags(payload["tags"])
        stub["tags"] = tags
        _ensure_tags_exist(tags)
        changed = True

    if changed:
        stubs[stub_id] = stub
        _save_stubs(stubs)
    return stub

@router.delete("/stubs/{stub_id}", status_code=204)
async def delete_stub(stub_id: str = PathParam(...)) -> Response:
    stubs = _load_stubs()
    if stub_id not in stubs:
        raise HTTPException(status_code=404, detail="Stub not found")
    stubs.pop(stub_id, None)
    _save_stubs(stubs)
    return Response(status_code=204)

@router.post("/stubs/{stub_id}/tags")
async def add_tags_to_stub(stub_id: str = PathParam(...), payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    tags = _validate_tags(payload.get("tags"))
    stubs = _load_stubs()
    stub = stubs.get(stub_id)
    if not stub:
        raise HTTPException(status_code=404, detail="Stub not found")
    current = _validate_tags(stub.get("tags", []))
    merged = []
    seen = set()
    for t in current + tags:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    stub["tags"] = merged
    stubs[stub_id] = stub
    _save_stubs(stubs)
    _ensure_tags_exist(merged)
    return {"updated": True, "id": stub_id, "tags": merged}

@router.delete("/stubs/{stub_id}/tags/{tag}", status_code=200)
async def remove_tag_from_stub(stub_id: str = PathParam(...), tag: str = PathParam(...)) -> Dict[str, Any]:
    tag = (tag or "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="invalid tag")
    stubs = _load_stubs()
    stub = stubs.get(stub_id)
    if not stub:
        raise HTTPException(status_code=404, detail="Stub not found")
    cur = [t for t in _validate_tags(stub.get("tags", [])) if t != tag]
    stub["tags"] = cur
    stubs[stub_id] = stub
    _save_stubs(stubs)
    return {"updated": True, "id": stub_id, "tags": cur}
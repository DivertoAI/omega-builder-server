# backend/app/services/meta_store.py
from __future__ import annotations

import json
import os
import sys
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

# =========================================================
# Store root & per-test isolation
# =========================================================

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

# =========================================================
# JSON helpers (with atomic write)
# =========================================================

def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)

def _save_json(path: Path, data) -> None:
    _ensure_store()
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))

# =========================================================
# environments
# =========================================================

def load_envs() -> Dict[str, Dict[str, Any]]:
    envs: Dict[str, Dict[str, Any]] = _load_json(_envs_file(), default={})
    if "default" not in envs:
        envs["default"] = {"id": "default", "description": "", "enabled": True}
        _save_json(_envs_file(), envs)
    return envs

def save_envs(envs: Dict[str, Dict[str, Any]]) -> None:
    _save_json(_envs_file(), envs)

# =========================================================
# tags
# =========================================================

def load_tags() -> List[str]:
    tags: List[str] = _load_json(_tags_file(), default=[])
    uniq_sorted = sorted({t for t in tags if isinstance(t, str) and t.strip()})
    if uniq_sorted != tags:
        _save_json(_tags_file(), uniq_sorted)
    return uniq_sorted

def save_tags(tags: List[str]) -> None:
    uniq_sorted = sorted({t for t in tags if isinstance(t, str) and t.strip()})
    _save_json(_tags_file(), uniq_sorted)

# =========================================================
# Workspace snapshot / diff for "last run" summaries
# =========================================================

# Workspace root to scan (defaults to "workspace")
_WORKSPACE_DIR = Path(os.getenv("OMEGA_WORKSPACE_DIR", "workspace")).expanduser().resolve()

# Where we persist the last snapshot (inside the omega/store dir)
_SNAPSHOT_FILE = _omega_dir() / "snapshot.json"

@dataclass
class _FileStat:
    size: int
    mtime: float

_Snapshot = Dict[str, _FileStat]  # rel path (posix) -> _FileStat

def _should_skip(path: Path) -> bool:
    """
    Skip noise: the .omega store itself, AppleDouble, dotfiles at root we don't care about.
    """
    # Skip omega state dir (where snapshot/last_run live)
    try:
        if _omega_dir().exists() and path.is_relative_to(_omega_dir()):
            return True
    except Exception:
        # .is_relative_to not available pre-3.9, or other edge; fall back below
        try:
            _ = path.resolve().as_posix().startswith(_omega_dir().resolve().as_posix() + "/")
            if _:
                return True
        except Exception:
            pass

    name = path.name
    if name.startswith("._"):      # AppleDouble
        return True
    return False

def _scan_workspace(root: Path) -> _Snapshot:
    snap: _Snapshot = {}
    if not root.exists():
        return snap

    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if _should_skip(p):
            continue
        try:
            rel = p.relative_to(root).as_posix()
            st = p.stat()
            snap[rel] = _FileStat(size=int(st.st_size), mtime=float(st.st_mtime))
        except Exception:
            # Ignore files we can’t stat
            pass
    return snap

def _load_snapshot(path: Path) -> _Snapshot:
    raw = _load_json(path, default={})
    out: _Snapshot = {}
    if not isinstance(raw, dict):
        return out
    for rel, obj in raw.items():
        try:
            size = int(obj.get("size", 0))
            mtime = float(obj.get("mtime", 0.0))
            out[rel] = _FileStat(size=size, mtime=mtime)
        except Exception:
            continue
    return out

def _save_snapshot(path: Path, snap: _Snapshot) -> None:
    serial = {rel: asdict(fs) for rel, fs in snap.items()}
    _save_json(path, serial)

def _diff(prev: _Snapshot, curr: _Snapshot) -> Tuple[List[str], List[str], List[str]]:
    prev_keys = set(prev.keys())
    curr_keys = set(curr.keys())
    added = sorted(curr_keys - prev_keys)
    deleted = sorted(prev_keys - curr_keys)
    modified: List[str] = []
    for rel in (prev_keys & curr_keys):
        a, b = prev[rel], curr[rel]
        if a.size != b.size or abs(a.mtime - b.mtime) > 1e-6:
            modified.append(rel)
    modified.sort()
    return added, modified, deleted

def _fmt_bytes(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.0f} PB"

def _preview_block(title: str, items: List[str], limit: int = 40) -> str:
    if not items:
        return f"{title}: (none)"
    head = items[:limit]
    more = len(items) - len(head)
    lines = "\n".join(f"* {it}" for it in head)
    if more > 0:
        lines += f"\n… (+{more} more)"
    return f"{title} ({len(items)}):\n{lines}"

def compute_workspace_diff_summary() -> Dict[str, str]:
    """
    Scan the current workspace, diff it against the previous snapshot,
    save the new snapshot, and return a human-friendly summary + preview.

    Returns:
      {
        "summary": "DONE: Added A, Modified M, Deleted D files; workspace now has N files (X MB), Δ≈ +... / -...",
        "preview": "# Snapshot summary\n- Files counted: ...\n- Total bytes (approx): ...\n\nAdded ...\nModified ...\nDeleted ..."
      }
    """
    prev = _load_snapshot(_SNAPSHOT_FILE)
    curr = _scan_workspace(_WORKSPACE_DIR)

    added, modified, deleted = _diff(prev, curr)

    total_files = len(curr)
    total_bytes = sum(fs.size for fs in curr.values())

    def _size_of(paths: List[str], snap: _Snapshot) -> int:
        return sum(snap[p].size for p in paths if p in snap)

    bytes_added = _size_of(added, curr)
    bytes_deleted = _size_of(deleted, prev)

    summary = (
        f"DONE: Added {len(added)}, Modified {len(modified)}, Deleted {len(deleted)} files; "
        f"workspace now has {total_files} files ({_fmt_bytes(total_bytes)}), "
        f"Δ≈ +{_fmt_bytes(bytes_added)} / -{_fmt_bytes(bytes_deleted)}"
    )

    blocks = [
        "# Snapshot summary",
        f"- Files counted: {total_files}",
        f"- Total bytes (approx): {total_bytes}",
        "",
        _preview_block("Added", added),
        "",
        _preview_block("Modified", modified),
        "",
        _preview_block("Deleted", deleted),
    ]
    preview = "\n".join(blocks)

    # Persist the new snapshot for the next run
    _save_snapshot(_SNAPSHOT_FILE, curr)

    return {"summary": summary, "preview": preview}
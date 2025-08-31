from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, Body, HTTPException, Path as PathParam

router = APIRouter(prefix="/api", tags=["environments", "tags"])

# Reuse same .omega store convention as envs
_OMEGA_DIR = Path("workspace/.omega")
_TAGS_FILE = _OMEGA_DIR / "tags.json"

def _ensure_store() -> None:
    _OMEGA_DIR.mkdir(parents=True, exist_ok=True)

def _load_tags() -> List[str]:
    try:
        if not _TAGS_FILE.exists():
            return []
        data = json.loads(_TAGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        # unique + sorted for stable API output
        uniq = sorted({t for t in data if isinstance(t, str) and t.strip()})
        if uniq != data:
            _ensure_store()
            _TAGS_FILE.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
        return uniq
    except Exception:
        return []

def _save_tags(tags: List[str]) -> None:
    _ensure_store()
    uniq = sorted({t for t in tags if isinstance(t, str) and t.strip()})
    _TAGS_FILE.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")

@router.get("/tags")
async def list_tags() -> List[str]:
    """List known tags."""
    return _load_tags()

@router.post("/tags")
async def add_tag(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Add a tag. Accepts either {"name":"..."} (tests) or {"tag":"..."} (smoke).
    Returns 200, with created flag.
    """
    tag = (payload.get("name") or payload.get("tag") or "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="name/tag must be a non-empty string")
    if len(tag) > 64:
        raise HTTPException(status_code=400, detail="tag too long")

    tags = _load_tags()
    if tag in tags:
        return {"created": False, "tag": tag}  # idempotent
    tags.append(tag)
    _save_tags(tags)
    return {"created": True, "tag": tag}

@router.delete("/tags/{tag}")
async def delete_tag(tag: str = PathParam(...)) -> Dict[str, Any]:
    """Remove a tag if present."""
    if not isinstance(tag, str) or not tag.strip():
        raise HTTPException(status_code=400, detail="invalid tag")
    tag = tag.strip()
    tags = _load_tags()
    if tag not in tags:
        return {"deleted": False, "tag": tag}
    tags = [t for t in tags if t != tag]
    _save_tags(tags)
    return {"deleted": True, "tag": tag}
from __future__ import annotations

import glob as _glob
import json
import os
import shutil
import stat
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Repo root resolution (same heuristic you used: go up 3 levels)
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]

# Protected names (we never delete these; we also keep all ops inside ROOT)
_FORBIDDEN = {".git", ".venv", "__pycache__", ".tox", ".mypy_cache"}


# --------------------------------------------------------------------------
# Safety helpers
# --------------------------------------------------------------------------
def _safe_path(rel_or_abs: str) -> Path:
    """
    Resolve a path safely inside the repository root.
    Absolutes are allowed only if they are inside ROOT.
    """
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    else:
        p = p.resolve()

    try:
        p.relative_to(ROOT)
    except Exception:
        raise ValueError(f"path escapes repo root: {p}")

    return p


def _ensure_parents(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _is_forbidden(p: Path) -> bool:
    return any(seg in _FORBIDDEN for seg in p.parts)


# --------------------------------------------------------------------------
# Tool Schemas (Pydantic)
# --------------------------------------------------------------------------
class FSReadArgs(BaseModel):
    path: str = Field(description="File path, relative to repo root")
    max_bytes: int = Field(default=200_000, ge=1, description="Max bytes to read (prevents huge loads)")


class FSWriteArgs(BaseModel):
    path: str = Field(description="File path, relative to repo root")
    content: str = Field(description="Full new file content (UTF-8 text)")
    mode: str = Field(default="overwrite", description="overwrite | append")


class FSMkdirArgs(BaseModel):
    path: str = Field(description="Directory path, relative to repo root")
    exist_ok: bool = Field(default=True)


class FSDeleteArgs(BaseModel):
    path: str = Field(description="File or directory path, relative to repo root")
    recursive: bool = Field(default=False, description="If true and path is a directory, remove recursively")


class FSGlobArgs(BaseModel):
    pattern: str = Field(description="Glob pattern from repo root (e.g. 'backend/**/*.py')")


class FSMapArgs(BaseModel):
    root: str = Field(default=".", description="Directory to scan (relative to repo root)")
    max_depth: int = Field(default=4, ge=0, le=10)
    include_content: bool = Field(default=False, description="Include file content when size <= 64KB")


class FSDiffArgs(BaseModel):
    path: str = Field(description="File to diff against")
    new_content: str = Field(description="Proposed new content (full text)")


class FSPatchArgs(BaseModel):
    path: str = Field(description="File to patch")
    patch: str = Field(description="Either full new content (strategy=replace) or a unified diff")
    strategy: str = Field(default="replace", description="replace | unified")


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def fs_map(args: FSMapArgs) -> Dict[str, Any]:
    base = _safe_path(args.root)
    try:
        rel_root = str(base.relative_to(ROOT))
    except Exception:
        rel_root = "."

    entries: List[Dict[str, Any]] = []

    def depth(p: Path) -> int:
        try:
            rel = p.relative_to(base)
        except Exception:
            return 0
        return len(rel.parts)

    if not base.exists():
        return {"ok": False, "error": f"Directory not found: {args.root}"}

    for p in base.rglob("*"):
        if depth(p) > args.max_depth:
            continue
        if _is_forbidden(p):
            continue
        try:
            rel = str(p.relative_to(ROOT))
        except Exception:
            continue

        item: Dict[str, Any] = {
            "path": rel,
            "type": "dir" if p.is_dir() else "file",
        }
        if p.is_file():
            try:
                item["size"] = p.stat().st_size
            except Exception:
                item["size"] = None
            if args.include_content and (item["size"] or 0) <= 64_000:
                try:
                    item["content"] = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    item["content"] = None
        entries.append(item)

    return {"ok": True, "root": rel_root, "entries": entries}


def fs_glob(args: FSGlobArgs) -> Dict[str, Any]:
    pat = str(ROOT / args.pattern)
    matches = [
        str(Path(m).resolve().relative_to(ROOT))
        for m in _glob.glob(pat, recursive=True)
    ]
    return {"ok": True, "matches": matches[:1000]}


def fs_read(args: FSReadArgs) -> Dict[str, Any]:
    p = _safe_path(args.path)
    if not p.exists():
        return {"ok": False, "error": f"Path not found: {args.path}"}
    if p.is_dir():
        return {"ok": False, "error": f"Requested path is a directory: {args.path}"}
    try:
        data = p.read_bytes()
        truncated = len(data) > args.max_bytes
        if truncated:
            data = data[: args.max_bytes]
        try:
            text = data.decode("utf-8")
        except Exception:
            text = data.decode("utf-8", errors="ignore")
        return {
            "ok": True,
            "path": str(p.relative_to(ROOT)),
            "size": p.stat().st_size,
            "truncated": truncated,
            "content": text,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_write(args: FSWriteArgs) -> Dict[str, Any]:
    p = _safe_path(args.path)
    try:
        _ensure_parents(p)
        if args.mode not in {"overwrite", "append"}:
            args.mode = "overwrite"

        if args.mode == "append" and p.exists():
            prev = p.read_text(encoding="utf-8", errors="ignore")
            new_content = prev + args.content
        else:
            new_content = args.content

        p.write_text(new_content, encoding="utf-8")
        return {"ok": True, "path": str(p.relative_to(ROOT)), "written_bytes": len(new_content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_mkdir(args: FSMkdirArgs) -> Dict[str, Any]:
    p = _safe_path(args.path)
    try:
        p.mkdir(parents=True, exist_ok=args.exist_ok)
        return {"ok": True, "path": str(p.relative_to(ROOT))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_delete(args: FSDeleteArgs) -> Dict[str, Any]:
    p = _safe_path(args.path)
    rel = str(p.relative_to(ROOT))
    if _is_forbidden(p):
        return {"ok": False, "error": f"Refusing to delete protected path: {rel}"}
    try:
        if not p.exists():
            return {"ok": False, "error": "Path not found"}
        if p.is_dir():
            if args.recursive:
                # Make writable, then remove
                for sub in p.rglob("*"):
                    try:
                        os.chmod(sub, stat.S_IWRITE | stat.S_IREAD)
                    except Exception:
                        pass
                shutil.rmtree(p)
            else:
                p.rmdir()
            return {"ok": True, "path": rel, "deleted": True}
        # file
        p.unlink()
        return {"ok": True, "path": rel, "deleted": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_diff(args: FSDiffArgs) -> Dict[str, Any]:
    p = _safe_path(args.path)
    old = ""
    if p.exists() and p.is_file():
        try:
            old = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            old = ""
    diff = "\n".join(
        unified_diff(
            old.splitlines(),
            args.new_content.splitlines(),
            fromfile=str(p),
            tofile=str(p),
            lineterm="",
        )
    )
    return {"ok": True, "path": str(p.relative_to(ROOT)), "diff": diff}


def fs_patch(args: FSPatchArgs) -> Dict[str, Any]:
    """
    Apply a patch to a file.
    - strategy="replace": treat `patch` as the entire new file body (recommended).
    - strategy="unified": *best effort*; if parsing isn't confident, we fall back to replace.
    """
    p = _safe_path(args.path)
    try:
        _ensure_parents(p)

        if args.strategy != "unified":
            p.write_text(args.patch, encoding="utf-8")
            return {"ok": True, "path": str(p.relative_to(ROOT)), "applied": "replace"}

        # Minimal unified support
        lines = args.patch.splitlines()
        plus: List[str] = []
        in_hunk = False
        for ln in lines:
            if ln.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk:
                continue
            if ln.startswith("+"):
                plus.append(ln[1:])
            elif ln.startswith(" ") or ln.startswith("-"):
                # Can't guarantee correctness without a full patcher; bail to replace.
                return _patch_fallback_replace(p, args.patch)

        if plus:
            content = "\n".join(plus) + ("\n" if args.patch.endswith("\n") else "")
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(p.relative_to(ROOT)), "applied": "unified_plus_only"}

        return _patch_fallback_replace(p, args.patch)

    except Exception as e:
        return {"ok": False, "error": str(e)}


def _patch_fallback_replace(p: Path, patched: str) -> Dict[str, Any]:
    p.write_text(patched, encoding="utf-8")
    return {"ok": True, "path": str(p.relative_to(ROOT)), "applied": "fallback_replace"}


# --------------------------------------------------------------------------
# OpenAI tool exposure
# --------------------------------------------------------------------------
@dataclass
class _Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Any


_TOOLS: List[_Tool] = [
    _Tool(
        name="fs_map",
        description="List files/folders under a path (relative to repo root). Use to explore before editing.",
        parameters=FSMapArgs.model_json_schema(),
        fn=lambda **kw: fs_map(FSMapArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_glob",
        description="Glob files from repo root (supports **). Returns relative paths.",
        parameters=FSGlobArgs.model_json_schema(),
        fn=lambda **kw: fs_glob(FSGlobArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_read",
        description="Read a UTF-8 text file (size-guarded).",
        parameters=FSReadArgs.model_json_schema(),
        fn=lambda **kw: fs_read(FSReadArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_write",
        description="Write/overwrite or append text to a file. Creates parent dirs.",
        parameters=FSWriteArgs.model_json_schema(),
        fn=lambda **kw: fs_write(FSWriteArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_mkdir",
        description="Create a directory (parents ok).",
        parameters=FSMkdirArgs.model_json_schema(),
        fn=lambda **kw: fs_mkdir(FSMkdirArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_delete",
        description="Delete a file or directory. Set recursive=true for recursive removal (protected paths refused).",
        parameters=FSDeleteArgs.model_json_schema(),
        fn=lambda **kw: fs_delete(FSDeleteArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_diff",
        description="Unified diff between current file content and proposed new content.",
        parameters=FSDiffArgs.model_json_schema(),
        fn=lambda **kw: fs_diff(FSDiffArgs.model_validate(kw)),
    ),
    _Tool(
        name="fs_patch",
        description="Apply a patch. Prefer strategy='replace' where `patch` is the full new content.",
        parameters=FSPatchArgs.model_json_schema(),
        fn=lambda **kw: fs_patch(FSPatchArgs.model_validate(kw)),
    ),
]


def openai_tool_specs() -> List[dict]:
    """
    Return tool definitions for the OpenAI Responses API using the **flat schema**
    expected by your installed SDK: {"type":"function","name":...,"description":...,"parameters":...}
    """
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in _TOOLS
    ]


def dispatch_tool_call(name: str, arguments: dict) -> Dict[str, Any]:
    """
    Executes a tool call by name with validated arguments.
    """
    tool = next((t for t in _TOOLS if t.name == name), None)
    if not tool:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return tool.fn(**(arguments or {}))
    except Exception as e:
        return {"ok": False, "error": str(e)}
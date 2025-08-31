from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------------------
# Local filesystem helpers (self-contained; no external deps)
# --------------------------------------------------------------------------------------

REPO_ROOT = Path(".").resolve()

# Ignore lists to keep scans light and noise-free
_IGNORE_NAMES = {"__pycache__", ".git", ".venv", "node_modules"}
_IGNORE_PREFIXES = (".",)  # hidden dotfiles/dirs


def _safe_rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT))
    except Exception:
        return str(p)


def _is_text(data: bytes) -> bool:
    # Heuristic "looks like text"
    if b"\x00" in data[:1024]:
        return False
    try:
        data.decode("utf-8")
        return True
    except Exception:
        return False


def _read_file(path: Path, max_bytes: int = 200_000) -> Tuple[bool, Optional[str], bool, int]:
    if not path.exists() or not path.is_file():
        return False, None, False, 0
    data = path.read_bytes()
    size = len(data)
    truncated = False
    if size > max_bytes:
        data = data[:max_bytes]
        truncated = True
    if not _is_text(data):
        # Return marker for binary files
        return True, f"<<binary:{size}bytes>>", truncated, size
    return True, data.decode("utf-8", errors="replace"), truncated, size


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _list_tree(root: Path, max_depth: int = 4) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    def walk(base: Path, depth: int):
        if depth > max_depth:
            return
        for child in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            name = child.name
            # Skip heavy/noisy and hidden
            if name in _IGNORE_NAMES or name.startswith(_IGNORE_PREFIXES):
                continue
            rel = _safe_rel(child)
            if child.is_dir():
                entries.append({"path": rel, "type": "dir"})
                walk(child, depth + 1)
            else:
                try:
                    size = child.stat().st_size
                except Exception:
                    size = None
                entries.append({"path": rel, "type": "file", "size": size})

    walk(root, 0)
    return entries


# --------------------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------------------

def fs_map(root: str = ".", max_depth: int = 4, include_content: bool = False) -> Dict[str, Any]:
    """
    List files/folders under a path (relative to repo root). Optionally include
    content for small text files (<= 64KB). Skips noisy dirs and hidden dotfiles.
    """
    try:
        base = (REPO_ROOT / root).resolve()
        if not base.exists():
            return {"ok": True, "root": _safe_rel(base), "entries": []}

        out = _list_tree(base, max_depth=max_depth)

        if include_content:
            MAX_INLINE = 64 * 1024
            for e in out:
                if e.get("type") == "file" and isinstance(e.get("size"), int) and e["size"] <= MAX_INLINE:
                    ok, content, truncated, _size = _read_file(REPO_ROOT / e["path"], max_bytes=MAX_INLINE)
                    if ok and content is not None and not truncated and not content.startswith("<<binary"):
                        e["content"] = content
        return {"ok": True, "root": _safe_rel(base), "entries": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_glob(pattern: str, max_matches: int = 2000) -> Dict[str, Any]:
    """
    Glob for paths (relative to repo root). Returns a list of matches as strings.
    """
    try:
        matches: List[str] = []
        for p in REPO_ROOT.glob(pattern):
            matches.append(_safe_rel(p))
            if len(matches) >= max_matches:
                break
        return {"ok": True, "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_read(path: str, max_bytes: int = 200_000) -> Dict[str, Any]:
    """
    Read up to max_bytes from a file (UTF-8). Binary files are marked and not decoded.
    """
    try:
        fp = (REPO_ROOT / path).resolve()
        ok, content, truncated, size = _read_file(fp, max_bytes=max_bytes)
        if not ok:
            return {"ok": False, "error": "File not found", "path": _safe_rel(fp)}
        return {
            "ok": True,
            "path": _safe_rel(fp),
            "size": size,
            "truncated": truncated,
            "content": content,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def fs_write(path: str, content: str, mode: str = "w") -> Dict[str, Any]:
    """
    Write text content to a file. Creates parent dirs as needed.
    mode: "w" (overwrite) or "a" (append).
    """
    try:
        if mode not in {"w", "a"}:
            return {"ok": False, "error": "Invalid mode (use 'w' or 'a')"}
        fp = (REPO_ROOT / path).resolve()
        _ensure_parent(fp)
        with fp.open(mode, encoding="utf-8", newline="") as f:
            f.write(content)
        return {"ok": True, "path": _safe_rel(fp), "bytes": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def fs_mkdir(path: str, exist_ok: bool = True, parents: bool = True) -> Dict[str, Any]:
    """
    Create a directory (and parents).
    """
    try:
        fp = (REPO_ROOT / path).resolve()
        fp.mkdir(parents=parents, exist_ok=exist_ok)
        return {"ok": True, "path": _safe_rel(fp)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def fs_delete(path: str, recursive: bool = True) -> Dict[str, Any]:
    """
    Delete a file or directory tree.
    """
    try:
        fp = (REPO_ROOT / path).resolve()
        if fp.is_dir():
            if recursive:
                shutil.rmtree(fp)
            else:
                fp.rmdir()
            return {"ok": True, "path": _safe_rel(fp), "type": "dir"}
        elif fp.is_file():
            fp.unlink()
            return {"ok": True, "path": _safe_rel(fp), "type": "file"}
        else:
            return {"ok": True, "path": _safe_rel(fp), "type": "missing"}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def fs_diff(paths: Optional[List[str]] = None, unified: bool = True, max_bytes: int = 120_000) -> Dict[str, Any]:
    """
    Generate a quick, human-readable snapshot summary for given paths.
    - Defaults to scoped roots ["backend", "workspace"] if paths is omitted.
    - Skips heavy/noisy dirs and hidden dotfiles/dirs.
    - Skips previews for binaries and anything > 48KB.
    Not a VCS diff, but enough for post-edit visibility and for the agent's diff preview.

    Returns keys 'unified', 'diff', and 'text' (same content) so downstream code
    can find a diff-like string reliably.
    """
    try:
        base = REPO_ROOT
        targets: List[Path] = []

        # Default to scoped paths (avoid whole-repo scans)
        scan_paths = paths or ["backend", "workspace"]

        for p in scan_paths:
            tp = (base / p).resolve()
            if not tp.exists():
                continue
            if tp.is_dir():
                for sub in tp.rglob("*"):
                    name = sub.name
                    if name in _IGNORE_NAMES or name.startswith(_IGNORE_PREFIXES):
                        continue
                    if sub.is_file():
                        targets.append(sub)
            else:
                targets.append(tp)

        items: List[Dict[str, Any]] = []
        total = 0
        for fp in sorted(set(targets)):
            try:
                size = fp.stat().st_size
                total += size
                entry = {"path": _safe_rel(fp), "size": size}
                # Small preview inline (skip binaries and >48KB)
                if size <= 48_000 and fp.is_file():
                    ok, content, truncated, _ = _read_file(fp, max_bytes=48_000)
                    if ok and not truncated and content is not None and not content.startswith("<<binary"):
                        entry["preview"] = content
                items.append(entry)
            except Exception:
                # skip unreadable files
                continue

        # Provide a unified "directory listing" style summary (not a VCS diff)
        lines = [
            "# Snapshot summary",
            f"- Files counted: {len(items)}",
            f"- Total bytes (approx): {total}",
            "",
        ]
        for it in items[:200]:  # cap for size
            lines.append(f"* {it['path']} ({it.get('size', '?')} bytes)")
        summary = "\n".join(lines)

        out = {
            "ok": True,
            "count": len(items),
            "total_bytes": total,
            "files": items[:500],
            "summary": summary,
        }
        if unified:
            out["unified"] = summary
        # Ensure agent diff preview pick-up works:
        out["diff"] = summary
        out["text"] = summary

        # Cap overall payload if necessary
        blob = json.dumps(out, ensure_ascii=False)
        if len(blob) > max_bytes:
            trimmed = summary[-(max_bytes // 2):]
            out["unified"] = trimmed
            out["diff"] = trimmed
            out["text"] = trimmed
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fs_patch(
    path: str,
    replacements: Optional[List[Dict[str, Any]]] = None,
    create_if_missing: bool = True,
    flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Apply small, surgical regex replacements to a text file.

    Args:
      path: file to patch
      replacements: list of { "pattern": str, "replacement": str, "count": int|None }
        - 'pattern' is a Python regex (DOTALL by default via flags below)
        - 'replacement' is the replacement string
        - 'count' (optional) defaults to 0 (replace all)
      create_if_missing: if True, will create an empty file before applying replacements
      flags: optional list of flags ["IGNORECASE", "MULTILINE", "DOTALL", "UNICODE"]
    """
    try:
        fp = (REPO_ROOT / path).resolve()
        if not fp.exists():
            if not create_if_missing:
                return {"ok": False, "error": "File not found", "path": _safe_rel(fp)}
            _ensure_parent(fp)
            fp.write_text("", encoding="utf-8")

        text_ok, content, _trunc, _size = _read_file(fp, max_bytes=2_000_000)
        if not text_ok or content is None:
            return {"ok": False, "error": "Cannot patch non-text or unreadable file", "path": _safe_rel(fp)}

        current = content

        flag_val = re.UNICODE | re.DOTALL  # good default for multi-line edits
        if flags:
            for f in flags:
                name = f.upper().strip()
                if hasattr(re, name):
                    flag_val |= getattr(re, name)

        total_edits = 0
        if replacements:
            for r in replacements:
                pat = r.get("pattern", "")
                repl = r.get("replacement", "")
                count = int(r.get("count", 0))  # 0 = replace all
                try:
                    new_text, n = re.subn(pat, repl, current, count=count, flags=flag_val)
                    if n > 0:
                        total_edits += n
                        current = new_text
                except re.error as rex:
                    return {"ok": False, "error": f"Regex error for pattern {pat!r}: {rex}", "path": _safe_rel(fp)}

        changed = current != content
        if changed:
            fp.write_text(current, encoding="utf-8")

        return {
            "ok": True,
            "path": _safe_rel(fp),
            "changed": changed,
            "edits": total_edits,
            "bytes": fp.stat().st_size,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


# --------------------------------------------------------------------------------------
# Tool registry & OpenAI tool specs
# --------------------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[..., Dict[str, Any]]


_TOOLS: List[Tool] = [
    Tool(
        name="fs_map",
        description="List files/folders under a path (relative to repo root). Use to explore before editing.",
        parameters={
            "type": "object",
            "title": "FSMapArgs",
            "properties": {
                "root": {"type": "string", "title": "Root", "description": "Directory to scan (relative to repo root)", "default": "."},
                "max_depth": {"type": "integer", "title": "Max Depth", "minimum": 0, "maximum": 10, "default": 4},
                "include_content": {"type": "boolean", "title": "Include Content", "description": "Include file content when size <= 64KB", "default": False},
            },
        },
        func=fs_map,
    ),
    Tool(
        name="fs_glob",
        description="Glob for paths relative to repo root. Returns a list of matches.",
        parameters={
            "type": "object",
            "title": "FSGlobArgs",
            "properties": {
                "pattern": {"type": "string", "title": "Pattern"},
                "max_matches": {"type": "integer", "title": "Max Matches", "default": 2000},
            },
            "required": ["pattern"],
        },
        func=fs_glob,
    ),
    Tool(
        name="fs_read",
        description="Read up to max_bytes from a file (UTF-8). Binary files are marked.",
        parameters={
            "type": "object",
            "title": "FSReadArgs",
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "max_bytes": {"type": "integer", "title": "Max Bytes", "default": 200000},
            },
            "required": ["path"],
        },
        func=fs_read,
    ),
    Tool(
        name="fs_write",
        description="Write text content to a file. Creates parent dirs if needed. mode: 'w' overwrite or 'a' append.",
        parameters={
            "type": "object",
            "title": "FSWriteArgs",
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "content": {"type": "string", "title": "Content"},
                "mode": {"type": "string", "title": "Mode", "enum": ["w", "a"], "default": "w"},
            },
            "required": ["path", "content"],
        },
        func=fs_write,
    ),
    Tool(
        name="fs_mkdir",
        description="Create a directory (and parents).",
        parameters={
            "type": "object",
            "title": "FSMkdirArgs",
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "exist_ok": {"type": "boolean", "title": "Exist OK", "default": True},
                "parents": {"type": "boolean", "title": "Parents", "default": True},
            },
            "required": ["path"],
        },
        func=fs_mkdir,
    ),
    Tool(
        name="fs_delete",
        description="Delete a file or directory tree.",
        parameters={
            "type": "object",
            "title": "FSDeleteArgs",
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "recursive": {"type": "boolean", "title": "Recursive", "default": True},
            },
            "required": ["path"],
        },
        func=fs_delete,
    ),
    Tool(
        name="fs_diff",
        description="Summarize current repo snapshot or specific paths. Returns a readable summary and per-file previews.",
        parameters={
            "type": "object",
            "title": "FSDiffArgs",
            "properties": {
                "paths": {
                    "type": "array",
                    "title": "Paths",
                    "items": {"type": "string"},
                    "description": "Specific files/dirs to include. If omitted, scans ['backend','workspace'].",
                },
                "unified": {"type": "boolean", "title": "Unified", "default": True},
                "max_bytes": {"type": "integer", "title": "Max Bytes", "default": 120000},
            },
        },
        func=fs_diff,
    ),
    Tool(
        name="fs_patch",
        description="Apply small regex-based edits to a text file. Safer than wholesale rewrites.",
        parameters={
            "type": "object",
            "title": "FSPatchArgs",
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "replacements": {
                    "type": "array",
                    "title": "Replacements",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "title": "Pattern"},
                            "replacement": {"type": "string", "title": "Replacement"},
                            "count": {"type": "integer", "title": "Count", "description": "0 = replace all", "default": 0},
                        },
                        "required": ["pattern", "replacement"],
                    },
                    "description": "List of regex replacements to apply in order.",
                },
                "create_if_missing": {"type": "boolean", "title": "Create If Missing", "default": True},
                "flags": {
                    "type": "array",
                    "title": "Flags",
                    "items": {"type": "string", "enum": ["IGNORECASE", "MULTILINE", "DOTALL", "UNICODE"]},
                },
            },
            "required": ["path"],
        },
        func=fs_patch,
    ),
]


def openai_tool_specs() -> List[dict]:
    """
    Return tools in the exact shape OpenAI's Responses / Chat Completions
    APIs expect: {"type": "function", "function": {...}}.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in _TOOLS
    ]


def dispatch_tool_call(name: str, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Dispatch a tool by name with provided arguments. Always returns a JSON-serializable dict.
    """
    arguments = arguments or {}
    tool = next((t for t in _TOOLS if t.name == name), None)
    if not tool:
        return {"ok": False, "error": f"Unknown tool: {name}"}

    try:
        return tool.func(**arguments)
    except TypeError as te:
        # Helpful error when schema mismatches runtime kwargs
        return {"ok": False, "error": f"Invalid arguments for {name}: {te}", "arguments": arguments}
    except Exception as e:
        return {"ok": False, "error": str(e)}
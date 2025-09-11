# backend/app/services/quality_gate.py
from __future__ import annotations

import json
import subprocess
import shlex
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.app.core.config import settings
from backend.app.models.spec import OmegaSpec


# -------------------------
# Data structures
# -------------------------

@dataclass
class GateResult:
    passed: bool
    errors: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -------------------------
# Helpers
# -------------------------

def _read_text_safe(p: Path, max_bytes: int = 512_000) -> str:
    try:
        if not p.exists():
            return ""
        data = p.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _try_cmd(cmd: str, cwd: Optional[Path] = None, timeout: int = 25) -> Tuple[int, str, str]:
    """
    Run a shell command defensively. Timeouts and failures are reported but not raised.
    """
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        # tool not installed
        return 127, "", f"{cmd.split()[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _manifest_to_paths(root: Path, manifest: Union[Dict[str, Any], List[Any]]) -> List[Path]:
    """
    Accepts both the new manifest shape:
      {"files":[{"path":"...","language":"dart",...}, ...], "notes":"..."}
    and legacy list of paths/objects. Returns absolute Paths (existing or not).
    """
    paths: List[Path] = []

    def add_path(rel: str) -> None:
        if not rel:
            return
        p = (root / rel).resolve()
        if str(p).startswith(str(root.resolve())):
            paths.append(p)

    if isinstance(manifest, dict):
        files = manifest.get("files", [])
        # allow 'files' to be list[str] or list[dict]
        for f in files:
            if isinstance(f, str):
                add_path(f)
            elif isinstance(f, dict):
                add_path(str(f.get("path", "")).strip())
    elif isinstance(manifest, list):
        for f in manifest:
            if isinstance(f, str):
                add_path(f)
            elif isinstance(f, dict):
                add_path(str(f.get("path", "")).strip())

    # de-dup while preserving order
    out: List[Path] = []
    seen = set()
    for p in paths:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _basic_file_checks(paths: List[Path]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """
    Generic, language-agnostic checks: existence and non-empty, simple size limits.
    """
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {
        "file_count": len(paths),
        "present": 0,
        "nonempty": 0,
        "bytes_total": 0,
    }

    for p in paths:
        if not p.exists():
            warnings.append(f"missing: {p}")
            continue
        metrics["present"] += 1
        size = p.stat().st_size
        metrics["bytes_total"] += size
        if size == 0:
            warnings.append(f"empty: {p}")
        else:
            metrics["nonempty"] += 1

        # crude sanity filters to catch obvious junk
        if size > 2_000_000:  # 2 MB single file is likely off for generated source
            warnings.append(f"large-file: {p.name} ~{size} bytes")

        # quick header sniff for common types
        ext = p.suffix.lower()
        if ext in {".dart", ".yaml", ".yml", ".json", ".html", ".css", ".js", ".ts"}:
            head = _read_text_safe(p, max_bytes=500)
            if not head.strip():
                warnings.append(f"text-empty: {p}")

    # A truly broken artifact set
    if metrics["present"] == 0:
        errors.append("No generated files were found in staging manifest.")

    return errors, warnings, metrics


# -------------------------
# Target-specific checks
# -------------------------

def _flutter_checks(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """
    Light-touch Flutter gating:
      - pubspec.yaml exists and contains 'flutter:'
      - lib/main.dart exists and has main() and runApp(
      - optional: attempt `flutter --version` to confirm tool presence (no failure if missing)
      - optional: quick syntax probe via `dart --version` (presence only)
      - optional: `flutter analyze` when tools exist (soft-fail -> warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    pubspec = root / "pubspec.yaml"
    main_dart = root / "lib" / "main.dart"

    if not pubspec.exists():
        errors.append("Flutter: pubspec.yaml not found at project root.")
    else:
        content = _read_text_safe(pubspec)
        if "flutter:" not in content:
            warnings.append("Flutter: pubspec.yaml does not declare 'flutter:' section.")
        metrics["pubspec_bytes"] = len(content.encode("utf-8", errors="ignore"))

    if not main_dart.exists():
        errors.append("Flutter: lib/main.dart not found.")
    else:
        main_src = _read_text_safe(main_dart)
        if "void main(" not in main_src or "runApp(" not in main_src:
            warnings.append("Flutter: main.dart missing obvious entrypoint (main() / runApp()).")
        metrics["main_dart_bytes"] = len(main_src.encode("utf-8", errors="ignore"))

    # Tool presence (soft)
    rc_fv, out_fv, err_fv = _try_cmd("flutter --version", cwd=root)
    metrics["flutter_tool_rc"] = rc_fv
    if rc_fv == 127:
        warnings.append("Flutter SDK not installed on runner; skipped 'flutter analyze'.")
    elif rc_fv in (0, 124):  # ok or timeout
        # If available, try a very short analyze (may still be heavy; keep timeout)
        rc_an, out_an, err_an = _try_cmd("flutter analyze", cwd=root, timeout=40)
        metrics["flutter_analyze_rc"] = rc_an
        # We don't fail generation on analyzer warnings—just surface them
        if rc_an not in (0, 124, 127):
            warnings.append("Flutter analyze returned issues (non-blocking).")
            snippet = (out_an or err_an).splitlines()[-20:]
            metrics["flutter_analyze_tail"] = "\n".join(snippet)

    # Dart presence (soft)
    rc_dv, out_dv, err_dv = _try_cmd("dart --version", cwd=root)
    metrics["dart_tool_rc"] = rc_dv
    if rc_dv == 127:
        warnings.append("Dart SDK not installed on runner.")

    return errors, warnings, metrics


def _web_checks(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """
    Minimal Web gating:
      - index.html present and has a <head> and <body>
      - If a package.json exists, ensure it's valid JSON and has a start/build script (warn-only)
    """
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    index_html = root / "index.html"
    if not index_html.exists():
        # support typical scaffolds under web/
        index_html = root / "web" / "index.html"

    if not index_html.exists():
        errors.append("Web: index.html not found (searched ./ and ./web).")
    else:
        html = _read_text_safe(index_html)
        if "<body" not in html or "<head" not in html:
            warnings.append("Web: index.html missing <head> or <body>.")
        metrics["index_html_bytes"] = len(html.encode("utf-8", errors="ignore"))

    pkg = root / "package.json"
    if pkg.exists():
        raw = _read_text_safe(pkg)
        try:
            j = json.loads(raw or "{}")
            scripts = (j.get("scripts") or {}) if isinstance(j, dict) else {}
            if not isinstance(scripts, dict) or not any(k in scripts for k in ("start", "dev", "build")):
                warnings.append("Web: package.json lacks typical scripts (start/dev/build).")
            metrics["package_scripts"] = list(scripts.keys()) if isinstance(scripts, dict) else []
        except Exception:
            warnings.append("Web: package.json is not valid JSON.")

    return errors, warnings, metrics


# -------------------------
# Entry point
# -------------------------

def run_quality_gate(
    spec: OmegaSpec,
    manifest: Union[Dict[str, Any], List[Any]],
    staging_root: Optional[Path] = None,
) -> GateResult:
    """
    Performs quality checks over generated artifacts described by `manifest`.

    - Understands new manifest shape {"files":[...], "notes": "..."} and legacy lists.
    - Applies generic file checks plus target-specific checks:
        * Flutter (if it looks like a Flutter project)
        * Web (fallback minimal checks)
    - Honors feature flags in settings:
        * gate_enable_compile_guard
        * gate_enable_mvvm_checks (advisory only for Flutter)
        * gate_enable_web_checks

    Returns GateResult with pass/fail, errors, warnings, metrics, and a short summary.
    """
    staging = staging_root or settings.staging_root
    staging = Path(staging).resolve()

    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {
        "staging_root": str(staging),
        "feature_flags": {
            "compile_guard": settings.gate_enable_compile_guard,
            "mvvm_checks": settings.gate_enable_mvvm_checks,
            "web_checks": settings.gate_enable_web_checks,
        },
    }

    # 1) Expand manifest to absolute file paths
    paths = _manifest_to_paths(staging, manifest)
    e0, w0, m0 = _basic_file_checks(paths)
    errors.extend(e0)
    warnings.extend(w0)
    metrics.update(m0)  # merge basic metrics

    # Early exit if nothing to check
    if errors and metrics.get("present", 0) == 0:
        summary = "No files present to check. Failing gate."
        return GateResult(False, errors, warnings, metrics, summary)

    # 2) Detect target (best-effort)
    # FIX: don't pass a boolean into any(); just compute booleans directly
    looks_flutter = (
        (staging / "pubspec.yaml").exists()
        or (staging / "lib" / "main.dart").exists()
    )
    looks_web = (
        (staging / "index.html").exists()
        or (staging / "web" / "index.html").exists()
    )

    # If neither obvious, infer from file extensions/filenames found in manifest
    if not looks_flutter and not looks_web:
        for p in paths:
            if p.suffix == ".dart":
                looks_flutter = True
                break
            if p.name == "index.html" or p.suffix in {".html", ".css", ".js"}:
                looks_web = True

    # 3) Target-specific gates
    if looks_flutter and settings.gate_enable_compile_guard:
        e1, w1, m1 = _flutter_checks(staging)
        errors.extend(e1)
        warnings.extend(w1)
        metrics["flutter"] = m1

        # Optional advisory MVVM checks (structure only)
        if settings.gate_enable_mvvm_checks:
            mvvm_warnings: List[str] = []
            lib_dir = staging / "lib"
            if lib_dir.exists():
                for folder in ("core", "features"):
                    if not (lib_dir / folder).exists():
                        mvvm_warnings.append(f"Advisory: Consider 'lib/{folder}/' for modular structure.")
            if mvvm_warnings:
                warnings.extend(mvvm_warnings)

    # If not Flutter, try minimal web checks (or both if hybrid)
    if (not looks_flutter and looks_web) and settings.gate_enable_web_checks:
        e2, w2, m2 = _web_checks(staging)
        errors.extend(e2)
        warnings.extend(w2)
        metrics["web"] = m2

    # 4) Finalize
    passed = len(errors) == 0
    summary = (
        "Quality gate passed."
        if passed
        else f"Quality gate found {len(errors)} error(s) and {len(warnings)} warning(s)."
    )
    return GateResult(passed, errors, warnings, metrics, summary)
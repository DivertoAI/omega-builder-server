from __future__ import annotations

import json
import shlex
import subprocess
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
    """Run a shell command defensively. Timeouts and failures are reported but not raised."""
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
        return 127, "", f"{cmd.split()[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _manifest_to_paths(root: Path, manifest: Union[Dict[str, Any], List[Any]]) -> List[Path]:
    """
    Accepts both:
      {"files":[{"path":"...","language":"dart",...}, ...], "notes":"..."}
    and legacy list of strings/objects. Returns absolute Paths.
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

    out: List[Path] = []
    seen = set()
    for p in paths:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _basic_file_checks(paths: List[Path]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Language-agnostic checks: existence, non-empty, crude size sanity."""
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
        if size > 2_000_000:
            warnings.append(f"large-file: {p.name} ~{size} bytes")

        ext = p.suffix.lower()
        if ext in {".dart", ".yaml", ".yml", ".json", ".html", ".css", ".js", ".ts"}:
            head = _read_text_safe(p, max_bytes=500)
            if not head.strip():
                warnings.append(f"text-empty: {p}")

    if metrics["present"] == 0:
        errors.append("No generated files were found in staging manifest.")
    return errors, warnings, metrics


# -------------------------
# Target-specific checks
# -------------------------

def _flutter_checks(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
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

    rc_fv, _, _ = _try_cmd("flutter --version", cwd=root)
    metrics["flutter_tool_rc"] = rc_fv
    if rc_fv == 127:
        warnings.append("Flutter SDK not installed on runner; skipped 'flutter analyze'.")
    elif rc_fv in (0, 124):
        rc_an, out_an, err_an = _try_cmd("flutter analyze", cwd=root, timeout=40)
        metrics["flutter_analyze_rc"] = rc_an
        if rc_an not in (0, 124, 127):
            warnings.append("Flutter analyze returned issues (non-blocking).")
            snippet = (out_an or err_an).splitlines()[-20:]
            metrics["flutter_analyze_tail"] = "\n".join(snippet)

    rc_dv, _, _ = _try_cmd("dart --version", cwd=root)
    metrics["dart_tool_rc"] = rc_dv
    if rc_dv == 127:
        warnings.append("Dart SDK not installed on runner.")
    return errors, warnings, metrics


def _web_checks(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    index_html = root / "index.html"
    if not index_html.exists():
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
# North-star readiness checks
# -------------------------

def _check_design(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    droot = root / "design"
    fonts = droot / "fonts"
    tokens = droot / "tokens"
    theme = droot / "theme"

    metrics["present"] = {
        "design_dir": droot.exists(),
        "fonts": fonts.exists(),
        "tokens": tokens.exists(),
        "theme": theme.exists(),
    }

    # tokens
    tok_file = tokens / "tokens.dart"
    if not tok_file.exists():
        warnings.append("Design: tokens.dart missing.")
    else:
        src = _read_text_safe(tok_file)
        for needle in ("class OmegaTokens", "spacing", "radius", "colors"):
            if needle not in src:
                warnings.append(f"Design tokens missing hint: {needle}")

    # fonts
    fonts_file = fonts / "google_fonts.dart"
    if not fonts_file.exists():
        warnings.append("Design: fonts/google_fonts.dart missing.")
    else:
        ff = _read_text_safe(fonts_file)
        if "OmegaFonts" not in ff:
            warnings.append("Design fonts wrapper missing OmegaFonts class.")

    # theme
    theme_file = theme / "omega_theme.dart"
    if not theme_file.exists():
        warnings.append("Design: theme/omega_theme.dart missing.")
    else:
        th = _read_text_safe(theme_file)
        if "buildOmegaTheme" not in th:
            warnings.append("Design theme missing buildOmegaTheme().")

    ready = all(metrics["present"].values())
    metrics["ready"] = ready
    return errors, warnings, metrics


def _dir_stats(p: Path, exts: Optional[set[str]] = None) -> Tuple[int, int]:
    count = 0
    total = 0
    if not p.exists():
        return 0, 0
    for child in p.rglob("*"):
        if child.is_file():
            if exts:
                if child.suffix.lower() not in exts:
                    continue
            count += 1
            try:
                total += child.stat().st_size
            except Exception:
                pass
    return count, total


def _check_assets(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    warnings: List[str] = []
    assets_root = root / "assets"
    count, total = _dir_stats(assets_root, {".png", ".jpg", ".jpeg", ".webp", ".svg"})
    metrics = {
        "assets_dir": str(assets_root),
        "images_count": count,
        "images_bytes": total,
        "ready": count > 0,
    }
    if count == 0:
        warnings.append("Assets: no images found under /assets.")
    return [], warnings, metrics


def _check_infra(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    warnings: List[str] = []
    infra = root / "infra"
    dc = infra / "docker-compose.yml"
    envex = infra / ".env.example"
    ci = infra / "ci-preview.yml"

    present = {
        "infra_dir": infra.exists(),
        "docker_compose": dc.exists(),
        "env_example": envex.exists(),
        "ci_preview": ci.exists(),
    }
    ready = all(present.values())
    metrics = {"present": present, "ready": ready}
    if not ready:
        warnings.append("Infra: expected docker-compose.yml, .env.example, ci-preview.yml under /infra.")
    return [], warnings, metrics


def _check_adapters(root: Path) -> Tuple[List[str], List[str], Dict[str, Any]]:
    warnings: List[str] = []
    adapters = root / "adapters"
    stubs = {
        "payments_adapter.dart": (adapters / "payments_adapter.dart").exists(),
        "ocr_adapter.dart": (adapters / "ocr_adapter.dart").exists(),
        "telemed_adapter.dart": (adapters / "telemed_adapter.dart").exists(),
        "logistics_adapter.dart": (adapters / "logistics_adapter.dart").exists(),
    }
    ready = all(stubs.values())
    if not ready:
        warnings.append("Adapters: one or more adapter stubs are missing under /adapters.")
    return [], warnings, {"present": stubs, "ready": ready}


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
    Also reports north-star readiness for design/assets/infra/adapters.
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

    # 1) Expand manifest + generic checks
    paths = _manifest_to_paths(staging, manifest)
    e0, w0, m0 = _basic_file_checks(paths)
    errors.extend(e0)
    warnings.extend(w0)
    metrics.update(m0)

    if errors and metrics.get("present", 0) == 0:
        summary = "No files present to check. Failing gate."
        return GateResult(False, errors, warnings, metrics, summary)

    # 2) Target detection
    looks_flutter = (staging / "pubspec.yaml").exists() or (staging / "lib" / "main.dart").exists()
    looks_web = (staging / "index.html").exists() or (staging / "web" / "index.html").exists()
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

        if settings.gate_enable_mvvm_checks:
            mvvm_warnings: List[str] = []
            lib_dir = staging / "lib"
            if lib_dir.exists():
                for folder in ("core", "features"):
                    if not (lib_dir / folder).exists():
                        mvvm_warnings.append(f"Advisory: Consider 'lib/{folder}/' for modular structure.")
            warnings.extend(mvvm_warnings)

    if (not looks_flutter and looks_web) and settings.gate_enable_web_checks:
        e2, w2, m2 = _web_checks(staging)
        errors.extend(e2)
        warnings.extend(w2)
        metrics["web"] = m2

    # 4) North-star readiness
    de, dw, dm = _check_design(staging)
    ae, aw, am = _check_assets(staging)
    ie, iw, im = _check_infra(staging)
    ce, cw, cm = _check_adapters(staging)
    errors += de + ae + ie + ce
    warnings += dw + aw + iw + cw
    metrics["readiness"] = {
        "design": dm,
        "assets": am,
        "infra": im,
        "adapters": cm,
    }

    # 5) Finalize
    passed = len(errors) == 0
    summary = (
        "Quality gate passed."
        if passed
        else f"Quality gate found {len(errors)} error(s) and {len(warnings)} warning(s)."
    )
    return GateResult(passed, errors, warnings, metrics, summary)
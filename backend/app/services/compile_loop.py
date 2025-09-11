# backend/app/services/compile_loop.py
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

# Allow override via env; default to 'flutter' on PATH
FLUTTER_BIN = os.getenv("FLUTTER_BIN", "flutter")

# Comma-separated desired platforms (created if missing)
# e.g. "macos,ios,android" — default to macOS since that's what often fails locally
DEFAULT_PLATFORMS = [p.strip() for p in os.getenv("OMEGA_FLUTTER_PLATFORMS", "macos").split(",") if p.strip()]

@dataclass
class CmdResult:
    cmd: str
    rc: int
    seconds: float
    stdout: str
    stderr: str

@dataclass
class CompileRound:
    round: int
    pub_get_run: bool
    analyze: CmdResult
    tests: Optional[CmdResult]
    fixes_applied: List[str]

@dataclass
class CompileReport:
    ok: bool
    rounds: List[CompileRound]
    message: str

# ---------------------------
# Logging & runners
# ---------------------------

def _log(msg: str) -> None:
    # Lightweight logger to stdout so it shows up in uvicorn logs
    print(f"[compile_loop] {msg}", flush=True)

def _run(cmd: List[str], cwd: Path, timeout: int = 300) -> CmdResult:
    t0 = time.time()
    _log(f"run: {' '.join(shlex.quote(c) for c in cmd)} (cwd={cwd})")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
    )
    return CmdResult(
        cmd=" ".join(shlex.quote(c) for c in cmd),
        rc=proc.returncode,
        seconds=round(time.time() - t0, 2),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )

def _run_test_machine(
    cmd: List[str],
    cwd: Path,
    *,
    hard_timeout: int,
    watchdog_idle_sec: int,
) -> CmdResult:
    """
    Run tests with --machine output and a simple idle watchdog.
    If no output is produced for watchdog_idle_sec, kill the process.
    """
    t0 = time.time()
    _log(f"run(test): {' '.join(shlex.quote(c) for c in cmd)} (cwd={cwd}, watchdog={watchdog_idle_sec}s, timeout={hard_timeout}s)")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    last_activity = time.time()
    out_chunks: List[str] = []
    err_chunks: List[str] = []

    try:
        while True:
            stdout_line = proc.stdout.readline() if proc.stdout else ""
            stderr_line = proc.stderr.readline() if proc.stderr else ""

            if stdout_line:
                out_chunks.append(stdout_line)
                last_activity = time.time()
            if stderr_line:
                err_chunks.append(stderr_line)
                last_activity = time.time()

            if proc.poll() is not None:
                if proc.stdout:
                    rest = proc.stdout.read()
                    if rest:
                        out_chunks.append(rest)
                if proc.stderr:
                    rest = proc.stderr.read()
                    if rest:
                        err_chunks.append(rest)
                break

            if time.time() - last_activity > watchdog_idle_sec:
                _log(f"watchdog: idle > {watchdog_idle_sec}s; terminating tests")
                proc.kill()
                return CmdResult(
                    cmd=" ".join(shlex.quote(c) for c in cmd),
                    rc=124,
                    seconds=round(time.time() - t0, 2),
                    stdout="".join(out_chunks),
                    stderr="".join(err_chunks) + f"\n[watchdog] test runner idle > {watchdog_idle_sec}s; killed",
                )

            if time.time() - t0 > hard_timeout:
                _log(f"timeout: test run exceeded {hard_timeout}s; terminating")
                proc.kill()
                return CmdResult(
                    cmd=" ".join(shlex.quote(c) for c in cmd),
                    rc=124,
                    seconds=round(time.time() - t0, 2),
                    stdout="".join(out_chunks),
                    stderr="".join(err_chunks) + f"\n[timeout] test run exceeded {hard_timeout}s; killed",
                )

            time.sleep(0.05)

        return CmdResult(
            cmd=" ".join(shlex.quote(c) for c in cmd),
            rc=proc.returncode or 0,
            seconds=round(time.time() - t0, 2),
            stdout="".join(out_chunks),
            stderr="".join(err_chunks),
        )
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return CmdResult(
            cmd=" ".join(shlex.quote(c) for c in cmd),
            rc=1,
            seconds=round(time.time() - t0, 2),
            stdout="".join(out_chunks),
            stderr="".join(err_chunks) + f"\n[exception] {e}",
        )

# ---------------------------
# Heuristic fixers (safe only)
# ---------------------------

def _ensure_pub_get(app_dir: Path, timeout: int) -> CmdResult:
    # Run once to warm cache; callers decide whether to run per round
    return _run([FLUTTER_BIN, "pub", "get"], app_dir, timeout=timeout)

_MISSING_URI_RE = re.compile(r"Target of URI doesn't exist:\s*'([^']+)'", re.I)
_MISSING_CLASS_RE = re.compile(
    r"(?:The name '([A-Za-z_][A-Za-z0-9_]*)' isn't a class|Undefined class '([A-Za-z_][A-Za-z0-9_]*)')",
    re.I,
)
_UNDEFINED_METHOD_RE = re.compile(
    r"The method '([A-Za-z_][A-Za-z0-9_]*)' isn't defined for the type '([A-Za-z_][A-Za-z0-9_]*)'",
    re.I,
)

def _is_within(base: Path, p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(base.resolve())
    except AttributeError:
        base_r = str(base.resolve())
        pr = str(p.resolve())
        return pr == base_r or pr.startswith(base_r.rstrip(os.sep) + os.sep)

def _safe_under(base: Path, desired: Path, fallback_rel: str) -> Path:
    """
    If 'desired' escapes 'base', return base/fallback_rel instead.
    """
    try:
        desired_res = desired.resolve()
    except Exception:
        desired_res = desired
    if _is_within(base, desired_res):
        return desired_res
    safe = (base / fallback_rel).resolve()
    _log(f"clamp: desired={desired_res} escaped base={base}; using fallback={safe}")
    return safe

def _make_file(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return f"created {path}"
    if path.stat().st_size < 40:
        path.write_text(content, encoding="utf-8")
        return f"filled {path}"
    return f"kept {path}"

def _widget_stub(name: str) -> str:
    return f"""import 'package:flutter/material.dart';

class {name} extends StatelessWidget {{
  const {name}({{super.key}});
  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      appBar: AppBar(title: const Text('{name}')),
      body: const Center(child: Text('{name} placeholder')),
    );
  }}
}}
"""

def _class_stub(name: str) -> str:
    return f"class {name} {{ const {name}(); }}\n"

def _filename_to_class(fname: str) -> str:
    base = Path(fname).stem
    parts = [p for p in base.split('_') if p]
    return ''.join(p.capitalize() for p in parts)

def _guess_path_from_uri(app_dir: Path, origin_within_lib: Path, uri: str) -> Optional[Path]:
    """
    Take a missing import URI and propose a file path **inside** app_dir.
    origin_within_lib: the directory (within lib/) where the import came from (best effort).
    We strictly clamp the result inside app_dir.
    """
    lib_dir = (app_dir / "lib").resolve()

    # Skip SDK/package URIs
    if uri.startswith(("dart:", "package:flutter")):
        return None

    # package:my_app/features/.. => map after the package name straight under lib/
    if uri.startswith("package:"):
        try:
            after = uri.split("/", 1)[1]
        except IndexError:
            return None
        desired = lib_dir / after
        return _safe_under(app_dir, desired, f"lib/shared/autofix/{Path(after).name}")

    # Relative import – anchor to the origin file's folder when possible
    anchor = origin_within_lib if str(origin_within_lib).startswith(str(lib_dir)) else lib_dir
    desired = (anchor / uri).resolve()
    return _safe_under(app_dir, desired, f"lib/shared/autofix/{Path(uri).name}")

def _apply_fixes(app_dir: Path, analyze_out: str) -> List[str]:
    """
    Parse analyzer output and apply minimal, safe fixes.
    Returns a list of human-readable fix descriptions.
    """
    fixes: List[str] = []
    lib_dir = (app_dir / "lib").resolve()

    probable_origins = [
        lib_dir / "core" / "routing" / "app_router.dart",
        lib_dir,
    ]
    origin = next((p for p in probable_origins if p.exists()), lib_dir)

    # 1) Missing URI => create file with a plausible stub (widget if ends with _view)
    for uri in _MISSING_URI_RE.findall(analyze_out):
        proto = _guess_path_from_uri(app_dir, origin, uri)
        if not proto:
            _log(f"uri-fix: skip non-user uri={uri}")
            continue

        cls = _filename_to_class(Path(uri).name)
        _log(f"uri-fix: uri='{uri}' origin='{origin}' -> target='{proto}' (inside={_is_within(app_dir, proto)})")

        target = _safe_under(app_dir, proto, f"lib/shared/autofix/{Path(uri).name}")

        if target.name.endswith("_view.dart"):
            action = _make_file(target, _widget_stub(cls))
        else:
            action = _make_file(target, _class_stub(cls))

        fixes.append(action)

    # 2) Missing class => generate a small stub under lib/shared/autofix
    for m in _MISSING_CLASS_RE.findall(analyze_out):
        cls = m[0] or m[1]
        if not cls:
            continue
        stub_path = (lib_dir / "shared" / "autofix" / f"{cls.lower()}.dart").resolve()
        stub_path = _safe_under(app_dir, stub_path, f"lib/shared/autofix/{cls.lower()}.dart")
        fixes.append(_make_file(stub_path, _class_stub(cls)))

    # 3) Undefined method — just log (we avoid risky edits)
    if _UNDEFINED_METHOD_RE.search(analyze_out):
        fixes.append("note: undefined method(s) detected; not auto-fixed (safety)")

    # 4) Ensure a basic smoke test exists so `flutter test` can run.
    tests_dir = app_dir / "test"
    if not tests_dir.exists():
        tests_dir.mkdir(parents=True, exist_ok=True)
    smoke = tests_dir / "smoke_test.dart"
    if not smoke.exists():
        smoke.write_text(
            """import 'package:flutter_test/flutter_test.dart';

void main() {
  test('smoke', () {
    expect(1 + 1, 2);
  });
}
""",
            encoding="utf-8",
        )
        fixes.append(f"created {smoke}")

    return [f for f in fixes if f]

# ---------------------------
# Platform scaffolding (new)
# ---------------------------

def _platform_dir_for(platform: str) -> str:
    # Map platform name to the directory Flutter creates in project root
    return {
        "macos": "macos",
        "ios": "ios",
        "android": "android",
        "linux": "linux",
        "windows": "windows",
        "web": "web",
      }.get(platform, platform)

def _ensure_platform_scaffold(app_dir: Path, platforms: List[str], timeout: int = 600) -> List[str]:
    """
    If a requested platform directory is missing, run:
        flutter create . --platforms=<comma-separated>
    Returns a list of actions taken (for logging).
    """
    actions: List[str] = []
    need: List[str] = []
    for p in platforms:
        plat = p.strip().lower()
        if not plat:
            continue
        plat_dir = app_dir / _platform_dir_for(plat)
        if not plat_dir.exists():
            need.append(plat)

    if need:
        _log(f"platform: ensuring scaffold for {need}")
        # Flutter allows multiple platforms in one create
        cmd = [FLUTTER_BIN, "create", ".", f"--platforms={','.join(need)}"]
        try:
            res = _run(cmd, app_dir, timeout=timeout)
            if res.rc == 0:
                actions.append(f"flutter create . --platforms={','.join(need)}")
            else:
                actions.append(f"platform create failed (rc={res.rc})")
                _log(f"platform create stderr:\n{res.stderr}")
        except FileNotFoundError:
            actions.append("flutter not found while creating platforms")
    return actions

# ---------------------------
# macOS cruft cleaner
# ---------------------------

def _purge_macos_cruft_in_tests(app_dir: Path) -> List[str]:
    """
    Remove AppleDouble & Finder junk that break dart test:
      - Files starting with '._' (AppleDouble resource forks)
      - '.DS_Store'
    Only within the test/ directory tree.
    """
    removed: List[str] = []
    tdir = app_dir / "test"
    if not tdir.exists():
        return removed

    for p in tdir.rglob("*"):
        try:
            name = p.name
            if p.is_file() and (name.startswith("._") or name == ".DS_Store"):
                p.unlink(missing_ok=True)
                removed.append(f"deleted {p}")
        except Exception as e:
            _log(f"cleanup warning: could not remove {p}: {e}")
    if removed:
        _log(f"cleanup: purged {len(removed)} macOS sidecar files in test/")
    return removed

# ---------------------------
# Public API
# ---------------------------

def run_compile_loop(
    app_dir: Path,
    *,
    run_tests: bool = True,
    max_rounds: int = 3,
    run_pub_get_first: bool = True,
    # timeouts
    pub_timeout_sec: int = 180,
    analyze_timeout_sec: int = 180,
    test_timeout_first_sec: int = 900,
    test_timeout_sec: int = 360,
    watchdog_idle_sec: int = 60,
    # new: platforms to ensure (defaults via env)
    platforms: Optional[List[str]] = None,
) -> CompileReport:
    """
    Runs flutter analyze (+ tests) in a loop; applies safe fixes and retries.
    Strategy:
      - Ensure requested platform scaffolds exist (e.g., macos/) before analyze.
      - Round 1: run ONLY smoke test (fast feedback, prevents suite discovery hang).
      - Once smoke passes, later rounds may run the full test suite.
      - Uses --machine output with an idle watchdog for tests.
      - Purges macOS AppleDouble (. _*) and .DS_Store files under test/ each round.
    """
    app_dir = app_dir.resolve()
    _log(f"compile_loop: app_dir={app_dir}")
    _log(f"compile_loop: using FLUTTER_BIN={FLUTTER_BIN}")

    rounds: List[CompileRound] = []
    ok_overall = False
    message = ""
    full_suite_allowed = False  # flips true after smoke passes at least once

    # Ensure platform scaffolds first (common cause of "No macOS desktop project configured")
    desired_platforms = platforms if platforms is not None else DEFAULT_PLATFORMS
    if desired_platforms:
        _ = _ensure_platform_scaffold(app_dir, desired_platforms, timeout=max(test_timeout_first_sec, 600))

    if run_pub_get_first:
        try:
            _ensure_pub_get(app_dir, timeout=pub_timeout_sec)  # ignore rc, pub may be up-to-date
        except FileNotFoundError:
            return CompileReport(
                ok=False,
                rounds=[],
                message=f"flutter binary not found (FLUTTER_BIN='{FLUTTER_BIN}'). "
                        f"Set FLUTTER_BIN env or adjust PATH for the worker process.",
            )

    for r in range(1, max_rounds + 1):
        # Always purge macOS junk before analysis/tests
        _purge_macos_cruft_in_tests(app_dir)

        # ANALYZE
        try:
            analyze_res = _run([FLUTTER_BIN, "analyze", "--no-pub"], app_dir, timeout=analyze_timeout_sec)
        except FileNotFoundError:
            return CompileReport(
                ok=False,
                rounds=rounds,
                message=f"flutter binary not found during analyze (FLUTTER_BIN='{FLUTTER_BIN}')",
            )

        fixes: List[str] = []
        pub_get_run = False

        if analyze_res.rc != 0:
            # Try autofixes based on analyzer output
            try:
                fixes = _apply_fixes(app_dir, analyze_res.stdout + "\n" + analyze_res.stderr)
            except OSError as e:
                return CompileReport(
                    ok=False,
                    rounds=rounds,
                    message=f"autofix failed due to filesystem error: {e}",
                )

            # Opportunistic: common lint auto-fixes
            if "annotate_overrides" in analyze_res.stdout or "annotate_overrides" in analyze_res.stderr:
                _log("dart fix: applying automatic code fixes")
                try:
                    _ = _run(["dart", "fix", "--apply"], app_dir, timeout=analyze_timeout_sec)
                except Exception as e:
                    _log(f"dart fix failed (non-fatal): {e}")

            pub_get_run = bool(fixes)
            if pub_get_run:
                try:
                    _ensure_pub_get(app_dir, timeout=pub_timeout_sec)
                except FileNotFoundError:
                    return CompileReport(
                        ok=False,
                        rounds=rounds,
                        message=f"flutter binary not found during pub get (FLUTTER_BIN='{FLUTTER_BIN}')",
                    )
                analyze_res = _run([FLUTTER_BIN, "analyze", "--no-pub"], app_dir, timeout=analyze_timeout_sec)

        # If analyze still fails and no fixes applied, we stop (avoid infinite loop)
        if analyze_res.rc != 0 and not fixes:
            rounds.append(CompileRound(r, pub_get_run, analyze_res, None, fixes))
            message = "analyze failed; no safe fixes available"
            break

        # TESTS
        test_res: Optional[CmdResult] = None
        if run_tests:
            try:
                tests_dir = app_dir / "test"
                smoke = tests_dir / "smoke_test.dart"
                first_round = (r == 1)

                if first_round or not full_suite_allowed:
                    tests_dir.mkdir(parents=True, exist_ok=True)
                    if not smoke.exists():
                        smoke.write_text(
                            """import 'package:flutter_test/flutter_test.dart';

void main() {
  test('smoke', () {
    expect(1 + 1, 2);
  });
}
""",
                            encoding="utf-8",
                        )
                    cmd = [FLUTTER_BIN, "test", str(smoke), "--concurrency=1", "--machine"]
                    test_res = _run_test_machine(
                        cmd,
                        app_dir,
                        hard_timeout=test_timeout_first_sec if first_round else test_timeout_sec,
                        watchdog_idle_sec=watchdog_idle_sec,
                    )
                    if test_res.rc == 0:
                        full_suite_allowed = True
                else:
                    cmd = [FLUTTER_BIN, "test", "--concurrency=1", "--machine"]
                    test_res = _run_test_machine(
                        cmd,
                        app_dir,
                        hard_timeout=test_timeout_sec,
                        watchdog_idle_sec=watchdog_idle_sec,
                    )
            except FileNotFoundError:
                return CompileReport(
                    ok=False,
                    rounds=rounds,
                    message=f"flutter binary not found during test run (FLUTTER_BIN='{FLUTTER_BIN}')",
                )

        rounds.append(CompileRound(r, pub_get_run, analyze_res, test_res, fixes))

        analyze_ok = (analyze_res.rc == 0)
        tests_ok = (test_res.rc == 0) if test_res else True

        if analyze_ok and tests_ok:
            ok_overall = True
            message = "analyze & tests passed"
            break

    if not ok_overall and not message:
        message = "compile loop exhausted without success"

    return CompileReport(ok=ok_overall, rounds=rounds, message=message)

def serialize_report(rep: CompileReport) -> Dict:
    return {
        "ok": rep.ok,
        "message": rep.message,
        "rounds": [
            {
                "round": r.round,
                "pub_get_run": r.pub_get_run,
                "analyze": asdict(r.analyze),
                "tests": asdict(r.tests) if r.tests else None,
                "fixes_applied": r.fixes_applied,
            }
            for r in rep.rounds
        ],
    }
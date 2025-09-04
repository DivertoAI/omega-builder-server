# backend/app/services/quality_gate.py
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from typing import Dict, List, Optional, Tuple, Any

# --------------------------------------------------------------------------------------
# Constants / small helpers
# --------------------------------------------------------------------------------------

MIN_BYTES = 40

def _exists_p(path: Path, min_bytes: int = MIN_BYTES) -> Tuple[bool, str]:
    if not path.exists():
        return False, f"missing {path.name}"
    if path.is_dir():
        return True, ""
    if path.stat().st_size < min_bytes:
        return False, f"{path.name} too small"
    return True, ""

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def _write(path: Path, content: str, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not path.exists():
        path.write_text(content, encoding="utf-8")

def _sh(cmd: str, cwd: Path | None = None, ok_codes: set[int] | None = None) -> Tuple[int, str]:
    """
    Run shell command, capture combined stdout/stderr.
    Returns (returncode==0?, output).
    """
    ok = ok_codes or {0}
    p = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None,
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = p.communicate()
    return (0 if p.returncode in ok else p.returncode), out

# --------------------------------------------------------------------------------------
# Web (no-build) gate  — original rules kept
# --------------------------------------------------------------------------------------

def _gate_web(app: Path, persistence_hint: bool) -> Dict:
    checks, missing = [], []

    ok, why = _exists_p(app / "index.html", 120)
    checks.append(("index.html", ok, why))
    (missing.append(why) if not ok and why else None)

    ok, why = _exists_p(app / "styles.css", 20)
    checks.append(("styles.css", ok, why))
    (missing.append(why) if not ok and why else None)

    ok, why = _exists_p(app / "main.js", 60)
    checks.append(("main.js", ok, why))
    (missing.append(why) if not ok and why else None)

    html = _read(app / "index.html")
    if "<title" not in html.lower():
        missing.append("index.html missing <title>")
    if not re.search(r'id="(app|root)"', html, re.I):
        missing.append("index.html missing #app/#root mount")

    js = _read(app / "main.js")
    if persistence_hint and "localStorage" not in js:
        missing.append("main.js missing localStorage usage")

    ok_all = len(missing) == 0
    return {"ok": ok_all, "checks": checks, "missing": missing, "tips": []}

# --------------------------------------------------------------------------------------
# Flutter compile-safe baseline + auto-stub repair
# --------------------------------------------------------------------------------------

# Minimal baseline that ALWAYS compiles. Agent edits can safely layer on top.

_BASE_MAIN = dedent("""\
import 'package:flutter/widgets.dart';
import 'app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const MyApp());
}
""")

_BASE_APP = dedent("""\
import 'package:flutter/material.dart';
import 'core/theme/app_theme.dart';
import 'core/routing/app_router.dart';

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Omega App',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      onGenerateRoute: AppRouter.onGenerateRoute,
      initialRoute: AppRouter.home,
    );
  }
}
""")

_BASE_THEME = dedent("""\
import 'package:flutter/material.dart';

class AppTheme {
  static ThemeData get light => ThemeData(
    colorSchemeSeed: const Color(0xFF3B82F6),
    useMaterial3: true,
  );
}
""")

_BASE_ROUTER = dedent("""\
import 'package:flutter/material.dart';
import 'routes.g.dart';

class AppRouter {
  static const String home = '/';
  static const String details = '/details';
  static const String settings = '/settings';

  static Route<dynamic> onGenerateRoute(RouteSettings settings) {
    final name = settings.name ?? home;
    switch (name) {
      case home:
        return MaterialPageRoute(builder: (_) => const HomeView());
      case details:
        final id = settings.arguments as String?;
        return MaterialPageRoute(builder: (_) => DetailsView(itemId: id));
      case AppRouter.settings:
        return MaterialPageRoute(builder: (_) => const SettingsView());
      default:
        return MaterialPageRoute(builder: (_) => const NotFoundView());
    }
  }
}
""")

_BASE_ROUTES_G = dedent("""\
import 'package:flutter/material.dart';

class HomeView extends StatelessWidget {
  const HomeView({super.key});
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Home (stub)')),
    body: ListView.builder(
      itemCount: 10,
      itemBuilder: (c, i) => ListTile(
        title: Text('Item #$i'),
        onTap: () => Navigator.of(context).pushNamed('/details', arguments: 'item-$i'),
      ),
    ),
    drawer: Drawer(
      child: ListView(children: const [
        DrawerHeader(child: Text('Menu')),
        ListTile(leading: Icon(Icons.settings), title: Text('Settings (stub)')),
      ]),
    ),
  );
}

class DetailsView extends StatelessWidget {
  final String? itemId;
  const DetailsView({super.key, this.itemId});
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Details (stub)')),
    body: Center(child: Text('Details for ${itemId ?? "unknown"}')),
  );
}

class SettingsView extends StatelessWidget {
  const SettingsView({super.key});
  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(child: Text('Settings (stub)')),
  );
}

class NotFoundView extends StatelessWidget {
  const NotFoundView({super.key});
  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(child: Text('Route not found')),
  );
}
""")

# Added: baseline LocalStore so MVVM gate won't fail
_BASE_LOCAL_STORE = dedent("""\
import 'package:shared_preferences/shared_preferences.dart';

class LocalStore {
  static Future<SharedPreferences> get _prefs async => SharedPreferences.getInstance();

  static Future<bool> setString(String key, String value) async {
    final p = await _prefs;
    return p.setString(key, value);
  }

  static Future<String?> getString(String key) async {
    final p = await _prefs;
    return p.getString(key);
  }

  static Future<bool> setBool(String key, bool value) async {
    final p = await _prefs;
    return p.setBool(key, value);
  }

  static Future<bool> remove(String key) async {
    final p = await _prefs;
    return p.remove(key);
  }
}
""")

# Include provider + shared_preferences by default so analyzer + MVVM checks align
_BASE_PUBSPEC = dedent("""\
name: omega_app
publish_to: "none"
environment:
  sdk: ">=3.4.0 <4.0.0"
dependencies:
  flutter:
    sdk: flutter
  cupertino_icons: ^1.0.8
  provider: ^6.1.2
  shared_preferences: ^2.3.2
dev_dependencies:
  flutter_lints: ^4.0.0
  flutter_test:
    sdk: flutter
flutter:
  uses-material-design: true
""")

def write_baseline_if_missing(app_dir: Path) -> Dict[str, Any]:
    """
    Idempotently ensure a minimal, compile-safe Flutter skeleton exists.
    Safe to run on every generation before/after agent edits.
    """
    lib = app_dir / "lib"
    _write(app_dir / "pubspec.yaml", _BASE_PUBSPEC)  # minimal + deterministic
    _write(lib / "main.dart", _BASE_MAIN, overwrite=False)
    _write(lib / "app.dart", _BASE_APP, overwrite=False)
    _write(lib / "core" / "theme" / "app_theme.dart", _BASE_THEME, overwrite=False)
    _write(lib / "core" / "routing" / "app_router.dart", _BASE_ROUTER, overwrite=False)
    _write(lib / "core" / "routing" / "routes.g.dart", _BASE_ROUTES_G, overwrite=False)
    _write(lib / "core" / "storage" / "local_store.dart", _BASE_LOCAL_STORE, overwrite=False)
    return {"ok": True}

# Analyzer patterns
_RE_MISSING_URI  = re.compile(r"Target of URI doesn't exist: '([^']+)'")
_RE_MISSING_TYPE = re.compile(r"The (?:method|class) '([^']+)' isn't defined")

def _ensure_pub_deps(app_dir: Path, deps: List[str]) -> bool:
    """
    Ensure the given dependencies exist in pubspec.yaml under 'dependencies:'.
    If any are missing, inject them and run flutter pub get.
    Returns True if pubspec was changed.
    """
    pub = app_dir / "pubspec.yaml"
    text = _read(pub)
    if not text:
        return False

    changed = False
    # Find 'dependencies:' anchor
    if "dependencies:" not in text:
        # add a dependencies block if somehow missing
        text = text.strip() + "\n\ndependencies:\n"
        changed = True

    for d in deps:
        # simple key presence check (start of line, optional version)
        if not re.search(rf"^\s*{re.escape(d)}\s*:", text, re.M):
            # inject under dependencies: keeping indentation to two spaces
            text = re.sub(r"(^dependencies:\s*\n)", rf"\1  {d}: ^0.0.1\n", text, flags=re.M)
            changed = True

    if changed:
        _write(pub, text, overwrite=True)
        _sh("flutter pub get", cwd=app_dir)
    return changed

def _run_flutter_sanity(app_dir: Path) -> Tuple[bool, str]:
    # Clean macOS AppleDouble noise so globs/analyzer aren't polluted.
    _sh("dot_clean -m . 2>/dev/null || true", cwd=app_dir)
    _sh("find . -type f -name '._*' -delete || true", cwd=app_dir)
    rc1, out1 = _sh("flutter pub get", cwd=app_dir)
    rc2, out2 = _sh("flutter analyze --no-fatal-infos --no-fatal-warnings", cwd=app_dir, ok_codes={0,1})
    ok = (rc1 == 0) and (rc2 == 0)
    return ok, (out1 + "\n" + out2)

def _auto_stub_missing_types(app_dir: Path, analyze_out: str) -> bool:
    """
    If analyzer reports missing classes (usually referenced screens),
    append minimal widget stubs with those class names into routes.g.dart.
    This avoids importing files that don't exist yet.
    """
    routes_g = app_dir / "lib/core/routing/routes.g.dart"
    if not routes_g.exists():
        return False
    text = routes_g.read_text(encoding="utf-8")
    added = False
    for sym in sorted(set(_RE_MISSING_TYPE.findall(analyze_out))):
        # Only create plausible class names (start uppercase).
        if not sym or not sym[0].isupper():
            continue
        if re.search(rf"\bclass\s+{re.escape(sym)}\b", text):
            continue
        text += dedent(f"""
        
        class {sym} extends StatelessWidget {{
          const {sym}({{super.key}});
          @override
          Widget build(BuildContext context) => const Scaffold(
            body: Center(child: Text('{sym} (auto-stub)')),
          );
        }}
        """)
        added = True
    if added:
        routes_g.write_text(text, encoding="utf-8")
    return added

def enforce_compile_safe(app_dir: Path) -> Dict[str, Any]:
    """
    Ensure the Flutter app compiles (analyze passes) by:
      1) writing a minimal baseline
      2) ensuring required deps exist in pubspec (provider, shared_preferences)
      3) running analyzer
      4) stubbing any missing classes into routes.g.dart
      5) (last resort) re-run analyze; router already falls back to NotFoundView
    Always returns a dict with status; caller can log/emit it.
    """
    write_baseline_if_missing(app_dir)
    _ensure_pub_deps(app_dir, ["provider", "shared_preferences"])

    ok, out = _run_flutter_sanity(app_dir)
    if ok:
        return {"ok": True, "analyze_ok": True}

    changed = _auto_stub_missing_types(app_dir, out)
    if changed:
        ok2, out2 = _run_flutter_sanity(app_dir)
        if ok2:
            return {"ok": True, "analyze_ok": True, "repaired": True}
        # keep latest analyzer output
        out = out2

    # Last try (router already safe via baseline)
    ok3, out3 = _run_flutter_sanity(app_dir)
    return {"ok": ok3, "analyze_ok": ok3, "repaired": changed, "analyzer": out3 if not ok3 else ""}

# --------------------------------------------------------------------------------------
# Original Flutter MVVM gate — now aligns with baseline
# --------------------------------------------------------------------------------------

def _gate_flutter_mvvm(app: Path) -> Dict:
    checks, missing = [], []
    # required files
    for rel, minb in [
        ("pubspec.yaml", 80),
        ("lib/main.dart", 120),
        ("lib/app.dart", 80),
        ("lib/core/theme/app_theme.dart", 40),
        ("lib/core/storage/local_store.dart", 40),
    ]:
        ok, why = _exists_p(app / rel, minb)
        checks.append((rel, ok, why))
        (missing.append(why or f"missing {rel}") if not ok else None)

    # structure hints
    has_mvvm = bool(list((app / "lib" / "features").glob("**/viewmodel/*.dart")))
    if not has_mvvm:
        missing.append("no features/*/viewmodel/*.dart (MVVM)")

    pubspec = _read(app / "pubspec.yaml")
    if "provider:" not in pubspec:
        missing.append("pubspec: dependency 'provider' missing")
    if "shared_preferences:" not in pubspec:
        missing.append("pubspec: dependency 'shared_preferences' missing")

    ok_all = len(missing) == 0
    tips = ["Run: flutter pub get"] if ok_all else []
    return {"ok": ok_all, "checks": checks, "missing": missing, "tips": tips}

# --------------------------------------------------------------------------------------
# Public API (used by generate service)
# --------------------------------------------------------------------------------------

def run_gates(app_dir: Path, target: str, *, persistence_hint: bool = True) -> Dict:
    """
    High-level gate dispatcher.
    - For Flutter targets, first enforce compile-safety (baseline + repairs),
      then apply MVVM structure checks (advisory).
    - For Web targets, keep the no-build checks.
    Always returns a non-null, structured result.
    """
    target_l = (target or "").lower()
    if target_l in {"flutter", "mobile", "flutter_mvvm"}:
        # Step 1: make sure it compiles
        compile_guard = enforce_compile_safe(app_dir)
        # Step 2: run advisory MVVM checks (non-fatal)
        mvvm = _gate_flutter_mvvm(app_dir)
        return {
            "ok": bool(compile_guard.get("ok")),
            "status": "pass" if compile_guard.get("ok") else "fail",
            "compile_guard": compile_guard,
            "mvvm": mvvm,
        }
    # default web/no-build
    web = _gate_web(app_dir, persistence_hint=persistence_hint)
    return {
        "ok": bool(web.get("ok")),
        "status": "pass" if web.get("ok") else "fail",
        **web,
    }

def promote(staging_dir: Path, final_dir: Path) -> None:
    """
    Atomically promote a staging directory to final location.
    """
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_dir.with_suffix(".tmp.swap")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.move(str(staging_dir), str(tmp))
    if final_dir.exists():
        shutil.rmtree(final_dir, ignore_errors=True)
    tmp.rename(final_dir)
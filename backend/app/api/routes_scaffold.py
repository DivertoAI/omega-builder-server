from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/scaffold", tags=["scaffold"])

ROOT = Path("/workspace").resolve()

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def _maybe_rm(p: Path) -> None:
    if p.exists():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

@router.post("/monorepo")
def scaffold_monorepo(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Create a light monorepo layout with apps/, services/, design/, infra/, adapters/.
    Accepts:
      {
        "project": "insta_pharma",
        "clean_if_exists": true,
        "apps": [{"name":"customer","org":"com.omega.insta","description":"Customer app"}, ...]
      }
    """
    project = payload.get("project")
    if not project:
        raise HTTPException(status_code=400, detail="project required")
    clean = bool(payload.get("clean_if_exists"))
    apps: List[Dict[str, str]] = payload.get("apps") or []

    root = ROOT / project
    if clean:
        _maybe_rm(root)

    # Always ensure core dirs
    (root / "apps").mkdir(parents=True, exist_ok=True)
    (root / "services").mkdir(parents=True, exist_ok=True)
    (root / "design" / "fonts").mkdir(parents=True, exist_ok=True)
    (root / "design" / "tokens").mkdir(parents=True, exist_ok=True)
    (root / "design" / "theme").mkdir(parents=True, exist_ok=True)
    (root / "infra").mkdir(parents=True, exist_ok=True)
    (root / "adapters").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)

    # Design seeds (minimal, safe)
    _write(root / "design/tokens/tokens.dart", _TOKENS_DART)
    _write(root / "design/fonts/google_fonts.dart", _FONTS_DART)
    _write(root / "design/theme/omega_theme.dart", _THEME_DART)

    # Infra seeds
    _write(root / "infra/.env.example", _ENV_EXAMPLE)
    _write(root / "infra/docker-compose.yml", _DOCKER_COMPOSE)
    _write(root / "infra/ci-preview.yml", _CI_PREVIEW)

    # Adapter stubs
    _write(root / "adapters/payments_adapter.dart", _PAYMENTS_DART)
    _write(root / "adapters/ocr_adapter.dart", _OCR_DART)
    _write(root / "adapters/telemed_adapter.dart", _TELEMED_DART)
    _write(root / "adapters/logistics_adapter.dart", _LOGISTICS_DART)

    # Optional: create blank apps directories if listed (actual `flutter create` happens elsewhere)
    for a in apps:
        name = a.get("name")
        if name:
            (root / "apps" / name).mkdir(parents=True, exist_ok=True)

    return {
        "status": "ok",
        "project_dir": str(root),
        "notes": [
            "design tokens/fonts/theme written",
            "infra docker-compose/.env.example/ci-preview.yml written",
            "adapters stubs written",
        ],
    }


# ---------------- seeds ----------------

_TOKENS_DART = """// GENERATED: design/tokens/tokens.dart
class OmegaTokens {
  static const String fontFamily = 'Inter';

  static const spacing = _Spacing();
  static const radius  = _Radius();
  static const colors  = _Colors();
  static const elevation = _Elevation();
}

class _Spacing { const _Spacing();
  final double xs = 4, sm = 8, md = 12, lg = 16, xl = 24, xxl = 32;
}

class _Radius { const _Radius();
  final double sm = 6, md = 12, lg = 20, full = 999;
}

class _Colors { const _Colors();
  final int primary = 0xFF0052CC;
  final int secondary = 0xFF36B37E;
  final int background = 0xFFF7F8FA;
  final int surface = 0xFFFFFFFF;
  final int textPrimary = 0xFF0F172A;
  final int textSecondary = 0xFF475569;
  final int error = 0xFFEF4444;
  final int success = 0xFF22C55E;
}

class _Elevation { const _Elevation();
  final double level0 = 0, level1 = 1, level2 = 3, level3 = 6, level4 = 12;
}
"""

_FONTS_DART = """// GENERATED: design/fonts/google_fonts.dart
import 'package:flutter/material.dart';

/// Thin wrapper so apps can swap in google_fonts without hard dependency.
/// If you add `google_fonts` to pubspec, you can replace this implementation.
class OmegaFonts {
  static TextTheme textTheme([TextTheme? base]) {
    final b = base ?? ThemeData.light().textTheme;
    return b.apply(fontFamily: 'Inter');
  }

  static String primaryFamily() => 'Inter';
}
"""

_THEME_DART = """// GENERATED: design/theme/omega_theme.dart
import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import '../fonts/google_fonts.dart';

ThemeData buildOmegaTheme({Brightness brightness = Brightness.light}) {
  final isDark = brightness == Brightness.dark;
  final base = isDark ? ThemeData.dark() : ThemeData.light();

  final primary = Color(OmegaTokens.colors.primary);
  final secondary = Color(OmegaTokens.colors.secondary);
  final bg = Color(OmegaTokens.colors.background);
  final surface = Color(OmegaTokens.colors.surface);

  final txt = OmegaFonts.textTheme(base.textTheme);

  return base.copyWith(
    useMaterial3: true,
    colorScheme: ColorScheme(
      brightness: brightness,
      primary: primary,
      onPrimary: Colors.white,
      secondary: secondary,
      onSecondary: Colors.white,
      error: Color(OmegaTokens.colors.error),
      onError: Colors.white,
      background: bg,
      onBackground: Color(OmegaTokens.colors.textPrimary),
      surface: surface,
      onSurface: Color(OmegaTokens.colors.textPrimary),
    ),
    textTheme: txt,
    scaffoldBackgroundColor: bg,
    appBarTheme: AppBarTheme(
      backgroundColor: surface,
      foregroundColor: Color(OmegaTokens.colors.textPrimary),
      elevation: OmegaTokens.elevation.level1,
      centerTitle: true,
    ),
    cardTheme: CardTheme(
      elevation: OmegaTokens.elevation.level1,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(OmegaTokens.radius.lg),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(OmegaTokens.radius.md),
      ),
    ),
  );
}
"""

_ENV_EXAMPLE = """# infra/.env.example
# Copy to .env and fill values. App builds can read these via --dart-define
PAYMENT_PROVIDER=stripe
TELEMED_API_KEY=changeme
OCR_PROVIDER=google
LOGISTICS_PROVIDER=shippo
"""

_DOCKER_COMPOSE = """# infra/docker-compose.yml
version: "3.9"
services:
  preview:
    image: nginx:stable-alpine
    ports: ["8088:80"]
    volumes:
      - ../apps:/usr/share/nginx/html/apps:ro
      - ../preview:/usr/share/nginx/html/preview:ro
    restart: unless-stopped
"""

_CI_PREVIEW = """# infra/ci-preview.yml
name: Build Web Preview
on:
  workflow_dispatch:
  push:
    branches: [ main ]
jobs:
  build-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: subosito/flutter-action@v2
        with:
          channel: stable
      - name: Build each app for web
        run: |
          set -eux
          for app in $(ls -1 apps); do
            cd apps/$app
            flutter pub get
            flutter build web --release
            cd - >/dev/null
          done
      - name: Upload preview artifact
        uses: actions/upload-artifact@v4
        with:
          name: web-preview
          path: apps/*/build/web
"""

_PAYMENTS_DART = """// adapters/payments_adapter.dart
class PaymentsAdapter {
  static const _provider = String.fromEnvironment('PAYMENT_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> charge(int cents, {required String currency}) async {
    if (!enabled) return 'disabled';
    // TODO: wire real provider
    return 'ok:$_provider:$cents$currency';
  }
}
"""

_OCR_DART = """// adapters/ocr_adapter.dart
class OcrAdapter {
  static const _provider = String.fromEnvironment('OCR_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> extractText(List<int> imageBytes) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'ok:$_provider:len=${imageBytes.length}';
  }
}
"""

_TELEMED_DART = """// adapters/telemed_adapter.dart
class TelemedAdapter {
  static const _key = String.fromEnvironment('TELEMED_API_KEY', defaultValue: '');
  static bool get enabled => _key.isNotEmpty;

  Future<String> createVisit(String patientId) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'visit-created:$patientId';
  }
}
"""

_LOGISTICS_DART = """// adapters/logistics_adapter.dart
class LogisticsAdapter {
  static const _provider = String.fromEnvironment('LOGISTICS_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> quote({required double kg, required String toZip}) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'quote:$_provider:$kg:$toZip';
  }
}
"""
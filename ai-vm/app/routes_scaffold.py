# ai-vm/app/routes_scaffold.py
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/scaffold", tags=["scaffold"])

WORKSPACE_DIR = Path("/workspace")

class AppSpec(BaseModel):
    name: str = Field(..., description="App folder and package name (e.g. customer)")
    org: str = Field("com.omega", description="Org identifier used by Flutter create")
    description: str = Field("", description="App description")
    sdk: str = Field("stable", description="Flutter SDK channel/tag (informational)")

class MonorepoSpec(BaseModel):
    project: str = Field(..., description="Top-level project folder under /workspace (e.g. insta_pharma)")
    apps: List[AppSpec] = Field(default_factory=list)
    # You can extend later with services/design/infra templates
    clean_if_exists: bool = Field(False, description="If true, remove existing project folder first")

@dataclass
class CmdResult:
    ok: bool
    out: str

def _run(cmd: list[str], cwd: Optional[Path] = None) -> CmdResult:
    try:
        out = subprocess.check_output(
            cmd,
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace")
        return CmdResult(True, out)
    except subprocess.CalledProcessError as e:
        return CmdResult(False, e.output.decode("utf-8", "replace"))

def _ensure_base_placeholder(app_dir: Path) -> None:
    """Force web/index.html to use $FLUTTER_BASE_HREF so --base-href works."""
    web_index = app_dir / "web" / "index.html"
    if not web_index.is_file():
        return
    text = web_index.read_text(encoding="utf-8", errors="ignore")
    if 'href="$FLUTTER_BASE_HREF"' in text:
        return
    text = text.replace('<base href="/">', '<base href="$FLUTTER_BASE_HREF">')
    web_index.write_text(text, encoding="utf-8")

def _write_minimal_main(app_dir: Path, title: str) -> None:
    lib_main = app_dir / "lib" / "main.dart"
    lib_main.parent.mkdir(parents=True, exist_ok=True)
    lib_main.write_text(
        f"""import 'package:flutter/material.dart';

void main() => runApp(const _App());

class _App extends StatelessWidget {{
  const _App({{super.key}});
  @override
  Widget build(BuildContext context) {{
    return MaterialApp(
      title: '{title}',
      home: Scaffold(
        appBar: AppBar(title: const Text('{title}')),
        body: const Center(child: Text('Hello from {title}')),
      ),
    );
  }}
}}
""",
        encoding="utf-8",
    )

@router.post("/monorepo")
def scaffold_monorepo(spec: MonorepoSpec) -> Dict[str, Any]:
    root = WORKSPACE_DIR / spec.project

    if root.exists() and spec.clean_if_exists:
        shutil.rmtree(root)

    # Create folders
    for sub in ("apps", "services", "design", "infra"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    created: List[Dict[str, Any]] = []
    for app in spec.apps:
        app_dir = root / "apps" / app.name
        if not app_dir.exists():
            # flutter create
            res = _run(
                ["flutter", "create", "--platforms=web", "--org", app.org, app.name],
                cwd=root / "apps",
            )
            if not res.ok:
                raise HTTPException(status_code=500, detail=f"flutter create failed for {app.name}:\n{res.out}")
        else:
            res = CmdResult(True, "exists")

        # Ensure placeholder + minimal main
        _ensure_base_placeholder(app_dir)
        _write_minimal_main(app_dir, app.name.capitalize())

        created.append(
            {
                "app": app.name,
                "dir": str(app_dir),
                "flutter_create": res.out[:1000],  # trim
            }
        )

    return {
        "status": "ok",
        "project_dir": str(root),
        "apps": created,
        "notes": [
            "web/index.html patched to use $FLUTTER_BASE_HREF",
            "lib/main.dart replaced with a minimal screen",
        ],
    }
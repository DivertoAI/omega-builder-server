# ai-vm/app/routes_services.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess
import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/scaffold", tags=["scaffold"])

WORKSPACE_DIR = Path("/workspace")

class ServiceSpec(BaseModel):
    name: str = Field(..., description="package name, e.g. api_client")
    description: str = Field("", description="pubspec description")

class ServicesRequest(BaseModel):
    project: str = Field(..., description="monorepo root under /workspace, e.g. insta_pharma")
    services: List[ServiceSpec] = Field(default_factory=list)
    clean_if_exists: bool = False

@dataclass
class CmdResult:
    ok: bool
    out: str

def _run(cmd: list[str], cwd: Optional[Path] = None) -> CmdResult:
    try:
        out = subprocess.check_output(cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.STDOUT)\
                        .decode("utf-8", "replace")
        return CmdResult(True, out)
    except subprocess.CalledProcessError as e:
        return CmdResult(False, e.output.decode("utf-8", "replace"))

def _write_pubspec(dir_: Path, name: str, description: str) -> None:
    (dir_ / "pubspec.yaml").write_text(f"""name: {name}
description: {description or name}
version: 0.1.0
publish_to: "none"

environment:
  sdk: ">=3.3.0 <4.0.0"

dependencies:
  meta: ^1.11.0

dev_dependencies:
  lints: ^5.0.0
  test: ^1.25.0
""", encoding="utf-8")

def _write_lib_stub(dir_: Path, name: str) -> None:
    lib = dir_ / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / f"{name}.dart").write_text(f"""library {name};

/// Example: {name} entrypoint
String {name}Hello() => "Hello from {name}";
""", encoding="utf-8")

def _write_test_stub(dir_: Path, name: str) -> None:
    t = dir_ / "test"
    t.mkdir(parents=True, exist_ok=True)
    (t / f"{name}_test.dart").write_text(f"""import 'package:test/test.dart';
import 'package:{name}/{name}.dart';

void main() {{
  test('{name} says hello', () {{
    expect({name}Hello(), 'Hello from {name}');
  }});
}}
""", encoding="utf-8")

def _write_melos_yaml(root: Path, packages: List[str]) -> None:
    (root / "melos.yaml").write_text(f"""name: {root.name}
packages:
  - "apps/*"
  - "services/*"

command:
  bootstrap:
    usePubspecOverrides: true

ide:
  intellij: true
""", encoding="utf-8")

@router.post("/services")
def scaffold_services(req: ServicesRequest) -> Dict[str, Any]:
    root = WORKSPACE_DIR / req.project
    services_dir = root / "services"
    if not root.exists():
      raise HTTPException(status_code=400, detail=f"project dir not found: {root}")

    if services_dir.exists() and req.clean_if_exists:
        shutil.rmtree(services_dir)

    services_dir.mkdir(parents=True, exist_ok=True)

    created: list[dict[str, str]] = []
    pkg_names: list[str] = []

    for svc in req.services:
        pkg_dir = services_dir / svc.name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        _write_pubspec(pkg_dir, svc.name, svc.description)
        _write_lib_stub(pkg_dir, svc.name)
        _write_test_stub(pkg_dir, svc.name)
        created.append({"name": svc.name, "dir": str(pkg_dir)})
        pkg_names.append(svc.name)

    _write_melos_yaml(root, pkg_names)

    # Install melos in the repo (local dev); harmless if already globally installed in dev machines
    _run(["dart", "pub", "global", "activate", "melos"])  # ignore failure
    # Try bootstrap but do not fail the API if something minor goes wrong
    _run(["melos", "bootstrap"], cwd=root)

    return {
        "status": "ok",
        "project_dir": str(root),
        "services": created,
        "melos": str(root / "melos.yaml"),
        "notes": [
            "Created Dart packages under services/.",
            "Generated melos.yaml and attempted melos bootstrap.",
        ],
    }
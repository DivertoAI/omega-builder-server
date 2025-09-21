from __future__ import annotations

import json, urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .routes_build_preview import build_publish_matrix_impl

router = APIRouter(prefix="/api/scaffold", tags=["scaffold"])
AI_VM_URL = "http://ai-vm:8080"

class AppRef(BaseModel):
    name: str
    hello_text: Optional[str] = None

class WireReq(BaseModel):
    project: str
    apps: List[AppRef]
    run_bootstrap: bool = True
    build_and_publish: bool = True

def _load_text(p: Path) -> str: return p.read_text(encoding="utf-8")
def _save_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(s, encoding="utf-8")

def _http_json(method: str, url: str, payload: Dict[str, Any], timeout: int = 600) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _ensure_service_packages(project_dir: Path) -> Dict[str, Any]:
    """
    If services packages are missing (fresh volume), ask ai-vm to scaffold them.
    Handles both API shapes:
      1) {"project_dir": "...", "services":[...]}
      2) {"project": "name", "services":[...]}
    Then ensures a minimal lib exists so imports resolve.
    """
    services_root = project_dir / "services"
    wanted = ["api_client", "auth", "models"]
    missing = [n for n in wanted if not (services_root / n / "pubspec.yaml").exists()]

    created: Dict[str, str] = {}
    if missing:
        # try contract #1 (project_dir)
        payload1 = {"project_dir": str(project_dir),
                    "services": [{"name": n, "description": n} for n in missing]}
        try:
            _http_json("POST", f"{AI_VM_URL}/api/scaffold/services", payload1)
        except Exception:
            # fall back to contract #2 (project)
            payload2 = {"project": project_dir.name,
                        "services": [{"name": n, "description": n} for n in missing]}
            _http_json("POST", f"{AI_VM_URL}/api/scaffold/services", payload2)

    # ensure tiny libs (idempotent) so imports compile even before you add real code
    lib_files = {
        "api_client": ("api_client.dart", 'library api_client;\n\nString api_clientHello() => "Hello from api_client";\n'),
        "auth": ("auth.dart", 'library auth;\n\nString authHello() => "Hello from auth";\n'),
        "models": ("models.dart", "library models;\n\nclass EmptyModel {}\n"),
    }
    for name, (fname, contents) in lib_files.items():
        p = services_root / name / "lib" / fname
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contents, encoding="utf-8")
            created[name] = str(p)

    return {"scaffolded": missing, "libs_created": created}

def _ensure_path_deps(pubspec_text: str, entries: Dict[str, str]) -> str:
    if "dependencies:" not in pubspec_text:
        pubspec_text = pubspec_text.rstrip() + "\n\ndependencies:\n"
    lines = pubspec_text.splitlines()
    out: List[str] = []; i = 0; in_deps = False; deps_lines: List[str] = []
    while i < len(lines):
        line = lines[i]
        if not in_deps and line.startswith("dependencies:"):
            in_deps = True; out.append("dependencies:"); i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                deps_lines.append(lines[i]); i += 1
            existing = {}; j = 0
            while j < len(deps_lines):
                l = deps_lines[j]
                if l.startswith("  ") and l.strip().endswith(":"):
                    name = l.strip()[:-1]; block = [l]; k = j + 1
                    while k < len(deps_lines) and deps_lines[k].startswith("  "):
                        block.append(deps_lines[k]); k += 1
                    existing[name] = block; j = k
                else:
                    # keep loose lines like "  cupertino_icons: ^1.0.6"
                    if l.startswith("  ") and ":" in l and not l.strip().endswith(":"):
                        key = l.strip().split(":")[0]
                        existing.setdefault(key, []).extend([l])
                    j += 1
            for name, rel in entries.items():
                existing[name] = [f"  {name}:", f"    path: {rel}"]
            for name in sorted(existing.keys()):
                out.extend(existing[name])
            continue
        else:
            out.append(line); i += 1
    if not any(l.startswith("dependencies:") for l in out):
        out.append("dependencies:")
        for name, rel in sorted(entries.items()):
            out.append(f"  {name}:"); out.append(f"    path: {rel}")
    if not out[-1].endswith("\n"): out[-1] += "\n"
    return "\n".join(out)

def _write_main_dart(app_dir: Path, app_title: str, hello_text: str) -> None:
    lib = app_dir / "lib" / "main.dart"
    # NOTE: correct Dart interpolation; this CALLS api_clientHello()
    template = f"""// GENERATED BY wire-services
import 'package:flutter/material.dart';
import 'package:api_client/api_client.dart';
import 'package:auth/auth.dart';
import 'package:models/models.dart';

String omegaHello() => "${{api_clientHello()}} from {app_title}";

void main() => runApp(const _App());

class _App extends StatelessWidget {{
  const _App({{super.key}});
  @override
  Widget build(BuildContext context) {{
    return MaterialApp(
      title: '{app_title}',
      home: Scaffold(
        appBar: AppBar(title: const Text('{app_title}')),
        body: Center(child: Text(omegaHello())),
      ),
    );
  }}
}}
"""
    _save_text(lib, template)

@router.post("/wire-services")
def wire_services(req: WireReq) -> Dict[str, Any]:
    proj = Path("/workspace") / req.project
    if not proj.is_dir():
        raise HTTPException(status_code=400, detail=f"project not found: {proj}")

    svc_info = _ensure_service_packages(proj)

    patched: List[Dict[str, Any]] = []
    matrix: List[Dict[str, str]] = []
    for a in req.apps:
        app_dir = proj / "apps" / a.name
        pubspec = app_dir / "pubspec.yaml"
        if not pubspec.is_file():
            raise HTTPException(status_code=400, detail=f"missing pubspec: {pubspec}")

        text = _load_text(pubspec)
        text = _ensure_path_deps(text, {
            "models": "../../services/models",
            "api_client": "../../services/api_client",
            "auth": "../../services/auth",
        })
        _save_text(pubspec, text)

        app_title = a.name.capitalize()
        hello_text = a.hello_text or f"Hello from {a.name} + services"
        _write_main_dart(app_dir, app_title, hello_text)

        patched.append({"app": a.name, "pubspec": str(pubspec), "main": str(app_dir / "lib" / "main.dart")})
        matrix.append({"app_path": str(app_dir), "app_name": a.name})

    result: Dict[str, Any] = {"status": "ok", "project": req.project, "services": svc_info, "patched": patched}

    if req.run_bootstrap:
        result["melos"] = _http_json("POST", f"{AI_VM_URL}/api/melos/bootstrap", {"project_dir": str(proj)})

    if req.build_and_publish:
        result["preview"] = build_publish_matrix_impl(req.project, matrix)

    return result
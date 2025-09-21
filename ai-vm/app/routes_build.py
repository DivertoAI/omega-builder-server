# ai-vm/app/routes_build.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/build", tags=["build"])

class BuildWebRequest(BaseModel):
    app_path: str                   # e.g., /workspace/staging
    base_href: Optional[str] = None # e.g., /preview/insta_pharma/customer/
    release: bool = False
    wasm_dry_run: bool = True
    pwa_strategy: str = "none"      # none in dev to avoid SW cache headaches

class BuildWebResponse(BaseModel):
    status: str
    app_path: str
    build_dir: str
    log: str

def _run(cmd: list[str], cwd: Optional[Path] = None) -> str:
    try:
        out = subprocess.check_output(
            cmd,
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.STDOUT,
        )
        return out.decode("utf-8", "replace")
    except subprocess.CalledProcessError as e:
        # Surface full Flutter output for debugging
        msg = e.output.decode("utf-8", "replace")
        raise HTTPException(status_code=500, detail=msg)

@router.post("/web", response_model=BuildWebResponse)
def build_web(req: BuildWebRequest) -> BuildWebResponse:
    app_dir = Path(req.app_path)
    if not app_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"app_path not found: {app_dir}")

    # 1) dependencies
    pub_log = _run(["flutter", "pub", "get"], cwd=app_dir)

    # 2) build web with proper base href and no SW in dev
    cmd = ["flutter", "build", "web", f"--pwa-strategy={req.pwa_strategy}"]
    if req.base_href:
        # Ensure it ends with trailing slash to satisfy Flutter expectations
        base = req.base_href if req.base_href.endswith("/") else req.base_href + "/"
        cmd += ["--base-href", base]
    if req.release:
        cmd += ["--release"]
    if not req.wasm_dry_run:
        cmd += ["--no-wasm-dry-run"]

    build_log = _run(cmd, cwd=app_dir)

    return BuildWebResponse(
        status="ok",
        app_path=str(app_dir),
        build_dir=str(app_dir / "build" / "web"),
        log=pub_log + "\n" + build_log,
    )
# ai-vm/app/routes_melos.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/melos", tags=["melos"])

class MelosBootstrapReq(BaseModel):
    project_dir: str  # e.g. /workspace/insta_pharma

def _run(cmd: list[str], cwd: str | None = None, env: Dict[str, str] | None = None) -> tuple[int, str]:
    p = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = p.communicate()
    return p.returncode, out or ""

@router.post("/bootstrap")
def melos_bootstrap(req: MelosBootstrapReq) -> Dict[str, Any]:
    proj = Path(req.project_dir).resolve()
    if not proj.is_dir():
        raise HTTPException(status_code=400, detail=f"project_dir not found: {proj}")

    env = os.environ.copy()
    # Ensure Dart/Flutter + pub cache are on PATH in the container
    pub_bin = Path.home() / ".pub-cache" / "bin"
    env["PATH"] = f"{pub_bin}:{env.get('PATH','')}"
    env.setdefault("CI", "true")

    # 1) activate melos
    rc1, log1 = _run(["dart", "pub", "global", "activate", "melos"], cwd=str(proj), env=env)
    if rc1 != 0:
        raise HTTPException(status_code=500, detail=f"melos activate failed:\n{log1}")

    # 2) melos bootstrap
    rc2, log2 = _run(["bash", "-lc", "export PATH=\"$HOME/.pub-cache/bin:$PATH\"; melos bootstrap -v"], cwd=str(proj), env=env)
    if rc2 != 0:
        raise HTTPException(status_code=500, detail=f"melos bootstrap failed:\n{log2}")

    return {"status": "ok", "activated": log1, "bootstrap": log2}
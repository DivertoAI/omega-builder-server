# backend/app/api/routes_build_preview.py
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Tuple, List

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/preview", tags=["preview"])

AI_VM_URL = os.environ.get("AI_VM_URL", "http://ai-vm:8080")
DEST_ROOT = Path(os.environ.get("OMEGA_PREVIEW_ROOT", "/preview"))

# ---- Optional metrics (no-op if module missing) ------------------------------------
try:
    from backend.app.core.metrics import (
        build_counter,
        publish_counter,
        build_duration,
        publish_duration,
    )
    def _metric_build(result: str, app: str, dur: float):
        build_counter.inc(labels={"result": result, "app": app})
        build_duration.observe(dur, labels={"result": result, "app": app})

    def _metric_publish(app: str, dur: float, result: str = "success"):
        publish_counter.inc(labels={"result": result, "app": app})
        publish_duration.observe(dur, labels={"app": app})
except Exception:  # pragma: no cover
    def _metric_build(result: str, app: str, dur: float):  # no-op
        pass
    def _metric_publish(app: str, dur: float, result: str = "success"):  # no-op
        pass
# ------------------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        out = subprocess.check_output(
            cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.STDOUT
        )
        return out.decode("utf-8", "replace")
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=e.output.decode("utf-8", "replace"),
        )


def _http_json(url: str, payload: Dict[str, Any], timeout: int = 900) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Surface ai-vm body for easier debugging
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        # Keep the original status code but include ai-vm payload in detail
        raise HTTPException(status_code=e.code, detail=f"Internal Server Error\n{body}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Upstream ai-vm unreachable: {e}")


def _patch_index_html(index_path: Path, base_href: str) -> Tuple[bool, str]:
    """
    Post-publish patcher:
      - Normalize <base href="..."> to the correct subpath (ensures trailing '/')
      - Strip service worker registration for dev-friendliness
    """
    if not index_path.is_file():
        return False, f"index.html not found at {index_path}"

    original = index_path.read_text(encoding="utf-8", errors="ignore")
    content = original

    if not base_href.endswith("/"):
        base_href += "/"

    # 1) Normalize any base tag (covers '/', '$FLUTTER_BASE_HREF', or custom)
    content, base_count = re.subn(
        r'<base\s+href=["\'].*?["\']\s*/?>',
        f'<base href="{base_href}">',
        content,
        flags=re.IGNORECASE,
    )

    # 2) If missing, inject after </title>
    if base_count == 0:
        content = re.sub(
            r"(</title\s*>\s*)",
            rf'</title>\n    <base href="{base_href}">\n',
            content,
            count=1,
            flags=re.IGNORECASE,
        )

    # 3) Remove common SW registration snippets
    content = re.sub(
        r"""navigator\.serviceWorker\.register\([^)]*\);\s*""",
        "",
        content,
        flags=re.MULTILINE,
    )

    changed = content != original
    if changed:
        index_path.write_text(content, encoding="utf-8")

    return changed, f"patched base_href to {base_href}; removed SW registration if present"


def _publish(build_dir: Path, project: str, app_name: str, base_href: str) -> Dict[str, Any]:
    """
    Rsync the built web bundle into /preview/<project>/<app_name> and patch index.html.
    Returns a publish dict with destination and patch info.
    """
    dest_dir = DEST_ROOT / project / app_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    _run(["rsync", "-a", f"{build_dir}/", f"{dest_dir}/"])
    changed, patch_msg = _patch_index_html(dest_dir / "index.html", base_href)
    return {
        "dest": str(dest_dir),
        "patched": changed,
        "patch_msg": patch_msg,
    }


@router.post("/build")
def build_and_publish(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    """
    Build a Flutter web app via ai-vm, then publish to /preview/<project>/<app_name>.
    Robust to base-href placeholder issues (falls back to build without base_href).
    """
    app_path = payload.get("app_path")
    project = payload.get("project")
    app_name = payload.get("app_name")

    if not (app_path and project and app_name):
        raise HTTPException(status_code=400, detail="Provide app_path, project, app_name")

    base_href = f"/preview/{project}/{app_name}/"

    # --- Build (with metrics) ---
    t0 = time.perf_counter()
    build = None
    build_err = None

    # Try with base_href first (best case)
    try:
        build = _http_json(
            f"{AI_VM_URL}/api/build/web",
            {
                "app_path": app_path,
                "base_href": base_href,
                "release": False,
                "wasm_dry_run": True,
                "pwa_strategy": "none",
            },
        )
    except HTTPException as e:
        build_err = str(e.detail)

    # Fallback: no base_href
    if build is None:
        try:
            build = _http_json(
                f"{AI_VM_URL}/api/build/web",
                {
                    "app_path": app_path,
                    "release": False,
                    "wasm_dry_run": True,
                    "pwa_strategy": "none",
                },
            )
        except HTTPException as e2:
            dt = time.perf_counter() - t0
            _metric_build("fail", app_name, dt)
            raise HTTPException(
                status_code=502,
                detail=f"ai-vm build failed (with base_href -> {build_err!r}; without base_href -> {e2.detail!r})",
            )

    # Validate build_dir
    build_dir = Path(build.get("build_dir", ""))
    if not build_dir.is_dir():
        dt = time.perf_counter() - t0
        _metric_build("fail", app_name, dt)
        raise HTTPException(status_code=500, detail=f"build_dir missing: {build_dir}")

    # Record successful build metrics
    _metric_build("success", app_name, time.perf_counter() - t0)

    # --- Publish (with metrics) ---
    tp = time.perf_counter()
    publish_info = _publish(build_dir, project, app_name, base_href)
    _metric_publish(app_name, time.perf_counter() - tp, result="success")

    return JSONResponse(
        {
            "status": "ok",
            "build": build,
            "publish": publish_info,
            "preview_url": f"{base_href}index.html",
        }
    )


def build_publish_matrix_impl(project: str, matrix: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Build/publish multiple apps in one go.
    matrix items: {"app_path": "...", "app_name": "..."}
    """
    results: List[Dict[str, Any]] = []

    for item in matrix:
        app_path = item["app_path"]
        app_name = item["app_name"]
        base_href = f"/preview/{project}/{app_name}/"

        t0 = time.perf_counter()
        build = None
        build_err = None

        # Try with base_href first
        try:
            build = _http_json(
                f"{AI_VM_URL}/api/build/web",
                {
                    "app_path": app_path,
                    "base_href": base_href,
                    "release": False,
                    "wasm_dry_run": True,
                    "pwa_strategy": "none",
                },
            )
        except HTTPException as e:
            build_err = str(e.detail)

        # Fallback
        if build is None:
            try:
                build = _http_json(
                    f"{AI_VM_URL}/api/build/web",
                    {
                        "app_path": app_path,
                        "release": False,
                        "wasm_dry_run": True,
                        "pwa_strategy": "none",
                    },
                )
            except HTTPException as e2:
                dt = time.perf_counter() - t0
                _metric_build("fail", app_name, dt)
                raise HTTPException(
                    status_code=502,
                    detail=f"ai-vm build failed for {app_name} "
                           f"(with base_href -> {build_err!r}; without base_href -> {e2.detail!r})",
                )

        build_dir = Path(build.get("build_dir", ""))
        if not build_dir.is_dir():
            dt = time.perf_counter() - t0
            _metric_build("fail", app_name, dt)
            raise HTTPException(status_code=500, detail=f"build_dir missing: {build_dir}")

        _metric_build("success", app_name, time.perf_counter() - t0)

        # Publish
        tp = time.perf_counter()
        publish_info = _publish(build_dir, project, app_name, base_href)
        _metric_publish(app_name, time.perf_counter() - tp, result="success")

        results.append(
            {
                "app_path": app_path,
                "project": project,
                "app_name": app_name,
                "preview_url": f"{base_href}index.html",
                "publish_dest": publish_info["dest"],
                "patched": publish_info["patched"],
                "patch_msg": publish_info["patch_msg"],
                "build": build,
            }
        )

    return {"status": "ok", "project": project, "results": results}


@router.post("/build-matrix")
def build_and_publish_matrix(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    """
    HTTP wrapper for matrix builds:
    {
      "project": "insta_pharma",
      "matrix": [
        {"app_path": "/workspace/insta_pharma/apps/customer", "app_name": "customer"},
        {"app_path": "/workspace/insta_pharma/apps/pharmacist", "app_name": "pharmacist"}
      ]
    }
    """
    project = payload.get("project")
    matrix = payload.get("matrix")
    if not project or not isinstance(matrix, list) or not matrix:
        raise HTTPException(status_code=400, detail="Provide project and non-empty matrix list")

    result = build_publish_matrix_impl(project, matrix)
    return JSONResponse(result)
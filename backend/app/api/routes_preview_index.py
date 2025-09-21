# backend/app/api/routes_preview_index.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List, Tuple

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, HTMLResponse

router = APIRouter(prefix="/api/preview", tags=["preview"])

DEST_ROOT = Path(os.environ.get("OMEGA_PREVIEW_ROOT", "/preview"))

def _scan() -> List[Dict[str, str]]:
    """
    Walk /preview/<project>/<app>/index.html and return entries:
      { "project": ..., "app": ..., "url": "/preview/<project>/<app>/" }
    """
    items: List[Dict[str, str]] = []
    if not DEST_ROOT.is_dir():
        return items
    for project_dir in sorted(p for p in DEST_ROOT.iterdir() if p.is_dir()):
        for app_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            index_html = app_dir / "index.html"
            if index_html.is_file():
                rel = f"/preview/{project_dir.name}/{app_dir.name}/"
                items.append({"project": project_dir.name, "app": app_dir.name, "url": rel})
    return items

def _render_html(items: List[Dict[str, str]], title: str = "Omega – Previews") -> str:
    lines = [
        "<!doctype html>",
        '<meta charset="utf-8">',
        f"<title>{title}</title>",
        "<style>body{font:16px system-ui;margin:2rem} a{display:block;margin:.5rem 0}</style>",
        f"<h1>{title}</h1>",
    ]
    # group by project
    by_proj: Dict[str, List[Dict[str, str]]] = {}
    for it in items:
        by_proj.setdefault(it["project"], []).append(it)
    for proj, apps in sorted(by_proj.items()):
        lines.append(f"<h2>{proj}</h2>")
        for it in apps:
            lines.append(f'<a href="{it["url"]}">{it["app"]}</a>')
    return "\n".join(lines)

@router.get("/index")
def list_previews() -> JSONResponse:
    """Return JSON list of all previews."""
    return JSONResponse({"status": "ok", "items": _scan()})

@router.post("/hub-regen")
def regenerate_hub(
    project: str | None = Query(default=None, description="If provided, write /preview/<project>/index.html; else write /preview/index.html")
) -> JSONResponse:
    items = _scan()
    if project:
        # filter items for this project only
        items = [i for i in items if i["project"] == project]
        hub_path = DEST_ROOT / project / "index.html"
        hub_path.parent.mkdir(parents=True, exist_ok=True)
        hub_path.write_text(_render_html(items, f"{project} – Previews"), encoding="utf-8")
        return JSONResponse({"status": "ok", "wrote": str(hub_path), "count": len(items)})
    else:
        hub_path = DEST_ROOT / "index.html"
        hub_path.write_text(_render_html(items), encoding="utf-8")
        return JSONResponse({"status": "ok", "wrote": str(hub_path), "count": len(items)})

@router.get("/hub", response_class=HTMLResponse)
def view_hub() -> HTMLResponse:
    """HTML view (no write), useful for quick access."""
    return HTMLResponse(_render_html(_scan()))
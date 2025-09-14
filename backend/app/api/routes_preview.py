# backend/app/api/routes_preview.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["preview"])

# Shared with routes_appetize
APPETIZE_STATE = Path("/workspace/.omega/appetize.json")
APPETIZE_STATE.parent.mkdir(parents=True, exist_ok=True)


def _load_public_key() -> Optional[str]:
    try:
        if APPETIZE_STATE.is_file():
            data = json.loads(APPETIZE_STATE.read_text(encoding="utf-8"))
            pk = data.get("publicKey")
            if isinstance(pk, str) and pk.strip():
                return pk.strip()
    except Exception:
        pass
    return None


@router.get("/api/preview/key")
def get_public_key() -> JSONResponse:
    """
    Return the saved Appetize publicKey (if any).
    Response: {"publicKey":"..."} or 404 if missing.
    """
    pk = _load_public_key()
    if not pk:
        raise HTTPException(status_code=404, detail="No Appetize publicKey found. Upload an APK first.")
    return JSONResponse({"publicKey": pk})


@router.get("/preview", response_class=HTMLResponse)
def preview_page() -> HTMLResponse:
    """
    Simple HTML page that embeds the Appetize device if a key exists,
    otherwise shows instructions.
    """
    pk = _load_public_key()
    if not pk:
        html = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Preview – Omega Builder</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:2rem;}</style>
  </head>
  <body>
    <h1>Interactive Preview</h1>
    <p>No Appetize app found yet.</p>
    <ol>
      <li>Build an APK in the <code>ai-vm</code> container.</li>
      <li>Upload it: <code>POST /api/preview/upload</code>.</li>
      <li>Reload this page.</li>
    </ol>
    <pre>curl -sS http://localhost:8000/api/preview/upload -H 'content-type: application/json' -d '{"apk_path":"/workspace/staging/build/app/outputs/flutter-apk/app-debug.apk"}'</pre>
  </body>
</html>
"""
        return HTMLResponse(html)

    iframe_src = f"https://appetize.io/embed/{pk}"
    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Preview – Omega Builder</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      html,body{{height:100%;margin:0}}
      body{{display:flex;flex-direction:column;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
      header{{padding:12px 16px;border-bottom:1px solid #eee;display:flex;align-items:center;gap:12px}}
      main{{flex:1;display:flex;align-items:center;justify-content:center;background:#f7f7f7}}
      iframe{{border:0; width:378px; height:800px; box-shadow:0 10px 30px rgba(0,0,0,.15); border-radius:12px; background:#000}}
      .meta{{color:#666;font-size:14px}}
    </style>
  </head>
  <body>
    <header>
      <strong>Omega Builder – Interactive Preview</strong>
      <span class="meta">Appetize publicKey: {pk}</span>
    </header>
    <main>
      <iframe id="appetize" src="{iframe_src}" allow="clipboard-read; clipboard-write"></iframe>
    </main>
  </body>
</html>
"""
    return HTMLResponse(html)
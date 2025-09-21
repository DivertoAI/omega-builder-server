# backend/app/api/routes_preview.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["preview"])

# Persistent state for Appetize publicKey
APPETIZE_STATE = Path("/workspace/.omega/appetize.json")
APPETIZE_STATE.parent.mkdir(parents=True, exist_ok=True)

# Root where Flutter web builds are published (see publish_preview.sh)
PREVIEW_ROOT = Path((Path("/preview")).as_posix())


# -----------------------------
# Helpers
# -----------------------------
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


def _save_public_key(pk: str) -> None:
    data: Dict[str, str] = {"publicKey": pk}
    APPETIZE_STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_public_key() -> None:
    if APPETIZE_STATE.exists():
        try:
            APPETIZE_STATE.unlink()
        except Exception:
            # fall back to empty file
            APPETIZE_STATE.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")


def _list_web_previews() -> List[str]:
    """
    Return list of relative directories under PREVIEW_ROOT that contain an index.html.
    e.g. ["insta_pharma/customer", "my_proj/admin"]
    """
    out: List[str] = []
    if not PREVIEW_ROOT.exists():
        return out
    for dirpath in PREVIEW_ROOT.rglob("*"):
        if dirpath.is_dir():
            index = dirpath / "index.html"
            if index.exists():
                rel = dirpath.relative_to(PREVIEW_ROOT).as_posix()
                out.append(rel)
    # Sort stable by project/app path
    out.sort()
    return out


# -----------------------------
# API: Appetize key management
# -----------------------------
@router.get("/api/preview/key")
def get_public_key() -> JSONResponse:
    """
    Return the saved Appetize publicKey (if any).
    Response: {"publicKey":"..."} or 404 if missing.
    """
    pk = _load_public_key()
    if not pk:
        raise HTTPException(status_code=404, detail="No Appetize publicKey found. Upload or set a key first.")
    return JSONResponse({"publicKey": pk})


@router.post("/api/preview/key")
def set_public_key(payload: Dict[str, str] = Body(...)) -> JSONResponse:
    """
    Set/replace the Appetize publicKey explicitly.
    Body: {"publicKey":"xyz..."}
    """
    pk = (payload or {}).get("publicKey")
    if not pk or not isinstance(pk, str) or not pk.strip():
        raise HTTPException(status_code=400, detail="Provide a non-empty 'publicKey'.")
    _save_public_key(pk.strip())
    return JSONResponse({"status": "ok", "publicKey": pk.strip()})


@router.delete("/api/preview/key")
def delete_public_key() -> JSONResponse:
    """
    Clear the stored Appetize publicKey.
    """
    _clear_public_key()
    return JSONResponse({"status": "ok"})


# -----------------------------
# API: Web previews listing
# -----------------------------
@router.get("/api/preview/web")
def list_web_previews() -> JSONResponse:
    """
    List relative web preview paths that contain an index.html
    Example: ["insta_pharma/customer", "insta_pharma/admin"]
    """
    items = _list_web_previews()
    return JSONResponse({"items": items})


# -----------------------------
# (Optional) Upload stub
# -----------------------------
@router.post("/api/preview/upload")
def upload_apk_stub(payload: Dict[str, str] = Body(...)) -> JSONResponse:
    """
    Stub endpoint for APK upload → Appetize.
    Preferred path: upload externally to Appetize, then call /api/preview/key with the publicKey.

    Accepts either:
      - {"publicKey":"..."} -> saves immediately and returns ok.
      - {"apk_path":"/workspace/staging/.../app-debug.apk"} -> returns 501 with guidance.

    This avoids external API calls from the backend container.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid body.")

    pk = payload.get("publicKey")
    if isinstance(pk, str) and pk.strip():
        _save_public_key(pk.strip())
        return JSONResponse({"status": "ok", "publicKey": pk.strip(), "note": "publicKey saved"})

    if payload.get("apk_path"):
        # Not implementing remote upload here to keep the service offline-friendly.
        raise HTTPException(
            status_code=501,
            detail=(
                "Direct APK upload not implemented here. "
                "Upload the APK to Appetize using their CLI/UI, then POST /api/preview/key with the publicKey."
            ),
        )

    raise HTTPException(status_code=400, detail="Provide either 'publicKey' or 'apk_path'.")


# -----------------------------
# HTML: Combined Preview Page
# -----------------------------
@router.get("/preview", response_class=HTMLResponse)
def preview_page() -> HTMLResponse:
    """
    Renders a page that:
      - Embeds the Appetize device if a publicKey exists.
      - Lists available Flutter Web previews found in /preview/<project>/<app>.
        (Assumes your web server / nginx serves files from /preview.)
    """
    pk = _load_public_key()
    items = _list_web_previews()

    # Build items list HTML
    if items:
        links_html = "\n".join(
            f'<li><a href="/preview/{p}/index.html" target="_blank" rel="noopener noreferrer">{p}</a></li>'
            for p in items
        )
        web_section = f"""
        <section>
          <h2>Flutter Web Previews</h2>
          <ul>{links_html}</ul>
          <p class="meta">These links expect your server (or nginx) to serve static files from <code>/preview</code>.</p>
        </section>
        """
    else:
        web_section = """
        <section>
          <h2>Flutter Web Previews</h2>
          <p>No web builds found under <code>/preview</code>.</p>
          <ol>
            <li>Build inside ai-vm: <code>flutter build web</code></li>
            <li>Publish: <code>ai-vm/scripts/publish_preview.sh /workspace/staging/apps/customer/build/web insta_pharma customer</code></li>
            <li>Reload this page.</li>
          </ol>
        </section>
        """

    # Appetize embed or instructions
    if not pk:
        appetize_section = """
        <section>
          <h2>Interactive Appetize Preview</h2>
          <p>No Appetize app found yet.</p>
          <ol>
            <li>Upload your APK to Appetize via UI/CLI and copy the <code>publicKey</code>.</li>
            <li>Save the key:
              <pre>curl -sS http://localhost:8000/api/preview/key \\
  -H 'content-type: application/json' \\
  -d '{"publicKey":"YOUR_PUBLIC_KEY"}' | jq</pre>
            </li>
            <li>Reload this page.</li>
          </ol>
          <p class="meta">Optional (unsupported stub): POST <code>/api/preview/upload</code> with <code>{"apk_path": "..."}</code> returns 501 with guidance.</p>
        </section>
        """
    else:
        iframe_src = f"https://appetize.io/embed/{pk}"
        appetize_section = f"""
        <section>
          <h2>Interactive Appetize Preview</h2>
          <div class="device">
            <iframe id="appetize" src="{iframe_src}" allow="clipboard-read; clipboard-write"></iframe>
          </div>
          <p class="meta">publicKey: {pk}</p>
        </section>
        """

    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Preview – Omega Builder</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root {{
        --bg:#f7f7f7; --card:#fff; --text:#111; --muted:#666; --border:#e5e5e5;
      }}
      * {{ box-sizing: border-box; }}
      html,body{{height:100%;margin:0}}
      body{{display:flex;min-height:100%;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
      aside{{width:320px;background:var(--card);border-right:1px solid var(--border);padding:16px;position:sticky;top:0;height:100vh;overflow:auto}}
      main{{flex:1;display:block;padding:24px}}
      header{{padding:12px 16px;border-bottom:1px solid var(--border);background:var(--card);position:sticky;top:0;z-index:10}}
      h1{{margin:0;font-size:18px}}
      h2{{margin:24px 0 12px 0;font-size:16px}}
      ul{{margin:0;padding-left:18px}}
      code,pre{{background:#000;color:#0f0;padding:8px;border-radius:8px;display:block;overflow:auto}}
      .meta{{color:var(--muted);font-size:13px}}
      .device iframe{{border:0;width:378px;height:800px;box-shadow:0 10px 30px rgba(0,0,0,.15);border-radius:12px;background:#000}}
      .card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}}
      a{{color:#2563eb;text-decoration:none}}
      a:hover{{text-decoration:underline}}
    </style>
  </head>
  <body>
    <aside>
      <h2>Web Preview Index</h2>
      {(lambda items=items: "<ul>" + "".join(f'<li><a href="/preview/{p}/index.html" target="_blank">{p}</a></li>' for p in items) + "</ul>" if items else "<p class='meta'>No web builds yet.</p>")()}
      <div class="card">
        <strong>API</strong>
        <pre>GET  /api/preview/web
GET  /api/preview/key
POST /api/preview/key
DEL  /api/preview/key</pre>
      </div>
    </aside>
    <main>
      <header><h1>Omega Builder — Preview</h1></header>
      {appetize_section}
      {web_section}
    </main>
  </body>
</html>
"""
    return HTMLResponse(html)
# backend/app/api/routes_appetize.py
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/preview", tags=["preview", "appetize"])

APPETIZE_STATE = Path("/workspace/.omega/appetize.json")
APPETIZE_STATE.parent.mkdir(parents=True, exist_ok=True)

API_URL = "https://api.appetize.io/v2/apps"  # v2 upload
TOKEN_ENV = "APPETIZE_API_TOKEN"


def _save_key(public_key: str) -> None:
    data = {"publicKey": public_key}
    APPETIZE_STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@router.post("/upload")
def upload_to_appetize(
    apk_path: Optional[str] = Body(None),
    platform: str = Body("android"),
    note: Optional[str] = Body("omega-builder upload"),
):
    """
    Uploads an APK to Appetize and persists the publicKey for embedding.
    Body:
      { "apk_path": "/workspace/staging/build/app/outputs/flutter-apk/app-debug.apk" }
    If apk_path is omitted, we pick the newest *.apk under the standard Flutter output dir.
    Requires env APPETIZE_API_TOKEN.
    """
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise HTTPException(status_code=400, detail=f"Missing {TOKEN_ENV} in environment.")

    if not apk_path:
        candidates = sorted(glob.glob("/workspace/staging/build/app/outputs/flutter-apk/*.apk"))
        if not candidates:
            raise HTTPException(status_code=400, detail="No APK found; provide apk_path or build one.")
        apk_path = candidates[-1]

    if not os.path.isfile(apk_path):
        raise HTTPException(status_code=400, detail=f"apk_path not found: {apk_path}")

    files = {"file": open(apk_path, "rb")}
    data = {
        "platform": platform,
        # Optional metadata; adjust as needed:
        "note": note or "omega-builder upload",
        "visibility": "public",
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.post(API_URL, headers=headers, files=files, data=data, timeout=300)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upload failed: {e}")

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"Appetize error: {resp.text}")

    payload = resp.json()
    # v2 returns { "publicKey": "...", ... } or under "data" depending on plan
    public_key = payload.get("publicKey") or (payload.get("data") or {}).get("publicKey")
    if not public_key:
        raise HTTPException(status_code=502, detail=f"Upload ok but publicKey missing: {payload}")

    _save_key(public_key)
    return {"ok": True, "publicKey": public_key}
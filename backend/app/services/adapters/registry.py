# backend/app/services/adapters/registry.py
from __future__ import annotations
from typing import List
import os

SUPPORTED = [
    "ocr",
    "telemed",
    "payments",
    "logistics",
    "firebase",
    "bluetooth",
]

ENV_KEYS = {
    "ocr": ["OCR_API_KEY"],
    "telemed": ["TELEMED_BASE_URL"],
    "payments": ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"],
    "logistics": ["LOGISTICS_API_URL"],
    "firebase": ["FIREBASE_PROJECT_ID"],
    "bluetooth": ["BT_ENABLED"],
}


def activate(requested: List[str]) -> List[str]:
    active = []
    for name in requested:
        if name not in SUPPORTED:
            continue
        keys = ENV_KEYS.get(name, [])
        if all(os.getenv(k) for k in keys) or not keys:
            active.append(name)
    return active
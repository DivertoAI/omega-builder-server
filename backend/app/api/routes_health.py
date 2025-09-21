from __future__ import annotations

from fastapi import APIRouter

from backend.app.core.config import settings
from backend.app.integrations.openai.client import (
    get_openai_client,
    OpenAIUnavailable,
)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    # Default to "skip" to avoid accidental spend; only probe when both openai_enabled
    # and the specific probe flag are set.
    text_probe = "skip"
    image_probe = "skip"

    if settings.openai_enabled and settings.openai_api_key:
        # Text probe
        if settings.health_probe_text:
            try:
                client = get_openai_client()
                echo = client.text_echo("omega-ok")
                text_probe = "ok" if echo and "omega-ok" in echo else "fail"
            except OpenAIUnavailable:
                text_probe = "disabled"
            except Exception:
                text_probe = "fail"

        # Image probe
        if settings.health_probe_image:
            try:
                ok = get_openai_client().image_probe()
                image_probe = "ok" if ok else "fail"
            except OpenAIUnavailable:
                image_probe = "disabled"
            except Exception:
                image_probe = "fail"

    return {
        "service": settings.service_name,
        "version": settings.version,
        "env": {
            "omega_llm_model": settings.omega_llm_model,
            "omega_image_model": settings.omega_image_model,
            "openai_key_present": bool(settings.openai_api_key),
            "openai_enabled": bool(settings.openai_enabled),
        },
        "features": {
            "web": settings.omega_enable_web,
            "file_search": settings.omega_enable_file_search,
            "mcp": settings.omega_enable_mcp,
            "image_size": settings.omega_image_size,
            "probe_text_enabled": settings.health_probe_text,
            "probe_image_enabled": settings.health_probe_image,
        },
        "status": "ok",
        "probes": {
            "text_echo": text_probe,
            "image_echo": image_probe,
        },
    }
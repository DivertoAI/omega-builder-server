from fastapi import APIRouter

from backend.app.core.config import settings
from backend.app.integrations.openai.client import (
    get_openai_client,
    OpenAIUnavailable,
)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    text_probe = "pending"
    image_probe = "pending"

    if settings.openai_api_key:
        try:
            client = get_openai_client()
            echo = client.text_echo("omega-ok")
            text_probe = "ok" if echo and "omega-ok" in echo else "fail"
        except OpenAIUnavailable:
            text_probe = "disabled"
        except Exception:
            text_probe = "fail"

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
        },
        "features": {
            "web": settings.omega_enable_web,
            "file_search": settings.omega_enable_file_search,
            "mcp": settings.omega_enable_mcp,
            "image_size": settings.omega_image_size,
        },
        "status": "ok",
        "probes": {
            "text_echo": text_probe,
            "image_echo": image_probe,
        },
    }
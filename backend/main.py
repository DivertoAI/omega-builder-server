# backend/main.py
from __future__ import annotations

import os
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app.core.logging import setup_logging
from backend.app.core.config import settings  # <- unified settings

# Core routers
from backend.app.api.routes_health import router as health_router
from backend.app.api.sse import router as sse_router
from backend.app.api.routes_plan import router as plan_router
from backend.app.api.routes_generate import router as generate_router
from backend.app.api.routes_debug import router as debug_router

# BEGIN OMEGA STUB IMPORTS (managed)
from backend.app.api.routes_stubs import router as stubs_router
from backend.app.api.routes_envs import router as envs_router
from backend.app.api.routes_tags import router as tags_router
from backend.app.api.routes_assets import router as assets_router
from backend.app.api.routes_preview import router as preview_router
from backend.app.api.routes_appetize import router as appetize_router

# END OMEGA STUB IMPORTS (managed)

# Lightweight middleware (kept minimal to avoid test flakiness)
try:
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware
except Exception:  # pragma: no cover
    CORSMiddleware = None  # type: ignore
    GZipMiddleware = None  # type: ignore


def create_app() -> FastAPI:
    # Initialize logging early so all imports use correct handlers/levels
    setup_logging()  # respects LOG_LEVEL/LOG_FORMAT (and config.py fallbacks)

    app = FastAPI(
        title=settings.service_name or "Omega Builder",
        version=settings.version or "0.1.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # --- Global JSON error handler: convert unexpected 500s to JSON so clients/jq can parse ---
    @app.exception_handler(Exception)
    async def _unhandled_exc_to_json(request: Request, exc: Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # Log full traceback to server console (stdout/stderr collected by Docker)
        print("\n=== Unhandled exception ===\n", tb, flush=True)
        # Return a compact JSON response
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": str(exc),
                "traceback_tail": tb[-2000:],  # keep payload small
                "path": str(request.url),
                "method": request.method,
            },
        )

    # Optional middlewares for local DX (SSE-friendly CORS + small responses gzipped)
    if GZipMiddleware is not None:
        app.add_middleware(GZipMiddleware, minimum_size=1024)

    if CORSMiddleware is not None:
        # Pull allowlists from Settings (falls back to permissive defaults)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins or ["*"],
            allow_credentials=True,
            allow_methods=settings.cors_allow_methods or ["*"],
            allow_headers=settings.cors_allow_headers or ["*"],
        )

    # Routes
    app.include_router(health_router)
    app.include_router(sse_router)
    app.include_router(plan_router)
    app.include_router(generate_router)
    app.include_router(debug_router)
    app.include_router(assets_router)

    # BEGIN OMEGA STUB INCLUDES (managed)
    app.include_router(stubs_router)
    app.include_router(envs_router)
    app.include_router(tags_router)
    app.include_router(preview_router)
    app.include_router(appetize_router)
    # END OMEGA STUB INCLUDES (managed)

    # Friendly root
    @app.get("/")
    def root():
        return {
            "service": settings.service_name or "omega-builder",
            "version": settings.version or "0.1.0",
            "environment": settings.environment or "dev",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "tips": {
                "stream_progress": "/api/stream?job_id=<ID>",
                "health": "/api/health",
                "plan": "POST /api/plan",
                "generate": "POST /api/generate",
                "debug_last_run": "/api/debug/last-run",
            },
        }

    # Minimal runtime /meta for quick diagnostics (safe flags only)
    @app.get("/meta")
    def meta():
        # Only expose non-sensitive toggles (never API keys or secrets)
        return {
            "service": settings.service_name,
            "version": settings.version,
            "environment": settings.environment,
            "flags": {
                "disable_image_gen": os.getenv("OMEGA_DISABLE_IMAGE_GEN", "0") in {"1", "true", "yes"},
                "safe_mode": os.getenv("OMEGA_SAFE_MODE", "0") in {"1", "true", "yes"},
                "max_agent_rounds": os.getenv("OMEGA_MAX_AGENT_ROUNDS", "8"),
                "default_wall_clock_sec": os.getenv("OMEGA_DEFAULT_WALL_CLOCK_SEC", "900"),
            },
            "cors": {
                "allow_origins": settings.cors_allow_origins,
                "allow_methods": settings.cors_allow_methods,
                "allow_headers": settings.cors_allow_headers,
            },
        }

    return app


app = create_app()
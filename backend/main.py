# backend/main.py
from __future__ import annotations

import os
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.core.logging import setup_logging
from backend.app.core.config import settings  # <- unified settings

# Core routers
from backend.app.api.routes_health import router as health_router
from backend.app.api.sse import router as sse_router
from backend.app.api.routes_plan import router as plan_router
from backend.app.api.routes_generate import router as generate_router
from backend.app.api.routes_debug import router as debug_router
from backend.app.api.routes_assets import router as assets_router  # <-- assets

# BEGIN OMEGA STUB IMPORTS (managed)
from backend.app.api.routes_stubs import router as stubs_router
from backend.app.api.routes_envs import router as envs_router
from backend.app.api.routes_tags import router as tags_router
from backend.app.api.routes_preview import router as preview_router
from backend.app.api.routes_appetize import router as appetize_router
# END OMEGA STUB IMPORTS (managed)

# NEW: Build+Publish orchestration (ai-vm build → omega publish)
from backend.app.api.routes_build_preview import router as build_preview_router
from backend.app.api.routes_build_matrix import router as build_matrix_router
from backend.app.api.routes_scaffold import router as scaffold_router
from backend.app.api.routes_preview_index import router as preview_index_router
from backend.app.api.routes_wire_services import router as wire_services_router
from backend.app.api.routes_metrics import router as metrics_router
from backend.app.api.routes_orchestrate import router as orchestrate_router
from backend.routes.api_products import router as products_router
from backend.routes.api_cart import router as cart_router
from backend.routes.api_checkout import router as checkout_router
from backend.routes.api_orders import router as orders_router
from backend.routes.api_rx import router as rx_router
from backend.routes.api_validate import router as validate_router

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
    app.include_router(assets_router)  # <-- exposes /api/assets/generate

    # BEGIN OMEGA STUB INCLUDES (managed)
    app.include_router(stubs_router)
    app.include_router(envs_router)
    app.include_router(tags_router)
    app.include_router(preview_router)
    app.include_router(appetize_router)
    # END OMEGA STUB INCLUDES (managed)

    # NEW: Build+Publish (one-call) — /api/preview/build and friends
    app.include_router(build_preview_router)
    app.include_router(build_matrix_router)
    app.include_router(scaffold_router)
    app.include_router(preview_index_router)
    app.include_router(wire_services_router)
    app.include_router(metrics_router)
    app.include_router(orchestrate_router) 



    app.include_router(products_router)
    app.include_router(cart_router)
    app.include_router(checkout_router)
    app.include_router(orders_router)
    app.include_router(rx_router)
    app.include_router(validate_router)

    # Static mount for web previews (served at /preview/<project>/<app>)
    # IMPORTANT: html=True enables directory index fallback to index.html
    OMEGA_PREVIEW_ROOT = os.environ.get("OMEGA_PREVIEW_ROOT", "/preview")
    try:
        app.mount(
            "/preview",
            StaticFiles(directory=OMEGA_PREVIEW_ROOT, html=True),
            name="preview",
        )
    except Exception:
        # If the directory is missing at startup, we'll still try to serve later; avoid startup crash
        # (Directory will be created on first publish.)
        pass

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
                "assets_generate": "POST /api/assets/generate",
                "preview_index": "/preview",
                "preview_build": "POST /api/preview/build",
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
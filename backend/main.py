# backend/main.py
from __future__ import annotations

import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app.core.logging import setup_logging

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
# END OMEGA STUB IMPORTS (managed)

# Lightweight middleware (kept minimal to avoid test flakiness)
try:
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware
except Exception:  # pragma: no cover
    CORSMiddleware = None  # type: ignore
    GZipMiddleware = None  # type: ignore


def create_app() -> FastAPI:
    setup_logging()  # respects LOG_LEVEL/LOG_FORMAT
    app = FastAPI(title="Omega Builder", version="0.1.0")

    # --- Global JSON error handler: convert unexpected 500s to JSON so clients/jq can parse ---
    @app.exception_handler(Exception)
    async def _unhandled_exc_to_json(request: Request, exc: Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # Log full traceback to server console
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
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://127.0.0.1:3000",
                "http://localhost:3000",
                "http://127.0.0.1:5500",
                "http://localhost:5500",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Routes
    app.include_router(health_router)
    app.include_router(sse_router)
    app.include_router(plan_router)
    app.include_router(generate_router)
    app.include_router(debug_router)

    # BEGIN OMEGA STUB INCLUDES (managed)
    app.include_router(stubs_router)
    app.include_router(envs_router)
    app.include_router(tags_router)
    # END OMEGA STUB INCLUDES (managed)

    # Friendly root
    @app.get("/")
    def root():
        return {
            "service": "omega-builder",
            "version": "0.1.0",
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

    return app


app = create_app()
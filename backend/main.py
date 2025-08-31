from fastapi import FastAPI

from backend.app.core.logging import setup_logging
from backend.app.api.routes_health import router as health_router
from backend.app.api.sse import router as sse_router
from backend.app.api.routes_plan import router as plan_router
from backend.app.api.routes_generate import router as generate_router


def create_app() -> FastAPI:
    setup_logging()  # respects LOG_LEVEL/LOG_FORMAT
    app = FastAPI(title="Omega Builder", version="0.1.0")

    # Routes
    app.include_router(health_router)
    app.include_router(sse_router)
    app.include_router(plan_router)
    app.include_router(generate_router)

    return app


app = create_app()
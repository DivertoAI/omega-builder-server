from fastapi import FastAPI

from backend.app.core.logging import setup_logging
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


def create_app() -> FastAPI:
    setup_logging()  # respects LOG_LEVEL/LOG_FORMAT
    app = FastAPI(title="Omega Builder", version="0.1.0")

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

    return app


app = create_app()
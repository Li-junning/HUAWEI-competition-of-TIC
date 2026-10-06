"""Application assembly and ownership of database/pipeline resources."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .access import AccessBoundary, validate_access_settings

from .api import knowledge, status, tasks
from .api.errors import register_error_handlers
from .api.request_limits import TaskRequestBodyLimit
from .api.task_admission import TaskAdmissionLimit
from .config import Settings, get_settings
from .diagnostics import configure_provider_logging
from .pipeline import Pipeline
from .storage import Storage


def create_app(
    settings: Settings | None = None,
    *,
    pipeline_factory: Callable[[Storage, Settings], Pipeline] = Pipeline,
) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    validate_access_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage = Storage(settings.database_path)
        try:
            configure_provider_logging(Path(settings.database_path).parent / "logs")
            app.state.pipeline = pipeline_factory(storage, settings)
            storage.mark_running_interrupted()
            yield
        finally:
            app.state.pipeline = None
            storage.close()

    production = settings.environment == "production"
    app = FastAPI(title="AI Answer Verifier API", version="0.1.0", lifespan=lifespan,
                  docs_url=None if production else "/docs", redoc_url=None if production else "/redoc",
                  openapi_url=None if production else "/openapi.json")
    app.add_middleware(TaskRequestBodyLimit)
    app.add_middleware(TaskAdmissionLimit, max_active=settings.max_concurrency)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=production,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Accept", "X-CSRF-Token", "X-Verifier-Request"],
    )
    app.add_middleware(AccessBoundary, settings=settings)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts), www_redirect=False)
    register_error_handlers(app)
    app.include_router(status.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    return app

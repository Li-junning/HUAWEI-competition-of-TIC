"""Application assembly and ownership of database/pipeline resources."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import status, tasks
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

    app = FastAPI(title="AI Answer Verifier API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TaskRequestBodyLimit)
    app.add_middleware(TaskAdmissionLimit, max_active=settings.max_concurrency)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )
    register_error_handlers(app)
    app.include_router(status.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    return app

"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.internal.health import router as health_router
from app.api.internal.test_enqueue import router as test_enqueue_router
from app.api.public.downloads import router as downloads_router
from app.composition import build_api
from app.config import Settings


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        composition = await build_api(settings)
        app.state.composition = composition
        app.state.settings = settings
        # Start the in-process /metrics server (ADR-0007 §2.5). Noop'd
        # when METRICS_ENABLED=false (composition.metrics_server is
        # None). Started inside the lifespan so the bind happens only
        # in worker processes that actually serve traffic, never in
        # ``--reload`` parent processes.
        if composition.metrics_server is not None:
            start = getattr(composition.metrics_server, "start", None)
            if start is not None:
                await start()
        try:
            yield
        finally:
            await composition.aclose()

    app = FastAPI(
        title="DWTGBot API",
        version="0.1.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    app.include_router(health_router, tags=["health"])
    app.include_router(downloads_router, tags=["downloads"])
    # Always mount; the handler returns 404 unless INTERNAL_TEST_TOKEN is set.
    app.include_router(test_enqueue_router, tags=["internal"])
    return app

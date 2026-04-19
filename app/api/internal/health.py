"""Health/readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.composition import ApiComposition
from app.config import Settings

router = APIRouter()


def _composition(request: Request) -> ApiComposition:
    return request.app.state.composition


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness — process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    composition: ApiComposition = Depends(_composition),
    settings: Settings = Depends(_settings),
) -> dict[str, object]:
    """Readiness — DB + Redis reachable, storage writable."""
    checks: dict[str, str] = {}

    try:
        async with composition.core.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail: {exc}"

    try:
        pong = await composition.core.redis.ping()
        checks["redis"] = "ok" if pong else "fail"
    except Exception as exc:
        checks["redis"] = f"fail: {exc}"

    try:
        composition.storage.init()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"fail: {exc}"

    if any(v != "ok" for v in checks.values()):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=checks)

    return {"status": "ready", "env": settings.APP_ENV.value, "checks": checks}

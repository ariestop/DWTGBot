"""Health/readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.composition import ApiComposition
from app.config import Settings
from app.logging_config import get_logger

router = APIRouter()
_logger = get_logger(__name__)


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
    """Readiness — DB + Redis reachable, storage writable.

    Audit fix A5: the failure branches used to put ``str(exc)`` into
    the JSON body, which leaked driver-level detail (socket paths,
    occasionally credential fragments from DSNs) to any caller that
    reached the endpoint. Now the body carries only ``"fail"`` per
    check and the full exception goes to the structured log via
    ``_logger.exception(...)``.
    """
    checks: dict[str, str] = {}

    try:
        async with composition.core.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        _logger.exception("readyz_check_failed", check="postgres")
        checks["postgres"] = "fail"

    try:
        pong = await composition.core.redis.ping()
        checks["redis"] = "ok" if pong else "fail"
    except Exception:
        _logger.exception("readyz_check_failed", check="redis")
        checks["redis"] = "fail"

    try:
        composition.storage.init()
        checks["storage"] = "ok"
    except Exception:
        _logger.exception("readyz_check_failed", check="storage")
        checks["storage"] = "fail"

    if any(v != "ok" for v in checks.values()):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=checks)

    return {"status": "ready", "env": settings.APP_ENV.value, "checks": checks}

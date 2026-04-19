"""Dev-only ``POST /internal/test/enqueue`` endpoint.

This endpoint exists to make load and capacity testing reproducible
(`docs/37-load-and-capacity.md`). It is **gated three ways**:

1. ``INTERNAL_TEST_TOKEN`` must be set in the environment. If it is empty,
   the route returns 404 — the endpoint behaves as if it does not exist.
2. The caller must present that exact token in the
   ``X-Internal-Test-Token`` header. Wrong/missing token → 401.
3. ``Settings.validate_runtime`` refuses to start the process at all when
   ``APP_ENV=production`` and ``INTERNAL_TEST_TOKEN`` is non-empty.

The endpoint reuses ``EnqueueDownloadUseCase`` so the per-user concurrency
cap (``MAX_CONCURRENT_JOBS_PER_USER``) and the same logging contract apply.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.application.dto.jobs import EnqueueDownloadInput
from app.config import Settings
from app.domain.enums import Platform
from app.exceptions import AppError
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

if TYPE_CHECKING:
    from app.composition import ApiComposition

router = APIRouter()
_logger = get_logger(__name__)


class _EnqueueRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    chat_id: int = Field(..., gt=0)
    source_url: str = Field(..., min_length=1, max_length=2048)
    platform: Platform
    selected_option_key: str = Field(..., min_length=1, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=64)


class _EnqueueResponse(BaseModel):
    job_id: int
    correlation_id: str


def _composition(request: Request) -> "ApiComposition":
    return request.app.state.composition


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/internal/test/enqueue",
    response_model=_EnqueueResponse,
    include_in_schema=False,
)
async def test_enqueue(
    payload: _EnqueueRequest,
    request: Request,
    x_internal_test_token: str | None = Header(default=None),
) -> _EnqueueResponse:
    settings = _settings(request)
    composition = _composition(request)

    if not settings.INTERNAL_TEST_TOKEN or composition.enqueue_download is None:
        # Behave as if the route doesn't exist when the feature is disabled.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if x_internal_test_token is None or not secrets.compare_digest(
        x_internal_test_token, settings.INTERNAL_TEST_TOKEN
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad token")

    correlation_id = payload.correlation_id or new_request_id()

    with bind_context(
        request_id=correlation_id,
        user_id=payload.user_id,
        chat_id=payload.chat_id,
    ):
        try:
            result = await composition.enqueue_download.execute(
                EnqueueDownloadInput(
                    user_id=payload.user_id,
                    chat_id=payload.chat_id,
                    source_url=payload.source_url,
                    platform=payload.platform,
                    selected_option_key=payload.selected_option_key,
                    correlation_id=correlation_id,
                )
            )
        except AppError as exc:
            _logger.warning("test_enqueue_rejected", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=exc.user_message,
            ) from exc

    _logger.info("test_enqueue_ok", job_id=result.job_id)
    return _EnqueueResponse(job_id=result.job_id, correlation_id=correlation_id)

"""Internal API token guard for protected HTTP routes.

Scope:

* protect ``/readyz`` and ``/internal/*`` with ``X-Internal-Token``;
* leave ``/healthz`` and public temp-link downloads ``/d/{token}`` open;
* degrade to a no-op when ``API_INTERNAL_TOKEN`` is empty (dev mode).
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.config import Settings, get_settings

_PUBLIC_PATHS = frozenset({"/healthz"})
_PUBLIC_PREFIXES = ("/d/",)
_PROTECTED_PATHS = frozenset({"/readyz"})
_PROTECTED_PREFIXES = ("/internal/",)


def should_require_api_token(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return False
    if path in _PROTECTED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES)


def _settings_from_request(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


async def require_api_token(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if not should_require_api_token(request.url.path):
        return await call_next(request)

    settings = _settings_from_request(request)
    expected = settings.API_INTERNAL_TOKEN
    if not expected:
        return await call_next(request)

    supplied = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(supplied, expected):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
        )

    return await call_next(request)

"""Optional Sentry / GlitchTip integration (L7 audit fix).

``configure_sentry(settings, role)`` is safe to call unconditionally
— it's a no-op when ``SENTRY_DSN`` is empty. Every entrypoint
(``main_bot``, ``main_api``, worker, cleanup, backup) calls it
alongside :func:`configure_logging`, so turning on error
aggregation is a one-env-var redeploy rather than a code change.

Why do we initialise per-role rather than in a shared composition
root? Because each role has a different reasonable sampling and
a different set of integrations:

* ``bot`` benefits from FastAPI's equivalent for ``python-telegram-bot`` (there is none),
  so it uses the stdlib logging integration only;
* ``api`` wires the FastAPI/ASGI integration so HTTP tracing works;
* ``worker`` / ``cleanup`` / ``backup`` are long-running loops, so
  a lower default sample rate is preferred.

The ``role`` argument drives those differences. It is a pure
string tag; ``configure_sentry`` never inspects it for control
flow beyond the logged role name.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.logging_config import get_logger

_logger = get_logger(__name__)


def configure_sentry(settings: Settings, *, role: str) -> None:
    """Initialise Sentry for this process if ``SENTRY_DSN`` is set.

    Safe to call multiple times — Sentry's own ``init`` is idempotent
    per-process. On missing ``sentry_sdk`` the function logs a
    warning and returns rather than crashing; on a malformed DSN we
    catch the exception at the SDK boundary and log, so a stray
    config typo doesn't take down the process.
    """
    dsn = (settings.SENTRY_DSN or "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        _logger.warning(
            "sentry_sdk_missing",
            hint="SENTRY_DSN is set but sentry-sdk is not installed",
            role=role,
        )
        return

    environment = (settings.SENTRY_ENVIRONMENT or settings.APP_ENV.value or "unknown").strip()
    release = settings.SENTRY_RELEASE.strip() or None

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            # Every entrypoint already funnels through structlog →
            # stdlib; the logging integration captures ERROR+ as
            # events without us sprinkling ``capture_exception`` at
            # every ``_logger.exception`` site.
            #
            # sentry-sdk 2.x expects ``event_level`` as a stdlib
            # ``logging`` int, not a level name string.
            integrations=[
                LoggingIntegration(level=None, event_level=logging.ERROR),
            ],
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            # PII is forwarded by default as of sentry-sdk 2.x — keep
            # it explicit so no one later reads the docs and flips
            # the default by accident.
            send_default_pii=False,
            attach_stacktrace=True,
            # The ``role`` shows up on every event as a first-class
            # tag, which is what most incident queries filter by.
            before_send=_tag_role(role),
        )
    except Exception:  # pragma: no cover  defensive
        # Audit fix A12: ``.exception`` attaches the full stack so the
        # exact reason Sentry refused to initialise (DNS failure,
        # malformed DSN, SSL handshake) is recoverable from the log.
        # The service still runs without Sentry — this is not fatal —
        # but silently dropping the diagnostics was making support
        # tickets unactionable.
        _logger.exception("sentry_init_failed", role=role)
        return

    _logger.info("sentry_enabled", role=role, environment=environment)


def _tag_role(role: str) -> Any:
    """Return a ``before_send`` callback that tags every event
    with the role name. Kept out of ``configure_sentry`` so the
    closure over ``role`` is obvious at the call site."""

    def _before_send(event: Any, _hint: Any) -> Any:
        tags = event.setdefault("tags", {})
        tags.setdefault("role", role)
        return event

    return _before_send

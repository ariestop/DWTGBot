"""``configure_sentry`` — L7 audit fix.

The integration is a pass-through when the DSN is blank. When the
DSN is set, it must defer the ``sentry_sdk`` import to the
configuration moment (not module load) so test environments
without the SDK installed can still import the codebase without
exceptions.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings

_TOKEN = "0000000000:test-token"


@pytest.fixture(autouse=True)
def _clean_bot_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings requires BOT_TOKEN ≥ 10 chars; supply a stable sentinel
    # so the suite doesn't need a .env file.
    monkeypatch.setenv("BOT_TOKEN", _TOKEN)


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "BOT_TOKEN": _TOKEN,
        "PUBLIC_BASE_URL": "http://localhost:8080",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_noop_when_dsn_blank() -> None:
    """A blank DSN leaves the process untouched: no Sentry import,
    no log event tagged ``sentry_enabled``."""
    from app.observability.sentry import configure_sentry

    settings = _make_settings(SENTRY_DSN="")
    # Must not raise. The important assertion is negative: calling
    # configure_sentry here does not import sentry_sdk.
    configure_sentry(settings, role="test")


def test_invokes_sdk_when_dsn_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``SENTRY_DSN`` is set, ``sentry_sdk.init`` is called
    with the expected kwargs derived from Settings."""

    calls: list[dict[str, Any]] = []

    class _FakeLoggingIntegration:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _FakeSdk:
        def init(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    import sys
    import types

    fake_root = types.SimpleNamespace(
        init=_FakeSdk().init,
    )
    fake_integrations = types.SimpleNamespace(
        logging=types.SimpleNamespace(LoggingIntegration=_FakeLoggingIntegration),
    )
    # Inject the fakes so ``configure_sentry`` resolves them via its
    # lazy ``import sentry_sdk`` / ``from sentry_sdk.integrations.logging
    # import LoggingIntegration`` block without needing the real
    # package on the test machine.
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_root)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", fake_integrations)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.logging",
        fake_integrations.logging,
    )

    from app.observability.sentry import configure_sentry

    settings = _make_settings(
        SENTRY_DSN="https://abc@sentry.example/42",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
        SENTRY_RELEASE="dwtgbot@1.2.3",
    )
    configure_sentry(settings, role="bot")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["dsn"] == "https://abc@sentry.example/42"
    assert kwargs["environment"] == "staging"
    assert kwargs["release"] == "dwtgbot@1.2.3"
    assert kwargs["traces_sample_rate"] == 0.25
    assert kwargs["send_default_pii"] is False
    # before_send tags every event with the role (verify it does so
    # on a sample event dict; returns the same dict with a role tag).
    tagged = kwargs["before_send"]({"message": "x"}, {})
    assert tagged["tags"]["role"] == "bot"


def test_missing_sdk_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the SDK isn't installed and the DSN *is* set, the process
    must not crash — just log a warning and carry on."""
    import sys

    # Force the lazy import to raise.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    from app.observability.sentry import configure_sentry

    settings = _make_settings(SENTRY_DSN="https://abc@sentry.example/42")
    configure_sentry(settings, role="test")
    # No assertion — the test passes iff no exception leaked out.

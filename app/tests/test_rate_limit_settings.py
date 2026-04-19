"""``RL_*`` parsing + cross-layer validation (docs/36- §6, §10 #6)."""

from __future__ import annotations

import pytest

from app.config import get_settings, parse_rl_window
from app.domain.rate_limit import LimitLayer


class TestParseRlWindow:
    def test_happy_path(self) -> None:
        w = parse_rl_window("5/60", layer=LimitLayer.USER_BURST)
        assert w.limit == 5
        assert w.window_s == 60
        assert w.layer is LimitLayer.USER_BURST

    def test_strips_whitespace(self) -> None:
        w = parse_rl_window("  20 / 3600  ", layer=LimitLayer.USER_HOURLY)
        assert w.limit == 20 and w.window_s == 3600

    @pytest.mark.parametrize("bad", ["", "5", "5/", "/60", "abc/60", "5/abc", "5,60"])
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_rl_window(bad, layer=LimitLayer.USER_BURST)

    def test_zero_limit_rejected_by_value_object(self) -> None:
        with pytest.raises(ValueError):
            parse_rl_window("0/60", layer=LimitLayer.USER_BURST)


class TestSettingsValidation:
    def test_default_windows_parse(self) -> None:
        get_settings.cache_clear()
        s = get_settings()
        rl = s.rate_limit_windows
        assert rl.user_burst.limit == 5
        assert rl.global_burst.limit == 60

    def test_user_above_chat_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RL_USER_BURST", "20/60")
        monkeypatch.setenv("RL_CHAT_BURST", "10/60")
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("RL_USER_BURST" in e and "RL_CHAT_BURST" in e for e in errors)

    def test_chat_above_global_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RL_CHAT_BURST", "100/60")
        monkeypatch.setenv("RL_GLOBAL_BURST", "60/60")
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("RL_CHAT_BURST" in e and "RL_GLOBAL_BURST" in e for e in errors)

    def test_user_burst_window_above_hourly_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RL_USER_BURST", "5/7200")
        monkeypatch.setenv("RL_USER_HOURLY", "20/3600")
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("RL_USER_BURST" in e and "RL_USER_HOURLY" in e for e in errors)

    def test_malformed_rl_propagates_as_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RL_USER_BURST", "garbage")
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("Invalid RL_*" in e for e in errors)

    def test_production_metrics_public_bind_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
        monkeypatch.setenv("METRICS_ENABLED", "true")
        monkeypatch.setenv("METRICS_BIND_HOST", "0.0.0.0")  # noqa: S104
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("METRICS_BIND_HOST" in e for e in errors)

    def test_metrics_disabled_skips_bind_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # METRICS_ENABLED=false → bind host is irrelevant.
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
        monkeypatch.setenv("METRICS_ENABLED", "false")
        monkeypatch.setenv("METRICS_BIND_HOST", "0.0.0.0")  # noqa: S104
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert not any("METRICS_BIND_HOST" in e for e in errors)

    def test_production_fail_closed_requires_explicit_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
        monkeypatch.setenv("RL_FAIL_OPEN", "false")
        get_settings.cache_clear()
        errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
        assert any("RL_FAIL_OPEN" in e for e in errors)

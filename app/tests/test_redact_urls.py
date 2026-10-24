"""URLs never reach logs or Sentry in full (P11, docs audit 2026-10 §2.5).

``test_no_urls_in_logs.py`` stops ``url=`` keys; this covers what that
cannot: URLs inside ``error=str(exc)``, rendered tracebacks and
third-party log lines (httpx logs Telegram calls with the bot token in
the path).
"""

from __future__ import annotations

import json
import logging

import pytest

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.observability.sentry import _tag_role
from app.utils.redact import redact_urls

_TOKEN = "123456789:AAH-fake-token-for-tests-0123456789abc"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "ERROR: Unsupported URL: https://www.example.com/watch?v=abc&t=1",
            "ERROR: Unsupported URL: https://www.example.com/…",
        ),
        (
            f'HTTP Request: POST https://api.telegram.org/bot{_TOKEN}/getUpdates "HTTP/1.1 200 OK"',
            'HTTP Request: POST https://api.telegram.org/… "HTTP/1.1 200 OK"',
        ),
        ("proxy http://user:secret@10.0.0.5:3128/x failed", "proxy http://10.0.0.5/… failed"),
        ("(see https://YouTu.be/abc)", "(see https://youtu.be/…)"),
        (
            "a https://a.test/1 and b socks5://b.test:1080",
            "a https://a.test/… and b socks5://b.test/…",
        ),
        ("no links here: job 42 failed", "no links here: job 42 failed"),
    ],
)
def test_redact_urls_keeps_scheme_and_host_only(text: str, expected: str) -> None:
    assert redact_urls(text) == expected


def _json_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    # Called inside the test, not a fixture: the stdlib handler binds
    # sys.stdout when configured, and only during the test is that capsys.
    monkeypatch.setenv("LOG_JSON", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOG_LEVEL", "INFO")  # httpx request lines are INFO
    get_settings.cache_clear()
    configure_logging(get_settings())


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_error_text_and_traceback_lose_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _json_logging(monkeypatch)
    log = get_logger("dwtgbot.test")
    exc = RuntimeError("ERROR: Unsupported URL: https://www.instagram.com/p/SECRETPOST/")
    log.warning("link_analysis_failed", error=str(exc))
    try:
        raise exc
    except RuntimeError:
        log.exception("job_failed_unexpected")

    out = _lines(capsys)
    rendered = json.dumps(out)
    assert "SECRETPOST" not in rendered
    assert out[0]["error"] == "ERROR: Unsupported URL: https://www.instagram.com/…"
    assert "https://www.instagram.com/…" in str(out[1]["exception"])


def test_stdlib_httpx_line_loses_the_bot_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _json_logging(monkeypatch)
    logging.getLogger("httpx").info(
        'HTTP Request: %s %s "%s %d %s"',
        "POST",
        f"https://api.telegram.org/bot{_TOKEN}/getUpdates",
        "HTTP/1.1",
        200,
        "OK",
    )

    [line] = _lines(capsys)
    assert _TOKEN not in json.dumps(line)
    assert "_record" not in line
    assert line["event"] == 'HTTP Request: POST https://api.telegram.org/… "HTTP/1.1 200 OK"'


def test_sentry_events_lose_urls() -> None:
    event = {
        "logentry": {"message": "fetch https://a.test/private?id=1 failed"},
        "exception": {
            "values": [{"type": "DownloadError", "value": "Unsupported URL: https://b.test/x"}]
        },
        "breadcrumbs": {"values": [{"message": f"POST https://api.telegram.org/bot{_TOKEN}/send"}]},
    }

    out = _tag_role("worker")(event, None)

    assert out["tags"]["role"] == "worker"
    assert out["logentry"]["message"] == "fetch https://a.test/… failed"
    assert out["exception"]["values"][0]["value"] == "Unsupported URL: https://b.test/…"
    assert _TOKEN not in json.dumps(out)

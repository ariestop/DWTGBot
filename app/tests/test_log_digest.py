"""deploy/scripts/log_digest.py: its output lands in a public CI log.

The digest must keep enough to diagnose a failure (event names, levels,
exception types, startup crash text) and drop everything that identifies
a user or leaks a secret.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "log_digest.py"


@pytest.fixture(scope="module")
def digest_mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("log_digest", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (
            "HTTP Request: POST https://api.telegram.org/bot123456789:AAH-abcdefghijklmnopqrstuvwxyz012345/getUpdates",
            "AAH-abc",
        ),
        ("connect redis://:s3cr3tpass@redis:6379/0 failed", "s3cr3tpass"),
        ("REDIS_PASSWORD=hunter2 is weak", "hunter2"),
        ('{"api_token": "abcd1234"}', "abcd1234"),
        ("input_value='postgresql://u:pw@db/x', input_type=str", "pw@db"),
        ("user_id=987654321 chat_id=-1001234567890", "987654321"),
        ("user_id=987654321 chat_id=-1001234567890", "1001234567890"),
        ("download https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("wrote /srv/dwtgbot/storage/ab/My Holiday Video.mp4", "Holiday Video.mp4"),
        ("from 203.0.113.7 by someone@example.com", "203.0.113.7"),
        ("from 203.0.113.7 by someone@example.com", "someone@example.com"),
        ("job 3f2b8c1e-1234-4abc-8def-0123456789ab failed", "3f2b8c1e-1234"),
    ],
)
def test_redact_removes_sensitive_values(digest_mod: ModuleType, raw: str, secret: str) -> None:
    assert secret not in digest_mod.redact(raw)


def test_redact_keeps_diagnostic_text(digest_mod: ModuleType) -> None:
    text = (
        "RuntimeError: Invalid configuration: Redis must require a password in production: "
        "set REDIS_PASSWORD (or put the password into REDIS_URL) "
        'File "/app/app/config.py", line 331'
    )
    out = digest_mod.redact(text)
    assert "Redis must require a password in production" in out
    assert 'File "/app/app/config.py", line 331' in out
    assert "progress_recovery_read_failed" in digest_mod.redact("progress_recovery_read_failed")
    assert "https://www.youtube.com/" in digest_mod.redact("https://www.youtube.com/watch?v=x")


def _line(container: str, record: dict[str, object] | str) -> str:
    body = record if isinstance(record, str) else json.dumps(record)
    return f"{container} | 2026-10-22T18:04:16.000000000Z {body}\n"


def test_digest_counts_and_whitelists_fields(digest_mod: ModuleType) -> None:
    stream = [
        _line("dwtgbot_worker", {"event": "job_started", "level": "info", "user_id": 111111111}),
        _line(
            "dwtgbot_worker",
            {
                "event": "job_failed",
                "level": "error",
                "logger": "app.worker",
                "timestamp": "2026-10-22T18:04:16Z",
                "error_type": "UpstreamUnavailableError",
                "user_id": 222222222,
                "url": "https://www.instagram.com/p/SECRETPOST/",
                "title": "private title",
                "exception": (
                    "Traceback (most recent call last):\n"
                    '  File "/app/x.py", line 3, in f\n'
                    "ValueError: boom"
                ),
            },
        ),
        _line("dwtgbot_bot", {"event": "progress_update_failed", "level": "warning"}),
        _line("dwtgbot_api", "RuntimeError: Invalid configuration: 1 validation error"),
        _line("dwtgbot_api", "plain info line"),
    ]
    out = "\n".join(digest_mod.digest(stream, last=10))

    assert "dwtgbot_worker error: 1" in out
    assert "dwtgbot_bot warning: 1" in out
    assert "job_failed" in out
    assert "error_type=UpstreamUnavailableError" in out
    assert "ValueError: boom" in out
    assert "RuntimeError: Invalid configuration" in out
    assert "plain info line" not in out
    for leaked in ("222222222", "111111111", "SECRETPOST", "private title"):
        assert leaked not in out


def test_digest_handles_empty_input(digest_mod: ModuleType) -> None:
    out = "\n".join(digest_mod.digest([], last=5))
    assert "(no log lines)" in out

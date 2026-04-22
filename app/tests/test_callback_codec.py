"""Callback-data codec for inline buttons."""

from __future__ import annotations

import pytest

from app.bot.callbacks.codec import (
    MAX_CALLBACK_LEN,
    CancelJobCallback,
    DownloadCallback,
    PostTextCallback,
    encode_cancel,
)


class TestEncode:
    def test_roundtrip(self) -> None:
        cb = DownloadCallback(request_id="r12345", option_key="video_720")
        decoded = DownloadCallback.try_decode(cb.encode())
        assert decoded == cb

    def test_decode_rejects_unknown_prefix(self) -> None:
        assert DownloadCallback.try_decode("zz|r|k") is None

    def test_decode_rejects_garbage(self) -> None:
        assert DownloadCallback.try_decode("") is None
        assert DownloadCallback.try_decode("dl|") is None
        assert DownloadCallback.try_decode("dl||") is None
        assert DownloadCallback.try_decode("dl|abc") is None

    def test_encode_enforces_size_limit(self) -> None:
        with pytest.raises(ValueError):
            DownloadCallback(request_id="r" * 60, option_key="k").encode()

    def test_encode_within_limit(self) -> None:
        encoded = DownloadCallback(request_id="abcd1234", option_key="audio_mp3").encode()
        assert len(encoded) <= MAX_CALLBACK_LEN


class TestCancel:
    def test_cancel_format(self) -> None:
        assert encode_cancel("abc") == "x|abc"


class TestCancelJobCallback:
    """Wire format ``cj|<job_id>`` — ADR-0010 §2.1 cancel button."""

    def test_roundtrip(self) -> None:
        cb = CancelJobCallback(job_id=12345)
        assert cb.encode() == "cj|12345"
        assert CancelJobCallback.try_decode(cb.encode()) == cb

    def test_decode_rejects_wrong_prefix(self) -> None:
        assert CancelJobCallback.try_decode("dl|1|v") is None
        assert CancelJobCallback.try_decode("x|1") is None
        assert CancelJobCallback.try_decode("") is None

    def test_decode_rejects_non_numeric(self) -> None:
        assert CancelJobCallback.try_decode("cj|abc") is None
        assert CancelJobCallback.try_decode("cj|") is None

    def test_decode_rejects_non_positive_id(self) -> None:
        assert CancelJobCallback.try_decode("cj|0") is None
        assert CancelJobCallback.try_decode("cj|-5") is None

    def test_encode_stays_within_telegram_cap(self) -> None:
        # 64-byte ceiling — even an astronomical 10^18 id fits.
        encoded = CancelJobCallback(job_id=10**18).encode()
        assert len(encoded) <= MAX_CALLBACK_LEN


class TestPostTextCallback:
    """Wire format ``pt|<job_id>`` — ADR-0010 §2.3 post-text button."""

    def test_roundtrip(self) -> None:
        cb = PostTextCallback(job_id=12345)
        assert cb.encode() == "pt|12345"
        assert PostTextCallback.try_decode(cb.encode()) == cb

    def test_decode_rejects_wrong_prefix(self) -> None:
        # Specifically guard against collisions with neighbouring
        # prefixes — they share the same length so a naive
        # ``startswith`` would mis-route.
        assert PostTextCallback.try_decode("dl|1|v") is None
        assert PostTextCallback.try_decode("cj|1") is None
        assert PostTextCallback.try_decode("x|1") is None
        assert PostTextCallback.try_decode("") is None

    def test_decode_rejects_non_numeric(self) -> None:
        assert PostTextCallback.try_decode("pt|abc") is None
        assert PostTextCallback.try_decode("pt|") is None
        assert PostTextCallback.try_decode("pt|1.5") is None

    def test_decode_rejects_non_positive_id(self) -> None:
        assert PostTextCallback.try_decode("pt|0") is None
        assert PostTextCallback.try_decode("pt|-1") is None

    def test_encode_stays_within_telegram_cap(self) -> None:
        encoded = PostTextCallback(job_id=10**18).encode()
        assert len(encoded) <= MAX_CALLBACK_LEN

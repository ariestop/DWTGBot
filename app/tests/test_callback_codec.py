"""Callback-data codec for inline buttons."""

from __future__ import annotations

import pytest

from app.bot.callbacks.codec import (
    MAX_CALLBACK_LEN,
    DownloadCallback,
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

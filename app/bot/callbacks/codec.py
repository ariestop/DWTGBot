"""
Callback-data codec.

Telegram limits callback_data to 64 bytes. We keep the wire format compact:

    dl|<request_id>|<option_key>

The bot does NOT trust callback_data: it always validates the request_id
against the request state store, so fabricated/stale callbacks are safe.
"""

from __future__ import annotations

from dataclasses import dataclass

PREFIX_DOWNLOAD = "dl"
PREFIX_CANCEL = "x"
SEP = "|"
MAX_CALLBACK_LEN = 64


@dataclass(frozen=True, slots=True)
class DownloadCallback:
    request_id: str
    option_key: str

    def encode(self) -> str:
        encoded = SEP.join((PREFIX_DOWNLOAD, self.request_id, self.option_key))
        if len(encoded) > MAX_CALLBACK_LEN:
            raise ValueError(f"callback_data too long: {len(encoded)} > {MAX_CALLBACK_LEN}")
        return encoded

    @classmethod
    def try_decode(cls, raw: str) -> DownloadCallback | None:
        if not raw or not raw.startswith(PREFIX_DOWNLOAD + SEP):
            return None
        parts = raw.split(SEP, 2)
        if len(parts) != 3:
            return None
        _, request_id, option_key = parts
        if not request_id or not option_key:
            return None
        return cls(request_id=request_id, option_key=option_key)


def encode_cancel(request_id: str) -> str:
    return SEP.join((PREFIX_CANCEL, request_id))

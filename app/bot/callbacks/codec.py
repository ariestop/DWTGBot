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
PREFIX_CANCEL_JOB = "cj"
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


@dataclass(frozen=True, slots=True)
class CancelJobCallback:
    """Cancel an in-flight auto-enqueued job (ADR-0010 §2.1).

    Wire format: ``cj|<job_id>``. The ``job_id`` is a DB auto-increment
    int so the encoded string fits well under the 64-byte Telegram
    callback cap for any realistic ID (even 10^18 is 21 bytes).
    """

    job_id: int

    def encode(self) -> str:
        encoded = SEP.join((PREFIX_CANCEL_JOB, str(self.job_id)))
        if len(encoded) > MAX_CALLBACK_LEN:
            raise ValueError(f"callback_data too long: {len(encoded)} > {MAX_CALLBACK_LEN}")
        return encoded

    @classmethod
    def try_decode(cls, raw: str) -> CancelJobCallback | None:
        if not raw or not raw.startswith(PREFIX_CANCEL_JOB + SEP):
            return None
        parts = raw.split(SEP, 1)
        if len(parts) != 2:
            return None
        _, job_id_str = parts
        try:
            job_id = int(job_id_str)
        except ValueError:
            return None
        if job_id <= 0:
            return None
        return cls(job_id=job_id)

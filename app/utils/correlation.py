"""Helpers for correlation/trace ids in logs."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog


def new_request_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def bind_context(**kwargs: object) -> Iterator[None]:
    """Bind kv pairs to the structlog contextvars for the duration of the block."""
    tokens = structlog.contextvars.bind_contextvars(**kwargs)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)

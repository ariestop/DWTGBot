"""Bot fallback text: say "service is down" only when Postgres/Redis is."""

from __future__ import annotations

import socket

import pytest
import redis.exceptions
import sqlalchemy.exc

from app.bot.services.fallback_text import BACKEND_UNAVAILABLE_TEXT, unexpected_error_text
from app.exceptions import ProviderError
from app.utils.errors import is_backend_unavailable


def _sqlalchemy_operational() -> sqlalchemy.exc.OperationalError:
    return sqlalchemy.exc.OperationalError("SELECT 1", {}, ConnectionRefusedError(111, "refused"))


def _chained(outer: Exception, inner: BaseException) -> Exception:
    try:
        try:
            raise inner
        except BaseException as exc:
            raise outer from exc
    except Exception as exc:
        return exc


@pytest.mark.parametrize(
    "exc",
    [
        redis.exceptions.ConnectionError("Error 111 connecting to redis:6379"),
        redis.exceptions.TimeoutError("Timeout reading from redis:6379"),
        redis.exceptions.BusyLoadingError("Redis is loading the dataset in memory"),
        _sqlalchemy_operational(),
        sqlalchemy.exc.InterfaceError("SELECT 1", {}, Exception("connection is closed")),
        ConnectionRefusedError(111, "Connection refused"),
        socket.gaierror(-2, "Name or service not known"),
        # Repositories / use cases wrap the driver error.
        _chained(RuntimeError("enqueue failed"), redis.exceptions.ConnectionError("down")),
        _chained(ValueError("lookup"), _sqlalchemy_operational()),
    ],
)
def test_backend_outages_are_recognised(exc: BaseException) -> None:
    assert is_backend_unavailable(exc)
    assert unexpected_error_text(exc, "default") == BACKEND_UNAVAILABLE_TEXT


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad option key"),
        KeyError("request_id"),
        ProviderError("yt-dlp failed"),
        redis.exceptions.ResponseError("WRONGTYPE"),  # a bug, not an outage
        sqlalchemy.exc.IntegrityError("INSERT", {}, Exception("duplicate key")),
        TimeoutError("yt-dlp probe timed out"),  # builtin, not the Redis client's
    ],
)
def test_other_failures_keep_the_handler_text(exc: BaseException) -> None:
    assert not is_backend_unavailable(exc)
    assert unexpected_error_text(exc, "Не удалось обработать ссылку.") == (
        "Не удалось обработать ссылку."
    )


def test_cyclic_exception_chain_terminates() -> None:
    a = ValueError("a")
    b = ValueError("b")
    a.__context__ = b
    b.__context__ = a
    assert not is_backend_unavailable(a)


def test_asyncpg_errors_recognised_by_name() -> None:
    asyncpg_exceptions = pytest.importorskip("asyncpg.exceptions")
    assert is_backend_unavailable(asyncpg_exceptions.CannotConnectNowError("starting up"))
    assert not is_backend_unavailable(asyncpg_exceptions.UniqueViolationError("dup"))

"""Classify unexpected exceptions without importing the drivers.

The bot layer may not import ``redis`` / ``sqlalchemy`` (see
``.claude/rules/architecture.md``), yet it has to tell a user
whether a failure means "the service is down, retry in a minute" or
"something about this link went wrong". The check goes by the exception
class's top-level package and name, walking ``__cause__`` /
``__context__`` because repositories and arq wrap driver errors.
"""

from __future__ import annotations

import socket

# Packages whose connection-level errors mean Postgres or Redis is gone.
_BACKEND_PACKAGES = frozenset({"redis", "sqlalchemy", "asyncpg"})
_BACKEND_ERROR_NAMES = frozenset(
    {
        # redis-py
        "ConnectionError",
        "TimeoutError",
        "BusyLoadingError",
        # SQLAlchemy (wraps asyncpg) and its pool
        "OperationalError",
        "InterfaceError",
        # asyncpg, when it surfaces unwrapped
        "CannotConnectNowError",
        "ConnectionDoesNotExistError",
        "TooManyConnectionsError",
    }
)


def is_backend_unavailable(exc: BaseException) -> bool:
    """Whether ``exc`` (or anything it wraps) says Postgres/Redis is unreachable."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # Refused / reset connections and DNS failures for ``postgres`` /
        # ``redis`` when the container is down.
        if isinstance(current, ConnectionError | socket.gaierror):
            return True
        cls = type(current)
        if cls.__module__.split(".", 1)[0] in _BACKEND_PACKAGES and (
            cls.__name__ in _BACKEND_ERROR_NAMES
        ):
            return True
        current = current.__cause__ or current.__context__
    return False

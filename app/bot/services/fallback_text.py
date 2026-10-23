"""User-facing text for exceptions that no handler anticipated."""

from __future__ import annotations

from app.utils.errors import is_backend_unavailable

BACKEND_UNAVAILABLE_TEXT = (
    "Сервис временно недоступен — идут технические работы. Попробуйте через пару минут."
)


def unexpected_error_text(exc: BaseException, default: str) -> str:
    """``default``, unless ``exc`` means Postgres/Redis is down.

    "Попробуйте ещё раз" right away is the wrong advice during an outage:
    the retry fails the same way and the user gives up on the bot.
    """
    return BACKEND_UNAVAILABLE_TEXT if is_backend_unavailable(exc) else default

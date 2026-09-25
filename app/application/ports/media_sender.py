"""MediaSender port — sends results and notices back to the user's chat.

Implemented by ``app.infrastructure.telegram.sender.TelegramSender``.
Inline keyboards travel as the framework-free :class:`InlineKeyboard`
DTO; the implementation maps it onto the messenger's own markup types.
Text and captions are HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InlineButton:
    """Exactly one of ``url`` / ``callback_data`` must be set."""

    text: str
    url: str | None = None
    callback_data: str | None = None

    def __post_init__(self) -> None:
        if (self.url is None) == (self.callback_data is None):
            raise ValueError("InlineButton needs exactly one of url / callback_data")


@dataclass(frozen=True, slots=True)
class InlineKeyboard:
    rows: tuple[tuple[InlineButton, ...], ...]

    def with_row_on_top(self, row: tuple[InlineButton, ...]) -> InlineKeyboard:
        return InlineKeyboard(rows=(row, *self.rows))


class MediaSender(Protocol):
    # Exceptions a direct upload may raise that are worth retrying in
    # place before the job-level (arq) retry takes over.
    upload_retry_errors: tuple[type[BaseException], ...]

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboard | None = None,
        disable_web_page_preview: bool = False,
    ) -> None: ...

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboard | None = None,
    ) -> str | None: ...

    async def send_audio(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboard | None = None,
    ) -> str | None: ...

    async def send_photo(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboard | None = None,
    ) -> str | None: ...

    async def send_document(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboard | None = None,
    ) -> str | None: ...

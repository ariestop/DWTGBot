"""
Telegram sender used by the worker.

The worker process owns its own ``telegram.Bot`` (no ``Application``):
it doesn't poll updates, it only sends results back to the chat that
originated the job. The bot token is the same — both processes are
the same Telegram bot.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot, InputFile
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

from app.config import Settings
from app.logging_config import get_logger

_logger = get_logger(__name__)


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self._bot = Bot(
            token=settings.BOT_TOKEN,
            request=HTTPXRequest(connect_timeout=10, read_timeout=120, write_timeout=120),
        )

    async def initialize(self) -> None:
        await self._bot.initialize()

    async def shutdown(self) -> None:
        await self._bot.shutdown()

    async def send_text(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    async def send_video(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        with file_path.open("rb") as f:
            msg = await self._bot.send_video(
                chat_id=chat_id,
                video=InputFile(f, filename=file_path.name),
                caption=caption,
                parse_mode=ParseMode.HTML if caption else None,
                supports_streaming=True,
            )
        return msg.video.file_id if msg.video else None

    async def send_audio(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        with file_path.open("rb") as f:
            msg = await self._bot.send_audio(
                chat_id=chat_id,
                audio=InputFile(f, filename=file_path.name),
                caption=caption,
                parse_mode=ParseMode.HTML if caption else None,
            )
        return msg.audio.file_id if msg.audio else None

    async def send_photo(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        with file_path.open("rb") as f:
            msg = await self._bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f, filename=file_path.name),
                caption=caption,
                parse_mode=ParseMode.HTML if caption else None,
            )
        return msg.photo[-1].file_id if msg.photo else None

    async def send_document(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        with file_path.open("rb") as f:
            msg = await self._bot.send_document(
                chat_id=chat_id,
                document=InputFile(f, filename=file_path.name),
                caption=caption,
                parse_mode=ParseMode.HTML if caption else None,
            )
        return msg.document.file_id if msg.document else None

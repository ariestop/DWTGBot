"""
Telegram sender used by the worker.

The worker process owns its own ``telegram.Bot`` (no ``Application``):
it doesn't poll updates, it only sends results back to the chat that
originated the job. The bot token is the same — both processes are
the same Telegram bot.

L3 (audit fix): file reads are off-loaded to a worker thread via
``asyncio.to_thread`` before being handed to ``InputFile``. PTB's
``InputFile(obj, ...)`` with a file-handle synchronously calls
``obj.read()`` in its ``__init__`` — a 49 MiB ``read()`` on a
network file system is tens of milliseconds of CPU-blocking I/O in
the event loop, which starves the polling heartbeat and arq
health-check. Reading to ``bytes`` in a worker thread keeps the
event-loop responsive; the extra RAM is bounded by
``TELEGRAM_MAX_UPLOAD_MB`` * ``WORKER_CONCURRENCY`` (<= ~200 MiB at
the default 4 * 49 MiB).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import Bot, InputFile
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

from app.config import Settings
from app.logging_config import get_logger

_logger = get_logger(__name__)


async def _read_bytes(path: Path) -> bytes:
    """Read file contents in a worker thread.

    Centralised so every ``send_*`` path uses the same off-loading
    primitive. Returning raw ``bytes`` (instead of an ``aiofiles``
    handle) matches what ``InputFile`` already does internally —
    PTB's ``InputFile(obj, ...)`` with a bytes payload skips the
    synchronous ``obj.read()`` branch entirely.
    """
    return await asyncio.to_thread(path.read_bytes)


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
        data = await _read_bytes(file_path)
        msg = await self._bot.send_video(
            chat_id=chat_id,
            video=InputFile(data, filename=file_path.name),
            caption=caption,
            parse_mode=ParseMode.HTML if caption else None,
            supports_streaming=True,
        )
        return msg.video.file_id if msg.video else None

    async def send_audio(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        data = await _read_bytes(file_path)
        msg = await self._bot.send_audio(
            chat_id=chat_id,
            audio=InputFile(data, filename=file_path.name),
            caption=caption,
            parse_mode=ParseMode.HTML if caption else None,
        )
        return msg.audio.file_id if msg.audio else None

    async def send_photo(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        data = await _read_bytes(file_path)
        msg = await self._bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(data, filename=file_path.name),
            caption=caption,
            parse_mode=ParseMode.HTML if caption else None,
        )
        return msg.photo[-1].file_id if msg.photo else None

    async def send_document(
        self, chat_id: int, file_path: Path, caption: str | None = None
    ) -> str | None:
        data = await _read_bytes(file_path)
        msg = await self._bot.send_document(
            chat_id=chat_id,
            document=InputFile(data, filename=file_path.name),
            caption=caption,
            parse_mode=ParseMode.HTML if caption else None,
        )
        return msg.document.file_id if msg.document else None

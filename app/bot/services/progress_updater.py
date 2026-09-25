"""
Bot-side live-progress updater (ADR-0010 §2.2).

Owns two asyncio tasks:

* **pubsub task** — subscribes once to ``progress:events`` and, on each
  incoming ``job_id``, reads ``progress:{job_id}`` / ``progress_meta:{job_id}``
  and edits the associated Telegram placeholder message.
* **watchdog task** — polls the in-memory active-job registry at a
  small interval and:
    - after ``PROGRESS_STALE_WARN_SEC`` without a pubsub event for a job,
      overwrites the caption with a «connection lost» notice (once);
    - after ``PROGRESS_STALE_DROP_SEC`` without an event, deletes the
      placeholder and drops the job from memory.

At ``start()`` time the updater performs a one-shot ``SCAN progress_meta:*``
so a bot restart during an active download does not leave orphaned
placeholders — any meta with a live TTL is re-attached and future events
continue to update it in place.

The updater intentionally lives *only* in the bot process. The worker is
the sole writer to the Redis side-channel (see ADR-0010 §2.2,
§3 "Alternatives considered" B2); key names live in
``app.application.ports.progress_channel`` and caption text in
``app.bot.services.progress_caption``.

Failure policy
--------------
Every Telegram edit / delete is wrapped: the progress UI is strictly
best-effort and must never propagate an error back to the main flow.
Redis errors in the pubsub loop trigger a short backoff + reconnect.
``RetryAfter`` is swallowed — the next publish cycle will re-apply fresh
progress, so a single dropped edit costs at most one debounce window.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from redis.exceptions import RedisError
from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TelegramError

from app.application.ports.progress_channel import (
    EVENTS_CHANNEL,
    PROGRESS_META_KEY_PREFIX,
    progress_key,
    progress_meta_key,
)
from app.bot.services.progress_caption import STALE_CAPTION, render_caption
from app.config import Settings
from app.domain.enums import ProgressStage
from app.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

_logger = get_logger(__name__)


_WATCHDOG_TICK_SEC = 5.0
# Keep recent placeholder deletions in memory for a short window so the
# recovery scan on a bot restart does not resurrect a job that was
# already finalized just before the restart.
_TOMBSTONE_TTL_SEC = 60.0
# Terminal-caption hold before delete: gives the user a brief glance at
# "Готово" / "Отменено" before the placeholder disappears.
_TERMINAL_HOLD_SEC = 2.0


def _bytes_to_str(value: object) -> str | None:
    """Decode a Redis field value regardless of ``decode_responses`` mode."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _get_field(mapping: dict, key: str) -> str | None:
    """Extract ``key`` from a Redis hash reply that may key by ``str`` or ``bytes``."""
    if not mapping:
        return None
    if key in mapping:
        return _bytes_to_str(mapping[key])
    encoded = key.encode("utf-8")
    if encoded in mapping:
        return _bytes_to_str(mapping[encoded])
    return None


@dataclass(slots=True)
class _ActiveJob:
    """Bookkeeping for one in-flight placeholder message."""

    job_id: int
    chat_id: int
    message_id: int
    has_photo: bool
    last_event_at: float = field(default_factory=time.monotonic)
    last_apply_at: float = 0.0
    last_percent: float = -1.0
    last_stage: ProgressStage | None = None
    last_caption: str = ""
    stale_notified: bool = False
    terminated: bool = False

    @classmethod
    def from_meta(cls, job_id: int, meta: dict) -> _ActiveJob | None:
        chat_id = _get_field(meta, "chat_id")
        message_id = _get_field(meta, "message_id")
        if chat_id is None or message_id is None:
            return None
        try:
            chat_id_int = int(chat_id)
            message_id_int = int(message_id)
        except (TypeError, ValueError):
            return None
        has_photo = _get_field(meta, "thumbnail_url") is not None
        return cls(
            job_id=job_id,
            chat_id=chat_id_int,
            message_id=message_id_int,
            has_photo=has_photo,
        )


class ProgressUpdater:
    """Asyncio-driven consumer of the Redis progress side-channel."""

    __slots__ = (
        "_active",
        "_bot",
        "_pubsub_task",
        "_redis",
        "_running",
        "_settings",
        "_stop_event",
        "_tombstones",
        "_watchdog_task",
        "last_iteration_at",
    )

    def __init__(self, *, settings: Settings, redis: Redis) -> None:
        self._settings = settings
        self._redis = redis
        self._bot: Bot | None = None
        self._pubsub_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._active: dict[int, _ActiveJob] = {}
        self._tombstones: dict[int, float] = {}
        self._running = False
        # Updated on every successful pubsub iteration (including "no event"
        # wakeups when the watchdog ticks). Consumed by /readyz in a
        # follow-up PR; exposed as a public attribute per task §8.
        self.last_iteration_at: float = time.monotonic()

    async def start(self, bot: Bot) -> None:
        if self._running:
            raise RuntimeError("ProgressUpdater already started")
        self._bot = bot
        self._running = True
        await self._recover_active_jobs()
        self._pubsub_task = asyncio.create_task(self._pubsub_loop(), name="progress-updater-pubsub")
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="progress-updater-watchdog"
        )
        _logger.info(
            "progress_updater_started",
            recovered=len(self._active),
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        for task in (self._pubsub_task, self._watchdog_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._pubsub_task = None
        self._watchdog_task = None
        self._running = False
        _logger.info("progress_updater_stopped", still_active=len(self._active))

    # ---- recovery ---------------------------------------------------------

    async def _recover_active_jobs(self) -> None:
        try:
            async for key in self._redis.scan_iter(
                match=f"{PROGRESS_META_KEY_PREFIX}*",
                count=100,
            ):
                key_text = _bytes_to_str(key)
                if key_text is None:
                    continue
                try:
                    job_id = int(key_text.split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                try:
                    meta = await self._redis.hgetall(key)
                except RedisError:
                    _logger.warning("progress_recovery_read_failed", key=key_text)
                    continue
                if not meta:
                    continue
                job = _ActiveJob.from_meta(job_id, meta)
                if job is None:
                    continue
                self._active[job_id] = job
        except RedisError:
            _logger.exception("progress_recovery_scan_failed")

    # ---- pubsub loop ------------------------------------------------------

    async def _pubsub_loop(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                pubsub = self._redis.pubsub()
                try:
                    await pubsub.subscribe(EVENTS_CHANNEL)
                    backoff = 1.0
                    await self._consume_pubsub(pubsub)
                finally:
                    with contextlib.suppress(Exception):
                        await pubsub.unsubscribe(EVENTS_CHANNEL)
                    with contextlib.suppress(Exception):
                        await pubsub.close()
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError):
                _logger.warning("progress_pubsub_reconnect", backoff_s=round(backoff, 2))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            except Exception:
                _logger.exception("progress_pubsub_loop_error")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _consume_pubsub(self, pubsub: object) -> None:
        # ``get_message`` lets us honour ``_stop_event`` promptly without
        # cancelling the whole listener; ``pubsub.listen()`` would block
        # indefinitely on an idle channel.
        while not self._stop_event.is_set():
            try:
                message = await pubsub.get_message(  # type: ignore[attr-defined]
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
            except TimeoutError:
                message = None
            self.last_iteration_at = time.monotonic()
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                job_id = int(_bytes_to_str(raw) or "")
            except ValueError:
                continue
            try:
                await self._handle_event(job_id)
            except Exception:
                _logger.exception("progress_event_handle_failed", job_id=job_id)

    async def _handle_event(self, job_id: int) -> None:  # noqa: PLR0911
        # Eight exit points, all distinct guard conditions (tombstone,
        # missing progress hash, missing meta, malformed stage/percent,
        # debounce). Collapsing into a single return path via nested
        # ifs hurts readability without saving any logic — the method
        # is already a straight-line validator.
        if self._tombstones.get(job_id, 0.0) > time.monotonic():
            return
        try:
            progress = await self._redis.hgetall(progress_key(job_id))
        except RedisError:
            _logger.warning("progress_read_failed", job_id=job_id)
            return
        if not progress:
            return

        job = self._active.get(job_id)
        if job is None:
            try:
                meta = await self._redis.hgetall(progress_meta_key(job_id))
            except RedisError:
                _logger.warning("progress_meta_read_failed", job_id=job_id)
                return
            job = _ActiveJob.from_meta(job_id, meta) if meta else None
            if job is None:
                return
            self._active[job_id] = job

        stage_value = _get_field(progress, "stage")
        percent_text = _get_field(progress, "percent")
        if stage_value is None or percent_text is None:
            return
        try:
            stage = ProgressStage(stage_value)
            percent = float(percent_text)
        except ValueError:
            return
        reason = _get_field(progress, "reason")

        job.last_event_at = time.monotonic()
        job.stale_notified = False

        if self._is_debounced(job, percent=percent, stage=stage):
            return

        caption = render_caption(stage, percent, reason=reason)
        applied = await self._apply_caption(job, caption)
        if applied:
            job.last_apply_at = time.monotonic()
            job.last_percent = percent
            job.last_stage = stage
            job.last_caption = caption

        if stage.is_terminal:
            await self._finalize(job, stage)

    # ---- watchdog ---------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=_WATCHDOG_TICK_SEC)
                return
            except TimeoutError:
                pass
            try:
                await self._watchdog_tick()
            except Exception:
                _logger.exception("progress_watchdog_tick_failed")

    async def _watchdog_tick(self) -> None:
        now = time.monotonic()
        # Evict expired tombstones first so the map does not grow
        # unbounded in a long-lived bot process.
        self._tombstones = {
            job_id: expires_at
            for job_id, expires_at in self._tombstones.items()
            if expires_at > now
        }
        warn_after = float(self._settings.PROGRESS_STALE_WARN_SEC)
        drop_after = float(self._settings.PROGRESS_STALE_DROP_SEC)
        for job_id, job in list(self._active.items()):
            if job.terminated:
                continue
            age = now - job.last_event_at
            if age > drop_after:
                await self._delete_message(job)
                self._forget(job_id)
                continue
            if age > warn_after and not job.stale_notified:
                await self._apply_caption(job, STALE_CAPTION)
                job.stale_notified = True

    # ---- telegram helpers ------------------------------------------------

    async def _apply_caption(self, job: _ActiveJob, caption: str) -> bool:  # noqa: PLR0911
        # Each branch below has to propagate a distinct boolean/logging
        # outcome (rate-limit, not-modified, message-gone, generic TG
        # error). Unifying them behind a single ``return`` would lose
        # the intent of per-exception logging which is the whole point
        # of the method.
        if self._bot is None:
            return False
        if caption == job.last_caption:
            return False
        try:
            if job.has_photo:
                await self._bot.edit_message_caption(
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                    caption=caption,
                )
            else:
                await self._bot.edit_message_text(
                    text=caption,
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                )
        except RetryAfter as exc:
            _logger.warning(
                "progress_edit_rate_limited",
                job_id=job.job_id,
                retry_after=getattr(exc, "retry_after", None),
            )
            return False
        except BadRequest as exc:
            lower = str(exc).lower()
            if "not modified" in lower:
                return False
            if "message to edit not found" in lower or "can't be edited" in lower:
                _logger.info("progress_message_gone", job_id=job.job_id)
                job.terminated = True
                self._forget(job.job_id)
                return False
            _logger.warning(
                "progress_edit_bad_request",
                job_id=job.job_id,
                reason=str(exc),
            )
            return False
        except TelegramError:
            _logger.warning("progress_edit_failed", job_id=job.job_id)
            return False
        return True

    async def _delete_message(self, job: _ActiveJob) -> None:
        if self._bot is None:
            return
        try:
            await self._bot.delete_message(
                chat_id=job.chat_id,
                message_id=job.message_id,
            )
        except TelegramError:
            _logger.info("progress_delete_failed", job_id=job.job_id)

    async def _finalize(self, job: _ActiveJob, stage: ProgressStage) -> None:
        job.terminated = True
        if stage is ProgressStage.FAILED:
            # Keep the error caption in place — user should see the
            # failure reason; cleanup happens via the watchdog (drop_after).
            self._forget(job.job_id, drop_immediately=False)
            return
        # DONE / CANCELLED: short hold so the terminal caption is visible,
        # then remove the placeholder. ``DeliveryService.deliver`` posts
        # the final video as a separate message; no need to keep this one.
        await asyncio.sleep(_TERMINAL_HOLD_SEC)
        await self._delete_message(job)
        self._forget(job.job_id)

    def _forget(self, job_id: int, *, drop_immediately: bool = True) -> None:
        self._active.pop(job_id, None)
        if drop_immediately:
            self._tombstones[job_id] = time.monotonic() + _TOMBSTONE_TTL_SEC

    # ---- debounce ---------------------------------------------------------

    def _is_debounced(
        self,
        job: _ActiveJob,
        *,
        percent: float,
        stage: ProgressStage,
    ) -> bool:
        if stage.is_terminal:
            return False
        if job.last_stage is None:
            return False
        if job.last_stage is not stage:
            return False
        elapsed = time.monotonic() - job.last_apply_at
        if elapsed >= self._settings.PROGRESS_REDRAW_INTERVAL_SEC:
            return False
        return abs(percent - job.last_percent) < self._settings.PROGRESS_DEBOUNCE_PERCENT

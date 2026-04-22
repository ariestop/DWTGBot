"""
Redis-backed :class:`ProgressReporter`.

Implements the side-channel contract from ADR-0010 §2.2:

* ``progress:{job_id}``      — HASH {percent, stage, updated_at [, reason]}
                               with ``PROGRESS_TTL_SEC`` expiry.
* ``progress_meta:{job_id}`` — HASH {chat_id, message_id, started_at
                               [, thumbnail_url]} with ``PROGRESS_META_TTL_SEC``
                               expiry. Written once at ``start()``; the bot
                               restart-recovery path scans this keyspace.
* ``progress:events``        — PUB/SUB channel carrying ``str(job_id)`` on
                               every state change. The progress-updater
                               task subscribes once and reads the hash on
                               each push.

The class also exposes :meth:`download_hook` that returns a plain
synchronous callable suitable for yt-dlp's ``progress_hooks``: yt-dlp
runs inside ``asyncio.to_thread``, so the callback must not touch the
async event loop. We use a lazily-constructed *sync* redis client for
that path — creating it on first use keeps unit tests that never drive
download lifecycles from needing a Redis instance at all.

Debouncing
----------
``update()`` keeps a small in-memory bookkeeping dict per job. An
update is skipped when ALL of:

* the stage is unchanged,
* the absolute percent delta is under ``PROGRESS_DEBOUNCE_PERCENT``,
* and the last applied write was within
  ``PROGRESS_REDRAW_INTERVAL_SEC``.

Terminal stages (``DONE``/``FAILED``/``CANCELLED``) always bypass the
debounce so the updater can drop the placeholder promptly.

Failure policy
--------------
Every Redis call is wrapped in try/except: ADR-0010 forbids the
progress side-channel from breaking the main download flow. On any
failure we log at WARNING and return; the worker proceeds and the
bot-side watchdog eventually swaps the placeholder for a "connection
lost" notice (PR 4).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import redis as redis_sync
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.ports.progress_reporter import ProgressReporter
from app.config import Settings
from app.domain.enums import ProgressStage
from app.logging_config import get_logger

if TYPE_CHECKING:
    from types import TracebackType


_logger = get_logger(__name__)

_EVENTS_CHANNEL = "progress:events"


def _progress_key(job_id: int) -> str:
    return f"progress:{job_id}"


def _meta_key(job_id: int) -> str:
    return f"progress_meta:{job_id}"


@dataclass(slots=True)
class _DebounceState:
    last_write_monotonic: float
    last_percent: float
    last_stage: ProgressStage


class RedisProgressReporter(ProgressReporter):
    """Async :class:`ProgressReporter` driven by redis-py's async client.

    Thread-safety: ``update()`` is called from the asyncio loop, so the
    in-memory debounce dict does not need a lock. The sync download-hook
    path uses its own keys (``progress:{job_id}`` only, no meta
    manipulation) and a private sync client, so there is no contention
    with the async methods either.
    """

    __slots__ = (
        "_r",
        "_redis_url",
        "_settings",
        "_state",
        "_sync_client",
    )

    def __init__(self, *, redis: Redis, redis_url: str, settings: Settings) -> None:
        self._r = redis
        self._redis_url = redis_url
        self._settings = settings
        self._state: dict[int, _DebounceState] = {}
        # Constructed on first download_hook() call; shared across hooks.
        self._sync_client: redis_sync.Redis | None = None

    # ---- ProgressReporter --------------------------------------------------

    async def start(
        self,
        *,
        job_id: int,
        chat_id: int,
        message_id: int,
        thumbnail_url: str | None,
    ) -> None:
        meta = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "started_at": str(int(time.time())),
        }
        if thumbnail_url:
            meta["thumbnail_url"] = thumbnail_url
        progress = {
            "percent": "0",
            "stage": ProgressStage.ANALYZING.value,
            "updated_at": str(int(time.time())),
        }
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.hset(_meta_key(job_id), mapping=meta)  # type: ignore[arg-type]
                pipe.expire(_meta_key(job_id), self._settings.PROGRESS_META_TTL_SEC)
                pipe.hset(_progress_key(job_id), mapping=progress)  # type: ignore[arg-type]
                pipe.expire(_progress_key(job_id), self._settings.PROGRESS_TTL_SEC)
                pipe.publish(_EVENTS_CHANNEL, str(job_id))
                await pipe.execute()
        except RedisError:
            _logger.warning("progress_start_failed", job_id=job_id)
            return
        _logger.info(
            "progress_started",
            job_id=job_id,
            chat_id=chat_id,
            message_id=message_id,
            has_thumbnail=bool(thumbnail_url),
        )

    async def update(
        self,
        *,
        job_id: int,
        percent: float,
        stage: ProgressStage,
    ) -> None:
        clamped = _clamp_percent(percent)
        if self._is_debounced(job_id, percent=clamped, stage=stage):
            return

        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.hset(
                    _progress_key(job_id),
                    mapping={
                        "percent": f"{clamped:.1f}",
                        "stage": stage.value,
                        "updated_at": str(int(time.time())),
                    },
                )
                pipe.expire(_progress_key(job_id), self._settings.PROGRESS_TTL_SEC)
                pipe.publish(_EVENTS_CHANNEL, str(job_id))
                await pipe.execute()
        except RedisError:
            _logger.warning("progress_update_failed", job_id=job_id, stage=stage.value)
            return

        self._state[job_id] = _DebounceState(
            last_write_monotonic=time.monotonic(),
            last_percent=clamped,
            last_stage=stage,
        )

    async def finish(self, *, job_id: int) -> None:
        await self._terminal(job_id, ProgressStage.DONE, reason=None)
        # Meta key can stay until its own TTL expires; progress_updater
        # will notice the DONE stage and clean up the placeholder.

    async def fail(self, *, job_id: int, reason: str) -> None:
        await self._terminal(job_id, ProgressStage.FAILED, reason=reason)

    # ---- sync bridge for yt-dlp progress hook ------------------------------

    def download_hook(self, *, job_id: int) -> Callable[[float], None]:
        """Return a sync callable that writes DOWNLOADING progress to Redis.

        Used by :class:`YtDlpRunner` to forward yt-dlp's percent into the
        side channel without crossing the sync/async boundary on every
        chunk. The callable is best-effort: any Redis error is logged
        once per call site and swallowed so the download keeps running.
        """
        # Defer constructing the sync client until the first hook call.
        # build_worker() has no way to ``await`` during setup, so we
        # cannot use the shared async client here. redis-py's sync
        # Redis is cheap: it multiplexes one TCP connection lazily.

        def _hook(percent: float) -> None:
            client = self._ensure_sync_client()
            if client is None:
                return
            clamped = _clamp_percent(percent)
            try:
                pipe = client.pipeline(transaction=False)
                pipe.hset(
                    _progress_key(job_id),
                    mapping={
                        "percent": f"{clamped:.1f}",
                        "stage": ProgressStage.DOWNLOADING.value,
                        "updated_at": str(int(time.time())),
                    },
                )
                pipe.expire(_progress_key(job_id), self._settings.PROGRESS_TTL_SEC)
                pipe.publish(_EVENTS_CHANNEL, str(job_id))
                pipe.execute()
            except RedisError:
                _logger.warning("progress_hook_failed", job_id=job_id)

        return _hook

    def close_sync_client(self) -> None:
        """Release the cached sync client (safe to call at worker shutdown)."""
        client = self._sync_client
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover defensive
            _logger.exception("progress_sync_client_close_failed")
        finally:
            self._sync_client = None

    def __enter__(self) -> RedisProgressReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close_sync_client()

    # ---- internals ---------------------------------------------------------

    def _ensure_sync_client(self) -> redis_sync.Redis | None:
        if self._sync_client is not None:
            return self._sync_client
        try:
            self._sync_client = redis_sync.Redis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_keepalive=True,
                socket_timeout=5.0,
            )
        except (RedisError, ValueError):
            _logger.exception("progress_sync_client_init_failed")
            return None
        return self._sync_client

    async def _terminal(
        self,
        job_id: int,
        stage: ProgressStage,
        *,
        reason: str | None,
    ) -> None:
        mapping: dict[str, str] = {
            "percent": "100.0" if stage is ProgressStage.DONE else "0.0",
            "stage": stage.value,
            "updated_at": str(int(time.time())),
        }
        if reason:
            # Bounded: reasons come from exception messages which can be
            # multi-paragraph; the updater only needs a short human
            # summary. 512 chars is plenty.
            mapping["reason"] = reason[:512]
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.hset(_progress_key(job_id), mapping=mapping)  # type: ignore[arg-type]
                pipe.expire(_progress_key(job_id), self._settings.PROGRESS_TTL_SEC)
                pipe.publish(_EVENTS_CHANNEL, str(job_id))
                await pipe.execute()
        except RedisError:
            _logger.warning("progress_terminal_failed", job_id=job_id, stage=stage.value)
            return

        # Clear debounce memory so a subsequent re-use of the same
        # job_id (won't happen in practice, but keeps the map bounded)
        # starts fresh.
        self._state.pop(job_id, None)
        _logger.info("progress_terminal", job_id=job_id, stage=stage.value)

    def _is_debounced(
        self,
        job_id: int,
        *,
        percent: float,
        stage: ProgressStage,
    ) -> bool:
        if stage.is_terminal:
            return False
        state = self._state.get(job_id)
        if state is None:
            return False
        if state.last_stage is not stage:
            return False
        now = time.monotonic()
        elapsed = now - state.last_write_monotonic
        if elapsed >= self._settings.PROGRESS_REDRAW_INTERVAL_SEC:
            return False
        return abs(percent - state.last_percent) < self._settings.PROGRESS_DEBOUNCE_PERCENT


def _clamp_percent(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 100.0:
        return 100.0
    return float(value)

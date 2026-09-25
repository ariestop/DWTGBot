"""Tests for :class:`ProgressUpdater` (ADR-0010 §2.2, PR 4).

The updater owns two long-running asyncio tasks; covering the full loop
would require a real Redis pubsub. The unit suite exercises the
deterministic seams instead:

* ``_handle_event`` — the single-event code path that is cheap to drive
  with a fake async Redis hash-store;
* debounce / terminal handling in the same path;
* ``_watchdog_tick`` — threshold logic for warn / drop;
* recovery scan — rebuilds in-memory active jobs from ``progress_meta:*``;
* caption rendering — deterministic and trivially assertable.
"""

from __future__ import annotations

import time

import pytest
from telegram.error import BadRequest, RetryAfter, TelegramError

from app.bot.services.progress_caption import render_bar, render_caption
from app.bot.services.progress_updater import (
    ProgressUpdater,
    _ActiveJob,
)
from app.config import get_settings
from app.domain.enums import ProgressStage

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis that only implements what the updater uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hgetall(self, key: str | bytes) -> dict[bytes, bytes]:
        key_str = key.decode() if isinstance(key, bytes) else key
        mapping = self.hashes.get(key_str, {})
        return {k.encode(): v.encode() for k, v in mapping.items()}

    async def scan_iter(self, *, match: str, count: int = 100):
        for k in list(self.hashes):
            if _glob_match(k, match):
                yield k.encode()


def _glob_match(key: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return key.startswith(pattern[:-1])
    return key == pattern


class _FakeBot:
    def __init__(self) -> None:
        self.captions: list[tuple[int, int, str]] = []
        self.texts: list[tuple[int, int, str]] = []
        self.deletes: list[tuple[int, int]] = []
        self.raise_on_edit: Exception | None = None
        self.raise_on_delete: Exception | None = None

    async def edit_message_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
    ) -> None:
        if self.raise_on_edit is not None:
            exc, self.raise_on_edit = self.raise_on_edit, None
            raise exc
        self.captions.append((chat_id, message_id, caption))

    async def edit_message_text(
        self,
        *,
        text: str,
        chat_id: int,
        message_id: int,
    ) -> None:
        if self.raise_on_edit is not None:
            exc, self.raise_on_edit = self.raise_on_edit, None
            raise exc
        self.texts.append((chat_id, message_id, text))

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        if self.raise_on_delete is not None:
            exc, self.raise_on_delete = self.raise_on_delete, None
            raise exc
        self.deletes.append((chat_id, message_id))


def _make_updater() -> tuple[ProgressUpdater, _FakeRedis, _FakeBot]:
    redis = _FakeRedis()
    settings = get_settings()
    updater = ProgressUpdater(settings=settings, redis=redis)  # type: ignore[arg-type]
    bot = _FakeBot()
    updater._bot = bot  # type: ignore[attr-defined]
    return updater, redis, bot


def _seed_meta(
    redis: _FakeRedis,
    *,
    job_id: int,
    chat_id: int = 100,
    message_id: int = 200,
    thumbnail_url: str | None = "https://x/t.jpg",
) -> None:
    meta: dict[str, str] = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "started_at": str(int(time.time())),
    }
    if thumbnail_url:
        meta["thumbnail_url"] = thumbnail_url
    redis.hashes[f"progress_meta:{job_id}"] = meta


def _seed_progress(
    redis: _FakeRedis,
    *,
    job_id: int,
    percent: float,
    stage: ProgressStage,
    reason: str | None = None,
) -> None:
    mapping: dict[str, str] = {
        "percent": f"{percent:.1f}",
        "stage": stage.value,
        "updated_at": str(int(time.time())),
    }
    if reason:
        mapping["reason"] = reason
    redis.hashes[f"progress:{job_id}"] = mapping


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_bar_bounds() -> None:
    assert render_bar(0.0) == "[" + "░" * 10 + "]"
    assert render_bar(100.0) == "[" + "█" * 10 + "]"
    assert render_bar(-5.0).count("█") == 0
    assert render_bar(200.0).count("█") == 10


def test_render_bar_middle() -> None:
    # 50 % => 5/10 filled exactly.
    assert render_bar(50.0) == "[" + "█" * 5 + "░" * 5 + "]"


def test_render_caption_downloading_has_bar_and_percent() -> None:
    caption = render_caption(ProgressStage.DOWNLOADING, 42.0)
    assert "Скачиваю" in caption
    assert "42" in caption
    assert "[" in caption and "]" in caption


def test_render_caption_analyzing_has_no_bar() -> None:
    caption = render_caption(ProgressStage.ANALYZING, 0.0)
    assert "[" not in caption


def test_render_caption_failed_includes_reason() -> None:
    caption = render_caption(ProgressStage.FAILED, 0.0, reason="timeout")
    assert "timeout" in caption


# ---------------------------------------------------------------------------
# _handle_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_event_edits_caption_when_photo_present() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=42, thumbnail_url="https://x/t.jpg")
    _seed_progress(redis, job_id=42, percent=30.0, stage=ProgressStage.DOWNLOADING)

    await updater._handle_event(42)

    assert len(bot.captions) == 1
    assert bot.texts == []
    chat_id, message_id, caption = bot.captions[0]
    assert chat_id == 100
    assert message_id == 200
    assert "30" in caption
    # Job was registered for watchdog bookkeeping.
    assert 42 in updater._active


@pytest.mark.asyncio
async def test_handle_event_edits_text_when_no_photo() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=7, thumbnail_url=None)
    _seed_progress(redis, job_id=7, percent=10.0, stage=ProgressStage.DOWNLOADING)

    await updater._handle_event(7)

    assert bot.captions == []
    assert len(bot.texts) == 1


@pytest.mark.asyncio
async def test_handle_event_skips_when_meta_missing() -> None:
    updater, redis, bot = _make_updater()
    _seed_progress(redis, job_id=5, percent=10.0, stage=ProgressStage.DOWNLOADING)

    await updater._handle_event(5)

    assert bot.captions == []
    assert bot.texts == []
    assert 5 not in updater._active


@pytest.mark.asyncio
async def test_handle_event_skips_when_progress_missing() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=5)
    # No progress hash seeded.

    await updater._handle_event(5)

    assert bot.captions == []
    assert bot.texts == []


@pytest.mark.asyncio
async def test_handle_event_debounces_small_delta_same_stage() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=1)
    _seed_progress(redis, job_id=1, percent=10.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(1)
    assert len(bot.captions) == 1

    # Tiny delta within PROGRESS_DEBOUNCE_PERCENT (default 3): must be suppressed.
    _seed_progress(redis, job_id=1, percent=11.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(1)
    assert len(bot.captions) == 1


@pytest.mark.asyncio
async def test_handle_event_bypasses_debounce_on_stage_change() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=1)
    _seed_progress(redis, job_id=1, percent=70.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(1)
    _seed_progress(redis, job_id=1, percent=70.0, stage=ProgressStage.PROCESSING)
    await updater._handle_event(1)

    assert len(bot.captions) == 2


@pytest.mark.asyncio
async def test_handle_event_bypasses_debounce_on_big_delta() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=1)
    _seed_progress(redis, job_id=1, percent=10.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(1)

    _seed_progress(redis, job_id=1, percent=80.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(1)

    assert len(bot.captions) == 2


@pytest.mark.asyncio
async def test_handle_event_done_deletes_placeholder() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=9)
    _seed_progress(redis, job_id=9, percent=100.0, stage=ProgressStage.DONE)

    # Monkey-patch the 2s hold so the test stays fast.
    import app.bot.services.progress_updater as mod

    original = mod._TERMINAL_HOLD_SEC
    mod._TERMINAL_HOLD_SEC = 0.0
    try:
        await updater._handle_event(9)
    finally:
        mod._TERMINAL_HOLD_SEC = original

    assert (100, 200) in bot.deletes
    assert 9 not in updater._active
    # Tombstone prevents accidental resurrection on a late event.
    assert 9 in updater._tombstones


@pytest.mark.asyncio
async def test_handle_event_failed_keeps_caption_and_stops_processing() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=11)
    _seed_progress(
        redis,
        job_id=11,
        percent=0.0,
        stage=ProgressStage.FAILED,
        reason="upstream 500",
    )

    await updater._handle_event(11)

    # Caption was edited with the failure text.
    assert len(bot.captions) == 1
    _, _, caption = bot.captions[0]
    assert "upstream 500" in caption
    # No delete on FAILED — user should see the reason.
    assert bot.deletes == []
    # Job is forgotten so late events don't re-render.
    assert 11 not in updater._active


@pytest.mark.asyncio
async def test_handle_event_tombstone_blocks_late_events() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=22)
    _seed_progress(redis, job_id=22, percent=100.0, stage=ProgressStage.DONE)

    import app.bot.services.progress_updater as mod

    original = mod._TERMINAL_HOLD_SEC
    mod._TERMINAL_HOLD_SEC = 0.0
    try:
        await updater._handle_event(22)
    finally:
        mod._TERMINAL_HOLD_SEC = original

    captions_after_terminal = len(bot.captions)
    deletes_after_terminal = len(bot.deletes)
    # Simulate a late publish for the same job_id.
    _seed_progress(redis, job_id=22, percent=50.0, stage=ProgressStage.DOWNLOADING)
    await updater._handle_event(22)

    # No new caption edits or deletes — tombstone ignored the event.
    assert len(bot.captions) == captions_after_terminal
    assert len(bot.deletes) == deletes_after_terminal


# ---------------------------------------------------------------------------
# Telegram error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_not_modified_swallowed() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=3)
    _seed_progress(redis, job_id=3, percent=10.0, stage=ProgressStage.DOWNLOADING)
    bot.raise_on_edit = BadRequest("Message is not modified")

    await updater._handle_event(3)

    # Job is still tracked; no exception propagated.
    assert 3 in updater._active


@pytest.mark.asyncio
async def test_retry_after_swallowed() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=3)
    _seed_progress(redis, job_id=3, percent=10.0, stage=ProgressStage.DOWNLOADING)
    bot.raise_on_edit = RetryAfter(5.0)

    await updater._handle_event(3)

    assert 3 in updater._active  # no crash


@pytest.mark.asyncio
async def test_message_gone_drops_job() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=3)
    _seed_progress(redis, job_id=3, percent=10.0, stage=ProgressStage.DOWNLOADING)
    bot.raise_on_edit = BadRequest("Message to edit not found")

    await updater._handle_event(3)

    assert 3 not in updater._active


@pytest.mark.asyncio
async def test_generic_telegram_error_does_not_forget_job() -> None:
    updater, redis, bot = _make_updater()
    _seed_meta(redis, job_id=3)
    _seed_progress(redis, job_id=3, percent=10.0, stage=ProgressStage.DOWNLOADING)
    bot.raise_on_edit = TelegramError("network blip")

    await updater._handle_event(3)

    # The next tick may recover; job must stay registered.
    assert 3 in updater._active


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_shows_stale_notice_after_threshold() -> None:
    updater, _redis, bot = _make_updater()
    stale = updater._settings.PROGRESS_STALE_WARN_SEC
    job = _ActiveJob(
        job_id=1,
        chat_id=10,
        message_id=20,
        has_photo=True,
        last_event_at=time.monotonic() - stale - 5,
    )
    updater._active[1] = job

    await updater._watchdog_tick()

    assert len(bot.captions) == 1
    _, _, caption = bot.captions[0]
    assert "продолжается" in caption.lower()
    assert job.stale_notified is True


@pytest.mark.asyncio
async def test_watchdog_notice_fires_only_once() -> None:
    updater, _redis, bot = _make_updater()
    stale = updater._settings.PROGRESS_STALE_WARN_SEC
    job = _ActiveJob(
        job_id=1,
        chat_id=10,
        message_id=20,
        has_photo=True,
        last_event_at=time.monotonic() - stale - 5,
    )
    updater._active[1] = job

    await updater._watchdog_tick()
    await updater._watchdog_tick()

    assert len(bot.captions) == 1


@pytest.mark.asyncio
async def test_watchdog_drops_after_hard_threshold() -> None:
    updater, _redis, bot = _make_updater()
    drop = updater._settings.PROGRESS_STALE_DROP_SEC
    job = _ActiveJob(
        job_id=1,
        chat_id=10,
        message_id=20,
        has_photo=True,
        last_event_at=time.monotonic() - drop - 5,
    )
    updater._active[1] = job

    await updater._watchdog_tick()

    assert (10, 20) in bot.deletes
    assert 1 not in updater._active


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_active_jobs_from_meta_scan() -> None:
    updater, redis, _bot = _make_updater()
    _seed_meta(redis, job_id=1, chat_id=11, message_id=21)
    _seed_meta(redis, job_id=2, chat_id=12, message_id=22, thumbnail_url=None)

    await updater._recover_active_jobs()

    assert set(updater._active) == {1, 2}
    assert updater._active[1].has_photo is True
    assert updater._active[2].has_photo is False


@pytest.mark.asyncio
async def test_recover_skips_malformed_meta() -> None:
    updater, redis, _bot = _make_updater()
    redis.hashes["progress_meta:1"] = {"started_at": "x"}  # no chat_id / message_id

    await updater._recover_active_jobs()

    assert updater._active == {}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_start_raises() -> None:
    updater, _redis, _bot = _make_updater()
    updater._running = True
    with pytest.raises(RuntimeError):
        await updater.start(_bot)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    updater, _redis, _bot = _make_updater()
    # Not running → stop is a no-op (no raise, no hang).
    await updater.stop()

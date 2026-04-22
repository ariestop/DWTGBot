"""AutoEnqueueDownloadUseCase — picker-less enqueue flow (ADR-0010 §2.1)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.application.dto.jobs import WorkerJobPayload
from app.application.dto.media import AnalyzedMedia
from app.application.use_cases.auto_enqueue_download import (
    AutoEnqueueDownloadUseCase,
    AutoEnqueueInput,
)
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform, ProgressStage
from app.exceptions import TooManyJobsError
from app.tests.test_enqueue_use_case import _FakeJobsRepo, _FakeQueue


@dataclass(slots=True)
class _FakeReporterRecord:
    job_id: int
    chat_id: int
    message_id: int
    thumbnail_url: str | None


class _FakeProgressReporter:
    def __init__(self) -> None:
        self.starts: list[_FakeReporterRecord] = []

    async def start(
        self, *, job_id: int, chat_id: int, message_id: int, thumbnail_url: str | None
    ) -> None:
        self.starts.append(
            _FakeReporterRecord(
                job_id=job_id,
                chat_id=chat_id,
                message_id=message_id,
                thumbnail_url=thumbnail_url,
            )
        )

    async def update(self, *, job_id: int, percent: float, stage: ProgressStage) -> None:
        return None

    async def finish(self, *, job_id: int) -> None:
        return None

    async def fail(self, *, job_id: int, reason: str) -> None:
        return None

    async def cancel(self, *, job_id: int) -> None:
        return None


class _FakePostTextStore:
    """In-memory ``PostTextStore`` double.

    Records every ``put`` so assertions can pin down both the payload
    and the fact that the use case writes *after* the DB row lands.
    """

    def __init__(self) -> None:
        self.puts: list[tuple[int, str]] = []

    async def put(self, *, job_id: int, text: str) -> None:
        self.puts.append((job_id, text))

    async def get(self, *, job_id: int) -> str | None:
        for jid, text in self.puts:
            if jid == job_id:
                return text
        return None

    async def exists(self, *, job_id: int) -> bool:
        return any(jid == job_id for jid, _ in self.puts)


def _make_uc(
    *,
    provider,
    repo,
    queue,
    reporter,
    post_text: _FakePostTextStore | None = None,
    max_concurrent_per_user: int = 3,
    post_text_min_chars: int = 10,
) -> AutoEnqueueDownloadUseCase:
    return AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),
        jobs_repo=repo,
        queue=queue,
        progress_reporter=reporter,
        post_text_store=post_text if post_text is not None else _FakePostTextStore(),
        max_concurrent_per_user=max_concurrent_per_user,
        post_text_min_chars=post_text_min_chars,
    )


class _FakeProvider:
    """Duck-typed Provider: only ``default_option`` is exercised here."""

    def __init__(self, *, default: DownloadOption) -> None:
        self.platform = Platform.YOUTUBE
        self._default = default

    def default_option(self, info: MediaInfo) -> DownloadOption:
        del info
        return self._default


class _FakeRegistry:
    def __init__(self, provider) -> None:
        self._provider = provider

    def get(self, platform: Platform):
        del platform
        return self._provider


def _make_analyzed() -> AnalyzedMedia:
    info = MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc123",
        title="Demo",
        kind=MediaKind.VIDEO,
        duration_sec=30,
        items=(),
        thumbnail_url="https://example.com/thumb.jpg",
        description="Hello, world — this is long enough",
        raw={"source_url": "https://youtu.be/abc123"},
    )
    option = DownloadOption(
        key="video_720",
        label="720p",
        container="mp4",
        kind=MediaKind.VIDEO,
    )
    return AnalyzedMedia(info=info, options=(option,))


def _make_input(
    analyzed: AnalyzedMedia, *, message_id: int = 999, user_id: int = 42, chat_id: int = 7
) -> AutoEnqueueInput:
    return AutoEnqueueInput(
        user_id=user_id,
        chat_id=chat_id,
        analyzed=analyzed,
        source_url="https://youtu.be/abc123",
        placeholder_message_id=message_id,
        correlation_id="corr",
    )


@pytest.mark.asyncio
async def test_auto_enqueue_happy_path() -> None:
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    post_text = _FakePostTextStore()
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    uc = _make_uc(provider=provider, repo=repo, queue=queue, reporter=reporter, post_text=post_text)

    result = await uc.execute(_make_input(analyzed))

    assert result.job_id == 100
    assert result.selected.key == "video_720"
    assert result.has_description is True
    assert queue.payloads == [WorkerJobPayload(job_id=100, correlation_id="corr")]
    assert len(reporter.starts) == 1
    rec = reporter.starts[0]
    assert rec.job_id == 100
    assert rec.message_id == 999
    assert rec.thumbnail_url == "https://example.com/thumb.jpg"
    # Post-text written verbatim (stripped) under the created job id.
    assert post_text.puts == [(100, "Hello, world — this is long enough")]


@pytest.mark.asyncio
async def test_auto_enqueue_rejects_at_user_cap() -> None:
    repo = _FakeJobsRepo(active_for_user=3)
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    post_text = _FakePostTextStore()
    uc = _make_uc(provider=provider, repo=repo, queue=queue, reporter=reporter, post_text=post_text)

    with pytest.raises(TooManyJobsError):
        await uc.execute(_make_input(analyzed))

    # No side effects survive a cap rejection — the UI replaces the
    # placeholder with an error and the user gets a chance to retry.
    assert queue.payloads == []
    assert reporter.starts == []
    assert post_text.puts == []


@pytest.mark.asyncio
async def test_auto_enqueue_swallows_reporter_failure() -> None:
    """A Redis blip on ``reporter.start`` must NOT unroll the DB row.

    ADR-0010 §2.2 forbids progress bookkeeping from aborting the main
    flow; the job is already persisted + enqueued by the time we call
    the reporter, a crash here would leave arq holding an untracked job.
    """

    class _BoomReporter(_FakeProgressReporter):
        async def start(self, **_kw):
            raise RuntimeError("redis is sad")

    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    reporter = _BoomReporter()
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    uc = _make_uc(provider=provider, repo=repo, queue=queue, reporter=reporter)

    result = await uc.execute(_make_input(analyzed))

    assert result.job_id == 100
    assert queue.payloads and queue.payloads[0].job_id == 100


@pytest.mark.asyncio
async def test_auto_enqueue_flags_missing_description() -> None:
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    analyzed = _make_analyzed()
    # Strip description — emulates a provider that returned no post text.
    info = MediaInfo(
        platform=analyzed.info.platform,
        media_id=analyzed.info.media_id,
        title=analyzed.info.title,
        kind=analyzed.info.kind,
        duration_sec=analyzed.info.duration_sec,
        items=analyzed.info.items,
        thumbnail_url=analyzed.info.thumbnail_url,
        description="   ",
        raw=analyzed.info.raw,
    )
    analyzed = AnalyzedMedia(info=info, options=analyzed.options)
    provider = _FakeProvider(default=analyzed.options[0])

    post_text = _FakePostTextStore()
    uc = _make_uc(provider=provider, repo=repo, queue=queue, reporter=reporter, post_text=post_text)

    result = await uc.execute(_make_input(analyzed))
    assert result.has_description is False
    # Whitespace-only description falls below the min-chars threshold —
    # the store MUST NOT receive a write, otherwise ``DeliveryService``
    # would render a button that pops an empty reply.
    assert post_text.puts == []


def test_auto_enqueue_constructor_rejects_zero_cap() -> None:
    reporter = _FakeProgressReporter()
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    option = DownloadOption(key="v", label="v", container="mp4", kind=MediaKind.VIDEO)
    with pytest.raises(ValueError):
        _make_uc(
            provider=_FakeProvider(default=option),
            repo=repo,
            queue=queue,
            reporter=reporter,
            max_concurrent_per_user=0,
        )


def test_auto_enqueue_constructor_rejects_zero_min_chars() -> None:
    reporter = _FakeProgressReporter()
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    option = DownloadOption(key="v", label="v", container="mp4", kind=MediaKind.VIDEO)
    # Symmetric guard — zero would defeat the "skip short description"
    # product rule, so the constructor rejects it eagerly instead of
    # letting bad config creep into a release.
    with pytest.raises(ValueError):
        _make_uc(
            provider=_FakeProvider(default=option),
            repo=repo,
            queue=queue,
            reporter=reporter,
            post_text_min_chars=0,
        )


@pytest.mark.asyncio
async def test_auto_enqueue_skips_short_description() -> None:
    """Descriptions shorter than ``post_text_min_chars`` do not get
    persisted, and the use case reports ``has_description=False`` so
    the bot does not reserve UI state for an empty button."""
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    post_text = _FakePostTextStore()
    analyzed = _make_analyzed()
    info = MediaInfo(
        platform=analyzed.info.platform,
        media_id=analyzed.info.media_id,
        title=analyzed.info.title,
        kind=analyzed.info.kind,
        duration_sec=analyzed.info.duration_sec,
        items=analyzed.info.items,
        thumbnail_url=analyzed.info.thumbnail_url,
        description="hi!",
        raw=analyzed.info.raw,
    )
    analyzed = AnalyzedMedia(info=info, options=analyzed.options)
    provider = _FakeProvider(default=analyzed.options[0])

    uc = _make_uc(
        provider=provider,
        repo=repo,
        queue=queue,
        reporter=reporter,
        post_text=post_text,
        post_text_min_chars=10,
    )

    result = await uc.execute(_make_input(analyzed))

    assert result.has_description is False
    assert post_text.puts == []


@pytest.mark.asyncio
async def test_auto_enqueue_swallows_post_text_put_failure() -> None:
    """A Redis outage on the post-text side channel must NOT abort
    the main enqueue path. Same contract as the reporter; a missing
    post-text surfaces later as the "больше недоступен" alert."""

    class _BoomStore(_FakePostTextStore):
        async def put(self, *, job_id: int, text: str) -> None:  # type: ignore[override]
            raise RuntimeError("redis is sad")

    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    uc = _make_uc(
        provider=provider,
        repo=repo,
        queue=queue,
        reporter=reporter,
        post_text=_BoomStore(),
    )

    result = await uc.execute(_make_input(analyzed))

    assert result.job_id == 100
    assert queue.payloads and queue.payloads[0].job_id == 100
    # ``has_description=True`` reflects that the description was long
    # enough to qualify — the button-visibility check at delivery
    # time uses ``EXISTS``, so a failed write degrades to "no button"
    # without confusing downstream logic.
    assert result.has_description is True

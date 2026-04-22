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
        description="Hello",
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
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    uc = AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),
        jobs_repo=repo,
        queue=queue,
        progress_reporter=reporter,
        max_concurrent_per_user=3,
    )

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


@pytest.mark.asyncio
async def test_auto_enqueue_rejects_at_user_cap() -> None:
    repo = _FakeJobsRepo(active_for_user=3)
    queue = _FakeQueue()
    reporter = _FakeProgressReporter()
    analyzed = _make_analyzed()
    provider = _FakeProvider(default=analyzed.options[0])

    uc = AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),
        jobs_repo=repo,
        queue=queue,
        progress_reporter=reporter,
        max_concurrent_per_user=3,
    )

    with pytest.raises(TooManyJobsError):
        await uc.execute(_make_input(analyzed))

    # No side effects survive a cap rejection — the UI replaces the
    # placeholder with an error and the user gets a chance to retry.
    assert queue.payloads == []
    assert reporter.starts == []


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

    uc = AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),
        jobs_repo=repo,
        queue=queue,
        progress_reporter=reporter,
        max_concurrent_per_user=3,
    )

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

    uc = AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),
        jobs_repo=repo,
        queue=queue,
        progress_reporter=reporter,
        max_concurrent_per_user=3,
    )

    result = await uc.execute(_make_input(analyzed))
    assert result.has_description is False


def test_auto_enqueue_constructor_rejects_zero_cap() -> None:
    reporter = _FakeProgressReporter()
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    option = DownloadOption(key="v", label="v", container="mp4", kind=MediaKind.VIDEO)
    with pytest.raises(ValueError):
        AutoEnqueueDownloadUseCase(
            providers=_FakeRegistry(_FakeProvider(default=option)),
            jobs_repo=repo,
            queue=queue,
            progress_reporter=reporter,
            max_concurrent_per_user=0,
        )

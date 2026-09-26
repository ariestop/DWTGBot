"""ProcessDownloadUseCase emits progress-reporter calls on phase boundaries.

Focused unit tests: we wire a fake ``ProgressReporter`` that records
every call and drive the use-case through a happy-path download. The
concrete provider / delivery are fakes; this file does not test them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.application.ports.progress_reporter import ProgressReporter
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import JobStatus, MediaKind, Platform, ProgressStage

# --------- Fake collaborators -------------------------------------------------


@dataclass(slots=True)
class _ReporterCall:
    method: str
    kwargs: dict[str, Any]


class _RecordingReporter(ProgressReporter):
    def __init__(self) -> None:
        self.calls: list[_ReporterCall] = []
        self.hook_percents: list[float] = []

    async def start(
        self,
        *,
        job_id: int,
        chat_id: int,
        message_id: int,
        thumbnail_url: str | None,
    ) -> None:
        self.calls.append(
            _ReporterCall(
                "start",
                {
                    "job_id": job_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "thumbnail_url": thumbnail_url,
                },
            )
        )

    async def update(self, *, job_id: int, percent: float, stage: ProgressStage) -> None:
        self.calls.append(
            _ReporterCall("update", {"job_id": job_id, "percent": percent, "stage": stage})
        )

    async def finish(self, *, job_id: int) -> None:
        self.calls.append(_ReporterCall("finish", {"job_id": job_id}))

    async def fail(self, *, job_id: int, reason: str) -> None:
        self.calls.append(_ReporterCall("fail", {"job_id": job_id, "reason": reason}))

    def download_hook(self, *, job_id: int) -> Callable[[float], None]:
        def _hook(p: float) -> None:
            self.hook_percents.append(p)

        return _hook


class _FakeJobsRepo:
    def __init__(self, job: DownloadJob) -> None:
        self._job = job
        self.updates: list[DownloadJob] = []

    async def get(self, job_id: int) -> DownloadJob | None:
        return self._job if self._job.id == job_id else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        self.updates.append(job)
        return job


class _FakeProvider:
    platform = Platform.YOUTUBE

    def __init__(self, info: MediaInfo, option: DownloadOption, result: DownloadResult) -> None:
        self._info = info
        self._option = option
        self._result = result
        self.received_on_progress: Callable[[float], None] | None = None
        self.probed_sizes: list[int | None] = [50 * 1024 * 1024]

    async def get_info(self, url: str) -> MediaInfo:
        del url
        return self._info

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        del info
        return [self._option]

    def default_option(self, info: MediaInfo) -> DownloadOption:
        del info
        return self._option

    async def probe_size(
        self,
        url: str,
        *,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None:
        del url, info, option
        return self.probed_sizes.pop(0) if self.probed_sizes else None

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
        on_progress: Callable[[float], None] | None = None,
        info: MediaInfo | None = None,
    ) -> DownloadResult:
        del url, option, target_dir, info
        # Simulate yt-dlp calling progress hook 3 times.
        if on_progress is not None:
            for p in (0.0, 50.0, 100.0):
                on_progress(p)
        self.received_on_progress = on_progress
        return self._result


class _FakeProviderRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._p = provider

    def get(self, platform: Platform) -> Any:
        del platform
        return self._p


@dataclass(slots=True)
class _FakeDeliveryOutcome:
    delivered_path: str = "/tmp/x.mp4"
    file_size: int = 1000
    primary_telegram_file_id: str | None = None
    public_url: str | None = None
    method: Any = field(default=None)


class _FakeDelivery:
    def __init__(self) -> None:
        self._method = _StubMethod()

    async def deliver(self, *, job_id: int, chat_id: int, result: DownloadResult) -> Any:
        del job_id, chat_id, result
        outcome = _FakeDeliveryOutcome()
        outcome.method = self._method
        return outcome


class _StubMethod:
    value = "telegram_inline"


class _FakeSender:
    async def send_text(self, chat_id: int, text: str) -> None:
        del chat_id, text


class _FakeStorage:
    def __init__(self, tmp_path: str) -> None:
        self._tmp = tmp_path

    def job_dir(self, job_id: int) -> Any:
        from pathlib import Path

        p = Path(self._tmp) / str(job_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def reset_job_dir(self, job_id: int) -> Any:
        return self.job_dir(job_id)

    def assert_free_space(self) -> None:
        return None


# --------- Builders -----------------------------------------------------------


def _make_job(job_id: int = 42) -> DownloadJob:
    return DownloadJob(
        id=job_id,
        user_id=1,
        chat_id=1,
        platform=Platform.YOUTUBE,
        source_url="https://youtube.com/watch?v=abc",
        selected_option_key="video_720",
        status=JobStatus.PENDING,
    )


def _make_info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="T",
        kind=MediaKind.VIDEO,
        duration_sec=60.0,
    )


def _make_option() -> DownloadOption:
    return DownloadOption(
        key="video_720",
        label="Видео 720p",
        kind=MediaKind.VIDEO,
        height=720,
        container="mp4",
    )


def _make_result() -> DownloadResult:
    return DownloadResult(
        files=("/tmp/x.mp4",),
        total_size_bytes=1000,
        primary_mime="video/mp4",
        title="T",
        kind=MediaKind.VIDEO,
    )


def _make_use_case(
    reporter: ProgressReporter,
    job: DownloadJob,
    tmp_path: str,
    *,
    provider: _FakeProvider | None = None,
) -> tuple[ProcessDownloadUseCase, _FakeJobsRepo, _FakeProvider]:
    repo = _FakeJobsRepo(job)
    prov = provider or _FakeProvider(_make_info(), _make_option(), _make_result())
    registry = _FakeProviderRegistry(prov)
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=registry,  # type: ignore[arg-type]
        storage=_FakeStorage(tmp_path),  # type: ignore[arg-type]
        delivery=_FakeDelivery(),  # type: ignore[arg-type]
        sender=_FakeSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        progress_reporter=reporter,
    )
    return use_case, repo, prov


# --------- Tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_emits_phase_boundaries(tmp_path: Any) -> None:
    reporter = _RecordingReporter()
    job = _make_job()
    use_case, _repo, _prov = _make_use_case(reporter, job, str(tmp_path))
    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))

    update_stages = [c.kwargs["stage"] for c in reporter.calls if c.method == "update"]
    # Expect ANALYZING → DOWNLOADING → PROCESSING → UPLOADING order.
    assert update_stages == [
        ProgressStage.ANALYZING,
        ProgressStage.DOWNLOADING,
        ProgressStage.PROCESSING,
        ProgressStage.UPLOADING,
    ]

    percents = [c.kwargs["percent"] for c in reporter.calls if c.method == "update"]
    assert percents == [5.0, 0.0, 70.0, 95.0]


@pytest.mark.asyncio
async def test_happy_path_calls_finish_exactly_once(tmp_path: Any) -> None:
    reporter = _RecordingReporter()
    use_case, _repo, _prov = _make_use_case(reporter, _make_job(), str(tmp_path))
    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))
    finish_calls = [c for c in reporter.calls if c.method == "finish"]
    assert len(finish_calls) == 1
    assert finish_calls[0].kwargs["job_id"] == 42


@pytest.mark.asyncio
async def test_download_hook_receives_scaled_percent(tmp_path: Any) -> None:
    reporter = _RecordingReporter()
    use_case, _repo, _prov = _make_use_case(reporter, _make_job(), str(tmp_path))
    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))
    # Provider emitted 0/50/100 → scaled into 0/35/70.
    assert reporter.hook_percents == [0.0, 35.0, 70.0]


@pytest.mark.asyncio
async def test_failure_calls_reporter_fail(tmp_path: Any) -> None:
    from app.exceptions import MediaPrivateError

    class _BrokenProvider(_FakeProvider):
        async def get_info(self, url: str) -> MediaInfo:
            raise MediaPrivateError("boom")

    reporter = _RecordingReporter()
    broken = _BrokenProvider(_make_info(), _make_option(), _make_result())
    use_case, _repo, _prov = _make_use_case(reporter, _make_job(), str(tmp_path), provider=broken)
    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))

    fail_calls = [c for c in reporter.calls if c.method == "fail"]
    assert len(fail_calls) == 1
    assert fail_calls[0].kwargs["job_id"] == 42
    assert fail_calls[0].kwargs["reason"]  # non-empty


@pytest.mark.asyncio
async def test_no_job_id_suppresses_progress_calls(tmp_path: Any) -> None:
    reporter = _RecordingReporter()
    # job.id = None intentionally.
    job = DownloadJob(
        id=None,
        user_id=1,
        chat_id=1,
        platform=Platform.YOUTUBE,
        source_url="https://youtube.com/watch?v=abc",
        selected_option_key="video_720",
        status=JobStatus.PENDING,
    )
    repo = _FakeJobsRepo(job)
    # get(None) → our fake returns None; use-case short-circuits with
    # "job_not_found". The reporter must not have been called at all.
    registry = _FakeProviderRegistry(_FakeProvider(_make_info(), _make_option(), _make_result()))
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=registry,  # type: ignore[arg-type]
        storage=_FakeStorage(str(tmp_path)),  # type: ignore[arg-type]
        delivery=_FakeDelivery(),  # type: ignore[arg-type]
        sender=_FakeSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        progress_reporter=reporter,
    )
    await use_case.execute(ProcessDownloadInput(job_id=999, correlation_id="r"))
    assert reporter.calls == []

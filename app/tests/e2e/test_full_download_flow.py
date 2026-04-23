"""Hermetic end-to-end flow tests for enqueue -> worker -> delivery.

The harness stays inside the default unit-test tier:

* ``AutoEnqueueDownloadUseCase`` persists the job + queue payload.
* ``ProcessDownloadUseCase`` drives the worker path.
* ``DeliveryService`` runs real caption / temp-link logic.

External systems are replaced with in-memory doubles:

* ``FakeYtDlpProvider`` writes files under ``tmp_path`` instead of
  talking to yt-dlp / ffmpeg / the network.
* ``FakeTelegramSender`` records outgoing media / text messages.
* ``InMemoryJobsRepo`` / ``InMemoryPostTextStore`` mirror the public
  ports used by the production code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup

from app.application.dto.jobs import WorkerJobPayload
from app.application.dto.media import AnalyzedMedia
from app.application.services.delivery_service import DeliveryService
from app.application.use_cases.auto_enqueue_download import (
    AutoEnqueueDownloadUseCase,
    AutoEnqueueInput,
)
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import Settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import DownloadOption, DownloadResult, MediaInfo
from app.domain.enums import JobStatus, MediaKind, Platform
from app.exceptions import DownloadTimeoutError
from app.infrastructure.storage.local_storage import LocalStorage

_FIXTURE_MP4 = Path(__file__).parent / "fixtures" / "sample.mp4"


@dataclass(slots=True)
class _SentVideo:
    chat_id: int
    file_path: Path
    caption: str | None
    reply_markup: InlineKeyboardMarkup | None


@dataclass(slots=True)
class _SentText:
    chat_id: int
    text: str
    reply_markup: InlineKeyboardMarkup | None


class _InMemoryJobsRepo:
    def __init__(self) -> None:
        self._jobs: dict[int, DownloadJob] = {}
        self._next_id = 100

    async def create(self, job: DownloadJob) -> DownloadJob:
        job.id = self._next_id
        self._next_id += 1
        self._jobs[job.id] = job
        return job

    async def get(self, job_id: int) -> DownloadJob | None:
        return self._jobs.get(job_id)

    async def update(self, job: DownloadJob) -> DownloadJob:
        if job.id is None:
            raise ValueError("job.id is required")
        job.status_version += 1
        self._jobs[job.id] = job
        return job

    async def increment_retries(self, job_id: int) -> int:
        job = self._jobs[job_id]
        job.retries_count += 1
        return job.retries_count

    async def count_active_for_user(self, user_id: int) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.user_id == user_id and job.status in (JobStatus.PENDING, JobStatus.PROCESSING)
        )

    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        if await self.count_active_for_user(job.user_id) >= cap:
            return None
        return await self.create(job)

    async def reap_orphan_processing(self, *, older_than_seconds: int) -> int:
        del older_than_seconds
        return 0


class _FakeQueue:
    def __init__(self) -> None:
        self.payloads: list[WorkerJobPayload] = []

    async def enqueue_download(self, payload: WorkerJobPayload) -> None:
        self.payloads.append(payload)


class _InMemoryPostTextStore:
    def __init__(self) -> None:
        self._data: dict[int, str] = {}

    async def put(self, *, job_id: int, text: str) -> None:
        self._data[job_id] = text

    async def get(self, *, job_id: int) -> str | None:
        return self._data.get(job_id)

    async def exists(self, *, job_id: int) -> bool:
        return job_id in self._data


class _FakeTempLinks:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url
        self.issued: list[tuple[int, str]] = []

    async def issue(self, *, job_id: int, file_path: str) -> tuple[object, str]:
        self.issued.append((job_id, file_path))
        return object(), f"{self._base_url}/d/temp-{job_id}"


class _FakeTelegramSender:
    def __init__(self) -> None:
        self.videos: list[_SentVideo] = []
        self.texts: list[_SentText] = []

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        self.videos.append(
            _SentVideo(
                chat_id=chat_id,
                file_path=file_path,
                caption=caption,
                reply_markup=reply_markup,
            )
        )
        return f"video:{file_path.name}"

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self.texts.append(_SentText(chat_id=chat_id, text=text, reply_markup=reply_markup))

    async def send_audio(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        del chat_id, file_path, caption, reply_markup
        raise AssertionError("send_audio is not expected in this harness")

    async def send_photo(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        del chat_id, file_path, caption, reply_markup
        raise AssertionError("send_photo is not expected in this harness")

    async def send_document(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        del chat_id, file_path, caption, reply_markup
        raise AssertionError("send_document is not expected in this harness")


class _FakeYtDlpProvider:
    def __init__(
        self,
        *,
        info: MediaInfo,
        option: DownloadOption,
        mode: str = "happy",
        output_size_bytes: int | None = None,
    ) -> None:
        self.platform = info.platform
        self._info = info
        self._option = option
        self._mode = mode
        self._output_size_bytes = output_size_bytes

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
        return self._option.estimated_size_bytes

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
        on_progress: Any = None,
    ) -> DownloadResult:
        del url, option
        if self._mode == "timeout":
            raise DownloadTimeoutError("fake yt-dlp timed out")

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        output = target / "result.mp4"
        payload = _FIXTURE_MP4.read_bytes()
        if self._output_size_bytes is not None:
            payload = b"x" * self._output_size_bytes
        output.write_bytes(payload)
        if on_progress is not None:
            on_progress(100.0)
        return DownloadResult(
            files=(str(output),),
            total_size_bytes=output.stat().st_size,
            primary_mime="video/mp4",
            title=self._info.title,
            kind=MediaKind.VIDEO,
        )


class _FakeRegistry:
    def __init__(self, provider: _FakeYtDlpProvider) -> None:
        self._provider = provider

    def get(self, platform: Platform) -> _FakeYtDlpProvider:
        del platform
        return self._provider


def _build_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    telegram_max_upload_mb: int = 49,
) -> Settings:
    storage_root = tmp_path / "storage"
    storage_tmp = tmp_path / "tmp"
    monkeypatch.setenv("STORAGE_PATH", str(storage_root))
    monkeypatch.setenv("STORAGE_TMP_PATH", str(storage_tmp))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://media.example.com")
    monkeypatch.setenv("BRAND_FOOTER", "Footer text")
    monkeypatch.setenv("TELEGRAM_MAX_UPLOAD_MB", str(telegram_max_upload_mb))
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _make_analyzed(*, estimated_size_bytes: int) -> tuple[AnalyzedMedia, DownloadOption]:
    option = DownloadOption(
        key="video_720",
        label="720p",
        kind=MediaKind.VIDEO,
        container="mp4",
        estimated_size_bytes=estimated_size_bytes,
    )
    info = MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="fake-yt-id",
        title="Demo title",
        kind=MediaKind.VIDEO,
        duration_sec=1.0,
        description="This description is long enough to expose the post-text button.",
        thumbnail_url="https://example.com/thumb.jpg",
    )
    return AnalyzedMedia(info=info, options=(option,)), option


def _make_delivery(
    *,
    settings: Settings,
    sender: _FakeTelegramSender,
    storage: LocalStorage,
    temp_links: _FakeTempLinks,
    post_text_store: _InMemoryPostTextStore,
) -> DeliveryService:
    return DeliveryService(
        settings=settings,
        sender=sender,  # type: ignore[arg-type]
        storage=storage,
        temp_links=temp_links,  # type: ignore[arg-type]
        post_text_store=post_text_store,  # type: ignore[arg-type]
    )


def _make_worker(
    *,
    repo: _InMemoryJobsRepo,
    provider: _FakeYtDlpProvider,
    storage: LocalStorage,
    delivery: DeliveryService,
    sender: _FakeTelegramSender,
    settings: Settings,
) -> ProcessDownloadUseCase:
    return ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=_FakeRegistry(provider),  # type: ignore[arg-type]
        storage=storage,
        delivery=delivery,
        sender=sender,  # type: ignore[arg-type]
        settings=settings,
    )


async def _enqueue_job(
    *,
    repo: _InMemoryJobsRepo,
    queue: _FakeQueue,
    provider: _FakeYtDlpProvider,
    post_text_store: _InMemoryPostTextStore,
    settings: Settings,
    analyzed: AnalyzedMedia,
) -> WorkerJobPayload:
    auto_enqueue = AutoEnqueueDownloadUseCase(
        providers=_FakeRegistry(provider),  # type: ignore[arg-type]
        jobs_repo=repo,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        progress_reporter=object(),  # type: ignore[arg-type]
        post_text_store=post_text_store,  # type: ignore[arg-type]
        max_concurrent_per_user=settings.MAX_CONCURRENT_JOBS_PER_USER,
        post_text_min_chars=settings.POST_TEXT_MIN_CHARS,
    )
    result = await auto_enqueue.execute(
        AutoEnqueueInput(
            user_id=1,
            chat_id=777,
            analyzed=analyzed,
            source_url="https://youtube.com/watch?v=fake",
            placeholder_message_id=555,
            correlation_id="corr-1",
        )
    )
    assert result.has_description is True
    assert queue.payloads
    return queue.payloads[0]


@pytest.mark.asyncio
async def test_happy_path_delivers_video_with_caption_and_post_text_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_settings(monkeypatch, tmp_path)
    storage = LocalStorage(settings)
    storage.init()
    repo = _InMemoryJobsRepo()
    queue = _FakeQueue()
    sender = _FakeTelegramSender()
    post_text_store = _InMemoryPostTextStore()
    temp_links = _FakeTempLinks(base_url=settings.PUBLIC_BASE_URL)
    analyzed, option = _make_analyzed(estimated_size_bytes=_FIXTURE_MP4.stat().st_size)
    provider = _FakeYtDlpProvider(info=analyzed.info, option=option)
    delivery = _make_delivery(
        settings=settings,
        sender=sender,
        storage=storage,
        temp_links=temp_links,
        post_text_store=post_text_store,
    )
    worker = _make_worker(
        repo=repo,
        provider=provider,
        storage=storage,
        delivery=delivery,
        sender=sender,
        settings=settings,
    )

    payload = await _enqueue_job(
        repo=repo,
        queue=queue,
        provider=provider,
        post_text_store=post_text_store,
        settings=settings,
        analyzed=analyzed,
    )

    await worker.execute(
        ProcessDownloadInput(job_id=payload.job_id, correlation_id=payload.correlation_id)
    )

    assert len(sender.videos) == 1
    sent = sender.videos[0]
    assert sent.chat_id == 777
    assert sent.caption is not None
    assert "Demo title" in sent.caption
    assert "Размер файла:" in sent.caption
    assert "Footer text" in sent.caption
    assert sent.reply_markup is not None
    assert sent.reply_markup.inline_keyboard[0][0].text == "Получить текст поста 👇"
    job = await repo.get(payload.job_id)
    assert job is not None
    assert job.status is JobStatus.DONE
    assert temp_links.issued == []


@pytest.mark.asyncio
async def test_download_timeout_is_reported_via_terminal_failure_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_settings(monkeypatch, tmp_path)
    storage = LocalStorage(settings)
    storage.init()
    repo = _InMemoryJobsRepo()
    queue = _FakeQueue()
    sender = _FakeTelegramSender()
    post_text_store = _InMemoryPostTextStore()
    temp_links = _FakeTempLinks(base_url=settings.PUBLIC_BASE_URL)
    analyzed, option = _make_analyzed(estimated_size_bytes=1024)
    provider = _FakeYtDlpProvider(info=analyzed.info, option=option, mode="timeout")
    delivery = _make_delivery(
        settings=settings,
        sender=sender,
        storage=storage,
        temp_links=temp_links,
        post_text_store=post_text_store,
    )
    worker = _make_worker(
        repo=repo,
        provider=provider,
        storage=storage,
        delivery=delivery,
        sender=sender,
        settings=settings,
    )

    payload = await _enqueue_job(
        repo=repo,
        queue=queue,
        provider=provider,
        post_text_store=post_text_store,
        settings=settings,
        analyzed=analyzed,
    )

    with pytest.raises(DownloadTimeoutError) as excinfo:
        await worker.execute(
            ProcessDownloadInput(job_id=payload.job_id, correlation_id=payload.correlation_id)
        )

    await worker.mark_terminally_failed(payload.job_id, excinfo.value)

    assert sender.videos == []
    assert len(sender.texts) == 1
    assert "Скачивание заняло слишком много времени." in sender.texts[0].text
    job = await repo.get(payload.job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED


@pytest.mark.asyncio
async def test_large_result_falls_back_to_temp_link_without_direct_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_settings(monkeypatch, tmp_path, telegram_max_upload_mb=1)
    storage = LocalStorage(settings)
    storage.init()
    repo = _InMemoryJobsRepo()
    queue = _FakeQueue()
    sender = _FakeTelegramSender()
    post_text_store = _InMemoryPostTextStore()
    temp_links = _FakeTempLinks(base_url=settings.PUBLIC_BASE_URL)
    analyzed, option = _make_analyzed(estimated_size_bytes=2 * 1024 * 1024)
    provider = _FakeYtDlpProvider(
        info=analyzed.info,
        option=option,
        output_size_bytes=2 * 1024 * 1024,
    )
    delivery = _make_delivery(
        settings=settings,
        sender=sender,
        storage=storage,
        temp_links=temp_links,
        post_text_store=post_text_store,
    )
    worker = _make_worker(
        repo=repo,
        provider=provider,
        storage=storage,
        delivery=delivery,
        sender=sender,
        settings=settings,
    )

    payload = await _enqueue_job(
        repo=repo,
        queue=queue,
        provider=provider,
        post_text_store=post_text_store,
        settings=settings,
        analyzed=analyzed,
    )

    await worker.execute(
        ProcessDownloadInput(job_id=payload.job_id, correlation_id=payload.correlation_id)
    )

    assert sender.videos == []
    assert len(sender.texts) == 1
    assert "https://media.example.com/d/temp-100" in sender.texts[0].text
    assert "Footer text" in sender.texts[0].text
    expected_path = str(storage.job_dir(payload.job_id) / "result.mp4")
    assert temp_links.issued == [(payload.job_id, expected_path)]
    job = await repo.get(payload.job_id)
    assert job is not None
    assert job.status is JobStatus.DONE

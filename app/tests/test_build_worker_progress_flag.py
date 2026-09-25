"""`build_worker()` respects `INSTANT_DOWNLOAD_ENABLED` for progress wiring."""

from __future__ import annotations

from types import SimpleNamespace

import app.composition.worker as composition_mod
from app.config import get_settings


class _FakeStorage:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.inited = False

    def init(self) -> None:
        self.inited = True


class _FakeJobsRepo:
    def __init__(self, sessionmaker) -> None:
        self.sessionmaker = sessionmaker


class _FakeTempLinksRepo:
    def __init__(self, sessionmaker) -> None:
        self.sessionmaker = sessionmaker


class _FakeSender:
    def __init__(self, settings) -> None:
        self.settings = settings


class _FakeTempLinkService:
    def __init__(self, *, settings, repo) -> None:
        self.settings = settings
        self.repo = repo


class _FakePostTextStore:
    def __init__(self, *, redis, settings) -> None:
        self.redis = redis
        self.settings = settings


class _FakeDeliveryService:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeRedisProgressReporter:
    def __init__(self, *, redis, redis_url, settings) -> None:
        self.redis = redis
        self.redis_url = redis_url
        self.settings = settings


class _FakeNoopProgressReporter:
    pass


class _FakeCancellationStore:
    def __init__(self, *, redis, settings) -> None:
        self.redis = redis
        self.settings = settings


class _FakeUseCase:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.progress_reporter = kwargs["progress_reporter"]


def _patch_worker_build(monkeypatch) -> None:
    core = SimpleNamespace(
        settings=None,
        redis=object(),
        engine=SimpleNamespace(),
        sessionmaker=object(),
    )
    monkeypatch.setattr(composition_mod, "build_core", lambda settings: core)
    monkeypatch.setattr(composition_mod, "build_provider_registry", lambda *_a, **_kw: object())
    monkeypatch.setattr(composition_mod, "LocalStorage", _FakeStorage)
    monkeypatch.setattr(composition_mod, "SqlAlchemyJobsRepository", _FakeJobsRepo)
    monkeypatch.setattr(composition_mod, "SqlAlchemyTempLinksRepository", _FakeTempLinksRepo)
    monkeypatch.setattr(composition_mod, "TelegramSender", _FakeSender)
    monkeypatch.setattr(composition_mod, "TempLinkService", _FakeTempLinkService)
    monkeypatch.setattr(composition_mod, "RedisPostTextStore", _FakePostTextStore)
    monkeypatch.setattr(composition_mod, "DeliveryService", _FakeDeliveryService)
    monkeypatch.setattr(composition_mod, "RedisProgressReporter", _FakeRedisProgressReporter)
    monkeypatch.setattr(composition_mod, "NoopProgressReporter", _FakeNoopProgressReporter)
    monkeypatch.setattr(composition_mod, "RedisJobCancellationStore", _FakeCancellationStore)
    monkeypatch.setattr(composition_mod, "ProcessDownloadUseCase", _FakeUseCase)
    monkeypatch.setattr(
        composition_mod,
        "build_metrics",
        lambda _settings: (object(), object(), None),
    )


def test_build_worker_uses_noop_reporter_when_feature_disabled(monkeypatch) -> None:
    _patch_worker_build(monkeypatch)
    settings = get_settings()
    object.__setattr__(settings, "INSTANT_DOWNLOAD_ENABLED", False)

    composition = composition_mod.build_worker(settings)

    assert isinstance(composition.progress_reporter, _FakeNoopProgressReporter)
    assert isinstance(composition.use_case.progress_reporter, _FakeNoopProgressReporter)


def test_build_worker_uses_redis_reporter_when_feature_enabled(monkeypatch) -> None:
    _patch_worker_build(monkeypatch)
    settings = get_settings()
    object.__setattr__(settings, "INSTANT_DOWNLOAD_ENABLED", True)

    composition = composition_mod.build_worker(settings)

    assert isinstance(composition.progress_reporter, _FakeRedisProgressReporter)
    assert isinstance(composition.use_case.progress_reporter, _FakeRedisProgressReporter)

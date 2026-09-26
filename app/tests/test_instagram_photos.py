"""Instagram photo posts: yt-dlp has no formats, the CDN image is fetched directly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.domain.entities.media_info import DownloadOption
from app.domain.enums import MediaKind
from app.exceptions import DownloadError
from app.infrastructure.providers import instagram as instagram_module
from app.infrastructure.providers.instagram import InstagramProvider
from app.infrastructure.storage.local_storage import LocalStorage

_IMG = "https://scontent-iad3-1.cdninstagram.com/v/t39.30808-6/{}.jpg?stp=dst-jpg_e35_tt6"


def _photo(entry_id: str) -> dict[str, Any]:
    small = _IMG.format(f"{entry_id}_s150")
    best = _IMG.format(entry_id)
    return {
        "id": entry_id,
        "title": "Video by someone",
        "formats": [],
        "thumbnail": best,
        "thumbnails": [{"url": small}, {"url": best}],
    }


def _video(entry_id: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "ext": "mp4",
        "vcodec": "avc1",
        "formats": [{"format_id": "1"}],
        "url": f"https://scontent.cdninstagram.com/{entry_id}.mp4",
    }


class _FakeYtDlp:
    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.extract_calls: list[dict[str, Any] | None] = []
        self.probe_calls: list[dict[str, Any] | None] = []
        self.download_calls: list[dict[str, Any] | None] = []

    async def extract_info(
        self, url: str, *, extra_opts: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.extract_calls.append(extra_opts)
        return self._raw

    async def probe_size(
        self, url: str, *, format_spec: str, extra_opts: dict[str, Any] | None = None
    ) -> int | None:
        self.probe_calls.append(extra_opts)
        return None

    async def download(
        self,
        url: str,
        *,
        format_spec: str,
        target_dir: Path,
        merge_output_format: str | None = None,
        extra_opts: dict[str, Any] | None = None,
        force_transcode: bool = False,
        on_progress: Any = None,
    ) -> list[Path]:
        self.download_calls.append(extra_opts)
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / "02_vid.mp4"
        out.write_bytes(b"video")
        return [out]


class _FakeImages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, url: str, *, target_dir: Path, stem: str) -> Path:
        self.calls.append((url, stem))
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stem}.jpg"
        path.write_bytes(b"jpeg")
        return path


def _provider(raw: dict[str, Any]) -> tuple[InstagramProvider, _FakeYtDlp, _FakeImages]:
    settings = get_settings()
    ytdlp = _FakeYtDlp(raw)
    images = _FakeImages()
    provider = InstagramProvider(
        settings=settings,
        ytdlp=ytdlp,  # type: ignore[arg-type]
        storage=LocalStorage(settings),
        image_fetcher=images,  # type: ignore[arg-type]
    )
    return provider, ytdlp, images


@pytest.mark.asyncio
async def test_photo_post_is_analysed_as_photo_with_largest_image_url() -> None:
    provider, ytdlp, _ = _provider(_photo("DdrzVBqDScp"))

    info = await provider.get_info("https://www.instagram.com/p/DdrzVBqDScp/")

    assert ytdlp.extract_calls == [{"ignore_no_formats_error": True}]
    assert info.kind is MediaKind.PHOTO
    assert info.items[0].url == _IMG.format("DdrzVBqDScp")
    assert [o.key for o in provider.build_options(info)] == ["single_photo"]


@pytest.mark.asyncio
async def test_photo_post_downloads_image_without_ytdlp_download(tmp_path: Path) -> None:
    provider, ytdlp, images = _provider(_photo("DdrzVBqDScp"))
    option = DownloadOption(key="single_photo", label="Скачать фото", kind=MediaKind.PHOTO)

    result = await provider.download(
        "https://www.instagram.com/p/DdrzVBqDScp/", option, target_dir=str(tmp_path / "job_1")
    )

    assert ytdlp.download_calls == []
    assert images.calls == [(_IMG.format("DdrzVBqDScp"), "DdrzVBqDScp")]
    assert result.kind is MediaKind.PHOTO
    assert [Path(f).name for f in result.files] == ["DdrzVBqDScp.jpg"]


@pytest.mark.asyncio
async def test_mixed_carousel_fetches_photos_and_downloads_only_video_positions(
    tmp_path: Path,
) -> None:
    raw = {
        "id": "post",
        "title": "Post by someone",
        "entries": [_photo("a"), _video("vid"), _photo("c")],
    }
    provider, ytdlp, images = _provider(raw)
    option = DownloadOption(key="gallery_all", label="Скачать всё (3)", kind=MediaKind.GALLERY)
    job_dir = tmp_path / "job_2"

    result = await provider.download(
        "https://www.instagram.com/p/post/", option, target_dir=str(job_dir)
    )

    assert len(ytdlp.download_calls) == 1
    assert ytdlp.download_calls[0] is not None
    assert ytdlp.download_calls[0]["playlist_items"] == "2"
    assert ytdlp.download_calls[0]["outtmpl"] == str(
        job_dir / "%(playlist_index)02d_%(id)s.%(ext)s"
    )
    assert [stem for _, stem in images.calls] == ["01_a", "03_c"]
    assert [Path(f).name for f in result.files] == ["01_a.jpg", "02_vid.mp4", "03_c.jpg"]
    assert result.kind is MediaKind.GALLERY


@pytest.mark.asyncio
async def test_carousel_video_only_option_skips_photos(tmp_path: Path) -> None:
    raw = {"id": "post", "entries": [_photo("a"), _video("vid")]}
    provider, ytdlp, images = _provider(raw)
    option = DownloadOption(key="gallery_videos", label="Только видео", kind=MediaKind.VIDEO)

    result = await provider.download(
        "https://www.instagram.com/p/post/", option, target_dir=str(tmp_path / "job_3")
    )

    assert images.calls == []
    assert [(call or {}).get("playlist_items") for call in ytdlp.download_calls] == ["2"]
    assert [Path(f).name for f in result.files] == ["02_vid.mp4"]


@pytest.mark.asyncio
async def test_photo_downloads_from_analysed_info_without_extraction(tmp_path: Path) -> None:
    provider, ytdlp, images = _provider(_photo("DdrzVBqDScp"))
    info = await provider.get_info("https://www.instagram.com/p/DdrzVBqDScp/")
    ytdlp.extract_calls.clear()

    result = await provider.download(
        "https://www.instagram.com/p/DdrzVBqDScp/",
        provider.default_option(info),
        target_dir=str(tmp_path / "job_4"),
        info=info,
    )

    assert ytdlp.extract_calls == []
    assert images.calls == [(_IMG.format("DdrzVBqDScp"), "DdrzVBqDScp")]
    assert [Path(f).name for f in result.files] == ["DdrzVBqDScp.jpg"]


class _ExpiredThenOkImages(_FakeImages):
    async def fetch(self, url: str, *, target_dir: Path, stem: str) -> Path:
        if not self.calls:
            self.calls.append((url, stem))
            raise DownloadError("Image download failed: 403 Forbidden")
        return await super().fetch(url, target_dir=target_dir, stem=stem)


@pytest.mark.asyncio
async def test_expired_analysed_image_url_falls_back_to_fresh_extraction(tmp_path: Path) -> None:
    settings = get_settings()
    ytdlp = _FakeYtDlp(_photo("DdrzVBqDScp"))
    images = _ExpiredThenOkImages()
    provider = InstagramProvider(
        settings=settings,
        ytdlp=ytdlp,  # type: ignore[arg-type]
        storage=LocalStorage(settings),
        image_fetcher=images,  # type: ignore[arg-type]
    )
    info = await provider.get_info("https://www.instagram.com/p/DdrzVBqDScp/")
    ytdlp.extract_calls.clear()

    result = await provider.download(
        "https://www.instagram.com/p/DdrzVBqDScp/",
        provider.default_option(info),
        target_dir=str(tmp_path / "job_5"),
        info=info,
    )

    assert len(ytdlp.extract_calls) == 1
    assert len(images.calls) == 2
    assert [Path(f).name for f in result.files] == ["DdrzVBqDScp.jpg"]


@pytest.mark.asyncio
async def test_photo_size_is_probed_by_head_without_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, ytdlp, _ = _provider(_photo("DdrzVBqDScp"))
    info = await provider.get_info("https://www.instagram.com/p/DdrzVBqDScp/")
    heads: list[str] = []

    def _fake_head(url: str, *, proxy_url: str | None) -> int:
        heads.append(url)
        return 393_987

    monkeypatch.setattr(instagram_module, "head_content_length", _fake_head)

    size = await provider.probe_size(
        "https://www.instagram.com/p/DdrzVBqDScp/",
        info=info,
        option=provider.default_option(info),
    )

    assert size == 393_987
    assert heads == [_IMG.format("DdrzVBqDScp")]
    assert ytdlp.probe_calls == []

"""URL detection / platform routing."""

from __future__ import annotations

import pytest

from app.domain.enums import Platform
from app.exceptions import InvalidUrlError, UnsupportedPlatformError
from app.utils.url import detect, detect_platform, extract_first_url, normalize_domain


class TestExtractFirstUrl:
    def test_returns_url_in_freeform_text(self) -> None:
        assert (
            extract_first_url("посмотри https://youtu.be/abc?x=1 круто")
            == "https://youtu.be/abc?x=1"
        )

    def test_returns_none_for_text_without_url(self) -> None:
        assert extract_first_url("привет мир") is None

    def test_returns_none_for_empty(self) -> None:
        assert extract_first_url("") is None


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "url",
        [
            "https://youtube.com/watch?v=abc",
            "https://www.youtube.com/watch?v=abc",
            "https://m.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
        ],
    )
    def test_youtube(self, url: str) -> None:
        assert detect_platform(url) is Platform.YOUTUBE

    @pytest.mark.parametrize(
        "url",
        [
            "https://instagram.com/p/abc",
            "https://www.instagram.com/reel/xyz",
            "https://m.instagram.com/p/abc",
        ],
    )
    def test_instagram(self, url: str) -> None:
        assert detect_platform(url) is Platform.INSTAGRAM

    def test_unsupported_host_raises(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            detect_platform("https://tiktok.com/@a/video/1")

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            detect_platform("youtube.com/watch")


class TestDetect:
    def test_bare_url(self) -> None:
        d = detect("https://youtu.be/dQw4w9WgXcQ")
        assert d.platform is Platform.YOUTUBE
        assert d.normalized == "https://youtu.be/dQw4w9WgXcQ"

    def test_url_in_text(self) -> None:
        d = detect("Look! https://www.instagram.com/p/abc/ amazing")
        assert d.platform is Platform.INSTAGRAM

    def test_empty_input_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            detect("   ")

    def test_no_url_in_text_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            detect("просто текст без ссылки")


class TestNormalizeDomain:
    """L4 (per-domain) limiter — see docs/36- §3.2."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://youtu.be/abc", "youtube.com"),
            ("https://youtube.com/watch?v=abc", "youtube.com"),
            ("https://www.youtube.com/watch?v=abc", "youtube.com"),
            ("https://m.youtube.com/watch?v=abc", "youtube.com"),
            ("https://music.youtube.com/watch?v=abc", "youtube.com"),
            ("https://instagram.com/p/x", "instagram.com"),
            ("https://www.instagram.com/p/x", "instagram.com"),
            ("https://m.instagram.com/p/x", "instagram.com"),
        ],
    )
    def test_known_providers_collapse_to_etld_plus_one(self, url: str, expected: str) -> None:
        assert normalize_domain(url) == expected

    def test_unknown_host_strips_www(self) -> None:
        assert normalize_domain("https://www.example.com/x") == "example.com"

    def test_unknown_host_kept_as_is(self) -> None:
        assert normalize_domain("https://example.org/x") == "example.org"

    def test_strips_port(self) -> None:
        assert normalize_domain("https://example.com:8080/x") == "example.com"

    def test_returns_none_for_blank(self) -> None:
        assert normalize_domain("") is None
        assert normalize_domain("not-a-url") is None

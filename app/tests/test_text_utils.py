"""Unit tests for :mod:`app.domain.text_utils`."""

from __future__ import annotations

import pytest

from app.domain.text_utils import truncate_description


class TestTruncateDescription:
    def test_none_returns_empty(self) -> None:
        assert truncate_description(None, max_chars=100) == ""

    def test_empty_returns_empty(self) -> None:
        assert truncate_description("", max_chars=100) == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert truncate_description("   \n\t  ", max_chars=100) == ""

    def test_strips_surrounding_whitespace(self) -> None:
        assert truncate_description("  hi  ", max_chars=100) == "hi"

    def test_under_budget_passthrough(self) -> None:
        text = "abc def"
        assert truncate_description(text, max_chars=100) == text

    def test_exact_budget_passthrough(self) -> None:
        text = "a" * 10
        assert truncate_description(text, max_chars=10) == text

    def test_over_budget_cuts_at_word_boundary(self) -> None:
        text = "one two three four five"
        out = truncate_description(text, max_chars=15)
        # Ellipsis preserved; no trailing space before marker.
        assert out.endswith("…")
        assert "  " not in out
        assert len(out) <= 15

    def test_over_budget_no_word_boundary_hard_cut(self) -> None:
        text = "a" * 100  # no spaces at all
        out = truncate_description(text, max_chars=10)
        assert len(out) == 10
        assert out.endswith("…")

    def test_very_small_budget_returns_hard_cut(self) -> None:
        out = truncate_description("hello world", max_chars=1)
        assert len(out) <= 1

    @pytest.mark.parametrize("budget", [0, -1, -1000])
    def test_non_positive_budget_returns_empty(self, budget: int) -> None:
        assert truncate_description("text", max_chars=budget) == ""

    def test_unicode_passthrough(self) -> None:
        text = "Привет мир"
        assert truncate_description(text, max_chars=100) == text

    def test_unicode_truncation(self) -> None:
        text = "Привет красивый мир"
        out = truncate_description(text, max_chars=10)
        # Should not crash, should contain ellipsis, length ≤ max_chars.
        assert out.endswith("…")
        assert len(out) <= 10

    def test_boundary_half_budget_fallback_to_hard_cut(self) -> None:
        # Space appears before the halfway mark → hard cut chosen.
        text = "x" + " " + ("y" * 100)
        out = truncate_description(text, max_chars=20)
        # The space is at position 1 which is < budget // 2, so we do
        # NOT trim at it; we hard cut and append ellipsis.
        assert out.endswith("…")
        assert len(out) <= 20

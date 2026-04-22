"""Pure text helpers used by the domain/application layers.

No IO, no logging. Safe to call from anywhere including domain entities.
"""

from __future__ import annotations


def truncate_description(text: str | None, *, max_chars: int) -> str:
    """Return a safe, bounded representation of a user-facing description.

    - ``None`` / empty / whitespace-only collapses to ``""``.
    - Leading/trailing whitespace is stripped so the first/last line is
      never blank.
    - If the stripped text fits into ``max_chars`` it is returned as-is.
    - Otherwise the text is cut at the last word boundary (space) that
      still leaves room for the ellipsis marker. If no sensible boundary
      exists within the window, a hard cut is made.

    The helper is platform-agnostic: YouTube descriptions and Instagram
    captions both flow through here. Keeping the rule in one place makes
    the ``MediaInfo.description`` invariant (bounded, trimmed, no None)
    cheap to verify from tests (see ``test_text_utils.py``) and avoids
    divergence between providers.
    """
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if max_chars <= 0:
        return ""
    if len(stripped) <= max_chars:
        return stripped

    marker = "…"
    budget = max_chars - len(marker)
    if budget <= 0:
        return stripped[:max_chars]

    window = stripped[:budget]
    cut = window.rfind(" ")
    if cut >= budget // 2:
        window = window[:cut].rstrip()
    return window + marker

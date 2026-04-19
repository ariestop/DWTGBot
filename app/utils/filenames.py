"""Safe filename utilities — sanitization and path-traversal protection."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.exceptions import StorageError

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
_MULTI_DOT_RE = re.compile(r"\.{2,}")
_WS_RE = re.compile(r"\s+")

MAX_NAME_LEN = 120


def sanitize_filename(name: str, *, fallback: str = "media") -> str:
    """
    Make a filesystem-safe filename:
      - normalize unicode
      - strip control chars and shell-unfriendly characters
      - collapse whitespace
      - prevent leading dots / traversal artefacts
      - truncate to MAX_NAME_LEN, preserving extension
    """
    if not name:
        return fallback

    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = "".join(ch for ch in cleaned if not unicodedata.category(ch).startswith("C"))
    cleaned = _SAFE_CHARS_RE.sub("_", cleaned)
    cleaned = _MULTI_DOT_RE.sub(".", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" ._-")

    if not cleaned:
        cleaned = fallback

    # Preserve extension if reasonable.
    if "." in cleaned:
        stem, _, ext = cleaned.rpartition(".")
        if 0 < len(ext) <= 8 and ext.isalnum():
            keep = MAX_NAME_LEN - len(ext) - 1
            return f"{stem[:keep]}.{ext.lower()}" if keep > 0 else cleaned[:MAX_NAME_LEN]
    return cleaned[:MAX_NAME_LEN]


def ensure_within(base: Path, candidate: Path) -> Path:
    """
    Resolve `candidate` and ensure it is inside `base`. Raises StorageError otherwise.
    Used to defend against path traversal in any code that consumes user-influenced names.
    """
    base_resolved = base.resolve()
    target_resolved = (base / candidate if not candidate.is_absolute() else candidate).resolve()
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise StorageError(
            f"Path traversal blocked: {target_resolved} not within {base_resolved}"
        ) from exc
    return target_resolved

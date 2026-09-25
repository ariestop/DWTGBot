"""yt-dlp must ship with its YouTube JS-challenge solver and runtime.

Without ``yt-dlp-ejs`` + ``deno`` extraction still succeeds but every
YouTube media URL answers HTTP 403, so the failure only shows up in prod.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REQ = Path(__file__).resolve().parents[2] / "requirements"


def _pinned(lock: str) -> set[str]:
    text = (_REQ / lock).read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z0-9][a-z0-9._-]*)==", text, flags=re.MULTILINE))


@pytest.mark.parametrize("lock", ["prod.lock", "dev.lock"])
def test_lock_ships_youtube_js_challenge_solver(lock: str) -> None:
    assert {"yt-dlp", "yt-dlp-ejs", "deno"} <= _pinned(lock)


def test_base_requirement_keeps_extras_and_no_exact_pin() -> None:
    line = next(
        ln
        for ln in (_REQ / "base.txt").read_text(encoding="utf-8").splitlines()
        if ln.startswith("yt-dlp")
    )
    assert re.match(r"^yt-dlp\[default,deno\]>=", line), line

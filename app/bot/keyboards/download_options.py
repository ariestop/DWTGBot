"""Inline keyboard builders."""

from __future__ import annotations

from collections.abc import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks.codec import DownloadCallback, encode_cancel
from app.domain.entities.media_info import DownloadOption
from app.domain.enums import MediaKind


def build_options_keyboard(
    request_id: str,
    options: Iterable[DownloadOption],
) -> InlineKeyboardMarkup:
    """
    Build a keyboard with one button per download option, plus a cancel row.

    Buttons are ordered: video qualities (asc), then audio, then photo/gallery.
    """
    sorted_opts = sorted(options, key=_sort_key)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for opt in sorted_opts:
        cb = DownloadCallback(request_id=request_id, option_key=opt.key).encode()
        row.append(InlineKeyboardButton(text=_button_label(opt), callback_data=cb))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=encode_cancel(request_id))])
    return InlineKeyboardMarkup(rows)


def _button_label(opt: DownloadOption) -> str:
    base = opt.label
    if opt.estimated_size_bytes:
        mb = opt.estimated_size_bytes / 1024 / 1024
        base = f"{base} · ~{mb:.0f} MB"
    return base


_KIND_ORDER = {
    MediaKind.VIDEO: 0,
    MediaKind.AUDIO: 1,
    MediaKind.PHOTO: 2,
    MediaKind.GALLERY: 3,
}


def _sort_key(opt: DownloadOption) -> tuple[int, int]:
    return (_KIND_ORDER.get(opt.kind, 99), opt.height or 0)

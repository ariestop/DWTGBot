"""Text of the live-progress placeholder caption (ADR-0010 §2.2).

Pure functions: no Telegram or Redis access, so the wording can be tested
and changed without touching the updater's I/O loops.
"""

from __future__ import annotations

from app.domain.enums import ProgressStage

STAGE_PHRASES: dict[ProgressStage, str] = {
    ProgressStage.ANALYZING: "Анализирую ссылку…",
    ProgressStage.DOWNLOADING: "Скачиваю",
    ProgressStage.PROCESSING: "Обрабатываю",
    ProgressStage.UPLOADING: "Загружаю",
    ProgressStage.DONE: "Готово",
    ProgressStage.CANCELLED: "Отменено",
    ProgressStage.FAILED: "Не удалось скачать",
}

# yt-dlp's ``progress_hooks`` emit only during the "downloading" status.
# HLS fragment transitions and the postprocess merge step (Instagram,
# some YouTube formats) therefore produce routine silent gaps where the
# worker is perfectly healthy and ffmpeg is just muxing. A "lost
# connection" wording read like a crash to users even when the download
# finished seconds later — describe the real situation instead.
STALE_CAPTION = "Скачивание продолжается, это может занять ещё немного времени…"

_STAGES_WITH_BAR = frozenset(
    {ProgressStage.DOWNLOADING, ProgressStage.PROCESSING, ProgressStage.UPLOADING}
)


def render_bar(percent: float, *, width: int = 10) -> str:
    filled = max(0, min(width, round(percent / 100.0 * width)))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def render_caption(stage: ProgressStage, percent: float, *, reason: str | None = None) -> str:
    """Caption shown on the placeholder message.

    Kept to one line on small screens; the bar has 10 cells (~10 % each).
    Terminal stages have no bar.
    """
    phrase = STAGE_PHRASES.get(stage, "...")
    if stage in _STAGES_WITH_BAR:
        return f"{phrase}… {render_bar(percent)} {int(percent)} %"
    if stage is ProgressStage.FAILED and reason:
        return f"{phrase}: {reason}"
    return phrase

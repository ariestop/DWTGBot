#!/usr/bin/env python3
"""Redacted digest of DWTGBot container logs, safe to print in a public CI log.

Reads ``<container> | <line>`` records on stdin (see diagnostics.sh) and
prints error/warning counts per container, the most frequent events and
the latest error records. Only whitelisted structlog fields are kept, and
every printed line goes through :func:`redact` — user/chat ids, media
URLs, file names, tokens and passwords never reach the output.

    ... | python3 log_digest.py [--last N]   # digest of container logs
    ... | python3 log_digest.py --plain      # redact arbitrary text

Stdlib only: runs on the host's python3 or inside any project image.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, deque

# Fields of a JSON record that may be printed (after redaction). Context
# such as user_id, chat_id, url, title or file_path is dropped.
SAFE_FIELDS = (
    "error",
    "error_type",
    "exc_type",
    "reason",
    "provider",
    "stage",
    "status",
    "attempt",
)
SERIOUS = {"error", "critical", "exception", "fatal"}
PLAIN_ERROR_RE = re.compile(
    r"traceback|error|exception|fatal|critical|panic|refused|denied|failed|unhealthy", re.I
)
MAX_LINE = 400
MAX_EXC_LINES = 25

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # pydantic ValidationError echoes the offending value.
    (re.compile(r"input_value=.*?(?=, input_type=|$)"), "input_value=<redacted>"),
    # key=value / "key": "value" pairs that carry secrets.
    (
        re.compile(
            r"(?i)\b((?:[a-z_]*_)?(?:password|passwd|token|secret|api_key|cookie|authorization))"
            r"(\"?\s*[:=]\s*\"?)[^\s\"',}]+"
        ),
        r"\1\2<redacted>",
    ),
    # Telegram bot token, bare or inside an api.telegram.org path.
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<tg-token>"),
    # URLs: keep scheme + host, drop credentials, path and query.
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)(?:[^/\s@]*@)?([^/\s:?#\"']+)[^\s\"'<>)]*"), r"\1\2/…"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<uuid>",
    ),
    # Media files carry the user's video/photo title in the name.
    (
        re.compile(
            r"[^\s\"'=]*\.(?:mp4|mp3|m4a|webm|mkv|mov|jpe?g|png|webp|heic|zip|part|ytdl)\b", re.I
        ),
        "<media-file>",
    ),
    # Opaque tokens / ids: long runs that mix letters and digits.
    (re.compile(r"\b(?=[A-Za-z_-]*\d)(?=[\d_-]*[A-Za-z])[A-Za-z0-9_-]{20,}\b"), "<id>"),
    # user_id / chat_id / message_id style numbers.
    (re.compile(r"(?<![\w.])-?\d{6,}\b"), "<n>"),
]

_TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s+")


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    if len(text) > MAX_LINE:
        text = text[:MAX_LINE] + "…"
    return text


def _split(raw: str) -> tuple[str, str]:
    """``"dwtgbot_api | 2026-…Z {…}"`` → ``("dwtgbot_api", "{…}")``."""
    container, sep, line = raw.partition(" | ")
    if not sep:
        return "?", raw
    return container.strip(), _TS_RE.sub("", line, count=1)


def _format_record(container: str, rec: dict[str, object]) -> list[str]:
    head = " ".join(
        str(part)
        for part in (
            rec.get("timestamp", ""),
            str(rec.get("level", "")).upper(),
            container,
            rec.get("logger", ""),
            rec.get("event", ""),
        )
        if part
    )
    extras = " ".join(f"{k}={rec[k]}" for k in SAFE_FIELDS if k in rec)
    lines = [redact(f"{head} {extras}".rstrip())]
    exc = rec.get("exception")
    if isinstance(exc, str) and exc:
        exc_lines = exc.strip().splitlines()
        if len(exc_lines) > MAX_EXC_LINES:
            exc_lines = ["…", *exc_lines[-MAX_EXC_LINES:]]
        lines.extend("    " + redact(x) for x in exc_lines)
    return lines


def digest(stream: list[str], last: int) -> list[str]:
    levels: Counter[tuple[str, str]] = Counter()
    events: Counter[tuple[str, str, str]] = Counter()
    latest: deque[list[str]] = deque(maxlen=last)
    plain: deque[str] = deque(maxlen=last)
    containers: Counter[str] = Counter()

    for raw in stream:
        container, line = _split(raw.rstrip("\n"))
        if not line.strip():
            continue
        containers[container] += 1
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        if isinstance(rec, dict) and "event" in rec:
            level = str(rec.get("level", "info")).lower()
            if level in SERIOUS or level == "warning":
                levels[(container, level)] += 1
                events[(container, level, str(rec.get("event", "")))] += 1
            if level in SERIOUS:
                latest.append(_format_record(container, rec))
        elif PLAIN_ERROR_RE.search(line) or line.startswith(("  File ", "    ")):
            plain.append(f"{container} | {redact(line)}")

    out = ["== Lines per container =="]
    out += [f"  {c}: {n}" for c, n in sorted(containers.items())] or ["  (no log lines)"]
    out.append("== Errors / warnings per container ==")
    out += [f"  {c} {lvl}: {n}" for (c, lvl), n in sorted(levels.items())] or ["  none"]
    out.append("== Top events (error/warning) ==")
    out += [f"  {n:>5}  {c} {lvl} {redact(ev)}" for (c, lvl, ev), n in events.most_common(20)] or [
        "  none"
    ]
    out.append(f"== Latest {last} error records ==")
    for rec_lines in latest:
        out.extend(rec_lines)
    if not latest:
        out.append("  none")
    out.append(f"== Latest {last} non-JSON error-looking lines (startup crashes, tracebacks) ==")
    out += list(plain) or ["  none"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plain", action="store_true", help="only redact stdin line by line")
    parser.add_argument("--last", type=int, default=30, help="error records to show (default 30)")
    args = parser.parse_args(argv)
    if args.plain:
        for line in sys.stdin:
            print(redact(line.rstrip("\n")))
        return 0
    for line in digest(list(sys.stdin), args.last):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

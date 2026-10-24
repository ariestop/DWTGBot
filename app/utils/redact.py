"""Strip URLs out of log and error-report text (P11).

A source URL tells what a user downloaded, and a URL's path or query can
carry a credential: httpx logs Telegram calls as
``https://api.telegram.org/bot<token>/getUpdates``. Every URL is cut to
``scheme://host/…`` — enough to see which service a line is about.
Same format as ``deploy/scripts/log_digest.py``. Stdlib only: imported
by ``app.logging_config``, which nearly every module imports.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

# scheme, optional user:password@ (dropped), host; the rest (port, path,
# query, fragment) is replaced.
_URL_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)(?:[^/\s@]*@)?([^/\s:?#\"'<>]+)[^\s\"'<>)\]]*", re.I)


def redact_urls(text: str) -> str:
    """Replace every URL in ``text`` with ``scheme://host/…``."""
    if "://" not in text:
        return text
    return _URL_RE.sub(lambda m: f"{m.group(1).lower()}{m.group(2).lower()}/…", text)


def redact_event_urls(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact URLs in every string value of the event.

    Runs after ``format_exc_info``, so rendered tracebacks are covered.
    """
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_urls(value)
    return event_dict

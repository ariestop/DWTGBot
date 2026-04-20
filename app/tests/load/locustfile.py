"""
Locust load-test harness for DWTGBot (L11).

Opt-in: install Locust separately (``pip install locust``) — it is
deliberately **not** in ``requirements/dev.txt`` to keep the baseline
image lean.

Headline use::

    export INTERNAL_TEST_TOKEN=...
    locust -f app/tests/load/locustfile.py --headless \\
        -u 20 -r 2 -t 5m --host http://localhost:8080

What it does:
  * Each simulated user posts one ``/internal/test/enqueue`` call
    every ``wait_time`` seconds.
  * Locust reports p50/p95/p99 of the *enqueue latency* (time to
    get HTTP 200, **not** time to download). Enqueue latency is the
    A1 SLO proxy (``bot → DB INSERT → arq push`` happy path).
  * To measure A4/A5 (end-to-end), watch the dashboards during the
    run — Locust can't poll for job completion without extra wiring.

Safety knobs:
  * ``TEST_URL`` defaults to the 18-second "Me at the zoo" clip.
    Override via env var for staging-specific URLs.
  * Uses a synthetic ``user_id`` per simulated user so the
    ``MAX_CONCURRENT_JOBS_PER_USER`` cap doesn't mask system-level
    throughput (see ``docs/37`` §6.2 for the rationale).

The module intentionally has no ``pytest`` collector — we do not
want CI to accidentally run a Locust scenario against a bot.
"""

from __future__ import annotations

import os
import random

try:
    from locust import HttpUser, between, task
except ImportError as exc:  # pragma: no cover - optional dep
    raise SystemExit(
        "locustfile requires `pip install locust` — intentionally "
        "kept out of requirements/dev.txt (L11 is opt-in)",
    ) from exc

_TEST_URL = os.environ.get(
    "TEST_URL",
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
)
_TOKEN = os.environ.get("INTERNAL_TEST_TOKEN")
if not _TOKEN:
    raise SystemExit(
        "set INTERNAL_TEST_TOKEN to the value from your staging .env — "
        "the test endpoint returns 404 without it",
    )


class EnqueueUser(HttpUser):
    """One simulated Telegram user firing enqueues at the internal endpoint.

    The on-host bot would also go through rate-limit (``36-``), user-cap
    (``7.3``), and state-store writes. The internal endpoint hits
    *exactly* the same ``EnqueueDownloadUseCase`` and so exercises the
    real critical path — minus PTB polling, which is not the bottleneck
    per ``37-`` §1.
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        # Assign this simulated user a stable random ``user_id`` for the
        # duration of the session. Different user_ids across workers
        # prevent the per-user concurrency cap from gating the test
        # before the upstream bottleneck does.
        # S311 is not a concern: these IDs seed a load-test only and
        # don't cross a trust boundary.
        self._user_id = random.randint(10_000, 10_000_000)  # noqa: S311
        self._chat_id = self._user_id

    @task
    def enqueue(self) -> None:
        self.client.post(
            "/internal/test/enqueue",
            headers={"X-Internal-Test-Token": _TOKEN or ""},
            json={
                "user_id": self._user_id,
                "chat_id": self._chat_id,
                "source_url": _TEST_URL,
                "platform": "youtube",
                "selected_option_key": "video_360",
            },
            name="/internal/test/enqueue",
        )

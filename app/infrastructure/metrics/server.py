"""In-process ``/metrics`` HTTP server.

Runs alongside the bot (``app.main_bot``) so the limiter and the
exporter share the same process and the same metric instances. The
alternative — a separate sidecar container that scrapes Redis itself —
was rejected in ADR-0006 because we'd lose the per-decision Histogram
data (Redis only stores counters).

Security: bound to ``METRICS_BIND_HOST`` (default ``127.0.0.1``). In
production we expose the port only inside the private NL-1 ↔ NL-2
network via Docker networking; ``Settings.validate_runtime`` refuses
``0.0.0.0`` in production unless the operator overrides it explicitly
(this matches docs/35- §7's "private port, not 80/443" rule).
"""

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from app.logging_config import get_logger

_logger = get_logger(__name__)


def _build_metrics_app(registry: CollectorRegistry) -> Any:
    """Return a tiny ASGI app exposing GET /metrics.

    We do **not** pull in FastAPI here — the route is one path, no
    routing table needed, and we don't want metrics to share lifespan
    state with anything else.
    """

    async def app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "/")
        method = scope.get("method", "GET")
        if method != "GET" or path != "/metrics":
            await _send_plain(send, 404, b"not found\n")
            return
        body = generate_latest(registry)
        await _send_plain(
            send,
            200,
            body,
            content_type=CONTENT_TYPE_LATEST.encode("ascii"),
        )

    return app


async def _send_plain(
    send: Any,
    status: int,
    body: bytes,
    *,
    content_type: bytes = b"text/plain; charset=utf-8",
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type)],
        }
    )
    await send({"type": "http.response.body", "body": body})


class MetricsServer:
    """Owns the uvicorn task. ``start`` is non-blocking; ``stop`` joins."""

    def __init__(
        self,
        *,
        registry: CollectorRegistry,
        host: str,
        port: int,
    ) -> None:
        config = uvicorn.Config(
            app=_build_metrics_app(registry),
            host=host,
            port=port,
            log_level="warning",  # uvicorn access logs would dwarf bot logs
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._task: asyncio.Task[None] | None = None
        self._host = host
        self._port = port

    async def start(self) -> None:
        if self._task is not None:
            return
        _logger.info("metrics_server_starting", host=self._host, port=self._port)
        self._task = asyncio.create_task(self._server.serve(), name="metrics-server")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except TimeoutError:
            _logger.warning("metrics_server_stop_timeout")
            self._task.cancel()
        finally:
            self._task = None
            _logger.info("metrics_server_stopped")

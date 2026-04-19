"""Metrics infrastructure (Prometheus exporter + ASGI server).

The application layer talks to ``app.application.services.rate_limit_metrics
.RateLimitMetrics`` (a Protocol). This package contains the *only*
``prometheus_client`` import in the codebase — keep it that way (P7: no
infra leakage into domain/application).
"""

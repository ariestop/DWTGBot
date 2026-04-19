# ADR-0006 — In-process Prometheus exporter for rate-limit metrics

- **Status:** Accepted
- **Date:** 2026-04-19
- **Deciders:** project owner, original architect
- **Tags:** observability, metrics, rate-limit, deployment, security
- **Supersedes:** —
- **Superseded by:** —

---

## 1. Context

[`docs/36-rate-limiting.md`](../36-rate-limiting.md) §8 specifies four
metrics for the layered limiter:

- `rate_limit_decisions_total{decision, layer}` — counter
- `rate_limit_retry_after_seconds{layer}` — histogram
- `rate_limit_redis_errors_total{kind}` — counter
- `rate_limit_first_deny_total{layer[, user_id]}` — counter (used by §8.1
  "noisy users" topk)

[`docs/35-metrics-and-slo.md`](../35-metrics-and-slo.md) §7 reserves the
right to add a Prometheus exporter when log-based SLI queries become too
expensive, and gives three acceptance criteria:

1. The exporter lives in `deploy/nl1/docker-compose.yml` (or NL-2 sibling).
2. `/metrics` is on a **private** port — never 80/443.
3. An ADR records the decision and updates §3 / §4 / §6 of `35-` with the
   new metric names.

Non-goals:

- We are **not** introducing a Prometheus *server* / Grafana / scraping
  topology in this ADR. Operators install Prometheus separately.
- We are **not** publishing job/queue/worker metrics yet. Those come in a
  follow-up ADR; the registry is structured to accept them without
  re-architecting.
- We are **not** changing P11 (logs are the canonical signal). Metrics
  are additive.

---

## 2. Decision

We expose `/metrics` from **inside the bot process** (`app.main_bot`),
not from a sidecar container.

Concretely:

> The bot owns a `prometheus_client.CollectorRegistry`, instantiates
> `PrometheusRateLimitMetrics` against it, and serves the registry over
> HTTP via a tiny ASGI app embedded in the same event loop
> (`app/infrastructure/metrics/server.py`). The port is bound to
> `127.0.0.1` by default; production deploys publish it only on the
> internal Docker network.

Code locations:

- Port: `app/application/services/rate_limit_metrics.py`
- Implementation: `app/infrastructure/metrics/prometheus_metrics.py`
- HTTP server: `app/infrastructure/metrics/server.py`
- Wiring: `app/composition.py:_build_metrics`
- Lifecycle: `app/main_bot.py` (start before `application.start`, stop
  in the `finally` block)
- Settings: `METRICS_ENABLED`, `METRICS_BIND_HOST`, `METRICS_PORT`,
  `METRICS_USER_ID_LABEL` in `app/config.py`

Default state: `METRICS_ENABLED=false`. The deploy template flips it on
once Prometheus is provisioned.

---

## 3. Consequences

### 3.1 Positive

- Metric values come from the same Python objects that take the
  decisions — no duplicated state in Redis, no race between increment
  and scrape.
- One process to deploy, one set of secrets, one PID. No new sidecar
  container, no new image, no new restart policy.
- Histogram data (`rate_limit_retry_after_seconds`) is preserved. A
  Redis-only sidecar would have to recompute distributions from raw
  counters and would lose precision.
- The exporter shares the bot's event loop, so back-pressure on
  `/metrics` (e.g. a stuck Prometheus scrape) cannot starve a separate
  process — it just delays the next scrape.

### 3.2 Negative / accepted trade-offs

- Adds `prometheus-client==0.21.0` and `uvicorn` (already present) to
  the bot image. Cost: ~250 KB wheel; negligible runtime overhead
  (Counter increments are atomic Python ops).
- The `rate_limit_first_deny_total{user_id}` series is **per-user
  cardinality** when `METRICS_USER_ID_LABEL=true`. For our scale
  (hundreds of active users/day) this is fine; for any future
  public-scale rollout the label MUST be turned off and the topk query
  switched to LogQL on the `rate_limit_first_deny` log event (see
  [`36-`](../36-rate-limiting.md) §8.1).
- Restarting the bot resets all counters. We accept this — `rate()`
  queries don't care, and Prometheus survives counter resets natively.
- A bug in the bot loop *could* freeze `/metrics` scrapes. Mitigated by
  the standard `up == 0` alert from
  [`35-`](../35-metrics-and-slo.md) §6.2.

### 3.3 Operational impact

- New env block in `deploy/nl1/.env.example`:
  `METRICS_ENABLED`, `METRICS_BIND_HOST`, `METRICS_PORT`,
  `METRICS_USER_ID_LABEL`.
- `Settings.validate_runtime` rejects `METRICS_BIND_HOST=0.0.0.0` in
  production unless explicitly overridden. This enforces the §7
  "private port" rule at startup.
- `deploy/nl1/docker-compose.yml` does **not** publish the metrics
  port to the host by default — operators add a `ports:` line scoped
  to the WireGuard subnet.
- No change to firewall (`ufw`) — UFW already denies all inbound by
  default. Operators allow the port from the NL-2/Prometheus IP only.

---

## 4. Alternatives considered

### 4.1 Sidecar exporter scraping Redis (`redis_exporter` + custom queries)

We'd run a separate container that polls Redis for limiter counters and
re-publishes them as gauges.

Rejected because:

- Loses the histogram (Redis stores counts, not distributions).
- Doubles the operational footprint (new container per node).
- Doesn't see Python-side facts (`rate_limit_redis_errors_total`,
  parse failures) — the very signals we most want.
- Adds a polling lag (typically 15 s) that can hide alert-worthy
  bursts.

### 4.2 Pull metrics out via the existing FastAPI application on NL-2

The temp-link API already runs FastAPI. We could mount `/metrics` there.

Rejected because:

- The limiter lives on NL-1 (P11: limiter at the bot edge). Shipping
  its state across the WireGuard tunnel for scrape purposes is
  needless coupling.
- NL-2 serves *user* traffic; mixing operator-only paths increases the
  blast radius of an Nginx misconfiguration.

### 4.3 OpenTelemetry exporter (OTLP)

We could push metrics to an OTLP collector instead of pulling.

Rejected (for now) because:

- Adds a hard dependency on a collector we don't run yet.
- Pull-based Prometheus integrates trivially with the alerting
  recipes already in [`35-`](../35-metrics-and-slo.md) §6.
- We can add OTLP later by registering a second exporter against the
  same `CollectorRegistry` — this ADR doesn't lock us out.

### 4.4 No exporter — keep log-only SLIs

The status quo from [`35-`](../35-metrics-and-slo.md) §1.

Rejected because the limiter's hot path (every message) generates a
JSON log line per decision, and `topk(...)` over a 28-day LogQL window
is already the slowest panel on the SLO dashboard. A counter scrape
collapses that to O(1).

---

## 5. Compliance

- **Code lint:** the `prometheus_client` import is allowed only inside
  `app/infrastructure/metrics/`. Any `from prometheus_client …` outside
  that package fails review (P7: no infra leakage into domain /
  application). Update `docs/26-cursor-rules.md` if the rule needs to
  be machine-enforced.
- **Tests:** `app/tests/test_rate_limit_metrics.py` asserts both the
  Noop sink and the Prometheus sink record what `evaluate()` and
  `RedisRateLimitGate` expect. Any rename of a metric (or label) MUST
  update this test in the same PR.
- **Docs:**
  - [`docs/35-metrics-and-slo.md`](../35-metrics-and-slo.md) §7 lists
    the new metric names.
  - [`docs/36-rate-limiting.md`](../36-rate-limiting.md) §8 marks the
    exporter as implemented and points here.
  - [`docs/13-config-and-env.md`](../13-config-and-env.md) lists the
    `METRICS_*` env vars.
- **Deploy:** `deploy/nl1/.env.example` carries the `METRICS_*` block
  with `METRICS_ENABLED=false` by default.

---

## 6. References

- Code: `app/infrastructure/metrics/prometheus_metrics.py`,
  `app/infrastructure/metrics/server.py`,
  `app/composition.py`, `app/main_bot.py`
- Tests: `app/tests/test_rate_limit_metrics.py`,
  `app/tests/test_rate_limit_settings.py`
- Docs: `docs/35-metrics-and-slo.md` §7,
  `docs/36-rate-limiting.md` §8 / §8.1,
  `docs/13-config-and-env.md`
- External: [`prometheus_client` README](https://github.com/prometheus/client_python),
  [Prometheus naming conventions](https://prometheus.io/docs/practices/naming/)

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2026-04-19 | Accepted | Drafted alongside the in-process exporter implementation. |

# ADR-0007 — Job, queue and worker metrics for the in-process exporter

- **Status:** Accepted
- **Date:** 2026-04-19
- **Deciders:** project owner, original architect
- **Tags:** observability, metrics, slo, jobs, queue, worker, api
- **Supersedes:** —
- **Superseded by:** —
- **Builds on:** [`ADR-0006`](0006-in-process-rate-limit-metrics-exporter.md)

---

## 1. Context

[`docs/35-metrics-and-slo.md`](../35-metrics-and-slo.md) §7 enumerates a
"recommended minimum metrics" table for the operational SLOs A1–A6 and
the SRE-facing B3/B4. After [`ADR-0006`](0006-in-process-rate-limit-metrics-exporter.md)
the only metrics actually emitted are the four rate-limit ones. The
job/queue/worker family is still target design.

The codebase has further drifted from `35-` §4.1 in three concrete
ways that this ADR has to face honestly before instrumenting anything:

1. **Log event names diverge from §4.1.** Production code emits
   `job_enqueued` (not `job_created`), `job_done` (not `job_completed`),
   has no `job_status_changed` event, no `bot_message_handled` event,
   and no periodic `queue_depth` / `worker_active_jobs` sampler.
2. **`JobStatus` enum has four states (`pending/processing/done/failed`),
   not the eight-ish set §35 §3.1 implies (`ready_for_choice`,
   `failed_internal`, `failed_provider`, …).** Renaming the enum is a
   data-migration-class change (it backs a Postgres native enum, see
   `app/infrastructure/db/models.py`); doing it now would balloon the
   scope of an observability change into a domain refactor.
3. **`reason_class` is documented but not enforced anywhere in code.**
   The docs imply five values (`ok`, `user_error`, `provider_error`,
   `network_error`, `internal_error`); the runtime has none.

We cannot ship usable SLO dashboards without resolving these gaps.
But we also cannot rename `JobStatus` (a P9 architectural change with
DB migration consequences) inside an "add metrics" change.

This ADR records how we square that.

Three additional realities shape the design:

- **Three OS processes** (`bot`, `worker`, `api`) handle disjoint
  surfaces (analyse-link / process-job / serve-temp-link). Each owns
  its own subset of the metrics. `ADR-0006`'s in-process exporter was
  introduced for the bot only; we now have to repeat the same shape
  for `worker` and `api`.
- **arq has no public "queue depth" or "active jobs" API.** Queue
  depth comes from a Redis `ZCARD` on the queue key; in-flight job
  count is a process-local counter that has to be maintained by
  wrapping the task entrypoint.
- **P11 (logs are canonical) still holds.** Every metric increment
  must remain in lockstep with a structured log event so a deployment
  that runs with `METRICS_ENABLED=false` still has the data on disk
  for `logcli` queries (§35 §4.2).

---

## 2. Decision

### 2.1 Add seven metric families to the same `CollectorRegistry`

| Metric | Type | Labels | Process | Increments where |
|---|---|---|---|---|
| `bot_message_handled_total` | Counter | `outcome`, `latency_bucket` | bot | post-handler middleware (`app/bot/middleware/metrics_mw.py`) |
| `jobs_created_total` | Counter | `platform` | bot | `EnqueueDownloadUseCase.execute` after `_jobs.create` |
| `jobs_status_changed_total` | Counter | `to`, `reason_class` | worker | `ProcessDownloadUseCase` at every status transition (`_transition_to`, `_record_done`, `_fail`, `_handle_cancellation`) |
| `job_duration_seconds` | Histogram | `file_size_class` | worker | success path of `ProcessDownloadUseCase` (`_record_done`) |
| `temp_link_serves_total` | Counter | `result` | api | every return path of `download` (`/d/{token}`) |
| `arq_queue_depth` | Gauge | – | bot (sampler) | periodic `ZCARD` task on the bot's `arq_pool` |
| `worker_active_jobs` | Gauge | – | worker | task wrapper around `process_download_job` |

Naming conventions:

- All counters end in `_total` per Prometheus best practice.
- Singular `job_duration_seconds` (Histogram) vs plural `jobs_*_total`
  (Counters) follows the Prometheus convention "histograms are about a
  single observation, counters are about events".
- `arq_queue_depth` keeps the namespace prefix (`arq_`) because it is
  library-shaped, not domain-shaped; future operators tracing
  cardinality will look for it under that name.

### 2.2 Use the `JobStatus` enum as it exists today

`to` label values are the four real `JobStatus` strings:
`pending`, `processing`, `done`, `failed`. Cardinality is bounded
(four values × five `reason_class` values = 20 series) so this is
safe.

The §35 §4.1 names (`ready_for_choice`, `failed_internal`, …) reflect
an aspirational state machine. **`docs/35-` is updated in the same
PR** to match the runtime, and a separate roadmap item tracks any
future state-machine refactor (a P10 / P9-class change).

### 2.3 Introduce a `ReasonClass` enum in the domain

Lives in `app/domain/reason_class.py` (new file). Five values mirror
the docs: `ok`, `user_error`, `provider_error`, `network_error`,
`internal_error`. Classification helper:

```python
def classify_exception(exc: BaseException) -> ReasonClass: ...
```

Maps the existing `app/exceptions.py` hierarchy (`InvalidUrlError`,
`UnsupportedPlatformError`, `MediaPrivateError`, `MediaNotFoundError`
→ `user_error`; `ProviderError`, `FfmpegError` → `provider_error`;
`DownloadTimeoutError`, network/Redis errors → `network_error`;
everything else → `internal_error`). Pure function, easy to unit-test.

> **Дополнено (2026-09-25):** актуальная таблица соответствия —
> колонка `reason_class` в [`16-error-handling.md`](../16-error-handling.md) §2.
> Прочие `AppError` по-прежнему уходят в `provider_error`, а
> неожиданные исключения — в `internal_error`, но каждый подкласс
> `AppError` теперь обязан стоять в явной группе (guard-тест в
> `app/tests/test_reason_class.py`).

### 2.4 Add `bot_message_handled` and `job_status_changed` log events

These are emitted **alongside** the metric increment (P11). Existing
`job_enqueued` / `job_done` events stay as-is to avoid breaking
downstream `logcli` queries; the new events are additive. §35 §4.1
is updated to list both.

### 2.5 Three independent metrics servers

Each of `bot`, `worker`, `api` constructs its own
`CollectorRegistry` + `MetricsServer` (port `9090` by default; same
host-binding rules from `ADR-0006`). Cross-process metrics
(`jobs_created` from bot vs `jobs_status_changed` from worker) are
joined at scrape time by the operator's Prometheus configuration:

```yaml
scrape_configs:
  - job_name: 'dwtgbot-bot'
    static_configs:
      - targets: ['nl1-bot:9090']
  - job_name: 'dwtgbot-worker'
    static_configs:
      - targets: ['nl2-worker:9090']
  - job_name: 'dwtgbot-api'
    static_configs:
      - targets: ['nl2-api:9090']
```

We do **not** introduce push-gateway / cross-process aggregation —
that would re-architect the deployment.

### 2.6 Queue-depth sampler instead of scrape-time collection

`prometheus_client`'s `generate_latest()` is synchronous; calling
into asyncio Redis from a sync collector requires
`asyncio.run_coroutine_threadsafe` and a thread bridge. Instead we
run a bounded asyncio task in the bot process:

```python
async def _sample_queue_depth():
    while not stop:
        depth = await pool.zcard(QUEUE_KEY)   # ZCARD on arq's queue zset
        gauge.set(depth)
        await asyncio.sleep(METRICS_QUEUE_SAMPLE_INTERVAL_S)
```

Default interval **15 s** — fast enough for the 5-min `B3` window in
`35-` §3.2, slow enough that a `ZCARD` storm cannot DoS Redis. The
interval is configurable via `METRICS_QUEUE_SAMPLE_INTERVAL_S`.

### 2.7 Worker active-jobs gauge via task wrapper

`process_download_job` is wrapped by a thin decorator that does
`gauge.inc()` on entry and `gauge.dec()` in a `finally`. arq does not
expose its own counter; this is the only place that is guaranteed
correct under retries, timeouts and worker restarts.

A second gauge `worker_concurrency` is set once at startup so PromQL
can compute saturation `worker_active_jobs / worker_concurrency` for
SLO B4.

### 2.8 `temp_link_serves_total` uses a `result` label, not `status_code`

§35 §7 names the label `status_code`. We deviate intentionally:

| `result` | Maps to | Why this matters |
|---|---|---|
| `ok` | 200 | success |
| `not_found` | 404 (token unknown) | possible enumeration attempt |
| `expired` | 410 (token used up / past TTL) | by-design, must NOT count against A6 |
| `gone` | 410 (file deleted by cleanup) | a cleanup race, not user-facing |
| `forbidden` | 403 (`ensure_within` rejection) | security-grade alert |
| `error` | 500 | bug |

Numeric status codes alone cannot distinguish "expired-by-design"
from "file-vanished-because-cleanup-was-too-eager", and SLO A6 hinges
on that distinction. §35 is updated to match.

---

## 3. Consequences

### 3.1 Positive

- Closes the §35 §7 implementation gap that has been open since the
  doc was written.
- Operators get all six SLO panels populated from PromQL without
  cobbling together LogQL fallbacks.
- The cost of adding a future metric is one entry in
  `PrometheusJobMetrics` plus one increment site — same shape that
  `RateLimitMetrics` set in `ADR-0006`.
- `ReasonClass` makes failure classification a domain concept,
  testable independently of the worker.

### 3.2 Negative

- Three processes × three `MetricsServer` instances = three private
  ports to firewall correctly. Documented in `35-` §7 and in the
  `.env.example` files; `validate_runtime` already enforces the
  private-bind rule so a misconfigured deploy fails fast on startup
  rather than silently leaking.
- The queue-depth sampler is one more asyncio task on the bot
  process. The operational runbook ([`24-`](../24-runbooks.md))
  picks up an "if `arq_queue_depth` stops moving for >2 sample
  intervals, the sampler is wedged" alert.
- We are explicitly *not* renaming `JobStatus` to match §35 §3.1.
  Operators reading older copies of the docs will see different
  vocabulary. Mitigated by updating §35 in the same PR and by a
  callout at the top of the metric table.

### 3.3 Neutral

- `prometheus-client` is already pinned by `ADR-0006`. No new runtime
  dependency.
- All new code goes through the existing `MetricsServer` /
  `CollectorRegistry` shape — no new infrastructure layer.

---

## 4. Operational impact

| Surface | Today | With this ADR |
|---|---|---|
| Bot `/metrics` | 4 limiter metrics | + `bot_message_handled_total`, `jobs_created_total`, `arq_queue_depth` |
| Worker `/metrics` | n/a | new server: `jobs_status_changed_total`, `job_duration_seconds`, `worker_active_jobs`, `worker_concurrency` |
| API `/metrics` | n/a | new server: `temp_link_serves_total` |
| Logs | unchanged | + `bot_message_handled`, `job_status_changed` (additive) |
| Config | `METRICS_*` | + `METRICS_QUEUE_SAMPLE_INTERVAL_S` (default 15) |
| Runtime validation | `METRICS_BIND_HOST` private bind in prod | unchanged — applies to all three processes |

---

## 5. Alternatives considered

### 5.1 Rename `JobStatus` to match §35 §4.1 first

Rejected. That is a five-step migration:

1. Add new enum values.
2. Backfill existing rows.
3. Switch read paths.
4. Switch write paths.
5. Drop old values.

Each step is a separate Alembic migration with its own deployment
window. Bundling that into "add metrics" is the textbook P10
violation. The roadmap entry tracks it as a standalone change.

### 5.2 Push-gateway aggregation

Rejected. Adds an extra service to NL-1, breaks the "no shared
state outside Postgres/Redis" invariant from `ADR-0001`, and gives
us monotonicity headaches with worker restarts. Three independent
scrape targets is the standard Prometheus shape.

### 5.3 Scrape-time queue depth via custom `Collector.collect`

Rejected. Sync collector + async Redis ⇒ thread bridge or
`asyncio.run_coroutine_threadsafe`, both of which add a deadlock
surface during shutdown. The 15-s sampler is simpler and sufficient
for B3 (which is itself measured over 5-minute windows).

### 5.4 Drop `result` label, use raw `status_code`

Rejected. Cannot distinguish "expired by design" (must not count
against A6) from "file vanished" (must absolutely count against
A6). The label vocabulary is small and bounded; cardinality is fine.

### 5.5 Keep all metrics in the bot process and ship them via Redis

Rejected. Means the worker has to publish counters to a Redis hash
which the bot then scrapes. Couples three processes through a fourth
data structure for no observable benefit.

---

## 6. Compliance

| Principle | How we comply |
|---|---|
| **P9 (architecture-first)** | This ADR exists; structure mirrors `ADR-0006`. |
| **P10 (minimal safe scope)** | No `JobStatus` rename, no logging rename, no new infra service. |
| **P11 (logs are canonical)** | Every metric is paired with a log event. `METRICS_ENABLED=false` keeps the system fully observable via `logcli`. |
| **§35 §7 acceptance criteria** | Private bind enforced; ADR present; §35 updated in same PR. |
| **`docs/34-` privacy contract** | No PII in any label; `user_id` is *not* a label on any metric introduced here. |
| **`docs/17-security.md`** | New `/metrics` ports all default to `127.0.0.1`; production binds validated by `Settings.validate_runtime`. |

---

## 7. References

- [`docs/35-metrics-and-slo.md`](../35-metrics-and-slo.md) §3, §4, §7
- [`docs/14-logging-observability.md`](../14-logging-observability.md) §5
- [`ADR-0006`](0006-in-process-rate-limit-metrics-exporter.md) — the in-process exporter shape we extend
- [`ADR-0001`](0001-two-server-topology.md) — process split
- [Prometheus naming](https://prometheus.io/docs/practices/naming/)
- [arq queue model](https://arq-docs.helpmanual.io/) — `default_queue_name`, `ZCARD` semantics

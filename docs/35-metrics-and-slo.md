# 35 — Metrics and SLO

> Status: Reference (operational target — partial implementation today)
> Audience: SREs, on-call responders, capacity planners, AI agents
> wiring monitoring
> Read first: [`14-logging-observability.md`](14-logging-observability.md),
> [`15-healthchecks.md`](15-healthchecks.md),
> [`24-runbooks.md`](24-runbooks.md)

This document defines the **service level indicators (SLIs)**, the
**service level objectives (SLOs)** we hold ourselves to, and the
**concrete queries / alert rules** to evaluate them.

> 🔒 **Locked invariants** referenced here:
> - **No tracing or metrics backend is shipped by default** (P11). Logs are the canonical signal; metrics are derived from logs (Loki/`logcli`/Promtail) or from an explicitly added Prometheus exporter sidecar.
> - **A change to the SLO targets is an architectural change.** It must come with an ADR (P10).
> - **Burning the error budget is a stop-the-line event.** No new feature work until the budget refills.

> ⚠️ **Doc-vs-code deviation note (ADR-0007)** — This document was written
> as a *target* contract before the code was instrumented. The bulk of
> §4.1 (event names), §3 (`JobStatus` value names like `ready_for_choice`,
> `failed_internal`, `failed_provider`), and §7 alert expressions were
> drafted in aspirational form. The runtime today emits a slightly
> different vocabulary, documented exhaustively in
> [`adr/0007-job-queue-worker-metrics.md`](adr/0007-job-queue-worker-metrics.md)
> §1.3. Where this page differs from reality, ADR-0007 is the authority
> until a follow-up doc-sync rewrite (P10) lands. The most important
> concrete deltas are also pinned inline in §4.1 and §7 below.

---

## §1 — Why this document exists

Without numeric targets, "the bot is slow" is a gut feeling. With
targets, it's a triage decision. The four roles this doc serves:

1. **On-call** — "is this a paging incident, or wait-for-business-hours?"
   answered by the alert rules in §6.
2. **Product** — "can we promise <60 s download for under-1 GB videos?"
   answered by the SLO table in §3.
3. **Capacity planning** — "do we need to add a worker?"
   answered by the queue-depth and processing-time queries in §5.
4. **AI agents** — when proposing a change, agents must justify the
   change against SLO impact (P10) and must not silently expand the
   error budget (P11).

---

## §2 — SLI / SLO / SLA: definitions used here

| Term | Meaning in this project |
|---|---|
| **SLI** (indicator) | A precisely-defined ratio of "good events / total events" that we can compute from logs or metrics. |
| **SLO** (objective) | A target value for an SLI, measured over a 28-day rolling window. |
| **Error budget** | `1 - SLO`, expressed as time or events allowed to be "bad" per window. |
| **SLA** (agreement) | We do **not** offer an external SLA today (free service). The SLOs below are internal targets only. |
| **Page** | An alert that wakes someone up. Tied to multi-window burn rate (§6.2). |
| **Ticket** | A non-paging alert (work-hours queue). |

> If you ever sign an SLA with a paying customer, raise this through an
> ADR — it changes the entire posture (P10, P11).

---

## §3 — SLO catalogue (the targets)

### 3.1 Public-facing SLOs

These are what a user perceives.

| # | Name | SLI definition | SLO target (28-d rolling) | Error budget |
|---|---|---|---|---|
| **A1** | Bot responsiveness | `count(events where event="bot_message_handled") with latency_ms < 1500 / total` | **99 %** | ~14.5 h/month "slow" |
| **A2** | URL-analysis success | `count(jobs where status="ready_for_choice") / count(jobs where created)` for the same period, **excluding user-error classes** (`unsupported_url`, `private_content`, `geo_block`, `auth_required`) | **97 %** | ~21.6 h/month "broken" |
| **A3** | Download completion | `count(jobs where status="completed") / count(jobs where status in ("completed","failed_internal","failed_provider"))` excluding user-error classes | **95 %** | ~36 h/month |
| **A4** | Time-to-delivery (≤200 MB files) | `histogram_quantile(0.95, job_total_seconds_bucket{file_size_class="lt_200mb"})` | **p95 ≤ 90 s** | n/a (latency, not availability) |
| **A5** | Time-to-delivery (>200 MB files) | same, `file_size_class="gt_200mb"` | **p95 ≤ 240 s** | n/a |
| **A6** | Temp-link delivery | `count(temp_link_serve_ok) / count(temp_link_serve_total)` (excluding 404 expired) | **99.5 %** | ~3.6 h/month |

### 3.2 Internal / SRE-facing SLOs

These are leading indicators — if they break, A1–A6 follow.

| # | Name | SLI | SLO target |
|---|---|---|---|
| **B1** | NL-1 health endpoint | `count(GET /healthz=200) / count(GET /healthz)` | **99.9 %** |
| **B2** | NL-2 health endpoint | `count(GET /healthz=200) / count(GET /healthz)` | **99.9 %** |
| **B3** | Queue depth | `max_over_time(queue_depth[5m])` | **< 50** for 99 % of 5-min windows |
| **B4** | Worker concurrency saturation | `avg_over_time(worker_active_jobs / WORKER_CONCURRENCY [5m])` | **< 0.85** for 95 % of 5-min windows |
| **B5** | Postgres connection saturation | `pg_stat_activity / max_connections` | **< 0.6** sustained |
| **B6** | Disk usage on `STORAGE_PATH` | `node_filesystem_used / node_filesystem_size` | **< 0.85** |

### 3.3 Out-of-scope SLOs (deliberately undefined)

- Throughput per platform (YouTube vs Instagram) — diagnostic only, not a target.
- yt-dlp upgrade error rate — covered by deploy gates ([`21-`](21-cicd.md)), not an SLO.
- ffmpeg processing time per format — diagnostic only.
- Telegram send latency — partly out of our control.

---

## §4 — How SLIs are computed today (logs-based)

Until / unless a Prometheus exporter is added (§7), every SLI is derived
from structured logs. Each log event has the canonical fields from
[`14-`](14-logging-observability.md) §5.

### 4.1 Required log events (contract)

> 🔁 **Renamed in code (ADR-0007 §1.3):** `job_created` → `job_enqueued`,
> `job_completed` → `job_done`, `queue_depth` → `queue_depth_sample`,
> `worker_active_jobs` is **not** emitted as a standalone log line (the
> value is exported as a Prometheus gauge only). Old aspirational names
> are kept in the LogQL recipes below as *examples of the shape*; the
> live LogQL equivalents are in §4.2.

| Event | Emitted from | Required fields |
|---|---|---|
| `bot_message_handled` | bot middleware (`app/bot/middleware/metrics_mw.py`) | `request_id`, `user_id`, `chat_id`, `latency_ms`, `latency_bucket`, `outcome` ∈ {ok, user_error, internal_error} |
| `job_enqueued` | `app/application/use_cases/enqueue_download.py` | `job_id`, `user_id`, `platform`, `request_id` (job-cap rejections do NOT emit this — see ADR-0007 §2.2) |
| `job_status_changed` | `app/application/use_cases/process_download.py` | `job_id`, `from_`, `to` ∈ values of `JobStatus` enum (`pending`, `processing`, `done`, `failed`), `reason_class` ∈ {`ok`, `user_error`, `provider_error`, `network_error`, `internal_error`} |
| `job_done` | worker (`process_download._run`) | `job_id`, `total_seconds`, `file_size_bytes`, `file_size_class` ∈ {`lt_50mb`, `lt_200mb`, `gt_200mb`} |
| `temp_link_served` | `api/public/downloads.py` | `token_prefix`, `result` ∈ {`ok`, `not_found`, `expired`, `gone`, `forbidden`}, `bytes_sent` (only when `result="ok"`) |
| `queue_depth_sample` | `app/infrastructure/metrics/queue_depth_sampler.py` (every `METRICS_QUEUE_SAMPLE_INTERVAL_S`) | `depth` |

If you add a code path that creates a job, emit `job_enqueued` with the
fields above; otherwise the SLIs go silently wrong (P10 violation).
Worker concurrency is set once at boot via
`PrometheusJobMetrics.set_worker_concurrency` (ADR-0007 §2.7) and shows
up only in `/metrics`, not the log stream.

### 4.2 LogQL recipes (Loki)

```logql
# A1 — bot responsiveness (5-min window)
sum(rate({app="bot"} | json | event="bot_message_handled" | latency_ms < 1500 [5m]))
/
sum(rate({app="bot"} | json | event="bot_message_handled" [5m]))

# A2 — URL-analysis success (5-min window)
sum(rate({app="bot"} | json | event="job_status_changed" | to="ready_for_choice" [5m]))
/
sum(rate({app="bot"} | json | event="job_created" [5m]))

# A3 — download completion, excluding user errors
sum(rate({app="worker"} | json | event="job_status_changed" | to="completed" [5m]))
/
sum(rate({app="worker"} | json | event="job_status_changed"
         | to=~"completed|failed_internal|failed_provider"
         | reason_class!="user_error" [5m]))

# A4 / A5 — time-to-delivery percentiles
quantile_over_time(
  0.95,
  {app="worker"} | json | event="job_completed" | file_size_class="lt_200mb"
                | unwrap total_seconds [5m]
)

# B3 — queue depth
max_over_time({app="bot"} | json | event="queue_depth" | unwrap depth [5m])
```

> Loki users: index `event` as a label only if cardinality is bounded. The
> events listed in §4.1 are bounded by design (no unbounded values in
> field names).

### 4.3 PromQL recipes (only meaningful if §7 is in place)

```promql
# A1 — bot responsiveness
sum(rate(bot_message_handled_total{outcome="ok",latency_bucket="lt_1500ms"}[5m]))
/
sum(rate(bot_message_handled_total[5m]))

# A4 — p95 latency for small files
histogram_quantile(
  0.95,
  sum by (le) (rate(job_total_seconds_bucket{file_size_class="lt_200mb"}[5m]))
)

# B3 — queue depth
max_over_time(arq_queue_depth[5m])

# B5 — Postgres saturation (postgres_exporter)
pg_stat_activity_count / on (instance) pg_settings_max_connections
```

---

## §5 — Diagnostic queries (not SLOs, but commonly needed)

```logql
# Failure rate by platform (last 1 h)
topk(5,
  sum by (platform) (rate({app="worker"} | json
    | event="job_status_changed" | to=~"failed_.*" [1h])))

# Top 10 error_class right now
topk(10,
  sum by (error_class) (rate({app=~"bot|worker|api"} | json
    | level="error" [15m])))

# Slowest 5 jobs of the last hour
topk(5,
  max_over_time({app="worker"} | json
    | event="job_completed"
    | unwrap total_seconds [1h])) by (job_id, platform)

# Per-user request rate (privacy: user_id is numeric only — see 34-)
sum by (user_id) (rate({app="bot"} | json
  | event="bot_message_handled" [5m]))
```

```bash
# Quick local check on NL-1: failed-job count, last hour
docker compose -f deploy/nl1/docker-compose.yml exec postgres \
  psql -U dwtgbot -d dwtgbot -t -c "
    SELECT count(*) FROM download_jobs
     WHERE status LIKE 'failed_%'
       AND updated_at > now() - interval '1 hour';"

# Quick local check on NL-2: queue depth right now
docker compose -f deploy/nl2/docker-compose.yml exec worker \
  python -c "import asyncio
from app.composition import build_composition
async def main():
    c = await build_composition()
    print(await c.queue.depth())
asyncio.run(main())"
```

---

## §6 — Alert rules

### 6.1 Alert taxonomy

| Severity | Examples | Routing |
|---|---|---|
| **page** | A1 burn-rate fast, B1/B2 down, B3 explosion | wakes on-call |
| **ticket** | A2/A3 burn-rate slow, B5 over 0.7 sustained | work-hours |
| **info** | yt-dlp upgrade ran, backup completed | log only |

### 6.2 Multi-window burn-rate alerts (preferred)

We follow Google SRE workbook style: alert on **two** windows
simultaneously to suppress flaps.

```yaml
# /etc/prometheus/alerts.yml (template; values calibrated for SLO 99 %)
groups:
- name: dwtgbot-slo
  rules:
  # A1 — bot responsiveness fast burn (5m AND 1h, 14.4× budget burn)
  - alert: BotResponsivenessFastBurn
    expr: |
      (
        (1 - (
          sum(rate(bot_message_handled_total{outcome="ok",latency_bucket="lt_1500ms"}[5m]))
          / sum(rate(bot_message_handled_total[5m]))
        )) > (14.4 * (1 - 0.99))
      )
      and
      (
        (1 - (
          sum(rate(bot_message_handled_total{outcome="ok",latency_bucket="lt_1500ms"}[1h]))
          / sum(rate(bot_message_handled_total[1h]))
        )) > (14.4 * (1 - 0.99))
      )
    for: 2m
    labels: { severity: page, slo: A1 }
    annotations:
      runbook: docs/24-runbooks.md#1-bot-unresponsive
      summary: "A1 burning error budget at 14.4× (fast)"

  # A1 slow burn (30m AND 6h, 6× burn)
  - alert: BotResponsivenessSlowBurn
    expr: |
      (
        (1 - (
          sum(rate(bot_message_handled_total{outcome="ok",latency_bucket="lt_1500ms"}[30m]))
          / sum(rate(bot_message_handled_total[30m]))
        )) > (6 * (1 - 0.99))
      )
      and
      (
        (1 - (
          sum(rate(bot_message_handled_total{outcome="ok",latency_bucket="lt_1500ms"}[6h]))
          / sum(rate(bot_message_handled_total[6h]))
        )) > (6 * (1 - 0.99))
      )
    for: 15m
    labels: { severity: ticket, slo: A1 }

  # A3 — download completion fast burn
  - alert: DownloadCompletionFastBurn
    expr: |
      (
        (1 - (
          sum(rate(job_status_changed_total{to="completed"}[5m]))
          / sum(rate(job_status_changed_total{to=~"completed|failed_internal|failed_provider",reason_class!="user_error"}[5m]))
        )) > (14.4 * (1 - 0.95))
      )
      and
      (
        (1 - (
          sum(rate(job_status_changed_total{to="completed"}[1h]))
          / sum(rate(job_status_changed_total{to=~"completed|failed_internal|failed_provider",reason_class!="user_error"}[1h]))
        )) > (14.4 * (1 - 0.95))
      )
    for: 5m
    labels: { severity: page, slo: A3 }
    annotations:
      runbook: docs/24-runbooks.md#5-yt-dlp-stopped-working

  # B3 — queue backlog
  - alert: QueueBacklog
    expr: max_over_time(arq_queue_depth[10m]) > 100
    for: 5m
    labels: { severity: ticket, slo: B3 }
    annotations:
      runbook: docs/24-runbooks.md#13-too-many-tasks-in-queue

  # B6 — disk pressure on STORAGE_PATH
  - alert: StorageDiskFilling
    expr: |
      (node_filesystem_avail_bytes{mountpoint="/var/lib/dwtgbot"}
       / node_filesystem_size_bytes{mountpoint="/var/lib/dwtgbot"}) < 0.10
    for: 10m
    labels: { severity: page, slo: B6 }
    annotations:
      runbook: docs/24-runbooks.md#12-disk-full

  # Health endpoints
  - alert: NL1Down
    expr: up{job="nl1-healthz"} == 0
    for: 2m
    labels: { severity: page, slo: B1 }
  - alert: NL2Down
    expr: up{job="nl2-healthz"} == 0
    for: 2m
    labels: { severity: page, slo: B2 }
```

### 6.3 No-Prometheus alternative — periodic log query

For deployments without Prometheus, a 5-minute cron that runs the LogQL
queries (§4.2) and pages on threshold breach is a valid substitute.
Example with `logcli`:

```bash
# /etc/cron.d/dwtgbot-slo-check
*/5 * * * * dwtgbot /usr/local/bin/dwtgbot-slo-check.sh
```

```bash
#!/usr/bin/env bash
# dwtgbot-slo-check.sh — minimal viable alerter
set -euo pipefail
RATIO=$(logcli query --quiet --output=raw \
  'sum(rate({app="bot"} | json | event="bot_message_handled" | latency_ms < 1500 [5m]))
   / sum(rate({app="bot"} | json | event="bot_message_handled" [5m]))' \
  | tail -1)
THRESHOLD="0.85"   # well below 0.99 = something is on fire
if awk "BEGIN {exit !($RATIO < $THRESHOLD)}"; then
  curl -s -X POST "$ALERTMANAGER_URL/api/v1/alerts" -d "[{\"labels\":{\"alertname\":\"BotResponsivenessRaw\",\"severity\":\"page\"}}]"
fi
```

---

## §7 — Adding a Prometheus exporter (when, how)

> **Status:** Job/queue/worker metrics are now live in code (ADR-0007).
> Rate-limit metrics from ADR-0006 remain live as well. Both sets are
> gated by `METRICS_ENABLED=false` (default off in dev, opt-in in prod).
> Each of the three processes (bot, worker, API) exposes its **own**
> in-process `/metrics` endpoint on a separate private port — see
> ADR-0007 §2.5 for why we rejected a single shared sidecar.

The exporter for limiter signals lives **inside** the bot process, not
in a sidecar (see `ADR-0006` §4.1 for why we rejected the sidecar
shape). ADR-0007 extends that decision to the worker and API
processes. The acceptance criteria below remain the contract for any
*new* metrics families (e.g. caching layer, future provider stats):

- Either run inside an existing process (preferred — less ops surface)
  or stand up a new service in `deploy/nl1/docker-compose.yml`. Either
  way, `/metrics` is exposed on a **private** port (not 80/443).
- An ADR records the decision; update §3 / §4 / §6 of this document
  with the new metric names.
- The exporter does **not** emit any field forbidden by P11 — see
  [`34-`](34-data-retention-and-privacy.md) §6 for the allow-list.
- `Settings.validate_runtime` enforces the "private port" rule for the
  bot exporter (`METRICS_BIND_HOST != 0.0.0.0` in production). New
  exporters MUST wire an equivalent check.

Live metrics (rate-limit, ADR-0006):

| Metric | Type | Labels | Reason |
|---|---|---|---|
| `rate_limit_decisions_total` | Counter | `decision`, `layer` | docs/36- §8 |
| `rate_limit_retry_after_seconds` | Histogram | `layer` | docs/36- §8 |
| `rate_limit_redis_errors_total` | Counter | `kind` | docs/36- §8 (fail-open visibility) |
| `rate_limit_first_deny_total` | Counter | `layer` (+ optional `user_id`) | docs/36- §8.1 (noisy-users topk) |

Live metrics (jobs / queue / workers, ADR-0007):

| Metric | Type | Labels | Process | SLI |
|---|---|---|---|---|
| `bot_message_handled_total` | Counter | `outcome`, `latency_bucket` | bot | A1 |
| `jobs_created_total` | Counter | `platform` | bot | A2 base |
| `jobs_status_changed_total` | Counter | `to` ∈ values of `JobStatus`, `reason_class` | worker | A2 / A3 |
| `job_replay_ignored_total` | Counter | `reason` ∈ {`done`, `failed`, `processing`} | worker | A18 diagnostics |
| `job_duration_seconds` | Histogram | `file_size_class` | worker | A4 / A5 |
| `storage_orphan_dirs_removed_total` | Counter | – | cleanup | A20 diagnostics |
| `temp_link_serves_total` | Counter | `result` ∈ {`ok`, `not_found`, `expired`, `gone`, `forbidden`} | api | A6 |
| `arq_queue_depth` | Gauge | – | bot (sampler) | B3 |
| `worker_active_jobs` | Gauge | – | worker | B4 |
| `worker_concurrency` | Gauge | – | worker | B4 (denominator) |

> 📛 **Naming notes (ADR-0007 §1.3):** the metric name uses the suffix
> `_total` per Prometheus convention even though §3 / §6 of this doc
> still reference some aspirational names (`job_created_total` without
> `s`, `job_total_seconds`, `temp_link_serve_total` without `s`). The
> suffix `s` (`jobs_…`, `serves_…`) is the source of truth in code.
> Update your alert rules accordingly when copying §6 expressions —
> see ADR-0007 §1.3 for the full rename map.

External exporters (out of scope for this repo, listed for context):

| Metric | Type | Labels | Reason |
|---|---|---|---|
| `pg_stat_activity_count` | (postgres_exporter) | – | B5 |
| `node_filesystem_*` | (node_exporter) | `mountpoint` | B6 |

---

## §8 — Sample Grafana dashboard (skeleton)

Save as `deploy/grafana/dashboards/dwtgbot-slo.json` once you stand up
Grafana. The JSON below is intentionally a **minimal skeleton** — wire
your real datasource UID before importing.

```json
{
  "title": "DWTGBot — SLO Overview",
  "schemaVersion": 39,
  "version": 1,
  "panels": [
    { "id": 1, "type": "stat",
      "title": "A1 — bot responsiveness (28d)",
      "targets": [{ "expr": "1 - (sum(increase(bot_message_handled_total{outcome=\"ok\",latency_bucket=\"lt_1500ms\"}[28d])) / sum(increase(bot_message_handled_total[28d])))" }],
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }},
      "fieldConfig": { "defaults": { "unit": "percentunit", "thresholds": { "steps": [
        { "color": "green", "value": null }, { "color": "yellow", "value": 0.005 }, { "color": "red", "value": 0.01 }
      ]}}}
    },
    { "id": 2, "type": "timeseries",
      "title": "A4/A5 — time-to-delivery p95",
      "targets": [
        { "expr": "histogram_quantile(0.95, sum by (le) (rate(job_total_seconds_bucket{file_size_class=\"lt_200mb\"}[5m])))", "legendFormat": "≤200 MB" },
        { "expr": "histogram_quantile(0.95, sum by (le) (rate(job_total_seconds_bucket{file_size_class=\"gt_200mb\"}[5m])))", "legendFormat": ">200 MB" }
      ]
    },
    { "id": 3, "type": "timeseries",
      "title": "B3 — queue depth",
      "targets": [{ "expr": "max_over_time(arq_queue_depth[5m])" }]
    },
    { "id": 4, "type": "timeseries",
      "title": "Failures by reason_class (1h rate)",
      "targets": [{ "expr": "sum by (reason_class) (rate(job_status_changed_total{to=~\"failed_.*\"}[5m]))" }]
    },
    { "id": 5, "type": "stat",
      "title": "B6 — STORAGE_PATH free",
      "targets": [{ "expr": "node_filesystem_avail_bytes{mountpoint=\"/var/lib/dwtgbot\"} / node_filesystem_size_bytes{mountpoint=\"/var/lib/dwtgbot\"}" }],
      "fieldConfig": { "defaults": { "unit": "percentunit" }}
    }
  ]
}
```

---

## §9 — Error-budget policy

| Budget burnt this 28-d window | Posture |
|---|---|
| **< 25 %** | Normal: ship features, take risks where they pay off. |
| **25 – 75 %** | Caution: prefer reliability fixes, avoid risky deploys on Friday. |
| **75 – 100 %** | Freeze: only reliability work, only carefully. Daily standup checks budget. |
| **> 100 %** | **Stop the line.** No new feature work. Post-mortem mandatory. ADR if root cause is architectural. |

This applies per-SLO. Burning A1's budget does not freeze B6 work, etc.

---

## §10 — When to revise an SLO

- After two consecutive windows of consistently being well under (e.g.
  A4 p95 = 30 s for 8 weeks against a 90 s target) — the target is too
  loose and gives a false sense of headroom.
- After a load profile change (new platform, virality event) that
  permanently shifts user expectations.
- After a major architectural change (ADR-tracked) that alters latency
  characteristics.

A revision is an ADR-grade decision. Do not silently retune `0.99 → 0.97`
in alert rules to "stop pages".

---

## §11 — Common mistakes

| # | Mistake | Symptom | Fix |
|---|---|---|---|
| 1 | Counting user errors against A2/A3 | Numbers tank during a popular but unsupported URL going viral | Filter `reason_class!="user_error"` (§3.1) |
| 2 | Single-window alerts (5-min only) | Pages flap wildly | Use multi-window burn rate (§6.2) |
| 3 | Histogram buckets too coarse | p95 jumps in big steps | Use buckets like `0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300` |
| 4 | Mixing platforms in A4/A5 | Hides Instagram regressions behind YouTube success | Add a per-platform diagnostic panel, but keep the SLI aggregated for budget purposes |
| 5 | Letting alerts fire without a runbook annotation | On-call spends 10 min figuring out where to start | Every alert references a §-anchored line in [`24-`](24-runbooks.md) |
| 6 | Treating B5 (Postgres) like an SLO instead of a leading indicator | Pages on saturation before users notice | Keep it as ticket-grade; A1/A3 will catch the user-visible part |
| 7 | Adding new log events but not declaring them in §4.1 | SLI math silently goes wrong | Update this doc in the same PR (P10) |
| 8 | Pushing alert thresholds during an incident | Hides the regression instead of fixing it | Open an ADR + post-mortem before changing |
| 9 | Treating absent metrics as zero (`absent_over_time` ignored) | Pages don't fire when the exporter dies | Add `up == 0` alerts (§6.2) |
| 10 | Counting `temp_link_served` 404s as failures | A6 collapses on link expiry | Exclude expired-link 404s; they are by-design (§3.1) |

---

## §12 — Quick reference

```
SLO targets (28-d):
  A1 bot responsiveness        ≥ 99 %
  A2 URL analysis success      ≥ 97 %
  A3 download completion       ≥ 95 %
  A4 delivery ≤200 MB p95      ≤ 90 s
  A5 delivery >200 MB p95      ≤ 240 s
  A6 temp-link delivery        ≥ 99.5 %

Page on:
  - A1/A3 fast burn (multi-window)
  - B1/B2 health down 2m
  - B6 storage free < 10% for 10m
  - QueueBacklog depth > 100 for 5m

Ticket on:
  - A1/A3 slow burn
  - B5 Postgres saturation > 0.7 sustained
  - QueueBacklog depth > 50 for 30m

Freeze when any SLO budget > 75 % burnt.
Stop-the-line when > 100 %.
```

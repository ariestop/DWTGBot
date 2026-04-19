# 37 — Load and capacity

> Status: Stable (caps in §7 and the §6 test endpoint are implemented;
> rate-limit hooks in [`36-`](36-rate-limiting.md) are still operational target)
> Audience: SREs, capacity planners, on-call responders, AI agents
> proposing infra changes
> Read first: [`02-architecture.md`](02-architecture.md),
> [`09-queue-and-workers.md`](09-queue-and-workers.md),
> [`11-storage-strategy.md`](11-storage-strategy.md),
> [`23-cleanup-retention.md`](23-cleanup-retention.md),
> [`35-metrics-and-slo.md`](35-metrics-and-slo.md),
> [`36-rate-limiting.md`](36-rate-limiting.md)

How big can DWTGBot grow on a given budget, and how do you tell when
you're about to outgrow it. This document is the back-of-the-envelope
math, the load-test recipes, and the scale-up runbook.

> 🔒 **Locked invariants** (P11):
> - **Two-server topology is fixed** ([`adr/0005`](adr/0005-locked-architectural-assumptions.md)). "Add capacity" means scale up NL-2 first; only consider sharding NL-2 with an ADR.
> - **No autoscaler is shipped by default.** Capacity changes are deliberate, operator-driven, and ADR-tracked when they cross a threshold.
> - **The queue is single-Redis** (P5). "Add a queue shard" is an ADR-grade decision.
> - **Capacity decisions must align with SLO budgets** ([`35-`](35-metrics-and-slo.md) §9). Adding load that burns the budget is the same as missing the SLO.

---

## §1 — The unit of capacity

For DWTGBot, capacity is **not** RPS. It's **concurrent download
seconds**, because every job:

1. Sits in the queue (cheap)
2. Downloads bytes from upstream (network-bound, seconds–minutes)
3. Optionally re-encodes via ffmpeg (CPU-bound, seconds)
4. Moves to `STORAGE_PATH` (disk-bound, ms)
5. Either ships through Telegram (small files) or registers a temp link (≈ms)

Steps 2 and 3 dominate. Everything else is rounding error.

### 1.1 The capacity equation

```
max_jobs_per_hour = WORKER_CONCURRENCY × 3600 / avg_job_seconds
```

Where:

| Symbol | What it is | Where it comes from |
|---|---|---|
| `WORKER_CONCURRENCY` | concurrent jobs per worker process | `deploy/nl2/.env` ([`13-`](13-config-and-env.md)) |
| `avg_job_seconds` | avg total seconds from `job_created` → `job_completed` | observed (A4 / A5 in [`35-`](35-metrics-and-slo.md)) |

This isn't theoretical — it's **the only number that matters**. If
you remember nothing else from this doc, remember the equation.

### 1.2 Worked example — small profile (default install)

Defaults: `WORKER_CONCURRENCY=2`, observed `avg_job_seconds=45`.

```
max_jobs_per_hour = 2 × 3600 / 45 = 160 jobs/hour
                                  ≈ 2.7 jobs/minute
                                  ≈ 3 800 jobs/day
```

That's plenty for a small / friend-group bot. Saturation point: ~75 %
of theoretical (queue starts to back up beyond that), so plan for
**~120 jobs/hour sustained** at defaults.

### 1.3 Worked example — bigger box

If you bump to `WORKER_CONCURRENCY=4`, with a larger NL-2 (more cores +
2 Gbps NIC), `avg_job_seconds` typically drops to ~30 because the
disk/network parallelism actually helps:

```
max_jobs_per_hour = 4 × 3600 / 30 = 480 jobs/hour
                                  ≈ 8 jobs/minute
                                  ≈ 11 500 jobs/day
```

Plan for ~360 jobs/hour sustained.

### 1.4 What WORKER_CONCURRENCY can't fix

When upstream (yt-dlp) is the bottleneck — e.g. a platform throttling
your IP — adding concurrency just stacks more parallel waits. The
queue empties faster *only if* the wall clock per job goes down.
Diagnostic: A4/A5 percentiles flat or rising while concurrency goes
up = upstream-bound, no point adding workers. See §6.5.

---

## §2 — Storage capacity

### 2.1 The storage equation

```
storage_steady_state_GiB ≈
    avg_jobs_per_hour
  × avg_file_size_GiB
  × (TEMP_LINK_TTL_SECONDS / 3600)
  × overhead_factor
```

Where `overhead_factor` is **1.3** (covers: scratch dirs, partial
failures, occasional manual retention extensions, filesystem block
overhead).

### 2.2 Worked examples

| Profile | jobs/h | avg file | TTL | Required free disk |
|---|---|---|---|---|
| Small (default) | 120 | 50 MiB | 24 h | 120 × 0.05 × 24 × 1.3 = **187 GiB** |
| Small, video-heavy | 120 | 200 MiB | 24 h | 120 × 0.2 × 24 × 1.3 = **750 GiB** |
| Larger | 360 | 100 MiB | 24 h | 360 × 0.1 × 24 × 1.3 = **1 123 GiB** ≈ 1.1 TiB |
| Larger, short TTL | 360 | 100 MiB | 6 h | 360 × 0.1 × 6 × 1.3 = **281 GiB** |

Two ways to shrink the requirement: smaller `TEMP_LINK_TTL_SECONDS`
(users have to be quicker, see [`23-`](23-cleanup-retention.md) §3.4)
or refusing very large files (see §7.1 capacity caps).

### 2.3 Disk usage SLO (B6 in [`35-`](35-metrics-and-slo.md))

`STORAGE_PATH` should sit at **< 85 % used**. The cleanup worker
(`23-` §4) handles routine churn; sustained > 85 % means either:

- traffic stepped up and you didn't update §2.2 math, or
- cleanup is failing (queue depth at the worker, see [`24-`](24-runbooks.md) §18), or
- TTL is too generous for current load — tune `TEMP_LINK_TTL_SECONDS` down.

> Pager fires at 90 % free → 10 % left. Below that you have minutes,
> not hours.

---

## §3 — Bandwidth and egress

### 3.1 The egress equation

Two flows, opposite directions:

```
inbound_bytes_per_hour  ≈ avg_jobs_per_hour × avg_file_size  (NL-2 ← upstream)
outbound_bytes_per_hour ≈ avg_jobs_per_hour × avg_file_size × delivery_factor
```

Where `delivery_factor` accounts for re-downloads:

- ≤ 50 MB files (Telegram-direct): `delivery_factor ≈ 1.0` (uploaded once to Telegram, who serves further)
- &gt; 50 MB files (temp links): `delivery_factor ≈ TEMP_LINK_MAX_DOWNLOADS × 0.4` (default 5 × 0.4 = **2.0**; 0.4 because most users only click once or twice)

### 3.2 Worked example

Larger profile, 360 jobs/h, 100 MiB avg, 50/50 small-vs-large split:

```
inbound  = 360 × 100 MiB                                      = 36 GiB/hour
outbound = 180 × 100 MiB × 1.0  +  180 × 100 MiB × 2.0
         = 18 GiB + 36 GiB                                    = 54 GiB/hour
```

That's a **steady ~120 Mbps in + 120 Mbps out**, with bursts >
**1 Gbps** during a viral moment. NL-2 needs at least 1 Gbps; for the
Larger profile, prefer 2.5 Gbps.

### 3.3 Egress cost

If your provider bills egress, the **outbound** number is the one that
matters. Multiply by `30 × 24` for monthly. The Larger profile above:
54 × 720 = **38 TiB/month** — convert to your provider's per-GiB price.

> Cost is also why temp links get capped TTL + max_downloads
> ([`23-`](23-cleanup-retention.md)). Loosen those settings only with
> the bill in mind.

### 3.4 When upstream chokes us

Hitting the Larger profile against YouTube from a single IP eventually
gets the IP throttled. Mitigations, in order of preference:

1. Tighten L4 in [`36-`](36-rate-limiting.md) §9.2 (per-domain limit).
2. Add a second NL-2 with a different egress IP — **ADR-grade decision**.
3. Use authenticated cookies for that platform — see [`13-`](13-config-and-env.md) `*_COOKIES_FILE`.

---

## §4 — Postgres capacity

The DB is small relative to everything else. Per-row estimates:

| Table | Bytes/row (incl. TOAST median) | Notes |
|---|---|---|
| `download_jobs` | ~1.0 KiB | depends on `extra` JSONB; can spike to 5–10 KiB for rich Instagram metadata |
| `temp_links` | ~256 B | small |
| `media_cache` | ~2 KiB | TOAST-friendly |
| `audit_logs` | ~512 B | depends on event |

For the Larger profile (360 jobs/h, no row purging — see
[`34-`](34-data-retention-and-privacy.md) §2):

```
download_jobs growth = 360 × 24 × 1.0 KiB         ≈ 8.5 MiB/day  ≈ 3 GiB/year
audit_logs growth    = 360 × 24 × 1 event × 0.5 K ≈ 4.3 MiB/day  ≈ 1.5 GiB/year
```

Even with no purging, you cross 10 GiB only at ~year 3. The DB is not
your bottleneck. **Don't preemptively shard.**

### 4.1 Connection capacity

| Source | Connections | Notes |
|---|---|---|
| `bot` | 1–2 | SQLAlchemy pool of 5 max |
| `api` | 1–2 | SQLAlchemy pool of 5 max |
| `worker` | up to `WORKER_CONCURRENCY` × 2 | one for repo ops, one for state-write |
| `migrate` (transient) | 1 | Alembic |
| `psql` ad-hoc | 1–2 | operator |

Total budget today: ≤ 20. Postgres `max_connections` defaults to 100.
**Plenty of headroom.** B5 SLO at 60 % triggers a ticket — a small
indicator that someone added a leaked connection somewhere.

### 4.2 When DB *does* become the bottleneck

- New feature scans `download_jobs` without an index → A1 SLO falls.
- Backup window competes with live traffic on a small box → A1 SLO
  blips during backup.

Mitigations in order: index, query rewrite, then (only then) bigger
Postgres box. Sharding is two ADRs away.

---

## §5 — Redis capacity

Redis stores: arq queue, request_state (TTLs), rate-limit counters
(TTLs).

| Source | RAM cost (estimate) |
|---|---|
| arq queue (per pending job) | ~1 KiB |
| `request_state:*` (TTL ≤ 6 h) | ~2 KiB / live conversation |
| `rl:*:burst` (TTL 60 s) | ~64 B per active user |
| `rl:*:hourly` (TTL 1 h) | ~64 B per active user |

For 1 000 active users / hour with 100 pending jobs:

```
queue        = 100 × 1 KiB                ≈ 100 KiB
request_state = 1000 × 2 KiB              ≈ 2 MiB
rate_limit   = 1000 × 4 keys × 64 B       ≈ 256 KiB
```

Total: < 5 MiB. Default Redis (256 MiB) is **massively** over-provisioned.

### 5.1 When Redis *does* matter

- Latency, not RAM. If Redis on NL-1 is on slow disk and `appendonly` is on,
  individual ops blow past 10 ms and cascade into A1 misses.
- Cross-server (NL-2 → NL-1) network latency. > 5 ms RTT and the
  worker's status updates start adding up.

Both are fixed by colocating Redis with the bot (which we already do)
and using a fast disk for AOF (or disabling AOF and accepting at-most
6 h of recent jobs lost — usually fine; document the trade-off).

---

## §6 — Load testing

### 6.1 The minimum viable test

You don't need k6 / Locust to know if the system breaks. Run
**one user, one URL, one hundred times**, then read SLOs.

```bash
# tests/load/smoke.sh — fires 100 jobs serially via the application API
#   (NOT via Telegram, to avoid PTB-side rate limit interference)
set -euo pipefail
URL="https://www.youtube.com/watch?v=jNQXAC9IVRw"   # 18-second clip
TOKEN="${INTERNAL_TEST_TOKEN:?set INTERNAL_TEST_TOKEN to use the test endpoint}"
for i in $(seq 1 100); do
  curl -fsS -X POST "http://localhost:8080/internal/test/enqueue" \
    -H "X-Internal-Test-Token: $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{
      \"user_id\": 1,
      \"chat_id\": 1,
      \"source_url\": \"$URL\",
      \"platform\": \"youtube\",
      \"selected_option_key\": \"video_360\"
    }" > /dev/null
  echo "enqueued $i"
done
```

> The `/internal/test/enqueue` endpoint is **dev-only**, gated three
> ways: it returns 404 unless `INTERNAL_TEST_TOKEN` is set in the env,
> it returns 401 unless the caller presents that token in
> `X-Internal-Test-Token`, and `Settings.validate_runtime` refuses to
> start the process at all when `APP_ENV=production` and the token is
> non-empty. Implemented in `app/api/internal/test_enqueue.py`.
>
> Because the endpoint reuses `EnqueueDownloadUseCase`, the per-user cap
> (§7.3) is enforced — a saturated user gets HTTP 429.

After running:

1. Watch queue depth (B3) — should peak then drain.
2. Watch worker active jobs (B4) — should pin at `WORKER_CONCURRENCY`.
3. Watch `avg_job_seconds` over the run.
4. Compute `max_jobs_per_hour` (§1.1) from the observed numbers.

### 6.2 Synthetic concurrency test

Increase parallelism gradually and find the cliff:

```bash
TOKEN="${INTERNAL_TEST_TOKEN:?set INTERNAL_TEST_TOKEN}"
URL="https://www.youtube.com/watch?v=jNQXAC9IVRw"
for c in 1 2 4 8 16; do
  echo "=== concurrency=$c ==="
  # Use a different user_id per parallel slot so the per-user cap (§7.3)
  # doesn't gate the test before you reach the system limit.
  seq 1 50 | xargs -n1 -P "$c" -I{} bash -c "
    curl -fsS -X POST 'http://localhost:8080/internal/test/enqueue' \
      -H 'X-Internal-Test-Token: $TOKEN' \
      -H 'Content-Type: application/json' \
      -d '{
        \"user_id\": '\$((RANDOM % 100 + 1000))',
        \"chat_id\": 1,
        \"source_url\": \"$URL\",
        \"platform\": \"youtube\",
        \"selected_option_key\": \"video_360\"
      }' > /dev/null
  "
  # Wait for the queue to drain
  while [ "$(redis-cli -h localhost -p 6379 LLEN arq:queue)" -gt 0 ]; do sleep 1; done
done
```

For each `c`, record from your dashboards / logs:

- Time to drain
- A1, A4 percentiles
- Any errors

Plot — you'll see throughput climb until concurrency × upstream caps
out, then plateau. That plateau is your **practical ceiling**.

### 6.3 Synthetic file-size mix

Real load is a mix. Run a weighted sampler:

```bash
# 50% small (clip), 30% medium (~30 MB), 20% large (~150 MB)
for i in $(seq 1 200); do
  case $((RANDOM % 10)) in
    0|1|2|3|4) URL="$URL_SMALL" ;;
    5|6|7)     URL="$URL_MEDIUM" ;;
    *)         URL="$URL_LARGE" ;;
  esac
  # ... enqueue ...
done
```

Pick three sample URLs from your own corpus. **Do not load-test
against external platforms with thousands of requests** — you'll get
your IP banned and slow everyone down.

### 6.4 What to record per test

A test that doesn't produce a row in the table below was a waste of
time:

| Test | Date | Profile | Concurrency | jobs/h achieved | A4 p95 | A5 p95 | Failures | Bottleneck observed |
|---|---|---|---|---|---|---|---|---|
| baseline-pre-tune | YYYY-MM-DD | Small | c=4 | 120 | 18 s | 95 s | 0 | upstream wait |
| post-WORKER=4 | YYYY-MM-DD | Small | c=4 | 240 | 14 s | 78 s | 0 | concurrency = ceiling |
| storage-stress | YYYY-MM-DD | 1 GB files | c=2 | 30 | n/a | 320 s | 2 (timeout) | disk write |

Keep this in `docs/tests/load-history.md` (operator-maintained; not
shipped). Refer to it when you next consider scaling.

### 6.5 Spotting a real-world bottleneck

| Symptom | Likely bottleneck | Confirmation |
|---|---|---|
| `WORKER_CONCURRENCY` doubles, jobs/h doesn't | upstream throttle | A4/A5 p95 doesn't drop; per-domain L4 deny rate ([`36-`](36-rate-limiting.md)) up |
| Queue stays empty but A4/A5 climbs | per-job slowdown (ffmpeg / disk / single-threaded provider) | `pidstat`, `iostat -xz 1`, ffmpeg vs download time split in `event=job_completed` |
| Queue backs up but workers idle | bot can't enqueue (PTB rate-limit / DB slowness) | A1 falls; worker active < concurrency |
| Disk fills before jobs/h ceiling | TTL too long for current rate | §2.2 math; tighten TTL |
| Egress saturates before everything else | NIC limit | host `ifstat` 1 — sustained ≥ 80 % of NIC capacity |

---

## §7 — Capacity caps (defensive limits)

When math forces choices, encode them as caps so the system fails
predictably.

### 7.1 File-size cap

`MAX_FILE_SIZE_MB` (default `2048` = 2 GiB) is enforced **three times**
on every job — fail-fast, then defence-in-depth:

| Where | When | Effect |
|---|---|---|
| `ProcessDownloadUseCase._reject_if_estimate_exceeds_cap` | right after `provider.build_options(...)`, *before* download | provider's own size estimate already over cap → `FileTooLargeError` and a friendly message; bandwidth saved |
| `LocalStorage.assert_under_max` | after download, before delivery | actual on-disk size over cap → `StorageError`, file purged via job cleanup |
| `DeliveryService._deliver_via_link` | when serving via temp link | belt-and-suspenders — should never fire if storage check passed |

```
deploy/nl2/.env:
  MAX_FILE_SIZE_MB=2048   # 2 GiB; reject larger upstream files
```

If you serve a video community where 4 GiB rips are normal, raise it
deliberately and re-do §2.2 math first.

### 7.2 Job-time cap

`JOB_TIMEOUT_SECONDS` (default `1800` = 30 min) is wired into arq via
`app/infrastructure/queue/worker_settings.py` (`job_timeout=...`). When
the timeout fires, arq cancels the worker coroutine and the job is
re-queued unless `JOB_MAX_RETRIES` is exhausted. Anything longer is
almost always a stuck connection or an attempt to download a multi-hour
stream. Don't disable; tune.

### 7.3 Per-user concurrent-jobs cap

Different from per-minute rate ([`36-`](36-rate-limiting.md)) — this
limits how many of *one user's* jobs may be in-flight simultaneously.
Default: **2**. Adjust via `MAX_CONCURRENT_JOBS_PER_USER`.

Implemented in `EnqueueDownloadUseCase.execute`:

1. `JobsRepository.count_active_for_user(user_id)` runs a single
   `SELECT count(*)` filtered by `status IN ('pending','processing')`
   (uses the existing `ix_download_jobs_user_id` index).
2. If the count is `>= MAX_CONCURRENT_JOBS_PER_USER`, the use case
   raises `TooManyJobsError` (an `AppError` subclass) **before** any
   `download_jobs` row is created and **before** the queue is touched.
3. The bot's callback handler (and the test endpoint) already catches
   `AppError` and renders `exc.user_message` to the user — so the user
   sees `"Сейчас у вас уже выполняется несколько задач..."` instead of
   a silent enqueue.

> Without this cap, one user with `RL_USER_BURST=5/60s` can have 5
> concurrent active jobs, which is half of an `c=10` worker. Cap them
> to 2 and the worker stays multi-user.

### 7.4 Cleanup tunables

See [`23-`](23-cleanup-retention.md) §6. Most relevant for capacity:

- `CLEANUP_INTERVAL_SECONDS` — shorter = more frequent reclaim, more CPU
- `TEMP_LINK_TTL_SECONDS` — shorter = less storage required, less user-friendly
- `TMP_MAX_AGE_HOURS` — shorter = less scratch leak survival, more risk of nuking an in-flight job

---

## §8 — Scaling-up runbook

When the small profile no longer fits, take steps in this order. Each
step is reversible until the next is taken.

### 8.1 Step 1 — vertical NL-2 (no ADR needed)

| What | Why |
|---|---|
| Bigger NL-2 host (more cores, more RAM, faster disk, faster NIC) | The cheapest step; worker is the bottleneck for almost every profile |
| Bump `WORKER_CONCURRENCY` to match cores (rule of thumb: `min(cores, 8)`) | Use the bigger box |
| Mount `STORAGE_PATH` on a dedicated volume sized per §2.2 | Don't compete with logs |

Validate: re-run §6.2 concurrency test; record in §6.4 table; check
SLOs hold for one week.

### 8.2 Step 2 — vertical NL-1 (no ADR needed)

| What | Why |
|---|---|
| Bigger NL-1 host | Postgres and Redis on the same box; if either is starved, A1 falls |
| Move Postgres data dir to fast disk | If you see B5 saturation tickets, this fixes most of it |

### 8.3 Step 3 — separate Redis from Postgres on NL-1 (small ADR)

| What | Why |
|---|---|
| One container per service was already true; move Redis to its own dedicated volume / disk | When AOF fsync starts impacting Redis latency, separate the I/O domain |

ADR-light: document in `adr/` under "host topology change", no
architecture invariant changes.

### 8.4 Step 4 — second NL-2 (full ADR required)

| What | Why |
|---|---|
| Add NL-2b sharing the same Redis queue | Horizontal worker scale-out |
| Decide: shared `STORAGE_PATH` (NFS/object) or per-host with delivery routing? | Each option has trade-offs |
| Decide: same egress IP or different? Per-platform routing? | §3.4 |

This crosses [`adr/0005`](adr/0005-locked-architectural-assumptions.md)'s
"two servers, NL-1 / NL-2" assumption. **Mandatory ADR**, mandatory
update to [`02-`](02-architecture.md), [`09-`](09-queue-and-workers.md),
[`11-`](11-storage-strategy.md). Don't take this step without one.

### 8.5 Step 5 — DB scaling (full ADR required)

If `download_jobs` actually grows past where a single Postgres can
serve it (years away on the math in §4), partition by month + a
read-replica for analytics. Not a primary-replica failover unless
you've done the §9 disaster-tolerance work.

---

## §9 — Disaster-tolerance vs capacity

Capacity ≠ resilience. A bigger box with no backups is a bigger fire.
Before any §8 step:

- Backups verified (`22-` §6, monthly drill).
- Cleanup verified (`23-` §11 quarterly check).
- Privacy retention verified (`34-` §9.2 quarterly check).
- SLO history saved (`35-` §9 budget hasn't been > 75 % burnt for
  4 weeks straight — if it has, fix the regression *before* scaling
  past it).

If any of these aren't green, scaling makes the eventual fire bigger.

---

## §10 — Capacity-planning worksheet

Fill this out before requesting infra changes. Save in the deploy
ticket.

```
=== Today's load (last 28 d, from 35- §3 dashboards) ===
A1 % met:                       ____
A4 p95 (≤200 MB):               ____ s
A5 p95 (>200 MB):               ____ s
Avg jobs/hour:                  ____
Peak jobs/hour (max 5-m window):____
Avg file size:                  ____ MiB
% files > 50 MB:                ____
Avg job seconds (job_completed):____ s
WORKER_CONCURRENCY today:       ____

=== Compute ===
Theoretical jobs/h max (§1.1):  ____
Saturation point (75 %):        ____
Headroom right now:             ____ %

=== Required ===
Target jobs/hour (planning horizon: 6 mo): ____
Required WORKER_CONCURRENCY (§1.1):        ____
Required free disk (§2.2):                 ____ GiB
Required egress capacity (§3.1):           ____ Mbps in / out
Required upstream tightening (§3.4)?       ____ (yes/no)

=== Decision ===
Step from §8 to take:                       ____ (1/2/3/4/5)
ADR needed?                                 ____ (yes/no — see §8)
Cost impact (egress + storage + box):       $ ____
Reversibility plan:                         ____
SLO budget posture allows this?             ____ (yes/no — §9)
```

---

## §11 — Common mistakes

| # | Mistake | Symptom / consequence | Fix |
|---|---|---|---|
| 1 | Doubling `WORKER_CONCURRENCY` without checking upstream | jobs/h doesn't move; cost doubles; per-domain throttling worsens | Run §6.2 first; check §6.5 row 1 |
| 2 | Treating RPS as the unit | meaningless number; nothing in the system measures RPS | Use jobs/h × avg_job_seconds (§1.1) |
| 3 | Loosening `TEMP_LINK_TTL_SECONDS` for "convenience" without re-doing §2.2 | disk fills 2× faster than expected | Do the math; document the change |
| 4 | Sharding Postgres before cleanup is fixed | shards full of garbage rows; same problem, more boxes | `34-` §5 first, scale second |
| 5 | Adding NL-2b without an ADR | architecture drifts; no doc reflects the new topology | §8.4 ADR is mandatory |
| 6 | Storage growth alarms but no one reads `35-` §9 budget | scaling decision made under SLO pressure (panicked) | Decide capacity at <25 % budget burn, not 100 % |
| 7 | Load-testing against real upstream platforms | IP gets banned; users on shared egress suffer | Use small clips, low rates, or a synthetic provider |
| 8 | Disabling `JOB_TIMEOUT_SECONDS` for "long videos" | one stuck job permanently occupies a worker slot | Tune up (e.g. 3600), don't disable |
| 9 | Picking a Postgres autovacuum schedule that competes with backup | A1 drops during the backup window | Schedule backup outside autovacuum's window; observe with `pg_stat_progress_vacuum` |
| 10 | Counting `request_state` Redis growth as a capacity issue | over-provisioning Redis | §5 — Redis is never the bottleneck on RAM here |
| 11 | Letting `MAX_FILE_SIZE_MB` drift without storage growth math | a single 8 GiB job destroys the §2.2 budget | Cap stays in line with disk; raise both together |
| 12 | Comparing yourself to last week's traffic during a viral spike | tuning to peak-of-peak makes you waste $ during normal | Plan to peak-of-mean (P95 of hourly), not peak-of-peak |
| 13 | Forgetting that egress (§3.3) is the most expensive line item | bill-shock at month end | Always track Mbps + GiB/month, not just %CPU / disk |
| 14 | Adding more workers when the bottleneck is ffmpeg-CPU on one job | new workers idle; one job still slow | Vertical CPU upgrade or `-preset ultrafast` for the format |
| 15 | Acting on §10 worksheet without §9 (resilience) check | scaled-up but undefended system | The two checks are paired |

---

## §12 — Quick reference

```
Capacity unit:    jobs/hour = WORKER_CONCURRENCY × 3600 / avg_job_seconds
Saturation:       plan for 75% of theoretical max
Storage:          jobs/h × file_size × (TTL_h) × 1.3
Egress (large):   jobs/h × file_size × ~2  (delivery_factor)
DB growth:        ~3 GiB/year/360-jobs-h (no purging)
Redis RAM:        < 10 MiB at any realistic scale
SLO posture:      scale at < 25% budget burn; never under 100% burn

Scale-up order:   vertical NL-2 → vertical NL-1 → separate volumes
                  → second NL-2 (ADR) → DB sharding (ADR)

Caps:
  MAX_FILE_SIZE_MB            2048 (2 GiB)
  JOB_TIMEOUT_SECONDS         1800 s
  MAX_CONCURRENT_JOBS_PER_USER 2

Don't:
  - load-test against real upstream platforms
  - scale past unfixed SLO regressions
  - cross adr/0005 without an ADR (NL-2b, sharding)
```

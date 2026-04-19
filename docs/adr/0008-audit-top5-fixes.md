# ADR-0008 — Top-5 audit fixes (retry semantics, atomic caps, log redaction, immutable images, atomic temp-link counter)

- **Status:** Accepted
- **Date:** 2026-04-19
- **Deciders:** project core
- **Tags:** reliability, security, ops, queue, db
- **Supersedes:** —
- **Superseded by:** —
- **Refines:** ADR-0007 §2 — clarifies the contract for the
  `JobStatus` value used in `dwtgbot_jobs_status_changed_total`
  labels: `FAILED` is now emitted exactly once per job lifecycle
  (terminal attempt only), so retry windows are no longer
  miscounted as failures.

---

## 1. Context

A full project audit (Architect / Backend / DevOps / SRE / Security / QA
hats) surfaced 5 top-priority issues that, taken together, broke the
contracts that the rest of the docs assume. Without this ADR the docs
contradict the code and future agents will faithfully revert the fixes.

The five issues:

1. **Retry was a no-op.** `ProcessDownloadUseCase._run` caught every
   `AppError` / `Exception` and called `_fail`, swallowing the
   exception. arq saw a successful coroutine and never re-scheduled.
   `JOB_MAX_RETRIES` had no effect; transient blips became permanent
   `FAILED` rows.
2. **Per-user concurrency cap had a TOCTOU race.** `EnqueueDownloadUseCase`
   did `count_active_for_user` then `create` in two coroutines. Two
   concurrent `/start`-button clicks could both observe
   `active == cap-1` and both insert, busting the cap and starving the
   worker pool.
3. **Temp-link tokens leaked into nginx access logs** via the default
   `$request` field. A 256-bit URL secret with a 1 h lifetime was
   then usable by anyone with read access to Loki / `access.log` for
   the lifetime of the link.
4. **Compose used mutable `:latest` image tags** (`${IMAGE_BOT:-...:latest}`).
   A first boot or a forgotten env override silently shipped whatever
   was tagged `:latest` at pull time. Rollbacks were ambiguous; CI
   races could overwrite tags between two main builds.
5. **Temp-link `downloads_count` increment was not atomic.** The
   `/d/{token}` handler did `link.register_use()` then
   `temp_links_repo.update(link)` in two separate steps. Two parallel
   requests on a `max_downloads=1` link could both see
   `downloads_count == 0` and both be served — defeating the
   single-use guarantee for private files.

Non-goals of this ADR:

- DLQ / re-driver UI (still in [`09-`](../09-queue-and-workers.md) §13 future).
- Replacing arq with a different queue.
- Hashing tokens at rest (separate threat-model exercise).

---

## 2. Decision

We adopt five small, surgical fixes. None of them rewrite a layer; each
one closes one race / leak / loop hole. They are deployed together
because they share verification effort (one full test run) and rollback
strategy (revert the merge commit).

### 2.1 Retryability is a property of the exception class

- `AppError.is_retryable: bool = False` is the default.
- `ProviderError`, `DownloadError`, `DownloadTimeoutError` set
  `is_retryable = True` (transient by definition).
- `MediaNotFoundError`, `MediaPrivateError`, `FileTooLargeError`,
  `FfmpegError` pin it back to `False` (permanent).
- `ProcessDownloadUseCase._run` re-raises retryable / unexpected
  exceptions so arq increments `job_try` and re-schedules with
  backoff. Permanent errors still go through `_fail` (unchanged).
- A new public method
  `ProcessDownloadUseCase.mark_terminally_failed(job_id, exc)` is
  called by the arq task wrapper **only on the last attempt**
  (`ctx["job_try"] >= ctx["max_tries"]`). It is idempotent: if the
  job is already in a terminal state (`DONE` / `FAILED`), it is a
  no-op. If `ctx` is missing the keys (mis-configured pool), it
  fails-closed and marks `FAILED` immediately rather than looping
  silently.

### 2.2 Per-user concurrency cap: atomic `create_if_under_cap`

- Added `JobsRepository.create_if_under_cap(job, *, cap) -> DownloadJob | None`.
- `SqlAlchemyJobsRepository.create_if_under_cap` runs a single
  transaction:
  1. `SELECT pg_advisory_xact_lock(:user_id)` — serialises only
     concurrent enqueue checks for the **same** user.
  2. `SELECT count(*) ... WHERE status IN (PENDING, PROCESSING)`.
  3. If `count >= cap` → return `None` (rolls back automatically).
  4. Otherwise `INSERT ... RETURNING`.
- `EnqueueDownloadUseCase.execute` no longer does count/create as two
  steps; it calls `create_if_under_cap` and converts `None` into
  `TooManyJobsError`.
- The advisory lock is released automatically at COMMIT — no
  bookkeeping, no leak risk on crash.

### 2.3 Token redaction in nginx access logs

- New `map $request_uri $safe_request_uri { ... }` block in
  `deploy/nginx/nginx.conf` rewrites the `/d/<token>` segment to
  `/d/<redacted>` **at log-emission time only**.
- `log_format` switched from `$request` (which embeds the full URI)
  to discrete `$request_method`, `$safe_request_uri`,
  `$server_protocol` fields.
- `proxy_pass` and `X-Accel-Redirect` still see the real URI —
  functionality is unchanged; only the log line is sanitised.

### 2.4 Image tags must be immutable

- All compose `image:` references that point at our images use the
  fail-fast form `${VAR:?message}`. Forgetting `IMAGE_BOT` (etc.) now
  fails `docker compose up` with a clear pointer to the variable.
- `.env.example` placeholders renamed to `sha-REPLACE_ME` so a copy-
  paste install never silently runs `:latest`.
- `.github/workflows/build-images.yml` no longer attaches `:latest`
  to `main` builds. `:latest` is reserved for `vX.Y.Z` semver tags
  (where it is unambiguous).
- A `concurrency:` group serialises image builds for the same git ref.
- `.github/workflows/deploy.yml` exports
  `IMAGE_*=ghcr.io/<repo>-<svc>:sha-<short>` before invoking
  `deploy_update.sh`, so deploys on running hosts pin to the tested
  build.

### 2.5 Temp-link counter: atomic `try_register_use`

- Added `TempLinksRepository.try_register_use(token) -> TempLink | None`.
- `SqlAlchemyTempLinksRepository.try_register_use` is a single
  `UPDATE temp_links SET downloads_count = downloads_count + 1,
  is_active = CASE WHEN downloads_count + 1 >= max_downloads THEN
  false ELSE true END, updated_at = now WHERE token = :t AND
  is_active AND expires_at > :now AND downloads_count < max_downloads
  RETURNING *`.
- Returns the post-update entity on success or `None` when the WHERE
  clause matched no rows (token missing, inactive, expired, already
  at the cap, or a parallel request just consumed the last slot).
- `app/api/public/downloads.py` reorders the handler so cheap
  read-only checks (path-traversal validation, file existence on
  disk) happen **before** the atomic increment. A path-traversal
  probe / cleanup-race no longer burns a download slot.

---

## 3. Consequences

### 3.1 Positive

- Transient upstream blips (DNS, 5xx, TLS, slow socket) now actually
  retry up to `JOB_MAX_RETRIES` times before any user sees a failure
  message — matches what `JOB_MAX_RETRIES` was always documented to do.
- Per-user cap is a hard contract again; A2 (failure rate) and B4
  (worker saturation) panels can be trusted under bursty load.
- A leaked Loki dump no longer hands out one hour of bearer tokens.
- A clean checkout + `docker compose up` cannot accidentally roll
  back the cluster to a stale `:latest`. Deploys are bit-for-bit
  reproducible (same SHA → same image content).
- Single-use temp links honour their `max_downloads=1` contract under
  any concurrency.

### 3.2 Negative / accepted trade-offs

- One extra round-trip per enqueue (the advisory lock acquire). At
  cap=2 / per-user enqueue rate ≤ a few per minute, this is invisible.
- `try_register_use` does an `UPDATE ... RETURNING` that wakes any
  trigger / replication on `temp_links` even when the WHERE filter
  rejects the row. Negligible because we do not have triggers there.
- Operators must remember to set `IMAGE_*` variables. Mitigation: the
  failure mode is loud (compose refuses to start) and the example
  `.env` files have explicit `sha-REPLACE_ME` placeholders.
- Mid-retry, the DB row stays in `PROCESSING` (not "rewound" to
  `PENDING`). This is a deliberate choice — see [`09-`](../09-queue-and-workers.md)
  §6 for the reasoning. The per-user cap counter therefore correctly
  reserves a slot during the retry window.

### 3.3 Operational impact

- **Deploy:** the first deploy after this ADR **must** include
  `IMAGE_*` env vars (or the compose call fails). CI/CD already
  exports them in `deploy.yml`; manual deploys must export them
  before running `deploy_update.sh`. Update local `.env` files first.
- **Monitoring:** `arq_queue_depth` and the `worker_active_jobs`
  gauges (ADR-0007) now correctly reflect retried jobs. No alert
  changes needed.
- **Logging:** any tooling that grepped `access.log` for the full
  `/d/<token>...` URI must be re-pointed at the worker's
  `temp_link_served` event (which still has the truncated `token=...`
  prefix). The full URI is no longer recoverable from logs — by design.

---

## 4. Alternatives considered

### 4.1 Keep the swallow-and-fail behaviour, add an out-of-band retry job
A second arq task type that scans `download_jobs` for
`status=FAILED AND retries_count < max` and re-enqueues. Rejected:
introduces a second source of truth for retries, doubles the surface
area for bugs, and still leaves the user notified before retries even
start.

### 4.2 Per-user cap via a Redis token bucket instead of an advisory lock
Cheaper round-trip, but Redis is not the system of record for jobs;
keeping the cap in Postgres lets us recover deterministically after
a Redis flush. Advisory locks are scoped to one user-id and freed on
COMMIT, so contention is bounded.

### 4.3 Hash tokens before logging instead of redacting
Hashes preserve correlation across log lines but still allow an
offline brute-force if the log line includes the original URL
elsewhere. Full redaction at log emission time is simpler and
sufficient — operators can correlate via `request_id` / `job_id`
which are already bound by structlog (see [`14-`](../14-logging-observability.md)).

### 4.4 Pin to digests (`@sha256:...`) instead of `:sha-XXXXXXX` tags
Strictly more reproducible, but breaks human readability in compose
files and `docker compose ps` output. Short SHA tags are immutable
in practice (CI never re-pushes them) and remain readable. We can
upgrade to digests later without a contract change.

### 4.5 SELECT ... FOR UPDATE on the temp_link row
Equivalent locking semantics, but two round-trips (read then write).
The single `UPDATE ... WHERE ... RETURNING` is one round-trip and
maps directly onto the desired contract.

---

## 5. Compliance

- **Code:** the contract is enforced by:
  - `app/exceptions.py` (`is_retryable` flag matrix, asserted by
    `app/tests/test_retry_semantics.py`).
  - `JobsRepository.create_if_under_cap` — `abstractmethod`; any
    new fake/impl must implement it.
  - `TempLinksRepository.try_register_use` — same.
  - Compose files using `${VAR:?...}` — `docker compose config` fails
    when the var is unset.
  - `.github/workflows/build-images.yml` — `:latest` only on tag
    builds (unit-checked at lint of the workflow if a future PR
    adds it).
- **Reviews:** PR template asks "did you keep an exception's
  `is_retryable` flag?" and "did you preserve the atomic
  contracts?" — see [`28-implementation-playbook.md`](../28-implementation-playbook.md)
  §10 (added in this PR).
- **Docs:** updated in lock-step with this ADR:
  - [`09-queue-and-workers.md`](../09-queue-and-workers.md) §6, §8, §11.
  - [`10-temp-links-and-delivery.md`](../10-temp-links-and-delivery.md)
    §3 (counter contract) + §9 (race-handling).
  - [`12-db-schema.md`](../12-db-schema.md) §6 (concurrency primitives).
  - [`16-error-handling.md`](../16-error-handling.md) §1 + §2 (retryable column).
  - [`17-security.md`](../17-security.md) §9 (log redaction).
  - [`21-cicd.md`](../21-cicd.md) §4 (immutable tags).

---

## 6. References

- Code:
  - `app/exceptions.py` (`is_retryable`)
  - `app/application/use_cases/process_download.py`
    (`_run`, `mark_terminally_failed`)
  - `app/application/use_cases/enqueue_download.py`
  - `app/api/public/downloads.py`
  - `app/infrastructure/db/repositories/jobs_repo_impl.py`
    (`create_if_under_cap`)
  - `app/infrastructure/db/repositories/temp_links_repo_impl.py`
    (`try_register_use`)
  - `app/infrastructure/queue/tasks.py` (terminal-mark wrapper)
  - `deploy/nginx/nginx.conf` (`map $safe_request_uri`)
  - `deploy/nl{1,2}/docker-compose.yml`, `deploy/nl{1,2}/.env.example`
  - `.github/workflows/build-images.yml`,
    `.github/workflows/deploy.yml`
- Tests:
  - `app/tests/test_retry_semantics.py`
  - `app/tests/test_enqueue_cap_atomic.py`
  - `app/tests/test_downloads_endpoint.py`
  - `app/tests/integration/test_advisory_lock_postgres.py`
    (opt-in, requires Postgres — see file header)
- Related ADRs: ADR-0007 (metrics) — retry behaviour now matches the
  status-changed contract that ADR §2.2 documents.

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2026-04-19 | Accepted | Drafted + merged together with the five fixes |

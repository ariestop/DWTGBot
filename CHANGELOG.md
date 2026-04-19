# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Architectural rationale lives in `docs/adr/`. Each entry below links to
the relevant ADR when one applies.

---

## [Unreleased]

### Fixed — audit Top-5 (ADR-0008)

The first project-wide audit (Architect / Backend / DevOps / SRE /
Security / QA hats) surfaced five top-priority issues. They are
shipped together because they share verification effort and rollback
strategy.

- **Retry semantics actually retry now.** `AppError` gained an
  `is_retryable` class flag; `ProviderError`, `DownloadError`,
  `DownloadTimeoutError` are retryable; `MediaPrivateError`,
  `MediaNotFoundError`, `FileTooLargeError`, `FfmpegError`,
  `StorageError`, config errors are not. The use case re-raises
  retryable / unexpected errors so arq schedules a real retry; the
  worker wrapper marks `FAILED` only on the terminal attempt
  (`mark_terminally_failed`). Previously, every error was caught and
  marked `FAILED` on the first try, making `JOB_MAX_RETRIES` a no-op.
  - Files: `app/exceptions.py`,
    `app/application/use_cases/process_download.py`,
    `app/infrastructure/queue/tasks.py`.
  - Tests: `app/tests/test_retry_semantics.py`.
  - Docs: `docs/16-error-handling.md` §1 + §2,
    `docs/09-queue-and-workers.md` §6 + §8.

- **Per-user concurrency cap is now atomic.** Added
  `JobsRepository.create_if_under_cap(job, *, cap)`; the SQLAlchemy
  implementation acquires `pg_advisory_xact_lock(user_id)` and runs
  the `count + insert` in one transaction. `EnqueueDownloadUseCase`
  now calls this single primitive instead of `count_active_for_user`
  followed by `create`. Concurrent enqueues for the same user can no
  longer both observe `active < cap` and both insert.
  - Files: `app/domain/repositories/jobs_repo.py`,
    `app/infrastructure/db/repositories/jobs_repo_impl.py`,
    `app/application/use_cases/enqueue_download.py`.
  - Tests: `app/tests/test_enqueue_cap_atomic.py`,
    updates to `app/tests/test_enqueue_use_case.py`,
    `app/tests/test_job_metrics.py`,
    integration test `app/tests/integration/test_advisory_lock_postgres.py`
    (opt-in, requires Postgres).
  - Docs: `docs/12-db-schema.md` §10.1.

- **Temp-link tokens redacted from nginx access logs.** New
  `map $request_uri $safe_request_uri` block rewrites
  `/d/<token>` to `/d/<redacted>` at log-emission time only;
  `log_format` switched from the catch-all `$request` to discrete
  fields using `$safe_request_uri`. Application functionality
  (`proxy_pass`, `X-Accel-Redirect`) sees the real URI; only the log
  line is sanitised.
  - Files: `deploy/nginx/nginx.conf`.
  - Docs: `docs/17-security.md` §2 + §9.

- **Image deployments are immutable by default.** Compose `image:`
  references switched to fail-fast `${IMAGE_*:?...}` form — a missing
  variable now fails `docker compose up` with a pointer to the
  variable rather than silently pulling `:latest`. The build workflow
  no longer attaches `:latest` to `main` builds (it is reserved for
  `vX.Y.Z` semver tags). The deploy workflow exports
  `IMAGE_*=ghcr.io/<repo>-<svc>:sha-<short>` derived from the chosen
  ref before invoking `deploy_update.sh`. A `concurrency:` group on
  `build-images.yml` prevents tag races for the same git ref.
  - Files: `deploy/nl{1,2}/docker-compose.yml`,
    `deploy/nl{1,2}/.env.example`,
    `.github/workflows/build-images.yml`,
    `.github/workflows/deploy.yml`.
  - Docs: `docs/21-cicd.md` §4.2.

- **Temp-link counter is now atomic.** Added
  `TempLinksRepository.try_register_use(token)`; the SQLAlchemy
  implementation runs a single `UPDATE temp_links SET
  downloads_count = downloads_count + 1, is_active = CASE WHEN ... END
  WHERE token = :t AND is_active AND expires_at > now AND
  downloads_count < max_downloads RETURNING *`. The `/d/{token}`
  handler runs cheap pre-checks (path traversal validation, file
  existence on disk) **before** the atomic call so probes / cleanup
  races never burn a download slot.
  - Files: `app/domain/repositories/temp_links_repo.py`,
    `app/infrastructure/db/repositories/temp_links_repo_impl.py`,
    `app/api/public/downloads.py`.
  - Tests: `app/tests/test_downloads_endpoint.py`,
    updates to `app/tests/test_temp_links.py`.
  - Docs: `docs/10-temp-links-and-delivery.md` §3 + §4.

### Documentation

- New ADR-0008 (`docs/adr/0008-audit-top5-fixes.md`) captures the
  context, the five decisions, alternatives considered, and the
  compliance hooks that prevent regressions.
- Updated `docs/16-error-handling.md`, `docs/09-queue-and-workers.md`,
  `docs/10-temp-links-and-delivery.md`, `docs/12-db-schema.md`,
  `docs/17-security.md`, `docs/21-cicd.md`, `docs/28-implementation-playbook.md`
  in lock-step with the code change. The playbook gained §2.3
  ("Contracts to preserve when touching hot paths") so future agents
  get a one-page anti-regression list.

### Migration / operator notes

- **Operators on existing hosts**: before the next deploy, set
  `IMAGE_BOT`, `IMAGE_API`, `IMAGE_WORKER`, `IMAGE_BACKUP` in
  `deploy/nl1/.env` and `deploy/nl2/.env`. `docker compose up` now
  refuses to start with an unset image var. The CI deploy workflow
  already exports them on its own; no action required for
  CI-driven deploys.
- **Log shippers**: anything that grepped nginx `access.log` for the
  full `/d/<token>` URI must re-point at the worker's
  `temp_link_served` event (which still carries a truncated `token`
  prefix). Full URIs are no longer recoverable from logs — by design.
- **No DB migration**: the new repository methods reuse existing
  columns. No `alembic upgrade head` required for these fixes.

### Tests

- Full suite: `python3 -m pytest app/tests` — 220 passing
  (one pre-existing baseline failure in `test_filenames.py` is
  unrelated and tracked separately).
- New opt-in integration test
  `app/tests/integration/test_advisory_lock_postgres.py` exercises
  the real `pg_advisory_xact_lock` against a Postgres instance
  (skipped when `DWTGBOT_TEST_POSTGRES_URL` is not set).

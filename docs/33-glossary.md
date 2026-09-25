# 33 — Glossary

> Status: Stable
> Audience: everyone

A canonical dictionary of terms used across the project.
**Pin this in your tab** while reading other docs.

---

## A

**ADR (Architectural Decision Record).**
A short markdown file under `docs/adr/` recording a single
architectural decision: context, decision, consequences. Required
when changing a [locked decision](#l).

**`AppError`.**
The root exception class for all application-level errors. Carries
a user-friendly `user_message`. See
[`16-error-handling.md`](16-error-handling.md).

**`arq`.**
The Redis-backed task queue we use to ship jobs from NL-1 (bot) to
NL-2 (worker). See [`09-queue-and-workers.md`](09-queue-and-workers.md).

**Audit log.**
Append-only `audit_logs` table recording security-relevant
actions (token issuance, deletions). Not user-readable.

---

## B

**`BaseProvider`.**
Abstract base class every platform integration extends. Defines
`get_info`, `build_options`, `download`. See
[`07-provider-architecture.md`](07-provider-architecture.md).

**Boundary.**
The point where the system interacts with the outside world:
Telegram updates, HTTP requests, queue messages. Boundaries are
where errors get translated into `AppError` and logs get
correlation IDs bound.

**`BotContainer`.**
A small dataclass-based DI container holding the dependencies
needed by bot handlers. Lives in `app/bot/container.py`.

---

## C

**Callback data.**
The opaque (≤ 64-byte) payload Telegram returns to your bot when
the user clicks an inline button. We use a small codec
(`app/bot/callbacks/`) with a single-letter discriminator.

**Cleanup worker.**
Runs on NL-2, periodically removes expired temp links, scratch
directories, and old job folders. See
[`23-cleanup-retention.md`](23-cleanup-retention.md).

**Compose fragment.**
`deploy/compose/control.yml` / `deploy/compose/media.yml` — единственное
место, где описаны сервисы. Стеки `single`, `nl1`, `nl2` подключают их
через `include` ([ADR-0011](adr/0011-single-server-topology.md)).

**Composition root.**
The single place (`app/composition/`) where concrete
infrastructure is wired into the application. Every entrypoint
calls `build_bot()` / `build_worker()` / `build_api()`.

**Container (the noun).**
1. *Docker container* — a process running a Docker image.
2. *DI container* — `BotContainer`, `WorkerComposition`,
   `ApiComposition`. Context disambiguates.

**Control plane.**
Synonym for **NL-1**: the server hosting bot, Postgres, Redis, and
the backup job. В `single` — те же сервисы (фрагмент
`deploy/compose/control.yml`) на общем хосте.

**Correlation ID.**
A short identifier (`request_id`, `job_id`, `user_id`, `chat_id`,
`update_id`) bound into the structured-log context so all log
events for one user action can be stitched together. See
[`14-logging-observability.md`](14-logging-observability.md).

---

## D

**Dead-letter.**
A job that has exceeded its retry budget. We log it and mark it
`failed`; we don't keep a separate dead-letter queue today.

**Delivery service.**
`app/application/services/delivery_service.py`. Decides whether to
upload the result file directly to Telegram or issue a temporary
HTTPS link.

**`DownloadJob`.**
The domain entity that represents one user's download request.
Mirrored 1:1 to the `download_jobs` table.

**`DownloadOption`.**
One choice the user can pick (e.g. "Видео 720p", "Только аудио
MP3"). Built by the provider via `build_options()`.

**`DownloadResult`.**
What `BaseProvider.download(...)` returns: a tuple of files, total
size, primary mime, title, kind.

---

## E

**Enqueue.**
The act of pushing a job onto the arq queue (Redis). Done by
`EnqueueDownloadUseCase`.

**`ensure_within(root, candidate)`.**
A path-traversal defence. Returns `candidate` only if it resolves
inside `root`; otherwise raises. Use this for **every** filesystem
path derived from outside input.

**Extension point.**
A place in the system designed to be extended without touching the
core. Catalogue in [`32-roadmap-and-extension-points.md`](32-roadmap-and-extension-points.md).

---

## F

**ffmpeg.**
External binary used for media muxing/demuxing/transcoding. Called
through `FFmpegRunner` (or via yt-dlp's postprocessors). Never
called as a shell string.

**Format spec.**
yt-dlp's selector string, e.g.
`bestvideo[height<=720]+bestaudio/best`. Owned by the provider
class.

---

## G

**Gallery.**
An Instagram (or future X) post containing multiple media items.
`MediaInfo.kind = MediaKind.GALLERY`.

---

## H

**Hexagonal-lite.**
Our architectural style: domain ⊂ application ⊂ infrastructure with
inward-only dependencies, but pragmatic about not implementing every
formal port/adapter. See [`02-architecture.md`](02-architecture.md).

**Hot fix.**
A change applied directly on a running container that is not (yet)
in the image. Always followed by a real release; otherwise it
disappears on next pull. See **Hot fix on host** for the riskier variant.

**Hot fix on host.**
A change applied directly on the **host filesystem** of NL-1 / NL-2
(e.g. editing a mounted `.env`, replacing a binary inside the storage
volume, hand-editing an Nginx conf bind-mounted into the container)
without going through git → CI → image build → deploy. **Forbidden in
production except for true emergencies**, because it leaves the running
state and the source of truth out of sync — the next `deploy_update.sh`
silently reverts it. If used: log the change, open a follow-up PR
within 24 h, and prove the gap is closed. See
[`24-runbooks.md`](24-runbooks.md) emergency checklist and
[`28-implementation-playbook.md`](28-implementation-playbook.md)
anti-patterns.

---

## I

**Idempotency.**
The property that the same operation can be safely repeated. We
enforce it on enqueue with deterministic `_job_id`s so arq dedupes
retries.

**Inactive link.**
A row in `temp_links` with `is_active = false`. The token no longer
resolves (Nginx returns `410 Gone` via the FastAPI router). Set by:
(a) the cleanup worker when `expires_at` is in the past, or (b) an
operator during a privacy / abuse incident. Reaching `max_downloads`
does not deactivate the row — it only refuses new downloads. The **row** stays
forever (audit); the **file** it pointed at is removed by the next
cleanup cycle. See [`23-cleanup-retention.md`](23-cleanup-retention.md)
§4 and [`34-data-retention-and-privacy.md`](34-data-retention-and-privacy.md)
§4.

**Inline keyboard.**
Telegram's `InlineKeyboardMarkup` — clickable buttons attached to a
message. Their actions are encoded as **callback data**.

---

## J

**Job.**
Two related-but-distinct meanings:
1. A `DownloadJob` (domain entity / DB row).
2. An **arq job** — the queued task that processes a `DownloadJob`.
Same conceptual thing; different lifecycles.

---

## K

**`MediaKind`.**
Enum: `VIDEO`, `AUDIO`, `PHOTO`, `GALLERY`. Drives both download
and delivery logic.

---

## L

**Locked decision.**
A foundational architectural choice that requires an **ADR** to
change. List in [`28-implementation-playbook.md`](28-implementation-playbook.md)
§5.

**LTS.**
Long-term support. We target Ubuntu 24.04 **LTS**.

---

## M

**Media plane.**
Synonym for **NL-2**: worker + Nginx + storage + cleanup.

**`MediaInfo`.**
Domain entity returned by `BaseProvider.get_info(url)`. Carries
title, kind, duration, items, raw provider blob.

**Migration.**
An alembic revision file under `migrations/versions/`. The only
sanctioned way to change the DB schema.

---

## N

**Nginx.**
Reverse proxy on NL-2. Terminates TLS, routes to FastAPI, serves
large files via `X-Accel-Redirect` from internal locations.

**NL-1.**
Server #1 in the topology — the **control plane**: bot, Postgres,
Redis, backup job. Private to the operator network.

**NL-2.**
Server #2 in the topology — the **media plane**: worker, Nginx,
Certbot, cleanup, file storage volume. The only host with public
inbound (80/443).

---

## O

**Option.**
Short for `DownloadOption`. The user picks one option per job.

**Orphan file.**
A file inside `STORAGE_PATH/jobs/...` whose corresponding
`download_jobs` row was deleted (e.g. by a manual purge, a botched
restore, or a failed transactional rollback) — or, less often, a file
on disk that no `temp_links` row ever referenced. Orphans waste disk
without serving anyone and **never** age out via the normal cleanup
worker (it drives off DB rows, not the filesystem). Detection: SQL
audit query in [`23-cleanup-retention.md`](23-cleanup-retention.md)
Appendix; reclamation: operator-driven `rm -rf` after manual review.
Distinct from a [**scratch leak**](#s) (in `STORAGE_TMP_PATH`).

---

## P

**Platform.**
Enum value (`youtube`, `instagram`, …). Maps URL host → provider.

**`Provider`.**
The protocol implemented by `BaseProvider` subclasses; what the
application layer programs against.

**Public link.**
The HTTPS URL that delivers a large file (`https://media.example.com/d/<token>`).
Time-limited, download-count-limited, served via Nginx.

---

## Q

**Queue.**
Two related-but-distinct surfaces:
1. The **abstract** `QueueProducer` (application layer).
2. The **concrete** arq pool (infrastructure).
Composition root binds the two.

---

## R

**Readiness.**
"Can I do useful work right now?" — checks dependencies (DB, Redis).
See [`15-healthchecks.md`](15-healthchecks.md).

**`RequestStateStore`.**
Redis-backed key-value store used during the analyze→options→enqueue
flow to remember what the user is doing without putting it in
callback data. TTL-bounded.

**Runbook.**
A step-by-step incident playbook. Catalogue in
[`24-runbooks.md`](24-runbooks.md).

---

## S

**`single`.**
Однохостовая топология: стек `deploy/single` запускает control и media
plane на одном сервере, разделяя их сетями Docker. Противоположность —
`split` (NL-1 + NL-2). См. [ADR-0011](adr/0011-single-server-topology.md).

**`sanitize_filename(name)`.**
Strips path separators, NUL bytes, and dangerous characters from a
candidate filename. Always used before writing user-provided names.

**Scratch directory.**
A temporary working directory under `STORAGE_TMP_PATH/...` used by
yt-dlp / ffmpeg during a job. Cleaned by `cleanup.sh` (`find -mmin
+TMP_MAX_AGE_HOURS×60 -delete`) if a job dies before it finishes.

**Scratch leak.**
A scratch directory left behind on NL-2 disk after a worker crash,
hard kill, or container restart mid-download. The bytes belong to a
job that no longer exists in the queue or the DB; nothing in the
running system will ever look at them again. Reaped by `cleanup.sh`
once they cross `TMP_MAX_AGE_HOURS` (default 24 h). Symptom: disk
usage in `STORAGE_TMP_PATH` grows but `STORAGE_PATH` doesn't. See
[`23-cleanup-retention.md`](23-cleanup-retention.md) §3.3.
Distinct from an [**orphan file**](#o) (in `STORAGE_PATH/jobs/`).

**Secret.**
Any value that grants access (BOT_TOKEN, DB password, Redis
password). Lives only in `.env` files; never logged; never in git.

**Sender.**
`TelegramSender` — thin wrapper over the bot HTTP API used by the
worker to upload files / send messages.

**`Settings`.**
The pydantic-settings model in `app/config.py`. The single source
of configuration; everything else reads from it.

**Smoke test.**
A minimal end-to-end test, post-deploy: paste a known good URL and
verify the file arrives.

**SQLAlchemy.**
Our ORM. Async. Used only inside `app/infrastructure/db/*` and
repository implementations.

**State store.**
See `RequestStateStore`.

**`STORAGE_PATH`.**
The root directory on NL-2 under which all per-job folders live.
Mounted into both the worker and Nginx containers (read-only for
nginx).

---

## T

**`TempLink`.**
Domain entity (and `temp_links` row) representing one tokenised
public URL: token, file path, expiry, max downloads, counter.

**`TempLinkService`.**
Application service that issues, redeems, and revokes temp links.
Owns the policy (TTL, max downloads, audit logging).

**TG / Telegram.**
The user-facing platform. We use the Bot API (no MTProto / userbot).

**Three-tier exception pattern.**
The standard handler shape:
1. specific `AppError` subclasses (info/warning + friendly message),
2. generic `AppError` (warning + friendly message),
3. `Exception` (logger.exception + generic message).
Mandatory at every entry point. See
[`16-error-handling.md`](16-error-handling.md).

**Token (temp link).**
A URL-safe random string (issued by `TempLinkService`). Single use
of "look up the file by token" → 32+ bytes of entropy → infeasible
to guess.

**Topology.**
`single` — control и media plane на одном хосте (`deploy/single`), или
`split` — NL-1 (control plane) + NL-2 (media plane). См.
[ADR-0011](adr/0011-single-server-topology.md),
[`02-architecture.md`](02-architecture.md), [`19-docker-architecture.md`](19-docker-architecture.md).

---

## U

**Use case.**
A class in `app/application/use_cases/` orchestrating one user
intent (`AnalyzeLinkUseCase`, `EnqueueDownloadUseCase`,
`ProcessDownloadUseCase`). Talks to repositories and services via
their abstractions.

---

## V

**Verb noun event names.**
Convention for log events: `download_pipeline_started`,
`temp_link_issued`. Always `snake_case`, always a constant string
(never an f-string).

---

## W

**Worker.**
The process running on NL-2 that consumes arq jobs and executes
them. Container `worker` in `deploy/nl2/docker-compose.yml`.

---

## X

**`X-Accel-Redirect`.**
Nginx feature: the application returns this header pointing at an
internal location, and Nginx serves the file directly without going
back through the app worker. We use it for all public file
delivery.

---

## Y

**yt-dlp.**
The download engine. Called through `YtDlpRunner` which wraps
blocking work in `asyncio.to_thread`. Treated as a versioned
dependency (bumped frequently to keep up with platform changes).

**`YtDlpRunner`.**
`app/infrastructure/downloader/ytdlp_runner.py` — the async wrapper
around yt-dlp.

---

## Z

**ZIP.**
The packaging format we use when an Instagram gallery has multiple
small files that we want to deliver as one Telegram document. The
ZIP path is decided by the delivery service, not the provider.

---

## Mandatory principles (P1–P11) — quick reference

These eleven principles are referenced throughout the agent-facing docs
([`25-agent-guide.md`](25-agent-guide.md),
[`26-cursor-rules.md`](26-cursor-rules.md),
[`28-implementation-playbook.md`](28-implementation-playbook.md),
[`29-feature-development-guide.md`](29-feature-development-guide.md),
[`30-add-new-provider-guide.md`](30-add-new-provider-guide.md)). The
canonical, fully-explained register lives in
[`25-agent-guide.md`](25-agent-guide.md) §4 — this table is the
30-second lookup.

| Code | Name | One-line meaning |
|---|---|---|
| **P1** | Minimal safe scope | Smallest diff that fully solves the stated goal; no extras. |
| **P2** | Architecture first | Decide layer + ADR-0005 impact **before** writing code. |
| **P3** | No unrelated changes | One PR = one concern; drive-by edits forbidden. |
| **P4** | Docs / tests / config travel together | Behaviour change ships with its docs, tests, config in the same PR. |
| **P5** | No infrastructure leakage into domain | `app/domain/` and `app/application/` never import drivers, frameworks, or env. |
| **P6** | No business logic in handlers | Bot/API/worker handlers route — they do not orchestrate, query, or compute. |
| **P7** | Queue semantics preserved | `_job_id` determinism, bounded retries, idempotent side-effects, stateless workers. |
| **P8** | Provider contracts explicit | Every change goes through the `BaseProvider` surface; no platform-specific leaks outside the provider. |
| **P9** | Deploy idempotency reasoned | Every `deploy/scripts/*.sh` and compose change is justified for re-run safety. |
| **P10** | DB migration reasoned | No schema change without an Alembic migration **and** a written rollback / deploy-order story. |
| **P11** | Security defaults never weakened | TLS, HSTS, firewall, `internal` Nginx, `ensure_within`, `sanitize_filename`, `Settings`-only secrets are floors, not suggestions. |

> **How to use them.** Each PR / change description must, at minimum,
> name the principles it touches and how it honours them. When a
> principle would be violated, the change requires an ADR per
> [`adr/0005`](adr/0005-locked-architectural-assumptions.md) before it
> is made.

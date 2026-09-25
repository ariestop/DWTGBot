# ADR-0010: Instant-download UX and progress side-channel

> Status: Proposed (2026-04-20)
> Audience: contributors, AI agents, reviewers, on-call
> Tags: bot-ux, queue, redis, worker, breaking-change
> Relates to: ADR-0001 (two-server topology), ADR-0005 (locked assumptions),
> ADR-0002 (python-telegram-bot), `docs/02-architecture.md`,
> `docs/06-bot-flow.md`, `docs/09-queue-and-workers.md`

> **Дополнено ADR-0011:** появилась топология `single` (всё на одном
> хосте, один стек `deploy/single`). Порядок деплоя «NL-1, затем NL-2»
> из §3.3 относится к `split`. В `single` обе плоскости поднимаются одним
> `docker compose up -d`, а `depends_on: migrate`
> (`condition: service_completed_successfully`) в
> `deploy/single/single.override.yml` гарантирует, что api, worker и
> cleanup стартуют только после миграций. Строгого порядка bot → worker
> это не задаёт; риск из §3.2 (лишние ключи прогресса в Redis до
> старта нового bot) остаётся безвредным и кратковременным.

---

## 1. Context

The current bot flow requires two user actions per download:

1. User sends a link → bot posts `«Выберите вариант»` + inline keyboard
   with 4–5 quality buttons (`handle_link`, `app/bot/handlers/links.py`).
2. User taps a button → bot enqueues an arq job and replies
   `«Скачиваю … id задачи: N»` (`app/bot/callbacks/download.py`).

Empirically the vast majority of users tap the top quality bucket and
the picker is friction, not choice. The `id задачи: N` line serves
operations (grep by id in logs) but is noise to the user.

During the wait between enqueue and `sendVideo`, the chat is silent —
for a 4-minute 1080p YouTube video on NL-2 that can be 10–30 seconds
of apparent inactivity, during which the user pings `/start` or sends
the link again, multiplying load.

Three product asks followed from this:

- Skip the picker — download best quality automatically.
- Show a live preview + progress bar while the worker works.
- Expose the source post's text on demand (YouTube/Instagram) via an
  inline button, rather than always dumping it in the caption.

Non-goals of this ADR:

- Per-user default quality / audio-only preferences. Those stay on the
  roadmap; no new tables in this change.
- WebSocket progress channel for the public API. Out of scope.
- A second bot framework alongside PTB. Locked by ADR-0002.
- Persisting post text in Postgres. Ephemeral UX state only.

---

## 2. Decision

We adopt a three-part decision; all three ship together behind one
feature flag.

### 2.1 Replace the quality picker with auto-enqueue

On any incoming link (`handle_link`) the bot calls a new application
use case `AutoEnqueueDownloadUseCase` that:

1. Normalises URL, detects platform (unchanged).
2. Calls `provider.get_info(url)`.
3. Calls `provider.default_option(info)` — a new method on
   `BaseProvider`, abstract, implemented per provider. There is no
   fallback to a generic default; providers that cannot pick one
   raise `DownloadError`.
4. Creates the `DownloadJob` row, enqueues the arq task, and records
   `progress_meta:{job_id} = {chat_id, message_id, started_at}` in
   Redis.

The old inline keyboard and its callback (`dl|<request_id>|<option_key>`)
are removed. A new callback `cancel_job|<job_id>` takes over the cancel
button, moved to the progress message.

### 2.2 Progress as a side-channel via Redis

Progress does **not** travel through the arq queue. A new application
port `ProgressReporter` is defined in
`app/application/ports/progress_reporter.py`:

```
class ProgressReporter(Protocol):
    async def start(job_id, chat_id, message_id, thumbnail_url) -> None
    async def update(job_id, percent: float, stage: ProgressStage) -> None
    async def finish(job_id) -> None
    async def fail(job_id, reason: str) -> None
```

`ProgressStage` is a new domain enum (`app/domain/enums.py`):
`ANALYZING`, `DOWNLOADING`, `PROCESSING`, `UPLOADING`, `DONE`,
`CANCELLED`, `FAILED`.

Two implementations live in `app/infrastructure/cache/`:

- `RedisProgressReporter` — writes `progress:{job_id}` as a Redis hash
  with TTL 10 min, publishes a notification on channel
  `progress:events` (payload: job_id + stage). Debounced at the
  writer (max 1 write/sec/job-id).
- `NoopProgressReporter` — for tests and for `INSTANT_DOWNLOAD_ENABLED=false`.

Wiring is done exclusively in `app/composition.py` (both `build_bot`
and `build_worker` receive the same concrete `RedisProgressReporter`;
use cases depend only on the port).

Worker responsibility: `ProcessDownloadUseCase` calls `reporter.update`
at fixed phase boundaries:

- `DOWNLOADING` 0 → 70 % — driven by yt-dlp `progress_hooks`
  (hook runs in a yt-dlp worker thread, uses a sync Redis client).
- `PROCESSING` 70 → 95 % — driven by `mobile_compat.ensure_mobile_compatible`
  (start / end, plus wall-clock extrapolation between).
- `UPLOADING` 95 → 100 % — driven around `DeliveryService.deliver`.

Bot responsibility: a new service
`app/bot/services/progress_updater.py` runs as a long-lived asyncio
task created by `BotComposition` in `build_bot` and `aclose`d on
shutdown. It subscribes to `progress:events`, looks up
`progress:{job_id}` + `progress_meta:{job_id}`, and calls
`bot.edit_message_caption`. Behaviour:

> **Дополнено (2026-09-25):** имена ключей и канала вынесены в
> `app/application/ports/progress_channel.py` (общий контракт writer ↔
> reader), текст подписи — в `app/bot/services/progress_caption.py`.

- Debounce: skip updates when `|Δpercent| < PROGRESS_DEBOUNCE_PERCENT`
  and the stage has not changed, unless the last successful edit is
  older than `PROGRESS_REDRAW_INTERVAL_SEC`.
- On Telegram `RetryAfter`: sleep for the requested delay, then
  publish only the **latest** state (no queued stale updates).
- On Telegram `MessageNotModified`: swallow.
- On bot startup: scan `progress_meta:*`, rehydrate `active_jobs`,
  continue editing.
- Watchdog: if a job has no update for >30 s, edit the message to
  «Связь с worker'ом потеряна, скачивание продолжается». If >5 min —
  delete the message.

The worker never calls Telegram for progress. The worker's own
`telegram.Bot` client keeps doing only what it did: the final
`send_video` / `send_document` / `send_message` with the public URL.

### 2.3 Post text via dedicated callback

`MediaInfo` gains a `description: str = ""` field (domain).
YouTube and Instagram providers fill it from the sanitised yt-dlp
info dict, truncated to `POST_TEXT_MAX_CHARS` UTF-8 chars (default
10 000). Providers that cannot produce a description return empty.

During `AutoEnqueueDownloadUseCase`, if the description is non-empty
(after `.strip()` and a minimum length of 10 chars), the bot copies
it into Redis under `post_text:{job_id}` with TTL
`POST_TEXT_TTL_SEC` (default 24 h). No Postgres column is added.

After successful `DeliveryService.deliver`, the final message is
decorated with an inline button «Получить текст поста 👇» whose
callback data is `post_text|{job_id}` (well under the 64-byte limit).
The handler reads `post_text:{job_id}`, HTML-escapes, and sends the
text as one or more messages of ≤ 4000 chars with
`disable_web_page_preview=True`. If the key is missing (TTL elapsed
or Redis eviction), `answer_callback_query` replies with an alert
«Текст поста больше недоступен».

### 2.4 Minor UX changes bundled with the above

- `DeliveryService._caption` appends `Settings.BRAND_FOOTER` (default:
  `«Спасибо за использование нашего бота @dwtgbot»`). Disabling is
  `BRAND_FOOTER=""`.
- The user-visible `«id задачи: N»` line is removed. Logs, metrics,
  and the `/d/{token}` URL path keep the job id; users don't see it.

### 2.5 Feature flag and rollback

A new setting `INSTANT_DOWNLOAD_ENABLED: bool = True` gates the new
flow. When `false`:

- `handle_link` falls back to the existing picker path (kept intact
  for at least one major release).
- `composition.py` injects `NoopProgressReporter` into the worker; no
  progress is written.
- The post-text button is not rendered.

This gives an instantaneous rollback (env toggle + `up -d bot`)
without pulling a previous image.

---

## 3. Consequences

### 3.1 Positive

- Zero user taps from link to video.
- Progress bar makes the 10–30 s wait observable; on-call gets fewer
  `«почему так долго?»` pings.
- Post text on demand keeps captions tight while preserving context.
- Introducing `ProgressReporter` as a port is an extension point:
  future providers / alternative transports (WebSocket for an API
  client) plug in without touching application code.
- No schema change → no downtime migration; rollback path is one env
  variable.

### 3.2 Negative / accepted trade-offs

- **Loss of explicit quality choice.** Users who specifically wanted
  480p to save bandwidth lose that control until `/quality` /
  user-settings land. Mitigation: provider selector already prefers
  H.264 MP4 (see fixes `287b914`, `988e9ea`), so defaults are sane
  across YouTube 1080p and IG reels.
- **Loss of MP3-only audio path.** No auto-flow equivalent. Users
  who used audio must wait for a dedicated `/audio <url>` command
  (explicit follow-up).
- **New Redis load:** progress hash + pubsub + `progress_meta:*` +
  `post_text:*`. Worst case at 100 concurrent jobs with 1 write/sec
  = 100 ops/sec — well within the current Redis budget, but it **is**
  new traffic to the control-plane Redis on NL-1 from workers on NL-2.
- **New coupling surface:** worker depends on Redis reachability to
  publish progress. Best-effort semantics are required — progress
  publication must never fail a download. Wrapped in a try/except
  that logs and continues.
- **New lifecycle risk in the bot:** a zombie `progress_updater`
  task (hung on Redis, silently caught exception) would stop edits
  without the bot crashing. Covered by a `/readyz` check that the
  task's last iteration timestamp is within 10 s.
- **Ordering of deploys changes:** new worker code publishing
  progress ahead of a new bot that can consume it wastes Redis keys.
  Deploy order becomes **bot → worker** (previously unconstrained).

### 3.3 Operational impact

- New env vars (NL-1 bot + NL-2 worker, plus deploy templates):
  `INSTANT_DOWNLOAD_ENABLED`, `BRAND_FOOTER`,
  `PROGRESS_REDRAW_INTERVAL_SEC`, `PROGRESS_TTL_SEC`,
  `PROGRESS_DEBOUNCE_PERCENT`, `POST_TEXT_TTL_SEC`,
  `POST_TEXT_MAX_CHARS`.
- Deploy order (runbook change): pull + `up -d` on NL-1 **first**,
  then on NL-2. Documented in `docs/24-runbooks.md`. (В `single` —
  см. дополнение в начале ADR.)
- New metrics (must be in `/metrics`):
  `progress_events_published_total{stage}`,
  `progress_updates_applied_total{result}`,
  `progress_update_latency_seconds`,
  `download_job_age_seconds`,
  `post_text_callbacks_total{result}`.
- No Alembic migration. No nginx / certbot changes.
- Rollback: set `INSTANT_DOWNLOAD_ENABLED=false` on NL-1 and
  `compose_nl1 up -d bot`. Worker can stay on the new image; its
  progress writes land in Redis and simply have no consumer.

---

## 4. Alternatives considered

### 4.1 Worker edits Telegram messages directly (no Redis side-channel)

The worker already holds a `telegram.Bot` client for uploads, so it
could `edit_message_caption` from NL-2 itself. **Rejected** because it
splits Telegram-UI ownership between two services (bot edits during
analyze, worker edits during download, bot edits again on post-text
callback), doubles Telegram rate-limit tracking, and contradicts the
existing inversion where the worker's Telegram client is an exception
reserved for heavy upload bytes (`docs/02-architecture.md` §3, §4).

### 4.2 HTTP callback worker → bot

Open an NL-2 → NL-1 HTTP path for the worker to POST progress to the
bot. **Rejected:** there is no such channel today; opening it would
require new firewall rules on NL-1 and a new internal endpoint in
the bot, violating ADR-0001's "minimal firewall surface" and ADR-0005
§3.2. Redis is already co-located on NL-1 and reachable from NL-2
over WireGuard — cheaper, tested.

### 4.3 Progress via arq result poll

arq supports `Job.status()` / `Job.result_info()` from the enqueueing
side. **Rejected:** it reports *task* state (queued/in-progress/done),
not download progress inside the task. Abusing `ctx["job_try"]` or
similar is a non-standard leak of infra-level metadata.

### 4.4 Embed progress in captions of the final video

Send a placeholder message at enqueue, then `edit_message_media` to
the finished file. **Partially adopted.** Used as the *final* step
when a thumbnail preview exists (avoids a send+delete churn). The
in-flight edits of caption still happen on the placeholder message.

### 4.5 Keep the quality picker with a "Quick" option

Add a single «Быстро (1080p)» button alongside the existing picker,
defaulting users who tap it to best quality. **Rejected:** still a
tap, does not address the ask («сразу начать скачивание»).

### 4.6 Persist post text in Postgres

Add `description` column to `download_jobs`. **Rejected:** it is not
audit or billing data; 24 h TTL is plenty for the "text button" UX.
Alembic change + downtime not justified. Redis with documented TTL
and a graceful "no longer available" alert is adequate.

---

## 5. Compliance

- **Code boundary:**
  `app/application/` must not import
  `app/infrastructure/cache/redis_progress_reporter.py`. Enforced by
  the dependency matrix in `docs/03-project-structure.md` §7 and
  existing mypy boundaries.
- **Composition:** `RedisProgressReporter` and its Noop variant are
  constructed only in `app/composition.py`. Reviewers reject any PR
  that instantiates them elsewhere (§9 anti-pattern 2).
- **Tests:**
  - `app/tests/unit/test_progress_reporter_protocol.py` — contract
    tests with FakeRedis.
  - `app/tests/unit/test_progress_updater.py` — debounce,
    rate-limit, watchdog.
  - `app/tests/unit/test_post_text_handler.py` — TTL expiry path.
  - `app/tests/integration/test_end_to_end_auto_download.py` —
    progress 0 → 100 and final delivery.
- **CI:** existing `ruff check`, `ruff format --check`, `mypy app/`.
  No new tooling.
- **Docs:** update
  - `docs/02-architecture.md` §4 (sequence diagram)
  - `docs/06-bot-flow.md` (new flow)
  - `docs/09-queue-and-workers.md` (progress side-channel)
  - `docs/13-config-and-env.md` (new env vars)
  - `docs/35-metrics-and-slo.md` (new metrics + SLO entries)
  - `docs/24-runbooks.md` (deploy order)
  - `docs/32-roadmap-and-extension-points.md` (remove or flip items
    that this decision addresses)
- **Deprecation clock:** the old picker path remains guarded by
  `INSTANT_DOWNLOAD_ENABLED` for **one** major release after this ADR
  is Accepted. Removal of that code is tracked as a follow-up task,
  not done in the same PR set.

---

## 6. References

- Code anchors:
  - `app/bot/handlers/links.py` (current `handle_link`)
  - `app/bot/callbacks/download.py` (picker callback — removed)
  - `app/application/use_cases/analyze_link.py`,
    `app/application/use_cases/enqueue_download.py`,
    `app/application/use_cases/process_download.py`
  - `app/application/services/delivery_service.py`
  - `app/domain/entities/media_info.py`,
    `app/domain/enums.py`
  - `app/infrastructure/providers/youtube.py`,
    `app/infrastructure/providers/instagram.py`,
    `app/infrastructure/providers/base.py`
  - `app/infrastructure/downloader/ytdlp_runner.py`
    (`download`, `progress_hooks` hook point is new; the mobile-compat
    step now lives in `mobile_compat.py`)
  - `app/infrastructure/cache/` (new reporter)
  - `app/bot/services/` (new progress updater)
  - `app/composition.py`
- Docs: `docs/tasks/archive/instant-download-ux.md` (this ADR's
  implementation spec), `docs/02-architecture.md`,
  `docs/06-bot-flow.md`, `docs/09-queue-and-workers.md`.
- Prior fixes relevant to behaviour: mobile-Telegram transcode
  (`287b914` — vp9/hevc transcode, `988e9ea` — tmp filename →
  MP4 muxer selection).

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2026-04-20 | Proposed | Drafted alongside `docs/tasks/instant-download-ux.md`. |

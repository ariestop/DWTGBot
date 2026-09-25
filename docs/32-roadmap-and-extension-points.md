# 32 — Roadmap & extension points

> Status: Stable
> Audience: tech leads, contributors, AI agents proposing features
> Read first: [`02-architecture.md`](02-architecture.md), [`28-implementation-playbook.md`](28-implementation-playbook.md)

This file lists known **extension points** (places designed to be
extended without changing the core) and the **near-term roadmap**.
It is not a project plan; it's a navigation aid for "what could come
next, and where would it go."

> 🔒 Locked architectural decisions (see [`28-implementation-playbook.md`](28-implementation-playbook.md)
> §5) are out of scope here. Anything in this file that would change
> them requires an ADR.

---

## 1. Extension points (by subsystem)

### 1.1 Providers / platforms

**Extends:** download surface (TikTok, X, Reddit, Vimeo, SoundCloud, …).

- Surface: `app/infrastructure/providers/<platform>.py` extending
  `BaseProvider`.
- Wired in: `app/composition.py::_build_provider_registry`.
- Recipe: [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md).
- Cost: **low** (1–2 days for a yt-dlp-supported platform).
- Risk: **low** (registry is isolated; failures don't affect existing
  providers).

### 1.2 Download options

**Extends:** what the user can pick (e.g. video 1440p, audio FLAC,
"GIF", subtitles).

- Surface: `DownloadOption` in `app/domain/entities/media_info.py`,
  consumed by providers' `build_options`/`download` and by the
  delivery service.
- Cost: **low–medium** depending on whether the new kind needs a new
  delivery path.
- Risk: **low** — additive.

### 1.3 Delivery channels

**Extends:** how a finished file reaches the user.

Today: Telegram upload OR temporary HTTPS link.
Possible future:
- pre-signed S3 / Backblaze B2 URLs (offload disk + bandwidth from
  NL-2).
- Telegram Premium 2 GB upload limit toggle (we currently assume
  bot-API 50 MB).
- Email a link.

- Surface: `app/application/services/delivery_service.py`
  (decision tree) + `app/infrastructure/storage/*` for new backends.
- Cost: **medium**.
- Risk: **medium** — touches retention semantics (a new backend has
  its own TTL).

### 1.4 Storage backends

**Extends:** local FS → S3 / B2 / GCS.

- Surface: protocol behind `LocalStorage`. Today only local; needs an
  ABC (`StorageBackend`) extracted before adding the second
  implementation.
- Cost: **medium** (refactor + integration tests).
- Risk: **medium-high** — touches cleanup, healthchecks, Nginx (we'd
  no longer use `X-Accel-Redirect`).

### 1.5 Background tasks (arq)

**Extends:** new asynchronous jobs (e.g. "regenerate thumbnail",
"transcribe audio").

- Surface: `app/workers/tasks/<task>.py` + producer methods on
  `app/application/services/queue.py`.
- Cost: **low**.
- Risk: **low** if isolated from the main download pipeline.

### 1.6 Scheduled jobs

**Extends:** new cron-like work (re-index media cache, prune audit
logs, send daily stats to admins).

- Surface: arq `cron_jobs` in worker settings.
- Cost: **low**.
- Risk: **low** unless it touches user data (then it's a retention
  policy change → ADR).

### 1.7 Bot UX

**Extends:** new commands, inline keyboards, chat states.

- Surface: `app/bot/handlers/`, `app/bot/keyboards/`, `app/bot/callbacks/`.
- Recipes: [`29-feature-development-guide.md`](29-feature-development-guide.md) §2–§3.
- Cost: **low** (per command).
- Risk: **low** — UX is well isolated.

### 1.8 Auth / user model

**Extends:** beyond "anyone with the bot link can use it".

Possible future:
- per-user quotas (downloads/day, GB/day);
- allow-list (Telegram user_id);
- premium tier (longer TTLs, larger files).

- Surface: a future `UserService` between bot handlers and use cases.
  Today there's no such layer because there is no user model.
- Cost: **medium-high** — needs DB tables, settings, admin tooling.
- Risk: **medium**. **Requires ADR** (changes a fundamental product
  assumption).

### 1.9 Observability

**Extends:** logs → metrics → tracing.

- Logs: structured JSON exists; ship to Loki/Datadog with a Promtail
  side-car.
- Metrics: **rate-limit, job, queue, and worker metrics all implemented**
  as in-process Prometheus exporters
  ([`36-rate-limiting.md`](36-rate-limiting.md) §8,
  [`35-metrics-and-slo.md`](35-metrics-and-slo.md) §7,
  [ADR-0006](adr/0006-in-process-rate-limit-metrics-exporter.md),
  [ADR-0007](adr/0007-job-queue-worker-metrics.md); all gated by
  `METRICS_ENABLED=false`). Each of the three processes (bot, worker,
  API) exposes its own `/metrics` — see ADR-0007 §2.5. Extending is
  straightforward: register the new collector against the per-process
  `CollectorRegistry` in `app/composition.py:_build_metrics`.
- Tracing: not implemented; OpenTelemetry would be the candidate. The
  Prometheus exporter from ADR-0006 is additive and does not block OTLP
  later (see ADR-0006 §4.3).

- Cost: **medium** (metrics) / **high** (full tracing).
- Risk: **low** if additive.

### 1.10 Healthchecks

**Extends:** new readiness probes.

- Surface: `app/api/internal/health.py`.
- Recipe: [`15-healthchecks.md`](15-healthchecks.md).
- Cost: **low**.

### 1.11 CI / CD

**Extends:** new gates, new image targets, new deploy environments.

- Surface: `.github/workflows/*.yml`.
- Recipe: [`21-cicd.md`](21-cicd.md).

### 1.12 Deployment topology

**Extends:** more than two servers, region failover, k8s.

- Surface: `deploy/`. Сейчас зафиксированы две Compose-топологии —
  `single` (один хост) и `split` (NL-1 + NL-2), см.
  [ADR-0011](adr/0011-single-server-topology.md); всё сверх этого
  (больше двух серверов, multi-region, k8s) требует нового ADR.
- Cost: **high** (multi-region adds split-brain concerns; k8s
  rewrites the operational story).
- Risk: **high**. **Requires ADR.**

### 1.13 Data lifecycle / retention

**Extends:** TTLs, max sizes, audit log retention.

- Surface: `app/config.py` + `app/workers/cleanup_worker.py` +
  [`23-cleanup-retention.md`](23-cleanup-retention.md).
- Risk: **medium** — directly affects user-visible behaviour and
  legal exposure.

---

## 2. Roadmap (illustrative — keep updated by maintainers)

This is **not a contract**; it's a snapshot of likely directions.

### 2.1 Near-term (next 1–3 months)

- Bug fixes and yt-dlp compatibility bumps.
- Minor UX polish (better error messages).
- Tighten mypy in `app/domain/` and `app/application/`.
- ✅ Prometheus `/metrics` endpoint behind `METRICS_ENABLED` flag — done
  for rate-limit signals (ADR-0006) and for job/queue/worker signals
  across all three processes (ADR-0007).
- Wire dashboards & burn-rate alert rules from
  [`35-metrics-and-slo.md`](35-metrics-and-slo.md) §6 / §8 against the
  freshly live metric inventory.
- ✅ Однохостовая топология `single` рядом со `split`
  ([ADR-0011](adr/0011-single-server-topology.md)). Переход на `split`
  выполняется по порогам из [`37-load-and-capacity.md`](37-load-and-capacity.md) §8.0.

### 2.2 Mid-term (3–9 months)

- Add 1–2 new providers (TikTok and one of: X, Reddit, Vimeo).
- Per-user quotas (requires user model — see §1.8; needs ADR).
- Move backups to off-host storage by default (S3-compatible).
- Improve the temp-link UX (preview thumbnail on the link page).

### 2.3 Long-term (9+ months)

- Optional storage backend abstraction (S3/B2/GCS).
- Optional Telegram Premium 2 GB upload tier.
- Optional admin dashboard (read-only metrics & job inspector).

> All long-term items will need ADRs. Don't start them without one.

---

## 3. Things we will *not* do (at least not without ADR)

Explicit non-goals — agents and contributors should push back if a
proposal goes here:

1. **Multi-tenant SaaS** — we are a single-deployment bot. Going
   multi-tenant is a different product.
2. **Full transcoding service** — we extract or downscale through
   yt-dlp/ffmpeg. We don't aspire to be a generic transcoder.
3. **Hosting copyrighted libraries of media** — we are a download
   utility. The temp-link mechanism is short-lived by design.
4. **Synchronous API for third parties** — temp links are the only
   public surface. No "submit URL by HTTP" endpoint.
5. **k8s migration** — Compose meets the requirement. k8s adds ops
   complexity for no current win.
6. **Move to a different bot framework** — `python-telegram-bot` is
   locked. Other libraries (`aiogram`, raw Bot API) are not on the
   roadmap.
7. **Drop JSON logs** — the ops story depends on it.
8. **Третья топология или стек в обход фрагментов** `deploy/compose/*`
   (например, «single, но без nginx»). Любой новый стек собирается из
   тех же фрагментов и требует ADR, который дополняет ADR-0011.

---

## 4. Adding to the roadmap

Process:

1. Open an issue describing the use case (user-visible outcome,
   not the implementation).
2. Reference the relevant extension point from §1.
3. If it touches a locked decision, draft an ADR in `docs/adr/`.
4. Update §2 of this file when accepted.

If you're an AI agent: do **not** add to the roadmap unless the user
explicitly asks. The roadmap is a human commitment.

---

## 5. Anti-patterns when extending

1. **Adding a feature without an extension point** — i.e. surgery
   into the core. Either extract an extension point first, or open
   an ADR.
2. **Extending the "wrong layer"** — putting download logic into the
   bot, putting bot logic into the worker. See
   [`02-architecture.md`](02-architecture.md).
3. **Adding a "small" config flag that turns out to be a feature
   flag** — feature flags need lifecycle (default, ramp,
   removal). Treat them as features.
4. **Backwards-incompatible change to callback data / temp link
   token format** — old in-flight clicks/links will break silently.
   Use a versioned discriminator (`v2:` prefix).
5. **Adding a dependency for one feature** — assess the maintenance
   surface; sometimes 30 lines of code beats a 30 KB lib.

---

## 6. Common mistakes

| Mistake | Why it hurts | Better |
|---|---|---|
| New feature with no extension-point doc | next agent re-discovers from scratch | add a row to §1 |
| Roadmap update without an issue | invisible to humans | open the issue first |
| Building §3 ("won't do") items "to see if it works" | wastes effort, locks in the wrong direction | discuss first |
| Extending storage to S3 by branching `if backend == "s3"` everywhere | leaks backend everywhere | extract a `StorageBackend` ABC first |

# Tasks — порядок реализации

Единый master-индекс для всех активных engineering-задач. **Фазы выполняются строго по порядку**: сначала фичи (ценность для пользователя), потом аудит-фиксы (техдолг).

Правило: очередная фаза **не стартует**, пока предыдущая не доведена до "released to production + stable ≥ 24 ч".

---

## Phase 1 — Фичи (released)

**Файл:** [`instant-download-ux.md`](instant-download-ux.md)
**ADR:** [`docs/adr/0010-instant-download-ux.md`](../adr/0010-instant-download-ux.md)
**Статус:** все 6 PR'ов замёржены в `main` и в проде. `INSTANT_DOWNLOAD_ENABLED=true` глобально, с per-platform opt-out для YouTube (picker остаётся, см. `c626019`). Post-release stabilisation: `96a9e8d` (IG wording), `6bd7bae` (live progress на picker-ветке YT), `958b028` (drop queue id из caption), `58e9e09` / `f703b05` (YT size cascade).
**Бизнес-цель:** снизить число тапов до медиа с 2 до 0, сделать ожидание осязаемым.

| PR | Скоуп | Оценка | Статус |
|---|---|---|---|
| PR 1 | `ProgressStage` enum, `MediaInfo.description`, `ProgressReporter` protocol, `NoopProgressReporter`, wiring в composition | 0.5 д | ✅ done `d697f66` |
| PR 2 | `BaseProvider.default_option()` abstract + YT/IG + `description` в `MediaInfo` | 0.5 д | ✅ done `3af187f` |
| PR 3 | `RedisProgressReporter`, yt-dlp `progress_hooks`, публикация фаз в use-case | 1 д | ✅ done `74068b1` |
| PR 4 | `app/bot/services/progress_updater.py`, lifecycle, recovery через `SCAN`, debounce / rate-limit / watchdog | 1 д | ✅ done `5914b2b` |
| PR 5 | `AutoEnqueueDownloadUseCase`, флаг `INSTANT_DOWNLOAD_ENABLED`, cancel callback, `BRAND_FOOTER`, убрать `id задачи` | 1 д | ✅ done `2ba18a4` |
| PR 6 | `post_text|<job_id>` callback + handler, HTML-escape, chunking | 0.5 д | ✅ done `4433cdb` |

**Итого Phase 1:** ~5.5 д + 0.5 д docs + 0.5 д canary = **~6.5 дней**.

**Gate на Phase 2:** ✅ закрыт. PR 6 замёржен, `INSTANT_DOWNLOAD_ENABLED=true` живёт в проде, метрики `progress_*` зелёные.

---

## Phase 2 — Аудит-фиксы (released)

**Файл:** [`audit-fixes-2026-04.md`](audit-fixes-2026-04.md)
**Источник:** аудит от 2026-04-20 (Staff Architect + Sr. Backend + DevOps + SRE + Security + QA).
**Статус:** ✅ закрыт. Sprint 2.1 (`1a4e7cd` / `608d548` / `ccbef49` — A1–A15), Sprint 2.2–2.4 (`0b2dc1a` / `e23e0f9` — A16–A29) замёржены и развёрнуты на NL-1/NL-2. Post-rollout hotfix: **A30 healthchecks** (worker/cleanup `pgrep`-free probes + `/readyz` token в `healthcheck.sh`) — PR #3, merged as `769ee05`, выкачен.
**Бизнес-цель:** закрыть TOP-10 критических проблем, без которых публичный запуск небезопасен.

| Sprint | Скоуп | Оценка |
|---|---|---|
| 2.1 — Quick wins | 15 механических фиксов (TOP-10 + выборка) без ADR | ~1.5 д |
| 2.2 — Security hardening | `API_INTERNAL_TOKEN` middleware, SSRF-ограничение в yt-dlp, HTML-escape | ~3 д |
| 2.3 — Reliability | Replay-guard на `ProcessDownloadUseCase`, pre-download HEAD-check, orphan-dir sweep, optimistic lock | ~3 д |
| 2.4 — Observability & tests | Integration CI job, E2E fixture, Trivy/Gitleaks, `--cov-fail-under` | ~3 д |

**Итого Phase 2:** **~10 рабочих дней** одного разработчика.

**Gate на Phase 3:** ✅ TOP-10 закрыт, security-тесты зелёные, стек на NL-1/NL-2 healthy после деплоя `769ee05` + `c626019`.

---

## Phase 3 — Mid-term (1–2 месяца, не в этой сессии)

Перечисляется здесь только для roadmap-контекста. Каждый пункт требует отдельного ADR перед стартом.

- Ports/Services унификация (`StoragePort`, `TelegramSenderPort`, миграция `process_download` и `delivery_service`).
- yt-dlp полная ENV-конфигурация (вынести магические тайм-ауты).
- Graceful degradation для Redis/Postgres down (user-facing "сервис в пониженном режиме").
- Canary-деплой + auto-rollback по `/readyz`.
- Prometheus alerting rules в репозиторий.
- Per-user daily disk quota.

---

## Правила работы с этим индексом

1. **Один PR → один файл → одна строка в таблице** выше. После merge строка помечается `✅` + hash коммита.
2. **Phase 2 не стартует параллельно с Phase 1** даже при наличии свободного разработчика. Причина: аудит-фиксы трогают composition и infrastructure — merge-конфликты с PR 3/4/5 гарантированы.
3. **Любой P0-блокер из prod** (падение сервиса, CVE, утечка) — вне очереди. Создать отдельный hotfix-файл в `docs/tasks/hotfix-<дата>-<slug>.md`, пометить в этом README.
4. **Новые задачи** добавляются в Phase 3 и дозревают до ADR перед повышением в Phase 2 следующего цикла.

# Tasks — порядок реализации

Единый master-индекс для всех активных engineering-задач. **Фазы выполняются строго по порядку**: сначала фичи (ценность для пользователя), потом аудит-фиксы (техдолг).

Правило: очередная фаза **не стартует**, пока предыдущая не доведена до "released to production + stable ≥ 24 ч".

---

## Phase 1 — Фичи (активный sprint)

**Файл:** [`instant-download-ux.md`](instant-download-ux.md)
**ADR:** [`docs/adr/0010-instant-download-ux.md`](../adr/0010-instant-download-ux.md)
**Статус:** PR 1 реализован локально (не закоммичен), PR 2–6 в очереди.
**Бизнес-цель:** снизить число тапов до медиа с 2 до 0, сделать ожидание осязаемым.

| PR | Скоуп | Оценка | Статус |
|---|---|---|---|
| PR 1 | `ProgressStage` enum, `MediaInfo.description`, `ProgressReporter` protocol, `NoopProgressReporter`, wiring в composition | 0.5 д | ✅ done (локально) |
| PR 2 | `BaseProvider.default_option()` abstract + YT/IG + `description` в `MediaInfo` | 0.5 д | ⏳ pending |
| PR 3 | `RedisProgressReporter`, yt-dlp `progress_hooks`, публикация фаз в use-case | 1 д | ⏳ pending |
| PR 4 | `app/bot/services/progress_updater.py`, lifecycle, recovery через `SCAN`, debounce / rate-limit / watchdog | 1 д | ⏳ pending |
| PR 5 | `AutoEnqueueDownloadUseCase`, флаг `INSTANT_DOWNLOAD_ENABLED`, cancel callback, `BRAND_FOOTER`, убрать `id задачи` | 1 д | ⏳ pending |
| PR 6 | `post_text|<job_id>` callback + handler, HTML-escape, chunking | 0.5 д | ⏳ pending |

**Итого Phase 1:** ~5.5 д + 0.5 д docs + 0.5 д canary = **~6.5 дней**.

**Gate на Phase 2:** PR 6 замёржен, `INSTANT_DOWNLOAD_ENABLED=true` на NL-2 прошёл 24 ч без регрессий, метрики `progress_*` зелёные.

---

## Phase 2 — Аудит-фиксы (следующий sprint)

**Файл:** [`audit-fixes-2026-04.md`](audit-fixes-2026-04.md)
**Источник:** аудит от 2026-04-20 (Staff Architect + Sr. Backend + DevOps + SRE + Security + QA).
**Статус:** A16–A29 замёржены в `main` и развёрнуты на NL-1/NL-2. Post-rollout hotfix: **A30 healthchecks** (worker/cleanup `pgrep`-free probes + `/readyz` token в `healthcheck.sh`) — сливается отдельным PR без rebuild'а требований.
**Бизнес-цель:** закрыть TOP-10 критических проблем, без которых публичный запуск небезопасен.

| Sprint | Скоуп | Оценка |
|---|---|---|
| 2.1 — Quick wins | 15 механических фиксов (TOP-10 + выборка) без ADR | ~1.5 д |
| 2.2 — Security hardening | `API_INTERNAL_TOKEN` middleware, SSRF-ограничение в yt-dlp, HTML-escape | ~3 д |
| 2.3 — Reliability | Replay-guard на `ProcessDownloadUseCase`, pre-download HEAD-check, orphan-dir sweep, optimistic lock | ~3 д |
| 2.4 — Observability & tests | Integration CI job, E2E fixture, Trivy/Gitleaks, `--cov-fail-under` | ~3 д |

**Итого Phase 2:** **~10 рабочих дней** одного разработчика.

**Gate на Phase 3:** TOP-10 закрыт + все новые security-тесты зелёные.

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

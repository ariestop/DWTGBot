# TASK: Закрытие TOP-10 проблем аудита 2026-04

> **Архив.** Задача выполнена и выкачена в прод; документ сохранён как
> историческая спецификация. Чекбоксы ниже не обновлялись после релиза —
> актуальный статус см. в [`docs/tasks/README.md`](../README.md).

Status: **RELEASED** — Sprint 2.1–2.4 и A30 замёржены и выкачены (см. `docs/tasks/README.md`).
Source: аудит от 2026-04-20, проведён как команда Staff Architect + Sr. Backend + DevOps + SRE + Security + QA.
Prerequisite: `docs/tasks/archive/instant-download-ux.md` (Phase 1) должен быть мёржнут и стабилен ≥ 24 ч.
Related: [`docs/tasks/README.md`](../README.md) — master ordering.

---

## 0. Общая стратегия

План разбит на **четыре независимых sprint'а** (2.1–2.4). Каждый sprint = один merge train в `main`, 1 rollout на NL-1/NL-2 в конце sprint'а. Внутри sprint'а PR'ы **можно мёржить параллельно** (зависимостей между ними нет, разные файлы).

| Sprint | Цель | PR count | Календарь |
|---|---|---|---|
| 2.1 Quick wins | 15 механических фиксов, 3 из TOP-10 | 15 | 1.5 рабочих дня |
| 2.2 Security hardening | Internal-API auth + SSRF + HTML-escape | 4 | 3 дня |
| 2.3 Reliability | Replay-guard + size-cap + orphan sweep + optimistic lock | 5 | 3 дня |
| 2.4 Observability & tests | Integration CI + E2E fixture + scanners + coverage gate | 5 | 3 дня |

**Итого: ≈ 10 рабочих дней**, 29 PR'ов. Все микро-коммиты; ни один PR не превышает 300 строк diff.

Метод: каждый PR — **одна проблема, одно исправление, свой тест**. Никаких "заодно поправил".

---

## 1. SPRINT 2.1 — Quick wins

Sprint #1: снять всё, что чинится ≤ 1 час и не требует архитектурного решения. 15 PR'ов.

### PR A1 — `cleanup.sh` импортирует несуществующий `build_api`

**Problem:** `deploy/scripts/cleanup.sh:35-44` вызывает `build_api` из `app.workers.cleanup_worker`, но функция переименована в `build_cleanup` в audit-фиксе L13. Ручной one-shot cleanup ломается с `ImportError`.

**Goal:** manual cleanup корректно дёргает DB+files sweep.

**Steps**

1. Открыть `deploy/scripts/cleanup.sh`.
2. В строках 34–44 заменить `build_api` → `build_cleanup`, убрать `SqlAlchemyMediaCacheRepository` (функция `_run_cycle(composition)` уже принимает composition целиком, см. `app/workers/cleanup_worker.py:29`).
3. Актуализировать docstring и inline-комментарии.
4. На NL-2 прогнать `TMP_MAX_AGE_HOURS=2 bash deploy/scripts/cleanup.sh` — должен завершиться с exit 0.

**DoD**

- [ ] `bash deploy/scripts/cleanup.sh --help` (если добавлен) или реальный прогон на NL-2 — без ImportError.
- [ ] Логи содержат `orphan_reaper_done` / `storage_tmp_cleanup_done`.

**Estimate:** 15 мин.

---

### PR A2 — Docker healthcheck: `api` → `/readyz`

**Problem:** `deploy/nl1/docker-compose.yml:118-120` и `deploy/nl2/docker-compose.yml:39` дёргают `/healthz` (liveness). При мёртвом Postgres/Redis контейнер всё равно "healthy" — `worker` стартует и сразу падает. False-green сигнал в depends_on-цепочке.

**Goal:** "healthy" означает "готов обрабатывать запросы".

**Steps**

1. В обоих compose-файлах заменить:
   ```yaml
   test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:${API_PORT:-8080}/healthz >/dev/null"]
   ```
   на:
   ```yaml
   test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:${API_PORT:-8080}/readyz >/dev/null"]
   ```
2. Увеличить `start_period: 30s` на `api`, чтобы миграции успевали пройти до первого пробоя.
3. Проверить, что `/readyz` уже корректно возвращает 503 при недоступной БД (см. `app/api/internal/health.py`). Если нет — отдельным PR (см. A3).
4. На dev-окружении: `docker compose up api`, остановить `postgres` — `docker ps` должен показать `(unhealthy)` через 30-45 сек.

**DoD**

- [ ] `docker compose ps api` показывает `(unhealthy)` при остановленной Postgres.
- [ ] `worker` с `depends_on: api: healthy` не стартует при лежащей БД.
- [ ] Тест reload: после восстановления Postgres api возвращается в healthy ≤ 60 сек.

**Estimate:** 30 мин.

---

### PR A3 — `deploy.yml target: nl2` молча skip'ается

**Problem:** `.github/workflows/deploy.yml:61-63` — job `nl2` имеет `needs: [nl1]` без `if: always()`. При `workflow_dispatch` с `target: nl2` → `nl1` skipped → `nl2` тоже skipped. Оператор думает "задеплоил", ничего не произошло.

**Goal:** `target: nl2` независим от `nl1`.

**Steps**

1. Открыть `.github/workflows/deploy.yml`.
2. На job `nl2` заменить условие `if:` на:
   ```yaml
   if: ${{ always() && (inputs.target == 'nl2' || inputs.target == 'both') && (needs.nl1.result == 'success' || needs.nl1.result == 'skipped') }}
   ```
3. Аналогично проверить job `nl1` (если она тоже имеет `needs`) — guard через `always()` + check на `success/skipped`.
4. Прогнать `act` (или `gh workflow run`) с тремя вариантами: `nl1`, `nl2`, `both`. Убедиться, что job'ы запускаются правильно.

**DoD**

- [ ] `gh workflow run deploy.yml --field target=nl2` реально триггерит NL-2 деплой.
- [ ] `gh workflow run deploy.yml --field target=both` запускает обе (nl1 → nl2).
- [ ] `target=nl1` не триггерит nl2.

**Estimate:** 15 мин + 15 мин тестов.

---

### PR A4 — Redis `--maxmemory` + `--maxmemory-policy`

**Problem:** `deploy/nl1/docker-compose.yml:47-59` — Redis без лимитов. Утечка / кэш-overflow съест всю RAM NL-1 → упадёт всё (bot + postgres + redis).

**Goal:** Redis имеет жёсткий memory cap с LRU-эвикцией.

**Steps**

1. Открыть `deploy/nl1/docker-compose.yml`, секция `redis:`.
2. Заменить `command: >` на:
   ```yaml
   command: >
     redis-server
     --save 60 1000
     --appendonly yes
     --maxmemory ${REDIS_MAXMEMORY:-1gb}
     --maxmemory-policy allkeys-lru
     ${REDIS_PASSWORD:+--requirepass $REDIS_PASSWORD}
   ```
3. В `deploy/nl1/.env.example` добавить:
   ```
   REDIS_MAXMEMORY=1gb
   ```
4. На NL-1: `docker compose up -d redis`; проверить `redis-cli CONFIG GET maxmemory` → `1073741824`, `maxmemory-policy` → `allkeys-lru`.

**Risks / Edge cases**

- `arq` job payloads и `progress:*` ключи живут в этом же Redis. При LRU-эвикции из-за overflow можем потерять активные задачи. Mitigation: `1gb` — с запасом на текущий профиль (≤ 10k ключей, ≈ 30 МБ). Мониторить `redis_memory_used_bytes`.
- `allkeys-lru` эвиктит даже без TTL. Альтернатива — `volatile-lru` (эвиктит только TTL-ключи). В нашей схеме TTL есть на всех application-ключах (progress, post_text, state, rate-limit), но arq-queue ключи — без TTL. Чтобы их не потерять при эвикции, **лучше `volatile-lru`**. Использовать его.

**DoD**

- [ ] `redis-cli CONFIG GET maxmemory` = 1073741824 на NL-1.
- [ ] `redis-cli CONFIG GET maxmemory-policy` = `volatile-lru`.
- [ ] При принудительной заливке мусором (`redis-cli DEBUG POPULATE 500000`) процесс не ООМ'ится, а эвиктит старые ключи.

**Estimate:** 15 мин + 10 мин smoke.

---

### PR A5 — `/readyz` не отдаёт `str(exc)` наружу

**Problem:** `app/api/internal/health.py:36-56` кладёт `str(exc)` в JSON body при ошибке. Клиент получает driver-level сообщения: путь к сокету, иногда креды. Утечка internal info.

**Goal:** тело ответа — только санитизированное. Детали — только в лог.

**Steps**

1. Открыть `app/api/internal/health.py`.
2. В except-ветке (строки ~36–56) заменить:
   ```python
   return JSONResponse({"status": "fail", "check": "postgres", "error": str(exc)}, status_code=503)
   ```
   на:
   ```python
   _logger.exception("readyz_check_failed", check="postgres")
   return JSONResponse({"status": "fail", "check": "postgres"}, status_code=503)
   ```
3. То же для redis/storage веток.
4. Добавить unit-тест `app/tests/test_readyz_sanitization.py`: мокаем Postgres-connect с исключением `RuntimeError("sensitive: user=admin password=xxx")`, проверяем `response.json()` не содержит `password` / `admin` / `xxx`.

**DoD**

- [ ] `rg -n "str(exc)" app/api/` пусто.
- [ ] Новый тест зелёный.
- [ ] `curl http://127.0.0.1:8080/readyz` (при лежащей БД) возвращает `{"status":"fail","check":"postgres"}` и **ничего больше**.

**Estimate:** 30 мин.

---

### PR A6 — `html.escape` на `selected.label` в download callback

**Problem:** `app/bot/callbacks/download.py:88-91` — `parse_mode="HTML"` + f-string с `selected.label`. Сейчас label-ы стат-ные (`"Видео 720p"`), но latent risk: если завтра label содержит user-supplied title — HTML injection.

**Goal:** любые строки из `DownloadOption` экранируются до вставки в HTML-caption.

**Steps**

1. Открыть `app/bot/callbacks/download.py`.
2. Импортировать `html` (stdlib).
3. В f-string заменить `{selected.label}` на `{html.escape(selected.label)}`.
4. Пройтись `rg -n 'parse_mode=.HTML.' app/bot/` — для каждого места проверить, что все user-supplied строки проходят через `html.escape`.
5. Unit-тест `app/tests/test_download_callback_html_escape.py`: `DownloadOption(label='<b>pwned</b>')` → caption содержит `&lt;b&gt;pwned&lt;/b&gt;`.

**DoD**

- [ ] Все `parse_mode="HTML"` в `app/bot/` не имеют неэкранированных user-strings.
- [ ] Тест зелёный.

**Estimate:** 10 мин + 15 мин тест.

---

### PR A7 — Alembic `DROP TABLE` без `IF EXISTS`

**Problem:** `migrations/versions/20260420_0000_0002_drop_unused_tables.py:29-33` безусловно `DROP TABLE app_settings, audit_logs`. Если в чужой базе (форк, клон) таблиц уже нет — миграция падает.

**Goal:** миграция идемпотентна.

**Steps**

1. Открыть `migrations/versions/20260420_0000_0002_drop_unused_tables.py`.
2. Заменить `op.drop_table("app_settings")` на:
   ```python
   op.execute("DROP TABLE IF EXISTS app_settings")
   ```
3. То же для `audit_logs` и обоих `drop_index` (использовать `DROP INDEX IF EXISTS ... CASCADE` через `op.execute`).
4. В тесте `app/tests/integration/test_migrations.py` (если есть — иначе создать в scope A19) прогнать `alembic upgrade head` на чистой БД и **дважды** — оба прогона должны пройти.

**DoD**

- [ ] Повторный `alembic upgrade 0002` после отката на 0001 не падает.
- [ ] CI `restore-drill.yml` остаётся зелёным.

**Estimate:** 10 мин.

---

### PR A8 — URL-encode password в `database_url`

**Problem:** `app/config.py:262-269` — DSN строится через f-string без encoding. Пароль с `@`/`:`/`/` ломает URI parser asyncpg.

**Goal:** любой пароль корректно доставляется в asyncpg.

**Steps**

1. Открыть `app/config.py`.
2. В методе, собирающем `database_url` (свойство `database_url` ~строка 262), заменить f-string на:
   ```python
   from urllib.parse import quote_plus
   ...
   password = quote_plus(self.POSTGRES_PASSWORD, safe="")
   return f"postgresql+asyncpg://{self.POSTGRES_USER}:{password}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
   ```
3. Unit-тест `app/tests/test_database_url_encoding.py`: `POSTGRES_PASSWORD="p@ss:word/!"` → в DSN присутствует `p%40ss%3Aword%2F%21`.

**DoD**

- [ ] Тест зелёный.
- [ ] `rg 'DATABASE_URL=' deploy/` — строчка в `.env.example` остаётся, но в `README`/docs подчёркиваем: если используется `DATABASE_URL` (override), пароль туда **должен быть encoded вручную**.

**Estimate:** 15 мин.

---

### PR A9 — `mem_limit` / `cpus` на worker и bot/api

**Problem:** compose без resource-limits. Transcoding 4K ffmpeg съест всю RAM NL-2 → OOM-kill других контейнеров.

**Goal:** каждый контейнер имеет bounded resources.

**Steps**

1. Открыть `deploy/nl1/docker-compose.yml`. Для `bot`, `api`, `backup`:
   ```yaml
   mem_limit: 512m
   cpus: 1.0
   ```
2. Для `postgres` (нагрузка по БД):
   ```yaml
   mem_limit: 1g
   cpus: 1.0
   ```
3. Для `redis`:
   ```yaml
   mem_limit: 1152m   # maxmemory 1g + 128m overhead
   cpus: 0.5
   ```
4. В `deploy/nl2/docker-compose.yml`. Для `worker`:
   ```yaml
   mem_limit: 2g
   cpus: 2.0
   ```
5. Для `api`, `nginx`, `cleanup`:
   ```yaml
   mem_limit: 512m
   cpus: 1.0
   ```
6. На хостах проверить `docker stats` в течение smoke-теста — ни один контейнер не бьёт cap.

**Edge cases**

- Если Docker на NL-2 запущен без `--cgroup-version v2` — `mem_limit` без `memswap_limit` позволит swap. Тогда добавить `memswap_limit: <same as mem_limit>` чтобы отключить swap.

**DoD**

- [ ] `docker inspect dwtgbot_worker | jq '.[0].HostConfig.Memory'` = `2147483648`.
- [ ] Smoke-тест (1 youtube + 1 instagram) проходит без OOM-kill.

**Estimate:** 15 мин + 30 мин smoke.

---

### PR A10 — `DOWNLOAD_TIMEOUT_SECONDS` применяется к yt-dlp download

**Problem:** `app/infrastructure/downloader/ytdlp_runner.py:411-422` — `_download_sync` не завёрнут в `asyncio.wait_for`. Фактический upper bound = `JOB_TIMEOUT_SECONDS` (arq) на download + transcode + upload. На длинных transcode arq прибивает задачу посреди ffmpeg.

**Goal:** download имеет свой отдельный таймаут.

**Steps**

1. Открыть `app/infrastructure/downloader/ytdlp_runner.py`.
2. Найти место вызова `_download_sync` (скорее всего через `asyncio.to_thread(...)` в методе `download()`).
3. Обернуть в:
   ```python
   try:
       await asyncio.wait_for(
           asyncio.to_thread(self._download_sync, ...),
           timeout=self._settings.DOWNLOAD_TIMEOUT_SECONDS,
       )
   except asyncio.TimeoutError as e:
       _logger.warning("ytdlp_download_timeout", timeout_s=self._settings.DOWNLOAD_TIMEOUT_SECONDS)
       raise DownloadError("timeout", is_retryable=False) from e
   ```
4. **Важно:** `asyncio.wait_for` на `to_thread` не прервёт subprocess yt-dlp (thread продолжает работу, зомби). Нужно изолировать yt-dlp в subprocess (если уже так — timeout автоматом убьёт proc; если нет — отдельная работа). В данном PR: задокументировать в коде `# known-limitation: subprocess not killed on timeout; yt-dlp finishes in background, caller has already returned`. Полный fix — в PR A20 (timeout + proc kill).
5. Unit-тест `app/tests/test_ytdlp_timeout.py`: mocked slow `_download_sync` → `DownloadError("timeout", is_retryable=False)` через `DOWNLOAD_TIMEOUT_SECONDS=1`.

**DoD**

- [ ] При `DOWNLOAD_TIMEOUT_SECONDS=1` и намеренно медленной ссылке worker через ~1 сек возвращает `DownloadError("timeout")`.
- [ ] `JOB_TIMEOUT_SECONDS` сохранён (оба лимита дополняют друг друга: download ≤ DOWNLOAD_TIMEOUT, весь job ≤ JOB_TIMEOUT).
- [ ] Тест зелёный.

**Estimate:** 1 час (включая тест).

---

### PR A11 — `RL_ENABLED` default → `True`

**Problem:** `app/config.py:228` — RL выключен по дефолту. Если оператор забыл включить — публичный бот без rate-limit. Одна viral ссылка уложит сервис.

**Goal:** безопасный default. Выключение — сознательное действие оператора.

**Steps**

1. Открыть `app/config.py`.
2. Поле `RL_ENABLED: bool = False` → `RL_ENABLED: bool = True`.
3. В обоих `deploy/nl1/.env.example` и `deploy/nl2/.env.example`:
   - Строка `RL_ENABLED=false` → `RL_ENABLED=true`.
   - Комментарий над строкой обновить: `# Enabled by default. Set to false ONLY after documenting in an ADR.`
4. `docs/17-security.md` (или аналогичный) — добавить абзац: "RL on by default. Disabling requires ADR."
5. Все существующие тесты, где RL выключался неявно, — явно выставить `RL_ENABLED=false` в test-env или переписать на mock.

**DoD**

- [ ] Новый чистый деплой автоматически имеет RL включённым.
- [ ] Тесты `pytest -m "not integration"` зелёные.
- [ ] `ruff check` / `mypy` чисто.

**Estimate:** 15 мин + 15 мин фикс тестов.

---

### PR A12 — `_logger.exception()` вместо `.warning()` в catch-all блоках

**Problem:** `app/application/use_cases/analyze_link.py:118-121`, `app/application/use_cases/process_download.py:291-294` и ~3 других места — `except Exception: _logger.warning(...)` без стектрейса. На проде диагностировать unknown-failure невозможно.

**Goal:** все broad-except блоки логируют stack trace.

**Steps**

1. `rg -n 'except Exception' app/ | rg -v 'tests'` — составить полный список.
2. Для каждой найденной пары:
   - Если ошибка ожидаемая и обрабатывается как business-logic: `_logger.warning(...)` с явным `exc_info=True` + полями.
   - Если ошибка неожиданная / swallowed: `_logger.exception(...)` — это `ERROR` level с автоматическим stack trace.
3. Правило: **после `except Exception:` либо `_logger.exception(...)`, либо `raise`**. Никаких silent `warning`.
4. Добавить lint-правило в `.cursor/rules/30-error-and-logging.mdc`: запрещён `_logger.warning` в branch `except Exception`. Оформить pre-commit check через `rg`-assertion.

**DoD**

- [ ] `rg -n 'except Exception' app/ -A 2 | rg '_logger\.warning\('` пусто (после исправлений).
- [ ] `ruff check` зелёный.

**Estimate:** 30 мин (5 мест, мало кода).

---

### PR A13 — ADR-0010 в `docs/adr/README.md`

**Problem:** `docs/adr/README.md` не обновлён — новый ADR-0010 отсутствует в индексе.

**Steps**

1. Открыть `docs/adr/README.md`.
2. Добавить строку таблицы:
   ```
   | 0010 | Instant download UX | Proposed | ...link... |
   ```
3. Сохранить.

**DoD**

- [ ] `rg -n '0010' docs/adr/README.md` — совпадение есть.
- [ ] CI lychee (link-checker) зелёный.

**Estimate:** 5 мин.

---

### PR A14 — Alembic index `(status, updated_at)` на `download_jobs`

**Problem:** `reap_orphan_processing` использует `WHERE status = 'PROCESSING' AND updated_at < :cutoff`. Индекса нет — при росте до 100k+ jobs cleanup-sweep идёт seq-scan.

**Goal:** orphan-reaper константного времени.

**Steps**

1. Новая миграция `migrations/versions/20260501_0000_0003_index_status_updated_at.py`:
   ```python
   revision = "0003_index_status_updated_at"
   down_revision = "0002_drop_unused_tables"

   def upgrade():
       op.create_index(
           "ix_download_jobs_status_updated_at",
           "download_jobs",
           ["status", "updated_at"],
       )

   def downgrade():
       op.drop_index("ix_download_jobs_status_updated_at", table_name="download_jobs")
   ```
2. Прогнать `alembic upgrade head` локально.
3. Проверить `EXPLAIN SELECT ... FROM download_jobs WHERE status='PROCESSING' AND updated_at < now() - interval '1 hour'` — должен быть `Index Scan using ix_download_jobs_status_updated_at`.

**DoD**

- [ ] Миграция применяется/откатывается.
- [ ] `EXPLAIN` показывает index scan.
- [ ] `restore-drill.yml` зелёный.

**Estimate:** 30 мин.

---

### PR A15 — Pin версий `certbot`, `ffmpeg`, `awscli`, `rclone`

**Problem:** `deploy/nl2/docker-compose.yml:108-110` — `certbot/certbot:latest`. `docker/worker.Dockerfile:38-39` — `apt install ffmpeg` без версии. `backup.Dockerfile` — аналогично для `awscli`/`rclone`. Mutable teg = не-reproducible builds.

**Goal:** все инфра-образы и apt-пакеты — с явной версией.

**Steps**

1. `deploy/nl2/docker-compose.yml` → `certbot/certbot:v2.11.0` (текущая stable на момент фикса).
2. `docker/worker.Dockerfile` — `apt install ffmpeg=7:6.*` (минимум major). Идеально — собрать ffmpeg из BtbN builds с явной версией, но это большой PR; здесь — только пин apt.
3. Аналогично `awscli`, `rclone` в `backup.Dockerfile` — через apt pin или явный `curl` с sha256-проверкой.
4. Прогнать полный rebuild всех 4 образов локально: `docker buildx build .`.
5. Зафиксировать версии в `CHANGELOG.md`.

**DoD**

- [ ] `rg ':latest' deploy/` — только в `.env.example` (там плейсхолдер) или 0 совпадений.
- [ ] Все 4 образа успешно собираются.

**Estimate:** 30 мин.

---

## 2. SPRINT 2.2 — Security hardening

Четыре PR'а, все — security-sensitive. Каждый требует тест + security-ревью.

### PR A16 — Middleware `require_api_token` на internal-роуты

**Problem:** `app/config.py:336-337` — `API_INTERNAL_TOKEN` декларирован, но **ни один роут его не проверяет**. Grep подтверждает: только `INTERNAL_TEST_TOKEN` используется и только на dev-endpoint. `/readyz`, `/healthz`, `/d/{token}` (и любой будущий `/internal/*`) открыты для всего, кто достучался до `:8080`. На NL-2 это за nginx, но на NL-1 — прямой доступ по WireGuard.

**Goal:** все internal-endpoint'ы (кроме публичного `/d/{token}` и `/healthz` для ACME) защищены через `X-Internal-Token` + `hmac.compare_digest`.

**Steps**

1. Создать `app/api/middleware/api_token.py`:
   ```python
   import hmac
   from typing import Callable
   from fastapi import Request, HTTPException, status

   async def require_api_token(request: Request, call_next: Callable):
       from app.config import get_settings
       settings = get_settings()
       if not settings.API_INTERNAL_TOKEN:
           return await call_next(request)  # dev mode, open
       supplied = request.headers.get("X-Internal-Token", "")
       if not hmac.compare_digest(supplied, settings.API_INTERNAL_TOKEN):
           raise HTTPException(status.HTTP_401_UNAUTHORIZED)
       return await call_next(request)
   ```
2. В `app/api/main.py` (или где собирается FastAPI app) — добавить dependency/middleware **только** на префиксы `/readyz`, `/internal/*`. `/healthz` остаётся открытым (ACME, Docker healthcheck без права иметь токен). `/d/{token}` — уже защищён собственным token-check'ом.
3. На NL-2 обновить nginx-snippet, чтобы при проксировании `/readyz` добавлялся `proxy_set_header X-Internal-Token $api_internal_token;` (ENV → envsubst). Это нужно чтобы Docker healthcheck (который через `curl localhost`) всё ещё работал. Альтернатива: `/healthz` используется и для readiness — но это откатывает фикс A2.
   - **Лучший вариант:** Docker healthcheck ходит напрямую на `127.0.0.1:8080/readyz` внутри контейнера **с токеном**, через `-H "X-Internal-Token: ${API_INTERNAL_TOKEN}"`. В compose `test:` это `CMD-SHELL` с variable substitution.
4. Unit-тест `app/tests/api/test_internal_token_middleware.py`: запрос без токена → 401; с невалидным токеном → 401; с валидным → 200.
5. Integration-тест: поднять api-контейнер, проверить что Docker healthcheck проходит.

**Risks**

- Prometheus scraper тоже должен уметь поставить токен. Если он используется — обновить scrape_config.
- `/readyz` теперь 401 без токена. Внешний мониторинг (uptime robot) должен переключиться на `/healthz` или получить токен.

**DoD**

- [ ] `curl http://127.0.0.1:8080/readyz` без токена → 401.
- [ ] `curl -H "X-Internal-Token: $TOKEN" http://127.0.0.1:8080/readyz` → 200 (при здоровой БД).
- [ ] Docker healthcheck с токеном зелёный.
- [ ] Все тесты pass.

**Estimate:** 2 дня (включая интеграцию с nginx и тесты).

---

### PR A17 — SSRF через yt-dlp redirect → `allowed_extractors` + outbound proxy ACL

**Problem:** `app/infrastructure/downloader/ytdlp_runner.py:270-293` — host-whitelist в `app/utils/url.py` проверяет только исходный URL. yt-dlp следует редиректам. Через `youtu.be/xxx` или сервис-сокращатель можно заставить yt-dlp дёрнуть `http://10.10.0.1:5432` (внутренний Postgres) или `http://169.254.169.254/` (cloud metadata).

**Goal:** yt-dlp физически не может вытянуть URL вне whitelist'а.

**Steps**

1. **Этап 1 — application-level guard.** В `YtdlpRunner.download()` передавать в yt-dlp опцию:
   ```python
   ytdlp_opts["allowed_extractors"] = ["Youtube", "Instagram", "YoutubeTab"]
   ```
   Это ограничивает yt-dlp только нашими провайдерами — неизвестные хосты не ресолвятся через generic-extractor.
2. **Этап 2 — URL filter callback.** yt-dlp имеет `match_filter` на уровне entry. Добавить:
   ```python
   def _url_filter(info_dict: dict) -> str | None:
       from app.utils.url import is_allowed_host
       url = info_dict.get("webpage_url") or info_dict.get("url") or ""
       if not is_allowed_host(url):
           return f"Disallowed host for URL {url!r}"
       return None
   ytdlp_opts["match_filter"] = _url_filter
   ```
3. **Этап 3 — network isolation.** Для прод-обстановки выставить `HTTPS_PROXY_URL` на outbound-прокси с ACL (например, `tinyproxy` или `squid` в отдельном контейнере, которому разрешено только `youtube.com`, `googlevideo.com`, `instagram.com`, `cdninstagram.com`, `ggpht.com`, `ytimg.com`). Это защищает от редиректов, которые ускользнут от match_filter (например, CDN внутри Google). В рамках этого PR — создать `deploy/proxy/` с compose-фрагментом, задокументировать. Активация — ENV на NL-2 (`HTTPS_PROXY_URL=http://proxy:3128`).
4. Unit-тест `app/tests/test_ytdlp_ssrf_guard.py`: mock yt-dlp options, убедиться что `allowed_extractors` и `match_filter` передаются.
5. Integration-тест (manual): `yt-dlp` с `--allowed-extractors Youtube,Instagram` не скачает `https://example.com/file.mp4`.

**Edge cases**

- Ссылки типа `youtu.be/...` раскрываются через Youtube extractor → их не заденет allowed_extractors. OK.
- Инстаграм Reels / Stories / IGTV — разные extractors? Проверить и добавить в whitelist.

**DoD**

- [ ] `allowed_extractors` + `match_filter` передаются в yt-dlp.
- [ ] Тесты зелёные.
- [ ] `deploy/proxy/` документирован в `docs/17-security.md`.
- [ ] Manual test: подать `https://example.com/` — yt-dlp возвращает `Unsupported URL`.

**Estimate:** 1 день (без proxy-deployment), +0.5 дня на proxy (optional этап).

---

### PR A18 — `ProcessDownloadUseCase` ранний exit при не-PENDING статусе

**Problem:** `app/application/use_cases/process_download.py:56-76` — `execute()` не проверяет, что job в `PENDING` перед `_transition_to(PROCESSING)`. В at-least-once семантике arq может прилететь replay для `DONE` job'а → мы сбросим его в `PROCESSING` и начнём скачивать снова. Плюс side-effects (повторная доставка).

**Goal:** no-op при replay завершённого job'а.

**Steps**

1. Открыть `app/application/use_cases/process_download.py`.
2. В начале `_run()` (после `await self._jobs.get(job_id)`), до `_transition_to(PROCESSING)`:
   ```python
   if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
       _logger.warning(
           "job_replay_ignored",
           job_id=job.id,
           current_status=job.status.value,
       )
       return  # idempotent no-op
   if job.status == JobStatus.PROCESSING:
       _logger.warning("job_concurrent_processing_detected", job_id=job.id)
       # advisory lock in DB should have caught this, but defensive:
       return
   ```
3. Добавить метрику `job_replay_ignored_total{reason=done|failed|cancelled|processing}`.
4. Unit-тест `app/tests/test_process_download_replay_guard.py`: fake-repo возвращает `DONE` job, use-case — no-op, no side-effects, metric incremented.

**DoD**

- [ ] Тест зелёный.
- [ ] Метрика регистрируется в `/metrics`.

**Estimate:** 1 день.

---

### PR A19 — HEAD-check / yt-dlp `filesize_approx` для pre-download size cap

**Problem:** `app/application/use_cases/process_download.py:179-200` — pre-download size cap работает только если `option.estimated_size_bytes is not None`. Многие yt-dlp форматы estimate == None → качается весь файл, guard только в delivery (пост-фактум). Пользователь может заказать 4K+часовое видео, сжечь 20 GB трафика.

**Goal:** **unknown size** либо вычисляется до скачивания, либо hard-fail.

**Steps**

1. В `YtdlpRunner` добавить метод `probe_size(url, format_spec) -> int | None`:
   ```python
   def probe_size(self, url: str, format_spec: str) -> int | None:
       opts = {**self._base_opts(), "format": format_spec, "simulate": True, "quiet": True}
       with yt_dlp.YoutubeDL(opts) as ydl:
           info = ydl.extract_info(url, download=False)
       return info.get("filesize") or info.get("filesize_approx")
   ```
2. В `ProcessDownloadUseCase._reject_if_estimate_exceeds_cap`:
   - Если `option.estimated_size_bytes is None` — вызвать `await asyncio.to_thread(self._downloader.probe_size, url, format_spec)`.
   - Если и после этого `None` и `option.kind == VIDEO` — **hard-fail** с `ReasonClass.SIZE_UNKNOWN` (new). Пользователю: "Не удалось определить размер, попробуйте другую ссылку."
   - Если определили — прогнать через существующий `MAX_FILE_SIZE_MB` check.
3. Unit-тест `app/tests/test_size_cap_unknown.py`: probe возвращает None → `DownloadError("size_unknown", is_retryable=False)`.

**Risks**

- `probe_size` = extra network call. Кэшируется ли yt-dlp? Да, но только в `YoutubeDL` instance. Для первого analyze → download flow кэша нет. Overhead = 1 HTTPS-запрос. Приемлемо.
- Для Instagram `filesize_approx` часто None. Фикс: для IG использовать `HEAD`-request на `items[0].url` через `httpx` (синхронно, 1 реквест) — получить `Content-Length`.

**DoD**

- [ ] Unknown-size видео возвращает явную ошибку до начала download.
- [ ] Known-size — работает как раньше.
- [ ] Тесты зелёные.

**Estimate:** 1 день.

---

## 3. SPRINT 2.3 — Reliability

### PR A20 — Storage orphan-dirs sweep в `cleanup_worker`

**Problem:** `app/application/use_cases/process_download.py:113-115,155-170` — при fail-ветке `job_dir` не удаляется. Плюс при crash'е worker'а (OOM, SIGKILL) `jobs/<id>/` остаётся навсегда. cleanup чистит только `STORAGE_TMP_PATH` и temp-link файлы.

**Goal:** orphan job-dirs чистятся автоматически.

**Steps**

1. В `app/workers/cleanup_worker.py` расширить `_run_cycle`:
   ```python
   async def _sweep_orphan_job_dirs(composition: CleanupComposition) -> None:
       storage_path = composition.settings.STORAGE_PATH
       jobs_dir = Path(storage_path) / "jobs"
       if not jobs_dir.exists():
           return
       cutoff = datetime.now(UTC) - timedelta(hours=24)
       async with composition.core.sessionmaker() as session:
           repo = SqlAlchemyJobsRepository(session)
           for entry in jobs_dir.iterdir():
               if not entry.is_dir() or not entry.name.isdigit():
                   continue
               job_id = int(entry.name)
               job = await repo.get(job_id)
               if job is None or job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                   # no DB row or terminal status — check age
                   mtime = datetime.fromtimestamp(entry.stat().st_mtime, UTC)
                   if mtime < cutoff:
                       shutil.rmtree(entry, ignore_errors=True)
                       _logger.info("orphan_job_dir_removed", job_id=job_id)
   ```
2. Вызвать из `_run_cycle` после `reap_orphan_processing`.
3. Метрика `storage_orphan_dirs_removed_total`.
4. Unit-тест `app/tests/test_cleanup_orphan_dirs.py`: fake-repo без записи, dir возрастом 25 ч → удалён; dir возрастом 1 ч → остался.

**DoD**

- [ ] На NL-2 после прогонов с принудительным crash worker'а orphan-dir'ы исчезают за ≤ `CLEANUP_INTERVAL_SECONDS + 24h`.
- [ ] Метрика в `/metrics`.

**Estimate:** 1 день.

---

### PR A21 — Optimistic locking на `JobsRepository.update`

**Problem:** `app/infrastructure/db/repositories/jobs_repo_impl.py:58-86` — `update` делает `get + mutate + flush` без `WHERE status=?`. Две параллельные `update` (worker + cleanup) перетрут друг друга.

**Goal:** update падает с conflict, если статус изменился с момента чтения.

**Steps**

1. Добавить в модель `DownloadJob` колонку `status_version: int` (default 0, increments on each update). Alembic миграция `0004_add_status_version.py`:
   ```python
   op.add_column("download_jobs", sa.Column("status_version", sa.Integer(), nullable=False, server_default="0"))
   ```
2. В `JobsRepository.update`:
   ```python
   result = await session.execute(
       update(DownloadJob)
       .where(DownloadJob.id == job.id, DownloadJob.status_version == job.status_version)
       .values(status=..., status_version=DownloadJob.status_version + 1, ...)
   )
   if result.rowcount == 0:
       raise JobConcurrentUpdateError(job_id=job.id)
   ```
3. Caller (`ProcessDownloadUseCase`) ловит `JobConcurrentUpdateError` и делает `job = await self._jobs.get(job.id)` + retry логику transition.
4. Unit-тест: две параллельные update на один job → одна из них `JobConcurrentUpdateError`.

**DoD**

- [ ] Миграция применяется.
- [ ] Параллельные update не теряют данные.
- [ ] Тест зелёный.

**Estimate:** 1 день (+ ревью, т.к. трогает core-pathway).

---

### PR A22 — `increment_retries()` действительно вызывается

**Problem:** `app/infrastructure/db/repositories/jobs_repo_impl.py` — метод `increment_retries` определён, но нигде не вызывается. БД `retries_count` всегда 0, метрики врут.

**Goal:** на каждом retry arq `retries_count` инкрементируется в БД.

**Steps**

1. В `app/workers/tasks.py` (или wherever arq-task `process_download` вызывается) — обернуть вызов use-case в:
   ```python
   try:
       await use_case.execute(payload)
   except Exception:
       await jobs_repo.increment_retries(job_id=payload.job_id)
       raise
   ```
2. Unit-тест `app/tests/test_retry_count_increment.py`: mocked arq job_try=3 → после fail `retries_count` в DB = 3.

**DoD**

- [ ] Метрика `job_retries_total` (если есть) совпадает с БД-значением.

**Estimate:** 0.5 дня.

---

### PR A23 — Runtime-assert `ORPHAN_JOB_AGE_SECONDS > JOB_TIMEOUT_SECONDS * 1.5`

**Problem:** `app/config.py` декларирует инвариант в комментарии, но не проверяет. Оператор может выставить `ORPHAN_JOB_AGE_SECONDS=60`, `JOB_TIMEOUT_SECONDS=1800` → reaper будет прибивать живые задачи.

**Goal:** invariant enforce'ится на старте.

**Steps**

1. В `Settings.validate_runtime`:
   ```python
   if self.ORPHAN_JOB_AGE_SECONDS < self.JOB_TIMEOUT_SECONDS * 1.5:
       raise ValueError(
           f"ORPHAN_JOB_AGE_SECONDS ({self.ORPHAN_JOB_AGE_SECONDS}) must be >= "
           f"JOB_TIMEOUT_SECONDS * 1.5 ({self.JOB_TIMEOUT_SECONDS * 1.5}) "
           f"to avoid reaping live jobs."
       )
   ```
2. Unit-тест `app/tests/test_config_runtime_asserts.py` с `APP_ENV=production`.

**DoD**

- [ ] Некорректная комбинация падает на старте с понятным сообщением.

**Estimate:** 0.5 дня.

---

### PR A24 — `asyncpg` statement/connect timeouts

**Problem:** `app/infrastructure/db/session.py` — нет `command_timeout` / `connect_timeout`. При WireGuard flap pool висит.

**Goal:** hard timeout на каждый SQL + connect.

**Steps**

1. Открыть `app/infrastructure/db/session.py`. В `create_async_engine`:
   ```python
   connect_args = {
       "command_timeout": settings.DB_STATEMENT_TIMEOUT_S,
       "timeout": settings.DB_CONNECT_TIMEOUT_S,
       "server_settings": {
           "idle_in_transaction_session_timeout": str(settings.DB_IDLE_IN_TX_TIMEOUT_MS),
       },
   }
   ```
2. Добавить в `Settings`:
   ```python
   DB_STATEMENT_TIMEOUT_S: int = 30
   DB_CONNECT_TIMEOUT_S: int = 10
   DB_IDLE_IN_TX_TIMEOUT_MS: int = 60_000
   ```
3. В `.env.example` (оба) — задокументировать.
4. Unit-тест ограничен mock'ом (реальный WG flap → integration).

**DoD**

- [ ] Settings вбирает новые поля.
- [ ] Engine создаётся с connect_args.

**Estimate:** 0.5 дня.

---

## 4. SPRINT 2.4 — Observability & tests

### PR A25 — Integration-test job в CI

**Problem:** `.github/workflows/ci.yml:97` — `pytest -m "not integration"`. `test_advisory_lock_postgres.py` никогда не бежит в CI.

**Goal:** Postgres/Redis-integration тесты гоняются на каждый PR.

**Steps**

1. В `.github/workflows/ci.yml` добавить job `integration-tests`:
   ```yaml
   integration-tests:
     runs-on: ubuntu-latest
     services:
       postgres:
         image: postgres:16-alpine
         env: { POSTGRES_USER: test, POSTGRES_PASSWORD: test, POSTGRES_DB: test }
         ports: ["5432:5432"]
         options: --health-cmd pg_isready ...
       redis:
         image: redis:7-alpine
         ports: ["6379:6379"]
     steps:
       - checkout
       - setup-python
       - install deps
       - alembic upgrade head
       - pytest -m integration
   ```
2. Прогнать локально: `docker run ... && pytest -m integration` — зелёно.
3. Убедиться, что CI-job не тимаутит на ≤ 5 мин.

**DoD**

- [ ] `pytest -m integration` зелёный в CI на каждый PR.
- [ ] advisory_lock_postgres тест реально прогоняется.

**Estimate:** 2 дня (включая fixture-рефакторинг).

---

### PR A26 — E2E fixture `FakeYtDlp` + `FakeTelegram`

**Problem:** Регрессии `ffmpeg`-аргументов ловятся только на проде (см. последние 4 фикса `287b914`, `988e9ea` и т.д.).

**Goal:** полный цикл enqueue → worker → delivery проверяется unit-ит тестом без внешних сервисов.

**Steps**

1. Создать `app/tests/e2e/test_full_download_flow.py`.
2. `FakeYtDlp`: создаёт файл на диске (fixture `tmp_path`) с заранее определённым MP4 (checked-in 1-sec video в `app/tests/e2e/fixtures/sample.mp4`, < 100 KB).
3. `FakeTelegram`: ловит `send_video` / `send_message` / `edit_message_caption` в список. Тест проверяет, что caption содержит title + size + footer.
4. `FakeRedis` уже есть — переиспользовать.
5. Тест: `AutoEnqueueDownloadUseCase.execute(url="https://youtube.com/watch?v=fake")` → `ProcessDownloadUseCase.execute(payload)` → `DeliveryService.deliver(result)` → FakeTelegram.sent[0] содержит ожидаемые поля.
6. 3 варианта: happy path, download timeout (FakeYtDlp спит > DOWNLOAD_TIMEOUT), delivery fallback на temp-link (> 50 MB).

**DoD**

- [ ] 3 E2E теста зелёные.
- [ ] `pytest -m "not integration" -k e2e` < 10 сек.

**Estimate:** 3 дня.

---

### PR A27 — Trivy + Gitleaks в CI

**Problem:** нет security-сканирования. Уязвимый base image / закомиченный секрет замечаются вручную.

**Goal:** каждый PR сканируется.

**Steps**

1. `.github/workflows/ci.yml`:
   ```yaml
   trivy:
     runs-on: ubuntu-latest
     steps:
       - checkout
       - name: Trivy fs scan
         uses: aquasecurity/trivy-action@master
         with:
           scan-type: fs
           severity: HIGH,CRITICAL
           exit-code: 1
   gitleaks:
     runs-on: ubuntu-latest
     steps:
       - checkout with fetch-depth: 0
       - gitleaks/gitleaks-action@v2
   ```
2. Прогнать на текущем репо — убедиться, что нет ложных срабатываний.

**DoD**

- [ ] Оба job'а зелёные на main.
- [ ] PR с намеренно добавленным фейк-токеном (локальная проверка) валится.

**Estimate:** 1 день.

---

### PR A28 — `--cov-fail-under=70` в pytest

**Problem:** coverage может регрессировать тихо.

**Goal:** coverage gate в CI.

**Steps**

1. В `pyproject.toml` (tool.pytest.ini_options):
   ```toml
   addopts = "--cov=app --cov-report=term --cov-fail-under=70"
   ```
2. Прогнать локально: `pytest`. Если < 70% — понять почему и либо добавить тесты, либо отрегулировать bar.
3. В CI-workflow — убедиться что coverage artifact загружается.

**DoD**

- [ ] CI валится на coverage < 70%.
- [ ] Текущий coverage ≥ 70%.

**Estimate:** 0.5 дня.

---

### PR A29 — `upstream api:${API_PORT}` templating в nginx

**Problem:** `deploy/nginx/conf.d/media.conf.template:14-17` — жёстко `api:8080`. `API_PORT` в `.env` игнорируется.

**Goal:** `API_PORT` — единственный источник правды.

**Steps**

1. Заменить `server api:8080;` на `server api:${API_PORT};`.
2. В `docker-entrypoint.d` (или командой запуска nginx) обеспечить `envsubst` для `API_PORT` тоже (сейчас — только `SERVER_NAME`).
3. Проверить `docker compose config` — развёрнутый template содержит правильный порт.

**DoD**

- [ ] Меняем `API_PORT=9000` в `.env`, перезапускаем — трафик идёт на 9000.

**Estimate:** 30 мин.

---

## 5. Суммарный учёт по PR'ам

| Sprint | PR'ы | Оценка |
|---|---|---|
| 2.1 Quick wins | A1–A15 (15) | 1.5 д |
| 2.2 Security | A16–A19 (4) | 3 д |
| 2.3 Reliability | A20–A24 (5) | 3 д |
| 2.4 Obs & tests | A25–A29 (5) | 3 д |
| **Итого** | **29 PR** | **~10 д** |

---

## 6. Контроль прогресса

После каждого sprint'а отметить в `docs/tasks/README.md` → столбец "Статус".

После всех четырёх — закрыть TOP-10 из аудита:

| # | Проблема из TOP-10 | Закрыта в PR |
|---|---|---|
| 1 | Docker healthcheck `/healthz` → `/readyz` | A2 |
| 2 | `deploy.yml target: nl2` skip | A3 |
| 3 | `API_INTERNAL_TOKEN` не проверяется | A16 |
| 4 | SSRF через yt-dlp redirect | A17 |
| 5 | `DOWNLOAD_TIMEOUT_SECONDS` не применяется | A10 |
| 6 | Replay-guard на `process_download` | A18 |
| 7 | Pre-download size cap при unknown size | A19 |
| 8 | Orphan job-dirs не чистятся | A20 |
| 9 | Redis без `--maxmemory` | A4 |
| 10 | `cleanup.sh` ImportError | A1 |

Плюс 19 дополнительных PR'ов закрывают оставшиеся пункты из 15 категорий.

---

## 7. OUT OF SCOPE (Phase 3 — отдельный ADR)

Не делаем в этом task'е:

- **Ports/Services унификация** (`StoragePort`, `TelegramSenderPort`). Трогает use-case signatures, 1 неделя, нужен ADR.
- **Graceful degradation** для Redis/Postgres down. Нужна доктрина для `DegradedMode` + user-facing фразы.
- **Canary deploy + auto-rollback** по `/readyz`. Инфра-задача.
- **Per-user daily disk quota.** Требует product-decision по лимитам.
- **Local Bot API server** (2 GB). Отдельный ADR.
- **OpenTelemetry tracing**. Отдельный ADR + коллектор.
- **Multi-region worker pool.** Отдельная инфра-сессия.
- **k8s миграция.** После зрелости текущей инфры (6+ месяцев).

Эти пункты попадут в отдельный `docs/tasks/phase-3-*.md` по мере созревания решений.

---

## 8. Замечания по порядку внутри sprint'а

- **Sprint 2.1** можно делать одним PR (batch), но по-канону держим 15 отдельных — чтобы blast-radius каждого был минимальным и bisect по регрессиям был точным.
- **Sprint 2.2 → 2.3:** A17 (SSRF outbound proxy) требует NL-2 changes. Если прод-инфра недоступна во время работы — этап 3 из A17 можно отложить на Phase 3, а этапы 1–2 (application-level guards) закрыть в 2.2.
- **Sprint 2.4** делается **после** Sprint 2.3: coverage gate после E2E-fixture имеет смысл (иначе новые E2E-тесты не успеют поднять bar).

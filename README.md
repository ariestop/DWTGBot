# DWTGBot — Telegram-бот для скачивания медиа

Готовый к продакшну Telegram-бот, который скачивает медиа с **YouTube** и
**Instagram**, отправляет небольшие файлы напрямую через Telegram, а большие
выдаёт через **токенизированные временные HTTPS-ссылки**, обслуживаемые Nginx.

Система состоит из двух плоскостей:

- **control plane** — bot, Postgres, Redis, backups
- **media plane** — worker (`yt-dlp` + `ffmpeg`), Nginx + Certbot, файловое хранилище

Поддерживаются две топологии ([ADR-0011](docs/adr/0011-single-server-topology.md)):

- **`single`** (рекомендуется для старта) — обе плоскости на одном хосте
  (`deploy/single`). Они разделены сетями Docker, наружу открыты только `:80`/`:443`.
- **`split`** — **NL-1** (control) + **NL-2** (media), связь по приватной сети
  (WireGuard). Переход `single → split` не требует миграции данных
  ([`docs/24-runbooks.md`](docs/24-runbooks.md) §26).

---

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Быстрый старт (один хост, dev)](#быстрый-старт-один-хост-dev)
- [Продакшн-развертывание](#продакшн-развертывание) — см. также [`docs/20-deployment.md`](docs/20-deployment.md)
- [Справочник конфигурации](#справочник-конфигурации)
- [Операции](#операции)
- [Разработка](#разработка)
- [Тестирование и CI](#тестирование-и-ci)
- [Структура проекта](#структура-проекта)
- [Устранение неполадок](#устранение-неполадок) — см. также [`docs/24-runbooks.md`](docs/24-runbooks.md) (операционные runbook'и) и [`docs/31-troubleshooting.md`](docs/31-troubleshooting.md) (диагностика)
- [Лицензия](#лицензия)

---

## Возможности

- **Команды**: `/start`, `/help`, `/health`, `/about`.
- **YouTube**: реальные доступные качества (360p / 480p / 720p / 1080p) + MP3-аудио,
  оценка размера файла, объединение видео+аудио через `ffmpeg`.
- **Instagram**: одиночное видео/фото, карусель/галерея (скачать всё, только видео,
  только фото), опциональная упаковка в ZIP.
- **Учитывается лимит Telegram 50 MB** — всё, что превышает настроенный лимит,
  отдаётся как токенизированная временная ссылка (TTL + максимум скачиваний,
  через Nginx `X-Accel-Redirect`).
- **Асинхронная фоновая обработка** — очередь `arq` в Redis, идемпотентные задачи,
  retry с backoff, dead-letter logging, понятные пользователю статусы.
- **Продакшн-логирование** — JSON-вывод `structlog` с корреляцией по `request_id` /
  `job_id` / `user_id` / `chat_id`.
- **Health endpoints** — `/healthz` (liveness) и `/readyz` (DB + Redis + storage).
- **Операционные инструменты** — интерактивное меню `install.sh`, идемпотентный
  `deploy_update.sh`, автоматические бэкапы и restore Postgres, cleanup истёкших
  ссылок, шаблоны firewall `ufw`.
- **Контейнеризация** — multi-stage Dockerfile'ы, non-root пользователи, два
  стека `docker-compose`, bootstrap SSL через Certbot.
- **CI/CD** — GitHub Actions: ruff + mypy + pytest + shellcheck, публикация
  образов в GHCR, ручной SSH deploy.

---

## Архитектура

```
                          ┌──────────────┐
                          │  Telegram    │
                          │  Bot API     │
                          └──────┬───────┘
                                 │ long-poll
                                 ▼
   ┌─────────────────────────────────────────────────────┐
   │ NL-1 (control plane)                                │
   │                                                     │
   │   bot ──enqueue──► redis ◄──poll── worker (NL-2)    │
   │     │                                               │
   │     └─► postgres (jobs, temp_links, media_cache)    │
   │                                                     │
   │   api (internal /healthz /readyz)                   │
   │   backup (cron pg_dump → /var/backups)              │
   └────────────────────────┬────────────────────────────┘
                            │ private network (WireGuard)
                            │   • Postgres :5432
                            │   • Redis    :6379
                            ▼
   ┌─────────────────────────────────────────────────────┐
   │ NL-2 (media plane)              Internet ► :80/:443 │
   │                                          │          │
   │   nginx ──► api (public /d/{token})      │          │
   │     │                                    │          │
   │     │   X-Accel-Redirect ◄──────────────┘          │
   │     ▼                                               │
   │   /_protected/  (internal)  ──► STORAGE_PATH        │
   │                                                     │
   │   worker  (yt-dlp + ffmpeg)  ──► STORAGE_PATH       │
   │     │                                               │
   │     └─► sends small files directly via Telegram Bot │
   │                                                     │
   │   cleanup (deactivate expired links + purge files)  │
   │   certbot (auto-renew Let's Encrypt)                │
   └─────────────────────────────────────────────────────┘
```

Слои (`app/`):

| Слой | Папка | Назначение |
|---|---|---|
| Domain | `domain/` | Чистые entities, enums, интерфейсы репозиториев |
| Application | `application/` | Use cases + service protocols + DTOs |
| Infrastructure | `infrastructure/` | DB, Redis, queue, providers, downloader, storage, telegram |
| Bot | `bot/` | handlers, callbacks, keyboards, middleware для `python-telegram-bot` |
| API | `api/` | FastAPI: внутренние health endpoints + публичные temp-link routes |
| Workers | `workers/` | Долгоживущие процессы cleanup + backup |
| Composition | `composition.py` | Dependency wiring (composition root) |

---

## Быстрый старт (один хост, dev)

Требования: Python 3.14+, Docker, `ffmpeg`. Целевая ОС для продакшна:
**Ubuntu 24.04 LTS**.

```bash
git clone <repo-url> dwtgbot && cd dwtgbot
cp .env.example .env                 # минимум заполните BOT_TOKEN
make dev-install                     # создаёт .venv + ставит dev-зависимости
make precommit-install               # опционально, рекомендуется

# Поднимите только Postgres + Redis через NL-1 stack:
cp deploy/nl1/.env.example deploy/nl1/.env
deploy/scripts/install.sh            # интерактивное меню (или Makefile targets)

# Запустите bot/api/worker в трёх терминалах:
make migrate
make bot
make api
make worker
```

Отправьте YouTube/Instagram-ссылку своему боту в Telegram. В ответ должны
появиться варианты скачивания в виде inline-кнопок.

---

## Продакшн-развертывание

Полная пошаговая инструкция находится в [`docs/20-deployment.md`](docs/20-deployment.md)
(канонический документ), а [`docs/24-runbooks.md`](docs/24-runbooks.md) описывает
реакцию на инциденты.

### Один сервер (`single`, рекомендуется)

1. **Подготовьте VM** на Ubuntu 24.04 LTS (рекомендуется 4 vCPU / 8 GB RAM /
   80+ GB SSD). Направьте DNS A-record домена на её IP.
2. **Склонируйте репозиторий** в `/opt/dwtgbot` и запустите
   `sudo bash deploy/scripts/install.sh`. В меню по порядку: `[1]` установка
   Docker (нужен Compose ≥ 2.24) → `[19]` **Prepare single server** (каталоги +
   генерация `deploy/single/.env` + cookies Instagram) → `[5]` firewall → `[6]`
   start containers → `[12]` SSL-сертификат.
3. **Cookies Instagram** (без них Instagram не скачивается). Установщик спросит
   их на шаге `[19]`; заменить позже — пункт `[20]` или
   `sudo bash deploy/scripts/cookies_setup.sh instagram`. Коротко: войдите в
   отдельный аккаунт Instagram в браузере, выгрузите cookies расширением
   «Get cookies.txt LOCALLY» (формат Netscape) и укажите файл скрипту — либо
   введите `sessionid`, `ds_user_id`, `csrftoken` из DevTools. Подробно:
   [`docs/20-deployment.md`](docs/20-deployment.md) §4a.5.
4. **Настройте offsite-бэкапы** (`BACKUP_S3_*`). В `single` бэкап лежит на том
   же диске, что и база, поэтому offsite обязателен.
5. **Проверьте состояние**: `bash deploy/scripts/healthcheck.sh single`.

### Два сервера (`split`)

1. **Подготовьте две VM** (NL-1 control plane, NL-2 media plane). **Только
   Ubuntu 24.04 LTS** — TUI installer привязывает Docker apt repo к `noble`.
   Настройте приватную сеть между серверами (рекомендуется WireGuard), чтобы
   NL-2 мог обращаться к NL-1 на `:5432` и `:6379`.
2. **Склонируйте репозиторий** в `/opt/dwtgbot` на обоих серверах.
3. **Запустите TUI installer** на каждом сервере:
   ```bash
   cd /opt/dwtgbot
   sudo bash deploy/scripts/install.sh
   ```
   Через меню установите Docker → запишите `deploy/nl1/.env`
   (или `deploy/nl2/.env`) → настройте `ufw` → поднимите stack.
4. **Только NL-2**: получите SSL-сертификаты:
   ```bash
   sudo bash deploy/scripts/certbot_init.sh
   ```
5. **Проверьте состояние** через `bash deploy/scripts/healthcheck.sh nl1`
   (и `nl2`).

### Auto-deploy через GitHub Actions

Задайте repo variable `DEPLOY_TOPOLOGY` (`single` или `split`, по умолчанию
`split`) и secrets для каждого environment (`single`, либо `nl1` и `nl2`):

| Secret | Описание |
|---|---|
| `SINGLE_HOST` / `NL1_HOST` / `NL2_HOST` | Публичный IP / DNS |
| `SINGLE_SSH_USER` / `NL1_SSH_USER` / `NL2_SSH_USER` | SSH-пользователь с `sudo` |
| `SINGLE_SSH_KEY` / `NL1_SSH_KEY` / `NL2_SSH_KEY` | Приватный ключ (PEM) |
| `SINGLE_SSH_PORT` / `NL1_SSH_PORT` / `NL2_SSH_PORT` | Опционально, по умолчанию 22 |
| `SINGLE_REPO_PATH` / `NL1_REPO_PATH` / `NL2_REPO_PATH` | Где склонирован репозиторий (например, `/opt/dwtgbot`) |

Затем запустите **Actions → Deploy → Run workflow** с `target=single` (или
`both` для split) и нужным ref. Workflow выполнит
`deploy/scripts/deploy_update.sh` по SSH на каждом хосте.

---

## Справочник конфигурации

Все настройки берутся из переменных окружения и валидируются через
`pydantic-settings` при старте. Процесс сразу завершается, если значение
невалидно или нарушены ограничения для production.

| Переменная | Значение по умолчанию | Примечания |
|---|---|---|
| `APP_ROLE` | `all` | `bot` \| `api` \| `worker` \| `all` (только dev) |
| `APP_ENV` | `development` | `production` требует HTTPS + internal token |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `LOG_JSON` | `false` | В production используйте `true` для структурированных логов |
| `BOT_TOKEN` | — | **Обязателен**, выдаётся @BotFather |
| `BOT_ADMIN_IDS` | empty | TG user IDs через запятую |
| `TELEGRAM_MAX_UPLOAD_MB` | `49` | Файлы больше этого лимита идут через temp links |
| `POSTGRES_*` | — | Переопределяется через `DATABASE_URL`, если задан |
| `DATABASE_URL` | derived | `postgresql+asyncpg://…` |
| `REDIS_*` | — | Переопределяется через `REDIS_URL`, если задан |
| `STORAGE_PATH` | `/var/lib/dwtgbot/storage` | Финальные артефакты |
| `STORAGE_TMP_PATH` | `/var/lib/dwtgbot/tmp` | Рабочие scratch-директории |
| `MAX_FILE_SIZE_MB` | `2048` | Жёсткий верхний предел одного скачивания |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | **В prod обязан быть HTTPS** |
| `TEMP_LINK_TTL_SECONDS` | `86400` | Срок жизни ссылки |
| `TEMP_LINK_MAX_DOWNLOADS` | `5` | Лимит использований на token |
| `TEMP_LINK_TOKEN_BYTES` | `32` | URL-safe entropy |
| `API_INTERNAL_TOKEN` | — | **Обязателен в prod** |
| `WORKER_CONCURRENCY` | `2` | Параллельные скачивания на worker |
| `JOB_TIMEOUT_SECONDS` | `1800` | Жёсткий timeout задачи `arq` |
| `JOB_MAX_RETRIES` | `2` | Retry budget для `arq` |
| `DOWNLOAD_TIMEOUT_SECONDS` | `900` | Жёсткий timeout `yt-dlp` |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Период cleanup loop |
| `MEDIA_CACHE_TTL_SECONDS` | `21600` | Свежесть строки `media_cache` |
| `BACKUP_DIR` | `/var/backups/dwtgbot` | Куда пишет `pg_dump` |
| `BACKUP_RETENTION_DAYS` | `14` | Старые dumps удаляются |
| `FFMPEG_BIN` / `YTDLP_BIN` | `ffmpeg` / `yt-dlp` | Override для нестандартных путей |

Полные шаблоны см. в [`.env.example`](.env.example),
[`deploy/single/.env.example`](deploy/single/.env.example),
[`deploy/nl1/.env.example`](deploy/nl1/.env.example) и
[`deploy/nl2/.env.example`](deploy/nl2/.env.example).

---

## Операции

Все операционные скрипты лежат в `deploy/scripts/`. Они используют общий
`helpers.sh` (strict mode, цветные логи в `/var/log/dwtgbot.log`, `ERR`-trap,
интерактивные подтверждения, wrapper `compose_stack single|nl1|nl2`).

| Скрипт | Назначение |
|---|---|
| `install.sh` | TUI-меню на whiptail (установка Docker, подготовка single/NL-1/NL-2, настройка env, firewall, start/stop/logs, backup/restore, deploy, certbot, cleanup, healthcheck) |
| `deploy_update.sh single\|nl1\|nl2` | `git pull` → config check → опциональный pre-backup → pull → `up -d` → migrate (single / NL-1) → healthcheck |
| `backup.sh` | `pg_dump` (в контейнере или через host exec), gzip, retention prune |
| `restore.sh` | Интерактивный выбор dump, drop/recreate DB, загрузка dump, restart зависимых сервисов (в single worker останавливается локально) |
| `firewall_setup.sh single\|nl1\|nl2` | Правила `ufw`; NL-1 требует `PRIVATE_NET` для Postgres/Redis, в single 5432/6379 закрыты |
| `cleanup.sh` | Удаляет старые `STORAGE_TMP_PATH/*`, запускает one-shot pass `cleanup_worker` |
| `certbot_init.sh` | Bootstrap Let's Encrypt certificate на хосте media plane (single или NL-2) |
| `cookies_setup.sh instagram\|youtube` | Установка cookies провайдера (файл / вставка / `sessionid` вручную) в `/srv/dwtgbot/secrets`, путь в `.env`, пересоздание bot/worker |
| `healthcheck.sh single\|nl1\|nl2` | Статусы сервисов + HTTP probes |

Задайте `ASSUME_YES=1`, чтобы отключить интерактивные prompts в automation.

### Метрики и SLO

Когда `METRICS_ENABLED=true`, каждый из трёх процессов (bot, worker, API)
поднимает свой in-process Prometheus exporter на
`http://${METRICS_BIND_HOST}:${METRICS_PORT}/metrics`. Биндинг должен быть
только на приватный interface — `validate_runtime` блокирует `0.0.0.0` в
production. Каталог SLO и живой inventory метрик описаны в
[`docs/35-metrics-and-slo.md`](docs/35-metrics-and-slo.md); архитектурная форма
трёхпроцессного exporter'а зафиксирована в
[`docs/adr/0007-job-queue-worker-metrics.md`](docs/adr/0007-job-queue-worker-metrics.md).

---

## Разработка

```bash
make dev-install      # .venv + dev deps
make fmt              # ruff format (black-compatible) + ruff --fix
make lint             # ruff check
make typecheck        # mypy
make test             # pytest
make precommit-install
```

> Formatter — **`ruff format`** (байт-совместимая реализация `black`, примерно
> в 30 раз быстрее). `black` напрямую не используется, чтобы не держать два
> formatter'а в одном репозитории.

Локальный запуск отдельных процессов (Postgres + Redis берутся из NL-1 compose stack):

```bash
make migrate
make bot       # python -m app.main_bot
make api       # python -m app.main_api
make worker    # python -m app.main_worker
```

---

## Тестирование и CI

- Unit tests находятся в `app/tests/`. Они используют fakes для repos/queues;
  реальная сеть/DB/Redis не затрагиваются.
- Integration tests должны быть помечены `@pytest.mark.integration`. По
  умолчанию они исключены (`pytest -m "not integration"`).
- `.github/workflows/ci.yml` запускает ruff format + check, mypy, pytest и
  shellcheck на каждом PR.
- `.github/workflows/build-images.yml` собирает и публикует образы
  `bot`/`api`/`worker`/`backup` в GHCR на `main` и tags.
- `.github/workflows/deploy.yml` запускается после сборки образов или вручную;
  подключается по SSH к single-хосту или к NL-1/NL-2 (по `DEPLOY_TOPOLOGY`) и
  выполняет `deploy_update.sh`.
- Задача `compose-validate` проверяет `docker compose config` для всех трёх
  стеков, а `app/tests/test_deploy_topology.py` проверяет инварианты топологий.

---

## Структура проекта

```
app/
  bot/                   handlers, keyboards, callbacks для python-telegram-bot
  api/                   FastAPI: internal health + public /d/{token}
  application/           Use cases, service protocols, DTOs
  domain/                Pure entities, enums, repository interfaces
  infrastructure/        DB, cache, queue, providers, downloader, storage, telegram
  workers/               cleanup_worker, backup_worker
  composition.py         Composition root (DI wiring)
  config.py              pydantic-settings
  logging_config.py      structlog setup
  exceptions.py          AppError hierarchy
  main_bot.py            Bot entrypoint
  main_api.py            API entrypoint
  main_worker.py         arq worker entrypoint
  tests/                 Unit tests
deploy/
  compose/control.yml         Фрагмент control plane (postgres, redis, migrate, bot, backup)
  compose/media.yml           Фрагмент media plane (api, worker, cleanup, nginx, certbot)
  single/docker-compose.yml   Один хост: оба фрагмента + single.override.yml
  nl1/docker-compose.yml      Split, control plane (+ nl1.overlay.yml)
  nl2/docker-compose.yml      Split, media plane
  nginx/                      Nginx config + snippets + media.conf.template
  certbot/init-letsencrypt.sh
  scripts/                    Operational bash scripts
  templates/env.template      Generic env template used by TUI installer
docker/
  bot.Dockerfile  api.Dockerfile  worker.Dockerfile  backup.Dockerfile
docs/
  DEPLOY.md  ARCHITECTURE.md  TROUBLESHOOTING.md
migrations/
  env.py  script.py.mako  versions/
.github/workflows/
  ci.yml  build-images.yml  deploy.yml
```

---

## Устранение неполадок

Операционные runbook'и для 20 самых частых инцидентов находятся в
[`docs/24-runbooks.md`](docs/24-runbooks.md); подробная диагностика
(анализ логов, классификация ошибок, таблицы symptom→cause) — в
[`docs/31-troubleshooting.md`](docs/31-troubleshooting.md). Несколько быстрых
подсказок:

- **"Sorry, I can't process this link"** → проверьте worker logs:
  `compose_stack single logs -f worker` (или `compose_nl2 …`). Обычно не удалось получить metadata через
  `yt-dlp` (private/region-locked content).
- **Файлы не доставляются, jobs stuck `pending`** → проверьте Redis connectivity
  с NL-2. Убедитесь, что `REDIS_HOST`/`REDIS_URL` и приватная сеть настроены
  правильно.
- **Temp link возвращает 404** → файл уже был очищен, ссылка истекла или
  `STORAGE_PATH` не совпадает между worker и Nginx volumes.
- **Certbot падает при первом запуске** → убедитесь, что DNS A-record указывает
  на NL-2, а `:80` открыт в `ufw` и на уровне cloud provider.

---

## Архитектурные решения (зафиксированные)

Следующие решения приняты намеренно и зафиксированы. Не меняйте их в PR без
явного RFC-обсуждения.

| # | Область | Решение |
|---|---|---|
| 1 | Язык | Python 3.14+ |
| 2 | Bot framework | `python-telegram-bot` |
| 3 | Очередь | Redis (`arq`) |
| 4 | База данных | PostgreSQL + SQLAlchemy 2.x + Alembic |
| 5 | Download engine | `yt-dlp` |
| 6 | Media processing | `ffmpeg` |
| 7 | Delivery | small files → Telegram; big files → temporary HTTPS link |
| 8 | Infrastructure | `single` (всё на одном хосте) или `split` (NL-1: bot + redis + postgres; NL-2: worker + nginx + certbot + cleanup), см. ADR-0011 |
| 9 | Deployment | Docker Compose |
| 10 | CI/CD | GitHub Actions |
| 11 | OS target | Ubuntu 24.04 LTS |
| 12 | TUI installer | bash (`deploy/scripts/install.sh`) |
| 13 | Logging | JSON structured logs (`structlog`) |
| 14 | Lint / format / types | `ruff check` + `ruff format` (black-compatible) + `mypy` |
| 15 | Tests | `pytest` |

## Лицензия

MIT — см. [`LICENSE`](LICENSE).

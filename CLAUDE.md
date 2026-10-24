# DWTGBot — контекст для Claude

Telegram-бот: принимает ссылки YouTube / Instagram, скачивает медиа через
`yt-dlp` + `ffmpeg` и отдаёт файл в Telegram (маленькие) или временной
HTTPS-ссылкой через nginx `X-Accel-Redirect` (большие). Python 3.14,
`python-telegram-bot`, `arq` + Redis, PostgreSQL + SQLAlchemy 2 + Alembic,
FastAPI, `structlog`.

**Прод — один сервер, топология `single`** (`deploy/single`, ADR-0011).
Топология `split` (NL-1 + NL-2) описана и поддерживается, но не развёрнута.

## Где что лежит

| Нужно | Где |
|---|---|
| Индекс документации | `docs/README.md` |
| Зафиксированные решения (менять только новым ADR) | `docs/adr/0005-locked-architectural-assumptions.md` §2, индекс ADR — `docs/adr/README.md` |
| Как думать и вести изменения (принципы P1–P11) | `docs/25-agent-guide.md` |
| Правила по областям кода (грузятся по путям) | `.claude/rules/*.md` |
| Процедуры | `.claude/skills/`: `plan-change`, `finish-change`, `add-config-variable`, `db-migration`, `server-ops` |
| Структура пакетов и матрица зависимостей | `docs/03-project-structure.md` |
| Все env-переменные | `docs/13-config-and-env.md` |

Перед правкой прочитайте тематический документ области (`docs/README.md`
→ «Quick lookup by task») и сам код с тестами.

## Зафиксированные решения (кратко, полный реестр — ADR-0005)

1. Python 3.14+ (ADR-0009).
2. Топология `single` | `split` (ADR-0011); сервисы описаны только во
   фрагментах `deploy/compose/control.yml` и `media.yml`, стеки их `include`.
3. Провайдеры: один класс на платформу (`BaseProvider`).
4. Очередь: Redis + `arq`, `_job_id = job:<db_pk>` (идемпотентность).
5. PostgreSQL — источник истины; миграции Alembic только вперёд.
6. Большие файлы — токен-ссылки через nginx `X-Accel-Redirect` (ADR-0004).
7. Логи — `structlog`, JSON, корреляция через contextvars.
8. Docker Compose v2 (≥ 2.24), никаких Python-сервисов на хосте.
9. CI/CD — GitHub Actions: CI → сборка образов в GHCR → деплой по SSH
   (сам после сборки на `main`, плюс ручной запуск).
10. Установщик — `deploy/scripts/install.sh` (меню + неинтерактивный режим).

## Принципы P1–P11 (одной строкой; смысл — `docs/25-agent-guide.md` §1.A)

| | Правило |
|---|---|
| P1 | Минимальный дифф под цель. Никаких попутных правок. |
| P2 | Сначала архитектура: слой и затронутые ADR решены до кода. |
| P3 | Один PR — одна задача; переименования и рефакторинг — отдельно. |
| P4 | Код, тесты, документация и конфиг меняются в одном PR. |
| P5 | `domain` и `application` не импортируют инфраструктуру; репозитории возвращают сущности, не ORM. |
| P6 | Обработчик = разбор → один use case → ответ. Драйверы только через порты. |
| P7 | `_job_id` детерминирован, `max_tries` конечен, побочные эффекты идемпотентны, воркеры без состояния. |
| P8 | Публичны только методы `BaseProvider`; всё про yt-dlp — внутри провайдера. |
| P9 | Скрипты деплоя идемпотентны, разрушительное — через `confirm`. |
| P10 | Схема БД меняется только миграцией с порядком деплоя; выпущенные миграции не редактируются. |
| P11 | Нельзя ослаблять безопасность: TLS/HSTS, `internal` в nginx, `ensure_within`, `sanitize_filename`, секреты только через `Settings`, никаких URL и секретов в логах. |

Тесты держат часть правил автоматически: `test_layering.py` (импорты слоёв
и драйверов), `test_env_access.py` (`os.environ` только в `app/config.py`),
`test_no_urls_in_logs.py`, `test_env_examples_sync.py`,
`test_deploy_topology.py`, `test_docs_references.py` (пути и события в
документации).

## Команды

```bash
ruff format app && ruff check app && mypy app
pytest                                   # coverage gate 70 %; integration пропускаются без env
DWTGBOT_TEST_POSTGRES_URL=postgresql+asyncpg://… DWTGBOT_TEST_REDIS_URL=redis://…/15 pytest app/tests/integration --no-cov
shellcheck --severity=warning deploy/scripts/*.sh
alembic check                            # модели совпадают с миграциями
```

## Соглашения о работе

- **Документация и CHANGELOG — на русском.** Идентификаторы, команды,
  пути, env, имена событий не переводятся.
- **CHANGELOG:** запись в начало `[Unreleased]`. Если нужна работа на
  сервере (env, миграция, файлы, секреты) — блок **Действия оператора**.
- **PR:** по шаблону `.github/pull_request_template.md` (Зачем / Изменения /
  Проверка / Действия оператора). Подробно — skill `finish-change`.
- **Мерж — только по явной просьбе владельца репозитория.** Готовый
  зелёный PR ждёт его команды.
- **Сервер — только через GitHub Actions** («Server diagnostics»,
  «Server maintenance», деплой), без SSH. Репозиторий публичный: логи
  Actions видны всем, в них не должно быть секретов, URL пользователей и
  внутренних адресов. Подробно — skill `server-ops`.

## Никогда

- `os.environ` вне `app/config.py`; `subprocess` с `shell=True`; `print`;
  `time.sleep` в async-коде; голый `except` / `except Exception: pass`.
- Новые пакеты верхнего уровня в `app/` (`services/`, `helpers/`, `core/`)
  и новые зависимости без согласия владельца.
- Правка выпущенной миграции; удаление колонки за один шаг.
- Публичные порты кроме 80/443 nginx; снятие `internal;` с локации файлов;
  отдача файлов через `FileResponse`.
- `pytest.mark.skip`, чтобы позеленить CI; `--no-verify`.
- Изменение строки ADR-0005 §2 без нового ADR, который её заменяет.

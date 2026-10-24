# Аудит документации, правил и скилов агентов — 2026-10

Status: Evolving

Источник: проверка 2026-10-24 на `main` (`ebab9bf`). Проверены 62
документа в `docs/`, корневые `README.md` / `CONTRIBUTING.md` / `CHANGELOG.md`,
`.cursor/rules/*.mdc`. Файлы не менялись. Скрипты проверки сверяли
документы с кодом:
- ссылки и якоря markdown;
- пути в обратных кавычках;
- имена событий в запросах к логам;
- env-переменные против `Settings`;
- цели `make`;
- утверждения правил против `pyproject.toml`, workflow и Dockerfile.

**Вывод:** оптимизировать нужно, но точечно. Ссылки в порядке, справочник
конфигурации тоже. Устарели три вещи: команды runbooks, часть правил
агентов и объём «процессных» документов.

---

## 1. Что в порядке

| Проверка | Результат |
|---|---|
| Markdown-ссылки и якоря | 0 битых якорей; одна битая ссылка — заготовка `<NN-newdoc>.md` в блоке кода `docs/30` |
| `docs/13-config-and-env.md` против `Settings` | 94 из 94 полей описаны; лишние строки — переменные скриптов и compose, так и задумано |
| ADR | процесс работает, индекс `docs/adr/README.md` полон до ADR-0014 |
| Скилы | в репозитории не было (`.claude/skills` отсутствовал) |

## 2. Что было неверно

### 2.1 Runbooks и troubleshooting искали несуществующие события (критично)

`docs/24`, `docs/31` и ещё 7 документов фильтровали логи по событиям,
которых нет в коде. Во время инцидента такие запросы молча возвращали
пустоту.

| В документах | В коде |
|---|---|
| `worker_job_done` (+ поле `duration_ms`) | `job_done` (`total_seconds`, `method`, `size`) |
| `worker_job_started` | `job_status_changed` (`to=processing`) |
| `bot_url_submitted` | `update_received` (с `request_id`, `user_id`) |
| `cleanup_done` / `cleanup_failed` | `temp_link_files_removed` / `cleanup_cycle_error` |
| `worker_delivery_failed`, `bot_send_failed` | `telegram_upload_retries_exhausted`, `bot_error` |
| `temp_link_not_found` / `_expired` | `temp_link_served` с `result` |
| `job_created` / `job_completed` / `queue_depth` (LogQL) | `job_enqueued` / `job_done` / `queue_depth_sample` |
| `job_failed_app_error`, `rate_limit_lua_error` | `job_failed_permanent`, `rate_limit_lua_parse_error` |
| `url_host`, `url_path_hash`, `job_id == "j_xxx"` | `platform` / `host`; `job_id` — целое |

Ещё одна ошибка в тех же командах: `docker compose logs … | jq` без
`--no-log-prefix`. Compose начинает каждую строку с `service |`, и `jq`
не может её разобрать. Цепочки событий для `request_id` / `job_id`
(`docs/31` §3.4–3.5) описывали поток, которого в коде нет. Документы
написаны под split (181 упоминание NL-1/NL-2 в `docs/24`), а прод работает
на `single`, логи хранятся в journald.

### 2.2 Правила агентов расходились с кодом

| Правило | Было | На самом деле |
|---|---|---|
| `00`, ADR-0005 строка 11, README | «manual SSH deploy» | `deploy.yml` запускается сам после `build-images.yml` (`workflow_run`), ручной запуск тоже есть |
| `60` | `latest` только на `main` | `latest` — только на тегах `v*`; на `main` — тег `main` |
| `10` | aiohttp «already in deps» | его нет в зависимостях; HTTP — `httpx` |
| `70` | integration исключаются через `-m "not integration"` по умолчанию | без env они пропускаются; `asyncio_mode` задан глобально |
| `30` | «Never log» без URL; в примерах `error=str(exc)` на WARNING | URL запрещены на любом уровне (P11, R4) |

### 2.3 Несуществующие файлы и данные

- **Устаревшие пути в документах.** Примеры: `app/bot/handlers/url.py`,
  `app/workers/tasks/download.py`, `app/workers/cleanup/runner.py`,
  `app/utils/paths.py`, `docker/bot.entrypoint.sh`,
  `docs/34-error-taxonomy.md`, `app/tools/*.py`, тесты `app/tests/unit/…`
  в ADR-0010. Затронуты 01/03/10/12/13/23/24/31/37 и ADR-0010.
- **Нет таких целей `make`:** `make setup` и `make ci`.
- **Устаревший индекс ADR.** В `docs/README.md` не было ADR-0007…0010 и
  0012…0014, а у ADR-0010 стоял статус «Proposed», хотя он реализован.

### 2.4 Объём и дублирование

- **Документации почти втрое больше кода:** около 37 тыс. строк против
  13.5 тыс.
- **Процессные документы для агентов** (`25`–`30` + `.cursor/rules`)
  занимают 13.1 тыс. строк, столько же, сколько весь код. Перед любой
  правкой обязательно прочитать 2.9 тыс. строк.
- **`25-agent-guide` и `26-cursor-rules` сознательно дублировали друг
  друга,** с картой дублирования: любое изменение приходилось вносить в
  два места.
- **Плейбуки `28`/`29`/`30`** — около 9 тыс. строк. В основном это
  гипотетические сценарии (TikTok, zip) и пути из раннего дизайна
  (`runners/yt_dlp.py`, `application_factory.py`, `workers/settings.py`).
- **Claude Code не читает `.cursor/rules`, а `CLAUDE.md` в репозитории не
  было.** Поэтому рабочие соглашения нигде не были записаны для агентов:
  - CHANGELOG на русском с «Действиями оператора»;
  - шаблон PR;
  - мерж только по просьбе владельца;
  - работа с сервером через Actions.

### 2.5 Замечено попутно (код, не документация)

`error=str(exc)` в WARNING/ERROR-логах (`app/bot/handlers/links.py`,
`app/application/use_cases/process_download.py`) может унести URL в
journald: сообщения yt-dlp вида `Unsupported URL: <url>` и
`No suitable extractor found for URL <url>` содержат его целиком. Тест
`test_no_urls_in_logs.py` ловит только ключи `url=` и `source_url=`.
Чинить в отдельном PR: логировать `error_class`, а текст ошибки пропускать
через обрезку URL (`app/utils/url.py`).

---

## 3. План и статус

| Шаг | Что | Статус |
|---|---|---|
| 1 | Исправить неверное: события, поля и команды в 24/31/20/21/35/37/09/14/36/23, заметка про journald и `single`, пути, `make`, ADR-0005 строка 11, README, индекс ADR, статус ADR-0010, предупреждение в плейбуках 28–30 | ✅ этот PR |
| 2 | Перенести правила Cursor и `docs/26` в Claude: `CLAUDE.md`, `.claude/rules/*.md` (по путям), `.claude/skills/` (`plan-change`, `finish-change`, `add-config-variable`, `db-migration`, `server-ops`); удалить `.cursor/` и `docs/26-cursor-rules.md` | ✅ этот PR |
| 3 | Тест `app/tests/test_docs_references.py`: пути в обратных кавычках существуют, события в запросах к логам есть в коде, ссылки в `CLAUDE.md` и `.claude/` живые. На `main` до шага 1 находит около 50 расхождений | ✅ этот PR |
| 4 | Сократить: из `28`–`30` оставить по одному рабочему рецепту на тип задачи, убрать гипотетические сценарии; затем снять их исключение в тесте шага 3 | ⏳ решение владельца |
| — | Код: URL в `error=str(exc)` (§2.5) | ⏳ отдельный PR |

## 4. Куда что переехало из `docs/26-cursor-rules.md`

| Было в `26` | Стало |
|---|---|
| §1.A P1–P11, §20 golden rules | `CLAUDE.md` (одна строка на принцип); полный смысл — `docs/25-agent-guide.md` §1.A |
| §2 стартовый протокол, §3 / §3.7 план, §3.8 красные флаги, §17.4 матрица, §17.8 самокоррекция | skill `plan-change` |
| §17.9 скрипты отказа A–G | `.claude/skills/plan-change/refusals.md` |
| §8 / §18 / §19 чек-листы, PR | skill `finish-change` |
| §9 миграции | skill `db-migration` |
| §10.5 env-переменные | skill `add-config-variable` |
| §5 слои, §10 compose / nginx / certbot, §11 провайдеры, §13 логи, §14 безопасность | `.claude/rules/` (`architecture`, `docker-and-deploy`, `providers`, `errors-and-logging`) и `CLAUDE.md` «Никогда» |
| §16 / §16A анти-паттерны и ошибки агентов | `docs/25-agent-guide.md` §17–18 (там они уже были) |

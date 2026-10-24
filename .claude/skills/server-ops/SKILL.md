---
name: server-ops
description: Inspect or operate the DWTGBot production server without SSH — follow a deploy, run the "Server diagnostics" and "Server maintenance" workflows, read redacted logs, free disk space. Use after a merge, when the user reports a production problem, or when the server needs a maintenance action.
---

# Прод без SSH

Прод — один хост, топология `single`. Всё делается через GitHub Actions.
**Репозиторий публичный: логи Actions видны всем.** Печатать можно только
обезличенное: никаких секретов, токенов, URL пользователей, внутренних
адресов и путей с секретами.

## После мержа

Цепочка на коммит мержа: `ci.yml` → `build-images.yml` → `deploy.yml`
(`workflow_run`). Дождаться успеха всех трёх, затем запустить диагностику и
проверить: контейнеры на `sha-<short>` нового коммита, рестартов 0,
`healthcheck.sh` проходит, в сводке логов нет новых ошибок. `deploy.yml`
при падении healthcheck сам откатывает на last-good — это видно в его логе.

## Server diagnostics (`.github/workflows/diagnostics.yml`)

Только чтение. Входы: `target` (`single` | `nl1` | `nl2`), `since`
(окно логов, `30m` / `2h` / `1d`), `last` (сколько последних ошибок).
Печатает: хост (память, диск, `docker system df`), journald, таймер
автодеплоя, текущий коммит, контейнеры (образ, health, рестарты, OOM),
`healthcheck.sh` и обезличенную сводку логов (`log_digest.py`).
Подробно — `docs/24-runbooks.md` §27.

Лог job удобнее читать через API job (`get_job_logs` по `job_id`):
`gh run view --log` в этом окружении часто пустой.

## Server maintenance (`.github/workflows/maintenance.yml`)

Входы: `target`, `action`:

| action | Что делает |
|---|---|
| `journald-setup` | лимиты journald (30 дней, 2G, держать 4G свободными), `deploy/scripts/journald_setup.sh` |
| `docker-prune` | удаляет образы без контейнеров и не из `last-good-*.env`, весь build cache (`deploy/scripts/docker_prune.sh`) |

Новое действие = скрипт в `deploy/scripts/` (идемпотентный, обезличенный
вывод) + ветка `case` и `options` в `maintenance.yml` + строка в
`docs/24-runbooks.md` §27.1. Ветку можно проверить запуском workflow с
`ref` этой ветки, пока файл workflow уже есть в `main`.

## Логи на сервере (если владелец сам заходит по SSH)

```bash
sudo journalctl -o cat CONTAINER_NAME=dwtgbot_worker --since "-2h" | jq -c 'select(.level=="error")'
docker logs --since 2h dwtgbot_worker 2>&1 | jq -c 'select(.level=="error")'
```

## Не делать

- Не править `.env` и файлы на сервере в обход репозитория.
- Не запускать разрушительное (`down -v`, удаление томов, restore) без
  явной просьбы владельца.
- Не печатать в логи Actions содержимое `.env`, секреты, сырые логи
  пользователей — только через `log_digest.py` / `redact`.

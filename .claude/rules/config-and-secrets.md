---
paths:
  - "app/config.py"
  - "app/config_groups.py"
  - "**/.env.example"
  - "deploy/**"
  - "docker/**"
---

# Конфигурация и секреты

Подробно: `docs/13-config-and-env.md`, `docs/17-security.md`.
Новая переменная — skill `add-config-variable`.

- Единственный источник — `Settings` (`app/config.py`, поля по группам в
  `app/config_groups.py`), доступ через `get_settings()` (кэшируется; в
  тестах сбрасывает autouse-фикстура `app/tests/conftest.py`).
- `os.environ` вне `app/config.py` запрещён; `Settings` не меняют на лету.
- Реальный `.env` не коммитят; в примерах — заглушки. Секретов нет в
  compose-файлах (`env_file:`), в коде, в документации и в логах.
- Прод: `PUBLIC_BASE_URL` только HTTPS, `API_INTERNAL_TOKEN` обязателен
  (`validate_runtime`).
- `single`: один `deploy/single/.env`, `POSTGRES_HOST=postgres`,
  `REDIS_HOST=redis`; отличия worker (`WORKER_DB_*`) — в
  `deploy/single/single.override.yml`.
- `split`: у NL-1 и NL-2 свои `.env`; совпадать должны `BOT_TOKEN`,
  `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `API_INTERNAL_TOKEN`; на NL-2
  хосты БД и Redis — приватный IP NL-1.
- Переменные обвязки compose (`IMAGE_*`, `LIMIT_*`, `WORKER_CPU_SHARES`,
  `WORKER_DB_*`, `NL1_PRIVATE_IP`, `SERVER_NAME`, `REDIS_MAXMEMORY`) и
  переменные скриптов (`BACKUP_S3_*`, `BACKUP_RCLONE_REMOTE`) в `Settings`
  не добавляют.
- Генерация секретов: `openssl rand -hex 32`; `install.sh` делает это сам.

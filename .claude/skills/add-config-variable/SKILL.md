---
name: add-config-variable
description: Add or change a DWTGBot environment variable (a Settings field) end to end — config group, all four .env examples, docs/13, validation and tests. Use whenever code needs a new setting or an existing one changes meaning, default or bounds.
---

# Новая переменная окружения

Перед этим: это поле `Settings` или переменная обвязки compose / скрипта?
`IMAGE_*`, `LIMIT_*`, `WORKER_CPU_SHARES`, `WORKER_DB_*`, `NL1_PRIVATE_IP`,
`SERVER_NAME`, `REDIS_MAXMEMORY`, `BACKUP_S3_*` интерполирует compose или
читает скрипт — их в `Settings` не добавляют.

## Шаги

1. **Поле** в подходящей группе `app/config_groups.py` с типом и границами
   `Field(default, ge=…, le=…)`; комментарий — кто читает поле. Имена env
   плоские, `Settings` наследует все группы.
2. **Обязательна в проде?** — проверка в `validate_runtime` (`app/config.py`).
3. **Примеры — все четыре:** `.env.example`, `deploy/single/.env.example`,
   `deploy/nl1/.env.example`, `deploy/nl2/.env.example`. Если хватает
   значения по умолчанию — закомментированной строкой `# NAME=default`.
4. **Документация:** строка в таблице `docs/13-config-and-env.md` (тип,
   границы, умолчание, кто читает).
5. **Передача в контейнер:** сервисы получают `.env` через `env_file`;
   нужен только сервису-исключению — `environment:` во фрагменте
   `deploy/compose/*.yml` или в `single.override.yml`.
6. **Тесты:** граница / валидация — в `app/tests/test_config.py` или
   `test_config_groups.py`. `app/tests/test_env_examples_sync.py` сам
   проверит, что поле есть в `docs/13` и во всех примерах.
7. **CHANGELOG:** если значение нужно задать на сервере — блок
   **Действия оператора** (какой стек, до или после деплоя).

Читать переменную — только `get_settings().NAME`; `os.environ` вне
`app/config.py` запрещён (`app/tests/test_env_access.py`). Секрет —
никогда в коде, примерах или логах.

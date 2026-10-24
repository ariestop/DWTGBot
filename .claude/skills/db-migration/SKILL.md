---
name: db-migration
description: Change the DWTGBot PostgreSQL schema safely — write and hand-review an Alembic migration, keep models and repositories in sync, plan the deploy order, and test it. Use for any change to tables, columns, indexes, constraints, enums or server defaults.
---

# Миграция БД

Миграция нужна для любого изменения схемы: таблицы, колонки, индексы,
ограничения, enum, типы, server defaults. Изменения только ORM-кода
(`__repr__`) — без миграции.

## Шаги

1. Изменить модель в `app/infrastructure/db/models.py`.
2. `alembic revision --autogenerate -m "<коротко, повелительно>"` и
   **прочитать файл руками**: autogenerate пропускает добавление значений
   enum (`op.execute("ALTER TYPE … ADD VALUE …")`), видит переименование как
   drop + create, не делает backfill.
3. `downgrade()` написан или явно объяснено, почему он пустой.
4. `alembic check` — модели совпадают с миграциями (`migrations/env.py`
   импортирует `app.infrastructure.db.models`, иначе сравнение пустое).
5. Обновить репозиторий (`app/infrastructure/db/repositories/*_impl.py`),
   доменную сущность и use cases, которые читают / пишут поле; все
   `match` по enum.
6. Тесты: integration в `app/tests/integration/test_repositories_postgres.py`
   (схема для каждого теста собирается из моделей). Проверить на чистой БД:
   `alembic upgrade head`, затем `alembic downgrade -1 && alembic upgrade head`.
7. `docs/12-db-schema.md`.

## Безопасность изменений

- Не удалять колонку в том же релизе, где перестали в неё писать:
  сначала перестать писать → следующий релиз → удалить.
- Не переименовывать колонку: добавить → писать в обе → backfill →
  удалить старую, за несколько релизов.
- NOT NULL к заполненной таблице — сначала nullable + default, backfill,
  потом ограничение.
- Выпущенную миграцию не редактировать — только новая миграция.

## Порядок деплоя (в описании PR)

Миграции применяет сервис `migrate` до старта `bot` / api / worker / cleanup.
Значит, миграция должна работать **и со старым кодом** (откат образа на
last-good не откатывает схему). Несовместимая со старым кодом миграция —
высокий риск: только с согласия владельца.

---
name: finish-change
description: Finish a DWTGBot change before declaring it done — run the checks, review the diff, update tests/docs/CHANGELOG, write the commit and the PR by the repository template. Use before every commit that is meant to be pushed and before opening or updating a PR.
---

# Завершение изменения

## 1. Проверки (всё зелёное — иначе не готово)

```bash
ruff format app && ruff check app && mypy app
pytest                                  # coverage gate 70 %
# integration, если тронуты БД / Redis / репозитории:
DWTGBOT_TEST_POSTGRES_URL=… DWTGBOT_TEST_REDIS_URL=… pytest app/tests/integration --no-cov
shellcheck --severity=warning deploy/scripts/*.sh   # если тронут bash
alembic check                                       # если тронуты модели
```

Исправление CI: сначала воспроизвести падение локально, потом показать,
что та же проверка проходит.

## 2. Дифф глазами постороннего

- Только файлы из плана; нет попутных правок, переформатирования чужих
  файлов, закомментированного кода, отладочных `print`, TODO без владельца.
- Нет `os.environ` вне `app/config.py`, `shell=True`, `time.sleep` в async,
  голого `except`, секретов и URL пользователей в логах.
- Новый тест-страж падает на старом коде (проверьте и скажите об этом).
- Что CI может отвергнуть? Исправьте до пуша.

## 3. Сопутствующее (P4)

| Изменилось | Обновить |
|---|---|
| Поведение области | её документ в `docs/` (таблица в `docs/README.md`) |
| Env-переменная | skill `add-config-variable` |
| Схема БД | skill `db-migration`, `docs/12-db-schema.md` |
| Новое событие лога | каталог `docs/14-logging-observability.md` §4 |
| Новый инцидент / команда оператора | `docs/24-runbooks.md` |
| Архитектурное решение | ADR (`docs/adr/`), индекс `docs/adr/README.md` |

**CHANGELOG** — запись в начало `## [Unreleased]`, на русском:
`### Added|Changed|Fixed — <что> (<пункт аудита / ADR>)`, абзац «зачем и
что изменилось». Если на сервере нужна работа (env, миграция, файлы,
секреты) — в конце блок:

```markdown
**Действия оператора** (single / nl1 / nl2; до или после деплоя):
- <что сделать>
```

## 4. Коммит

Повелительное наклонение, `тип(область): что` (`fix(worker): …`,
`docs: …`), в теле — почему. Один логический шаг на коммит; переименование
без изменения поведения — отдельным коммитом.

## 5. PR

Тело по `.github/pull_request_template.md`:

- **Зачем** — проблема и откуда она (аудит, инцидент, просьба).
- **Изменения** — по пунктам, с путями.
- **Проверка** — какие команды прогнаны, сколько тестов, что проверено
  вручную; честно — что не проверено.
- **Действия оператора** — «Не нужны» или конкретные шаги.

После открытия: следить за CI до зелёного, отвечать на ревью. **Мержить
только по явной просьбе владельца.** После мержа — дождаться деплоя и
проверить прод (skill `server-ops`).

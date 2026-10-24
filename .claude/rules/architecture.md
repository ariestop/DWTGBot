---
paths:
  - "app/**/*.py"
---

# Слои (hexagonal-lite)

Подробно: `docs/02-architecture.md`, `docs/03-project-structure.md`.

| Слой | Путь | Что внутри |
|---|---|---|
| Domain | `app/domain/` | Сущности, enum, интерфейсы репозиториев. Только stdlib, без IO. |
| Application | `app/application/` | Use cases, сервисы, **порты** (`application/ports/`). |
| Infrastructure | `app/infrastructure/` | БД, Redis/arq, хранилище, провайдеры, Telegram, yt-dlp. |
| Bot | `app/bot/` | Обработчики, клавиатуры, callbacks, middleware — тонкие обёртки над use cases. |
| API | `app/api/` | FastAPI: `/healthz`, `/readyz`, `/d/{token}`. |
| Workers | `app/workers/` | Отдельные процессы: cleanup, backup. arq-воркер — `app/main_worker.py`. |
| Composition | `app/composition/` | DI: связывает реализации с портами. Единственное место сборки. |
| Utils | `app/utils/` | Чистые помощники (URL, имена файлов, корреляция). Без IO. |

## Разрешённые импорты

```
domain         ← ничего внутреннего
application    ← domain
infrastructure ← domain, application
bot, api, workers ← application, domain, utils   (не infrastructure и не драйверы)
composition    ← всё
utils          ← ничего внутреннего
```

`bot`, `api`, `workers` не импортируют `redis`, `sqlalchemy`, `asyncpg`,
`arq`, `yt_dlp` и `app.infrastructure`: нужен драйвер — добавьте порт в
`application/ports/` и реализацию в `infrastructure/` (так сделаны
`ProgressFeed`, `MediaSender`, `MediaStorage`). Проверяет
`app/tests/test_layering.py`; точки входа `app/main_*.py` — исключение.

## Куда класть новый файл

- Ответ пользователю в Telegram → `app/bot/...`
- Бизнес-правило, оркестрация шагов → `app/application/use_cases/...`
- Данные с инвариантами → `app/domain/entities/...`
- PG / Redis / диск / Telegram API / yt-dlp → `app/infrastructure/...`
- HTTP-маршрут → `app/api/...`
- Отдельный долгоживущий процесс → `app/workers/...`
- Скрипт для оператора → `deploy/scripts/...`

Новые пакеты верхнего уровня (`app/services/`, `app/helpers/`, `app/core/`)
не создавать: нужен новый дом — это решение для ADR. Глобальное
изменяемое состояние — только в `app/composition/`.

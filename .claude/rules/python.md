---
paths:
  - "app/**/*.py"
---

# Python: стиль, типы, async

- Формат и линт: `ruff format`, `ruff check --fix` (не `black`, ADR-0003).
  Типы: `mypy app`. Pre-commit (`.pre-commit-config.yaml`) делает то же.
- `from __future__ import annotations` в каждом модуле. Все публичные
  функции и методы типизированы; `Any` — только с причиной.
- `pydantic` v2 — для конфигурации и IO-моделей; доменные сущности в
  `app/domain/entities/` — dataclass-подобные классы.
- Весь IO асинхронный. HTTP-клиент — `httpx`. Блокирующий синхронный код
  (библиотека yt-dlp, `urllib` в `http_probe.py`) — через
  `asyncio.to_thread(...)`.
- Подпроцессы — `asyncio.create_subprocess_exec(arg, arg, ...)`, только argv,
  **никогда** `shell=True`. Загрузка yt-dlp идёт в дочернем процессе
  `ytdlp_child` (ADR-0014); новые внешние бинарники — только в
  `app/infrastructure/`.
- Конфигурация — только `app.config.get_settings()`. `os.environ` вне
  `app/config.py` запрещён (`app/tests/test_env_access.py`).
- Логи — `get_logger(__name__)`, события `snake_case`, не f-строки
  (подробно — `errors-and-logging.md`). `print` запрещён.
- Ошибки — подклассы `app.exceptions.AppError` с русским `user_message`.

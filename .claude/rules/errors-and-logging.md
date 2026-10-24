---
paths:
  - "app/**/*.py"
---

# Ошибки и логи

Подробно: `docs/16-error-handling.md`, `docs/14-logging-observability.md`.

## Ошибки

Все прикладные ошибки — подклассы `app.exceptions.AppError` с русским
`user_message`; `str(exc)` идёт в логи и БД. Точка входа ловит в три уровня:

```python
try:
    ...
except (InvalidUrlError, UnsupportedPlatformError) as exc:
    _logger.info("link_rejected", reason=type(exc).__name__)
    await message.reply_text(exc.user_message)
except AppError as exc:
    _logger.warning("link_analysis_failed", error_class=type(exc).__name__)
    await message.reply_text(exc.user_message)
except Exception:
    _logger.exception("link_analysis_unexpected_error")
    await message.reply_text("Не удалось обработать ссылку. Попробуйте позже.")
```

Пользователь никогда не видит текст исключения или трейс.

## Логи

```python
from app.logging_config import get_logger
from app.utils.correlation import bind_context

_logger = get_logger(__name__)
with bind_context(request_id=req_id, user_id=uid, chat_id=cid):
    _logger.info("update_received")
```

- Имя события — константа `snake_case` (`job_done`, `temp_link_issued`),
  не f-строка. Новое событие добавьте в каталог `docs/14` §4; события,
  упомянутые в runbooks, должны существовать в коде
  (`app/tests/test_docs_references.py`).
- Уровни: INFO — переходы и решения; WARNING — обработанная `AppError`,
  повторы; ERROR — неожиданное, через `_logger.exception(...)`.
- `request_id` / `job_id` привязывайте в начале единицы работы через
  `bind_context`.

## Не логировать никогда

- `BOT_TOKEN`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `API_INTERNAL_TOKEN`,
  cookies, заголовок Authorization, сырые апдейты Telegram.
- Полный токен временной ссылки — только `token[:8] + "..."`.
- **URL источника, превью и загрузки — на любом уровне** (P11,
  `app/tests/test_no_urls_in_logs.py`). Логируйте `platform` / `host`.
  Осторожно с `error=str(exc)`: текст ошибок yt-dlp может содержать URL
  («Unsupported URL: …») — предпочитайте `error_class=type(exc).__name__`.
- Username, имя, телефон — только числовые `user_id` / `chat_id`.

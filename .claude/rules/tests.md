---
paths:
  - "app/tests/**"
---

# Тесты

Подробно: `docs/18-testing-strategy.md`.

- Юнит-тесты герметичны: без сети, реальных БД, Redis, Telegram. Пути —
  через `tmp_path`. Для use case — in-memory фейки портов и репозиториев;
  `app.infrastructure.db` в юнит-тестах не импортировать.
- `asyncio_mode = "auto"` задан в `pyproject.toml` — маркер
  `@pytest.mark.asyncio` не обязателен.
- Integration: `pytestmark = pytest.mark.integration` в `app/tests/integration/`.
  Без `DWTGBOT_TEST_POSTGRES_URL` / `DWTGBOT_TEST_REDIS_URL` они
  пропускаются; `pytest -m "not integration"` исключает их совсем.
  В CI их гоняет job «Integration tests».
- Файл `app/tests/test_<thing>.py`; имя теста читается как фраза
  (`test_register_use_marks_inactive_when_exhausted`); проверяйте
  конкретные значения, не только `is not None`.
- Новый тест-страж (правило, которое должно держаться автоматически) —
  проверьте, что он падает на старом коде.
- Нельзя: `pytest.mark.skip("flaky")`, реальные `httpx` / БД в юнит-тестах,
  мок приватных методов, `time.sleep` в async-тестах (`asyncio.sleep`
  или ожидание по условию).
- Coverage gate — 70 % (`addopts` в `pyproject.toml`).

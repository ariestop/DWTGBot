# ADR-0012 — Порты `MediaSender` / `MediaStorage` для слоя application

- **Status:** Accepted (2026-09-25)
- **Date:** 2026-09-25
- **Deciders:** project owner (Фаза 1 плана рефакторинга)
- **Tags:** architecture, layering, ports, delivery
- **Supersedes:** —
- **Relates to:** [ADR-0005](0005-locked-architectural-assumptions.md) (строка 5, provider-based design), [ADR-0010](0010-instant-download-ux.md) (`ProgressReporter`)

---

## 1. Context

Матрица зависимостей в [`03-project-structure.md`](../03-project-structure.md)
запрещает `application → infrastructure`. На деле два центральных класса
её нарушали:

- `app/application/services/delivery_service.py` импортировал
  `LocalStorage`, `TelegramSender` и SDK `telegram`
  (`InlineKeyboardButton`, `InlineKeyboardMarkup`, `TelegramError`);
- `app/application/use_cases/process_download.py` импортировал
  `LocalStorage`, `TelegramSender` и `NoopProgressReporter` из
  `app/infrastructure/cache/`.

Следствия: юнит-тесты доставки тянули PTB, типы конструкторов были
конкретными классами, а правило слоёв нигде не проверялось.

## 2. Decision

1. В `app/application/ports/` добавлены два `Protocol`:
   - `MediaStorage` (`media_storage.py`) — `job_dir`,
     `assert_free_space`, `assert_under_max`, `package_zip`: ровно то,
     что использует application. Реализация — `LocalStorage`.
   - `MediaSender` (`media_sender.py`) — `send_text` / `send_video` /
     `send_audio` / `send_photo` / `send_document`. Реализация —
     `TelegramSender`.
2. Инлайн-клавиатура передаётся DTO `InlineKeyboard` / `InlineButton`
   (у кнопки ровно одно из `url` / `callback_data`). Перевод в
   `InlineKeyboardMarkup` делает `TelegramSender`.
3. Повтор прямой загрузки: `MediaSender.upload_retry_errors` — кортеж
   исключений, на которых `DeliveryService` повторяет попытку. У
   `TelegramSender` это `(TelegramError,)`. Исключения не оборачиваются:
   после исчерпания попыток наружу (в arq) уходит исходная ошибка PTB,
   как и раньше.
4. `NoopProgressReporter` перенесён в `app/application/ports/progress_reporter.py`:
   у него нет I/O, и use case использует его как значение по умолчанию.
   Модуль `app/infrastructure/cache/noop_progress_reporter.py` удалён.
5. `app/tests/test_layering.py` разбирает импорты `app/domain` и
   `app/application` через AST: разрешены только stdlib и внутренние
   модули; `app.infrastructure`, `app.bot`, `app.api`, `app.workers`,
   `app.composition` и любые сторонние пакеты запрещены.

## 3. Consequences

- Поведение не меняется: те же сообщения, кнопки, повторы и исключения.
  Проверено существующими тестами доставки и e2e, а также тестами
  маппинга DTO в `app/tests/test_telegram_sender.py`.
- Фейки отправителя в тестах объявляют `upload_retry_errors`.
- Новый мессенджер-адаптер реализует `MediaSender` без правок
  `DeliveryService`.
- Нарушение слоёв теперь ловит обычный `pytest`.

## 4. Alternatives considered

- **Оборачивать `TelegramError` в свою `MediaSendError(AppError)`.**
  Отклонено: меняется тип исключения, которое видят arq и
  `classify_exception`, а значит и метки SLO; поведение повторов
  пришлось бы перепроверять.
- **Отдавать в application `InlineKeyboardMarkup` как есть.** Отклонено:
  это и есть утечка SDK, которую закрывает ADR.
- **Порт с полным API `LocalStorage`.** Отклонено: application нужны
  четыре метода; остальное (`remove_path`, `list_files`) используют
  только cleanup и провайдеры.

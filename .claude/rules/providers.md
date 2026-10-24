---
paths:
  - "app/infrastructure/providers/**"
  - "app/infrastructure/downloader/**"
---

# Провайдеры и yt-dlp

Подробно: `docs/07-provider-architecture.md`, `docs/08-download-pipeline.md`,
`docs/30-add-new-provider-guide.md`.

- Контракт `BaseProvider`: `get_info` (async), `build_options` (чистая),
  `download` (async). Других публичных методов, которые зовёт application,
  не добавлять. Всё про yt-dlp (форматы, опции, cookies) — внутри провайдера.
- Вывод yt-dlp недоверенный и меняется: `.get()` с умолчаниями, `formats`
  может быть пустым, размеры перепроверять после загрузки, имена — через
  `sanitize_filename`, пути — через `ensure_within(STORAGE_PATH, …)`.
- Файлы пишутся только в `target_dir` задачи.
- `YtDlpRunner` (`app/infrastructure/downloader/ytdlp_runner.py`) — единственная
  точка вызова yt-dlp: метаданные — в потоке, загрузка — в дочернем процессе
  `ytdlp_child` (ADR-0014). Опции загрузки должны сериализоваться в JSON;
  функции (`match_filter`, хуки) добавляет `ytdlp_child.build_opts`.
- Ошибки классифицирует `ytdlp_results.classify_error`: временные и
  rate limit (`UpstreamUnavailableError`) — повтор arq; приватное / не найдено /
  не поддерживается — постоянные с `user_message`; баг — `_logger.exception`.
  Не сводить всё к «повторять всегда».
- Circuit breaker на хост (`RedisCircuitBreaker`) открывается только на
  ответы, похожие на throttling.
- URL в логах нет: `platform` / `host`.

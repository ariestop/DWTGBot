# ADR-0014 — Загрузка yt-dlp в дочернем процессе

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** ariestop
- **Tags:** worker, reliability, downloads

---

## 1. Context

`YtDlpRunner.download` вызывал синхронный `YoutubeDL.download` в
`asyncio.to_thread`. Поток нельзя остановить снаружи:

- `asyncio.wait_for` по `DOWNLOAD_TIMEOUT_SECONDS` (A10) снимал только
  ожидание. Задача уходила в FAILED, а поток продолжал качать.
- R13 добавил progress hook, который бросает `DownloadCancelled` на
  следующем кадре прогресса. Он не помогает, когда кадров нет: при
  зависшем соединении (до `socket_timeout`) и при post-processing ffmpeg
  (слияние дорожек, извлечение аудио), который идёт до конца.
- Отмена задачи arq (`JOB_TIMEOUT_SECONDS`, остановка воркера) тоже не
  останавливала поток. Он держал слот `to_thread`, диск и сеть, а при
  `WORKER_CONCURRENCY=2` мог занять половину воркера.

Аудит 2026-10 (R16) отметил это как архитектурный долг: жёсткая
остановка невозможна без отдельного процесса.

## 2. Decision

`download` запускает `python -m app.infrastructure.downloader.ytdlp_child`
в **новой группе процессов** (`start_new_session=True`) и общается с ним
по каналам:

- **stdin:** один JSON `{"url", "opts"}`. Только сериализуемые опции.
  Source guards (`match_filter` — функция) и progress hook добавляет сам
  child (`build_opts`).
- **stdout:** JSON-строки.
  - `{"progress": p}` — не чаще шага 0.5 %.
  - Итог: `{"ok": true}`, `{"error": "ytdlp", "message"}` или
    `{"error": "unexpected", "message"}`.
  - Child переносит fd 1 на stderr до запуска yt-dlp. Поэтому вывод
    yt-dlp, ffmpeg и случайный `print` не попадают в протокол.
- **stderr** наследуется от воркера: в логи контейнера, как раньше.

Родитель:

- передаёт прогресс в прежний синхронный `on_progress` через
  `asyncio.to_thread`;
- превращает `error: ytdlp` в `yt_dlp.utils.DownloadError` с тем же
  текстом, так что `classify_error` и circuit breaker работают без
  изменений;
- если выходит из ожидания раньше child (таймаут или `CancelledError`
  от arq), шлёт `SIGKILL` всей группе: yt-dlp и запущенный им ffmpeg.

`extract_info` и `probe_size` остаются в `to_thread`. Это короткие
запросы метаданных без ffmpeg, ограниченные `socket_timeout=30` и
`retries=1`; цена процесса на каждый анализ ссылки им не окупается.

Если воркер убит (`SIGKILL`, OOM), child узнаёт об этом при следующей
записи в stdout (`EPIPE`) и выходит. Перезапуск контейнера убивает его
в любом случае.

## 3. Consequences

### 3.1 Positive

- Таймаут и отмена задачи останавливают загрузку и ffmpeg сразу.
- Сбой внутри yt-dlp (утечка памяти, падение интерпретатора) не
  трогает процесс воркера; задача получает `DownloadError`.
- Прогресс-хук больше не пишет в Redis на каждый кадр yt-dlp: child
  прореживает до шага 0.5 %.

### 3.2 Negative / accepted trade-offs

- Около 0.5 с и ~65 МБ RSS на загрузку: новый интерпретатор
  импортирует yt-dlp. Замер локально: `python -m … ytdlp_child`,
  ошибочный URL, 3 запуска. При `WORKER_CONCURRENCY=2` и
  `LIMIT_WORKER_MEM=2g` это допустимо.
- Опции загрузки должны сериализоваться в JSON. Провайдер, которому
  понадобится функция в опциях, должен добавить её в `build_opts`.
- `make_cancel_hook` (R13) удалён: он больше не нужен.

### 3.3 Operational impact

Переменные окружения и образы не меняются: child — модуль того же образа
воркера и наследует его env и cwd. Healthcheck воркера ищет
`app.main_worker`; child его не подменяет.

## 4. Alternatives considered

### 4.1 Оставить поток и полагаться на cancel hook (R13)

Не останавливает ffmpeg и зависшее соединение — ровно те случаи, ради
которых нужен таймаут.

### 4.2 CLI `yt-dlp` вместо модуля

Пришлось бы переводить опции в флаги и разбирать текстовый вывод
прогресса и ошибок. Модуль с JSON-протоколом сохраняет те же опции,
что и раньше, и ту же классификацию ошибок.

### 4.3 `multiprocessing` / `ProcessPoolExecutor`

Пул переиспользует процессы — убить один, не сломав пул, нельзя. Fork
из asyncio-процесса с открытыми соединениями Redis/Postgres
небезопасен; spawn даёт ту же цену запуска, что и наш child, но без
отдельной группы процессов для ffmpeg.

## 5. Compliance

- `app/tests/test_ytdlp_timeout.py`: таймаут и отмена убивают child и
  его дочерний процесс; успех, прогресс, классификация ошибок, сбой
  child, JSON-запрос; реальный модуль child говорит по протоколу.
- `app/tests/test_ytdlp_child.py`: прореживание прогресса, итоговые
  строки, в stdout только протокол.
- `app/tests/test_ytdlp_ssrf_guard.py`: child добавляет
  `allowed_extractors` и `match_filter`.

## 6. References

- `app/infrastructure/downloader/ytdlp_child.py`,
  `app/infrastructure/downloader/ytdlp_runner.py`.
- [ADR-0010](0010-instant-download-ux.md) §2.2 — контракт `on_progress`.
- `docs/08-download-pipeline.md`, `docs/09-queue-and-workers.md`.
- Аудит 2026-10, пункт R16.

## 7. History

- 2026-10-24 — принято вместе с реализацией (аудит R16).

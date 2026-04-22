# TASK: Мгновенное скачивание + live-прогресс + описание поста

Status: **READY FOR IMPLEMENTATION** (architectural blockers closed)
Owner: TBD
ADR: [`docs/adr/0010-instant-download-ux.md`](../adr/0010-instant-download-ux.md) — **must read first**
Related code anchors cited below as `startLine:endLine:filepath`.

Architectural review has flagged this task as safe to split into 6
independent PRs. The plan below is the result of that review; anything
marked 🔒 is a load-bearing decision and must not be re-opened during
implementation without updating ADR-0010.

---

## 1. DESCRIPTION

Убрать промежуточный шаг выбора качества и сразу начать скачивание после
получения ссылки. В чате появляется одно «живое» сообщение: превью
(thumbnail) + прогресс-бар (полоска 0 → 100 %). По окончании сообщение
замещается готовым видео с подписью. Плюс три мелких UX-правки.

Сводка пунктов пользователя:

1. Download стартует немедленно на ссылке — без инлайн-меню выбора опции.
2. Превью-кадр + анимация прогресс-бара в едином сообщении пока идёт
   скачивание и пост-обработка.
3. Добавить в подпись под видео: «Спасибо за использование нашего бота
   @dwtgbot».
4. Убрать вывод `id задачи: <N>` из пользовательских сообщений.
5. Для YouTube/Instagram с непустым описанием поста добавить inline-кнопку
   «Получить текст поста 👇»; по нажатию — бот присылает текст поста.

---

## 2. GOAL

Снизить число шагов/тапов до медиа с 2 до 0 (пользователь кинул ссылку —
получил видео). Сделать ожидание осязаемым (прогресс-бар) и сохранить
контекст поста (описание) без захламления основного сообщения.

---

## 3. USER FLOW

### Сегодня (для справки)

```
user → ссылка
bot  → «Получен YouTube ... Выберите вариант»  [inline: 360p/480p/720p/1080p/MP3]
user → [tap 1080p]
bot  → «Скачиваю «Видео 1080p» ... id задачи: 42»
bot  → (позже) video(file)  caption: «Название 13.4 MB»
```

### После фичи

```
user → ссылка
bot  → photo(thumbnail)  caption: «Скачиваю... [░░░░░░░░░░] 0 %»   [✖ Отмена]
bot  → (edit каждые ~2 с) caption: «Скачиваю... [██████░░░░] 58 %»
bot  → (edit при entering processing) caption: «Обрабатываю...   92 %»
bot  → (edit при finalizing) caption: «Загружаю...               98 %»
bot  → (удаляет progress-message) send video(file)
         caption: «Название · 13.4 MB\n\nСпасибо за использование нашего
                   бота @dwtgbot»
         reply_markup: [ «Получить текст поста 👇» ]   ← только если
                                                        description непуст
user → [tap button]
bot  → (новое сообщение)  «<текст описания поста; чанки по 4000 chars>»
```

Если превью-кадр недоступен (провайдер не дал `thumbnail`): вместо
`send_photo` используем `send_message` (text-only) с тем же caption-ом
прогресса. Локальный кадр из скачанного видео (`ffmpeg -ss 0 -frames:v 1`)
может быть добавлен в follow-up задаче, но в MVP — text-only fallback.

Cancel-кнопка действует только в стадии `DOWNLOADING`. На стадии
`PROCESSING` кнопка либо скрыта, либо отвечает alert'ом «Отмена недоступна
на этапе обработки» (см. Edge case E6).

---

## 4. SCOPE

### IN

1. **Auto-select + enqueue без picker.**
   Новый use-case `AutoEnqueueDownloadUseCase` в
   `app/application/use_cases/auto_enqueue_download.py` (объединяет
   текущую `AnalyzeLinkUseCase` + `EnqueueDownloadUseCase` в одну
   транзакцию: detect → get_info → default_option → create row →
   enqueue → `ProgressReporter.start`). Старый `AnalyzeLinkUseCase`
   остаётся для picker-пути за флагом `INSTANT_DOWNLOAD_ENABLED=false`.

   Новый абстрактный метод `BaseProvider.default_option(info) ->
   DownloadOption` (`app/infrastructure/providers/base.py`):
   - YouTube: наивысший доступный `video_<height>` из
     `_VIDEO_HEIGHTS` (`23:24:app/infrastructure/providers/youtube.py`).
   - Instagram: `single_video` / `single_photo` / `gallery_all` по
     `info.kind` (`92:99:app/infrastructure/providers/instagram.py`).
   - Если провайдер не может выбрать default — `DownloadError`.
     Никаких silent fallback'ов.

   Старый callback `dl|<request_id>|<option_key>` удаляется **только
   внутри ветки флага**. При `INSTANT_DOWNLOAD_ENABLED=false` работает
   как сейчас. Новый callback `cancel_job|<job_id>` (отдельный модуль
   `app/bot/callbacks/cancel_job.py`) переносит кнопку отмены в
   progress-сообщение.

2. **Live-прогресс через Redis side-channel.** 🔒 ADR-0010 §2.2

   Слой **application**:

   - `app/application/ports/progress_reporter.py` — новый протокол
     `ProgressReporter` (см. ADR-0010 §2.2 для сигнатуры).
   - `app/domain/enums.py` — новый enum `ProgressStage`.
   - `ProcessDownloadUseCase` принимает `ProgressReporter` через
     конструктор и вызывает `update()` на границах фаз
     (DOWNLOADING 0→70 %, PROCESSING 70→95 %, UPLOADING 95→100 %).
     Никаких `progress_ref` параметров в сигнатуре методов.

   Слой **infrastructure**:

   - `app/infrastructure/cache/redis_progress_reporter.py` — реализация
     `RedisProgressReporter`: пишет `progress:{job_id}` как hash с
     TTL 10 мин, публикует имя job_id на канал `progress:events`.
     Дебаунс в самом reporter'е (max 1 write/sec/job_id).
   - `app/infrastructure/cache/noop_progress_reporter.py` — пустая
     реализация для тестов и для `INSTANT_DOWNLOAD_ENABLED=false`.
   - `app/infrastructure/downloader/ytdlp_runner.py` —
     `YtdlpRunner.download()` принимает опциональный callback
     `on_progress: Callable[[float], None] | None` и включает
     `progress_hooks` (убрать `noprogress: True` на
     `270:270:app/infrastructure/downloader/ytdlp_runner.py`).
     Hook вызывает callback, use-case переводит в
     `reporter.update()`. Worker использует **sync** Redis-клиент в
     hook'е — без bridge'а в async loop.

   Слой **bot**:

   - `app/bot/services/progress_updater.py` — новый сервис,
     asyncio-task. Подписывается на `progress:events`, читает
     `progress:{job_id}` + `progress_meta:{job_id}`, вызывает
     `bot.edit_message_caption`. При старте bot'а делает `SCAN
     progress_meta:*` и ребилдит список активных jobs (recovery).
     Debounce: пропуск если `|Δ%| < PROGRESS_DEBOUNCE_PERCENT` И
     stage не изменился И с последнего применения прошло менее
     `PROGRESS_REDRAW_INTERVAL_SEC`. Обработка `RetryAfter` /
     `MessageNotModified`. Watchdog: >30 сек без обновления →
     caption становится «Связь с worker'ом потеряна, скачивание
     продолжается»; >5 мин → delete.
   - `app/bot/application.py` — lifecycle через
     `BotComposition.aclose()`, не через ad-hoc создание task'а
     в handler'е.

   Слой **composition**:

   - `app/composition.py` — wiring `RedisProgressReporter` в
     `build_bot()` и `build_worker()`. При
     `INSTANT_DOWNLOAD_ENABLED=false` worker получает
     `NoopProgressReporter`. Никаких прямых инстанций reporter'а
     нигде, кроме `composition.py` (§9 anti-pattern 2).

   На доставке: после успешного `send_video` / `send_document` /
   temp-link-сообщения `DeliveryService.deliver`
   (`58:136:app/application/services/delivery_service.py`)
   вызывает `reporter.finish(job_id)`; `progress_updater` при
   получении `finish` удаляет progress-message.

3. **Caption footer + job_id hidden.**

   - Расширить `_caption(...)`
     (`139:140:app/application/services/delivery_service.py`): в конце
     добавить `\n\n{Settings.BRAND_FOOTER}` если `BRAND_FOOTER`
     непустой. То же применяется к temp-link сообщению (не только
     к inline caption).
   - Удалить строку `id задачи: <code>{result.job_id}</code>` в
     `86:91:app/bot/callbacks/download.py`. В новом auto-flow её
     нет by design; в старом picker-path (флаг `false`) строку тоже
     убираем (один лог-grep по пользовательским сообщениям должен
     быть пуст: `rg "id задачи" app/ | grep -v tests`).

4. **«Получить текст поста».**

   - `MediaInfo.description: str = ""`
     (`41:51:app/domain/entities/media_info.py`). Truncate по UTF-8
     ≤ `POST_TEXT_MAX_CHARS` (default 10 000).
   - `YouTubeProvider.get_info()` и `InstagramProvider.get_info()`
     кладут в `MediaInfo.description` значение `entry["description"]`
     (yt-dlp sanitized dict). Если ключ отсутствует — пустая строка.
   - `RedisRequestStateStore._info_to_dict` /
     `_info_from_dict` расширяются полем `description`
     (`119:128:app/infrastructure/cache/redis_state_store.py`).
   - В `AutoEnqueueDownloadUseCase`: если
     `info.description.strip()` длиннее 10 символов, копируем в
     `post_text:{job_id}` с TTL `POST_TEXT_TTL_SEC` (default 24 ч).
   - Новая схема callback `post_text|<job_id>`:
     `app/bot/callbacks/post_text.py` (data model) +
     `app/bot/callbacks/post_text_handler.py` (handler). По
     нажатию:
     - `answer_callback_query()` без alert (убрать «часики»).
     - Если ключа `post_text:{job_id}` нет → alert «Текст поста
       больше недоступен» (TTL истёк).
     - Иначе: `html.escape` текста, чанкование по 4000 символов,
       `send_message(..., disable_web_page_preview=True)` без
       parse_mode. Если чанков > 1 — префикс «(n/N) » в начале
       каждого.
   - Кнопка рендерится в `DeliveryService.deliver` только если
     `post_text:{job_id}` существует на момент отправки
     (double-check через `EXISTS`, чтобы не показать кнопку, которая
     тут же вернёт alert).

### OUT

- Per-user / per-chat quality default (команда `/quality`, таблица
  `user_settings`). Follow-up задача после stabilization.
- `/audio <url>` команда для audio-only download. Follow-up.
- Gallery Instagram с inline-выбором «только видео/фото». По дефолту
  всегда `gallery_all`.
- Кастомизация `BRAND_FOOTER` per-user. Только один ENV на весь бот.
- WebSocket-прогресс в API-интерфейсе. Отдельная задача.
- Локальный extracted-frame как thumbnail (когда провайдер не даёт
  `thumbnail_url`). MVP = text-only fallback.
- Cancellation во время стадии `PROCESSING`. MVP = cancel только
  на `DOWNLOADING`.
- Частичный успех gallery (3 из 5 скачалось). Политика всё-или-ничего.
- Удаление старого picker-кода. Остаётся за флагом минимум один
  мажор после Accept ADR-0010.

---

## 5. TECH

| вопрос | ответ |
|---|---|
| нужен ли **worker**? | **Да**, без изменений сервисной схемы. `ProcessDownloadUseCase` расширяется инъекцией `ProgressReporter` через DI. Worker **не** вызывает Telegram для прогресса (см. 🔒 ADR-0010 §2.2 и §4.1 Alternatives). |
| нужна ли **очередь**? | **Да**, arq-pipeline остаётся. Контракт `WorkerJobPayload` не меняется. Прогресс — **side-channel** через Redis, не новая очередь. |
| нужна ли **БД**? | **Нет новых таблиц и колонок.** Описание поста и состояние прогресса — Redis (ephemeral UX-state). Никаких Alembic миграций. |
| нужен ли **provider**? | **Изменения в 2 существующих** (YouTube, Instagram): добавить `default_option()` (абстрактный в `BaseProvider`) + заполнение `MediaInfo.description`. |
| нужен ли **nginx**? | **Нет.** Temp-link доставка > 50 МБ работает как раньше; caption footer и кнопка «Получить текст» применимы и там. |

### Новые файлы

```
app/application/ports/progress_reporter.py           — Protocol
app/application/use_cases/auto_enqueue_download.py   — новый use-case
app/infrastructure/cache/redis_progress_reporter.py  — Redis реализация
app/infrastructure/cache/noop_progress_reporter.py   — Noop реализация
app/bot/services/__init__.py                          — новый подкаталог
app/bot/services/progress_updater.py                  — asyncio task
app/bot/callbacks/cancel_job.py                       — callback data
app/bot/callbacks/cancel_job_handler.py               — handler
app/bot/callbacks/post_text.py                        — callback data
app/bot/callbacks/post_text_handler.py                — handler

docs/adr/0010-instant-download-ux.md                  — уже написан
```

### Изменяемые файлы

```
app/application/use_cases/process_download.py         — DI ProgressReporter
app/application/services/delivery_service.py          — footer, post-text
                                                        button, finish()
app/bot/application.py                                — bootstrap updater
app/bot/handlers/links.py                             — авто-enqueue за
                                                        флагом
app/bot/callbacks/download.py                         — убрать id-строку
                                                        (picker path)
app/bot/keyboards/download_options.py                 — только при
                                                        флаге=false
app/config.py                                         — новые Settings поля
app/composition.py                                    — wiring reporters
app/domain/entities/media_info.py                     — +description
app/domain/enums.py                                   — +ProgressStage
app/infrastructure/downloader/ytdlp_runner.py         — progress_hooks +
                                                        sync-Redis hook
app/infrastructure/providers/base.py                  — abstract
                                                        default_option()
app/infrastructure/providers/youtube.py               — default_option() +
                                                        description
app/infrastructure/providers/instagram.py             — default_option() +
                                                        description
app/infrastructure/cache/redis_state_store.py         — description в
                                                        _info_to_dict
app/infrastructure/telegram/sender.py                 — edit_message_media
                                                        вариант для финала
                                                        (follow-up;
                                                        пока send_video)

deploy/nl1/.env.example                               — новые переменные
deploy/nl2/.env.example                               — новые переменные
deploy/templates/env.template                         — новые переменные

docs/02-architecture.md                               — sequence §4
docs/06-bot-flow.md                                   — новый user-flow
docs/09-queue-and-workers.md                          — side-channel
docs/13-config-and-env.md                             — новые переменные
docs/24-runbooks.md                                   — deploy order
docs/35-metrics-and-slo.md                            — новые метрики
```

### Новые Settings поля

```python
INSTANT_DOWNLOAD_ENABLED: bool = True
BRAND_FOOTER: str = "Спасибо за использование нашего бота @dwtgbot"
PROGRESS_REDRAW_INTERVAL_SEC: float = 2.0
PROGRESS_TTL_SEC: int = 600
PROGRESS_DEBOUNCE_PERCENT: int = 3
POST_TEXT_TTL_SEC: int = 86400
POST_TEXT_MAX_CHARS: int = 10000
```

### Ключи Redis

```
progress:{job_id}        hash: {percent, stage, updated_at}    TTL 10 min
progress_meta:{job_id}   hash: {chat_id, message_id, started_at} TTL 30 min
progress:events          pubsub channel: job_id                  —
post_text:{job_id}       string: description                    TTL 24 h
```

### Новые метрики (обязательны, иначе фича не observable)

```
progress_events_published_total{stage}          counter
progress_updates_applied_total{result}          counter
  # result: ok | rate_limited | message_not_modified | failed
progress_update_latency_seconds                 histogram
download_job_age_seconds                        histogram
post_text_callbacks_total{result}               counter
  # result: served | expired | not_found
```

---

## 6. RISKS

| # | Риск | Уровень | Митигация |
|---|---|---|---|
| R1 | Двойной источник прогресса для IG-VP9: yt-dlp hook + ffmpeg transcode | **High** | Единственный writer — `ProgressReporter.update()` из use-case, а не прямо из yt-dlp hook'а. Hook пишет в локальную переменную worker'а, use-case читает между фазами и репортит монотонно. |
| R2 | `send_photo(thumbnail_url)`: YouTube CDN часто отдаёт 403 / signed URL с коротким TTL | Medium | Если `send_photo` падает — fallback на `send_message` (text-only). Локальный extracted-frame — follow-up, не блокер. |
| R3 | Bot-polling Redis при 500+ одновременных jobs даёт нагрузку на CPU Redis | Low → High at scale | MVP: `PSUBSCRIBE progress:events` (push), а не polling. Если появляется bottleneck — partitioning по hash job_id. Trigger для миграции зафиксирован в ADR-0010. |
| R4 | Гонка: job завершился до того, как updater успел отрисовать 100 % | Low | `ProgressReporter.finish()` публикует terminal событие; updater при получении любой update'ы первым делом проверяет `stage == DONE/FAILED/CANCELLED` и снимает сообщение. |
| R5 | WireGuard flap: worker не может опубликовать прогресс в Redis | Medium | Publish с try/except, log-and-continue. Download не падает. Bot watchdog через 30 сек показывает placeholder «Связь потеряна». |
| R6 | Redis без persistence → `post_text:{job_id}` потерян при рестарте Redis | Low | Кнопка при нажатии возвращает alert «Текст поста больше недоступен». Проверить что `/readyz` ловит Redis down до того, как пользователи начнут жать кнопки. |
| R7 | `INSTANT_DOWNLOAD_ENABLED=false` должен восстанавливать старый flow 1-в-1 | Medium | Старый код picker-пути остаётся нетронутым как ветка в `handle_link` и остаётся в коде минимум 1 мажор (ADR-0010 §5 deprecation clock). |
| R8 | Pending jobs, enqueue'нутые старой версией, без `progress_meta:*` | Medium | Worker пишет прогресс в пустоту для них — прогресс-бар не появится, финальный `send_video` прилетит как раньше. Зафиксировано как acceptable. Рекомендуемый порядок деплоя: bot → worker (в `docs/24-runbooks.md`). |
| R9 | Cancellation в середине transcode: subprocess ffmpeg не убивается | Low | MVP: cancel работает только в `DOWNLOADING`. Кнопка скрывается / alert'ит при `PROCESSING`. |
| R10 | Orphan `progress:*` ключи при crash worker'а до `finish()` | Medium | TTL 10 мин на всех progress-ключах. Cleanup-loop на NL-2 расширяется sweep'ом `progress:*` с `updated_at < now - 15 min`. |
| R11 | Telegram rate-limit 429 на edit: 20 ссылок подряд в одном чате | Medium | Per-chat rate-limit в `progress_updater`: глобальный мьютекс на chat_id + round-robin между активными jobs этого chat'а. |
| R12 | `MessageNotModified` 400 при идентичном caption | Low | Сравнение с предыдущим текстом до вызова `edit_message_caption`. Метрика `progress_updates_applied_total{result=message_not_modified}`. |

---

## 7. EDGE CASES

| # | Case | Обработка |
|---|---|---|
| E1 | TikTok и будущие провайдеры | `BaseProvider.default_option()` — **abstract**. Новые провайдеры обязаны реализовать, иначе NotImplementedError на провайдер-registration'е. |
| E2 | Gallery с одними картинками (PHOTO) | Прогресс-сообщение без %: «Загружаю фото» статично. Reporter пропускает `update`, сразу `finish`. |
| E3 | Link пришёл во время bot shutdown | `progress_meta:{job_id}` создан, но job не enqueue'нут. При следующем старте bot увидит orphan и удалит. Лог: `orphaned_progress_on_startup`. |
| E4 | Pre-existing audio_mp3 user | В auto-flow нет. Пользователь получит видео. Явно задокументировано в ADR-0010 §3.2. Миграция — через `/audio <url>` (follow-up). |
| E5 | Partial gallery failure (3/5 видео) | `force_transcode=True` для IG + existing retry semantics. Политика: если хоть один item упал — весь job fails с человеческим reason'ом. Reporter → `fail()`. |
| E6 | Cancel двойной тап | Second tap: `answer_callback_query(text="Уже отменяется", show_alert=False)`. |
| E7 | Кнопка «Получить текст» после истечения TTL | `answer_callback_query(text="Текст поста больше недоступен", show_alert=True)`. Метрика `post_text_callbacks_total{result=expired}`. |
| E8 | Description есть, но `strip()` → пустой / меньше 10 символов | Кнопка не показывается. Ключ в Redis не создаётся. |
| E9 | Description с URL / упоминаниями | `disable_web_page_preview=True`. `html.escape`. Никакого parse_mode. |
| E10 | 20 ссылок подряд в одном чате | Per-chat rate-limit в updater'е (см. R11). |
| E11 | WireGuard flap | See R5. |
| E12 | `MessageNotModified` 400 | See R12. |
| E13 | Bot restart во время активных download | Recovery через SCAN `progress_meta:*`; продолжаем редактировать по `chat_id/message_id`. Ограничение: только для меток с `started_at` за последние 30 мин (TTL). |
| E14 | Description возвращается с HTML-тегами (IG iframe embed) | `html.escape` в handler'е перед `send_message`. Без parse_mode. |
| E15 | Video > 50 МБ → temp-link | Финальное сообщение — текст со ссылкой. Footer применяется. Кнопка «Получить текст поста» там же. Reporter: `UPLOADING 95→100` вокруг `TempLinkService.issue` + `send_message`. |

---

## 8. DEFINITION OF DONE

### Функциональные

- [ ] Ссылка YouTube → через ≤ 3 с в чате появляется превью + «Скачиваю
      [░░░░░░░░░░] 0 %» с кнопкой Cancel. В течение загрузки — обновления
      ≥ 5 раз.
- [ ] На мобиле и десктопе видео проигрывается без фриза (не регресс
      фиксов `287b914`, `988e9ea`).
- [ ] Caption финального видео содержит: название, размер, футер
      «Спасибо за использование нашего бота @dwtgbot». При `BRAND_FOOTER=""`
      футер не показывается.
- [ ] `rg "id задачи" app/ | grep -v tests` пусто.
- [ ] Пост с description (YouTube + Instagram) → под видео кнопка
      «Получить текст поста 👇». По нажатию — текст приходит отдельным
      сообщением (1..N чанков с префиксом «(n/N) »).
- [ ] Пост без description (или `strip()` короче 10 символов) → кнопки нет.
- [ ] `post_text:{job_id}` после TTL истёк → нажатие кнопки даёт alert
      «Текст поста больше недоступен». Не висят таймауты.
- [ ] Cancel во время `DOWNLOADING` завершает job в `CANCELLED` без
      сайд-эффектов; в чате остаётся «Отменено» и удаляется через 10 с.
- [ ] Cancel во время `PROCESSING` → кнопка отвечает alert'ом или скрыта.
- [ ] `INSTANT_DOWNLOAD_ENABLED=false` восстанавливает старый picker-flow
      1-в-1 (кроме «id задачи: N» — она убирается везде).

### Технические

- [ ] Unit тесты:
      - `test_default_option_youtube.py` — полный список сценариев.
      - `test_default_option_instagram.py` — single / gallery / unknown.
      - `test_media_info_description.py` — round-trip через
        `RedisRequestStateStore` + truncation.
      - `test_progress_stage_enum.py`.
      - `test_progress_reporter_protocol.py` — Noop + FakeRedis.
      - `test_redis_progress_reporter.py` — TTL, дебаунс, publish.
      - `test_auto_enqueue_download.py` — use-case с fakes.
      - `test_process_download_progress.py` — правильные границы
        фаз (0→70→95→100) и `finish()`.
      - `test_ytdlp_progress_hook.py` — mocked yt-dlp + fallback на
        `estimated_size_bytes` при `total_bytes is None`.
      - `test_providers_description.py` — YT + IG заполняют description.
      - `test_delivery_service_footer.py` — with/without footer,
        temp-link тоже с footer'ом.
      - `test_handle_link_auto_enqueue.py` — флаг true / false.
      - `test_progress_updater.py` — debounce, rate-limit, watchdog.
      - `test_post_text_handler.py` — TTL expiry, chunking.
- [ ] Integration тест `test_end_to_end_auto_download.py`: in-memory arq,
      fake-provider, FakeRedis, мок-TelegramSender. Прогрессы 0 → 100,
      финальный `send_video`, `post_text:*` создан.
- [ ] Observability: все 5 метрик (§5 «Новые метрики») зарегистрированы
      в `/metrics`, и хотя бы по одному assert'у на каждую в
      integration-тесте.
- [ ] `/readyz` проверяет, что `progress_updater.last_iteration_at <
      now - 10s`.
- [ ] Load: 10 параллельных YT в одном чате без 429 на edit_message.
- [ ] `ruff check`, `ruff format --check`, `mypy app/` — чисто.
- [ ] Обновлены все шесть docs-файлов (см. ADR-0010 §5 Compliance).

### Деплойно

- [ ] Миграция не нужна.
- [ ] `.env.example` обновлён в **трёх** местах (nl1, nl2, templates) —
      все 7 новых переменных.
- [ ] `docs/24-runbooks.md`: новый runbook-блок «Deploy order for
      instant-download feature» с явной инструкцией bot-first.
- [ ] Canary-прогон на NL-1 с `INSTANT_DOWNLOAD_ENABLED=true` 15 мин;
      rollback-путь — ENV toggle, без пересборки.
- [ ] Прогнано на NL-2 с реальными YT + IG ссылками; логи содержат
      `progress_published`, `progress_applied`, `post_text_sent`.

---

## 9. CRITICAL PATH: 6 PRs

Последовательность важна — каждый PR независимо мёржится, но последующие
опираются на предыдущие. Не смешивать.

1. **PR 1 — foundation (domain + application ports).**
   `ProgressStage` enum, `MediaInfo.description`, `ProgressReporter`
   Protocol, `NoopProgressReporter`. Wiring `NoopProgressReporter` в
   обе композиции, чтобы сборка не падала. Никаких видимых изменений
   UX. **Безопасно мёржить в любое время.**

2. **PR 2 — providers.**
   `BaseProvider.default_option()` abstract, реализации в YT/IG,
   заполнение `MediaInfo.description` в обоих. Тесты провайдеров.
   **Не меняет UX, не требует деплоя.**

3. **PR 3 — Redis reporter + worker hooks.**
   `RedisProgressReporter`, `progress_meta:*` / `progress:*` ключи,
   `progress_hooks` в yt-dlp runner'е, публикация прогресса на
   границах фаз в `ProcessDownloadUseCase`. Wiring в
   `build_worker()`. Bot пока не читает прогресс — ключи пишутся в
   Redis, никто не смотрит. **Безопасно мёржить; можно проверить
   `redis-cli KEYS progress:*` на NL-1.**

4. **PR 4 — bot progress updater.**
   `app/bot/services/progress_updater.py`, lifecycle через
   `BotComposition`, `progress_meta:*` recovery на старте,
   debounce / rate-limit / watchdog. Пока не триггерится, потому что
   `handle_link` ещё не создаёт `progress_meta:*`. **Требует
   `/readyz`-изменения.**

5. **PR 5 — auto-enqueue + flag.**
   `AutoEnqueueDownloadUseCase`, изменения в `handle_link` за флагом
   `INSTANT_DOWNLOAD_ENABLED`, новая cancel-кнопка и её callback.
   Добавление `BRAND_FOOTER` в `_caption`. Убрать `id задачи: N`
   из картины. **Это видимый для пользователя релиз — требует
   canary + rollback-готовности.**

6. **PR 6 — post text button.**
   `post_text|<job_id>` callback + handler, рендеринг кнопки в
   `DeliveryService.deliver`, chunking, HTML-escape, alert на TTL
   expiry. **Последний штрих, можно мёржить после стабилизации PR 5.**

---

## 10. ESTIMATE

- PR 1 (domain + ports + composition wiring): **0.5 д**
- PR 2 (providers + default_option + description): **0.5 д**
- PR 3 (Redis reporter + ytdlp hooks + use-case границы): **1 д**
- PR 4 (progress_updater + recovery + debounce): **1 д**
- PR 5 (auto-enqueue + flag + footer + remove job_id): **1 д**
- PR 6 (post-text button + handler): **0.5 д**
- ADR-0010 + docs (sequence, flow, runbook, metrics, env): **0.5 д**
- QA + canary на NL-2 (включая edge cases): **0.5 д**

**Итого: ~5.5 дней** одного разработчика. Можно параллелить PR 2 и PR 3
(разные файлы, нет общих зависимостей) → минус 0.5 дня календарно.

---

## 11. OUT-OF-ORDER FOLLOW-UPS (не блокируют DoD)

- Команда `/quality <url>` с опциональным выбором качества.
- Команда `/audio <url>` для audio-only download.
- Таблица `user_settings` с persistent preferences.
- WebSocket-прогресс в API-интерфейсе.
- Локальный extracted-frame как thumbnail (когда провайдер не даёт).
- Удаление старого picker-кода после Deprecation clock
  (ADR-0010 §5).
- `edit_message_media` вместо delete+send_video для более плавной
  UX-анимации finalize-шага.

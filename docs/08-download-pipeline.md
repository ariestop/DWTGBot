# 08 — Download pipeline (yt-dlp + ffmpeg)

> Status: Stable
> Audience: engineers debugging downloads, AI agents tweaking format specs
> Read next: [`07-provider-architecture.md`](07-provider-architecture.md), [`11-storage-strategy.md`](11-storage-strategy.md)

This document explains how a single `download_jobs` row becomes a finished
file on disk: the `YtDlpRunner`, the ffmpeg call paths, the format strings,
and the failure modes you will actually see in production.

---

## 1. Pipeline overview

```mermaid
flowchart LR
    UC[ProcessDownloadUseCase] --> Prep[LocalStorage.prepare_job_dir]
    Prep --> Prov[Provider.download]
    Prov --> YDL[YtDlpRunner.download]
    YDL -->|asyncio.to_thread| YT[yt_dlp.YoutubeDL]
    YT --> FFM[ffmpeg<br/>postprocessor / merge]
    FFM --> FS[(target_dir/*.{ext})]
    FS --> Result[DownloadResult]
    Result --> Deliver[DeliveryService]
```

Component responsibilities:

| Component | Responsibility |
|---|---|
| `LocalStorage.prepare_job_dir(job_id)` | create `STORAGE_PATH/<job_id>/`, ensure perms |
| `Provider.download(...)` | translate `DownloadOption` → yt-dlp format spec + postprocessors |
| `YtDlpRunner.download(...)` | call yt-dlp safely; classify errors |
| `yt_dlp.YoutubeDL` | actual HTTP / HLS / DASH download |
| `ffmpeg` | merge separate video+audio streams; transcode audio |
| `DownloadResult` | hand off paths + metadata to delivery |

---

## 2. The `YtDlpRunner`

Код разделён на четыре модуля в `app/infrastructure/downloader/`:

| Модуль | Ответственность |
|---|---|
| `ytdlp_runner.py` | `YtDlpRunner` — асинхронный фасад: `to_thread`, circuit breaker, таймаут загрузки, общий `_guarded` для всех вызовов |
| `ytdlp_opts.py` | чистые сборщики опций (`extract_opts` / `probe_opts` / `download_opts`), `apply_source_guards`, `apply_proxy`, `host_of`, хук прогресса (`ProgressEvent` TypedDict) |
| `ytdlp_results.py` | `classify_error`, `looks_like_throttle`, `extract_known_size`, `selected_media_urls` |
| `http_probe.py` | `head_content_length` — HEAD по разрешённому хосту (allowlist + `HTTPS_PROXY_URL`) |
| `mobile_compat.py` | пост-обработка MP4 для мобильного Telegram через ffprobe/ffmpeg (§5) |

```python
class YtDlpRunner:
    async def extract_info(self, url: str, *,
                           extra_opts: dict | None = None) -> dict[str, Any]: ...
    async def download(self, url: str, *, format_spec: str,
                       target_dir: Path,
                       postprocessors: list[dict] | None = None,
                       merge_output_format: str | None = None,
                       extra_opts: dict | None = None,
                       force_transcode: bool = False,
                       on_progress: Callable[[float], None] | None = None) -> list[Path]: ...
    async def probe_size(self, url: str, *, format_spec: str,
                         extra_opts: dict | None = None) -> int | None: ...
```

### `extract_info`
- `skip_download=True`, `socket_timeout=30 s`, `retries=1`.
- `extra_opts` lets providers inject extractor-specific params (for example
  YouTube / Instagram `cookiefile`) while preserving shared guards
  (`allowed_extractors`, `match_filter`) and proxy wiring.
- Runs `ydl.sanitize_info(...)` so what we cache is JSON-safe.
- All exceptions normalised through `classify_error(...)` (`ytdlp_results.py`).

### `probe_size`
- Same extraction as `extract_info` with the requested `format_spec`;
  returns `extract_known_size(...)` — the sum of `filesize` /
  `filesize_approx` of the selected formats.
- When yt-dlp reports no size (Instagram's anonymous DASH formats carry
  neither `filesize` nor `duration`), it HEADs the selected format URLs
  (`selected_media_urls`) and sums `Content-Length`; logs
  `ytdlp_size_from_head`. Any missing length → `None`, and the worker
  rejects a video of unknown size with `SizeUnknownError`.

### `download`
- Hard-coded options:
  - `outtmpl = "<target_dir>/%(title).80B [%(id)s].%(ext)s"` — bounded
    title length, deterministic id-suffixed filename, restricted charset.
  - `restrictfilenames = True` — no shell-special characters.
  - `concurrent_fragment_downloads = 4` — better throughput on HLS/DASH.
  - `socket_timeout = 30 s`, `retries = 2`, `fragment_retries = 2` —
    short retries; the *job* is retried by arq for bigger failures.
  - `ffmpeg_location = shutil.which(settings.FFMPEG_BIN)` — absolute path;
    yt-dlp rejects a bare name. Omitted (yt-dlp auto-discovers) when the
    binary is not on `PATH`.
- Bounded by `DOWNLOAD_TIMEOUT_SECONDS` → `DownloadTimeoutError`.
- MP4-family outputs go through `mobile_compat.make_mobile_compatible` (§5).
- Returns sorted list of files inside `target_dir`.

### Error classification

`classify_error` (`ytdlp_results.py`) maps yt-dlp's `DownloadError` to our hierarchy:

Rows are checked top to bottom; the first match wins.

| Substring in error message | Mapped exception |
|---|---|
| "http error 429" / "too many requests" / "rate limit" / "rate-limit" / "rate limited" | `UpstreamUnavailableError` ("Источник временно ограничивает скачивание…") |
| "private" / "login required" / "sign in to confirm" / "use --cookies" | `MediaPrivateError` |
| "not found" / "does not exist" / "removed" / "404" | `MediaNotFoundError` |
| "unsupported url" | `ProviderError` |
| anything else | `DownloadError` |

Anything not a `YtDlpDownloadError` (e.g. `RuntimeError`, parser bugs) is
re-raised after a full `_logger.exception(...)`: as
`ProviderError("yt-dlp failure: ...")` from `extract_info`, as
`DownloadError` from `probe_size` / `download`. Throttle-shaped messages
(`HTTP Error 429`, "too many requests", "rate limit") also count towards
the per-host circuit breaker (`looks_like_throttle`). They are checked
before the private markers because Instagram answers an IP that hit its
anonymous rate-limit with "redirected to the login page … exceeded the
rate-limit … Use --cookies": the fix is cookies or another egress IP
(`INSTAGRAM_COOKIES_FILE`, `HTTPS_PROXY_URL`), not a private post.

---

## 3. Format-spec patterns (YouTube)

The YouTube provider builds yt-dlp format strings from the chosen height:

```text
bestvideo[height<=H][ext=mp4]+bestaudio[ext=m4a]
  / bestvideo[height<=H]+bestaudio
  / best[height<=H]
```

Reading left-to-right:

1. **Preferred**: pick the best MP4 video stream up to height `H`, mux with
   the best M4A audio stream — yields a clean MP4 with no transcode.
2. **Fallback 1**: any best video up to `H` + any best audio. ffmpeg merges
   into the chosen `merge_output_format` (`mp4`).
3. **Fallback 2**: a single combined `best[height<=H]` stream.

This order is explicit so we maximise quality while minimising ffmpeg work.

For audio:

```text
format    = bestaudio/best
postproc  = FFmpegExtractAudio(preferredcodec=mp3, preferredquality=192)
```

ffmpeg encodes to MP3 at the user-selected bitrate (192 kbps default).

---

## 4. Format-spec patterns (Instagram)

| Option kind | Как скачивается | Postprocessors |
|---|---|---|
| Video | yt-dlp, `bestvideo*+bestaudio/best` | forced mobile transcode |
| Image | прямой GET картинки с CDN (`HttpImageFetcher`) | none |
| Carousel (gallery) | фото — прямой GET, видео — yt-dlp с `playlist_items` = позиции видео | transcode только для видео |

Для фото у yt-dlp нет форматов: без `ignore_no_formats_error` он
отвечает «There is no video in this post», а с флагом всё равно не
скачивает картинку. Поэтому все вызовы `extract_info` / `probe_size`
Instagram идут с `ignore_no_formats_error=True`, фото-элемент получает
URL из `thumbnail` (самый крупный кандидат `image_versions2`), а
`download()` перед скачиванием заново извлекает пост (подписанные
URL CDN истекают). `HttpImageFetcher`
(`app/infrastructure/downloader/http_image.py`) проверяет хост по тому же
allowlist, что и yt-dlp (включая каждый редирект), принимает только
`image/*`, ограничивает размер `MAX_FILE_SIZE_MB` и использует
`HTTPS_PROXY_URL`. Размер фото до скачивания — HEAD `Content-Length`.

The provider may pass `INSTAGRAM_COOKIES_FILE` via `extra_opts={"cookiefile": ...}`
to access auth-required posts. See [`13-config-and-env.md`](13-config-and-env.md).

---

## 5. ffmpeg call paths

Через yt-dlp:
- `merge_output_format="mp4"` triggers ffmpeg's `mux` phase.
- `FFmpegExtractAudio` postprocessor triggers transcode.

Напрямую — только пост-обработка в `mobile_compat.py`, после успешной
загрузки и только для `.mp4` / `.mov` / `.m4v`. Мобильные клиенты Telegram
декодируют аппаратно и замораживают видеодорожку для VP9, HEVC, не-AAC
звука и не-yuv420p:

1. `probe_codecs` — `ffprobe` читает кодеки первых видео- и аудиопотоков.
2. `needs_transcode` — чистое решение: H.264 + AAC + yuv420p (или
   неизвестные значения) → быстрый remux `-c copy -movflags +faststart`
   (таймаут 120 с); иначе — транскод в H.264 high@4.1 + AAC 48 кГц,
   ≤ 30 fps (таймаут 600 с). `force_transcode=True` (Instagram) всегда
   выбирает транскод.
3. Вывод пишется в `<name>.remux.tmp.<ext>` и атомарно заменяет исходник.
   Любая ошибка ffmpeg, таймаут или пустой вывод оставляют исходный файл
   нетронутым (события `faststart_remux_failed` / `mobile_transcode_failed`).

Без `ffprobe` выбирается remux. Тесты: `app/tests/test_mobile_compat.py`
(ffprobe/ffmpeg подменены фейком).

Других прямых вызовов ffmpeg (миниатюры, ресайз, водяные знаки) нет.

If you need to add a direct ffmpeg call:
1. Use `asyncio.create_subprocess_exec(self._settings.FFMPEG_BIN, ...)`.
2. Always `await proc.wait()` with a timeout (`asyncio.wait_for`).
3. Capture stderr to a buffer and include it in `DownloadError` on
   non-zero exit code.
4. Add a section to this document.

---

## 6. End-to-end sequence (worker side)

```mermaid
sequenceDiagram
    autonumber
    participant Q as arq
    participant W as worker
    participant UC as ProcessDownloadUseCase
    participant DB as Postgres
    participant FS as LocalStorage
    participant P as Provider
    participant YDL as YtDlpRunner
    participant Deliver as DeliveryService

    Q ->> W: process_download_job(job_id)
    W ->> UC: execute(job_id)
    UC ->> DB: jobs_repo.get(job_id)
    DB -->> UC: DownloadJob (PENDING)
    UC ->> DB: jobs_repo.update(job.mark_processing())
    UC ->> FS: prepare_job_dir(job_id)
    FS -->> UC: /storage/<job_id>/
    UC ->> P: provider.download(url, option, target_dir=...)
    P ->> YDL: download(format_spec, postproc=..., merge=..., target_dir=...)
    YDL ->> YDL: yt_dlp + ffmpeg in to_thread
    YDL -->> P: list[Path]
    P -->> UC: DownloadResult
    UC ->> Deliver: deliver(job_id, chat_id, result)
    Deliver -->> UC: DeliveryOutcome
    UC ->> DB: jobs_repo.update(job.mark_done(...))
```

---

## 7. Filesystem layout per job

```
$STORAGE_PATH/
└── <job_id>/                        # prepared by LocalStorage
    ├── <title> [<media_id>].mp4     # YouTube video
    ├── <title> [<media_id>].mp3     # YouTube audio (alt)
    ├── <title> [<media_id>].mp4     # Instagram reel / video post
    ├── <media_id>.jpg               # Instagram photo post (HttpImageFetcher)
    └── NN_<item_id>.<ext>           # Instagram carousel item, NN = позиция в посте
```

Rules:
- Every job gets its own subdirectory keyed by `job_id` (deterministic; no
  collisions across users).
- `outtmpl` ensures filenames are restricted (`restrictfilenames=True`) and
  bounded (`%(title).80B`).
- The directory survives until either:
  - all files are uploaded to Telegram (then deleted by the worker), or
  - the temp link is exhausted/expired (then deleted by cleanup).

---

## 8. Timeouts

| Layer | Setting | Default | Notes |
|---|---|---|---|
| HTTP socket | `socket_timeout` | 30 s | both `extract_info` and `download` |
| HTTP retries | `retries` | 2 | yt-dlp internal retries |
| Fragment retries (HLS/DASH) | `fragment_retries` | 2 | per fragment |
| Job timeout | `JOB_TIMEOUT_SECONDS` | 1800 s | enforced by arq; kills the whole job |
| Job retries | `JOB_MAX_RETRIES` | 2 | arq re-enqueues up to N times |

If you change `JOB_TIMEOUT_SECONDS`, also re-evaluate
`concurrent_fragment_downloads` — too many concurrent fragments under a
short timeout can flap.

---

## 9. Failure taxonomy

| What broke | Where it surfaces | What user sees |
|---|---|---|
| URL gibberish | `extract_first_url` returns nothing | "Это не похоже на ссылку." |
| Host unsupported | `detect_platform` returns `None` | "Этот источник пока не поддерживается." |
| yt-dlp says "Private video" / "Sign in to confirm your age" | `MediaPrivateError` | "Видео приватное / требует авторизации." |
| Removed / 404 | `MediaNotFoundError` | "Видео недоступно или удалено." |
| Network / TLS / DNS | `DownloadError` ("network") | "Не удалось скачать. Попробуйте позже." |
| Format unavailable | `DownloadError` | same as above; logs include `format_spec` |
| ffmpeg merge failed | `DownloadError` (yt-dlp wraps it) | same as above |
| Job > timeout | arq `asyncio.TimeoutError` | "Не удалось скачать (таймаут)." after retries exhausted |
| Disk full | `OSError ENOSPC` from `LocalStorage` | "Сервер занят. Попробуйте позже." (logged as ERROR + alert) |

---

## 10. Anti-patterns

1. **Calling `yt_dlp.YoutubeDL` directly from a use case or the bot.** Only
   the runner may. Otherwise we lose the `to_thread` boundary and risk
   blocking the event loop.
2. **Reusing the same `target_dir` across jobs.** Always job-scoped.
3. **Setting `outtmpl` without `%(id)s`.** Without the id, two videos with
   identical titles overwrite each other.
4. **Disabling `restrictfilenames`.** Re-introduces shell-special chars in
   paths; opens path-handling bugs.
5. **Silencing yt-dlp errors to "be nice".** Errors must propagate so arq
   can decide whether to retry.
6. **Adding `--external-downloader aria2c`.** Significantly more moving
   parts; not worth it at our scale.
7. **Pre-fetching 4K/8K because "users would like it".** Out of scope; we
   target Telegram-friendly sizes by default.

---

## 11. Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Forgot `ffmpeg` in worker container | yt-dlp errors with "ffmpeg not found" | Build `worker.Dockerfile`; verify `which ffmpeg` |
| `STORAGE_PATH` not writable by the worker user (uid 1000) | `PermissionError` on `prepare_job_dir` | `chown -R 1000:1000` on the volume |
| `JOB_TIMEOUT_SECONDS` too small for long videos | Repeated retries, eventual FAILED | Increase timeout for known long content; never set < 300 s in prod |
| `media_cache` contains stale `available_heights` | Bot offers 1080p but download fails | Cache TTL is short (10 min); shorten further if you change provider behaviour |
| Two jobs with same `job_id` | Impossible: ids come from DB sequence | If you see this, your repository is broken; investigate |

---

## 12. Performance notes

- A single worker handles `WORKER_CONCURRENCY` jobs in parallel (default 2).
  Each yt-dlp download runs in its own thread, so increase `WORKER_CONCURRENCY`
  cautiously — disk IO and bandwidth become bottlenecks before CPU does.
- `concurrent_fragment_downloads=4` is a sweet spot for typical residential
  bandwidth; higher values rarely help and cause more retries on flaky CDNs.
- ffmpeg muxing is fast (no transcode) for the YouTube video path. The
  audio path *does* transcode, which takes a few seconds for typical
  songs; it's still cheaper than maintaining a server-side transcoder.

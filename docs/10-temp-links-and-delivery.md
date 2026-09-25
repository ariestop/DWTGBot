# 10 — Temporary links and delivery

> Status: Stable
> Audience: backend / SRE engineers, security reviewers, AI agents
> Read next: [`11-storage-strategy.md`](11-storage-strategy.md), [`20-deployment.md`](20-deployment.md)

When a downloaded file fits Telegram's per-bot upload limit (50 MiB by
default), the worker uploads it directly. Otherwise it issues a
**temporary HTTPS link** served by Nginx via `X-Accel-Redirect`. This
document is the authoritative reference for that path: the decision
points, the wire format, the security model, and the operational behaviour.

---

## 1. Decision tree

```mermaid
flowchart TD
    R{"DownloadResult"} -->|len(files) == 1| One
    R -->|len(files) > 1| Many

    One{"single file:<br/>size ≤ 50 MiB?"}
    One -->|yes| TG1[sendVideo / sendAudio /<br/>sendPhoto / sendDocument]
    One -->|no| Link1[Issue temp link]

    Many{"each ≤ 50 MiB<br/>and count ≤ 10?"}
    Many -->|yes| TGN[per-file upload<br/>+ caption text]
    Many -->|no| ZIP[ZIP-package on disk]
    ZIP --> Link2[Issue temp link]
```

This is implemented in `app/application/services/delivery_service.py:DeliveryService.deliver`.

| Outcome | `DeliveryMethod` | DB fields populated on `download_jobs` |
|---|---|---|
| Telegram upload (single or gallery) | `TELEGRAM_UPLOAD` | `telegram_file_id`, `file_size`, `mime_type` |
| Temp link (single big file or zipped gallery) | `TEMP_LINK` | `public_url`, `file_size`, `mime_type` |

`MAX_INDIVIDUAL_FILES = 10` is a constant — kept conservative to avoid
flood limits and noisy chats. Adjust only with UX justification.

---

## 2. Token issuance

`TempLinkService.issue(job_id, file_path)` produces:

| Field | Source |
|---|---|
| `token` | `secrets.token_urlsafe(TEMP_LINK_TOKEN_BYTES)` (default 32 bytes ≈ 43 chars) |
| `expires_at` | `now(UTC) + TEMP_LINK_TTL_SECONDS` (default 6 h) |
| `max_downloads` | `TEMP_LINK_MAX_DOWNLOADS` (default 5) |
| `file_path` | absolute path inside `STORAGE_PATH` (validated again at serve time) |
| `is_active` | `True` |

The returned URL is `f"{PUBLIC_BASE_URL}/d/{token}"`. Logs include only
the first 8 chars of the token (`token[:8] + "..."`) to make grep useful
without leaking secrets.

> **Cryptographic strength:** 32 random bytes URL-safe-base64 → 256 bits of
> entropy. Brute-forcing one valid token per million guesses still gives
> infeasible enumeration. We never expose the token in error messages,
> URLs in metrics, or analytics.

---

## 3. Sequence: large file delivery

```mermaid
sequenceDiagram
    autonumber
    participant W as worker (NL-2)
    participant SVC as TempLinkService
    participant DB as Postgres (NL-1)
    participant TG as Telegram
    participant U as User
    participant NX as nginx (NL-2)
    participant API as API (NL-2)
    participant FS as Storage (NL-2)

    W ->> SVC: issue(job_id, file_path)
    SVC ->> DB: temp_links INSERT (token, expires_at, max_downloads)
    DB -->> SVC: TempLink with id
    SVC -->> W: (link, "https://media.host/d/<token>")
    W ->> TG: send_text("📦 Файл слишком большой...", reply_markup=url button)
    W ->> DB: jobs.update(mark_done(public_url=URL))

    U ->> NX: GET https://media.host/d/<token>
    NX ->> API: proxy_pass + X-Internal-XAccel: 1
    API ->> DB: SELECT temp_links WHERE token=...
    DB -->> API: TempLink (or None)
    API ->> API: ensure_within(STORAGE_PATH, link.file_path) + file exists?
    alt invalid (404 / 403 / 410, no counter mutation)
        API -->> NX: error code
        NX -->> U: error
    else Range continuation (start > 0) or HEAD
        API ->> API: link.is_usable() or link.can_resume()  # no counter mutation
        API -->> NX: 200 + X-Accel-Redirect (nginx answers 206 for the Range)
        NX -->> U: requested bytes (sendfile)
    else valid path, GET from byte 0
        API ->> DB: try_register_use(token)  # atomic UPDATE ... RETURNING
        alt UPDATE matched
            DB -->> API: post-update TempLink
            API -->> NX: 200 + X-Accel-Redirect: /_protected/<relpath><br/>Content-Disposition: attachment; filename="..."
            NX ->> FS: open(STORAGE_PATH/<relpath>)
            NX -->> U: file (sendfile)
        else None (expired / exhausted / lost race with concurrent request)
            API -->> NX: 410
            NX -->> U: error
        end
    end
```

> **Топологии.** Метки NL-1 / NL-2 обозначают роль — control plane /
> media plane. В `split` это два хоста, связанные WireGuard; в `single`
> все участники диаграммы работают на одном хосте
> ([ADR-0011](adr/0011-single-server-topology.md)). В обеих топологиях
> nginx, api, worker и cleanup делят один том `storage`.

---

## 4. The `/d/{token}` endpoint

Source: `app/api/public/downloads.py`.

Behaviour:

| Condition | HTTP code | Reason |
|---|---|---|
| token unknown | `404 Not Found` | "Link not found" |
| token expired / exhausted / inactive | `410 Gone` | "Link expired or exhausted" |
| Range continuation / `HEAD` on an exhausted link that was started, not expired, not revoked | `200 OK` + `X-Accel-Redirect` | finishing or seeking the last download does not need a slot |
| `file_path` outside `STORAGE_PATH` | `403 Forbidden` | path-traversal defense |
| `file_path` does not exist on disk | `410 Gone` | "File no longer available" |
| OK + behind nginx | `200 OK` + `X-Accel-Redirect` + `Content-Disposition: attachment` | nginx serves the bytes |
| OK + standalone (dev) | `200 OK` + `FileResponse` (FastAPI streams it) | fallback when no nginx |

Key code points:

```python
link = await composition.temp_links_repo.get_by_token(token)
if link is None: 404
if not link.is_usable(): 410                                             # cheap pre-check
file_path = ensure_within(settings.STORAGE_PATH, Path(link.file_path))   # 403 on traversal
if not file_path.exists() or not file_path.is_file(): 410                # never burn a slot for a missing file

# Atomic increment + deactivate-on-cap. Returns None on a race.
link = await composition.temp_links_repo.try_register_use(token)
if link is None: 410                                                     # expired / exhausted / lost race

if request.headers.get("x-internal-xaccel", "0") == "1":
    rel = file_path.relative_to(settings.STORAGE_PATH)
    return Response(headers={
        "X-Accel-Redirect": f"/_protected/{rel.as_posix()}",
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
    })
return FileResponse(...)
```

### 4.1 What counts as one download

A video player or a download manager fetches one file with many HTTP
requests: `Range: bytes=0-`, then `bytes=<offset>-` on every seek,
resume after a network stall, or probe for an `moov` atom at the end of
the MP4. When every request spent a slot, a 5-use link was exhausted
during the very first playback; the next Range request got `410` and the
player (or the saved file) was left truncated, and a second tap on the
button showed "Link expired or exhausted".

`starts_new_download(method, range_header)` decides whether a request
spends a slot:

| Request | Spends a slot? | Allowed when |
|---|---|---|
| `GET` without `Range` | yes | `try_register_use` succeeds |
| `GET` with `Range: bytes=0-…` (incl. `0-1` probes) | yes | `try_register_use` succeeds |
| `GET` with a non-`bytes` or unparseable `Range` | yes | `try_register_use` succeeds |
| `GET` with `Range: bytes=N-…`, `N > 0`, or suffix `bytes=-N` | no | `is_usable()` or `can_resume()` |
| `HEAD` | no | `is_usable()` or `can_resume()` |

`TempLink.can_resume()` is `is_active AND expires_at > now AND
downloads_count > 0`: an exhausted link can still finish or seek the
download it already started until the TTL ends, while a revoked
(`is_active=false`) or expired link refuses everything. The Range bytes
themselves are nginx's job — the static module honours `Range` on the
`X-Accel-Redirect` target and answers `206`.

Trade-off: `TEMP_LINK_MAX_DOWNLOADS` limits the number of downloads
started from byte 0, not bytes transferred. A client that already holds
the token can fetch the tail with Range requests without spending
slots. The token is the secret; the counter is a sharing budget, and the
TTL is the hard bound.

### 4.2 Atomic counter

`try_register_use(token)` is a single
`UPDATE temp_links SET downloads_count = downloads_count + 1 WHERE
token = :t AND is_active AND expires_at > now AND downloads_count <
max_downloads RETURNING *`. Reaching the limit does **not** flip
`is_active`: the `downloads_count < max_downloads` predicate refuses new
downloads on its own, and `is_active=false` keeps meaning "expired or
revoked" — the signal cleanup uses to delete the file. This guarantees that a
single-use link served to two concurrent clients is delivered to
exactly one of them — the other gets `410 Gone`. See ADR-0008 §2.5.

Pre-checks (`get_by_token`, `ensure_within`, `file_path.exists()`)
deliberately run **before** the atomic call so a 404/403/410 caused
by a path probe or a cleanup race never consumes a download slot.

---

## 5. Nginx role

Snippet from `deploy/nginx/conf.d/media.conf.template`:

```nginx
location /d/ {
    # Docker embedded DNS + variable in proxy_pass forces per-request
    # re-resolution of the ``api`` hostname so a recreated api container
    # is picked up within ``valid=10s`` (canonical comment lives in
    # ``deploy/nginx/conf.d/media.conf.template``).
    resolver        127.0.0.11 valid=10s ipv6=off;
    set $api_upstream http://api:${API_PORT};
    proxy_pass         $api_upstream;
    proxy_set_header   X-Internal-XAccel "1";
    proxy_buffering    off;
    gzip               off;
    gunzip             off;
}

location /_protected/ {
    internal;
    alias /var/lib/dwtgbot/storage/;
    sendfile on;
    gzip off;
    gunzip off;
}
```

- `internal;` — nginx refuses to serve `/_protected/...` for any request
  that did not originate via `X-Accel-Redirect`. This is the bedrock of
  the security model: you cannot access files by path even if you guess
  the layout.
- `alias` maps the URL prefix to `STORAGE_PATH` mounted into the nginx
  container.
- `Content-Disposition` is set by the API before returning
  `X-Accel-Redirect` and is forwarded by nginx. Do **not** add a second
  `Content-Disposition` in `/_protected/`: duplicate values (`attachment;
  filename=...` plus bare `attachment`) caused browser edge-case failures
  during the 2026-04-26 production incident.
- `gzip off; gunzip off;` on both locations guarantees raw byte delivery.
  Temp-link payloads are arbitrary binaries (MP4/JPEG/audio), often already
  compressed and range-requested by browsers. Content negotiation here adds
  no value and can create `Content-Encoding` mismatches.

> **Operational rule:** if you ever change `alias` or `STORAGE_PATH`,
> restart both nginx and the API container so they agree on paths.

---

## 6. Lifecycle of a temp link

```mermaid
stateDiagram-v2
    [*] --> ACTIVE: TempLinkService.issue
    ACTIVE --> ACTIVE: try_register_use (downloads_count + 1 < max)
    ACTIVE --> EXHAUSTED: try_register_use (downloads_count + 1 == max)
    EXHAUSTED --> EXHAUSTED: Range continuation / HEAD (can_resume)
    ACTIVE --> EXPIRED: cleanup (now > expires_at)
    EXHAUSTED --> EXPIRED: cleanup (now > expires_at)
    ACTIVE --> REVOKED: operator sets is_active=false
    EXHAUSTED --> REVOKED: operator sets is_active=false
    EXPIRED --> CLEANED: cleanup deletes file
    REVOKED --> CLEANED: cleanup deletes file
```

`EXHAUSTED` is `downloads_count >= max_downloads` with `is_active=true`:
no new downloads, but the file stays on disk until the TTL ends so the
last download can finish.

Cleanup is owned by the **cleanup container** on NL-2 (see
[`20-deployment.md`](20-deployment.md)). It runs on a
schedule and:

1. `temp_links_repo.deactivate_expired()` — flips `is_active=false` for
   `expires_at < now()`.
2. `temp_links_repo.list_inactive_with_files(limit=500)` — returns rows
   where the file should be removed.
3. For each, `Path(link.file_path).unlink(missing_ok=True)`.
4. Optionally deletes the row (or keeps for audit; see config).

The exact retention policy lives in `app/workers/cleanup/runner.py` and is
covered in [`09-queue-and-workers.md`](09-queue-and-workers.md).

---

## 7. Telegram upload routing

`DeliveryService._upload_one` selects the right send method based on
`MediaKind`:

| `MediaKind` | Telegram method | Notes |
|---|---|---|
| `VIDEO` | `sendVideo` | uses `supports_streaming=True`, attaches duration when known |
| `AUDIO` | `sendAudio` | sets title/performer when present |
| `PHOTO` | `sendPhoto` | scaled by Telegram automatically |
| `GALLERY` (per item) | inferred from extension | rare; usually `PHOTO`/`VIDEO` per file |
| anything else | `sendDocument` | safe fallback |

For multi-file galleries we deliberately *do not* use `sendMediaGroup` in
the baseline. Per-file uploads are easier to debug and to retry. The
caption is sent as a separate text message after all files.

The Telegram limit is honoured via
`Settings.telegram_max_upload_bytes` (defaults to 50 MiB). If your bot
runs in a self-hosted Bot API server, raise this in `Settings` to up to
2 GiB and re-deploy.

---

## 8. Failure paths

| Failure | Behaviour |
|---|---|
| Telegram upload fails (`Timeout`, `5xx`) | Sender raises; use case logs and proceeds to fail the job (no automatic fallback to link) |
| Temp link issued but worker crashes before `mark_done` persists the `DeliveryOutcome` | The `temp_links` row exists, but `download_jobs.public_url` may still be NULL. Cleanup will reap it. Re-run the job or inspect `temp_links.job_id` during incident triage. |
| User shares link in a group; 6 people open it | First 5 succeed; the 6th gets `410 Gone`. Configurable via `TEMP_LINK_MAX_DOWNLOADS` |
| File deleted between issue and serve | `410 Gone` with "File no longer available". Logged at INFO |
| `ensure_within` rejects `file_path` | `403 Forbidden`. Logged at ERROR — investigate immediately |

---

## 9. Security model — recap

1. **Random tokens** (256-bit entropy).
2. **Short TTL** (hours, configurable).
3. **Bounded uses** (default 5).
4. **Path validation** (`ensure_within(STORAGE_PATH, file_path)`).
5. **Internal-only file serving** (`internal;` in nginx).
6. **No directory listing** (nginx default; we never enable autoindex).
7. **Forced download** (`Content-Disposition: attachment; filename=...`,
   set once by the API and forwarded through `X-Accel-Redirect`).
8. **No referer or auth required** — the token *is* the credential.
9. **No HTTP caching** — `Cache-Control: no-store` (+ `Pragma: no-cache`
   + `Expires: 0`) is set explicitly by nginx on both `/d/` and
   `/_protected/` so a single-use, time-bounded file is never held by
   intermediaries or the user-agent. See `deploy/nginx/conf.d/media.conf.template`
   and [`17-security.md`](17-security.md) §5.
10. **TLS only** — port 80 redirects to 443; `/d/` is reachable only on
    443.
11. **Per-IP rate limiting on `/d/`** — `limit_req` (10 r/s, burst 20
    nodelay) + `limit_conn` (8 concurrent) declared in
    `deploy/nginx/nginx.conf` and applied in `media.conf.template`.
    Rejected requests get **429 Too Many Requests**, which is logged
    in the JSON access log under `limit_req_status`. See
    [`17-security.md`](17-security.md) §2 row "Token guessing".

---

## 10. Anti-patterns

1. **Bypass nginx for "convenience" in production.** The
   `X-Accel-Redirect` path is the secure, performant path. Direct
   `FileResponse` is for development only.
2. **Make tokens deterministic** (e.g. `hash(job_id+secret)`). One leak =
   total compromise of historical tokens.
3. **Log full tokens.** Use `token[:8] + "..."` everywhere.
4. **Reuse the same token for "the same file" across users.** Tokens are
   per-issue. Cross-user reuse breaks the audit trail and the counter.
5. **Set `TEMP_LINK_MAX_DOWNLOADS = 1` "for security".** The user
   themselves often opens the link twice (preview + browser). Five is the
   tested default; if you need stricter, also extend the message text to
   warn the user.
6. **Add an `/list` endpoint.** Tempting, but no listing — period.

---

## 11. Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Worker writes outside `STORAGE_PATH` | `403 Forbidden` on `/d/<token>` | Always pass `LocalStorage.job_dir(job_id)` to the provider |
| Nginx alias path mismatched with worker mount | `404` from nginx after API returns `200` | Ensure both containers mount the same path with the same uid/gid |
| Counter not incrementing | Off-by-one on retries | Confirm `TempLinksRepository.try_register_use(token)` performs the atomic `UPDATE ... RETURNING`; add an integration test |
| Cleanup container disabled | Disk fills up | Re-enable; alert on disk > 80% |
| `PUBLIC_BASE_URL` does not match cert SAN | TLS error in browser | Re-issue cert with the right `--domains` |
| Bot sends link before `mark_done` persisted | If worker crashes between, message exists but DB lacks `public_url` | Known current ordering: `DeliveryService` sends, then `ProcessDownloadUseCase` persists the returned `DeliveryOutcome`. During incidents, search by `temp_links.job_id`; do not assume `download_jobs.public_url` is populated. |
| Temp-link URL embedded in **message text** (anchor or plain) | First user click returns 410 "Link expired or exhausted" because Telegram's preview crawler (UA `TelegramBot (like TwitterBot)`) hit `/d/<token>` and consumed slot(s) from `downloads_count` — observed in production 2026-04-26 even with `link_preview_options.is_disabled=True` | Move the URL out of the message body into an inline `url=` button (`InlineKeyboardButton(text="📥 Скачать", url=...)`). Inline URL buttons are not subject to preview generation; the crawler never sees the URL. Keep `link_preview_options(is_disabled=True)` as defense-in-depth. See `DeliveryService._deliver_via_link`. |
| Every Range request charged as a download | Video does not play or the saved file is truncated; a second tap on "📥 Скачать" returns 410 "Link expired or exhausted" although TTL has not passed — observed 2026-09 | Only `GET` from byte 0 spends a slot (`starts_new_download`, §4.1). Check `temp_link_served` logs: `counted=false` lines are continuations. |
| `Content-Disposition` added both by API and nginx | Some browsers abort or behave inconsistently; headers show two `content-disposition` lines | Keep the filename-bearing header in the API response only. `/_protected/` must not add its own `Content-Disposition`. |
| Compression enabled on `/d/` or `/_protected/` | Firefox may show "Corrupted Content Error" / "Ошибка искажения содержимого"; partial body in nginx logs while `curl` works | Keep `gzip off; gunzip off;` in both locations. Verify with `curl --compressed -D - -o /tmp/body.bin https://.../d/<token>`: no `Content-Encoding`, size matches `Content-Length`, `file` identifies the MP4/JPEG/audio. |

---

## 12. Future extension points

- **Per-link auth**: optional Telegram-issued bearer attached to the link
  (`/d/<token>?u=<signed_user_id>`) for stricter sharing. Backwards
  compatible — token alone keeps working.
- **Bytes-served accounting**: Range requests are served (see §4.1);
  per-link `bytes-served` analytics would need nginx log correlation.
- **CDN offload**: front nginx with Cloudflare R2 / similar; signed URLs
  shorten time-to-first-byte. Significant change — design ADR first.

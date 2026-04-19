# ADR-0004 — Temp links delivered via Nginx `X-Accel-Redirect`

- **Status:** Accepted
- **Date:** 2025-01-XX
- **Deciders:** project owner
- **Tags:** delivery, nginx, security, performance

---

## 1. Context

Telegram's Bot API caps file uploads at **50 MB** per file. We must
support files larger than that (HD video for 5 minutes is already
~50 MB). The chosen mechanism (locked decision #7) is to issue a
**temporary HTTPS link** to the user when the file is too large or
when Telegram upload fails.

The question this ADR answers is: **how do we serve those large
files**?

Three implementation styles were on the table:

1. **Direct from FastAPI** (`FileResponse` / `StreamingResponse`).
   Application authenticates the token, then streams the bytes back
   itself.
2. **Nginx `X-Accel-Redirect`.** Application authenticates the token
   and returns an empty 200 with `X-Accel-Redirect: /internal/...`.
   Nginx then serves the file directly from disk.
3. **Pre-signed URLs to an external object store** (S3 / B2).
   Application uploads the file to the store, returns the
   pre-signed URL.

Constraints / forces:

- Files live on **NL-2's local filesystem** (per ADR-0001 and the
  storage strategy).
- We want **token validation** (TTL, max downloads, audit) — the
  application must approve every request.
- We want to **not block FastAPI workers** for the duration of a
  multi-MB / multi-second download.
- We don't want to **stream user files through Python** (memory,
  GIL, slow under load).
- We want to **keep public access surface minimal** — the file
  directory should not be browsable.

Non-goals: multi-region replication; CDN integration (could be a
future addition; layered on top, doesn't change this ADR).

---

## 2. Decision

We deliver large files via **Nginx `X-Accel-Redirect`**:

1. The user requests `https://media.example.com/d/<token>`.
2. Nginx proxies to the FastAPI public endpoint.
3. FastAPI validates the token (`TempLinkService.redeem`), records
   audit, increments `downloads_count`, and returns:
   ```http
   200 OK
   X-Accel-Redirect: /internal/<sanitised path>
   Content-Disposition: attachment; filename="..."
   ```
   with **no body**.
4. Nginx intercepts `X-Accel-Redirect`, opens the file from an
   `internal;` location (not exposed publicly), and streams it to
   the user.

Key configuration points (in `deploy/nginx/conf.d/media.conf.template`,
zones declared in `deploy/nginx/nginx.conf`):
- A `location /_protected/ { internal; alias /var/lib/dwtgbot/storage/; }`
  declared `internal` — only reachable via `X-Accel-Redirect`.
- A `location /d/ { proxy_pass http://dwtgbot_api; }` for the public
  endpoint, gated by `limit_req` (10 r/s, burst 20 nodelay) and
  `limit_conn` (8 concurrent) per `$binary_remote_addr`, returning
  **429** on overflow.
- `Cache-Control: no-store` (+ `Pragma`/`Expires` fallbacks) on both
  `/d/` and `/_protected/` so single-use tokens are never held by
  intermediaries — see [`../17-security.md`](../17-security.md) §6.

---

## 3. Consequences

### 3.1 Positive

- **Application is fast.** Token validation is O(1) DB lookup;
  FastAPI returns immediately after issuing the redirect.
- **Streaming is Nginx's job.** Range requests, sendfile, gzip
  (where applicable), partial downloads — all handled natively.
- **Filesystem is private.** The storage volume is mounted into
  Nginx but the relevant `location` is `internal;` — direct access
  is impossible.
- **Sanity-checked path.** FastAPI computes the `X-Accel-Redirect`
  from the DB row, ensures it's `ensure_within(STORAGE_PATH, ...)`,
  and only then sets the header.
- **One TLS termination, one access log.**

### 3.2 Negative / accepted trade-offs

- **Couples API + Nginx + worker** to a shared volume layout.
  Misconfiguration shows up as a 5xx on first download.
- **Doesn't generalise to multi-host worker** without sharing the
  storage volume (NFS/EBS/CephFS). For now, worker and Nginx run on
  the same host (NL-2) and share a local volume.
- **Range / partial requests inherit Nginx behaviour** — fine for
  static files but not transparent to FastAPI metrics.

### 3.3 Operational impact

- The Nginx config is **part of the contract** with the API; doc
  cross-references in [`10-temp-links-and-delivery.md`](../10-temp-links-and-delivery.md).
- Volume mounts in `deploy/nl2/docker-compose.yml` must keep worker
  and nginx aligned (worker rw, nginx ro).

---

## 4. Alternatives considered

### 4.1 Direct FastAPI streaming (`FileResponse`)
- **Rejected.** Holds a worker for the duration; doesn't scale; no
  built-in concurrency for byte-range requests; loses Nginx's
  optimised `sendfile`.

### 4.2 Pre-signed URLs to external object store
- **Rejected for now.** Adds a new dependency (S3-compatible store,
  credentials, costs); complicates retention semantics (object lifecycle
  vs our `temp_links` table). Feasible as a future delivery channel
  (see [`32-roadmap-and-extension-points.md`](../32-roadmap-and-extension-points.md)
  §1.4) but not the default.

### 4.3 Nginx `auth_request` (full control with sub-request)
- **Rejected.** More moving parts than needed; equivalent security
  posture as `X-Accel-Redirect`.

### 4.4 Public file directory (no app-side auth)
- **Rejected.** Tokens and TTLs are the entire point; can't be
  enforced by Nginx alone (we want audit logging and download
  counting).

---

## 5. Compliance

- Reviewers should reject PRs that:
  - Make the storage `location` non-`internal`.
  - Stream files directly from FastAPI (`FileResponse` /
    `StreamingResponse` of large bodies).
  - Put any `proxy_pass` or `alias` referring to the storage path
    into a public-facing `location` block.
- API endpoint must always go through `ensure_within(STORAGE_PATH, ...)`
  before setting `X-Accel-Redirect`.
- Tests cover token redemption + path validation;
  [`18-testing-strategy.md`](../18-testing-strategy.md) lists the
  relevant cases.

---

## 6. References

- Code: `app/api/public/downloads.py`,
  `app/application/services/temp_link_service.py`,
  `deploy/nginx/sites-available/dwtgbot.conf`,
  `deploy/nl2/docker-compose.yml`.
- Docs: [`10-temp-links-and-delivery.md`](../10-temp-links-and-delivery.md),
  [`11-storage-strategy.md`](../11-storage-strategy.md),
  [`17-security.md`](../17-security.md).
- External: <https://www.nginx.com/resources/wiki/start/topics/examples/x-accel/>.

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2025-01-XX | Accepted | Initial decision, locked at project inception. |

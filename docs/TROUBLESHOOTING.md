# Moved

This file is a redirect. The canonical troubleshooting documentation
lives in the numbered `docs/` series:

- [`docs/24-runbooks.md`](24-runbooks.md) — operational runbooks for the
  20 most common production incidents (symptoms → causes → diagnosis →
  fix → verify → prevent), plus emergency checklist and quick commands
  reference (canonical for "something is on fire, what do I do")
- [`docs/31-troubleshooting.md`](31-troubleshooting.md) — deep-dive
  diagnostic guide: how to read logs, how to classify errors
  (provider / network / queue / DB / Telegram / ffmpeg / yt-dlp),
  symptom → cause tables, jq one-liners, SQL diagnostic queries
  (canonical for "why is this happening")
- [`docs/15-healthchecks.md`](15-healthchecks.md) — what `/healthz` and
  `/readyz` actually check
- [`docs/16-error-handling.md`](16-error-handling.md) — `AppError`
  hierarchy and where each layer maps to what

Start the full reading order from [`docs/README.md`](README.md).

> **Why the redirect?** The old top-level `docs/ARCHITECTURE.md`,
> `docs/DEPLOY.md`, and `docs/TROUBLESHOOTING.md` predate the numbered
> series. They are kept only as pointers so existing external links don't
> break. Do not extend them — extend the numbered docs instead.

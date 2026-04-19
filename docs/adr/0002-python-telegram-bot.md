# ADR-0002 — `python-telegram-bot` as the bot framework

- **Status:** Accepted
- **Date:** 2025-01-XX
- **Deciders:** project owner
- **Tags:** bot, framework, dependency

---

## 1. Context

The bot's job is to receive Telegram updates, render inline
keyboards, post messages, and upload media files. We needed a Python
library that:

- Has first-class async / `asyncio` support (the rest of our stack
  is async).
- Is **actively maintained** with a healthy release cadence.
- Has good documentation and a large community (so on-call engineers
  can find help quickly).
- Supports both long-polling and webhooks (we use polling today;
  webhooks are a future option).
- Doesn't impose a heavy framework structure (we have our own
  hexagonal-lite layout).

Candidates evaluated:

- **`python-telegram-bot`** (PTB) — the longest-running and most
  popular library. v20+ is fully async.
- **`aiogram`** — also async-first, route-decorator based, popular
  in the Russian-speaking community.
- **`telethon`** — actually an MTProto client (userbot/bot dual
  use); too low-level for our needs.
- **Raw HTTP against the Bot API** — feasible but reinventing the
  wheel.

Non-goals: switching frameworks "for fun"; supporting MTProto /
userbot mode; multi-account.

---

## 2. Decision

We use **`python-telegram-bot` (PTB) v20+** as the bot framework.

We use it directly:
- `Application.builder().token(...)` to construct the application.
- `CommandHandler`, `MessageHandler`, `CallbackQueryHandler` for
  routing.
- `bot.send_message`, `bot.send_video`, etc. for outbound calls.
- Long polling by default; the architecture leaves room for
  webhooks if/when we want them.

We do **not** wrap PTB in our own facade. Bot handlers may import
from `telegram` and `telegram.ext` directly. The application layer
(`app/application/...`) does not import from `telegram`.

---

## 3. Consequences

### 3.1 Positive

- Mature library, semver releases, large stack of examples.
- Excellent typing in v20+; mypy-friendly.
- Native `asyncio` (no thread shims).
- Reasonable defaults for retries, network timeouts, and update
  parsing.

### 3.2 Negative / accepted trade-offs

- We are **coupled to PTB's surface** in the `app/bot/` package.
  Switching to `aiogram` later would mean rewriting all handlers
  (~moderate effort).
- PTB's `Application` runtime is opinionated; we wrap it lightly in
  `app/bot/application.py`.

### 3.3 Operational impact

- One dependency in `requirements/base.txt`.
- Version bumps occasionally introduce small API changes (handler
  signatures, `ContextTypes`); CI catches them.

---

## 4. Alternatives considered

### 4.1 `aiogram`
- **Rejected.** Comparable feature set; community is fine. Decision
  came down to PTB's larger documentation footprint and the
  preference for an explicit handler-registration style over
  decorator routing.

### 4.2 Raw Bot API client (httpx + handwritten parsing)
- **Rejected.** No upside for a single-product bot; we'd
  reimplement update polling, parser robustness, and retry policy
  from scratch.

### 4.3 `telethon` / userbot
- **Rejected.** Different threat model (TOS, account bans); not
  needed for our feature set.

### 4.4 No Python (Go / Node)
- **Rejected.** The rest of the stack is Python (yt-dlp, alembic,
  arq); switching languages multiplies operational and
  developer-onboarding cost.

---

## 5. Compliance

- Reviewers should reject PRs that:
  - Import `telegram` outside `app/bot/` or `app/infrastructure/telegram/`.
  - Bypass PTB to do raw HTTP against `api.telegram.org`
    (except in `TelegramSender` if absolutely necessary, with
    rationale).
- Bumps of PTB go through normal dependency-update CI.

---

## 6. References

- Code: `app/bot/application.py`, `app/bot/handlers/*.py`,
  `app/infrastructure/telegram/sender.py`.
- Docs: [`06-bot-flow.md`](../06-bot-flow.md).
- External: <https://docs.python-telegram-bot.org>.

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2025-01-XX | Accepted | Locked at project inception (decision #2 in the locked-decisions list). |

# Architectural Decision Records (ADRs)

> Status: Stable
> Audience: contributors, AI agents, reviewers

This folder stores **Architectural Decision Records** — short
markdown files documenting non-trivial architectural choices.

## Why ADRs

When someone (human or AI) asks "why is this done that way?", the
answer should be a file you can link to, not folklore. ADRs preserve
**context** (what we knew at the time), **the decision**, and
**consequences** (what we accept).

Without ADRs, the same arguments are re-litigated every six months
and well-meaning agents revert hard-won decisions because the
rationale has been lost.

## When to write an ADR

Write an ADR **before** making the change when:

- You're changing a [locked decision](0005-locked-architectural-assumptions.md#2-decision).
- You're picking between options with significant trade-offs (no
  obviously-best choice).
- You're introducing a major dependency (DB, queue, framework).
- You're changing a public contract (callback data format, temp link
  token format, env var meaning).
- A reviewer asks "why this and not that?" — write the ADR before
  answering.

You do **not** need an ADR for:

- Bug fixes.
- Refactors that don't change architecture.
- Adding a new provider/handler/option following an existing pattern.
- Style/naming choices already covered in
  [`27-coding-standards.md`](../27-coding-standards.md).

## Lifecycle / status

Each ADR has a `Status` field. Allowed values:

| Status | Meaning |
|---|---|
| `Proposed` | Drafted, under review |
| `Accepted` | Decision is in effect |
| `Superseded by ADR-XXXX` | A newer ADR replaces it |
| `Deprecated` | No longer applies, no replacement |
| `Rejected` | Considered and explicitly turned down |

Status changes are themselves part of the record — never delete an
ADR; supersede it.

## Naming convention

```
docs/adr/NNNN-short-kebab-title.md
```

- `NNNN` = zero-padded sequential number (`0001`, `0002`, …).
- Kebab-case title, ≤ 60 chars.
- The number is allocated by the next available integer at PR time.

## Template

Copy [`0000-template.md`](0000-template.md) and fill it in.

## Index of accepted ADRs

| ID | Title | Status |
|---|---|---|
| [0001](0001-two-server-topology.md) | Two-server topology (control plane / media plane) | Accepted, amended by 0011 |
| [0002](0002-python-telegram-bot.md) | `python-telegram-bot` as the bot framework | Accepted |
| [0003](0003-ruff-format-no-black.md) | `ruff format` (no `black` directly) | Accepted |
| [0004](0004-temp-links-via-nginx-x-accel.md) | Temp links delivered via Nginx `X-Accel-Redirect` | Accepted |
| [0005](0005-locked-architectural-assumptions.md) | **Locked architectural assumptions (canonical register)** — the 12 foundational decisions | Accepted |
| [0006](0006-in-process-rate-limit-metrics-exporter.md) | In-process Prometheus exporter for rate-limit metrics | Accepted |
| [0007](0007-job-queue-worker-metrics.md) | Job / queue / worker Prometheus metrics (3-process exporter) | Accepted |
| [0008](0008-audit-top5-fixes.md) | Audit Top-5 fixes — retry semantics, atomic per-user cap, log redaction, immutable image tags, atomic temp-link counter | Accepted |
| [0009](0009-python-314-runtime.md) | Raise the language floor to Python 3.14 (supersedes ADR-0005 row 1) | Accepted |
| [0010](0010-instant-download-ux.md) | Instant-download UX and progress side-channel (single-message flow, Redis Pub/Sub progress) | Proposed |
| [0011](0011-single-server-topology.md) | Односерверная топология `single` рядом с `split` (supersedes ADR-0005 rows 2–4, amends ADR-0001) | Accepted |
| [0012](0012-application-ports-media-sender-storage.md) | Порты `MediaSender` / `MediaStorage`: слой application без импортов infrastructure и `telegram` | Accepted |

> 🔒 **Start here when in doubt:**
> [ADR-0005](0005-locked-architectural-assumptions.md) is the canonical
> register of locked decisions. Any change touching one of the twelve
> rows in its §2 table requires a new ADR that explicitly supersedes
> the relevant row.

## Process

1. Copy the template into a new file (next NNNN).
2. Fill in Context / Decision / Consequences / Alternatives.
3. Set `Status: Proposed`.
4. Open a PR. Discussion happens on the PR.
5. On merge, set `Status: Accepted`.
6. Update the index above.
7. If a future ADR overturns this one, set
   `Status: Superseded by ADR-XXXX` here, and link from the new ADR.

## Anti-patterns

1. **Editing an Accepted ADR to "fix" the decision.** Write a new
   ADR that supersedes it.
2. **Deleting an ADR.** History matters; even rejected/superseded
   ADRs explain *why we don't do X anymore.*
3. **Vague ADRs ("we should be more cloud-native").** ADRs cover
   one concrete decision each.
4. **ADRs without consequences.** The "what we accept" section is
   the most useful part. Don't skip it.

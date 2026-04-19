# Moved

This file is a redirect. The canonical deployment documentation lives in
the numbered `docs/` series:

- [`docs/20-deployment.md`](20-deployment.md) — full NL-1 + NL-2
  walkthrough, server requirements, network plan, env reference,
  startup order, data-safety rules, production checklist, rollback,
  zero-downtime guidance (canonical)
- [`docs/19-docker-architecture.md`](19-docker-architecture.md) —
  containers, images, volumes
- [`docs/21-cicd.md`](21-cicd.md) — GitHub Actions, build & deploy flow
- [`docs/22-backup-restore.md`](22-backup-restore.md) — backup, restore,
  disaster recovery
- [`docs/23-cleanup-retention.md`](23-cleanup-retention.md) — temp
  files, retention, cleanup worker
- [`docs/24-runbooks.md`](24-runbooks.md) — operational runbooks for 20
  failure scenarios (replaces the old hardening / DR sections)

Start the full reading order from [`docs/README.md`](README.md).

> **Why the redirect?** The old top-level `docs/ARCHITECTURE.md`,
> `docs/DEPLOY.md`, and `docs/TROUBLESHOOTING.md` predate the numbered
> series. They are kept only as pointers so existing external links don't
> break. Do not extend them — extend the numbered docs instead.

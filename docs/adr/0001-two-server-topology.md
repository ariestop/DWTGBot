# ADR-0001 — Two-server topology (control plane / media plane)

- **Status:** Accepted, amended by [ADR-0011](0011-single-server-topology.md)
- **Date:** 2025-01-XX
- **Deciders:** project owner
- **Tags:** topology, deployment, security, isolation

> ADR-0011 добавляет вторую поддерживаемую топологию `single` (все
> сервисы на одном хосте). Всё, что описано ниже, остаётся в силе как
> описание топологии `split` и целевого состояния после роста. Отказ от
> однсерверного варианта из §4.1 пересмотрен в ADR-0011 с мерами защиты.

---

## 1. Context

DWTGBot has two very different workloads under one product:

1. **Control plane** — the always-on, low-CPU, high-availability
   bot process; the queue (Redis); the system-of-record (PostgreSQL);
   periodic backups.
2. **Media plane** — the bursty, IO + CPU heavy workload: yt-dlp
   downloads, ffmpeg muxing, large file storage, public HTTPS
   delivery via Nginx + Certbot, periodic cleanup.

These workloads have different operational profiles:

| Concern | Control plane | Media plane |
|---|---|---|
| CPU burst | low | high (ffmpeg) |
| Disk usage | tiny (<1 GB) | large (10s–100s GB) |
| Public exposure | none | 80/443 inbound |
| Failure blast radius if compromised | catastrophic (DB, secrets, queue) | bounded (file cache + bandwidth) |
| Restart impact | bot offline | jobs delayed (recoverable) |

We also wanted a **clear security perimeter**: the only host the
public touches should not be the host that holds the data.

Non-goals: high availability across regions; multi-tenant SaaS;
zero-downtime deploys.

---

## 2. Decision

We deploy DWTGBot across **two servers** ("NL-1" and "NL-2") on a
**private peer network**:

- **NL-1 (control plane)**: `bot`, `redis`, `postgres`, `backup`.
  No public inbound; all egress allowed (Telegram API, package
  registries).
- **NL-2 (media plane)**: `worker`, `nginx`, `certbot`, `cleanup`,
  storage volume. Public inbound only on 80/443.

Communication NL-1 ↔ NL-2:
- Worker connects to NL-1's Redis and Postgres over the **private**
  interface only.
- No app traffic from NL-2 to NL-1's public IP.

Containerised with Docker Compose; a separate `docker-compose.yml`
per host.

---

## 3. Consequences

### 3.1 Positive

- **Security perimeter:** the host on the public Internet (NL-2)
  doesn't store user identifiers, secrets, or the queue. A
  compromise of NL-2 leaks at most cached files — never the DB.
- **Resource isolation:** ffmpeg can saturate CPU/disk on NL-2
  without impacting bot responsiveness on NL-1.
- **Independent scaling:** add a second NL-2 if downloads dominate;
  add memory to NL-1 if the queue grows.
- **Independent restart:** restarting Nginx / worker doesn't bounce
  the bot.

### 3.2 Negative / accepted trade-offs

- **More hosts to operate.** Two `docker-compose.yml`s, two
  `.env` files, two firewalls.
- **NL-1 ↔ NL-2 network is now a dependency.** If the link is down,
  jobs stall (queue grows; DB writes still happen on NL-1 from the
  bot side; processing pauses).
- **Operator must keep storage volumes consistent** between worker
  and Nginx on NL-2 (both mount `/var/lib/dwtgbot/storage`).
- **Secret duplication:** `BOT_TOKEN` is needed on both hosts (NL-1
  for polling, NL-2 worker for sending media uploads).

### 3.3 Operational impact

- Two install passes for `deploy/scripts/install.sh` (one per host).
- Two `firewall_setup.sh` invocations with different rule sets.
- Backups run on NL-1; off-site copies are an operator concern (see
  [`22-backup-restore.md`](../22-backup-restore.md)).

---

## 4. Alternatives considered

### 4.1 Single host
Everything on one server.
- **Rejected.** Public Nginx and the database co-resident violate
  the principle of least exposure. Heavy ffmpeg / yt-dlp workloads
  would degrade bot responsiveness.
- **Reconsidered in [ADR-0011](0011-single-server-topology.md):**
  допустимо для малого масштаба при условиях — у Postgres/Redis нет
  портов на хосте, nginx в отдельной сети без маршрута к данным,
  cgroup-веса для worker, обязательные `STORAGE_MIN_FREE_MB` и offsite
  бэкапов.

### 4.2 Three or more hosts (separate worker, separate nginx)
- **Rejected for now.** Adds operational complexity without a
  current win. Splitting `worker` from `nginx` is a future
  optimisation if disk-bandwidth saturation becomes a problem;
  doable without changing this ADR's perimeter.

### 4.3 Kubernetes
- **Rejected.** Out of scope (see also `32-roadmap-and-extension-points.md`
  §3). Compose meets requirements with a fraction of the operational
  surface.

### 4.4 Serverless / managed services
- **Rejected.** yt-dlp + ffmpeg with multi-minute jobs and
  unpredictable CPU profile maps poorly to FaaS. Egress + storage
  costs would dominate.

---

## 5. Compliance

- Reviewers should reject PRs that:
  - Open public ports on NL-1 (`deploy/nl1/docker-compose.yml`).
  - Move stateful services (Postgres / Redis) to NL-2.
  - Co-locate Nginx with the DB.
- `docs/02-architecture.md`, `docs/19-docker-architecture.md`,
  `docs/20-deployment.md` must reflect the topology.

---

## 6. References

- Code: `deploy/nl1/docker-compose.yml`, `deploy/nl2/docker-compose.yml`,
  `deploy/scripts/install.sh`, `deploy/scripts/firewall_setup.sh`.
- Docs: [`02-architecture.md`](../02-architecture.md),
  [`17-security.md`](../17-security.md),
  [`19-docker-architecture.md`](../19-docker-architecture.md),
  [`20-deployment.md`](../20-deployment.md).

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2025-01-XX | Accepted | Initial topology, locked at project inception. |
| 2026-09-24 | Amended | [ADR-0011](0011-single-server-topology.md): `single` added as a supported topology; this ADR now describes `split`. |

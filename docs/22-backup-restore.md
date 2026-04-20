# 22 — Backup & Restore

> Status: Stable
> Audience: SRE, DBA, on-call, AI agents writing recovery automation
> Companion docs:
> [`19-docker-architecture.md`](19-docker-architecture.md) — what runs,
> [`20-deployment.md`](20-deployment.md) — host setup,
> [`21-cicd.md`](21-cicd.md) — release pipeline,
> [`23-cleanup-retention.md`](23-cleanup-retention.md) — storage cleanup loop,
> [`24-runbooks.md`](24-runbooks.md) — on-call playbooks,
> [`31-troubleshooting.md`](31-troubleshooting.md) — diagnosis.

End-to-end backup and restore for DWTGBot: what to back up, how
the automation works, where dumps live, retention, restore
procedures, partial restores, disaster recovery, and a one-page
emergency checklist. Bash commands, ordered, no fluff.

> 🔒 **Locked decisions** (ADR-0005):
> - **PostgreSQL is the source of truth.** Backup it nightly.
> - **Storage volume** holds large media files; treated as
>   regenerable cache, but operators can opt-in to volume backups.
> - **Backup script**: `deploy/scripts/backup.sh`
>   (`pg_dump --format=plain | gzip`).
> - **Restore script**: `deploy/scripts/restore.sh` (stops bot/api,
>   `DROP/CREATE DB`, loads dump, restarts).
> - **Retention**: `BACKUP_RETENTION_DAYS` (default 14 days),
>   pruned by `find -mtime`.

---

## §1 — What to back up

### 1.1 Inventory

| Class | Object | Location | Backed up by | RPO | RTO |
|---|---|---|---|---|---|
| **Critical** | `dwtgbot` Postgres database | NL-1 `postgres_data` volume | `backup.sh` (automatic) | `BACKUP_INTERVAL_SECONDS` (default 24 h) | minutes (run `restore.sh`) |
| **Critical** | `.env` files (NL-1, NL-2) | hosts: `/opt/dwtgbot/deploy/{nl1,nl2}/.env` | operator (secrets manager) | when changed | seconds |
| Important | TLS certs | NL-2 `letsencrypt_conf` volume | regenerable via certbot | n/a | minutes |
| Important | App config in repo (compose, nginx, scripts) | git | git itself | n/a | seconds |
| Optional | Storage volume (in-flight media + temp links) | NL-2 `/var/lib/dwtgbot/storage` | operator (rsync / snapshot) | as scheduled | depends on size |
| Optional | Redis state (queue + dedup + cache) | NL-1 `redis_data` volume | regenerable; AOF persists across restart but not host loss | n/a | minutes (workers re-process) |

### 1.2 Why each tier

- **Postgres = source of truth.** Loss = lost job history, lost `temp_links` rows (orphan files on disk), lost `media_cache`, lost audit log. **Backed up nightly, off-host copy mandatory.**
- **`.env` files contain irreplaceable secrets** (`BOT_TOKEN`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `API_INTERNAL_TOKEN`). Without them you cannot restore service even if you have the DB dump. **Store in a secrets manager** (Vault / SOPS / cloud KMS) — `.env` on the host is a working copy.
- **Storage volume** holds files that are **regenerable** (re-download from upstream). Live `temp_links` rows referencing missing files turn into 410 — acceptable. Backup only if your SLA requires preserving outstanding links across host loss.
- **Redis** is regenerable too: the worker queue and the dedup map can be lost without permanent harm. Users will re-submit URLs.
- **TLS certs** are regenerable via certbot in seconds; no need to back them up.

### 1.3 What to **never** back up

| Object | Why not |
|---|---|
| Bot session files / `getUpdates` offset | Telegram remembers; backup would replay old updates |
| `STORAGE_TMP_PATH` content | partial downloads; replaying corrupts state |
| `arq` in-flight job blobs in Redis | dedup keys would resurrect canceled jobs |
| Container filesystem (anything outside named volumes) | ephemeral by design |
| Docker images themselves | rebuild from git + GHCR |

---

## §2 — Backup script anatomy

`deploy/scripts/backup.sh` is the canonical entrypoint. One file, two run modes, deterministic naming, retention pruning.

### 2.1 Two run modes

```mermaid
flowchart TD
  Start[backup.sh] --> Q{POSTGRES_HOST set\nand != "postgres"?}
  Q -- yes --> A[run_in_container_mode\npg_dump over network]
  Q -- no  --> B{docker found and\ndeploy/nl1/docker-compose.yml exists?}
  B -- yes --> C[run_on_host_mode\ncompose exec postgres pg_dump]
  B -- no  --> D[die: missing inputs]
  A --> P[prune_old]
  C --> P
  P --> E[done]
```

| Mode | When used | How it talks to PG |
|---|---|---|
| `run_in_container_mode` | Inside the `backup` container (compose) | `pg_dump -h $POSTGRES_HOST -p ${POSTGRES_PORT:-5432} -U ... -d ...` over the docker network |
| `run_on_host_mode` | Operator running on NL-1 host directly | `compose exec -T postgres pg_dump ...` |

### 2.2 Dump command

```bash
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
  -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  --no-owner --no-privileges --format=plain \
  | gzip -9 > "${BACKUP_DIR}/dwtgbot_${TS}.sql.gz"
```

| Flag | Why |
|---|---|
| `--no-owner` | Restoring into a DB with a different owner doesn't fail |
| `--no-privileges` | GRANTs aren't part of the app contract |
| `--format=plain` | Plain SQL — restored with `psql` (matches `restore.sh`) |
| `gzip -9` | Smaller dumps (DBs are textual; compression ratio is high) |

> If you ever migrate to `--format=custom` (`-Fc`), update `restore.sh` to use `pg_restore` instead of `psql`. Both file format and restore tool must match.

### 2.3 Output naming

```
${BACKUP_DIR}/dwtgbot_${TS}.sql.gz
```

with `TS = $(date -u +'%Y%m%dT%H%M%SZ')`. Example:

```
/var/backups/dwtgbot/dwtgbot_20260419T021500Z.sql.gz
```

UTC + sortable → `ls -1 dwtgbot_*.sql.gz | sort` returns chronological order.

### 2.4 Retention pruning

```bash
find "${BACKUP_DIR}" -type f -name 'dwtgbot_*.sql.gz' \
  -mtime +"${RETENTION_DAYS}" -print -delete
```

Default `BACKUP_RETENTION_DAYS=14`. `mtime +N` means **strictly older than N×24 h**; e.g. with `14` you keep the last ~15 dumps in a 1-per-day cadence.

### 2.5 Failure behaviour

`backup.sh` uses `helpers.sh` which sets `set -Eeuo pipefail` and a `trap ERR`. Failure modes:

| Failure | Visible signal | Effect on disk |
|---|---|---|
| PG unreachable | `pg_dump: error: connection to server …` exit 1 | partial / empty `.sql.gz` may exist |
| Disk full mid-dump | `gzip: write error: No space left on device` | `.sql.gz` partially written |
| Wrong password | `pg_dump: error: connection failed: FATAL: password authentication failed` | no file written |

After any failure, **delete the partial file** (next §6) and investigate before relying on it.

---

## §3 — Backup container and schedule

### 3.1 Compose service

`deploy/nl1/docker-compose.yml` defines a long-running `backup` container that loops:

```yaml
backup:
  image: ${IMAGE_BACKUP}
  env_file: .env
  depends_on: { postgres: { condition: service_healthy } }
  volumes:
    - backups:/var/backups/dwtgbot
    - ./scripts:/scripts:ro
  command: ["python", "-m", "app.workers.backup_worker"]
  restart: unless-stopped
```

`app/workers/backup_worker.py` runs `backup.sh` on a Python loop with `BACKUP_INTERVAL_SECONDS` between iterations.

### 3.2 Default cadence

| Setting | Default | Effect |
|---|---|---|
| `BACKUP_INTERVAL_SECONDS` | `86400` | one dump per 24 h |
| `BACKUP_DIR` | `/var/backups/dwtgbot` (mounted from `backups` volume) | host-visible at the volume mount |
| `BACKUP_RETENTION_DAYS` | `14` | last ~14 dumps kept |

### 3.3 Why a container loop, not host cron

| Reason | Explanation |
|---|---|
| Same lifecycle as the app | restart-on-failure, log streaming, health visible via `compose ps` |
| No host-side dependencies | `pg_dump` shipped inside the image; host has no `postgresql-client` |
| Idempotent | restart-safe; missed tick is taken on next boot |
| Logs in the same JSON stream | greppable via the same `jq` recipes (`14-` / `31-`) |

If you prefer host cron, you can disable the container and add:

```cron
15 2 * * * cd /opt/dwtgbot && bash deploy/scripts/backup.sh >>/var/log/dwtgbot-backup.log 2>&1
```

---

## §4 — Where backups live, off-host copies (3-2-1)

### 4.1 On-host storage

| Path (host) | Contents | Owner | Mode |
|---|---|---|---|
| `/var/backups/dwtgbot/` | `dwtgbot_YYYYMMDDTHHMMSSZ.sql.gz` | `root:root` (volume default) | `0700` recommended |

This is mounted into the `backup` container at `/var/backups/dwtgbot`. The actual bytes live in the docker named volume `backups`.

### 4.2 The 3-2-1 rule

> 3 copies, 2 different storage media, 1 off-site.

What you get out of the box: **one copy, on the same host as the database**. That is **not enough** — a host fire ends both. You must add at least an off-host copy.

### 4.3 Off-host copy options

| Option | Setup effort | Safety |
|---|---|---|
| **Built-in `replicate_offsite()` — `aws s3 cp` or `rclone copyto`** | **lowest** (bundled in `docker/backup.Dockerfile`; one env var) | good; fail-open warnings keep local copy authoritative |
| `rsync` to a second host every N hours | low | good (different machine) |
| `restic` / `borg` to S3-compatible storage | medium | excellent (encrypted, deduped, off-region) |
| Cloud snapshot of the `backups` volume | depends on provider | good (vendor-locked) |
| Manual `scp` after each verified deploy | low | poor (operator forgets) |

**Built-in recipe** (S2 audit fix). The `backup` container already runs
every `BACKUP_INTERVAL_SECONDS`; after the local dump lands and the
retention prune runs, `deploy/scripts/backup.sh::replicate_offsite()`
uploads the fresh dump to *one* configured backend. Both `awscli` (v1,
Debian package) and `rclone` are bundled in `docker/backup.Dockerfile`
so the path is non-no-op out of the box.

```dotenv
# deploy/nl1/.env — pick exactly one
BACKUP_S3_BUCKET=my-dwtgbot-backups          # creds: instance profile or env
BACKUP_S3_PREFIX=dwtgbot                     # object-key prefix
# OR
BACKUP_RCLONE_REMOTE=myremote:dwtgbot/backups  # `rclone config` first
```

Both are **fail-open**: if the upload fails the script logs a warning
and returns success — the local dump in `BACKUP_DIR` is authoritative.
A CloudWatch alert on absent `Put-Object` events (or the equivalent
for rclone) is the operator's job.

> **Caveat:** `aws-cli v1` from Debian picks up creds from the
> environment, `~/.aws/credentials`, or an instance profile. Mount
> the creds file read-only into the `backup` container or set
> `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `deploy/nl1/.env`.
> For `rclone`, mount `/home/app/.config/rclone/rclone.conf` from
> outside the image — don't bake secrets into the image.

Minimal `rsync` recipe (host cron on NL-1 every 6 h):

```cron
0 */6 * * * rsync -aHAX --delete /var/backups/dwtgbot/ \
  ops@offsite.example.com:/srv/dwtgbot-backups/ >>/var/log/dwtgbot-rsync.log 2>&1
```

`restic` recipe (recommended):

```bash
# One-time
restic -r s3:s3.amazonaws.com/<bucket>/dwtgbot init

# Backup (cron daily after the local backup loop has run):
restic -r s3:s3.amazonaws.com/<bucket>/dwtgbot \
  --password-file /etc/restic.pass \
  backup /var/backups/dwtgbot \
  --tag postgres --tag nl1
```

Whichever you pick, **verify the off-host copy quarterly** by restoring a random dump into a scratch DB (§12).

### 4.4 Permissions

```bash
sudo chmod 0700 /var/backups/dwtgbot
sudo find /var/backups/dwtgbot -type f -name '*.sql.gz' -exec chmod 0600 {} +
```

Dumps contain everything. They must not be world-readable.

---

## §5 — Retention policy

### 5.1 Defaults

| Where | Default | Knob |
|---|---|---|
| On-host (NL-1) | 14 days, daily cadence ⇒ ≈14 dumps | `BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS` |
| Off-host (your choice) | suggested: 30 days nightly + 12 months monthly + 5 years yearly | restic `forget --keep-…` flags |

### 5.2 Why 14 days locally

- Average dump size is small (< 100 MB even for active deployments).
- 14 days survives a "we noticed corruption late on Friday" recovery.
- Anything longer should live off-host where storage is cheap.

### 5.3 Tiered retention example (off-host)

```bash
restic -r s3:... forget --tag postgres \
  --keep-daily 30 \
  --keep-weekly 12 \
  --keep-monthly 12 \
  --keep-yearly 5 \
  --prune
```

### 5.4 Audit retention

The `audit_logs` table inside the database is **not** subject to backup retention — it's part of the dump. To purge audit history you must `DELETE` rows in PG explicitly; we recommend partition-by-month + drop-old-partition rather than DELETE (see [`12-db-schema.md`](12-db-schema.md) for the schema).

---

## §6 — Manual backup (one-shots & verification)

### 6.1 Trigger one backup right now

```bash
# Via container loop (immediate run)
docker compose -f deploy/nl1/docker-compose.yml exec backup \
  bash /scripts/backup.sh

# OR from the host directly (uses run_on_host_mode)
sudo bash deploy/scripts/backup.sh
```

> Equivalent: `bash deploy/scripts/install.sh` → option **13) Backup database (NL-1)**.

### 6.2 Verify the dump

A backup that doesn't restore is no backup. After every meaningful run, verify:

```bash
DUMP=/var/backups/dwtgbot/$(ls -1t /var/backups/dwtgbot/dwtgbot_*.sql.gz | head -1 | xargs basename)

# 1) File is non-empty and correctly gzipped
ls -la "$DUMP"
gunzip -t "$DUMP" && echo "gzip OK"

# 2) Plain SQL contains expected DDL
gunzip -c "$DUMP" | head -50 | grep -E '^(--|CREATE|SET|ALTER|COPY)' | head -10

# 3) Has data (very rough sanity)
gunzip -c "$DUMP" | wc -l           # expect thousands of lines for a real DB
gunzip -c "$DUMP" | grep -c '^COPY ' # number of table COPY blocks

# 4) Restore into a throwaway PG (safest verification)
docker run --rm -d --name pg_verify -e POSTGRES_PASSWORD=verify postgres:16 >/dev/null
sleep 5
gunzip -c "$DUMP" | docker exec -i pg_verify psql -U postgres -v ON_ERROR_STOP=1
docker exec pg_verify psql -U postgres -c "\dt" | head
docker rm -f pg_verify
```

If step 4 succeeds and the table list looks right, the dump is good.

### 6.3 Partial-file detection

A killed `backup.sh` may leave a partial `.sql.gz`:

```bash
# Find suspect files (no SQL, or unreadable gzip)
for f in /var/backups/dwtgbot/dwtgbot_*.sql.gz; do
  if ! gunzip -t "$f" 2>/dev/null; then
    echo "BAD gzip: $f"
  elif ! gunzip -c "$f" 2>/dev/null | head -3 | grep -q '^--'; then
    echo "BAD content: $f"
  fi
done

# Remove suspect files
sudo rm -i /var/backups/dwtgbot/<bad-file>.sql.gz
```

---

## §7 — Storage files backup (operator-driven)

The storage volume (`/var/lib/dwtgbot/storage` on NL-2) holds files referenced by `temp_links`. By default we treat it as **regenerable cache**: lose it and live links return 410, but the system continues serving new requests.

If your SLA requires preserving outstanding links across host loss, add one of:

### 7.1 `rsync` to off-host

```bash
# On NL-2 (cron, e.g. every hour)
0 * * * * rsync -aHAX --delete \
  /var/lib/dwtgbot/storage/ \
  ops@offsite.example.com:/srv/dwtgbot-storage/ >>/var/log/dwtgbot-storage-rsync.log 2>&1
```

Caveats:
- Files appear and disappear (cleanup loop). `--delete` is fine; you accept "near-real-time" not "transactional".
- Backup is **disk-only**. Without the matching `temp_links` rows the files are unreachable. **Always pair with the PG dump** taken at the same hour.

### 7.2 LVM snapshot (atomic)

```bash
# Provided /dev/vg0/storage is the underlying LV
sudo lvcreate -L 10G -s -n storage_snap /dev/vg0/storage
sudo mkdir -p /mnt/storage_snap
sudo mount /dev/vg0/storage_snap /mnt/storage_snap
sudo tar czf /tmp/storage_$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /mnt/storage_snap .
sudo umount /mnt/storage_snap
sudo lvremove -f /dev/vg0/storage_snap
```

Pros: atomic point-in-time view. Cons: requires LVM under the volume.

### 7.3 Cloud volume snapshot

If NL-2 runs on a cloud provider with volume snapshots, schedule them via the provider's API. Snapshots are usually crash-consistent, which is enough for the storage volume (no transactional writes).

### 7.4 Coordinating with the cleanup loop

The cleanup container deletes files **after** marking the corresponding `temp_links.is_active=false`. To minimize "file gone, link still reachable" windows in your backup:

1. Take the PG dump first.
2. Take the storage snapshot **immediately after**.
3. Restore them as a pair — the dump's `temp_links` view of disk is consistent with the snapshot.

---

## §8 — Env / secrets backup

`.env` files are **not** part of the application backup loop. They live on the hosts and contain irreplaceable secrets. Treat them like SSH keys.

### 8.1 What's in them (recap)

| Var | Recoverable? | Source if lost |
|---|---|---|
| `BOT_TOKEN` | yes | rotate via BotFather (loses no data, but disrupts users) |
| `POSTGRES_PASSWORD` | only if you can reset it on the DB itself | recoverable on a host you still control; otherwise must reset via `ALTER USER` from a privileged login |
| `REDIS_PASSWORD` | yes | rotate, restart Redis with new password, sync `.env` to NL-2 |
| `API_INTERNAL_TOKEN` | yes | rotate; sync to NL-2 |
| `IMAGE_*` tags | yes | from git history / GHCR |
| Anything else | yes | from `deploy/templates/env.template` |

So **the only truly irreplaceable bit is `BOT_TOKEN`** (loss of the token means rotating, which doesn't lose data but is a visible disruption).

### 8.2 Storage options

Pick **one** and never improvise:

| Option | Notes |
|---|---|
| **Vault** (HashiCorp) | gold standard; per-host secret with audit log |
| **SOPS** + age/PGP key in a git repo | text-friendly, reviewable, encrypted at rest |
| **Cloud KMS** + parameter store / secrets manager | works if you're already in one cloud |
| **Encrypted offsite blob** (e.g. `age -e .env -o .env.age`, store the age key separately) | simplest, fits two-host topology |

### 8.3 Minimal recipe (`age` encrypted offsite)

```bash
# One-time: generate the age key
age-keygen -o /etc/dwtgbot/age.key
chmod 0600 /etc/dwtgbot/age.key

# Encrypt envs after every meaningful change
for env in deploy/nl1/.env deploy/nl2/.env; do
  age -r "$(grep -oP 'public key: \K\S+' /etc/dwtgbot/age.key)" \
      -o "${env}.age" "$env"
done

# Push to off-host
scp deploy/nl1/.env.age ops@offsite.example.com:/srv/dwtgbot-secrets/
scp deploy/nl2/.env.age ops@offsite.example.com:/srv/dwtgbot-secrets/

# Decrypt during recovery
age -d -i /etc/dwtgbot/age.key deploy/nl1/.env.age > deploy/nl1/.env
chmod 0600 deploy/nl1/.env
```

The age key (`age.key`) goes into your password manager / safe — not into the same offsite as the encrypted blobs.

### 8.4 Rotation = re-backup

Every time you rotate a secret, **immediately** re-encrypt and re-push. A secrets backup that's a month behind is useless.

---

## §9 — Restore — Database (step-by-step)

### 9.1 The script (`restore.sh`)

```mermaid
flowchart LR
  S[restore.sh dump.sql.gz] --> P[pick dump\ninteractive or arg]
  P --> C[confirm prompt]
  C --> W1[stop NL-2 worker\nvia ssh \$NL2_HOST or operator confirm]
  W1 --> St[stop bot, api, migrate on NL-1]
  St --> D[DROP DATABASE IF EXISTS … WITH FORCE]
  D --> Cr[CREATE DATABASE OWNER user]
  Cr --> L[gunzip | psql -d dwtgbot]
  L --> U[compose up -d bot api on NL-1]
  U --> W2[start NL-2 worker via EXIT trap]
  W2 --> E[done]
```

Key properties:

- **Stops the NL-2 `worker` BEFORE touching the database** — uses `ssh ${NL2_HOST}` if `NL2_HOST` is set; otherwise asks the operator to confirm a manual stop. Aborts before the DROP if the SSH stop fails.
- Stops `bot`, `api`, `migrate` on NL-1 to release connections (otherwise `DROP DATABASE` fails on `database is being accessed`).
- Uses `WITH (FORCE)` to evict any stragglers.
- Restores via plain `psql` (matches `--format=plain` from §2.2).
- Restarts `bot` and `api` after success.
- **Always restarts the NL-2 `worker` via an `EXIT` trap**, even if the restore failed midway — so a failed restore doesn't leave the worker stopped indefinitely.

> **Configuration.** Set these env vars on NL-1 (or in your deploy/SSH wrapper) before running `restore.sh`:
> ```bash
> export NL2_HOST=ops@nl-2.example.com           # required to auto-stop/start NL-2 worker
> export NL2_REPO_PATH=/opt/dwtgbot              # default
> export NL2_SSH_OPTS="-o StrictHostKeyChecking=yes"   # optional, for hardened SSH
> ```
> If `NL2_HOST` is empty the script falls back to an explicit `[y/N]` prompt that requires you to confirm the worker is already stopped.

### 9.2 Standard restore — interactive picker

```bash
# Run on NL-1 host
sudo bash deploy/scripts/restore.sh
```

You'll see:

```
Available backups (newest first):
   1) dwtgbot_20260419T021500Z.sql.gz  (12M)
   2) dwtgbot_20260418T021500Z.sql.gz  (12M)
   ...
Pick a number [1]:
```

Confirm `Y` when prompted. The script will:

1. **Stop NL-2 `worker`** via `ssh ${NL2_HOST}` (or operator confirmation if `NL2_HOST` is unset).
2. Stop `bot`, `api`, `migrate` on NL-1 (≈3 s).
3. `DROP DATABASE … WITH (FORCE)`.
4. `CREATE DATABASE … OWNER ${POSTGRES_USER}`.
5. `gunzip -c $DUMP | psql -d $POSTGRES_DB --quiet`.
6. `compose up -d bot api` on NL-1.
7. **Start NL-2 `worker`** back (via `EXIT` trap, runs even on failure).

> Equivalent: `bash deploy/scripts/install.sh` → option **14) Restore database (NL-1)**.

### 9.3 Standard restore — explicit file

```bash
sudo bash deploy/scripts/restore.sh \
  /var/backups/dwtgbot/dwtgbot_20260419T021500Z.sql.gz
```

Useful for scripting and for restoring a file copied from off-host (`scp` it to `/var/backups/dwtgbot/` or pass any absolute path).

### 9.4 Pre-flight (operator should always do)

Before pressing `Y`:

```bash
# 1. Verify the dump (§6.2)
DUMP=/var/backups/dwtgbot/<file>.sql.gz
gunzip -t "$DUMP" && echo "gzip OK"

# 2. Confirm there's enough disk to hold the new DB
df -h /var/lib/docker /var/backups/dwtgbot

# 3. Take a "right now" dump of the CURRENT state — even if it's broken
sudo bash deploy/scripts/backup.sh
ls -laht /var/backups/dwtgbot | head

# 4. Confirm the NL-2 worker stop path
#    Preferred: configure SSH so restore.sh stops it for you
export NL2_HOST=ops@nl2 NL2_REPO_PATH=/opt/dwtgbot
ssh -o BatchMode=yes "$NL2_HOST" 'docker compose -f $NL2_REPO_PATH/deploy/nl2/docker-compose.yml ps worker'
#    Fallback: stop manually beforehand and answer "y" at the script prompt
#    ssh "$NL2_HOST" "cd $NL2_REPO_PATH && docker compose -f deploy/nl2/docker-compose.yml stop worker"
```

The "right now" dump (step 3) is your bridge back if the restore turns out to be wrong. Step 4 verifies the worker-stop path; the script itself does the actual stop/start (§9.1).

### 9.5 Post-restore (operator should always do)

```bash
# 1. Schema is at the correct revision
docker exec dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c \
  "SELECT version_num FROM alembic_version;"
# Should match the alembic head matching the deployed code

# 2. If the deployed code is NEWER than the dump's schema → run migrations forward
docker compose -f deploy/nl1/docker-compose.yml run --rm migrate \
  alembic upgrade head

# 3. Confirm NL-2 worker is up (script restarts it via EXIT trap; verify here)
ssh "$NL2_HOST" "cd $NL2_REPO_PATH && docker compose -f deploy/nl2/docker-compose.yml ps worker"

# 4. Validate (§12)
```

---

## §10 — Restore — storage files

Files in `/var/lib/dwtgbot/storage/` referenced by `temp_links` rows.

### 10.1 If you have a snapshot or rsync mirror

```bash
# Stop services that read/write the volume
NL2='docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml'
$NL2 stop nginx api worker cleanup

# Restore (rsync example)
sudo rsync -aHAX --delete \
  ops@offsite.example.com:/srv/dwtgbot-storage/ \
  /var/lib/dwtgbot/storage/

# Fix ownership (must be the in-container UID/GID)
sudo chown -R 1000:1000 /var/lib/dwtgbot

# Restart
$NL2 start worker api cleanup nginx
```

### 10.2 If you have only the DB

You can't reconstitute file bytes you don't have. Be honest with users: live `temp_links` will return 410 / 404. Mark them inactive so cleanup proceeds and the system stops trying:

```sql
-- Run on NL-1 postgres
UPDATE temp_links
   SET is_active = false
 WHERE is_active = true
   AND expires_at > now();
```

Then trigger cleanup:

```bash
ssh ops@nl2 'sudo bash /opt/dwtgbot/deploy/scripts/cleanup.sh'
```

### 10.3 Coordination rule

Storage and DB must be restored as a **pair** taken at the same hour. Mismatch causes:

- DB has rows pointing to files that don't exist → 410/404 on link clicks.
- Storage has files with no matching DB row → orphans (cleanup will delete them on next run).

If you can't pair them, the DB wins (mark inactive per §10.2).

---

## §11 — Restore — env / secrets

### 11.1 If you backed up `.env` per §8

```bash
# On the rebuilt host:
sudo install -d -o root -g root -m 0700 /etc/dwtgbot
sudo cp /path/to/age.key /etc/dwtgbot/age.key
sudo chmod 0600 /etc/dwtgbot/age.key

age -d -i /etc/dwtgbot/age.key /srv/dwtgbot-secrets/nl1.env.age \
  | sudo tee /opt/dwtgbot/deploy/nl1/.env >/dev/null
sudo chmod 0600 /opt/dwtgbot/deploy/nl1/.env

# Repeat for NL-2
```

### 11.2 If you don't have a backup

You'll need to **rebuild** the env, which means:

- Re-fetch / re-issue `BOT_TOKEN` (BotFather → "Revoke Token" → use the new one).
- Generate **new** strong values for `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `API_INTERNAL_TOKEN`.
- For `POSTGRES_PASSWORD`: set the new password on the **restored** DB before bringing the bot up:
  ```bash
  docker exec -it dwtgbot_postgres psql -U postgres -c \
    "ALTER USER dwtgbot WITH PASSWORD '<new_strong_password>';"
  ```
  Then put the same value into both `.env` files and restart bot/api/worker.

This is one of the reasons §8 exists — restore-from-scratch is painful without the original `.env`.

---

## §12 — Restore validation

After **any** restore, run the full verification block from `20-deployment.md` §9 (container state, service-level health, end-to-end smoke). Plus, restore-specific checks:

### 12.1 Schema integrity

```bash
docker exec -it dwtgbot_postgres psql -U dwtgbot -d dwtgbot <<'SQL'
-- Tables present
\dt

-- Row counts (rough sanity)
SELECT 'download_jobs' AS t, count(*) FROM download_jobs UNION ALL
SELECT 'media_cache',         count(*) FROM media_cache  UNION ALL
SELECT 'temp_links',          count(*) FROM temp_links   UNION ALL
SELECT 'audit_logs',          count(*) FROM audit_logs;

-- Alembic at HEAD?
SELECT version_num FROM alembic_version;

-- No constraint violations crept in
SELECT n.nspname, c.relname, conname
  FROM pg_constraint co
  JOIN pg_class c ON c.oid = co.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT co.convalidated;
SQL
```

### 12.2 Data sanity

```bash
docker exec -it dwtgbot_postgres psql -U dwtgbot -d dwtgbot <<'SQL'
-- Newest data point should be close to the dump's mtime
SELECT max(created_at) FROM download_jobs;

-- No leftover "running" rows from a previous instance
SELECT status, count(*) FROM download_jobs GROUP BY 1 ORDER BY 2 DESC;

-- Reconcile with disk: how many active temp_links lack a file?
SELECT id, file_path FROM temp_links
 WHERE is_active = true AND expires_at > now()
 LIMIT 50;
SQL
```

For each row in the last query, confirm the file exists on NL-2:

```bash
ssh ops@nl2 'ls -la /var/lib/dwtgbot/storage/jobs/<id>/<file>'
```

If many are missing → see §10.2.

### 12.3 End-to-end

The single non-negotiable smoke is the same as in `20-` §9.3 / `21-` §10.5: paste a URL → small format → file delivered; large format → temp link → file downloaded.

If both legs work, the restore is good.

---

## §13 — Partial restore

Sometimes you don't want the whole DB back — you want one table, one row, or "everything from yesterday but not today's writes". The dump format supports it; the trick is to do it without affecting the live DB.

### 13.1 The pattern: restore into a scratch DB, then SELECT/INSERT

**Never** apply a partial restore directly on the production DB. Instead:

```bash
# 1. Spin up a throwaway PG
docker run -d --rm --name pg_scratch \
  -e POSTGRES_PASSWORD=scratch -p 5433:5432 postgres:16
sleep 5

# 2. Load the dump
DUMP=/var/backups/dwtgbot/dwtgbot_20260419T021500Z.sql.gz
gunzip -c "$DUMP" | docker exec -i pg_scratch psql -U postgres -v ON_ERROR_STOP=1

# 3. Cherry-pick the data you want
docker exec -it pg_scratch psql -U postgres -d postgres -c \
  "\copy (SELECT * FROM download_jobs WHERE id IN ('j_xxx','j_yyy')) TO STDOUT" > rows.tsv

# 4. Apply to production (carefully)
cat rows.tsv | docker exec -i dwtgbot_postgres \
  psql -U dwtgbot -d dwtgbot -c \
    "\copy download_jobs FROM STDIN"

# 5. Tear down the scratch PG
docker rm -f pg_scratch
```

### 13.2 Single table restore

```bash
# Extract just one table's CREATE + COPY block from the dump
gunzip -c "$DUMP" \
  | awk '/^-- Name: temp_links;/{p=1} p; /^-- Name: /{if(NR>1 && !/temp_links/){p=0}}' \
  > /tmp/temp_links.sql

# Inspect
less /tmp/temp_links.sql

# Apply to a scratch DB or to production after dropping the live table (rarely safe)
```

### 13.3 Time-window restore (approximation)

We don't run PITR (point-in-time recovery via WAL archiving) by default. Approximate it:

- Find the dump that's just **before** the bad event.
- Restore it to a scratch DB (§13.1).
- `SELECT` the rows you want from the scratch DB.
- `INSERT` them into production, scoped to the rows that aren't already there (`ON CONFLICT DO NOTHING`).

For real PITR see §20.

### 13.4 Anti-pattern: hand-edited dump

Do not edit `.sql.gz` by hand. The TEXT inside is consistent SQL produced by `pg_dump`; one wrong line and the restore aborts mid-way leaving a broken DB. If you need to filter the dump, do it via a scratch DB (§13.1).

---

## §14 — Golden rules: do not break the running system

### 14.1 The DON'Ts

| ❌ Don't | Why |
|---|---|
| Run `restore.sh` while `bot`/`api` is running | `DROP DATABASE` fails on active connections; even with FORCE, half-baked rollback may corrupt state |
| Run `pg_restore` directly without stopping writers | same as above |
| Restore on NL-1 while NL-2 worker is running | worker reconnects mid-restore and emits cascades of `relation does not exist`; restored state may be polluted by retries |
| `DROP DATABASE` without taking a "now" dump first | no road back if the restore is wrong (§9.4 step 3) |
| Restore an older dump on a newer schema without `alembic upgrade head` afterwards | code expects HEAD; queries fail on missing columns |
| Restore a newer dump on an older schema (downgrade by accident) | same in reverse; sometimes silent because new columns are just ignored on read |
| Use `docker compose down -v` "to clean up" before restore | **deletes the `postgres_data` volume**; you've made things worse |
| Run two `restore.sh` in parallel | races on `DROP/CREATE`; unrecoverable |
| Skip the post-restore smoke (§12) | "I think it worked" is not "it worked" |

### 14.2 The DO's

| ✅ Do | Why |
|---|---|
| Stop NL-2 `worker` before stopping NL-1 `bot/api` | drains in-flight jobs gracefully |
| Take a fresh dump of the **current** state right before restoring | bridge back if the chosen dump is wrong |
| Verify `alembic_version` after restore | confirm schema matches code |
| Run `migrate` forward if needed | dump may pre-date current schema head |
| Restart NL-2 `worker` last | new code talks to consistent DB |
| Document the restore in the team channel + runbooks | future you will thank present you |

---

## §15 — Disaster recovery scenarios

### 15.1 Scenario matrix

| Scenario | Severity | Procedure | RTO (typical) |
|---|---|---|---|
| Bad migration on the running DB | P1 | §15.2 — downgrade or restore from latest dump | 5–20 min |
| Postgres data corruption (single table) | P1 | §13.1 — partial restore from scratch DB | 10–30 min |
| Postgres data loss (full DB) | P0 | §15.3 — restore from latest dump | 15 min |
| `postgres_data` volume lost (e.g. `down -v`) | P0 | §15.3 + worker stop ordering | 15–30 min |
| NL-1 host lost (hardware / cloud incident) | P0 | §15.4 — rebuild NL-1 | 30–90 min |
| NL-2 host lost | P1 (no data loss) | §15.5 — rebuild NL-2 (storage history is sacrificed) | 30–60 min |
| Both hosts lost | P0 | §16 — worst-case bootstrap | 1–4 h |
| `BOT_TOKEN` compromised | P1 | rotate via BotFather; re-deploy both hosts | 10–20 min |
| Off-host backup destination lost | P2 | re-init off-host store; new dump pushes immediately | 5–30 min |
| Storage volume on NL-2 lost (live links die) | P2 | mark `temp_links is_active=false`; users requeue | 5 min |

### 15.2 Bad migration recovery

```bash
# Stop writers FIRST
docker compose -f deploy/nl1/docker-compose.yml stop bot api
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml stop worker'

# Option A: clean downgrade (only if downgrade is implemented & data-safe)
docker compose -f deploy/nl1/docker-compose.yml run --rm migrate \
  alembic downgrade -1

# Option B (preferred when in doubt): restore the dump taken right before deploy
sudo bash deploy/scripts/restore.sh
docker compose -f deploy/nl1/docker-compose.yml run --rm migrate \
  alembic upgrade head     # only if the new schema is intentionally desired

# Image rollback if needed (`21-` §12.2), then restart
docker compose -f deploy/nl1/docker-compose.yml start bot api
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml start worker'
```

### 15.3 Full DB restore (volume lost or corrupted)

```bash
# Stop everything that talks to PG
docker compose -f deploy/nl1/docker-compose.yml stop bot api migrate backup
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml stop worker api'

# If the volume is lost, recreate the postgres container (keeps env, fresh empty volume)
docker compose -f deploy/nl1/docker-compose.yml up -d postgres
docker exec dwtgbot_postgres pg_isready -U dwtgbot -d dwtgbot
# (the entrypoint will create the empty DB; the dump replaces it)

# Restore
sudo bash deploy/scripts/restore.sh
# (or with explicit file:)
sudo bash deploy/scripts/restore.sh /var/backups/dwtgbot/dwtgbot_<latest>.sql.gz

# Validate (§12)
# Bring writers back
docker compose -f deploy/nl1/docker-compose.yml start bot api backup
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml start api worker'
```

### 15.4 NL-1 host lost

1. Provision a fresh Ubuntu 24.04 host with the **same** private IP / DNS / hostname.
2. Run `20-deployment.md` §3 → §4 → §5.1–5.3 (host prep, Docker, env, firewall) using the **restored `.env`** from §11.
3. Pull the latest off-host dump to `/var/backups/dwtgbot/`.
4. `docker compose up -d postgres` (creates empty volume).
5. `sudo bash deploy/scripts/restore.sh /var/backups/dwtgbot/<latest>.sql.gz`.
6. `docker compose run --rm migrate alembic upgrade head` (matches code at the deployed ref).
7. Bring up rest of NL-1: `docker compose up -d`.
8. NL-2 will reconnect automatically (it's pointed at NL-1's private IP via env).
9. Validate (§12).

DNS unchanged. Telegram bot rejoins. Users continue.

### 15.5 NL-2 host lost

1. Provision fresh Ubuntu 24.04, same DNS A/AAAA target.
2. Run `20-deployment.md` §3 → §4 → §6 (NL-2 prep, env from §11, firewall, HTTP, certbot).
3. **Do not** restore storage if you haven't been backing it up. Live `temp_links` rows will return 410; new requests work normally.
   - To suppress repeat 410s, run §10.2 (`UPDATE temp_links SET is_active=false`).
4. New temp links accumulate as users submit URLs.
5. Validate (§12).

DB on NL-1 is **untouched** — no data loss. Only outstanding download files are lost.

---

## §16 — Worst-case recovery (everything lost)

Both hosts gone, fresh hardware. You need:

| Required asset | Stored where (per §8) |
|---|---|
| Latest PG dump | off-host backup (§4.3) |
| `.env` for NL-1 + NL-2 (or the secrets to rebuild them) | secrets manager / `age`-encrypted off-host (§8.2) |
| Git repo + deployed `ref` | GitHub |
| `IMAGE_*` tags in repo | git history |
| `BOT_TOKEN` | BotFather (re-issuable) |

### 16.1 Bootstrap order

```
[ ] 1) Provision NL-1 (fresh Ubuntu 24.04)
[ ] 2) Provision NL-2 (fresh Ubuntu 24.04)
[ ] 3) Install Docker on both (20- §4)
[ ] 4) git clone the repo on both at the deployed ref (20- §3.7)
[ ] 5) Restore .env on both (this doc §11)
[ ] 6) Set up firewall + private network on both (20- §5.3 / §6.3)
[ ] 7) Bring up NL-1 postgres only:
        docker compose -f deploy/nl1/docker-compose.yml up -d postgres
[ ] 8) Pull latest dump to /var/backups/dwtgbot/ (scp/rclone/restic restore)
[ ] 9) Restore the dump:
        sudo bash deploy/scripts/restore.sh /var/backups/dwtgbot/<file>
[ ] 10) alembic upgrade head (forward-fix any schema gap)
[ ] 11) Bring up the rest of NL-1: docker compose up -d
[ ] 12) Bring up NL-2 (HTTP first):
        docker compose -f deploy/nl2/docker-compose.yml up -d
[ ] 13) Reissue TLS:
        sudo bash deploy/scripts/certbot_init.sh --domain media.example.com --email ops@example.com
[ ] 14) Mark old temp_links as inactive (§10.2)
[ ] 15) Validate end-to-end (§12)
[ ] 16) Update DNS only if IPs changed (low-TTL helps)
[ ] 17) Announce; reset the cooldown of monitoring alerts
```

Estimated time: **1–4 hours** depending on host provisioning speed and dump size.

### 16.2 What you can't recover

- Files in storage older than your last storage backup (or all of them if you don't back up storage).
- Live download links not in the dump (anything between the last dump and the disaster).
- In-flight queue jobs (Redis volume gone). Users will need to re-submit URLs they didn't see results for.

Bot continues serving new requests once §16.1 step 11+12 are done.

---

## §17 — Manual restore commands (no scripts)

When `restore.sh` won't run (e.g. helpers broken, Python missing, you're on a recovery host without the repo cloned), do it by hand. This is exactly what the script does.

```bash
# Vars (substitute as appropriate)
DUMP=/var/backups/dwtgbot/dwtgbot_20260419T021500Z.sql.gz
PG_USER=dwtgbot
PG_DB=dwtgbot
PG_PASS='<the_password>'
NL1='docker compose -f /opt/dwtgbot/deploy/nl1/docker-compose.yml'

# 1) Verify the dump
gunzip -t "$DUMP" && echo "gzip OK"

# 2) Stop writers (NL-1 + NL-2)
$NL1 stop bot api migrate backup
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml stop worker api'

# 3) Take a "now" dump of current state (bridge back)
sudo bash /opt/dwtgbot/deploy/scripts/backup.sh \
  || sudo $NL1 exec -T -e PGPASSWORD="$PG_PASS" postgres \
       pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges --format=plain \
       | gzip -9 > "/var/backups/dwtgbot/dwtgbot_PRE_RESTORE_$(date -u +%Y%m%dT%H%M%SZ).sql.gz"

# 4) DROP / CREATE the database
$NL1 exec -T -e PGPASSWORD="$PG_PASS" postgres \
  psql -U "$PG_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS \"$PG_DB\" WITH (FORCE);"
$NL1 exec -T -e PGPASSWORD="$PG_PASS" postgres \
  psql -U "$PG_USER" -d postgres -c \
    "CREATE DATABASE \"$PG_DB\" OWNER \"$PG_USER\";"

# 5) Load the dump
gunzip -c "$DUMP" \
  | $NL1 exec -T -e PGPASSWORD="$PG_PASS" postgres \
       psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 --quiet

# 6) Verify schema
$NL1 exec -T -e PGPASSWORD="$PG_PASS" postgres \
  psql -U "$PG_USER" -d "$PG_DB" -c \
    "SELECT version_num FROM alembic_version;"

# 7) (Optional) forward-migrate to current code's HEAD
$NL1 run --rm migrate alembic upgrade head

# 8) Restart writers
$NL1 start bot api backup
ssh ops@nl2 'docker compose -f /opt/dwtgbot/deploy/nl2/docker-compose.yml start api worker'

# 9) Validate (§12)
```

---

## §18 — Common mistakes

| # | Mistake | Symptom | Cure |
|---|---|---|---|
| 1 | `down -v` on NL-1 thinking it's a clean restart | DB and queue **gone**; volumes deleted | restore from latest dump (§15.3); never use `-v` on prod |
| 2 | Restored dump older than current code's schema; skipped `alembic upgrade head` | bot logs `column "x" does not exist` | run `alembic upgrade head`; restart bot/api |
| 3 | Restored without stopping bot/api first | `DROP DATABASE … is being accessed` error or partial restore | always stop writers (`restore.sh` does this) |
| 4 | Restored on NL-1 with NL-2 worker running | flood of `relation does not exist` in worker logs during the gap | stop NL-2 worker before, start after |
| 5 | Pre-restore "now" dump skipped | the chosen dump turned out to be wrong; no road back | always take a `now` dump first (§9.4 step 3) |
| 6 | Dump file is partial / 0-byte but still attempted | `psql` aborts with cryptic SQL errors mid-load | `gunzip -t` before restoring (§6.2) |
| 7 | `cron` runs `backup.sh` as a different user → wrong `BACKUP_DIR` perms | dumps not written | run as `root` or fix ownership |
| 8 | Off-host backup is enabled but quarterly verify-restore is skipped | "we have backups" turns out to be useless during DR | schedule the verify (§4.3) |
| 9 | `.env` not backed up; lost during host disaster | can't restart bot — need to rotate `BOT_TOKEN` and reset PG password | implement §8 |
| 10 | Restored a partial dump directly to production (§13.4) | DB in inconsistent state | use scratch DB pattern (§13.1) |
| 11 | Mismatch between PG dump time and storage snapshot time | many `temp_links` point at missing files | snapshot both within minutes; OR mark inactive (§10.2) |
| 12 | Restore script edited locally on host, not in git | next deploy reverts the fix | edit in repo, PR, deploy |
| 13 | Used `docker exec ... pg_dump` instead of `compose exec` (older containers, no auth env) | `password authentication failed` | use the script (it sets `PGPASSWORD` correctly) |
| 14 | "Just restoring one row" with raw SQL on production | typos cascade; transaction kept open and locks build up | scratch DB → cherry-pick → INSERT (§13.1) |
| 15 | Started bot before validating `alembic_version` | runtime errors hours later | run §12.1 first |

---

## §19 — Emergency recovery checklist

Print and pin at the desk. Use during incidents.

```
== INCIDENT TRIAGE ==
[ ] Identify scope: schema-only? data-only? volume-lost? host-lost?
[ ] Pick scenario in §15 matrix; confirm severity and RTO target
[ ] Page on-call per 24-runbooks.md §24 if P0/P1
[ ] Open a comms channel; capture timestamps from now on

== STOP THE BLEEDING ==
[ ] Take a "now" dump of current state (even if it looks broken)
       sudo bash deploy/scripts/backup.sh
[ ] Stop writers in correct order:
       NL-2 worker first, then NL-1 bot/api
[ ] Confirm intended dump exists, is non-empty, gzip-valid (§6.2)
[ ] Confirm enough disk space on NL-1 (`df -h`)

== RESTORE ==
[ ] Run restore.sh (interactive picker OR explicit path)
       sudo bash deploy/scripts/restore.sh
       OR sudo bash deploy/scripts/restore.sh /path/to/dump.sql.gz
[ ] Verify alembic_version (§12.1)
[ ] If schema gap: alembic upgrade head
[ ] Restore storage if applicable (§10) and own the gap if not

== START WRITERS BACK ==
[ ] NL-1: docker compose start bot api backup
[ ] NL-2: docker compose start api worker  (worker LAST)
[ ] Reissue TLS only if NL-2 was rebuilt (certbot_init.sh)

== VALIDATE ==
[ ] §12.1 schema integrity
[ ] §12.2 data sanity
[ ] §12.3 end-to-end smoke (small + large file)
[ ] No new ERROR in logs for 15 minutes (jq histogram)

== AFTERMATH ==
[ ] If temp_links broken: UPDATE temp_links SET is_active=false WHERE … (§10.2)
[ ] Document the restore: dump used, schema rev before/after, downtime window
[ ] Capture knowledge: extend §15 matrix or §18 mistakes table
[ ] Schedule a post-mortem if downtime > 30 min
```

---

## §20 — Future extensions

- **Point-in-time recovery (PITR):** WAL archiving (`archive_command`) + `pg_basebackup` + replay to a chosen LSN. Eliminates the "lost since last dump" gap. Doc + scripts not yet in repo.
- **Streaming replica** (read-only Postgres on NL-2): instant failover candidate; reduces RTO to seconds.
- **Backup-validation CI job:** weekly GitHub Actions workflow restores the latest off-host dump into a scratch DB and runs sanity SQL; alerts on failure.
- **Encrypted dumps at rest:** `pg_dump | age | aws s3 cp -` instead of plain `gzip`.
- **Storage backup automation:** `restic` schedule + per-snapshot `temp_links` row export so storage and DB are time-coupled.
- **Telegram-hosted file IDs as a secondary recovery vector:** every successfully delivered Telegram file gets a Telegram-side `file_id`; storing those alongside `download_jobs` lets the bot re-deliver without re-downloading.

Each crosses an ADR-0005 assumption — propose in
[`26-cursor-rules.md`](26-cursor-rules.md) and add an ADR before
implementing.

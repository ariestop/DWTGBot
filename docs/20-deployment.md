# 20 — Deployment

> Status: Stable
> Audience: SRE, DevOps, on-call, AI agents writing deploy automation
> Companion docs: [`19-docker-architecture.md`](19-docker-architecture.md) — what runs,
> [`21-cicd.md`](21-cicd.md) — how images are built,
> [`22-backup-restore.md`](22-backup-restore.md) — backup/restore details,
> [`23-cleanup-retention.md`](23-cleanup-retention.md) — cleanup loop,
> [`24-runbooks.md`](24-runbooks.md) — incident playbooks,
> [`13-config-and-env.md`](13-config-and-env.md) — full env catalogue.

End-to-end production deployment of DWTGBot. Две топологии
([ADR-0011](adr/0011-single-server-topology.md)):

- **`single`** — всё на одном сервере, стек `deploy/single`. Рекомендуемый
  путь установки: **§4a**. Разделы §1–§4 (требования, подготовка хоста,
  Docker) общие; вместо §5/§6 выполните §4a.
- **`split`** — two servers (NL-1 control plane, NL-2 media plane): §5 и §6.
  Переход `single → split` — [`24-runbooks.md`](24-runbooks.md) §26.

No fluff — bash commands, ordered, with the dependencies and data-safety
rules called out.

> 🔒 **Locked decisions** (ADR-0005, rows 2–4 superseded by ADR-0011):
> - **Topology**: `single` (control + media plane on one host, split by
>   Docker networks) or `split` — NL-1 (bot + Redis + Postgres + backup);
>   NL-2 (worker + Nginx + storage + cleanup + certbot).
> - **Docker Compose ≥ 2.24.0** (`include` in all three stacks).
> - **OS**: Ubuntu 24.04 LTS (`noble`).
> - **Runtime**: Docker + Docker Compose plugin only (no k8s).
> - **Operator surface**: `deploy/scripts/install.sh` — numbered text menu by default; `--whiptail` (or `INSTALL_TUI_MODE=whiptail`) opts into the full-screen dialog UX. Plain stays default so the menu does not redraw over scrolling shell output (logs, healthcheck results) operators are reading.
> - **Idempotent**: any step is safe to re-run.

---

## §1 — Server requirements

### 1.1 Hardware / OS

| Requirement | NL-1 (control plane) | NL-2 (media plane) |
|---|---|---|
| OS | Ubuntu 24.04 LTS (`noble`) | Ubuntu 24.04 LTS (`noble`) |
| Architecture | x86_64 | x86_64 |
| vCPU | 1+ (2+ recommended) | 2+ (4+ recommended for ffmpeg) |
| RAM | 1 GiB+ (2 GiB recommended) | 2 GiB+ (4 GiB recommended) |
| Disk root (`/`) | 10 GiB+ | 10 GiB+ |
| Disk for data | `/var/lib/postgresql` 5+ GiB | `/var/lib/dwtgbot/storage` **50+ GiB** (local SSD strongly preferred) |
| Disk for backups | `/var/backups/dwtgbot` 10+ GiB | n/a |
| Swap | 1 GiB | 2 GiB |
| Public IP | not required | **one** (`:80`/`:443`) |
| DNS A/AAAA | not required | required, points to NL-2 |

**`single`** (один хост):

| Requirement | Минимум | Рекомендуется |
|---|---|---|
| vCPU / RAM | 2 vCPU / 4 GiB (уменьшить `LIMIT_*`, см. `deploy/single/.env.example`) | **4 vCPU / 8 GiB** |
| Disk | 60 GiB | **80+ GiB SSD**; storage лучше вынести на отдельный диск (`/var/lib/docker/volumes`) |
| Swap | 2 GiB | 2 GiB |
| Public IP / DNS | один IP, A/AAAA на него | — |

Сумма лимитов по умолчанию в `single`: worker 2 GiB, Postgres 1 GiB,
Redis 1152 MiB и по 512 MiB у bot, api, backup, cleanup и nginx. Это около
6.6 GiB, поэтому на 4 GiB лимиты нужно уменьшить.

### 1.2 Sizing notes

- NL-2 storage = `(MAX_FILE_SIZE_MB × concurrent jobs × TTL window)` plus headroom for cache. With defaults (2 GiB max file, `WORKER_CONCURRENCY=2`, `TEMP_LINK_TTL_SECONDS=3600`) provision **at least 50 GiB**.
- Postgres on NL-1 stays small — typically <1 GiB even at scale; backups dominate. `/var/backups/dwtgbot` should hold `BACKUP_RETENTION_DAYS × dump_size` plus 30%.
- Don't share NL-1 with other workloads competing for disk I/O — Postgres is sensitive.

### 1.3 Software pre-required

| Tool | NL-1 | NL-2 | Why |
|---|---|---|---|
| `bash` (≥ 4) | ✓ | ✓ | installer + helpers |
| `git` | ✓ | ✓ | repo checkout |
| `curl` | ✓ | ✓ | health, ACME, smoke |
| `jq` | ✓ | ✓ | log triage |
| `openssl` | ✓ | ✓ | secret generation |
| `ufw` (or iptables) | ✓ | ✓ | host firewall |
| `chrony` or `systemd-timesyncd` | ✓ | ✓ | clock sync |

Docker + compose are installed by step §4.

---

## §2 — Network plan

### 2.1 Diagram

```mermaid
flowchart LR
  U[Internet user] -- HTTPS :443 --> N[NL-2 nginx]
  TG((Telegram API)) <-- bot polling --> B[NL-1 bot]
  N -- :8080 --> APIN2[NL-2 api]
  B -- :5432 --> PG[(NL-1 postgres)]
  W[NL-2 worker] -- :5432 (private) --> PG
  W -- :6379 (private) --> RD[(NL-1 redis)]
  B -- :6379 --> RD
  W -- write --> ST[(NL-2 storage)]
  N -- read X-Accel --> ST
  CL[NL-2 cleanup] -- delete --> ST
```

### 2.2 Ports

| Port | NL-1 | NL-2 | Direction | Notes |
|---|---|---|---|---|
| `22/tcp` | ingress | ingress | from admin IPs only | SSH |
| `80/tcp` | — | ingress | public (during ACME) → 301 → 443 |  |
| `443/tcp` | — | ingress | public | TLS |
| `5432/tcp` | ingress | egress | **NL-2 private IP only** | Postgres |
| `6379/tcp` | ingress | egress | **NL-2 private IP only** | Redis |
| `8080/tcp` | — | localhost only | not exposed | API behind nginx |

**Default-deny** everywhere else. `5432`/`6379` MUST NOT be reachable from the public internet.

`single`: открыты только `22` (админ), `80`, `443`. Порты `5432`/`6379` на
хосте не публикуются вовсе, приватная сеть (§2.3) не нужна.

### 2.3 Private network

NL-1 ↔ NL-2 must speak over a private link:

- **WireGuard** (recommended for two hosts; one config file each).
- Cloud provider VPC peering / private subnet.
- Tailscale (works, less control).

Pick **one** and write its CIDR down — it goes into the firewall step (§5.3 / §6.3) and into the Postgres/Redis bind addresses indirectly (they bind on `0.0.0.0` inside the container, but the host firewall restricts the port).

Required reachability (verify after install):

```bash
# From NL-2:
nc -vz <NL1_PRIVATE_IP> 5432
nc -vz <NL1_PRIVATE_IP> 6379
```

### 2.4 DNS

- Set `media.example.com` A (and AAAA) to NL-2's public IP.
- TTL: lower it to 300s **24 hours before** any DNS-affecting change (failover, IP rotation).
- Verify before issuing ACME:

```bash
dig +short media.example.com A
dig +short media.example.com AAAA
```

### 2.5 Egress

| Target | NL-1 | NL-2 | Reason |
|---|---|---|---|
| `api.telegram.org` | ✓ | ✓ | bot polling (NL-1), file send (both) |
| Media providers (`youtube.com`, `instagram.com`, …) | — | ✓ | yt-dlp downloads |
| `acme-v02.api.letsencrypt.org` | — | ✓ | certbot |
| `ghcr.io` (or your registry) | ✓ | ✓ | image pulls |
| `pypi.org`, OS apt mirrors | ✓ | ✓ | image build (CI) and updates |

If you're behind a corporate proxy, set `HTTPS_PROXY` consistently in `/etc/docker/daemon.json` and in each container's env.

---

## §3 — Server preparation

Run these on **both** hosts unless marked otherwise.

### 3.1 Create non-root admin user (skip if you already have one)

```bash
sudo adduser ops
sudo usermod -aG sudo ops
sudo install -d -o ops -g ops -m 0700 /home/ops/.ssh
sudo cp ~/.ssh/authorized_keys /home/ops/.ssh/
sudo chown ops:ops /home/ops/.ssh/authorized_keys
sudo chmod 0600 /home/ops/.ssh/authorized_keys
```

Disable password SSH and root login:

```bash
sudo sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -ri 's/^#?PermitRootLogin.*/PermitRootLogin no/'                /etc/ssh/sshd_config
sudo systemctl reload ssh
```

### 3.2 Time sync (mandatory)

Postgres + Redis + temp-link expiry rely on accurate clocks.

```bash
sudo apt-get update -y
sudo apt-get install -y chrony
sudo systemctl enable --now chrony
chronyc tracking | head -5     # System time should be < 100ms off
timedatectl status             # NTP=active
```

### 3.3 Hostname

```bash
sudo hostnamectl set-hostname nl1     # or nl2
echo "127.0.1.1 $(hostname)" | sudo tee -a /etc/hosts
```

### 3.4 Swap (defensive)

```bash
# 1 GiB on NL-1, 2 GiB on NL-2
sudo fallocate -l 2G /swapfile
sudo chmod 0600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

### 3.5 Kernel sysctls

```bash
sudo tee /etc/sysctl.d/99-dwtgbot.conf >/dev/null <<'EOF'
vm.overcommit_memory = 1
net.core.somaxconn   = 1024
fs.file-max          = 1048576
EOF
sudo sysctl --system
```

`vm.overcommit_memory=1` is the Redis-recommended setting (otherwise BGSAVE may fail under memory pressure).

### 3.6 Data directories

NL-1:

```bash
sudo install -d -o root  -g root  -m 0755 /var/backups/dwtgbot
```

NL-2:

```bash
sudo install -d -o 1000  -g 1000  -m 0755 /var/lib/dwtgbot
sudo install -d -o 1000  -g 1000  -m 0755 /var/lib/dwtgbot/storage
sudo install -d -o 1000  -g 1000  -m 0755 /var/lib/dwtgbot/tmp
```

`1000:1000` is the in-container UID/GID used by worker / api / cleanup. Wrong ownership → silent write failures.

### 3.7 Repo checkout

```bash
sudo install -d -o ops -g ops -m 0755 /opt/dwtgbot
cd /opt/dwtgbot
git clone https://github.com/your-org/DWTGBot.git .
git fetch --all --tags
git checkout v1.0.0     # PIN to a tag or sha — never run :latest blindly
```

**Both hosts check out the same ref.**

---

## §4 — Docker installation

Run on **both** hosts. Use the official `docker.com` repo pinned to `noble`.

```bash
# Remove distro Docker (if any)
sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

# Install prerequisites
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the repo (pinned to noble)
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu noble stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update -y
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# Add ops to docker group (need re-login to take effect)
sudo usermod -aG docker ops

# Sanity (after re-login)
docker version
docker compose version
docker run --rm hello-world
```

> **Equivalent in the installer:** `bash deploy/scripts/install.sh` → option **1) Install Docker**.

Optional but recommended Docker daemon hardening:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}
EOF
sudo systemctl restart docker
```

`live-restore: true` keeps containers running across `docker` daemon restarts.

Проверьте версию Compose: для `include` нужна **≥ 2.24.0**
(`docker compose version --short`). `install.sh` и `deploy_update.sh`
останавливаются с ошибкой, если версия ниже.

---

## §4a — Single-server deployment (`single`, recommended)

Один хост выполняет роли NL-1 и NL-2. Сервисы, образы и healthcheck'и те же,
что в split. Отличается только compose-обвязка
(`deploy/single/docker-compose.yml` = `control.yml` + `media.yml` +
`single.override.yml`), см. [`19-docker-architecture.md`](19-docker-architecture.md) §4.

### 4a.1 Подготовка и `.env`

```bash
cd /opt/dwtgbot
sudo bash deploy/scripts/install.sh
#   1)  Install Docker
#   19) Prepare single server (all-in-one) — второй пункт меню: каталоги
#       backups/storage (uid 1000) и генерация deploy/single/.env (пароли
#       Postgres/Redis, API_INTERNAL_TOKEN, SERVER_NAME / PUBLIC_BASE_URL);
#       в конце предложит cookies Instagram (§4a.5)
#   5)  Configure firewall → single;  6) Start containers;  12) Obtain SSL cert
#   20) Configure cookies (Instagram / YouTube) — замена cookies позже
```

Без меню:

```bash
cp deploy/single/.env.example deploy/single/.env && chmod 0600 deploy/single/.env
# Сгенерируйте секреты так же, как в §5.1.1, но пишите в deploy/single/.env:
#   POSTGRES_PASSWORD, DATABASE_URL (@postgres:5432), REDIS_PASSWORD,
#   REDIS_URL (@redis:6379), API_INTERNAL_TOKEN.
```

Затем задайте в `deploy/single/.env`:

| Variable | Значение в `single` |
|---|---|
| `IMAGE_BOT` / `IMAGE_API` / `IMAGE_WORKER` / `IMAGE_BACKUP` | все четыре, неизменяемые теги `sha-<short>` |
| `APP_ROLE` | `all` (информационное) |
| `BOT_TOKEN`, `BOT_ADMIN_IDS` | как в §5.1 |
| `POSTGRES_HOST` / `REDIS_HOST` | `postgres` / `redis` (имена сервисов) |
| `SERVER_NAME` / `PUBLIC_BASE_URL` | домен и `https://<домен>` |
| `API_VALIDATE_STORAGE` / `XACCEL_ENABLED` | `true` / `true` |
| `STORAGE_MIN_FREE_MB` | **обязательно** (например, `6144`): storage делит диск с Postgres |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` (bot, api, cleanup) |
| `WORKER_DB_POOL_SIZE` / `WORKER_DB_MAX_OVERFLOW` | `10` / `10` (worker; укладывается в `max_connections=100`) |
| `LIMIT_*`, `WORKER_CPU_SHARES` | опционально, см. комментарии в `.env.example` |
| `BACKUP_S3_*` или `BACKUP_RCLONE_REMOTE` | **обязательно**: локальный бэкап лежит на том же диске, что и база ([`22-backup-restore.md`](22-backup-restore.md)) |

Синхронизировать `.env` между хостами (§7.2) не нужно: файл один.

### 4a.2 Firewall, образы, запуск

```bash
sudo bash deploy/scripts/firewall_setup.sh single   # SSH + 80/443; deny 5432/6379
echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin

cd deploy/single
docker compose config -q                 # валидирует include + .env
docker compose pull
docker compose up -d                     # migrate → bot/api → worker/cleanup → nginx
```

Порядок запуска задают `depends_on`: postgres и redis healthy → `migrate`
завершился успешно → bot, api, worker, cleanup → nginx (ждёт healthy api).

### 4a.3 TLS

```bash
sudo bash deploy/scripts/certbot_init.sh   # находит deploy/single/.env сам
```

### 4a.4 Проверка

```bash
bash deploy/scripts/healthcheck.sh single
curl -fsS https://<домен>/healthz
docker compose -f deploy/single/docker-compose.yml --env-file deploy/single/.env ps
```

Дальнейшие обновления — `sudo bash deploy/scripts/deploy_update.sh single`
(pre-backup → pull → `migrate` → `up -d` → healthcheck), в CI — задача
`single` в `deploy.yml` ([`21-cicd.md`](21-cicd.md)).

### 4a.5 Cookies Instagram / YouTube

Instagram показывает серверным IP стену логина даже для публичных постов:
без cookies бот отвечает *«Контент приватный или требует авторизации»*.
YouTube иногда требует то же (*«Sign in to confirm you're not a bot»*,
возрастные ограничения). Решение — файл `cookies.txt` в формате Netscape из
браузера, где выполнен вход. Раздел одинаков для `single` и `split`; в
`split` файл нужен на **обоих** хостах (bot на NL-1 анализирует ссылку,
worker на NL-2 скачивает).

**1. Получить cookies.** Заведите для бота отдельный аккаунт: Instagram
может ограничить аккаунт за автоматические запросы. Войдите в него на
`instagram.com` в обычном браузере и выберите один из способов:

- *Файл целиком (надёжнее).* Расширение «Get cookies.txt LOCALLY»
  (Chrome / Edge / Firefox) на вкладке `instagram.com` → **Export** →
  формат Netscape → `cookies-instagram.txt`.
- *Три значения вручную (только Instagram).* DevTools (F12) →
  Application (Firefox: Storage) → Cookies → `https://www.instagram.com` →
  скопируйте значения `sessionid`, `ds_user_id`, `csrftoken`. Скрипт сам
  соберёт из них файл.

Не нажимайте «Выйти» в этом браузере — выход аннулирует сессию и cookies.
Закройте вкладку. Файл даёт полный доступ к аккаунту: не пересылайте его и
не коммитьте.

**2. Установить на сервер.**

```bash
# (для способа «файл») с вашего компьютера:
scp cookies-instagram.txt root@<server>:/tmp/

# на сервере — или пункт [20] в install.sh:
sudo bash deploy/scripts/cookies_setup.sh instagram     # или youtube
#   1) путь к файлу (по умолчанию /tmp/cookies-<provider>.txt)
#   2) вставить содержимое файла в терминал, завершить строкой END
#   3) ввести sessionid / ds_user_id / csrftoken (только instagram)
# без вопросов: sudo COOKIES_SRC=/tmp/cookies-instagram.txt bash deploy/scripts/cookies_setup.sh instagram
rm -f /tmp/cookies-instagram.txt
```

Скрипт проверяет формат (строки из 7 полей через Tab, домен провайдера,
для Instagram — наличие `sessionid`), кладёт файл в
`/srv/dwtgbot/secrets/cookies-<provider>.txt` с правами `root:1000 0660`,
прописывает `INSTAGRAM_COOKIES_FILE` / `YOUTUBE_COOKIES_FILE` во все
`deploy/*/.env` хоста и пересоздаёт запущенные bot/worker.

Права важны: bot и worker работают под uid/gid 1000, каталог
`/srv/dwtgbot/secrets` должен быть `root:1000 0750`, а файл —
**доступен на запись** группе: yt-dlp сохраняет в него обновлённые cookies.
После ручной правки `.env` нужен `docker compose up -d bot worker`:
`restart` не перечитывает `.env`.

**3. Проверить.** Отправьте боту ссылку на пост или reels, затем:

```bash
docker exec dwtgbot_worker ls -l /srv/dwtgbot/secrets/
docker compose -f deploy/single/docker-compose.yml --env-file deploy/single/.env \
  logs --since 10m bot worker | grep -i cookiefile_missing || echo "no warnings"
```

Cookies живут недели-месяцы. Когда Instagram снова начнёт отвечать «требует
авторизации», повторите шаги 1–2 ([`24-runbooks.md`](24-runbooks.md) §5.4).

Если сессию Instagram не принимает (например, вход был с другого IP и
аккаунт ждёт подтверждения), API с cookies отвечает `HTTP Error 400` или
молчит до таймаута. Бот тогда пишет в лог `instagram_cookies_rejected`,
повторяет запрос анонимно и 10 минут не использует cookies. Увидели это
событие — войдите в аккаунт в браузере, пройдите проверки и повторите
шаги 1–2:

```bash
docker compose -f deploy/single/docker-compose.yml --env-file deploy/single/.env \
  logs --since 1h bot worker | grep instagram_cookies_rejected
```

---

## §5 — NL-1 deployment (control plane, `split`)

NL-1 runs: `postgres`, `redis`, `migrate` (one-shot), `bot`, `api`, `backup`.

### 5.1 Config (env)

```bash
cd /opt/dwtgbot
cp deploy/templates/env.template deploy/nl1/.env

# OR via installer:  bash deploy/scripts/install.sh  →  4) Create .env from template
```

#### 5.1.1 Postgres and Redis — you do **not** install them on the host

On NL-1, **PostgreSQL** and **Redis** are **Docker images** started by
`deploy/nl1/docker-compose.yml`. You never run `apt install postgresql`
or `apt install redis-server` for this stack.

What you **do** set in `deploy/nl1/.env` are:

| Variable | Meaning |
|---|---|
| `POSTGRES_PASSWORD` | A **secret string you invent** (or generate). The `postgres` container uses it on first volume init. It is **not** a password you “get from somewhere” — you choose it once and keep it. |
| `DATABASE_URL` | The **same** password, embedded in the URL the app uses. Format: `postgresql+asyncpg://USER:PASSWORD@postgres:5432/DB` — host **`postgres`** is the Compose service name, not `127.0.0.1`. |
| `REDIS_PASSWORD` | Another secret (can differ from Postgres). If empty, Redis runs **without** `requirepass` (OK for isolated dev; **set a strong password in production** before NL-2 connects). |
| `REDIS_URL` | If `REDIS_PASSWORD` is set: `redis://:PASSWORD@redis:6379/0` (note the `:` before the password). If no password: `redis://redis:6379/0`. |

**Rule:** the password in `DATABASE_URL` after the second `:` must **exactly** match `POSTGRES_PASSWORD`. If you change one, change both.

**First-time recipe** (installer already copied
`deploy/nl1/.env.example` → `deploy/nl1/.env`). Run on the server from the
repo root — it generates secrets and rewrites the four lines (adjust path
if your clone is not `/opt/dwtgbot`):

```bash
cd /opt/dwtgbot   # or: cd /opt/DWTGBot

PG_PASS="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)"
RD_PASS="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)"

# URL-encode safety: the generator above avoids +/= ; if you paste a manual
# password with @ # : / etc., you must percent-encode it inside DATABASE_URL.

sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|"           deploy/nl1/.env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://dwtgbot:${PG_PASS}@postgres:5432/dwtgbot|" deploy/nl1/.env
sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${RD_PASS}|"                 deploy/nl1/.env
sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://:${RD_PASS}@redis:6379/0|"     deploy/nl1/.env

chmod 0600 deploy/nl1/.env
printf '\nSave these for NL-2 (same byte-for-byte):\n  POSTGRES_PASSWORD=%s\n  REDIS_PASSWORD=%s\n' "$PG_PASS" "$RD_PASS"
```

Then set `BOT_TOKEN`, `BOT_ADMIN_IDS`, `API_INTERNAL_TOKEN`, `IMAGE_*`,
`PUBLIC_BASE_URL`, and `docker login ghcr.io` as in the rest of §5.1.

**GHCR image names must be lowercase** — e.g.
`ghcr.io/ariestop/dwtgbot-bot:sha-XXXXXXX`, not `…/DWTGBot-bot:…`, or
`docker pull` fails with `repository name must be lowercase`. CI
(`build-images.yml`) lowercases the repo segment automatically.

Edit `deploy/nl1/.env` (full reference in §7):

```bash
# Required values for NL-1 (paths assume repo at /opt/dwtgbot or /opt/DWTGBot)
sed -i "s|^APP_ROLE=.*|APP_ROLE=bot|"                                      deploy/nl1/.env
sed -i "s|^APP_ENV=.*|APP_ENV=production|"                                 deploy/nl1/.env

# Telegram
sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=<your_botfather_token>|"                  deploy/nl1/.env
sed -i "s|^BOT_ADMIN_IDS=.*|BOT_ADMIN_IDS=11111111,22222222|"               deploy/nl1/.env

# Postgres + Redis: use §5.1.1 one-liner block (sets POSTGRES_PASSWORD,
# DATABASE_URL, REDIS_PASSWORD, REDIS_URL). If you created .env from
# deploy/templates/env.template instead, substitute __PG_PASS__ everywhere:
#   sed -i "s|__PG_PASS__|${PG_PASS}|g" deploy/nl1/.env

API_TOK=$(openssl rand -hex 32)
sed -i "s|^API_INTERNAL_TOKEN=.*|API_INTERNAL_TOKEN=${API_TOK}|" deploy/nl1/.env
# If .env still has __API_TOKEN__ from env.template:
sed -i "s|__API_TOKEN__|${API_TOK}|g" deploy/nl1/.env 2>/dev/null || true

# Public URL of the media plane (NL-2) — must be https:// in production
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://media.example.com|"   deploy/nl1/.env

chmod 0600 deploy/nl1/.env
```

After running §5.1.1, save `PG_PASS`, `RD_PASS`, and `API_TOK` somewhere safe — you'll paste Postgres/Redis passwords into NL-2's `.env` (§6.1) verbatim.

#### 5.1.2 Provider secrets — Instagram cookies (optional but required for IG)

Instagram now serves the login wall to most non-residential egress IPs even
for public posts. Without a cookie file the worker raises
`MediaPrivateError` and users see *"Контент приватный или требует
авторизации"*. The fix is a Netscape `cookies.txt` exported from a
logged-in browser session. The bot (`control.yml`) and the worker
(`media.yml`) bind-mount `/srv/dwtgbot/secrets:/srv/dwtgbot/secrets` (RW),
so the canonical path is `/srv/dwtgbot/secrets/cookies-instagram.txt` on
**both** hosts.

Полная инструкция (как получить cookies, права, проверка) — §4a.5. Для
split выполните её на каждом хосте:

```bash
# на NL-1 и на NL-2 (файл заранее скопирован в /tmp/ через scp)
sudo COOKIES_SRC=/tmp/cookies-instagram.txt bash deploy/scripts/cookies_setup.sh instagram
rm -f /tmp/cookies-instagram.txt
```

Operational notes:

- `deploy_update.sh` runs `ensure_secrets_dir` early; it creates
  `/srv/dwtgbot/secrets` (`root:1000`, mode `0750`) and warns if the cookies file is
  absent — but it never **creates** the file (we never want a secret to
  appear out of thin air on the host).
- Missing file is graceful: the provider logs
  `instagram_cookiefile_missing` and falls back to anonymous fetches.
  Anonymous fetches will fail for IG today, but other providers stay
  unaffected.
- Rotate the file when it expires; see
  [`24-runbooks.md` §5.4 step 4](24-runbooks.md). For the threat model
  and rotation policy see
  [`17-security.md` §7](17-security.md#7-secrets-management).

### 5.2 Validate config

```bash
docker compose -f deploy/nl1/docker-compose.yml config -q && echo OK
```

`config -q` parses the compose file and substitutes env. Any `variable is not set` warning → fix before continuing.

### 5.3 Firewall (NL-1)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp                                 comment 'ssh'
sudo ufw allow from <NL2_PRIVATE_IP> to any port 5432 proto tcp comment 'postgres NL-2 only'
sudo ufw allow from <NL2_PRIVATE_IP> to any port 6379 proto tcp comment 'redis    NL-2 only'
sudo ufw --force enable
sudo ufw status numbered
```

> Equivalent: installer option **5) Configure firewall**.

> **Defence in depth.** Even if you trust the private link, the host firewall is the last line. Never expose `5432` / `6379` to the public internet.

### 5.4 Pull images (deterministic)

```bash
cd /opt/dwtgbot
docker compose -f deploy/nl1/docker-compose.yml pull
```

If you see `manifest unknown` — the tag in `IMAGE_*` does not exist in the registry. Verify and fix the tag.

### 5.5 Start NL-1 — strict order

The compose file declares `depends_on` with `condition: service_healthy`, so a single `up -d` will wait correctly. We list the order **explicitly** here so you understand what's happening (and can recover manually if needed):

| # | Service | Wait until | Why |
|---|---|---|---|
| 1 | `postgres` | `pg_isready` returns 0 | every other service needs DB |
| 2 | `redis` | `PONG` | bot/api/worker enqueue & dedup |
| 3 | `migrate` | exits 0 | schema must match new code |
| 4 | `bot` | healthy | starts polling Telegram |
| 5 | `api` | healthy | internal control plane (used by NL-2 worker / nginx flows) |
| 6 | `backup` | running | periodic dumps |

```bash
cd /opt/dwtgbot
docker compose -f deploy/nl1/docker-compose.yml up -d

# Monitor convergence
watch -n 2 'docker compose -f deploy/nl1/docker-compose.yml ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}"'
```

If `migrate` exits **non-zero**, do not bring `bot`/`api` up. Read the migration error in `docker compose logs migrate` and resolve it (§24-runbooks §17).

> Equivalent: installer option **6) Start containers**.

### 5.6 Verify NL-1

```bash
NL1='docker compose -f deploy/nl1/docker-compose.yml'

# Container status (all healthy / Up)
$NL1 ps

# Postgres + Redis from inside their own containers
docker exec dwtgbot_postgres pg_isready -U dwtgbot -d dwtgbot
docker exec dwtgbot_redis    redis-cli -a "$(grep ^REDIS_PASSWORD deploy/nl1/.env | cut -d= -f2)" ping

# Schema is at HEAD
docker exec dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c "SELECT version_num FROM alembic_version;"

# Bot got the token
docker exec dwtgbot_bot curl -fsS \
  "https://api.telegram.org/bot$(grep ^BOT_TOKEN deploy/nl1/.env | cut -d= -f2)/getMe" | jq '.ok'

# Internal API
docker exec dwtgbot_api curl -fsS http://localhost:8080/healthz
```

All five must succeed before moving to NL-2.

---

## §6 — NL-2 deployment (media plane)

NL-2 runs: `nginx`, `api`, `worker`, `cleanup`, `certbot`, plus storage volumes.

### 6.1 Config (env)

```bash
cd /opt/dwtgbot
cp deploy/templates/env.template deploy/nl2/.env

# Connection back to NL-1's PG and Redis (use private IP)
NL1_IP=10.10.0.1                   # ← put NL-1 private IP here
PG_PASS=...                        # ← from §5.1
RD_PASS=...                        # ← from §5.1
API_TOK=...                        # ← from §5.1

sed -i "s|^APP_ROLE=.*|APP_ROLE=worker|"                                     deploy/nl2/.env
sed -i "s|^APP_ENV=.*|APP_ENV=production|"                                   deploy/nl2/.env

# Same Telegram token (so /sendDocument works from worker via Bot API)
sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=<same as NL-1>|"                           deploy/nl2/.env

# Database (point to NL-1)
sed -i "s|^POSTGRES_HOST=.*|POSTGRES_HOST=${NL1_IP}|"                        deploy/nl2/.env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|"               deploy/nl2/.env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://dwtgbot:${PG_PASS}@${NL1_IP}:5432/dwtgbot|" deploy/nl2/.env

# Redis (point to NL-1)
sed -i "s|^REDIS_HOST=.*|REDIS_HOST=${NL1_IP}|"                              deploy/nl2/.env
sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${RD_PASS}|"                     deploy/nl2/.env
sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://:${RD_PASS}@${NL1_IP}:6379/0|"     deploy/nl2/.env

# Internal API token must match NL-1
sed -i "s|^API_INTERNAL_TOKEN=.*|API_INTERNAL_TOKEN=${API_TOK}|"             deploy/nl2/.env

# Public domain
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://media.example.com|"    deploy/nl2/.env
sed -i "s|^SERVER_NAME=.*|SERVER_NAME=media.example.com|"                    deploy/nl2/.env
sed -i "s|__DOMAIN__|media.example.com|g"                                    deploy/nl2/.env

chmod 0600 deploy/nl2/.env
```

### 6.2 Validate config

```bash
docker compose -f deploy/nl2/docker-compose.yml config -q && echo OK
```

### 6.3 Firewall (NL-2)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp     comment 'ssh'
sudo ufw allow 80/tcp     comment 'http (ACME + 301→https)'
sudo ufw allow 443/tcp    comment 'https'
sudo ufw --force enable
sudo ufw status numbered
```

### 6.4 Confirm reachability to NL-1

```bash
nc -vz "${NL1_IP}" 5432   # postgres
nc -vz "${NL1_IP}" 6379   # redis
```

If either fails, fix the private link / NL-1 firewall (§5.3) before continuing.

> **NL-1 MUST be fully up before NL-2** — NL-2 does not have a
> `migrate` service. The worker connects to NL-1's Postgres over
> WireGuard and expects the schema to already be `alembic upgrade head`.
> If you skip this, worker crash-loops with `relation "download_jobs"
> does not exist`. The §8.1 mermaid enforces the order for a reason.
> A sanity check before continuing to §6.5:
>
> ```bash
> psql "postgresql://dwtgbot:${PG_PASS}@${NL1_IP}:5432/dwtgbot" \
>     -c "SELECT version_num FROM alembic_version;"
> # must print 0002_drop_unused_tables (or newer)
> ```

### 6.5 Pull images and start (HTTP first)

The first nginx start must serve **HTTP only** so certbot can solve the ACME HTTP-01 challenge against the live nginx.

```bash
cd /opt/dwtgbot
docker compose -f deploy/nl2/docker-compose.yml pull
docker compose -f deploy/nl2/docker-compose.yml up -d
```

Startup order (driven by compose `depends_on`):

| # | Service | Wait until | Why |
|---|---|---|---|
| 1 | `api` | healthy | nginx upstream |
| 2 | `worker` | healthy | consumes the queue |
| 3 | `cleanup` | running | safe even if no files yet |
| 4 | `nginx` | healthy on `:80` (no TLS yet) | needed for ACME |
| 5 | `certbot` | one-shot success below | issues TLS cert |

### 6.6 Issue TLS cert

```bash
sudo bash deploy/scripts/certbot_init.sh      # домен из SERVER_NAME, email спросит
# без вопросов: sudo EMAIL=ops@example.com ASSUME_YES=1 bash deploy/scripts/certbot_init.sh
```

The script:
1. Reads `SERVER_NAME` / `DOMAIN` and `EMAIL` from `deploy/single/.env` or `deploy/nl2/.env` — whichever media-plane stack is configured, `single` first (or prompts).
2. **Первый выпуск** (в volume нет `renewal/<domain>.conf`): кладёт
   самоподписанную заглушку, чтобы nginx стартовал с HTTPS-блоком из
   `deploy/nginx/conf.d/media.conf.template`, ждёт nginx на `:80`, удаляет
   заглушку (иначе certbot падает с `live directory exists`) и запрашивает
   настоящий сертификат через webroot (`/var/www/certbot`). Если certbot
   упал, заглушка восстанавливается — nginx переживёт перезапуск.
3. **Повторный запуск** (сертификат уже есть): после подтверждения
   (`ASSUME_YES=1` — без вопроса) делает `--force-renewal` на месте. Каждый
   такой запуск расходует лимит Let's Encrypt на дубликаты (5 в неделю).
4. Reloads nginx.

After that, `:80` redirects to `:443`.

Продление автоматическое: контейнер `certbot` раз в 12 ч запускает
`certbot renew` (обновляет файлы за 30 дней до истечения), а nginx раз в 6 ч
делает `nginx -s reload` и подхватывает новый сертификат. Cron на хосте не
нужен.

> Equivalent: installer option **12) Obtain SSL cert (single / NL-2)**.

### 6.7 Verify NL-2

```bash
NL2='docker compose -f deploy/nl2/docker-compose.yml'

# Containers
$NL2 ps

# Worker is consuming
$NL2 logs --tail=50 worker | jq -c 'select(.event | test("worker_started|worker_job"))' | head

# Public health (TLS)
curl -fsS https://media.example.com/healthz   | jq

# Internal API still reachable (from inside the network)
docker exec dwtgbot_api curl -fsS -H "X-Internal-Token: ${API_TOK}" \
  http://localhost:8080/readyz | jq

# TLS cert sanity
echo | openssl s_client -connect media.example.com:443 -servername media.example.com 2>/dev/null \
     | openssl x509 -noout -dates -issuer -subject
```

---

## §7 — `.env` configuration (canonical reference)

> Authoritative source: [`13-config-and-env.md`](13-config-and-env.md). This section is the **deployment-time** subset — values you must set or sync between hosts.

### 7.1 Canonical variables

Для `single` берите значения из колонки NL-2 для media-переменных и из
колонки NL-1 для control-переменных; `POSTGRES_HOST` / `REDIS_HOST` — имена
сервисов (`postgres` / `redis`). Отличия перечислены в таблице §4a.1.

| Variable | NL-1 | NL-2 | Notes |
|---|---|---|---|
| `APP_ROLE` | `bot` | `worker` | informational; enum `bot` / `api` / `worker` / `all` (`all` в `single`) |
| `APP_ENV` | `production` | `production` |  |
| `LOG_LEVEL` | `INFO` | `INFO` | `DEBUG` only for active triage |
| `LOG_JSON` | `true` | `true` | required for `jq` recipes in `31-` |
| `BOT_TOKEN` | from BotFather | **same value** | |
| `BOT_ADMIN_IDS` | csv | csv | for `/admin` |
| `TELEGRAM_MAX_UPLOAD_MB` | `49` | `49` | hard 50 MB Telegram cap |
| `POSTGRES_HOST` | `postgres` (compose alias) | NL-1 private IP | |
| `POSTGRES_PORT` | `5432` | `5432` | |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | set | **same value** | strong random |
| `DATABASE_URL` | `postgresql+asyncpg://…@postgres:5432/dwtgbot` | …@`<NL1_IP>`:5432/dwtgbot | matches above |
| `REDIS_HOST` | `redis` (alias) | NL-1 private IP | |
| `REDIS_PASSWORD` | set | **same value** | |
| `REDIS_URL` | `redis://redis:6379/0` | `redis://:<pwd>@<NL1_IP>:6379/0` | |
| `STORAGE_PATH` | n/a | `/var/lib/dwtgbot/storage` | bind-mount target |
| `STORAGE_TMP_PATH` | n/a | `/var/lib/dwtgbot/tmp` | |
| `MAX_FILE_SIZE_MB` | n/a | e.g. `2048` | provider sanity gate |
| `PUBLIC_BASE_URL` | `https://media.example.com` | **same** | used to build temp link URLs |
| `SERVER_NAME` | n/a | `media.example.com` | nginx server_name |
| `API_PORT` | `8080` | `8080` | container-internal API listen port; NL-2 nginx upstream is rendered from this env var |
| `TEMP_LINK_TTL_SECONDS` | `3600` | `3600` | |
| `TEMP_LINK_MAX_DOWNLOADS` | `5` | `5` | |
| `TEMP_LINK_TOKEN_BYTES` | `32` | `32` | |
| `API_INTERNAL_TOKEN` | random | **same value** | `X-Internal-Token` header for `/readyz` and `/internal/*` |
| `WORKER_CONCURRENCY` | n/a | `2` | raise carefully (CPU + bandwidth) |
| `JOB_TIMEOUT_SECONDS` | n/a | `1800` | arq hard ceiling |
| `JOB_MAX_RETRIES` | n/a | `2` | retry cap |
| `DOWNLOAD_TIMEOUT_SECONDS` | n/a | `900` | yt-dlp wrapper timeout |
| `CLEANUP_INTERVAL_SECONDS` | n/a | `3600` | every hour |
| `MEDIA_CACHE_TTL_SECONDS` | n/a | `21600` | provider info cache |
| `BACKUP_DIR` | `/var/backups/dwtgbot` | n/a | |
| `BACKUP_RETENTION_DAYS` | `14` | n/a | |
| `BACKUP_INTERVAL_SECONDS` | `86400` | n/a | nightly |
| `BACKUP_S3_BUCKET` / `BACKUP_S3_PREFIX` | optional | n/a | S2 off-site via `aws s3 cp`. `awscli` bundled in `docker/backup.Dockerfile`; AWS creds via instance profile or env. |
| `BACKUP_RCLONE_REMOTE` | optional | n/a | S2 off-site via `rclone copyto`. Any rclone backend. `rclone` bundled in the backup image. Pick exactly one of S3 / rclone. |
| `IMAGE_BOT` / `IMAGE_API` / `IMAGE_WORKER` / `IMAGE_BACKUP` | pinned tag | pinned tag | **never `:latest` in prod** |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_S` / `DB_POOL_RECYCLE_S` | `5 / 10 / 30 / 1800` | **`20 / 20 / 30 / 1800`** on the worker container | S5. NL-2 worker holds a session per active job + cleanup; size the pool ≥ `WORKER_CONCURRENCY × 3`. |
| `STORAGE_MIN_FREE_MB` | `0` | `~3 × MAX_FILE_SIZE_MB` (e.g. `6144`) | S10 disk-space backpressure. NL-2 only; NL-1 bot never writes to storage. |
| `XACCEL_ENABLED` | `false` | `true` | S6 trust gate for `X-Accel-Redirect`. **NL-1 must keep this `false`** — flipping on without nginx in front of the api is a CVE. |
| `ORPHAN_JOB_AGE_SECONDS` | `3900` | `3900` | S1 reaper threshold; **must be ≥ 2 × `JOB_TIMEOUT_SECONDS`**. |
| `HTTPS_PROXY_URL` | optional | optional | S9 outbound proxy for yt-dlp. Empty = direct. |
| `CB_ENABLED` / `CB_FAILURE_THRESHOLD` / `CB_WINDOW_SECONDS` / `CB_COOLDOWN_SECONDS` | `true / 5 / 60 / 300` | `true / 5 / 60 / 300` | L5 circuit breaker for yt-dlp. Redis-shared; leave defaults unless your upstream behaves unusually. |
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE` / `SENTRY_RELEASE` | optional | optional | L7 error aggregation. Blank DSN disables. Pass `SENTRY_RELEASE=$IMAGE_SHA` at deploy time for correlation. |

### 7.2 Sync rules NL-1 ↔ NL-2

These **must** be byte-identical on both hosts (or NL-2 cannot connect):

- `BOT_TOKEN`
- `POSTGRES_PASSWORD`
- `REDIS_PASSWORD`
- `API_INTERNAL_TOKEN`
- `PUBLIC_BASE_URL`
- `SERVER_NAME`

If you ever rotate any of them, change **NL-1 first**, then NL-2, then redeploy NL-2. Otherwise NL-2 will lose connection to PG/Redis.

В `single` синхронизировать нечего: `.env` одна. При ротации пароля
Postgres поменяйте его в базе (`ALTER USER`), затем `POSTGRES_PASSWORD` и
`DATABASE_URL` в `deploy/single/.env`, затем перезапустите стек.

### 7.3 Secret hygiene

| Rule | Why |
|---|---|
| `chmod 0600 deploy/{single,nl1,nl2}/.env` | only the owning user reads them |
| Don't commit `.env` to git | the repo's `.gitignore` already excludes it; verify before pushing |
| Don't `echo` secrets in CI logs | use `::add-mask::` in GitHub Actions |
| Generate with `openssl rand` | `os.urandom`-equivalent entropy |
| Rotate when a contributor leaves | see `24-runbooks.md` §15.4 (token rotation) |

### 7.4 Validate before bringing services up

```bash
docker compose -f deploy/single/docker-compose.yml --env-file deploy/single/.env config -q   # single
docker compose -f deploy/nl1/docker-compose.yml config -q                                   # split
docker compose -f deploy/nl2/docker-compose.yml config -q
```

Each must print nothing on success. CI runs the same check for all three
stacks against their `.env.example` (`compose-validate` job in `ci.yml`).

---

## §8 — Container startup, dependencies, data safety

### 8.1 Strict global order (first-time deploy)

```mermaid
flowchart LR
  A[NL-1 postgres] --> B[NL-1 redis]
  B --> C[NL-1 migrate]
  C --> D[NL-1 bot + api]
  D --> E[NL-2 api + worker + cleanup]
  E --> F[NL-2 nginx HTTP]
  F --> G[NL-2 certbot]
  G --> H[NL-2 nginx HTTPS]
```

**Rule:** **NL-1 always comes up first** on any deploy that touches the schema (i.e. migrations must run before any worker process starts using the new code). For pure media-plane changes (e.g. nginx config) you can update NL-2 alone.

В `single` этот порядок обеспечивает compose: api, worker и cleanup
объявляют `depends_on: migrate: service_completed_successfully`
(`deploy/single/single.override.yml`), поэтому одного
`docker compose up -d` достаточно.

### 8.2 Compose `depends_on` map (what's enforced automatically)

| Service | Depends on (healthy) | Wait time typical |
|---|---|---|
| `migrate` (NL-1) | `postgres` | <2s |
| `bot` (NL-1) | `postgres`, `redis`, `migrate` (exited 0) | <5s |
| `api` (NL-1) | `postgres`, `redis` | <3s |
| `backup` (NL-1) | `postgres` | <1s |
| `api` (NL-2) | (no local deps; pings remote PG) | <5s |
| `worker` (NL-2) | (no local deps; pings remote Redis & PG) | <10s |
| `cleanup` (NL-2) | (no local deps) | <1s |
| `nginx` (NL-2) | `api` healthy | <2s |
| `certbot` (NL-2) | `nginx` reachable on `:80` | one-shot |

If a `depends_on` healthcheck never passes, `up -d` will eventually time out. Read `docker compose logs <svc>` immediately — do **not** restart blindly.

### 8.3 How NOT to break Postgres

| ❌ Don't | ✅ Do |
|---|---|
| `docker compose down -v` on NL-1 | use `down` (no `-v`) — `-v` deletes the `postgres_data` volume = **total data loss** |
| `docker exec postgres pg_dropdb` | use `restore.sh` (it stops bot/api first, then `DROP/CREATE/RESTORE` atomically) |
| Modify `POSTGRES_USER` / `POSTGRES_DB` after first init | the `postgres` image initialises only once; changing them later does nothing — you have to drop the volume (lose data) |
| Mount the `postgres_data` volume read-only | breaks WAL writes |
| Run two `migrate` containers in parallel on the same DB | races and partial migrations |
| Edit `pg_hba.conf` inside the running container | wiped on rebuild |
| Set `statement_timeout` < `JOB_TIMEOUT_SECONDS` | jobs killed mid-write; rows orphaned |
| Restore an old dump on a newer schema without `alembic downgrade` first | `relation does not exist` errors |

Safe shutdown:

```bash
docker compose -f deploy/nl1/docker-compose.yml stop bot api backup
docker compose -f deploy/nl1/docker-compose.yml stop postgres   # last
```

Postgres is `restart: unless-stopped` — it will return on `docker compose start postgres`. Volume is preserved.

### 8.4 How NOT to break Redis

| ❌ Don't | ✅ Do |
|---|---|
| Start Redis without `requirepass` | always set `REDIS_PASSWORD`; bot/api/worker depend on it |
| `FLUSHALL` / `FLUSHDB` to "fix something" | destroys queue + dedup state; in-flight jobs become orphans |
| Mount `redis_data` read-only | RDB snapshots fail; `loading RDB` error after restart |
| Run two Redis containers binding the same port | port clash on host; one starts, other crashes |
| Set `maxmemory-policy` to `allkeys-random` for arq queues | arq depends on its keys; lose them and you lose jobs |
| Disable AOF and RDB simultaneously | restart = empty queue, lost dedup |
| Forget `vm.overcommit_memory=1` (host) | `BGSAVE` may abort under pressure |

If Redis truly must be reset (rare):

```bash
docker compose -f deploy/nl1/docker-compose.yml stop bot api worker
docker compose -f deploy/nl1/docker-compose.yml exec redis \
  redis-cli -a "$REDIS_PASSWORD" FLUSHDB    # ← only after stopping producers/consumers
docker compose -f deploy/nl1/docker-compose.yml start bot api
```

### 8.5 How NOT to lose data

| Class | Where it lives | Loss vector | Defence |
|---|---|---|---|
| Job rows | `postgres_data` | `down -v` / `DROP DATABASE` | nightly `pg_dump`; off-host copy of `BACKUP_DIR` |
| Queue state | `redis_data` (AOF) | `FLUSHALL`, container reset | Redis persistence enabled; treat queue as recoverable but back up every N hours if traffic is critical |
| Files on disk | `/var/lib/dwtgbot/storage` | `cleanup` overreach, `rm -rf` | snapshot the LV / rsync to a secondary; backups are operator-driven |
| Temp links table | `postgres_data` (linked to disk) | inconsistency between disk and table | always **mark inactive** before deleting (`UPDATE temp_links SET is_active=false ...`); `cleanup.sh` does this |
| Audit logs | `postgres_data` | row deletes | append-only — never `DELETE`; rotate by `DROP PARTITION` if huge |
| Secrets | `.env` | host wipe | store in your secrets manager (Vault, SOPS, cloud KMS); `.env` is a working copy |

Two non-negotiable rules:

1. **Never run `docker compose down -v` on NL-1 or the `single` host.** That deletes named volumes (`postgres_data`, `redis_data`, `backups`, а в `single` ещё и `storage`). If you really mean to reset, do it on a scratch host.
2. **Never delete files in `STORAGE_PATH` while their `temp_links` rows are still `is_active=true`.** Mark inactive first. The cleanup loop already does this.

### 8.6 Updates from the TUI (operator path)

The installer menu is the preferred operator surface on production hosts:

```bash
cd /opt/DWTGBot
bash deploy/scripts/install.sh
```

The deploy-related options are intentionally split:

| Option | Meaning | Use when |
|---|---|---|
| **`[16] Update to latest main (autodeploy now)`** | Starts `dwtgbot-autodeploy.service` synchronously (`systemctl start --wait`). The service queries GitHub for the current head of `main`, waits for green `ci.yml` + `build-images.yml`, exports the correct immutable `IMAGE_*` tags for that SHA, checks out that SHA, then calls `deploy_update.sh`. | You want this host to advance to the latest green `main`. This is the default human deploy button. |
| **`[17] Rolling restart with current .env`** | Runs `deploy_update.sh <stack>` using the `IMAGE_*` values already present in the environment / `.env`. It does not discover the latest GitHub SHA. | You only want to bounce containers or apply bind-mounted config that is already on disk. |
| **`[18] Install autodeploy service`** | Installs `dwtgbot-autodeploy.service` + timer and creates `/etc/dwtgbot/autodeploy.env` if missing. | First-time setup on each host. Fill `GITHUB_TOKEN` before the first real run. |

The TUI auto-detects the default stack from which `deploy/{single,nl1,nl2}/.env`
exists on the host. On a normal NL-2 host, prompts should default to
`Stack [nl2]:`; on NL-1, to `Stack [nl1]:`; on a single host (or a fresh
host with no `.env` yet), to `Stack [single]:`.

Between autodeploy runs the repository may be in detached HEAD. That is
expected: `auto_deploy.sh` pins `/opt/DWTGBot` to the same SHA as the
running images. `deploy_update.sh` detects that state and skips `git pull`
cleanly.

### 8.7 Manual rolling restart primitive

```bash
# single
sudo bash deploy/scripts/deploy_update.sh single

# split: NL-1 first (so migrations apply before the new worker starts on NL-2)
sudo bash deploy/scripts/deploy_update.sh nl1
# Then NL-2
sudo bash deploy/scripts/deploy_update.sh nl2
```

`deploy_update.sh` per stack:

1. If on a normal branch, `git pull --ff-only`; if the host is in detached
   HEAD (autodeploy-pinned), skip pull cleanly.
2. Validate compose config (and Docker Compose ≥ 2.24.0).
3. Control-plane stacks (`single`, `nl1`): take a pre-deploy DB backup when Postgres is running.
4. `docker compose pull` using image tags from the current environment /
   `.env`.
5. `docker compose up -d` (Compose recreates only changed services).
6. Control-plane stacks (`single`, `nl1`): run Alembic migrations.
7. Media-plane stacks (`single`, `nl2`): restart nginx defensively so bind-mounted config and upstream
   resolution are refreshed.
8. Wait for healthchecks to report green; abort + log if anything stays
   `unhealthy` longer than the timeout.

Idempotent and safe to re-run.

> Equivalent: installer option **17) Rolling restart with current .env**.

### 8.8 Host-side autodeploy service

Если нужен автодеплой по опросу GitHub прямо с серверов, установите
systemd timer на **каждый** хост:

```bash
# single
sudo bash deploy/scripts/install_autodeploy.sh single

# split: on NL-1
sudo bash deploy/scripts/install_autodeploy.sh nl1

# split: on NL-2
sudo bash deploy/scripts/install_autodeploy.sh nl2
```

Что делает установщик:

1. Copies `deploy/systemd/dwtgbot-autodeploy.{service,timer}` into `/etc/systemd/system/`.
2. Creates `/etc/dwtgbot/autodeploy.env` from `deploy/systemd/autodeploy.env.example` if it does not exist.
3. Reloads systemd and optionally enables `dwtgbot-autodeploy.timer`.

Что нужно сделать перед первым запуском:

```bash
sudo editor /etc/dwtgbot/autodeploy.env
sudo systemctl enable --now dwtgbot-autodeploy.timer
sudo systemctl start dwtgbot-autodeploy.service
journalctl -u dwtgbot-autodeploy.service -n 200 --no-pager
```

Правила работы:

- NL-1 и NL-2 запускают **свои собственные** timer и service.
- Оба хоста смотрят на один и тот же SHA головы ветки в GitHub.
- NL-1 деплоит только после зелёных `ci.yml` и `build-images.yml`.
- NL-2 ждёт тот же зелёный SHA **и** успешный GitHub deployment status
  от NL-1 (`nl1-autodeploy`), так что миграции всё ещё выигрывают гонку.
- Сам rollout по-прежнему выполняется через `deploy_update.sh`, то есть
  базовый механизм раскатки на хосте не меняется.
- `single` (`DEPLOY_TARGET=single`) ни от кого не ждёт: он сам владеет
  миграциями, тянет все четыре образа и публикует deployment status в
  environment `single-autodeploy` (`GITHUB_ENVIRONMENT_SINGLE`).

#### 8.8.1 Canonical order for agents and operators

Если задача звучит как "установи автодеплой", "запусти автодеплой
сейчас" или "проверь host-side автодеплой", используйте **ровно** этот
порядок:

| Step | Action | Why |
|---|---|---|
| 1 | Меняйте `deploy/scripts/auto_deploy.sh`, `deploy/systemd/*`, docs и workflow-файлы **в репозитории**, затем пушьте commit. | Репозиторий — source of truth; live-хост не должен становиться канонической копией. |
| 2 | Дождитесь зелёных `ci.yml` и `build-images.yml` для **точного** SHA, который хотите раскатить. | `auto_deploy.sh` сам ждёт оба workflow и не должен опережать CI/CD. |
| 3 | Для первой установки на каждом хосте выполните `sudo bash deploy/scripts/install_autodeploy.sh nl1` / `nl2`, заполните `/etc/dwtgbot/autodeploy.env` и включите timer. | Установщик копирует unit-файлы, создаёт host-local config и подготавливает systemd. |
| 4 | Если rollout нужен **сейчас**, запускайте `sudo systemctl start dwtgbot-autodeploy.service` сначала на **NL-1** и читайте `journalctl` до явного успеха на целевом SHA. | NL-1 владеет миграциями и публикует gate `nl1-autodeploy`. |
| 5 | Только после успеха NL-1 запускайте тот же service на **NL-2** (или дождитесь его timer). | NL-2 должен видеть тот же зелёный SHA и успешный deployment status от NL-1. |
| 6 | На обоих хостах проверяйте checkout, `last_successful_sha` и steady state systemd. | Это подтверждает не только запуск unit, но и факт раскатки нужного SHA. |

#### 8.8.2 Verification snippet

```bash
set -a
source /etc/dwtgbot/autodeploy.env
set +a

git -C "${REPO_PATH}" rev-parse HEAD
cat "${AUTODEPLOY_STATE_DIR}/${DEPLOY_TARGET}.last_successful_sha"
systemctl is-active dwtgbot-autodeploy.timer
systemctl is-active dwtgbot-autodeploy.service
journalctl -u dwtgbot-autodeploy.service -n 200 --no-pager
```

Expected steady state between runs:

- `dwtgbot-autodeploy.timer` = `active`
- `dwtgbot-autodeploy.service` = usually `inactive` because it is a
  `oneshot` unit
- `git rev-parse HEAD` matches
  `${AUTODEPLOY_STATE_DIR}/${DEPLOY_TARGET}.last_successful_sha`

#### 8.8.3 Do not improvise

- Do **not** edit `/etc/systemd/system/dwtgbot-autodeploy.*` manually if
  reinstalling from `deploy/systemd/` is possible.
- Do **not** force NL-2 first when you need a deterministic immediate
  rollout.
- Do **not** treat `/etc/dwtgbot/autodeploy.env` as repo config; it is
  host-local orchestration state.
- Do **not** treat "timer enabled" as proof of rollout; always verify
  the exact SHA on host and in `AUTODEPLOY_STATE_DIR`.

---

## §9 — Health verification (after every deploy)

### 9.1 Container state

```bash
docker compose -f deploy/nl1/docker-compose.yml ps
docker compose -f deploy/nl2/docker-compose.yml ps
# Every service: State=running, Status contains "healthy"
```

### 9.2 Service-level

```bash
NL1='docker compose -f deploy/nl1/docker-compose.yml'
NL2='docker compose -f deploy/nl2/docker-compose.yml'
RPW=$(grep ^REDIS_PASSWORD deploy/nl1/.env | cut -d= -f2)
TOK=$(grep ^BOT_TOKEN      deploy/nl1/.env | cut -d= -f2)

# Postgres
docker exec dwtgbot_postgres pg_isready -U dwtgbot -d dwtgbot
docker exec dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c "SELECT version_num FROM alembic_version;"

# Redis (and queue depth = should be small at rest)
docker exec dwtgbot_redis redis-cli -a "$RPW" ping
docker exec dwtgbot_redis redis-cli -a "$RPW" ZCARD arq:queue

# Bot ↔ Telegram
docker exec dwtgbot_bot curl -fsS "https://api.telegram.org/bot${TOK}/getMe" | jq '.ok'
docker exec dwtgbot_bot curl -fsS "https://api.telegram.org/bot${TOK}/getWebhookInfo" \
  | jq '.result.url == "" or .result.url == null'   # MUST be true (we long-poll)

# Internal API (NL-1)
docker exec dwtgbot_api curl -fsS http://localhost:8080/healthz | jq
docker exec dwtgbot_api curl -fsS -H "X-Internal-Token: $(grep ^API_INTERNAL_TOKEN deploy/nl1/.env | cut -d= -f2)" \
  http://localhost:8080/readyz | jq

# Worker is alive and consuming
$NL2 logs --tail=200 worker | jq -c 'select(.event | test("worker_started|worker_job_started"))' | head

# Public HTTPS (end-to-end through nginx)
curl -fsS https://media.example.com/healthz | jq
curl -fsSI https://media.example.com/        | head -1     # 200 / 301 expected

# TLS expiry
echo | openssl s_client -connect media.example.com:443 -servername media.example.com 2>/dev/null \
     | openssl x509 -noout -dates
```

### 9.3 End-to-end smoke (one URL, one click)

1. From a Telegram client → DM the bot → `/start` → expect welcome.
2. Paste a YouTube URL.
3. Expect: keyboard with format options.
4. Pick a small format (e.g. audio MP3 128). Expect file delivered to the chat.
5. Pick a large format (e.g. 1080p video). Expect: a temp-link reply with
   an inline **"📥 Скачать"** URL button. The `/d/<token>` URL must **not**
   appear in the message text body; otherwise Telegram's preview crawler can
   consume download slots before the user clicks.
6. Before clicking the button, verify nginx saw no prefetch for that token:
   ```bash
   docker compose -f deploy/nl2/docker-compose.yml --env-file deploy/nl2/.env \
     logs --since=60s nginx | grep -E '/d/|TelegramBot' || true
   ```
   A correct fresh message produces no `/d/` access until the user taps the
   button. After the tap, expect one browser request with `status=200`.
7. After test: confirm new rows in DB:

```bash
docker exec -it dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c \
  "SELECT id,status,kind,created_at FROM download_jobs ORDER BY created_at DESC LIMIT 5;"
```

If anything fails, jump to [`24-runbooks.md`](24-runbooks.md) and [`31-troubleshooting.md`](31-troubleshooting.md).

---

## §10 — Production checklist

### 10.1 Pre-deploy

```
[ ] Both hosts on Ubuntu 24.04 LTS, fully patched (`sudo apt -y upgrade`)
[ ] Time synced (`chronyc tracking` reports < 100 ms)
[ ] Hostnames set (`nl1`, `nl2`)
[ ] Non-root admin user with SSH key; password auth disabled
[ ] Docker + compose plugin installed; `hello-world` passes; user in `docker` group
[ ] Private network up; `nc -vz NL1 5432` and `nc -vz NL1 6379` from NL-2
[ ] DNS A/AAAA for SERVER_NAME → NL-2 public IP
[ ] Repo cloned at the same pinned tag/sha on both hosts
[ ] Data dirs created with correct ownership (NL-1: backups; NL-2: 1000:1000 storage)
[ ] `deploy/nl1/.env` and `deploy/nl2/.env` filled, `chmod 0600`, sync-rules respected (§7.2)
[ ] `docker compose ... config -q` passes on both stacks
[ ] Image tags pinned (no `:latest` in `IMAGE_*`)
[ ] Firewall rules in place on both hosts; default-deny verified
[ ] Backup destination reachable from NL-1 (off-host copy if applicable)
[ ] S10: `STORAGE_MIN_FREE_MB` on NL-2 set to a meaningful value (typically `3 × MAX_FILE_SIZE_MB`); `0` is accepted only for dev
[ ] S6: `XACCEL_ENABLED=true` ONLY on NL-2; NL-1 stays `false` (flipping on without nginx = CVE)
[ ] S1: `ORPHAN_JOB_AGE_SECONDS ≥ 2 × JOB_TIMEOUT_SECONDS` in both envs
[ ] S2: if using off-site replication, exactly one of `BACKUP_S3_BUCKET` / `BACKUP_RCLONE_REMOTE` is set on NL-1; creds/remote tested (see §6.6 below and `22-backup-restore.md` §4)
[ ] L7: `SENTRY_DSN` either set (DSN validated, event delivered in staging) or explicitly empty
[ ] L7: `SENTRY_RELEASE=$IMAGE_SHA` exported at deploy time so events correlate with the shipped image
```

Для `single` пункты про второй хост, приватную сеть и синхронизацию `.env`
пропускаются, но добавляются:

```
[ ] `docker compose version --short` ≥ 2.24.0
[ ] `deploy/single/.env` заполнен, `chmod 0600`; все четыре IMAGE_* заданы
[ ] `firewall_setup.sh single` применён; 5432/6379 закрыты (`ss -ltnp | grep -E ':5432|:6379'` пусто)
[ ] STORAGE_MIN_FREE_MB > 0 (storage и Postgres делят диск)
[ ] offsite-бэкап настроен и проверен (BACKUP_S3_* или BACKUP_RCLONE_REMOTE) — ОБЯЗАТЕЛЬНО
[ ] Суммарные LIMIT_* не превышают RAM хоста минус ~1 GiB под ОС
```

### 10.2 Deploy

```
[ ] NL-1 brought up first; `migrate` exited 0
[ ] NL-1 health all green (§9.2 commands)
[ ] NL-2 brought up; nginx HTTP first, then certbot, then HTTPS
[ ] Public `https://.../healthz` returns ok
[ ] End-to-end smoke (§9.3) passed for small + large file
```

### 10.3 Post-deploy

```
[ ] `error_class` histogram quiet (`24-runbooks.md` §0)
[ ] No restart loops (`docker inspect ... -f '{{.RestartCount}}'` is stable)
[ ] Backup ran (`ls -laht /var/backups/dwtgbot | head`) within `BACKUP_INTERVAL_SECONDS`
[ ] S2: if off-site is configured, the same dump landed remotely
       (`aws s3 ls s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/` OR
        `rclone lsd $BACKUP_RCLONE_REMOTE:` — see last-modified timestamp)
[ ] S1: `docker logs dwtgbot_cleanup | jq -c 'select(.event=="orphan_jobs_reaped")'`
       is empty (non-zero reap counts indicate a worker is crashing mid-job)
[ ] Cleanup ran (`docker logs dwtgbot_cleanup | jq -c 'select(.event=="cleanup_done")' | tail`)
[ ] L5: if upstream throttling happened, `docker exec dwtgbot_redis redis-cli KEYS 'cb:*'`
       shows breaker keys only for the affected host(s) and they clear within
       `CB_COOLDOWN_SECONDS`
[ ] L7: if `SENTRY_DSN` is set, Sentry received at least one event from each
       role (`bot`, `api`, `worker`, `cleanup`, `backup`) — trigger with a
       manual `_logger.error` in staging or wait for natural traffic
[ ] TLS cert valid > 30 days
[ ] Documented the deployed version in your team's deploy log
[ ] Monitoring alerts re-enabled (if you silenced any during deploy)
```

---

## §11 — Common mistakes

| # | Mistake | Symptom | Fix |
|---|---|---|---|
| 1 | Used `:latest` in `IMAGE_*` | "worked yesterday, broken today" | pin to sha; redeploy |
| 2 | `POSTGRES_PASSWORD` differs between NL-1 and NL-2 | NL-2 worker logs `password authentication failed` | set the **same** value, redeploy NL-2 |
| 3 | Wrong ownership on `/var/lib/dwtgbot` (e.g. `root:root`) | worker `Permission denied` on download | `sudo chown -R 1000:1000 /var/lib/dwtgbot` |
| 4 | DNS not propagated when running certbot | ACME HTTP-01 fails | wait for DNS / lower TTL; re-run `certbot_init.sh` |
| 5 | Skipped firewall on NL-1 because "it's behind WireGuard" | `5432` reachable from public internet | apply §5.3 ufw rules; rotate passwords |
| 6 | NL-2 deployed first on a schema-changing PR | worker crash loops with `relation does not exist` | always NL-1 first; CI workflow enforces with `needs:` |
| 7 | `docker compose down -v` on NL-1 | DB and queue **gone** | restore from latest dump; never use `-v` on prod |
| 8 | Edited `docker-compose.yml` on the host | drift; lost on next deploy | revert; PR the change; redeploy |
| 9 | Started Redis without `requirepass` | NL-2 connects to wrong / unauth Redis | set `REDIS_PASSWORD`, recreate Redis container |
| 10 | Restored DB without stopping bot/api first | live connections during restore → corruption | use `restore.sh` (it stops/starts automatically) |
| 11 | Old cert renewal failed silently | TLS expired warning | enable certbot stderr alerting; reinstate `certbot_init.sh` |
| 12 | `PUBLIC_BASE_URL` still `http://...` after enabling TLS | `validate_runtime` aborts in production | set `https://...`, redeploy |
| 13 | `MAX_FILE_SIZE_MB` greater than disk allowance | recurring "disk full" incidents | lower it OR provision more disk |
| 14 | Skipped `migrate` after DB-changing PR | runtime errors on missing columns | `docker compose run --rm migrate alembic upgrade head` |
| 15 | Pulled image tag doesn't exist | `docker compose up` fails with `manifest unknown` | verify tag in registry; correct `IMAGE_*` |
| 16 | Set `LOG_LEVEL=DEBUG` in production and forgot | logs balloon; `jq` recipes get noisy | revert to `INFO` after triage |
| 17 | Containers running on different `docker network` after manual edits | inter-service DNS fails | recreate via `docker compose up -d --force-recreate`; PR the desired state |
| 18 | Credentials in CI logs | rotation required | use `::add-mask::` in GH Actions; rotate the leaked secret |
| 19 | Both `BACKUP_S3_BUCKET` and `BACKUP_RCLONE_REMOTE` set | the script runs both paths; double bandwidth + double cost, no benefit | pick exactly one off-site backend |
| 20 | `XACCEL_ENABLED=true` on NL-1 | the api emits `X-Accel-Redirect` to a non-existent nginx → downloads 502 (or worse, leak the internal path) | keep `XACCEL_ENABLED=false` on NL-1; only NL-2 fronts nginx (see §7.1) |
| 21 | `ORPHAN_JOB_AGE_SECONDS < 2 × JOB_TIMEOUT_SECONDS` | cleanup worker reaps jobs that are still legitimately running → users get a confusing "failed" message while their file is uploading | raise `ORPHAN_JOB_AGE_SECONDS` or lower `JOB_TIMEOUT_SECONDS` |
| 22 | `STORAGE_MIN_FREE_MB=0` on NL-2 | worker thrashes on a full disk, retries exhaust, user sees generic timeout | set to `3 × MAX_FILE_SIZE_MB` so S10 backpressure kicks in |
| 24 | `single` без offsite-бэкапа (`BACKUP_S3_*` / `BACKUP_RCLONE_REMOTE` пусты) | потеря диска = потеря базы **и** всех дампов | настроить offsite до выхода в прод ([`22-backup-restore.md`](22-backup-restore.md)) |
| 25 | Docker Compose < 2.24 | `include` не распознаётся, `config` падает | обновить `docker-compose-plugin` (installer `[1]`) |
| 26 | `.env` одновременно в `deploy/single` и `deploy/nl*` на одном хосте | скрипты выбирают `single`, меню показывает `multiple` | оставить `.env` только у реально работающего стека |
| 23 | `SENTRY_DSN` set but `sentry-sdk` missing at runtime | not possible with `prod.lock` (bundled), but if someone rebuilds the image from `dev.lock` by hand, `configure_sentry` logs a warning and silently returns | always deploy from `prod.lock`; confirm `python -c "import sentry_sdk"` in the image |

---

## §12 — Rollback strategy

### 12.1 Decision tree

```mermaid
flowchart TD
  A[Production incident] --> Q1{Did the last deploy change the DB schema?}
  Q1 -- no --> R1[§12.2 Image-only rollback]
  Q1 -- yes --> Q2{Is the new schema backwards compatible?}
  Q2 -- yes --> R1
  Q2 -- no --> Q3{Can we hot-fix forward in <30 min?}
  Q3 -- yes --> R3[§12.4 Fix forward]
  Q3 -- no --> R2[§12.3 Schema rollback (rare)]
```

### 12.2 Image-only rollback (most common, safest)

```bash
cd /opt/dwtgbot

# Identify previous good tag (e.g. from release notes / GHCR)
PREV=sha-abcdef1

# Pin the previous tag in BOTH .env files (back to the same value used previously)
for env in deploy/nl1/.env deploy/nl2/.env; do
  sed -i "s|^IMAGE_BOT=.*|IMAGE_BOT=ghcr.io/your-org/dwtgbot-bot:${PREV}|"           "$env"
  sed -i "s|^IMAGE_API=.*|IMAGE_API=ghcr.io/your-org/dwtgbot-api:${PREV}|"           "$env"
  sed -i "s|^IMAGE_WORKER=.*|IMAGE_WORKER=ghcr.io/your-org/dwtgbot-worker:${PREV}|"  "$env"
  sed -i "s|^IMAGE_BACKUP=.*|IMAGE_BACKUP=ghcr.io/your-org/dwtgbot-backup:${PREV}|"  "$env"
done

# NL-1 first, then NL-2
sudo bash deploy/scripts/deploy_update.sh nl1
sudo bash deploy/scripts/deploy_update.sh nl2

# Verify
docker compose -f deploy/nl1/docker-compose.yml ps
docker compose -f deploy/nl2/docker-compose.yml ps
curl -fsS https://media.example.com/healthz | jq
```

No DB rollback needed if the previous tag's schema matches.

В `single` тот же цикл `sed` выполните по одному файлу
`deploy/single/.env`, затем `sudo bash deploy/scripts/deploy_update.sh single`.

### 12.3 Schema rollback (rare; emergency)

Forward-only is the policy. Downgrade only when fix-forward is impossible **and** the new revision is misbehaving in production.

```bash
# Identify offending revision
docker exec dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c \
  "SELECT version_num FROM alembic_version;"

# Stop writers FIRST so no new rows are written under the broken schema
docker compose -f deploy/nl1/docker-compose.yml stop bot api
docker compose -f deploy/nl2/docker-compose.yml stop worker

# Downgrade one step
docker compose -f deploy/nl1/docker-compose.yml run --rm migrate \
  alembic downgrade -1

# Then image rollback as in §12.2
sudo bash deploy/scripts/deploy_update.sh nl1
sudo bash deploy/scripts/deploy_update.sh nl2

# Restart writers
docker compose -f deploy/nl1/docker-compose.yml start bot api
docker compose -f deploy/nl2/docker-compose.yml start worker
```

If the migration was destructive (DROP COLUMN, DROP TABLE), downgrade cannot restore data; restore the most recent good dump (§12.5).

### 12.4 Fix-forward (preferred when possible)

```bash
# Patch the bug in a hot-fix branch
git checkout -b hotfix/<issue>
# ... edit, test, push ...
# CI builds new images; pin the new tag in .env; run deploy_update.sh
```

Fix-forward keeps the audit trail clean and avoids destructive downgrades.

### 12.5 Data rollback (last resort)

Only when DB rows are corrupted by the new code (rare).

```bash
# Stop writers
docker compose -f deploy/nl1/docker-compose.yml stop bot api
docker compose -f deploy/nl2/docker-compose.yml stop worker

# Restore latest known-good dump
sudo bash deploy/scripts/restore.sh /var/backups/dwtgbot/dwtgbot_YYYYMMDDTHHMMSSZ.sql.gz

# Restart writers after image rollback (if needed)
docker compose -f deploy/nl1/docker-compose.yml start bot api
docker compose -f deploy/nl2/docker-compose.yml start worker
```

Communicate the data-loss window to users.

### 12.6 Rollback anti-patterns

| ❌ Don't | ✅ Do |
|---|---|
| `docker compose down -v` to "reset cleanly" | targeted rollback per §12.2 |
| Skip NL-1-first ordering on rollback | NL-1 first, NL-2 second (mirror of forward deploy) |
| Edit images on the host (`docker tag …`) | always pin in `.env`, run deploy script |
| Roll back without checking `alembic_version` | verify schema/code version match |
| Rollback during active traffic without a quick `stop bot` first | causes user-visible errors mid-rollback |

---

## §13 — Zero-downtime recommendations

Honest scope: with two single hosts and Docker Compose, **true zero-downtime is not free**. What you can achieve:

### 13.1 What's already near-zero-downtime

| Component | Restart impact | Why |
|---|---|---|
| `worker` (NL-2) | 0 user-visible | jobs in flight at restart get retried; queue catches up |
| `cleanup` (NL-2) | 0 | runs on schedule; missed tick is harmless |
| `backup` (NL-1) | 0 | next tick runs |
| `nginx` (NL-2) | <1s 502s during reload | use `nginx -s reload` instead of restart for config-only changes |
| `api` (both) | <2s; one or two requests refused | bot tolerates retries |

### 13.2 What does cause a small visible blip

| Component | Restart impact | Mitigation |
|---|---|---|
| `bot` (NL-1) | a few seconds: polling resumes; users see slight delay | put a status note if announced; restart at low-traffic hour |
| `postgres` (NL-1) | seconds: every dependent service shows errors during restart | rolling restart of writers; restart Postgres only when necessary |
| `redis` (NL-1) | similar | always stop producers/consumers first; restart Redis; restart them |

### 13.3 Patterns

1. **Recreate-only-changed:** `deploy_update.sh` already does `compose up -d` so unchanged services aren't recreated.
2. **NL-1 first on schema PRs:** `migrate` runs to head before bot/api start the new code.
3. **Nginx reload, not restart:**
   ```bash
   docker exec dwtgbot_nginx nginx -t && docker exec dwtgbot_nginx nginx -s reload
   ```
4. **Long-lived connections:** worker reconnects to PG/Redis on disconnect; bot reconnects to Telegram. No manual session pinning required.
5. **Backwards-compatible migrations** (mandatory for zero-downtime DB changes):
   - **Add column** as nullable / with default; backfill in a follow-up.
   - **Rename column** in two stages: add new + dual-write → backfill → switch reads → drop old.
   - **Drop column** only after the code that referenced it is fully removed in production for ≥ 1 release.
   - **Add ENUM value** in a migration *before* code uses it (§4.5 of `31-` and Appendix D.9 of `30-`).
   - **Never** rename a table in one shot.
6. **Maintenance window when unavoidable** (Postgres major upgrade, host migration): announce; stop bot first; do the work; restart; smoke.

### 13.4 What you cannot get without rearchitecting

- Multiple bot replicas polling the same Telegram token (Telegram allows only one polling client) — true blue/green for the bot would need webhooks behind a load balancer.
- Zero-downtime Postgres failover without a managed service / replica.

If you need either, design that in `26-cursor-rules.md` first; it crosses ADR-0005 / ADR-0011 and changes the topology.

---

## §14 — Day-2 quick reference

```bash
# ── Aliases ───────────────────────────────────────────────
# single: оба алиаса указывают на один стек, остальные команды не меняются
#   NL1='docker compose -f deploy/single/docker-compose.yml --env-file deploy/single/.env'; NL2="$NL1"
#   и читайте секреты из deploy/single/.env
NL1='docker compose -f deploy/nl1/docker-compose.yml'
NL2='docker compose -f deploy/nl2/docker-compose.yml'
RPW=$(grep ^REDIS_PASSWORD deploy/nl1/.env | cut -d= -f2)

# ── Status ────────────────────────────────────────────────
$NL1 ps
$NL2 ps
docker stats --no-stream

# ── Logs (JSON) ───────────────────────────────────────────
$NL1 logs --tail=200 bot      | jq -c 'select(.level=="error" or .level=="warning")'
$NL2 logs --tail=500 worker   | jq -c 'select(.event | test("classified_error"))'
$NL2 logs --tail=200 nginx

# ── Health ────────────────────────────────────────────────
curl -fsS https://media.example.com/healthz | jq
docker exec dwtgbot_api      curl -fsS -H "X-Internal-Token: $(grep ^API_INTERNAL_TOKEN deploy/nl2/.env | cut -d= -f2)" \
  http://localhost:8080/readyz | jq
docker exec dwtgbot_postgres pg_isready -U dwtgbot -d dwtgbot
docker exec dwtgbot_redis    redis-cli -a "$RPW" ping
docker exec dwtgbot_redis    redis-cli -a "$RPW" ZCARD arq:queue

# ── Schema / migrations ───────────────────────────────────
docker exec dwtgbot_postgres psql -U dwtgbot -d dwtgbot -c "SELECT version_num FROM alembic_version;"
$NL1 run --rm migrate alembic upgrade head

# ── Backup / restore ──────────────────────────────────────
sudo bash deploy/scripts/backup.sh
sudo bash deploy/scripts/restore.sh /var/backups/dwtgbot/dwtgbot_<TS>.sql.gz
ls -laht /var/backups/dwtgbot | head

# ── Cleanup ───────────────────────────────────────────────
sudo bash deploy/scripts/cleanup.sh
$NL2 logs --since=24h cleanup | jq -c 'select(.event=="cleanup_done")' | tail

# ── SSL ───────────────────────────────────────────────────
sudo bash deploy/scripts/certbot_init.sh --domain media.example.com --email ops@example.com
echo | openssl s_client -connect media.example.com:443 -servername media.example.com 2>/dev/null \
     | openssl x509 -noout -dates -issuer -subject

# ── Deploy / rollback ─────────────────────────────────────
bash deploy/scripts/install.sh                  # [16] = latest green main
sudo systemctl start --wait dwtgbot-autodeploy.service  # same primitive
sudo bash deploy/scripts/deploy_update.sh nl2   # rolling restart only; current .env

# ── Disk ──────────────────────────────────────────────────
df -h / /var/lib/dwtgbot /var/backups/dwtgbot 2>/dev/null
du -sh /var/lib/dwtgbot/storage/jobs/* | sort -h | tail
```

---

## §15 — Disaster recovery quick map

| Scenario | Procedure |
|---|---|
| **NL-1 host lost** | New Ubuntu 24.04 host → §3 → §4 → §5 (same `.env` values) → `restore.sh` from latest dump → §9 verify. DNS unchanged. |
| **NL-2 host lost** | New host → §3 → §4 → §6 (storage history is **NOT** recoverable — old temp links return 410). Reissue cert → point DNS A → §9 verify. Bot continues serving new users. |
| **Postgres data corruption** | `restore.sh` from most recent good dump (accept the data-loss window). |
| **Storage volume corruption** | `UPDATE temp_links SET is_active=false;`  then `cleanup.sh`. Users requeue. |
| **Bot token compromised** | Rotate via BotFather → update **both** `.env` (NL-1 then NL-2) → `deploy_update.sh nl1` → `deploy_update.sh nl2`. |
| **Internal API token leaked** | Rotate `API_INTERNAL_TOKEN` in **both** `.env`; redeploy NL-1 then NL-2. |
| **`single` host lost** | Новый хост → §3 → §4 → §4a (те же значения `.env`) → `restore.sh` из **offsite**-дампа (локальные дампы погибли вместе с диском) → выпустить сертификат → DNS A на новый IP → §9. Старые temp links не восстанавливаются. |

---

## §16 — Future extension points (out of current scope)

- Blue/green deploys at the compose level (two stacks behind nginx upstream).
- Healthcheck-aware deploy gates: deploy_update waits for `/readyz` on NL-2 before marking NL-1 update complete.
- Automated DR drills in CI: restore the latest dump into a scratch DB and run sanity SQL.
- Sealed secrets via Vault to replace `.env`.
- Image signing verification at pull time (cosign + admission).
- Postgres streaming replica on NL-2 (read-only) for fast failover.

Each of these crosses an ADR-0005 assumption — propose in `26-cursor-rules.md` and add an ADR before implementing.

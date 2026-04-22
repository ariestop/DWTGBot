# syntax=docker/dockerfile:1.7
# =====================================================================
# Backup image (NL-1).
# Slim Debian + postgresql-client + bash; runs the project's backup.sh
# on a schedule via app.workers.backup_worker.
#
# Deploy-audit fix (post-L8):
# 1. Install Python deps from ``requirements/prod.lock`` with
#    ``--require-hashes`` — the previous ``pip install pydantic pydantic-settings
#    structlog`` left versions floating and broke the reproducibility
#    story the other three images followed.
# 2. Bundle ``awscli`` + ``rclone`` so ``deploy/scripts/backup.sh::replicate_offsite``
#    (S2) has a non-no-op path in-container. Previously the script
#    warned "aws-cli is missing" and returned success, which silently
#    skipped off-site replication.
#
# Image size cost of the two binaries is ~80 MiB; we accept it because
# the backup container runs on NL-1 only and the alternative (host-side
# cron for replication) duplicates operator surface area.
# =====================================================================

ARG PYTHON_VERSION=3.14.4
# Audit fix A15: pin Debian codename so ``apt install awscli rclone``
# resolves to a stable major.minor across rebuilds; otherwise ``-slim``
# tracks Python's upstream default and flips silently on every Debian
# release, re-pointing our off-site backup client at a new CLI.
ARG DEBIAN_CODENAME=trixie
ARG APP_USER=app
ARG APP_UID=1000
ARG APP_GID=1000

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build
COPY requirements/ requirements/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -U pip wheel \
 && /opt/venv/bin/pip install --require-hashes -r requirements/prod.lock

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS runtime

ARG APP_USER
ARG APP_UID
ARG APP_GID

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" APP_HOME=/app

# awscli (v1 from Debian) + rclone cover the two off-site backends the
# backup script supports (BACKUP_S3_BUCKET / BACKUP_RCLONE_REMOTE).
# Neither is pulled from pip because awscli's dep graph conflicts with
# the pinned app graph in ``prod.lock``, and a static rclone binary via
# apt is strictly simpler than downloading the upstream tarball.
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client gzip tini ca-certificates bash \
        awscli rclone \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} ${APP_USER} \
    && useradd  --system --uid ${APP_UID} --gid ${APP_GID} \
                --home ${APP_HOME} --shell /bin/bash ${APP_USER}

COPY --from=builder /opt/venv /opt/venv

WORKDIR ${APP_HOME}
COPY --chown=${APP_USER}:${APP_USER} app/                  app/
COPY --chown=${APP_USER}:${APP_USER} deploy/scripts/       deploy/scripts/

RUN mkdir -p /var/backups/dwtgbot \
 && chown -R ${APP_USER}:${APP_USER} /var/backups/dwtgbot
USER ${APP_USER}

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.workers.backup_worker"]

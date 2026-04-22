# syntax=docker/dockerfile:1.7
# =====================================================================
# API image (used on both planes).
# - On NL-1: serves /healthz / /readyz to docker healthchecks.
# - On NL-2: serves /d/{token} behind nginx (X-Accel-Redirect).
# Same code, different listening URL.
# =====================================================================

ARG PYTHON_VERSION=3.14.4
# Audit fix A15: pin Debian codename for reproducible apt resolution.
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

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} ${APP_USER} \
    && useradd  --system --uid ${APP_UID} --gid ${APP_GID} \
                --home ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER}

COPY --from=builder /opt/venv /opt/venv
WORKDIR ${APP_HOME}
COPY --chown=${APP_USER}:${APP_USER} app/        app/
COPY --chown=${APP_USER}:${APP_USER} migrations/ migrations/
COPY --chown=${APP_USER}:${APP_USER} alembic.ini ./

# Deploy-audit fix: the storage volumes are mounted under
# /var/lib/dwtgbot/... in the NL-2 docker-compose; a first-boot of the
# stack initialises a *named* docker volume from the image's directory
# contents at that path. If the image doesn't own those dirs, the
# freshly-created volume ends up root-owned, and the next container
# that mounts it (e.g. worker, which runs as ``app``) cannot write.
# Mirror the mkdir+chown that already exists in worker.Dockerfile so
# whichever of api/worker gets started first seeds the volume with the
# correct ownership.
RUN mkdir -p /var/lib/dwtgbot/storage /var/lib/dwtgbot/tmp \
 && chown -R ${APP_USER}:${APP_USER} /var/lib/dwtgbot

USER ${APP_USER}
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${API_PORT:-8080}/healthz" >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main_api"]

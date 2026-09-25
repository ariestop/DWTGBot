# syntax=docker/dockerfile:1.7
# =====================================================================
# Worker image (NL-2, media plane).
# Includes ffmpeg for yt-dlp postprocessing and audio extraction.
# Runs arq for queue + a sibling cleanup process via the same image.
# =====================================================================

ARG PYTHON_VERSION=3.14.4
# Audit fix A15: pin the base image to an explicit Debian codename so
# ``apt install ffmpeg`` resolves to a stable major.minor across
# rebuilds. Without the codename suffix, ``-slim`` tracks whatever the
# Python image defaults to at pull time — which flips on every Debian
# release (bookworm → trixie, …) and silently shifts ffmpeg from 5.x
# to 6.x to 7.x between CI runs.
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
# L8: install from the fully-resolved lockfile, not the human-edited
# requirements/*.txt. Lockfiles carry transitive pins + hashes, so a
# CI image is bit-identical to the one that was tested.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -U pip wheel \
 && /opt/venv/bin/pip install --require-hashes -r requirements/prod.lock

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS runtime

ARG APP_USER
ARG APP_UID
ARG APP_GID

# DENO_DIR: deno (yt-dlp's JS runtime for YouTube) needs a writable cache;
# $HOME (/app) is root-owned.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" APP_HOME=/app DENO_DIR=/tmp/deno

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tini ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} ${APP_USER} \
    && useradd  --system --uid ${APP_UID} --gid ${APP_GID} \
                --home ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER}

COPY --from=builder /opt/venv /opt/venv

WORKDIR ${APP_HOME}
COPY --chown=${APP_USER}:${APP_USER} app/        app/
COPY --chown=${APP_USER}:${APP_USER} migrations/ migrations/
COPY --chown=${APP_USER}:${APP_USER} alembic.ini ./
COPY --chown=${APP_USER}:${APP_USER} deploy/scripts/ deploy/scripts/

# Storage volume mountpoints; created here so they exist if the volume is empty.
RUN mkdir -p /var/lib/dwtgbot/storage /var/lib/dwtgbot/tmp \
 && chown -R ${APP_USER}:${APP_USER} /var/lib/dwtgbot

USER ${APP_USER}

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD pgrep -f "app.main_worker|arq " >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main_worker"]

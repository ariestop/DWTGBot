# syntax=docker/dockerfile:1.7
# =====================================================================
# Bot image (NL-1, control plane).
# Polls Telegram, analyzes URLs, enqueues jobs into Redis.
# Does NOT need ffmpeg or the yt-dlp CLI (yt-dlp is used as a Python lib
# inside the worker image; the bot only calls metadata via providers,
# so we install the same Python deps to keep imports identical).
# =====================================================================

ARG PYTHON_VERSION=3.14.4
# Audit fix A15: pin Debian codename for reproducible apt resolution.
ARG DEBIAN_CODENAME=trixie
ARG APP_USER=app
ARG APP_UID=1000
ARG APP_GID=1000

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build
COPY requirements/ requirements/
# L8: install from the lockfile (see docker/worker.Dockerfile for
# rationale).
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -U pip wheel \
 && /opt/venv/bin/pip install --require-hashes -r requirements/prod.lock

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS runtime

ARG APP_USER
ARG APP_UID
ARG APP_GID

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOME=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} ${APP_USER} \
    && useradd  --system --uid ${APP_UID} --gid ${APP_GID} \
                --home ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER}

COPY --from=builder /opt/venv /opt/venv

WORKDIR ${APP_HOME}
COPY --chown=${APP_USER}:${APP_USER} app/        app/
COPY --chown=${APP_USER}:${APP_USER} migrations/ migrations/
COPY --chown=${APP_USER}:${APP_USER} alembic.ini ./

USER ${APP_USER}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost', 1)); s.close()" \
        || pgrep -f "app.main_bot" >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main_bot"]

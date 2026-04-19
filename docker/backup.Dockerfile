# syntax=docker/dockerfile:1.7
# =====================================================================
# Backup image (NL-1).
# Slim Debian + postgresql-client + bash; runs the project's backup.sh
# on a schedule via app.workers.backup_worker.
# =====================================================================

ARG PYTHON_VERSION=3.14.4
ARG APP_USER=app
ARG APP_UID=1000
ARG APP_GID=1000

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_USER
ARG APP_UID
ARG APP_GID

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    APP_HOME=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client gzip tini ca-certificates bash \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} ${APP_USER} \
    && useradd  --system --uid ${APP_UID} --gid ${APP_GID} \
                --home ${APP_HOME} --shell /bin/bash ${APP_USER}

WORKDIR ${APP_HOME}
COPY --chown=${APP_USER}:${APP_USER} app/                  app/
COPY --chown=${APP_USER}:${APP_USER} deploy/scripts/       deploy/scripts/
COPY --chown=${APP_USER}:${APP_USER} requirements/base.txt requirements/base.txt

RUN pip install --no-cache-dir pydantic pydantic-settings structlog

RUN mkdir -p /var/backups/dwtgbot && chown -R ${APP_USER}:${APP_USER} /var/backups/dwtgbot
USER ${APP_USER}

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.workers.backup_worker"]

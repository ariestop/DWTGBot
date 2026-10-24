---
paths:
  - "docker/**"
  - "deploy/**"
  - ".github/workflows/**"
---

# Docker, compose, деплой

Подробно: `docs/19-docker-architecture.md`, `docs/20-deployment.md`,
`docs/21-cicd.md`.

## Образы

- Multi-stage (`builder` с `gcc` → `runtime` без), база
  `python:${PYTHON_VERSION}-slim-…` с закреплённой патч-версией (ADR-0009).
- Пользователь `app:1000`, `tini` как PID 1, `HEALTHCHECK` обязателен.
- Сначала `requirements/`, потом `COPY --chown=app:app app/`.
- `ffmpeg` только в образе worker.

## Compose (ADR-0011)

- Сервисы — только во фрагментах `deploy/compose/control.yml` (postgres,
  redis, migrate, bot, backup) и `media.yml` (api, worker, cleanup, nginx,
  certbot). Стеки `deploy/{single,nl1,nl2}/docker-compose.yml` — только
  `include`; различия топологий — в `single.override.yml` / `nl1.overlay.yml`.
- Якоря `*restart`, `*logging`, `*app-env` на каждом долгоживущем сервисе;
  `depends_on: condition: service_healthy`; `migrate` одноразовый, остальные
  стартуют после `service_completed_successfully`.
- Наружу только nginx 80/443. Исключение — `nl1.overlay.yml` (Postgres/Redis
  на `NL1_PRIVATE_IP`). nginx не входит в сеть `dwtgbot_internal`.
- Логи контейнеров — драйвер journald (`deploy/scripts/journald_setup.sh`).
- Инварианты проверяют `app/tests/test_deploy_topology.py` и CI-job
  `compose-validate`.

## nginx и certbot

- Локация файлов остаётся `internal;` (отдача только через
  `X-Accel-Redirect`), `STORAGE_PATH` смонтирован в nginx только на чтение.
- `limit_req_zone` на публичных локациях и заголовки из
  `deploy/nginx/snippets/security.conf` (HSTS, X-Frame-Options, …) не снимать.
- `:80` перенаправляет на `:443`, кроме `/.well-known/acme-challenge/`.
- Правка конфига — проверить `nginx -t` в контейнере; правка certbot —
  объяснить, как продолжает работать продление.

## Теги и деплой

- CI публикует `sha-<short>` и `main` (с `main`), semver и `latest` —
  только с тегов `v*`. В проде — только неизменяемые теги (`sha-…` / semver).
- Цепочка: `ci.yml` → `build-images.yml` → `deploy.yml` (`workflow_run`
  после успешной сборки на `main`; есть и ручной запуск). Деплой —
  `deploy/scripts/deploy_update.sh` с автооткатом на last-good.
- Запрещено: `--privileged`, `cap_add`, `pid: host`, `network: host`,
  сборка образов на проде, секреты в compose-файлах.

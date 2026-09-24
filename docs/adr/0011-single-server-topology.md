# ADR-0011 — Однсерверная топология `single` рядом с `split` (NL-1 + NL-2)

- **Status:** Proposed
- **Date:** 2026-09-24
- **Deciders:** project owner
- **Tags:** topology, deployment, security, cost, compose
- **Supersedes:** ADR-0005 row 2 (Two-server architecture), row 3 (NL-1 = bot + redis + postgres), row 4 (NL-2 = worker + nginx + certbot + cleanup + storage); дополняет ADR-0001 (§4.1 «Single host — Rejected» пересмотрен)
- **Superseded by:** —

---

## 1. Context

[ADR-0001](0001-two-server-topology.md) и строки 2–4
[ADR-0005](0005-locked-architectural-assumptions.md) требуют ровно два
хоста: NL-1 (control plane) и NL-2 (media plane), связанные WireGuard.
Однсерверный вариант был явно отклонён (ADR-0001 §4.1): публичный nginx
рядом с БД нарушает принцип минимальной экспозиции, а ffmpeg мешает
отзывчивости бота.

Для текущего масштаба (сотни пользователей в день, `WORKER_CONCURRENCY=2`)
это стоит дорого:

- два сервера, два `.env` с ручной синхронизацией секретов (`BOT_TOKEN`,
  пароли, `API_INTERNAL_TOKEN`), два firewall;
- WireGuard, который установщик не автоматизирует;
- канал NL-1 ↔ NL-2 — отдельная точка отказа (при его падении задачи
  встают);
- второй экземпляр `api` на NL-1, который отвечает только на health.

При этом код приложения от топологии не зависит: процессы общаются
только через Postgres и Redis (`DATABASE_URL`, `REDIS_URL`), HTTP-вызовов
между хостами нет. Двухсерверность целиком живёт в `deploy/`, CI и
документации.

**Non-goals:**

- Изменения Python-кода, образов, схемы БД.
- Автоматизация WireGuard.
- Обратная миграция split → single (продакшена в split пока нет).
- Три и более хоста, Kubernetes (по-прежнему отклонены ADR-0001 §4.2–4.3).

---

## 2. Decision

Мы поддерживаем **две топологии** развёртывания:

| Топология | Стек(и) | Когда |
|---|---|---|
| `single` (по умолчанию для малого масштаба) | `deploy/single` — все сервисы на одном хосте | старт проекта, пока не сработали пороги роста (§3.3) |
| `split` | `deploy/nl1` + `deploy/nl2` — как в ADR-0001 | после порогов роста |

Реализация:

1. Сервисы описаны один раз во фрагментах
   `deploy/compose/control.yml` (postgres, redis, migrate, bot, backup)
   и `deploy/compose/media.yml` (api, worker, cleanup, nginx, certbot).
   Стеки — тонкие обёртки через Compose `include` со списком `path`:
   - `deploy/nl1` = `control.yml` + `nl1.overlay.yml` (порты Postgres/Redis
     на `${NL1_PRIVATE_IP}`, внутренний `api`);
   - `deploy/nl2` = `media.yml`;
   - `deploy/single` = `control.yml` + `media.yml` + `single.override.yml`.
2. В `single` у Postgres и Redis **нет опубликованных портов** вообще;
   на хост публикуются только 80/443 (nginx). Nginx подключён только к
   сети `dwtgbot_media` и не имеет маршрута до Postgres/Redis. api, worker
   и cleanup подключены к обеим сетям.
3. В `single` worker изолирован от control plane через cgroup-веса:
   `cpu_shares`, `blkio_config.weight`, `oom_score_adj` (у Postgres —
   отрицательный), `WORKER_CONCURRENCY=2`, обязательный
   `STORAGE_MIN_FREE_MB`.
4. Имена volume и `container_name` во всех стеках одинаковые. Поэтому
   рост `single → split` — это замена стека на том же хосте (он
   становится NL-1) без переноса данных.
5. Минимальная версия Docker Compose — **2.24.0** (`include` со списком
   `path`, `project_directory`, `env_file`). Установщик проверяет её.

Строки 5–12 ADR-0005 не меняются. Строки 10 (Docker-first) и 12 (bash
TUI) остаются в силе, но их содержимое расширяется: стеков три, а
установщик умеет готовить `single`.

---

## 3. Consequences

### 3.1 Positive

- Вдвое меньше серверов и операционной работы: одна `.env`, нет
  WireGuard, нет синхронизации секретов, деплой одним вызовом
  `deploy_update.sh single`.
- Меньше задержка до Postgres/Redis; исчезает межхостовый канал как
  точка отказа.
- Postgres и Redis не публикуются на хост вовсе — строже, чем в NL-1,
  где они слушают WG-IP.
- Код и образы те же; рост до `split` без миграции данных.
- Рефакторинг на фрагменты убирает дублирование compose, хардкод
  `10.10.0.1` и добавляет валидацию compose в CI.

### 3.2 Negative / accepted trade-offs

- **Радиус поражения.** Публичный nginx живёт на одном хосте с БД и
  секретами. Меры: отдельная сеть для nginx, никаких опубликованных
  портов у данных, non-root контейнеры, ufw 22/80/443. Остаточный риск —
  побег из контейнера nginx даёт доступ ко всему хосту — **принимается**
  на период малого масштаба.
- **Конкуренция за ресурсы.** ffmpeg может давить на bot и Postgres.
  Меры из §2 п.3 снижают, но не исключают влияние; сигнал к переезду —
  рост p95 латентности бота (§3.3).
- **Общий диск** для storage, Postgres и бэкапов. `STORAGE_MIN_FREE_MB`
  обязателен; offsite-копия бэкапов (S3/rclone в `backup.sh`)
  обязательна для `single`.
- **Одна точка отказа** — фактически без изменений: NL-1 и раньше был
  SPOF для всей системы.
- **Три стека в матрице поддержки** и зависимость от Compose ≥ 2.24.
  Меры: `compose-validate` в CI и тест инвариантов
  `app/tests/test_deploy_topology.py`.

### 3.3 Operational impact

- Новый стек `deploy/single` и `deploy/single/.env.example`.
- Скрипты `deploy/scripts/*` принимают цель `single`; установщик
  предлагает её по умолчанию на свежем хосте.
- `.github/workflows/deploy.yml`: задача `single`, выбор топологии через
  repo variable `DEPLOY_TOPOLOGY` (`single` | `split`).
- Железо для `single`: рекомендуется 4 vCPU / 8 GB RAM / 80+ GB SSD;
  минимум 2 vCPU / 4 GB с уменьшенными лимитами.
- Пороги перехода на `split` (любой из):
  - p95 латентности хендлеров бота растёт при нагрузке на worker;
  - storage занят больше чем на 70 % диска;
  - исходящий трафик близок к лимиту тарифа;
  - p95 ожидания задачи в очереди выше SLO
    ([`35-metrics-and-slo.md`](../35-metrics-and-slo.md));
  - нужен второй worker-хост.
  Процедура — [`24-runbooks.md`](../24-runbooks.md) §26.

---

## 4. Alternatives considered

### 4.1 Оставить только split
Отклонено: платим за два сервера и WireGuard без выигрыша на текущей
нагрузке.

### 4.2 Только single, split удалить
Отклонено: путь роста должен оставаться готовым и проверенным CI.

### 4.3 Отдельный `deploy/single/docker-compose.yml` (копия сервисов)
Не трогает действующие файлы, но дублирует ~400 строк compose; дрейф
между копиями неизбежен. Выбраны общие фрагменты + сравнение
`docker compose config` до/после рефакторинга.

### 4.4 Слияние через цепочки `-f` в `helpers.sh`
Работает, но ручной `docker compose` из папки стека перестаёт видеть
полную конфигурацию. `include` делает `cd deploy/single && docker compose
up -d` эквивалентным вызову через скрипты.

---

## 5. Compliance

Ревьюер отклоняет PR, который:

- публикует порты Postgres/Redis в `single` или в `deploy/compose/*`
  (порты допустимы только в `deploy/nl1/nl1.overlay.yml`);
- подключает nginx к `dwtgbot_internal`;
- меняет имена volume/`container_name` в одном стеке без остальных;
- добавляет сервис в стек в обход фрагментов `deploy/compose/*`.

Автоматически это проверяют `app/tests/test_deploy_topology.py` и задача
`compose-validate` в `.github/workflows/ci.yml`.

Документы, которые должны отражать решение: `00-overview.md`,
`02-architecture.md`, `03-project-structure.md`, `13-config-and-env.md`,
`17-security.md`, `19-docker-architecture.md`, `20-deployment.md`,
`21-cicd.md`, `22-backup-restore.md`, `24-runbooks.md`,
`37-load-and-capacity.md`.

---

## 6. References

- Code: `deploy/compose/control.yml`, `deploy/compose/media.yml`,
  `deploy/single/`, `deploy/nl1/nl1.overlay.yml`,
  `deploy/scripts/helpers.sh`, `.github/workflows/deploy.yml`.
- ADR: [ADR-0001](0001-two-server-topology.md),
  [ADR-0005](0005-locked-architectural-assumptions.md).
- External: [Compose `include`](https://docs.docker.com/reference/compose-file/include/).

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2026-09-24 | Proposed | Черновик вместе с реализацией; при мерже PR статус меняется на Accepted. |

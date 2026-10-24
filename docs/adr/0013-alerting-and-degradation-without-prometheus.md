# ADR-0013 — Алерты и деградация без Prometheus

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** ariestop
- **Tags:** observability, alerting, reliability, ux

---

## 1. Context

Прод (`single`, ADR-0011) лежал 2026-10-22 18:04 → 2026-10-23 05:26 UTC:
`startup_check_failed` в restart-loop, никто не узнал об этом 11 часов
(`docs/tasks/audit-2026-10.md`, P0). Шаг 5 плана аудита взял из Phase 3
roadmap три пункта: Prometheus alerting rules в репозитории, canary-деплой
с auto-rollback по `/readyz`, graceful degradation при недоступности
Redis/Postgres.

Как обстоят дела:

- Prometheus/Alertmanager не развёрнуты; `METRICS_ENABLED=false` по
  умолчанию (ADR-0006/0007). Шаблоны правил есть только в
  `docs/35-metrics-and-slo.md` §6.
- Один хост, по одному экземпляру каждого сервиса.
- Автооткат на last-good релиз по `healthcheck.sh` уже есть
  (`deploy_update.sh`, PR #20).
- В GitHub уже есть SSH-секреты деплоя и `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID`.
- `healthcheck.sh` считал «bot running» успешным для контейнера в
  restart-loop (во время P0 — OK при 644 рестартах).
- При недоступности Redis/Postgres обработчики бота отвечали «попробуйте
  ещё раз» — пользователь повторял в ту же ошибку.

**Не цели:** SLO-алерты по burn rate (нужен Prometheus), алерты по
бизнес-метрикам, on-call ротация.

---

## 2. Decision

1. **Алерт «сервис не работает» — Watchdog в GitHub Actions**, не
   Prometheus. `.github/workflows/watchdog.yml` каждые 15 минут по SSH
   выполняет `healthcheck.sh` на каждом хосте топологии и пишет в
   Telegram только смену состояния: DOWN, RECOVERED и напоминание раз
   в ~4 ч (`.github/scripts/watchdog-notify.sh`). Предыдущее состояние —
   из истории запусков самого workflow.
2. **`healthcheck.sh` требует Docker HEALTHCHECK `healthy`** для `bot` и
   `worker` (и `State.Restarting=false`), а не только `running` — иначе
   ни автооткат, ни Watchdog не видят restart-loop.
3. **Graceful degradation — честное сообщение пользователю.** Когда
   неожиданная ошибка (или то, что она оборачивает) — недоступность
   Postgres/Redis, бот отвечает «Сервис временно недоступен — идут
   технические работы. Попробуйте через пару минут.» вместо «попробуйте
   ещё раз» (`app/bot/services/fallback_text.py`). Классификация —
   `app/utils/errors.py` по пакету и имени класса исключения, чтобы слой
   `bot` не импортировал драйверы (`.claude/rules/architecture.md`).
4. **Canary не делаем:** на одном хосте с одним экземпляром каждого
   сервиса не на чем держать долю трафика; защиту даёт preflight +
   автооткат (PR #20). Вернуться — при переходе на несколько реплик.
5. **Prometheus rules откладываем** до развёртывания Prometheus: правила
   без сервера, который их вычисляет, ничего не ловят. Шаблоны — в
   `docs/35-metrics-and-slo.md` §6.

---

## 3. Consequences

### 3.1 Positive

- Падение вида P0 видно в Telegram через ~15 минут, а не через 11 часов.
- Никакой новой инфраструктуры и секретов: SSH и Telegram уже были.
- Деплой с crash-loop'ящимся ботом теперь откатывается автоматически.
- Во время аварии пользователи получают правильный совет — подождать.

### 3.2 Negative / accepted trade-offs

- Расписание GitHub неточное (задержки до десятков минут при нагрузке)
  и отключается после 60 дней без активности в репозитории.
- 96 SSH-логинов в день на хост; лог Watchdog публичный (только строки
  `healthcheck.sh` — без данных).
- Watchdog проверяет здоровье контейнеров и `/readyz`, а не путь
  пользователя целиком (Telegram → бот → загрузка): «бот жив, но YouTube
  отдаёт 403» он не поймает.
- Классификация по имени класса может разойтись с будущими версиями
  драйверов; тесты `app/tests/test_backend_unavailable.py` закрепляют
  поддерживаемые случаи.

---

## 4. Alternatives considered

| Вариант | Почему нет |
|---|---|
| Prometheus + Alertmanager на хосте | +2 контейнера и ~300 МБ RAM на сервере с 4 ГБ, нужно включать метрики и настраивать доставку; оправдано позже, для SLO-алертов |
| Внешний uptime-сервис (UptimeRobot и т. п.) | видит только публичный `/healthz` nginx — во время P0 он отвечал OK (nginx был жив) |
| systemd-таймер на хосте со своим Telegram | ставить и обновлять вручную на каждом хосте; хост, который лёг целиком, о себе не сообщит |
| Обёртка ошибок драйверов в `AppError` в каждом репозитории | правка всех репозиториев и Redis-сервисов ради одного текста ответа |

---

## 5. Compliance

- `app/tests/test_backend_unavailable.py` — классификация исключений.
- `.github/scripts/watchdog-notify.sh` проверяется shellcheck в CI;
  переходы состояний — переменная `WATCHDOG_HISTORY` (см. заголовок скрипта).
- Runbook — `docs/24-runbooks.md` §28.

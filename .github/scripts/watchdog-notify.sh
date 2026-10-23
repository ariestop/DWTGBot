#!/usr/bin/env bash
# =====================================================================
# Decide whether this Watchdog run is news, and tell Telegram if so.
#
#   OUTCOME=success|failure TARGET=single RUN_URL=... GH_TOKEN=... \
#     TG_TOKEN=... TG_CHAT=... watchdog-notify.sh
#
# Compares OUTCOME with the latest completed Watchdog runs (gh, newest
# first): success→failure sends DOWN, failure→success RECOVERED, and
# every REMIND_EVERY-th consecutive failure a reminder. A cancelled or
# skipped probe is not news. WATCHDOG_HISTORY (space-separated
# conclusions, newest first) replaces the gh lookup in tests.
# =====================================================================
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${OUTCOME:?}" "${TARGET:?}" "${RUN_URL:?}"
REMIND_EVERY="${REMIND_EVERY:-16}" # 16 × 15 min ≈ 4 h

if [[ "${OUTCOME}" != success && "${OUTCOME}" != failure ]]; then
  echo "Probe ${OUTCOME}; nothing to report"
  exit 0
fi

if [[ -z "${WATCHDOG_HISTORY+set}" ]]; then
  WATCHDOG_HISTORY="$(gh run list --repo "${GITHUB_REPOSITORY:?}" --workflow watchdog.yml \
    --status completed --limit 40 --json conclusion --jq '[.[].conclusion] | join(" ")')"
fi

# Previous state and how many failures in a row led up to this run.
previous="" streak=0
for c in ${WATCHDOG_HISTORY}; do
  [[ "${c}" == success || "${c}" == failure ]] || continue
  [[ -n "${previous}" ]] || previous="${c}"
  [[ "${c}" == failure ]] || break
  streak=$((streak + 1))
done
previous="${previous:-success}"

text=""
if [[ "${OUTCOME}" == failure ]]; then
  if [[ "${previous}" == success ]]; then
    text="🔴 DWTGBot: ${TARGET} не проходит healthcheck (или недоступен по SSH).
Причина — Actions → Server diagnostics. Лог: ${RUN_URL}"
  elif (((streak + 1) % REMIND_EVERY == 0)); then
    hours=$(((streak + 1) / 4))
    text="🔴 DWTGBot: ${TARGET} всё ещё не проходит healthcheck (~${hours} ч). ${RUN_URL}"
  fi
elif [[ "${previous}" == failure ]]; then
  text="🟢 DWTGBot: ${TARGET} снова проходит healthcheck. ${RUN_URL}"
fi

if [[ -z "${text}" ]]; then
  echo "No state change (${previous} → ${OUTCOME}, streak ${streak})"
  exit 0
fi
echo "${text}"
bash "${here}/telegram-notify.sh" "${text}"

#!/usr/bin/env bash
# =====================================================================
# Follow CI → Build & push images → Deploy for one commit and report the
# outcome to Telegram.
#
#   SHA=<commit> LABEL="yt-dlp 2026.8.19" RUN_URL=<this run> \
#     GH_TOKEN=... TG_TOKEN=... TG_CHAT=... follow-deploy.sh
#
# Exits 0 only when Deploy succeeded and at least one deploy job ran;
# every other outcome is reported to Telegram first, then exits 1.
# =====================================================================
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SHA:?}" "${LABEL:?}" "${RUN_URL:?}"

notify() { bash "${here}/telegram-notify.sh" "$1"; }

# fail REASON HINT URL
fail() {
  trap - ERR
  notify "DWTGBot: ошибка обновления ${LABEL}: $1.
$2
$3"
  echo "::error::$1"
  exit 1
}
trap 'fail "сбой при отслеживании деплоя" "Проверьте состояние вручную." "${RUN_URL}"' ERR

# find_run WORKFLOW_FILE: print the id of the run for SHA (waits up to 30 min).
find_run() {
  local id _
  for _ in $(seq 1 60); do
    id="$(gh run list --workflow "$1" --commit "${SHA}" --limit 1 \
      --json databaseId --jq '.[0].databaseId // empty')"
    if [[ -n "${id}" ]]; then
      echo "${id}"
      return 0
    fi
    sleep 30
  done
  return 1
}

stages=(
  "ci.yml|CI|Сервер не обновлялся."
  "build-images.yml|сборка образов|Сервер не обновлялся."
  "deploy.yml|деплой|Проверьте сервер: healthcheck.sh и логи worker."
)
id="" url="${RUN_URL}"
for stage in "${stages[@]}"; do
  IFS='|' read -r wf name hint <<<"${stage}"
  if ! id="$(find_run "${wf}")"; then
    fail "этап «${name}» не запустился за 30 минут" "${hint}" "${RUN_URL}"
  fi
  echo "Following ${name}: run ${id}"
  gh run watch "${id}" --interval 30 >/dev/null 2>&1 || true
  read -r conclusion url < <(gh run view "${id}" --json conclusion,url --jq '"\(.conclusion) \(.url)"')
  if [[ "${conclusion}" != "success" ]]; then
    fail "этап «${name}» завершился со статусом ${conclusion}" "${hint}" "${url}"
  fi
done

ran="$(gh run view "${id}" --json jobs --jq '[.jobs[] | select(.conclusion == "success")] | length')"
if (( ran == 0 )); then
  fail "деплой пропущен" "Проверьте переменную репозитория DEPLOY_TOPOLOGY." "${url}"
fi

notify "DWTGBot: ${LABEL} установлен на сервере (коммит ${SHA:0:7}).
${url}"
echo "${LABEL} deployed"

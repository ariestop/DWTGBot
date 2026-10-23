#!/usr/bin/env bash
# =====================================================================
# Read-only diagnostics of one stack, safe to print in a public CI log.
#
#   sudo bash diagnostics.sh single|nl1|nl2 [SINCE] [LAST]
#     SINCE  log window for ``docker logs --since`` (default 2h)
#     LAST   error records to show per section (default 30)
#     DEPLOY_REPO_PATH  server checkout, used for healthcheck.sh
#
# Prints host resources, container state, healthcheck.sh results and a
# redacted digest of container logs (log_digest.py next to this file).
# Changes nothing on the host. Run by .github/workflows/diagnostics.yml.
# =====================================================================
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
SINCE="${2:-2h}"
LAST="${3:-30}"
REPO="${DEPLOY_REPO_PATH:-$(cd "${HERE}/../.." && pwd)}"
LOG_TAIL_PER_CONTAINER=5000

[[ "${TARGET}" =~ ^(single|nl1|nl2)$ ]] || { echo "Usage: diagnostics.sh single|nl1|nl2 [SINCE] [LAST]" >&2; exit 2; }
[[ "${SINCE}" =~ ^[0-9]{1,4}[smhd]$ ]] || { echo "SINCE must look like 30m / 2h / 1d" >&2; exit 2; }
[[ "${LAST}" =~ ^[0-9]{1,3}$ ]] || { echo "LAST must be a number" >&2; exit 2; }
# ``docker logs --since`` takes Go durations, which have no ``d`` unit.
if [[ "${SINCE}" == *d ]]; then
  SINCE="$((10#${SINCE%d} * 24))h"
fi

mapfile -t CONTAINERS < <(docker ps -a --filter 'name=^dwtgbot_' --format '{{.Names}}' | sort)

# digest ARGS...: run log_digest.py on the host python3, or inside a
# project image (always has python) when the host has none.
digest() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "${HERE}/log_digest.py" "$@"
    return
  fi
  local image
  image="$(docker inspect --format '{{.Config.Image}}' "${CONTAINERS[0]:-}" 2>/dev/null || true)"
  if [[ -z "${image}" ]]; then
    echo "  (no python3 on host and no dwtgbot image to run log_digest.py)"
    cat >/dev/null
    return
  fi
  docker run --rm -i --network none --user 0:0 --entrypoint python3 \
    -v "${HERE}/log_digest.py:/diag/log_digest.py:ro" "${image}" /diag/log_digest.py "$@"
}
redact() { digest --plain; }
section() { printf '\n===== %s =====\n' "$1"; }

section "Host"
date -u '+%Y-%m-%dT%H:%M:%SZ'
uptime
free -m
df -h -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null | redact
docker system df 2>/dev/null || true

section "Checkout"
git -C "${REPO}" log -1 --format='%h %cd %s' --date=iso 2>/dev/null | redact || echo "  (no git checkout at DEPLOY_REPO_PATH)"

section "Containers"
if ((${#CONTAINERS[@]} == 0)); then
  echo "  no dwtgbot_* containers"
fi
for c in "${CONTAINERS[@]}"; do
  docker inspect --format \
    '{{.Name}} image={{.Config.Image}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} restarts={{.RestartCount}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} started={{.State.StartedAt}}' \
    "${c}" | redact
  # The last failed probe says why Docker marks a container unhealthy.
  docker inspect --format \
    '{{if .State.Health}}{{range .State.Health.Log}}{{if ne .ExitCode 0}}  probe exit={{.ExitCode}}: {{.Output}}{{"\n"}}{{end}}{{end}}{{end}}' \
    "${c}" | tail -n 3 | redact
done

section "healthcheck.sh ${TARGET}"
if [[ -f "${REPO}/deploy/scripts/healthcheck.sh" ]]; then
  NO_COLOR=1 timeout 300 bash "${REPO}/deploy/scripts/healthcheck.sh" "${TARGET}" 2>&1 | redact || true
else
  echo "  (healthcheck.sh not found under ${REPO})"
fi

section "Logs since ${SINCE} (redacted digest)"
for c in "${CONTAINERS[@]}"; do
  docker logs --timestamps --since "${SINCE}" --tail "${LOG_TAIL_PER_CONTAINER}" "${c}" 2>&1 \
    | sed "s/^/${c} | /" || true
done | digest --last "${LAST}"

#!/usr/bin/env bash
# =====================================================================
# Healthcheck — verifies tooling, env, containers and service endpoints.
# Usage:
#   bash deploy/scripts/healthcheck.sh         # auto-detect stack
#   bash deploy/scripts/healthcheck.sh nl1     # only NL-1
#   bash deploy/scripts/healthcheck.sh nl2     # only NL-2
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${1:-auto}"
FAIL=0

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    log_ok "${name}"
  else
    log_error "${name}"
    FAIL=$((FAIL + 1))
  fi
}

log_step "Toolchain"
check "docker present"          has_command docker
check "docker compose present"  bash -c '"${DOCKER_COMPOSE[@]:-docker compose}" version'
check "curl present"            has_command curl

if [[ "${TARGET}" == "auto" || "${TARGET}" == "nl1" ]]; then
  if [[ -f "${DEPLOY_DIR}/nl1/.env" ]]; then
    log_step "NL-1 stack"
    check "compose config valid"     compose_nl1 config -q
    check "postgres healthy"         bash -c 'compose_nl1 ps --status running postgres | grep -q dwtgbot_postgres'
    check "redis healthy"            bash -c 'compose_nl1 ps --status running redis    | grep -q dwtgbot_redis'
    check "bot running"              bash -c 'compose_nl1 ps --status running bot      | grep -q dwtgbot_bot'
    check "internal /healthz"        bash -c 'compose_nl1 exec -T api curl -fsS http://127.0.0.1:8080/healthz'
  else
    log_warn "NL-1 .env not found, skipping NL-1 checks"
  fi
fi

if [[ "${TARGET}" == "auto" || "${TARGET}" == "nl2" ]]; then
  if [[ -f "${DEPLOY_DIR}/nl2/.env" ]]; then
    log_step "NL-2 stack"
    check "compose config valid"     compose_nl2 config -q
    check "api running"              bash -c 'compose_nl2 ps --status running api     | grep -q dwtgbot_api'
    check "worker running"           bash -c 'compose_nl2 ps --status running worker  | grep -q dwtgbot_worker'
    check "nginx running"            bash -c 'compose_nl2 ps --status running nginx   | grep -q dwtgbot_nginx'
    check "internal /healthz"        bash -c 'compose_nl2 exec -T api curl -fsS http://127.0.0.1:8080/healthz'
    check "internal /readyz"         bash -c 'compose_nl2 exec -T api curl -fsS http://127.0.0.1:8080/readyz'
    check "nginx /healthz"           bash -c 'compose_nl2 exec -T nginx wget -qO- http://127.0.0.1/healthz | grep -q ok'
  else
    log_warn "NL-2 .env not found, skipping NL-2 checks"
  fi
fi

if [[ "${FAIL}" -gt 0 ]]; then
  log_error "${FAIL} check(s) failed"
  exit 1
fi
log_ok "All checks passed"

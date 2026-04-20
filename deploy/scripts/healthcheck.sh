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
# ``DOCKER_COMPOSE`` array expansion inside ``bash -c '...'`` breaks the
# probe — call the plugin form directly (same as operators type by hand).
check "docker compose present"  bash -c 'docker compose version >/dev/null 2>&1'
check "curl present"            has_command curl

if [[ "${TARGET}" == "auto" || "${TARGET}" == "nl1" ]]; then
  if [[ -f "${DEPLOY_DIR}/nl1/.env" ]]; then
    log_step "NL-1 stack"
    check "compose config valid"     compose_nl1 config -q
    # Prefer Docker health status; fall back to running (no healthcheck).
    check "postgres healthy"         bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_postgres 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
    check "redis healthy"            bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_redis 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
    check "bot running"              bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_bot 2>/dev/null | grep -qx running'
    check "internal /healthz"        bash -c 'compose_nl1 exec -T api curl -fsS http://127.0.0.1:8080/healthz'
  else
    log_warn "NL-1 .env not found, skipping NL-1 checks"
  fi
fi

if [[ "${TARGET}" == "auto" || "${TARGET}" == "nl2" ]]; then
  if [[ -f "${DEPLOY_DIR}/nl2/.env" ]]; then
    log_step "NL-2 stack"
    check "compose config valid"     compose_nl2 config -q
    check "api running"              bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_api 2>/dev/null | grep -qx running'
    check "worker running"           bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_worker 2>/dev/null | grep -qx running'
    check "nginx running"            bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_nginx 2>/dev/null | grep -qx running'
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

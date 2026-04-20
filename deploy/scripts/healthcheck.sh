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

# ``compose_nl1`` / ``compose_nl2`` are shell functions — they are *not*
# visible inside ``bash -c '...'``, so probes must use ``docker exec`` or
# call the wrapper in the current shell (see git history).
_nl1_curl_healthz() {
  local port="${1:-8080}"
  local _
  for _ in {1..20}; do
    if docker exec dwtgbot_api_nl1 curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

_nl2_curl_healthz() {
  local port="${1:-8080}"
  local _
  for _ in {1..20}; do
    if docker exec dwtgbot_api curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

_nl2_curl_readyz() {
  local port="${1:-8080}"
  local _
  for _ in {1..20}; do
    if docker exec dwtgbot_api curl -fsS "http://127.0.0.1:${port}/readyz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
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
    NL1_API_PORT="$(grep -E '^API_PORT=' "${DEPLOY_DIR}/nl1/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
    NL1_API_PORT="${NL1_API_PORT:-8080}"
    check "compose config valid"     compose_nl1 config -q
    # Prefer Docker health status; fall back to running (no healthcheck).
    check "postgres healthy"         bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_postgres 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
    check "redis healthy"            bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_redis 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
    check "bot running"              bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_bot 2>/dev/null | grep -qx running'
    check "internal /healthz"        _nl1_curl_healthz "${NL1_API_PORT}"
  else
    log_warn "NL-1 .env not found, skipping NL-1 checks"
  fi
fi

if [[ "${TARGET}" == "auto" || "${TARGET}" == "nl2" ]]; then
  if [[ -f "${DEPLOY_DIR}/nl2/.env" ]]; then
    log_step "NL-2 stack"
    NL2_API_PORT="$(grep -E '^API_PORT=' "${DEPLOY_DIR}/nl2/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
    NL2_API_PORT="${NL2_API_PORT:-8080}"
    check "compose config valid"     compose_nl2 config -q
    check "api running"              bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_api 2>/dev/null | grep -qx running'
    check "worker running"           bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_worker 2>/dev/null | grep -qx running'
    check "nginx running"            bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_nginx 2>/dev/null | grep -qx running'
    check "internal /healthz"        _nl2_curl_healthz "${NL2_API_PORT}"
    check "internal /readyz"         _nl2_curl_readyz "${NL2_API_PORT}"
    check "nginx /healthz"           bash -c 'docker exec dwtgbot_nginx wget -qO- http://127.0.0.1/healthz | grep -q ok'
  else
    log_warn "NL-2 .env not found, skipping NL-2 checks"
  fi
fi

if [[ "${FAIL}" -gt 0 ]]; then
  log_error "${FAIL} check(s) failed"
  exit 1
fi
log_ok "All checks passed"

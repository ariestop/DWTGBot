#!/usr/bin/env bash
# =====================================================================
# Healthcheck — verifies tooling, env, containers and service endpoints.
# Usage:
#   bash deploy/scripts/healthcheck.sh          # every stack with an .env here
#   bash deploy/scripts/healthcheck.sh single   # only the single-host stack
#   bash deploy/scripts/healthcheck.sh nl1      # only NL-1
#   bash deploy/scripts/healthcheck.sh nl2      # only NL-2
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

# ``compose_stack`` / ``compose_nl1`` / ``compose_nl2`` are shell functions — they are *not*
# visible inside ``bash -c '...'``, so probes must use ``docker exec`` or
# call the wrapper in the current shell (see git history).
_nl1_curl_healthz() {
  local port="${1:-8080}"
  local _
  for _ in {1..45}; do
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
  for _ in {1..45}; do
    if docker exec dwtgbot_api curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

_nl2_curl_readyz() {
  # Audit fix A30: ``/readyz`` is guarded by X-Internal-Token (A16).
  # Exec via ``sh -c`` inside the api container and read the token from
  # that container's env — keeps the secret out of the host process
  # list and of the operator's shell history.
  local port="${1:-8080}"
  local _
  for _ in {1..45}; do
    if docker exec dwtgbot_api sh -c \
        "curl -fsS -H \"X-Internal-Token: \$API_INTERNAL_TOKEN\" http://127.0.0.1:${port}/readyz" \
        >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# Strip quotes / whitespace from ``API_PORT=`` lines (``API_PORT="8080"`` is common).
_sanitize_env_value() {
  local v="$1"
  v="${v%%#*}"      # drop ``#`` inline comments
  v="${v//$'\r'/}"  # Windows line endings
  v="${v//\"/}"
  v="${v//\'/}"
  v="${v// /}"
  printf '%s' "${v}"
}

# Bot may still be ``created`` / ``restarting`` for a few seconds after ``compose up``.
_nl1_bot_running() {
  local _
  for _ in {1..45}; do
    if docker inspect --format "{{.State.Status}}" dwtgbot_bot 2>/dev/null | grep -qx running; then
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

_api_port_of() {
  local port
  port="$(grep -E '^API_PORT=' "$(stack_env_file "$1")" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  port="$(_sanitize_env_value "${port}")"
  printf '%s' "${port:-8080}"
}

# Control-plane probes (single / NL-1).
_check_control_plane() {
  # Prefer Docker health status; fall back to running (no healthcheck).
  check "postgres healthy"         bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_postgres 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
  check "redis healthy"            bash -c 's=$(docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" dwtgbot_redis 2>/dev/null || true); [[ "$s" == healthy || "$s" == running ]]'
  check "bot running"              _nl1_bot_running
}

# Media-plane probes (single / NL-2). Container name is ``dwtgbot_api``
# in both stacks.
_check_media_plane() {
  local port="$1"
  check "api running"              bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_api 2>/dev/null | grep -qx running'
  check "worker running"           bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_worker 2>/dev/null | grep -qx running'
  check "nginx running"            bash -c 'docker inspect --format "{{.State.Status}}" dwtgbot_nginx 2>/dev/null | grep -qx running'
  check "internal /healthz"        _nl2_curl_healthz "${port}"
  check "internal /readyz"         _nl2_curl_readyz "${port}"
  check "nginx /healthz"           bash -c 'docker exec dwtgbot_nginx wget -qO- http://127.0.0.1/healthz | grep -q ok'
}

if [[ "${TARGET}" != "auto" ]]; then
  is_valid_stack "${TARGET}" || die "Usage: healthcheck.sh [single|nl1|nl2]"
fi

for STACK in "${STACKS[@]}"; do
  [[ "${TARGET}" == "auto" || "${TARGET}" == "${STACK}" ]] || continue
  if [[ ! -f "$(stack_env_file "${STACK}")" ]]; then
    [[ "${TARGET}" == "auto" ]] || log_warn "${STACK} .env not found, skipping ${STACK} checks"
    continue
  fi
  log_step "${STACK} stack"
  API_PORT_VALUE="$(_api_port_of "${STACK}")"
  check "compose config valid"     compose_stack "${STACK}" config -q
  if stack_has_control_plane "${STACK}"; then
    _check_control_plane
  fi
  if [[ "${STACK}" == "nl1" ]]; then
    check "internal /healthz"      _nl1_curl_healthz "${API_PORT_VALUE}"
  fi
  if stack_has_media_plane "${STACK}"; then
    _check_media_plane "${API_PORT_VALUE}"
  fi
done

if [[ "${TARGET}" == "auto" && "$(detect_stack)" == "none" ]]; then
  log_warn "No .env found in deploy/single, deploy/nl1 or deploy/nl2 — only toolchain checked"
fi

if [[ "${FAIL}" -gt 0 ]]; then
  log_error "${FAIL} check(s) failed"
  exit 1
fi
log_ok "All checks passed"

#!/usr/bin/env bash
# =====================================================================
# Shared shell helpers for deploy/scripts/*.
# Source this file from other scripts; do not execute directly.
# =====================================================================

# shellcheck disable=SC2034

# ---------- safety ----------
set -Eeuo pipefail

# ---------- paths ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_DIR="${PROJECT_ROOT}/deploy"
LOG_FILE="${DWTGBOT_LOG:-/var/log/dwtgbot.log}"

# Fallback log path if not writable.
if ! touch "${LOG_FILE}" 2>/dev/null; then
  LOG_FILE="${HOME}/.dwtgbot.log"
  touch "${LOG_FILE}" 2>/dev/null || LOG_FILE="/tmp/dwtgbot.log"
  touch "${LOG_FILE}" 2>/dev/null || true
fi

# ---------- colors ----------
if [[ -t 1 && "${NO_COLOR:-0}" != "1" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_DIM=$'\033[2m'
else
  C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_DIM=""
fi

# ---------- logging ----------
_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  printf '%s %-5s %s\n' "${ts}" "${level}" "$*" >>"${LOG_FILE}" 2>/dev/null || true
}

log_info()  { printf '%s[INFO ]%s %s\n'  "${C_BLUE}"   "${C_RESET}" "$*"; _log "INFO"  "$*"; }
log_ok()    { printf '%s[ OK  ]%s %s\n'  "${C_GREEN}"  "${C_RESET}" "$*"; _log "OK"    "$*"; }
log_warn()  { printf '%s[WARN ]%s %s\n'  "${C_YELLOW}" "${C_RESET}" "$*"; _log "WARN"  "$*"; }
log_error() { printf '%s[ERROR]%s %s\n'  "${C_RED}"    "${C_RESET}" "$*" >&2; _log "ERROR" "$*"; }
log_step()  { printf '\n%s== %s ==%s\n'  "${C_BOLD}"   "$*" "${C_RESET}"; _log "STEP"  "$*"; }

die() { log_error "$*"; exit 1; }

# ---------- traps ----------
on_error() {
  local code=$?
  log_error "Aborted at line $1 (exit code ${code})"
  exit "${code}"
}
trap 'on_error ${LINENO}' ERR

# ---------- environment / OS detection ----------
require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "This action must be run as root (try with sudo)."
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

has_command() { command -v "$1" >/dev/null 2>&1; }

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VER="${VERSION_ID:-unknown}"
  else
    OS_ID="unknown"; OS_VER="unknown"
  fi
}

detect_docker() {
  if has_command docker; then
    DOCKER_BIN="$(command -v docker)"
  else
    DOCKER_BIN=""
  fi
  if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE=("docker" "compose")
  elif has_command docker-compose; then
    DOCKER_COMPOSE=("docker-compose")
  else
    DOCKER_COMPOSE=()
  fi
}

# ---------- interactivity ----------
confirm() {
  # confirm "Question?" [default=N]
  local prompt="$1" default="${2:-N}"
  local hint="[y/N]"; [[ "${default^^}" == "Y" ]] && hint="[Y/n]"
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  read -r -p "${prompt} ${hint} " ans || true
  ans="${ans:-${default}}"
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

prompt_value() {
  # prompt_value VAR_NAME "Question" [default]
  local __var="$1" __q="$2" __def="${3:-}"
  local __ans
  if [[ -n "${__def}" ]]; then
    read -r -p "${__q} [${__def}]: " __ans || true
    __ans="${__ans:-${__def}}"
  else
    read -r -p "${__q}: " __ans || true
  fi
  printf -v "${__var}" '%s' "${__ans}"
}

# ---------- env file helpers ----------
require_env_file() {
  local path="$1"
  [[ -f "${path}" ]] || die "Missing env file: ${path}"
}

# ---------- compose wrappers ----------
# Auto-pick docker-compose.override.yml when it exists next to the base file.
# This mirrors the default behaviour of `docker compose` invoked without -f
# (which implicitly merges override files). Since we always pass -f explicitly,
# we have to add the override path ourselves or host-specific tweaks (e.g.
# binding postgres/redis to a WireGuard IP) would silently be dropped.
compose_nl1() {
  local env="${DEPLOY_DIR}/nl1/.env"
  local file="${DEPLOY_DIR}/nl1/docker-compose.yml"
  local override="${DEPLOY_DIR}/nl1/docker-compose.override.yml"
  local args=(-f "${file}")
  [[ -f "${override}" ]] && args+=(-f "${override}")
  require_env_file "${env}"
  "${DOCKER_COMPOSE[@]}" "${args[@]}" --env-file "${env}" "$@"
}
compose_nl2() {
  local env="${DEPLOY_DIR}/nl2/.env"
  local file="${DEPLOY_DIR}/nl2/docker-compose.yml"
  local override="${DEPLOY_DIR}/nl2/docker-compose.override.yml"
  local args=(-f "${file}")
  [[ -f "${override}" ]] && args+=(-f "${override}")
  require_env_file "${env}"
  "${DOCKER_COMPOSE[@]}" "${args[@]}" --env-file "${env}" "$@"
}

# ---------- random / secrets ----------
gen_secret() {
  # gen_secret [bytes]
  local n="${1:-24}"
  if has_command openssl; then
    openssl rand -base64 "${n}" | tr -d '/+=' | cut -c1-$((n * 4 / 3))
  else
    head -c "${n}" /dev/urandom | base64 | tr -d '/+=' | cut -c1-$((n * 4 / 3))
  fi
}

# Initialize shared state immediately.
detect_os
detect_docker

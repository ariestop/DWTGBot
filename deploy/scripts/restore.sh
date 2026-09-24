#!/usr/bin/env bash
# =====================================================================
# Postgres restore script.
#
# Usage:
#   bash deploy/scripts/restore.sh                       # interactive picker
#   bash deploy/scripts/restore.sh /path/to/dump.sql.gz  # explicit file
#
# Performs a destructive replace of the target database — confirmation required.
# Runs on the host with Postgres: single (preferred) or NL-1.
#
# IMPORTANT: this script also stops/starts the worker (and cleanup), so
# that it does not see an empty/half-loaded database during the restore
# window. In the single topology they run locally and are stopped via
# compose. In split, the NL-2 worker is stopped over SSH — configure via
# env vars (recommended):
#   NL2_HOST=ops@nl-2.example.com
#   NL2_REPO_PATH=/opt/dwtgbot                  (default)
#   NL2_SSH_OPTS="-o StrictHostKeyChecking=yes" (optional)
# If NL2_HOST is empty, the script falls back to an explicit operator
# confirmation that the worker is already stopped — never assume.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dwtgbot}"
NL2_HOST="${NL2_HOST:-}"
NL2_REPO_PATH="${NL2_REPO_PATH:-/opt/dwtgbot}"
# shellcheck disable=SC2206
NL2_SSH_OPTS=( ${NL2_SSH_OPTS:-} )

STACK="$(find_stack_with control)" \
  || die "No .env for a stack with Postgres (restore runs on the single or NL-1 host)"
# shellcheck disable=SC1090
source "$(stack_env_file "${STACK}")"

run_compose() { compose_stack "${STACK}" "$@"; }

# Track which side-effects to undo on exit.
NL2_WORKER_STOPPED=0
LOCAL_WORKER_STOPPED=0

choose_dump() {
  local explicit="${1:-}"
  if [[ -n "${explicit}" ]]; then
    [[ -f "${explicit}" ]] || die "Backup file not found: ${explicit}"
    DUMP="${explicit}"; return
  fi
  mapfile -t files < <(ls -1t "${BACKUP_DIR}"/dwtgbot_*.sql.gz 2>/dev/null || true)
  [[ "${#files[@]}" -gt 0 ]] || die "No backups found in ${BACKUP_DIR}"
  log_info "Available backups (newest first):"
  local i=1
  for f in "${files[@]}"; do
    printf '  %2d) %s  (%s)\n' "${i}" "$(basename "${f}")" "$(du -h "${f}" | cut -f1)"
    i=$((i + 1))
  done
  read -r -p "Pick a number [1]: " idx
  idx="${idx:-1}"
  DUMP="${files[$((idx - 1))]}"
}

confirm_or_die() {
  log_warn "About to REPLACE database '${POSTGRES_DB}' with: $(basename "${DUMP}")"
  confirm "Are you absolutely sure?" "N" || die "Aborted by user"
}

stop_local_worker() {
  log_step "Stopping worker + cleanup on this host (avoid reads against the half-loaded DB)"
  run_compose stop worker cleanup
  LOCAL_WORKER_STOPPED=1
  log_ok "Local worker + cleanup stopped"
}

start_local_worker() {
  if [[ "${LOCAL_WORKER_STOPPED}" -ne 1 ]]; then return 0; fi
  log_step "Starting worker + cleanup back"
  if run_compose start worker cleanup; then
    log_ok "Local worker + cleanup started"
  else
    log_error "Failed to start worker/cleanup. Start manually:"
    log_error "  docker compose -f deploy/${STACK}/docker-compose.yml --env-file deploy/${STACK}/.env start worker cleanup"
  fi
}

stop_workers() {
  if stack_has_media_plane "${STACK}"; then stop_local_worker; else stop_nl2_worker; fi
}

start_workers() {
  start_local_worker
  start_nl2_worker
}

stop_nl2_worker() {
  log_step "Stopping NL-2 worker (avoid reads against the half-loaded DB)"
  if [[ -n "${NL2_HOST}" ]]; then
    log_info "Reaching ${NL2_HOST} via SSH"
    if ssh "${NL2_SSH_OPTS[@]}" "${NL2_HOST}" \
        "cd '${NL2_REPO_PATH}' && docker compose -f deploy/nl2/docker-compose.yml stop worker"; then
      NL2_WORKER_STOPPED=1
      log_ok "NL-2 worker stopped"
    else
      die "Failed to stop NL-2 worker via SSH (${NL2_HOST}). Aborting BEFORE touching the database."
    fi
  else
    log_warn "NL2_HOST is not set — cannot stop the NL-2 worker automatically."
    log_warn "If you proceed without stopping it, the worker will read a half-loaded"
    log_warn "database during restore and emit cascades of errors / poison rows."
    confirm "Have you ALREADY stopped the NL-2 worker manually?" "N" \
      || die "Aborted: stop the worker first, then re-run."
    NL2_WORKER_STOPPED=1   # operator promised; treat as if we stopped it (so we'll try to start it)
  fi
}

start_nl2_worker() {
  if [[ "${NL2_WORKER_STOPPED}" -ne 1 ]]; then return 0; fi
  log_step "Starting NL-2 worker back"
  if [[ -n "${NL2_HOST}" ]]; then
    if ssh "${NL2_SSH_OPTS[@]}" "${NL2_HOST}" \
        "cd '${NL2_REPO_PATH}' && docker compose -f deploy/nl2/docker-compose.yml start worker"; then
      log_ok "NL-2 worker started"
    else
      log_error "Failed to start NL-2 worker via SSH. Start it manually:"
      log_error "  ssh ${NL2_HOST} 'cd ${NL2_REPO_PATH} && docker compose -f deploy/nl2/docker-compose.yml start worker'"
    fi
  else
    log_warn "NL2_HOST is not set — start the NL-2 worker manually:"
    log_warn "  ssh <nl2-host> 'cd ${NL2_REPO_PATH} && docker compose -f deploy/nl2/docker-compose.yml start worker'"
  fi
}

# Always try to bring the worker back, even if restore failed.
on_exit() {
  local code=$?
  start_workers || true
  exit "${code}"
}
trap on_exit EXIT

restore() {
  log_step "Restoring ${DUMP}"
  log_info "Stopping bot/api/migrate (${STACK}) to release connections"
  run_compose stop bot api migrate >/dev/null || true

  log_info "Dropping/recreating database"
  run_compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    psql -U "${POSTGRES_USER}" -d postgres -c \
      "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\" WITH (FORCE);"
  run_compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    psql -U "${POSTGRES_USER}" -d postgres -c \
      "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";"

  log_info "Loading dump"
  gunzip -c "${DUMP}" | run_compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --quiet

  log_info "Restarting ${STACK} services"
  run_compose up -d bot api

  log_ok "Restore finished"
}

main() {
  choose_dump "${1:-}"
  confirm_or_die
  stop_workers
  restore
  # Workers are restarted by the EXIT trap.
}

main "$@"

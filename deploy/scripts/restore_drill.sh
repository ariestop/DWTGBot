#!/usr/bin/env bash
# =====================================================================
# Restore-drill: verify the latest backup actually restores into a
# disposable Postgres instance. Run nightly from CI (S8) or manually
# during incident drills.
#
# Strategy:
#   1. Locate the newest local dump (or pull one from $BACKUP_S3_BUCKET
#      / $BACKUP_RCLONE_REMOTE when --from-offsite is passed).
#   2. Spin up a throwaway postgres container (dwtgbot_restore_drill).
#   3. Pipe the gzipped dump through psql.
#   4. Run a smoke query: every expected table must exist.
#   5. Tear down the container regardless of outcome.
#
# Exit code: 0 on success, non-zero on any verification failure.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dwtgbot}"
DRILL_CONTAINER="dwtgbot_restore_drill"
DRILL_PORT="${DRILL_PORT:-55432}"
DRILL_DB="${DRILL_DB:-dwtgbot_drill}"
DRILL_USER="${DRILL_USER:-drill}"
DRILL_PASS="${DRILL_PASS:-$(gen_secret 24)}"
DRILL_IMAGE="${DRILL_IMAGE:-postgres:16-alpine}"

# Tables that MUST be present after a successful restore. Update when
# you add a new table via Alembic. Keep this list short — the goal is
# a smoke test, not a schema diff.
REQUIRED_TABLES=("download_jobs" "media_cache" "temp_links")

require_command docker
require_command psql
require_command gunzip

cleanup() {
  if docker ps -a --format '{{.Names}}' | grep -q "^${DRILL_CONTAINER}$"; then
    log_info "Cleaning up drill container ${DRILL_CONTAINER}"
    docker rm -f "${DRILL_CONTAINER}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

pick_dump() {
  if [[ "${1:-}" == "--from-offsite" ]]; then
    pull_from_offsite
  fi
  local dump
  dump="$(ls -1t "${BACKUP_DIR}"/dwtgbot_*.sql.gz 2>/dev/null | head -n1)"
  [[ -n "${dump}" ]] || die "No backup found in ${BACKUP_DIR} (try --from-offsite)"
  printf '%s' "${dump}"
}

pull_from_offsite() {
  if [[ -n "${BACKUP_S3_BUCKET:-}" ]] && has_command aws; then
    log_info "Syncing latest dump from s3://${BACKUP_S3_BUCKET}"
    mkdir -p "${BACKUP_DIR}"
    aws s3 sync --only-show-errors \
      "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-dwtgbot}/" "${BACKUP_DIR}/" \
      --exclude "*" --include "dwtgbot_*.sql.gz"
  elif [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]] && has_command rclone; then
    log_info "Syncing latest dump from ${BACKUP_RCLONE_REMOTE}"
    mkdir -p "${BACKUP_DIR}"
    rclone copy --quiet --include 'dwtgbot_*.sql.gz' \
      "${BACKUP_RCLONE_REMOTE}" "${BACKUP_DIR}/"
  else
    die "--from-offsite requested but no BACKUP_S3_BUCKET / BACKUP_RCLONE_REMOTE configured"
  fi
}

start_drill_postgres() {
  log_info "Starting throwaway postgres ${DRILL_IMAGE}"
  docker run -d --rm \
    --name "${DRILL_CONTAINER}" \
    -e POSTGRES_DB="${DRILL_DB}" \
    -e POSTGRES_USER="${DRILL_USER}" \
    -e POSTGRES_PASSWORD="${DRILL_PASS}" \
    -p "${DRILL_PORT}:5432" \
    "${DRILL_IMAGE}" >/dev/null

  log_info "Waiting for drill postgres to accept connections"
  local i=0
  until PGPASSWORD="${DRILL_PASS}" psql -h 127.0.0.1 -p "${DRILL_PORT}" \
        -U "${DRILL_USER}" -d "${DRILL_DB}" -c 'SELECT 1' >/dev/null 2>&1; do
    i=$((i + 1))
    [[ ${i} -gt 60 ]] && die "Drill postgres did not become ready within 60s"
    sleep 1
  done
  log_ok "Drill postgres is ready on 127.0.0.1:${DRILL_PORT}"
}

restore_dump() {
  local dump="$1"
  log_info "Restoring ${dump} into drill DB"
  gunzip -c "${dump}" \
    | PGPASSWORD="${DRILL_PASS}" psql -h 127.0.0.1 -p "${DRILL_PORT}" \
        -U "${DRILL_USER}" -d "${DRILL_DB}" -v ON_ERROR_STOP=1 -q
  log_ok "Dump restored"
}

verify_schema() {
  log_info "Verifying required tables: ${REQUIRED_TABLES[*]}"
  local missing=()
  for tbl in "${REQUIRED_TABLES[@]}"; do
    if ! PGPASSWORD="${DRILL_PASS}" psql -h 127.0.0.1 -p "${DRILL_PORT}" \
         -U "${DRILL_USER}" -d "${DRILL_DB}" -tAc "SELECT to_regclass('public.${tbl}')" \
         | grep -q "${tbl}"; then
      missing+=("${tbl}")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    die "Restore drill FAILED — missing tables: ${missing[*]}"
  fi
  log_ok "All required tables present"
}

main() {
  local dump
  dump="$(pick_dump "${1:-}")"
  log_info "Selected dump: ${dump}"
  start_drill_postgres
  restore_dump "${dump}"
  verify_schema
  log_ok "Restore drill PASSED"
}

main "$@"

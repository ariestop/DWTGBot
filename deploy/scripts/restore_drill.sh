#!/usr/bin/env bash
# =====================================================================
# Restore-drill: verify the newest backup actually restores into a
# disposable Postgres. Run weekly on the control-plane host by
# .github/workflows/restore-drill-server.yml, or manually:
#
#   sudo bash deploy/scripts/restore_drill.sh [--from-offsite]
#
# Strategy:
#   1. (--from-offsite) pull dumps from $BACKUP_S3_BUCKET /
#      $BACKUP_RCLONE_REMOTE into $BACKUP_DIR first.
#   2. Start a throwaway postgres container (dwtgbot_restore_drill,
#      --network none) with both dump locations mounted read-only:
#        * volume $BACKUP_VOLUME — the daily dumps of the backup service;
#        * $BACKUP_DIR on the host — pre-deploy and off-site dumps.
#   3. Pick the newest dwtgbot_*.sql.gz; fail if it is older than
#      $DRILL_MAX_AGE_HOURS (backups have stopped).
#   4. gunzip | psql inside the container; every expected table must
#      exist; print row counts and the Alembic revision.
#   5. Remove the container regardless of outcome.
#
# Needs only docker on the host: psql and gunzip run in the container.
# Output is safe for a public CI log — psql errors are reduced to their
# ERROR: lines with quoted values masked (DETAIL/CONTEXT lines and the
# message itself can quote row data).
# Exit code: 0 on success, non-zero on any verification failure.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dwtgbot}"
BACKUP_VOLUME="${BACKUP_VOLUME:-dwtgbot_backups}"
DRILL_CONTAINER="dwtgbot_restore_drill"
DRILL_DB="${DRILL_DB:-dwtgbot_drill}"
DRILL_USER="${DRILL_USER:-drill}"
DRILL_PASS="${DRILL_PASS:-$(gen_secret 24)}"
DRILL_IMAGE="${DRILL_IMAGE:-postgres:16-alpine}"
DRILL_MAX_AGE_HOURS="${DRILL_MAX_AGE_HOURS:-48}"

# Tables that MUST be present after a successful restore. Update when
# you add a new table via Alembic. Keep this list short — the goal is
# a smoke test, not a schema diff.
REQUIRED_TABLES=("download_jobs" "media_cache" "temp_links")

require_command docker

cleanup() {
  if docker ps -a --format '{{.Names}}' | grep -q "^${DRILL_CONTAINER}$"; then
    log_info "Cleaning up drill container ${DRILL_CONTAINER}"
    docker rm -f "${DRILL_CONTAINER}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

in_drill() { docker exec -i -e PGPASSWORD="${DRILL_PASS}" "${DRILL_CONTAINER}" "$@"; }
# TCP, not the socket: the image's init phase runs a socket-only server
# that is shut down again, so only a TCP answer means "really ready".
drill_psql() { in_drill psql -h 127.0.0.1 -U "${DRILL_USER}" -d "${DRILL_DB}" -v ON_ERROR_STOP=1 "$@"; }

pull_from_offsite() {
  mkdir -p "${BACKUP_DIR}"
  if [[ -n "${BACKUP_S3_BUCKET:-}" ]] && has_command aws; then
    log_info "Syncing dumps from s3://${BACKUP_S3_BUCKET}"
    aws s3 sync --only-show-errors \
      "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-dwtgbot}/" "${BACKUP_DIR}/" \
      --exclude "*" --include "dwtgbot_*.sql.gz"
  elif [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]] && has_command rclone; then
    log_info "Syncing dumps from ${BACKUP_RCLONE_REMOTE}"
    rclone copy --quiet --include 'dwtgbot_*.sql.gz' \
      "${BACKUP_RCLONE_REMOTE}" "${BACKUP_DIR}/"
  else
    die "--from-offsite requested but no BACKUP_S3_BUCKET / BACKUP_RCLONE_REMOTE configured"
  fi
}

start_drill_postgres() {
  local mounts=()
  if docker volume inspect "${BACKUP_VOLUME}" >/dev/null 2>&1; then
    mounts+=(-v "${BACKUP_VOLUME}:/backups/volume:ro")
  fi
  if [[ -d "${BACKUP_DIR}" ]]; then
    mounts+=(-v "${BACKUP_DIR}:/backups/host:ro")
  fi
  ((${#mounts[@]} > 0)) || die "No backups here: neither volume ${BACKUP_VOLUME} nor ${BACKUP_DIR} exists"

  log_info "Starting throwaway postgres ${DRILL_IMAGE}"
  docker run -d --rm --network none \
    --name "${DRILL_CONTAINER}" \
    -e POSTGRES_DB="${DRILL_DB}" \
    -e POSTGRES_USER="${DRILL_USER}" \
    -e POSTGRES_PASSWORD="${DRILL_PASS}" \
    "${mounts[@]}" \
    "${DRILL_IMAGE}" >/dev/null

  log_info "Waiting for drill postgres to accept connections"
  local i=0
  until drill_psql -tAc 'SELECT 1' >/dev/null 2>&1; do
    i=$((i + 1))
    ((i <= 60)) || die "Drill postgres did not become ready within 60s"
    sleep 1
  done
  log_ok "Drill postgres is ready"
}

# Newest dump across both mounts (ls -t sorts all operands together).
newest_dump() {
  in_drill sh -c 'ls -1t /backups/*/dwtgbot_*.sql.gz 2>/dev/null | head -n 1'
}

check_age() {
  local dump="$1" mtime age_hours
  mtime="$(in_drill stat -c %Y "${dump}")"
  age_hours=$(( ($(date +%s) - mtime) / 3600 ))
  DUMP_AGE_HOURS="${age_hours}"
  if ((age_hours > DRILL_MAX_AGE_HOURS)); then
    die "Newest dump ${dump##*/} is ${age_hours}h old (> ${DRILL_MAX_AGE_HOURS}h): backups have stopped"
  fi
  log_ok "Newest dump is ${age_hours}h old"
}

restore_dump() {
  local dump="$1"
  log_info "Restoring ${dump##*/} into drill DB"
  # The backup image's pg_dump (Debian trixie: client 17) emits
  # ``SET transaction_timeout``, which a 16 server rejects. restore.sh
  # drops the same line; everything else stays under ON_ERROR_STOP.
  if ! in_drill sh -c 'gunzip -c "$1" | sed "/^SET transaction_timeout = /d" \
        | psql -h 127.0.0.1 -U "$2" -d "$3" -v ON_ERROR_STOP=1 -q >/dev/null 2>/tmp/restore.err' \
      _ "${dump}" "${DRILL_USER}" "${DRILL_DB}"; then
    # Postgres quotes the offending value in the message itself
    # (invalid input syntax for type bigint: "<value>"): mask quoted parts.
    in_drill sh -c 'grep -E "ERROR:|gzip:" /tmp/restore.err | head -n 5' \
      | sed -E "s/\"[^\"]*\"/\"…\"/g; s/'[^']*'/'…'/g" | cut -c1-200 >&2 || true
    die "Restore drill FAILED — the dump does not restore"
  fi
  log_ok "Dump restored"
}

verify_schema() {
  log_info "Verifying required tables: ${REQUIRED_TABLES[*]}"
  local missing=() tbl
  for tbl in "${REQUIRED_TABLES[@]}"; do
    if [[ "$(drill_psql -tAc "SELECT to_regclass('public.${tbl}')")" != "${tbl}" ]]; then
      missing+=("${tbl}")
    fi
  done
  if ((${#missing[@]} > 0)); then
    die "Restore drill FAILED — missing tables: ${missing[*]}"
  fi
  log_ok "All required tables present"
}

summary() {
  local dump="$1" rows=() tbl revision
  for tbl in "${REQUIRED_TABLES[@]}"; do
    rows+=("${tbl}=$(drill_psql -tAc "SELECT count(*) FROM public.${tbl}")")
  done
  revision="$(drill_psql -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null || echo none)"
  log_ok "Restore drill PASSED: ${dump##*/} (${DUMP_AGE_HOURS}h old), alembic ${revision}, rows: ${rows[*]}"
}

main() {
  if [[ "${1:-}" == "--from-offsite" ]]; then
    pull_from_offsite
  fi
  start_drill_postgres
  local dump
  dump="$(newest_dump)"
  [[ -n "${dump}" ]] || die "No dwtgbot_*.sql.gz in volume ${BACKUP_VOLUME} or ${BACKUP_DIR} (try --from-offsite)"
  log_info "Selected dump: ${dump}"
  check_age "${dump}"
  restore_dump "${dump}"
  verify_schema
  summary "${dump}"
}

main "$@"

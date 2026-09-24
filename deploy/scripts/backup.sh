#!/usr/bin/env bash
# =====================================================================
# Postgres backup script.
#
# Two run modes:
#   1) Inside the `backup` container — talks to postgres over the
#      private docker network using $POSTGRES_HOST, $POSTGRES_USER, etc.
#   2) On the host (single or NL-1) — auto-detects the compose stack
#      that runs postgres and exec's `pg_dump` inside its container.
#
# Retention: keeps the last $BACKUP_RETENTION_DAYS days of dumps.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dwtgbot}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TS="$(date -u +'%Y%m%dT%H%M%SZ')"
mkdir -p "${BACKUP_DIR}"

run_in_container_mode() {
  require_command pg_dump
  local outfile="${BACKUP_DIR}/dwtgbot_${TS}.sql.gz"
  log_info "Dumping ${POSTGRES_DB} → ${outfile}"
  PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    --no-owner --no-privileges --format=plain \
    | gzip -9 >"${outfile}"
  log_ok "Backup written ($(du -h "${outfile}" | cut -f1))"
}

run_on_host_mode() {
  local stack
  stack="$(find_stack_with control)" \
    || die "No .env found for a stack with Postgres (deploy/single or deploy/nl1)"
  # shellcheck disable=SC1090
  source "$(stack_env_file "${stack}")"
  local outfile="${BACKUP_DIR}/dwtgbot_${TS}.sql.gz"
  log_info "Dumping via compose exec (${stack}) → ${outfile}"
  compose_stack "${stack}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    --no-owner --no-privileges --format=plain \
    | gzip -9 >"${outfile}"
  log_ok "Backup written ($(du -h "${outfile}" | cut -f1))"
}

prune_old() {
  log_info "Pruning backups older than ${RETENTION_DAYS} days"
  find "${BACKUP_DIR}" -type f -name 'dwtgbot_*.sql.gz' -mtime +"${RETENTION_DAYS}" -print -delete \
    | while read -r removed; do log_info "removed: ${removed}"; done
}

# ---------------------------------------------------------------------
# S2 (audit fix): off-site replication.
#
# Local backups protect against accidental DELETE; they do NOT protect
# against losing the host (NL-1, or the only host in the single topology,
# where off-site replication is mandatory). We replicate the latest dump
# to one of:
#
#   - BACKUP_S3_BUCKET  → ``aws s3 cp`` (requires aws-cli + creds)
#   - BACKUP_RCLONE_REMOTE → ``rclone copyto`` (any rclone backend)
#
# Both are best-effort: a failure logs a warning instead of aborting
# the script (the local copy is still valid). Run this inline with the
# normal backup so a single cron entry covers both planes.
# ---------------------------------------------------------------------
replicate_offsite() {
  local outfile="$1"
  local basename
  basename="$(basename "${outfile}")"

  if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
    if ! has_command aws; then
      log_warn "BACKUP_S3_BUCKET set but aws-cli is missing; skipping S3 upload"
    else
      local s3_path="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-dwtgbot}/${basename}"
      log_info "Uploading ${basename} → ${s3_path}"
      if aws s3 cp --only-show-errors "${outfile}" "${s3_path}"; then
        log_ok "S3 upload complete (${s3_path})"
      else
        log_warn "S3 upload failed for ${s3_path} (local copy retained)"
      fi
    fi
  fi

  if [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
    if ! has_command rclone; then
      log_warn "BACKUP_RCLONE_REMOTE set but rclone is missing; skipping remote upload"
    else
      local rc_path="${BACKUP_RCLONE_REMOTE%/}/${basename}"
      log_info "Uploading ${basename} → ${rc_path}"
      if rclone copyto --quiet "${outfile}" "${rc_path}"; then
        log_ok "rclone upload complete (${rc_path})"
      else
        log_warn "rclone upload failed for ${rc_path} (local copy retained)"
      fi
    fi
  fi
}

# ---------------------------------------------------------------------
# Latest local dump path (used by replication + restore drill).
# ---------------------------------------------------------------------
latest_local_dump() {
  ls -1t "${BACKUP_DIR}"/dwtgbot_*.sql.gz 2>/dev/null | head -n1
}

main() {
  # Prefer in-container mode whenever ``pg_dump`` is available *and*
  # ``POSTGRES_HOST`` is set. This covers both the standard NL-1
  # ``backup`` container (``POSTGRES_HOST=postgres`` — docker network
  # name) and any remote Postgres setup (``POSTGRES_HOST=<NL1_IP>``).
  # Falling back to ``run_on_host_mode`` only when ``pg_dump`` is
  # missing is a safer signal that we are running on the bare host.
  if has_command pg_dump && [[ -n "${POSTGRES_HOST:-}" ]]; then
    run_in_container_mode
  elif has_command docker && find_stack_with control >/dev/null; then
    run_on_host_mode
  else
    die "No PG connection info and no compose stack — set POSTGRES_* env or run on the single / NL-1 host"
  fi
  prune_old

  local latest
  latest="$(latest_local_dump || true)"
  if [[ -n "${latest}" ]]; then
    replicate_offsite "${latest}"
  else
    log_warn "No local dump found to replicate (run mode produced no file)"
  fi

  log_ok "Backup complete"
}

main "$@"

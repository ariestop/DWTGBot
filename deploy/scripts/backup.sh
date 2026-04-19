#!/usr/bin/env bash
# =====================================================================
# Postgres backup script.
#
# Two run modes:
#   1) Inside the `backup` container — talks to postgres over the
#      private docker network using $POSTGRES_HOST, $POSTGRES_USER, etc.
#   2) On the host (NL-1) — auto-detects compose stack and exec's
#      `pg_dump` inside the postgres container.
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
  [[ -f "${DEPLOY_DIR}/nl1/.env" ]] || die "NL-1 .env not found"
  # shellcheck disable=SC1091
  source "${DEPLOY_DIR}/nl1/.env"
  local outfile="${BACKUP_DIR}/dwtgbot_${TS}.sql.gz"
  log_info "Dumping via compose exec → ${outfile}"
  compose_nl1 exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
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

main() {
  if [[ -n "${POSTGRES_HOST:-}" && "${POSTGRES_HOST}" != "postgres" ]]; then
    run_in_container_mode
  elif has_command docker && [[ -f "${DEPLOY_DIR}/nl1/docker-compose.yml" ]]; then
    run_on_host_mode
  else
    die "No PG connection info and no compose stack — set POSTGRES_* env or run on NL-1 host"
  fi
  prune_old
  log_ok "Backup complete"
}

main "$@"

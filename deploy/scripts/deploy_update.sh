#!/usr/bin/env bash
# =====================================================================
# Idempotent in-place update of one stack (NL-1 or NL-2).
#
# Usage:
#   bash deploy/scripts/deploy_update.sh nl1
#   bash deploy/scripts/deploy_update.sh nl2
#   IMAGE_TAG=sha-abc123 bash deploy/scripts/deploy_update.sh nl1
#
# Steps:
#   1. git pull (if .git present)
#   2. validate compose config
#   3. (NL-1) backup db before applying migrations
#   4. docker compose pull
#   5. docker compose up -d (recreates only changed containers)
#   6. (NL-1) run alembic migrate one-shot
#   7. healthcheck
#   8. on failure → print rollback hint
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${1:-}"
[[ "${TARGET}" == "nl1" || "${TARGET}" == "nl2" ]] || die "Usage: deploy_update.sh nl1|nl2"

ENV_FILE="${DEPLOY_DIR}/${TARGET}/.env"
require_env_file "${ENV_FILE}"

run_compose() {
  if [[ "${TARGET}" == "nl1" ]]; then compose_nl1 "$@"; else compose_nl2 "$@"; fi
}

git_pull_if_possible() {
  if [[ "${AUTODEPLOY_SKIP_GIT_PULL:-0}" == "1" ]]; then
    log_info "AUTODEPLOY_SKIP_GIT_PULL=1, пропускаю git pull"
    return
  fi
  if [[ -d "${PROJECT_ROOT}/.git" ]]; then
    log_step "git pull"
    git -C "${PROJECT_ROOT}" pull --ff-only || log_warn "git pull failed (continuing)"
  else
    log_info "No .git directory — skipping pull"
  fi
}

validate_config() {
  log_step "Validating compose config"
  run_compose config -q
}

pre_backup() {
  if [[ "${TARGET}" == "nl1" ]]; then
    log_step "Pre-deploy backup"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx dwtgbot_postgres; then
      bash "${SCRIPT_DIR}/backup.sh" || log_warn "Backup failed — continuing under user discretion"
    else
      log_info "Postgres container not running — skipping pre-deploy backup (first boot?)"
    fi
  fi
}

pull_images() {
  log_step "Pulling images"
  run_compose pull
}

apply() {
  log_step "Applying compose up -d"
  run_compose up -d --remove-orphans
}

run_migrations() {
  if [[ "${TARGET}" == "nl1" ]]; then
    log_step "Running migrations"
    compose_nl1 run --rm migrate
  fi
}

verify() {
  log_step "Verifying"
  bash "${SCRIPT_DIR}/healthcheck.sh" "${TARGET}"
}

print_rollback_hint() {
  log_warn "Rollback hint:"
  echo "  Set IMAGE_BOT/IMAGE_API/IMAGE_WORKER to the previous tag in ${ENV_FILE}"
  echo "  Then re-run: bash deploy/scripts/deploy_update.sh ${TARGET}"
  if [[ "${TARGET}" == "nl1" ]]; then
    echo "  If migrations were applied, also restore the latest backup:"
    echo "    bash deploy/scripts/restore.sh"
  fi
}

main() {
  trap 'print_rollback_hint' ERR
  log_info "Updating ${TARGET}"
  git_pull_if_possible
  validate_config
  pre_backup
  pull_images
  apply
  run_migrations
  verify
  log_ok "Deploy ${TARGET} finished"
}

main "$@"

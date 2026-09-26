#!/usr/bin/env bash
# =====================================================================
# Idempotent in-place update of one stack (single, NL-1 or NL-2).
#
# Usage:
#   bash deploy/scripts/deploy_update.sh single
#   bash deploy/scripts/deploy_update.sh nl1
#   bash deploy/scripts/deploy_update.sh nl2
#   IMAGE_TAG=sha-abc123 bash deploy/scripts/deploy_update.sh nl1
#
# Steps:
#   1. git pull (if .git present)
#   2. validate compose config
#   3. (control plane: single / NL-1) backup db before applying migrations
#   4. docker compose pull
#   5. docker compose up -d (recreates only changed containers)
#   6. (control plane) run alembic migrate one-shot
#   7. (media plane: single / NL-2) restart nginx
#   8. healthcheck
#   9. remove old *:sha-* release images (keeps current + previous)
#  10. on failure → print rollback hint
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${1:-}"
is_valid_stack "${TARGET}" || die "Usage: deploy_update.sh single|nl1|nl2"

ENV_FILE="$(stack_env_file "${TARGET}")"
require_env_file "${ENV_FILE}"
YOUTUBE_COOKIE_PATH="${YOUTUBE_COOKIE_PATH:-/srv/dwtgbot/secrets/cookies-youtube.txt}"
INSTAGRAM_COOKIE_PATH="${INSTAGRAM_COOKIE_PATH:-/srv/dwtgbot/secrets/cookies-instagram.txt}"

run_compose() { compose_stack "${TARGET}" "$@"; }

git_pull_if_possible() {
  if [[ "${AUTODEPLOY_SKIP_GIT_PULL:-0}" == "1" ]]; then
    log_info "AUTODEPLOY_SKIP_GIT_PULL=1, пропускаю git pull"
    return
  fi
  if [[ ! -d "${PROJECT_ROOT}/.git" ]]; then
    log_info "No .git directory — skipping pull"
    return
  fi

  # Skip pull cleanly when the working copy is in detached HEAD state.
  # auto_deploy.sh deliberately puts the repo there (via
  # ``git checkout --detach <sha>``) to pin the checkout to the same sha
  # as the docker images. Calling ``git pull`` in that state errors out
  # with "You are not currently on a branch" — that's not a failure
  # operators need to see; it's the expected steady state for a
  # production host running under autodeploy.
  local current_branch
  current_branch="$(git -C "${PROJECT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
  if [[ "${current_branch}" == "HEAD" ]]; then
    log_info "Repo is in detached HEAD (autodeploy-pinned); skipping git pull"
    return
  fi

  log_step "git pull"
  git -C "${PROJECT_ROOT}" pull --ff-only || log_warn "git pull failed (continuing)"
}

validate_config() {
  log_step "Validating compose config"
  require_compose_version
  run_compose config -q
}

pre_backup() {
  if stack_has_control_plane "${TARGET}"; then
    log_step "Pre-deploy backup"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx dwtgbot_postgres; then
      bash "${SCRIPT_DIR}/backup.sh" || log_warn "Backup failed — continuing under user discretion"
    else
      log_info "Postgres container not running — skipping pre-deploy backup (first boot?)"
    fi
  fi
}

ensure_cookie_env() {
  local env_name="$1"
  local cookie_path="$2"

  log_step "Ensuring ${env_name} in ${ENV_FILE}"
  if [[ ! -w "${ENV_FILE}" ]]; then
    log_warn "Cannot modify ${ENV_FILE}; skipping ${env_name} sync"
    return
  fi
  set_env_value "${ENV_FILE}" "${env_name}" "${cookie_path}"
}

# Ensure the host directory backing the bind-mount exists with safe perms,
# so docker compose can mount it into bot/worker even before an operator
# uploads the actual cookies.txt files. Providers treat missing files as
# "no cookies" and proceed (with a warning), so an empty directory is OK.
ensure_secrets_dir() {
  local secrets_dir
  secrets_dir="$(dirname "${INSTAGRAM_COOKIE_PATH}")"
  log_step "Ensuring secrets directory ${secrets_dir}"
  if [[ ! -d "${secrets_dir}" ]]; then
    mkdir -p "${secrets_dir}"
  fi
  # bot/worker run as uid/gid 1000: without the group they cannot enter
  # the directory at all.
  chgrp 1000 "${secrets_dir}" 2>/dev/null || true
  chmod 0750 "${secrets_dir}" 2>/dev/null || true
  if [[ "$(dirname "${YOUTUBE_COOKIE_PATH}")" != "${secrets_dir}" ]]; then
    mkdir -p "$(dirname "${YOUTUBE_COOKIE_PATH}")"
    chmod 0750 "$(dirname "${YOUTUBE_COOKIE_PATH}")" 2>/dev/null || true
  fi
  for cookie_path in "${YOUTUBE_COOKIE_PATH}" "${INSTAGRAM_COOKIE_PATH}"; do
    if [[ -f "${cookie_path}" ]]; then
      chgrp 1000 "${cookie_path}" 2>/dev/null || true
      chmod 0660 "${cookie_path}" 2>/dev/null || true
      log_info "Cookies file present: ${cookie_path}"
    else
      log_warn "Cookies file NOT FOUND at ${cookie_path}"
      log_warn "Upload a Netscape cookies.txt to enable authenticated provider downloads."
    fi
  done
}

PREVIOUS_IMAGES=()

remember_previous_images() {
  mapfile -t PREVIOUS_IMAGES < <(docker ps -a --filter "name=^dwtgbot_" --format '{{.Image}}' 2>/dev/null || true)
}

pull_images() {
  log_step "Pulling images"
  run_compose pull
}

# Every deploy pulls new immutable ``*-{bot,api,worker,backup}:sha-*``
# images; left alone they fill the disk until STORAGE_MIN_FREE_MB makes
# the worker refuse every download. Keeps images of existing containers
# (the new release) and of the release that ran before this deploy, so
# the rollback hint still works without a re-pull.
prune_old_images() {
  log_step "Pruning old release images"
  local -A keep=()
  local image removed=0
  local current=()
  mapfile -t current < <(docker ps -a --format '{{.Image}}' 2>/dev/null || true)
  for image in "${current[@]}" "${PREVIOUS_IMAGES[@]}"; do
    [[ -n "${image}" ]] && keep["${image}"]=1
  done
  while IFS= read -r image; do
    [[ "${image}" =~ -(bot|api|worker|backup):sha-[0-9a-f]+$ ]] || continue
    [[ -n "${keep[${image}]:-}" ]] && continue
    if docker image rm "${image}" >/dev/null 2>&1; then
      removed=$((removed + 1))
    fi
  done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null || true)
  docker image prune -f >/dev/null 2>&1 || true
  log_ok "Removed ${removed} old image(s)"
}

apply() {
  log_step "Applying compose up -d"
  run_compose up -d --remove-orphans
}

# On the media plane the api container gets a new IP every time ``compose up -d``
# recreates it (image rolled, env changed, ...). The nginx config now
# uses a variable in ``proxy_pass`` + Docker's embedded DNS so it
# re-resolves on its own within ``valid=10s`` — but we still bounce
# nginx defensively here. Reasons:
#   1) Belt-and-suspenders: if a future change reintroduces an
#      ``upstream`` block by accident the deploy still self-heals.
#   2) Mounted nginx config files (``conf.d/*.template``,
#      ``snippets/*``) only get re-evaluated by nginx-entrypoint on
#      container start. ``compose up -d`` does NOT recreate nginx
#      when only its bind-mounts changed, so config edits would
#      otherwise sit dormant until the next manual restart. This
#      makes nginx-config changes auto-deployable.
# Cheap (sub-second on a healthy container) and idempotent.
restart_nginx_if_present() {
  if ! stack_has_media_plane "${TARGET}"; then
    return
  fi
  if run_compose ps --services 2>/dev/null | grep -qx nginx; then
    log_step "Restarting nginx to refresh upstream resolution"
    run_compose restart nginx
  fi
}

run_migrations() {
  if stack_has_control_plane "${TARGET}"; then
    log_step "Running migrations"
    run_compose run --rm migrate
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
  if stack_has_control_plane "${TARGET}"; then
    echo "  If migrations were applied, also restore the latest backup:"
    echo "    bash deploy/scripts/restore.sh"
  fi
}

main() {
  trap 'print_rollback_hint' ERR
  log_info "Updating ${TARGET}"
  git_pull_if_possible
  ensure_cookie_env "YOUTUBE_COOKIES_FILE" "${YOUTUBE_COOKIE_PATH}"
  ensure_cookie_env "INSTAGRAM_COOKIES_FILE" "${INSTAGRAM_COOKIE_PATH}"
  ensure_secrets_dir
  validate_config
  pre_backup
  remember_previous_images
  pull_images
  apply
  run_migrations
  restart_nginx_if_present
  verify
  prune_old_images
  log_ok "Deploy ${TARGET} finished"
}

main "$@"

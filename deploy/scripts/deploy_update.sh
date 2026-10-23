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
#   5. preflight: validate_runtime of every app service in the NEW
#      images, with the stack's real env; abort before touching the
#      running containers if it fails (DEPLOY_SKIP_PREFLIGHT=1 skips)
#   6. docker compose up -d (recreates only changed containers)
#   7. (control plane) run alembic migrate one-shot
#   8. (media plane: single / NL-2) restart nginx
#   9. healthcheck
#  10. remember this release as last-good, remove old *:sha-* images
#  11. if 6–9 failed → automatic rollback to the last-good release
#      (skipped when migrations changed the schema; then print the
#      manual rollback hint). DEPLOY_SKIP_ROLLBACK=1 disables it.
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

# Last release that passed healthcheck.sh, as IMAGE_*=<image> lines.
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/dwtgbot}"
LAST_GOOD_FILE="${DEPLOY_STATE_DIR}/last-good-${TARGET}.env"
# compose service → the IMAGE_* variable that selects its image.
RELEASE_SERVICES=(bot:IMAGE_BOT api:IMAGE_API worker:IMAGE_WORKER backup:IMAGE_BACKUP)

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
ROLLBACK_RELEASE=()
DB_REVISION_BEFORE=""

remember_previous_images() {
  mapfile -t PREVIOUS_IMAGES < <(docker ps -a --filter "name=^dwtgbot_" --format '{{.Image}}' 2>/dev/null || true)
}

# Print IMAGE_*=<image> for the release the stack's containers run now.
current_release() {
  local pair svc var image
  for pair in "${RELEASE_SERVICES[@]}"; do
    svc="${pair%%:*}"
    var="${pair#*:}"
    image="$(docker ps -a --filter "name=^dwtgbot_" \
      --filter "label=com.docker.compose.service=${svc}" \
      --filter "label=com.docker.compose.oneoff=False" \
      --format '{{.Image}}' 2>/dev/null | head -n 1 || true)"
    [[ -n "${image}" ]] && printf '%s=%s\n' "${var}" "${image}"
  done
  return 0
}

# Alembic head of the stack's database ("" when there is none here).
db_revision() {
  stack_has_control_plane "${TARGET}" || return 0
  docker exec dwtgbot_postgres sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"' \
    2>/dev/null | tr -d '[:space:]' || true
}

# Rollback target: the last release that passed healthcheck.sh on this
# host; before the first such deploy, whatever the containers run now.
remember_rollback_release() {
  local line
  if [[ -f "${LAST_GOOD_FILE}" ]]; then
    while IFS= read -r line; do
      [[ "${line}" =~ ^IMAGE_(BOT|API|WORKER|BACKUP)=[A-Za-z0-9./:@_-]+$ ]] && ROLLBACK_RELEASE+=("${line}")
    done <"${LAST_GOOD_FILE}"
    log_info "Rollback target: last-good release from ${LAST_GOOD_FILE}"
  else
    mapfile -t ROLLBACK_RELEASE < <(current_release)
    log_info "Rollback target: images running before this deploy (no ${LAST_GOOD_FILE} yet)"
  fi
  DB_REVISION_BEFORE="$(db_revision)"
}

record_last_good() {
  local tmp
  if ! mkdir -p "${DEPLOY_STATE_DIR}" 2>/dev/null; then
    log_warn "Cannot create ${DEPLOY_STATE_DIR}; automatic rollback will fall back to running images"
    return 0
  fi
  tmp="${LAST_GOOD_FILE}.tmp"
  if current_release >"${tmp}" && mv -f "${tmp}" "${LAST_GOOD_FILE}"; then
    log_ok "Recorded last-good release in ${LAST_GOOD_FILE}"
  else
    rm -f -- "${tmp}"
    log_warn "Could not write ${LAST_GOOD_FILE}"
  fi
}

pull_images() {
  log_step "Pulling images"
  run_compose pull
}

# Runs inside each new app image with the service's real environment.
# Prints only setting names and messages: the deploy log is public and a
# pydantic error would otherwise echo the offending value.
PREFLIGHT_PY='
import sys
try:
    from app.config import get_settings
    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
except RuntimeError as exc:
    cause = exc.__cause__
    if hasattr(cause, "errors"):
        errors = [".".join(map(str, e["loc"])) + ": " + e["msg"] for e in cause.errors()]
    else:
        errors = ["invalid configuration (details withheld)"]
for err in errors:
    print("    " + err)
sys.exit(1 if errors else 0)
'

# Catch configuration the new release refuses to start with (a new
# required setting, a stricter check) before ``up -d`` replaces the
# running containers: the 2026-10-22 outage was exactly that.
preflight_config() {
  if [[ "${DEPLOY_SKIP_PREFLIGHT:-0}" == "1" ]]; then
    log_warn "DEPLOY_SKIP_PREFLIGHT=1, skipping preflight"
    return 0
  fi
  log_step "Preflight: journald log driver"
  # Every service logs via journald (deploy/compose/*.yml); without it no
  # container — the preflight ones below included — can start.
  if docker info --format '{{range .Plugins.Log}}{{.}} {{end}}' 2>/dev/null | grep -qw journald \
      && [[ -S /run/systemd/journal/socket ]]; then
    log_ok "journald available"
  else
    log_error "Docker cannot log to journald here (needs systemd-journald running)"
    return 1
  fi
  log_step "Preflight: app config in the new images"
  local services=() failed=() svc
  mapfile -t services < <(run_compose config --services)
  for svc in bot api worker cleanup; do
    [[ " ${services[*]} " == *" ${svc} "* ]] || continue
    if run_compose run --rm --no-deps -T --name "dwtgbot_preflight_${svc}_$$" \
        --entrypoint python "${svc}" -c "${PREFLIGHT_PY}"; then
      log_ok "${svc}: config accepted"
    else
      log_error "${svc}: config rejected (see above)"
      failed+=("${svc}")
    fi
  done
  if ((${#failed[@]} > 0)); then
    log_error "Preflight rejected the config of: ${failed[*]}"
    return 1
  fi
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

# apply → migrate → nginx → healthcheck, failing fast. Called from an
# ``if`` so a failure reaches rollback_release instead of the ERR trap.
release_new() {
  apply && run_migrations && restart_nginx_if_present && verify
}

rollback_release() {
  log_step "Automatic rollback"
  if [[ "${DEPLOY_SKIP_ROLLBACK:-0}" == "1" ]]; then
    log_warn "DEPLOY_SKIP_ROLLBACK=1, leaving the failed release in place"
    print_rollback_hint
    return
  fi
  if ((${#ROLLBACK_RELEASE[@]} == 0)); then
    log_warn "No previous release on this host (first deploy?) — nothing to roll back to"
    print_rollback_hint
    return
  fi
  local revision_now
  revision_now="$(db_revision)"
  if [[ "${revision_now}" != "${DB_REVISION_BEFORE}" ]]; then
    log_error "Migrations moved the schema ${DB_REVISION_BEFORE:-none} -> ${revision_now:-none}; the previous images may not run on it, not rolling back automatically"
    print_rollback_hint
    return
  fi
  local line var same=1
  for line in "${ROLLBACK_RELEASE[@]}"; do
    var="${line%%=*}"
    [[ "${!var:-}" == "${line#*=}" ]] || same=0
  done
  if ((same)); then
    log_warn "The rollback target is the release that just failed — nothing to roll back to"
    print_rollback_hint
    return
  fi
  for line in "${ROLLBACK_RELEASE[@]}"; do
    log_info "  ${line}"
    export "${line?}"
  done
  if run_compose up -d --remove-orphans && verify; then
    log_warn "Rolled back: ${TARGET} is healthy on the previous release; the new one stays undeployed"
  else
    log_error "Rollback did not bring ${TARGET} back to healthy"
    print_rollback_hint
  fi
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
  remember_rollback_release
  pull_images
  preflight_config || die "Preflight failed — the running release was not touched"
  if ! release_new; then
    log_error "New release failed to come up healthy"
    rollback_release
    exit 1
  fi
  record_last_good
  prune_old_images
  log_ok "Deploy ${TARGET} finished"
}

main "$@"

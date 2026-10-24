#!/usr/bin/env bash
# =====================================================================
# Free disk: remove Docker images nothing needs and the build cache.
#
#   sudo bash deploy/scripts/docker_prune.sh
#
# Removes stopped one-off containers (`compose run` leftovers, e.g. a
# deploy preflight interrupted before --rm cleaned up): they pin old
# images and are never started again. Other stopped containers are only
# listed — they may belong to another project on the host.
#
# Keeps every image a container (running or stopped) uses and every
# image of a last-good release (/var/lib/dwtgbot/last-good-*.env), so
# deploy_update.sh can still roll back without a pull. Everything else
# goes, plus the whole build cache (the stacks pull images, they never
# build on the host). Idempotent. Run by .github/workflows/maintenance.yml
# (action docker-prune).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/dwtgbot}"

require_root
require_command docker

log_step "Before"
docker system df

stopped=(--filter status=exited --filter status=created --filter status=dead)
log_step "Stopped containers"
docker ps -a "${stopped[@]}" \
  --format '{{.Names}}  {{.Image}}  {{.Status}}  project={{.Label "com.docker.compose.project"}} oneoff={{.Label "com.docker.compose.oneoff"}}'
mapfile -t oneoff < <(docker ps -aq "${stopped[@]}" --filter label=com.docker.compose.oneoff=True)
if ((${#oneoff[@]} > 0)); then
  docker rm "${oneoff[@]}" >/dev/null
  log_ok "Removed ${#oneoff[@]} stopped one-off container(s)"
else
  log_info "No stopped one-off containers"
fi

declare -A keep=()
while IFS= read -r image; do
  [[ -n "${image}" ]] && keep["${image}"]=1
done < <(docker ps -a --format '{{.Image}}')
shopt -s nullglob
for file in "${DEPLOY_STATE_DIR}"/last-good-*.env; do
  while IFS='=' read -r _ image; do
    [[ -n "${image}" ]] && keep["${image}"]=1
  done <"${file}"
done
shopt -u nullglob
log_info "Keeping ${#keep[@]} image(s) in use or of a last-good release"

removed=0
while IFS= read -r ref; do
  [[ "${ref}" == *"<none>"* ]] && continue
  [[ -n "${keep[${ref}]:-}" ]] && continue
  if docker image rm "${ref}" >/dev/null 2>&1; then
    removed=$((removed + 1))
  fi
done < <(docker image ls --format '{{.Repository}}:{{.Tag}}')
# Untagged layers left by the removals and by earlier pulls.
docker image prune -f >/dev/null
log_ok "Removed ${removed} tagged image(s) and the dangling ones"

docker builder prune -af >/dev/null
log_ok "Build cache cleared"

log_step "After"
docker system df
df -h / | tail -n 1

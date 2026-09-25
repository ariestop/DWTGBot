#!/usr/bin/env bash
# =====================================================================
# Bootstrap Let's Encrypt certificates on the media-plane host
# (single or NL-2). Thin wrapper around deploy/certbot/init-letsencrypt.sh
# that picks up DOMAIN/EMAIL from deploy/{single,nl2}/.env (or prompts).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

STACK="$(find_stack_with media)" \
  || die "No .env for a media-plane stack (deploy/single or deploy/nl2)"
# shellcheck disable=SC1090
source "$(stack_env_file "${STACK}")"

DOMAIN="${DOMAIN:-${SERVER_NAME:-}}"
[[ -n "${DOMAIN}" ]] || prompt_value DOMAIN "Domain (e.g. media.example.com)"
[[ -n "${EMAIL:-}"  ]] || prompt_value EMAIL "Admin email for Let's Encrypt"

log_info "Bootstrapping cert for ${DOMAIN} (email ${EMAIL}) on stack ${STACK}"
cd "$(stack_dir "${STACK}")" || {
  log_error "Cannot cd into $(stack_dir "${STACK}")"
  exit 1
}
DOMAIN="${DOMAIN}" EMAIL="${EMAIL}" STAGING="${STAGING:-0}" ASSUME_YES="${ASSUME_YES:-0}" \
  bash "${DEPLOY_DIR}/certbot/init-letsencrypt.sh"

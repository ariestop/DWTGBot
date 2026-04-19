#!/usr/bin/env bash
# =====================================================================
# Bootstrap Let's Encrypt certificates on NL-2.
# Thin wrapper around deploy/certbot/init-letsencrypt.sh that picks up
# DOMAIN/EMAIL from deploy/nl2/.env (or prompts).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

[[ -f "${DEPLOY_DIR}/nl2/.env" ]] || die "NL-2 .env not found"
# shellcheck disable=SC1091
source "${DEPLOY_DIR}/nl2/.env"

DOMAIN="${DOMAIN:-${SERVER_NAME:-}}"
[[ -n "${DOMAIN}" ]] || prompt_value DOMAIN "Domain (e.g. media.example.com)"
[[ -n "${EMAIL:-}"  ]] || prompt_value EMAIL "Admin email for Let's Encrypt"

log_info "Bootstrapping cert for ${DOMAIN} (email ${EMAIL})"
cd "${DEPLOY_DIR}/nl2"
DOMAIN="${DOMAIN}" EMAIL="${EMAIL}" STAGING="${STAGING:-0}" \
  bash "${DEPLOY_DIR}/certbot/init-letsencrypt.sh"

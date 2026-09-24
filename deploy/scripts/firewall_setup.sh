#!/usr/bin/env bash
# =====================================================================
# Configure ufw firewall for single, NL-1 or NL-2.
#
# single (one host, ADR-0011):
#   - allow 22/tcp, 80/tcp, 443/tcp
#   - DENY 5432/tcp and 6379/tcp (defence in depth: the single stack
#     publishes no host ports for Postgres/Redis in the first place)
#
# NL-1 (control plane):
#   - allow 22/tcp from anywhere (consider restricting to admin IPs)
#   - DENY 5432/tcp and 6379/tcp from public
#   - allow 5432/tcp and 6379/tcp from PRIVATE_NET (e.g. WireGuard subnet)
#
# NL-2 (media plane):
#   - allow 22/tcp from anywhere
#   - allow 80/tcp and 443/tcp from anywhere
#
# Usage:
#   sudo                          bash deploy/scripts/firewall_setup.sh single
#   sudo PRIVATE_NET=10.10.0.0/24 bash deploy/scripts/firewall_setup.sh nl1
#   sudo                          bash deploy/scripts/firewall_setup.sh nl2
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${1:-}"
is_valid_stack "${TARGET}" || die "Usage: firewall_setup.sh single|nl1|nl2"

require_root
require_command ufw

log_step "Configuring ufw for ${TARGET}"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "ssh"

if [[ "${TARGET}" == "nl1" ]]; then
  PRIVATE_NET="${PRIVATE_NET:-}"
  [[ -n "${PRIVATE_NET}" ]] || die "PRIVATE_NET is required for nl1 (e.g. 10.10.0.0/24)"
  ufw deny  5432/tcp comment "postgres-public-deny"
  ufw deny  6379/tcp comment "redis-public-deny"
  ufw allow from "${PRIVATE_NET}" to any port 5432 proto tcp comment "postgres-private"
  ufw allow from "${PRIVATE_NET}" to any port 6379 proto tcp comment "redis-private"
fi

if [[ "${TARGET}" == "single" ]]; then
  ufw deny 5432/tcp comment "postgres-public-deny"
  ufw deny 6379/tcp comment "redis-public-deny"
fi

if stack_has_media_plane "${TARGET}"; then
  ufw allow 80/tcp  comment "http"
  ufw allow 443/tcp comment "https"
fi

if confirm "Enable ufw now? Existing connections will be preserved." "Y"; then
  ufw --force enable
  log_ok "ufw enabled"
else
  log_warn "ufw rules saved but not enabled. Run: ufw enable"
fi

ufw status verbose

#!/usr/bin/env bash
# =====================================================================
# Install the journald retention drop-in (deploy/systemd/journald-dwtgbot.conf)
# and restart systemd-journald. Idempotent; run on every host of a stack.
#
#   sudo bash deploy/scripts/journald_setup.sh
#
# Containers already log to journald (deploy/compose/*.yml); without
# this drop-in journald keeps its defaults (up to 10% of the disk,
# persistent only if /var/log/journal exists).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

SRC="${SCRIPT_DIR}/../systemd/journald-dwtgbot.conf"
DEST_DIR=/etc/systemd/journald.conf.d
DEST="${DEST_DIR}/dwtgbot.conf"

require_root
require_command journalctl
require_command systemctl
[[ -f "${SRC}" ]] || die "Missing ${SRC}"

if [[ -f "${DEST}" ]] && cmp -s "${SRC}" "${DEST}"; then
  log_ok "${DEST} is up to date"
else
  log_step "Installing ${DEST}"
  install -d -m 0755 "${DEST_DIR}"
  install -m 0644 "${SRC}" "${DEST}"
  # Storage=persistent writes here; create it so the restart switches
  # from /run at once instead of on the next flush.
  install -d -m 2755 -g systemd-journal /var/log/journal 2>/dev/null \
    || install -d -m 2755 /var/log/journal
  systemctl restart systemd-journald
  log_ok "systemd-journald restarted"
fi

# Apply the new caps to what is already stored.
journalctl --vacuum-time=30d --vacuum-size=2G >/dev/null
journalctl --disk-usage

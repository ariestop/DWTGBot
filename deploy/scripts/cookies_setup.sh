#!/usr/bin/env bash
# =====================================================================
# Install provider cookies (Netscape cookies.txt) for yt-dlp.
#
#   sudo bash deploy/scripts/cookies_setup.sh [instagram|youtube]
#
# Sources (asked interactively):
#   1) path to a file exported from the browser (e.g. uploaded via scp)
#   2) paste the file contents into the terminal
#   3) instagram only: enter sessionid / ds_user_id / csrftoken by hand
# Non-interactive: COOKIES_SRC=/path/to/cookies.txt.
#
# Writes /srv/dwtgbot/secrets/cookies-<provider>.txt (root:1000, 0660 —
# bot/worker run as uid 1000 and yt-dlp writes refreshed cookies back),
# sets <PROVIDER>_COOKIES_FILE in every deploy/*/.env on this host and
# recreates the running bot/worker so they pick it up.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

SECRETS_DIR="${SECRETS_DIR:-/srv/dwtgbot/secrets}"
APP_GID=1000

require_root

PROVIDER="${1:-}"
[[ -n "${PROVIDER}" ]] || prompt_value PROVIDER "Provider (instagram/youtube)" "instagram"
case "${PROVIDER}" in
  instagram) DOMAIN="instagram.com"; ENV_KEY="INSTAGRAM_COOKIES_FILE"; REQUIRED_COOKIE="sessionid" ;;
  youtube)   DOMAIN="youtube.com";   ENV_KEY="YOUTUBE_COOKIES_FILE";   REQUIRED_COOKIE="" ;;
  *) die "Unknown provider '${PROVIDER}' (expected instagram|youtube)" ;;
esac
TARGET="${SECRETS_DIR}/cookies-${PROVIDER}.txt"

TMP="$(mktemp)"
chmod 0600 "${TMP}"
trap 'rm -f "${TMP}"' EXIT

# Data lines are tab-separated; "#HttpOnly_<domain>" lines are data too.
_is_data_line() { [[ -n "$1" && ( "$1" != \#* || "$1" == \#HttpOnly_* ) ]]; }

# Terminals sometimes turn pasted tabs into spaces; cookie fields never
# contain whitespace, so collapsing runs of spaces back to tabs is safe.
_normalize() {
  local line
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    if _is_data_line "${line}" && [[ "${line}" != *$'\t'* ]]; then
      line="$(printf '%s' "${line}" | tr -s ' ' '\t')"
    fi
    printf '%s\n' "${line}"
  done
}

_from_file() {
  local src="$1"
  [[ -f "${src}" ]] || die "File not found: ${src}"
  _normalize <"${src}" >"${TMP}"
}

_from_paste() {
  log_info "Paste the cookies.txt contents, then type END on its own line and press Enter."
  local line
  {
    while IFS= read -r line; do
      [[ "${line%$'\r'}" == "END" ]] && break
      printf '%s\n' "${line}"
    done
  } | _normalize >"${TMP}"
}

_read_secret() {
  # _read_secret VAR "Prompt" — input is not echoed.
  local __var="$1" __ans
  read -r -s -p "$2: " __ans || true
  printf '\n' >&2
  printf -v "${__var}" '%s' "${__ans}"
}

# _instagram_line EXPIRES NAME VALUE — appends one cookie if VALUE is set.
_instagram_line() {
  if [[ -n "$3" ]]; then
    printf '.instagram.com\tTRUE\t/\tTRUE\t%s\t%s\t%s\n' "$1" "$2" "$3" >>"${TMP}"
  fi
}

_from_manual_instagram() {
  log_info "Browser → instagram.com (logged in) → DevTools (F12) → Application/Storage → Cookies → https://www.instagram.com"
  local sessionid ds_user_id csrftoken expires
  _read_secret sessionid "sessionid"
  prompt_value ds_user_id "ds_user_id"
  _read_secret csrftoken "csrftoken"
  [[ -n "${sessionid}" ]] || die "sessionid is required"
  expires=$(( $(date +%s) + 365 * 24 * 3600 ))
  printf '# Netscape HTTP Cookie File\n' >"${TMP}"
  _instagram_line "${expires}" sessionid "${sessionid}"
  _instagram_line "${expires}" ds_user_id "${ds_user_id}"
  _instagram_line "${expires}" csrftoken "${csrftoken}"
}

_validate() {
  local rows names
  rows="$(awk -F'\t' -v d="${DOMAIN}" '
    ($0 !~ /^#/ || $0 ~ /^#HttpOnly_/) && NF == 7 && index($1, d) { n++ }
    END { print n + 0 }' "${TMP}")"
  (( rows > 0 )) || die "No ${DOMAIN} cookies in Netscape format (7 tab-separated fields per line)"
  if [[ -n "${REQUIRED_COOKIE}" ]]; then
    names="$(awk -F'\t' -v d="${DOMAIN}" 'NF == 7 && index($1, d) { print $6 }' "${TMP}")"
    grep -qx "${REQUIRED_COOKIE}" <<<"${names}" \
      || die "Cookie '${REQUIRED_COOKIE}' not found — export while logged in to ${DOMAIN}"
  fi
  log_ok "Found ${rows} ${DOMAIN} cookies"
}

_update_env_files() {
  local s env
  for s in "${STACKS[@]}"; do
    env="$(stack_env_file "${s}")"
    [[ -f "${env}" ]] || continue
    set_env_value "${env}" "${ENV_KEY}" "${TARGET}"
    log_ok "${ENV_KEY}=${TARGET} in ${env}"
  done
}

# Recreate (not restart: restart keeps the old environment) whichever of
# bot/worker is running in each stack present here.
_recreate_consumers() {
  local s running svc to_up
  for s in "${STACKS[@]}"; do
    [[ -f "$(stack_env_file "${s}")" ]] || continue
    running="$(compose_stack "${s}" ps --status running --services 2>/dev/null || true)"
    to_up=()
    for svc in bot worker; do
      if grep -qx "${svc}" <<<"${running}"; then
        to_up+=("${svc}")
      fi
    done
    if (( ${#to_up[@]} > 0 )); then
      log_step "Recreating ${to_up[*]} (${s})"
      compose_stack "${s}" up -d --no-deps "${to_up[@]}"
    fi
  done
}

log_step "Cookies for ${PROVIDER} → ${TARGET}"
if [[ -f "${TARGET}" ]]; then
  log_warn "Existing ${TARGET} will be replaced"
fi

if [[ -n "${COOKIES_SRC:-}" ]]; then
  _from_file "${COOKIES_SRC}"
elif [[ "${ASSUME_YES:-0}" == "1" ]]; then
  log_warn "ASSUME_YES=1 without COOKIES_SRC — skipping ${PROVIDER} cookies"
  exit 0
else
  echo "  1) Path to an exported cookies.txt"
  echo "  2) Paste the file contents here"
  if [[ "${PROVIDER}" == "instagram" ]]; then
    echo "  3) Enter sessionid / ds_user_id / csrftoken manually"
  fi
  prompt_value MODE "Source" "1"
  case "${MODE}" in
    1) prompt_value SRC "Path" "/tmp/cookies-${PROVIDER}.txt"; _from_file "${SRC}" ;;
    2) _from_paste ;;
    3) [[ "${PROVIDER}" == "instagram" ]] || die "Manual entry is available for instagram only"
       _from_manual_instagram ;;
    *) die "Unknown choice: ${MODE}" ;;
  esac
fi

_validate

mkdir -p "${SECRETS_DIR}"
chmod 0750 "${SECRETS_DIR}"
chgrp "${APP_GID}" "${SECRETS_DIR}"
install -o root -g "${APP_GID}" -m 0660 "${TMP}" "${TARGET}"
log_ok "Wrote ${TARGET}"

_update_env_files
_recreate_consumers

if [[ -n "${COOKIES_SRC:-}" || "${MODE:-}" == "1" ]]; then
  log_warn "Delete the source file once done: rm -f ${COOKIES_SRC:-${SRC}}"
fi
log_ok "Done. Send the bot a ${PROVIDER} link to verify."

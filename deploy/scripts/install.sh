#!/usr/bin/env bash
# =====================================================================
# DWTGBot TUI installer / operator menu.
#
# Run as a regular user (it will sudo when needed). Idempotent and safe
# to re-run.
#
#   bash deploy/scripts/install.sh              # default: plain numbered menu
#   bash deploy/scripts/install.sh --whiptail   # whiptail GUI (if installed)
#   INSTALL_TUI_MODE=whiptail bash deploy/scripts/install.sh
#
# Why plain by default (operator feedback 2026-04-26): whiptail's
# dialog box hides the scrolling shell context (last command output,
# log lines, healthcheck results) the moment it draws, which is
# exactly the wrong UX during a deploy where the operator is reading
# logs between actions. Plain mode keeps everything in the same
# scrollable buffer; whiptail remains opt-in for SSH sessions on
# capable terminals where the dialog UX is preferred.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

# Force a UTF-8 locale so bash's ``${#var}`` counts visible glyphs, not
# bytes. The styled-plain panel pads cells with ``${#var}`` after
# stripping ANSI codes; without UTF-8 locale, multi-byte chars (``●``,
# ``─``, em-dash) push the right border off by 2 columns per glyph.
# ``C.UTF-8`` ships on Ubuntu 24.04 and macOS; we keep an existing
# operator override if they've explicitly set ``LC_ALL``.
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"

# Mode resolution order: CLI flag > env var > auto. The default
# ``auto`` always picks ``plain`` so the menu degrades to text on any
# host regardless of whether ``whiptail`` happens to be installed.
INSTALL_TUI_MODE="${INSTALL_TUI_MODE:-auto}"
for arg in "$@"; do
  case "${arg}" in
    --whiptail) INSTALL_TUI_MODE="whiptail" ;;
    --plain)    INSTALL_TUI_MODE="plain" ;;
    -h|--help)
      cat <<USAGE
Usage: install.sh [--plain | --whiptail]

  --plain       (default) numbered text menu, keeps shell scrollback
  --whiptail    full-screen whiptail dialog (requires whiptail binary)

Env: INSTALL_TUI_MODE=plain|whiptail (overridden by flags above).
USAGE
      exit 0
      ;;
  esac
done

USE_WHIPTAIL=0
case "${INSTALL_TUI_MODE}" in
  whiptail)
    if has_command whiptail; then
      USE_WHIPTAIL=1
    else
      log_warn "whiptail requested but not installed; falling back to plain menu"
    fi
    ;;
  plain|auto)
    USE_WHIPTAIL=0
    ;;
  *)
    log_warn "Unknown INSTALL_TUI_MODE='${INSTALL_TUI_MODE}'; using plain"
    ;;
esac

# ---------- option implementations ----------

opt_install_docker() {
  log_step "Install Docker (Ubuntu 24.04 / noble, official apt repository)"
  detect_os
  require_ubuntu_2404
  if has_command docker && docker compose version >/dev/null 2>&1; then
    log_ok "Docker + compose plugin already installed ($(compose_version || echo '?'))"
    require_compose_version
    return
  fi
  confirm "Install Docker Engine from docker.com apt repo for noble?" "Y" || return

  sudo install -m 0755 -d /etc/apt/keyrings
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg lsb-release
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu noble stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

  sudo apt-get update -y
  sudo apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  sudo systemctl enable --now docker
  sudo usermod -aG docker "${USER}" || true
  log_ok "Docker installed. Re-login (or 'newgrp docker') for group membership to take effect."
  detect_docker
  require_compose_version
}

require_ubuntu_2404() {
  if [[ "${OS_ID}" != "ubuntu" || "${OS_VER}" != "24.04" ]]; then
    log_warn "This TUI installer targets Ubuntu 24.04 LTS. Detected: ${OS_ID} ${OS_VER}"
    confirm "Continue anyway? (unsupported)" "N" || die "Aborted on unsupported OS"
  fi
}

opt_prepare_nl1() {
  log_step "Prepare NL-1"
  sudo mkdir -p /var/backups/dwtgbot
  sudo chown -R "${USER}:${USER}" /var/backups/dwtgbot 2>/dev/null || true
  if [[ ! -f "${DEPLOY_DIR}/nl1/.env" ]]; then
    log_info "Creating deploy/nl1/.env from example"
    cp "${DEPLOY_DIR}/nl1/.env.example" "${DEPLOY_DIR}/nl1/.env"
    log_warn "Edit ${DEPLOY_DIR}/nl1/.env with real secrets before starting."
  else
    log_ok ".env already present"
  fi
}

opt_prepare_nl2() {
  log_step "Prepare NL-2"
  sudo mkdir -p /var/lib/dwtgbot/storage /var/lib/dwtgbot/tmp
  sudo chown -R 1000:1000 /var/lib/dwtgbot
  if [[ ! -f "${DEPLOY_DIR}/nl2/.env" ]]; then
    log_info "Creating deploy/nl2/.env from example"
    cp "${DEPLOY_DIR}/nl2/.env.example" "${DEPLOY_DIR}/nl2/.env"
    log_warn "Edit ${DEPLOY_DIR}/nl2/.env (BOT_TOKEN, POSTGRES_HOST, SERVER_NAME) before starting."
  else
    log_ok ".env already present"
  fi
}

# Single host (ADR-0011): backups + storage dirs on the same box, one .env.
opt_prepare_single() {
  log_step "Prepare single server (all services on this host)"
  sudo mkdir -p /var/backups/dwtgbot
  sudo chown -R "${USER}:${USER}" /var/backups/dwtgbot 2>/dev/null || true
  sudo mkdir -p /var/lib/dwtgbot/storage /var/lib/dwtgbot/tmp
  sudo chown -R 1000:1000 /var/lib/dwtgbot
  if [[ -f "$(stack_env_file single)" ]]; then
    log_ok ".env already present"
    return
  fi
  if confirm "Generate deploy/single/.env with random DB/Redis/API secrets?" "Y"; then
    _create_single_env
  else
    log_info "Creating deploy/single/.env from example"
    cp "${DEPLOY_DIR}/single/.env.example" "$(stack_env_file single)"
    chmod 0600 "$(stack_env_file single)"
    log_warn "Edit $(stack_env_file single) with real secrets before starting."
  fi
}

# _create_single_env — deploy/single/.env.example with generated secrets
# filled in. Only called when the file does not exist yet (idempotent).
_create_single_env() {
  local out pgpass rdpass apitoken domain
  out="$(stack_env_file single)"
  pgpass="$(gen_secret 32)"
  rdpass="$(gen_secret 32)"
  apitoken="$(gen_secret 32)"
  prompt_value domain "Public domain (DNS A record → this host)" "media.example.com"
  sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pgpass}|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://dwtgbot:${pgpass}@postgres:5432/dwtgbot|" \
      -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${rdpass}|" \
      -e "s|^REDIS_URL=.*|REDIS_URL=redis://:${rdpass}@redis:6379/0|" \
      -e "s|^API_INTERNAL_TOKEN=.*|API_INTERNAL_TOKEN=${apitoken}|" \
      -e "s|^SERVER_NAME=.*|SERVER_NAME=${domain}|" \
      -e "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${domain}|" \
      "${DEPLOY_DIR}/single/.env.example" >"${out}"
  chmod 0600 "${out}"
  log_ok "Wrote ${out}"
  log_warn "Set BOT_TOKEN, BOT_ADMIN_IDS, IMAGE_* and an off-site backup target (BACKUP_S3_BUCKET or BACKUP_RCLONE_REMOTE) before starting."
}

opt_create_env() {
  log_step "Create .env from template"
  local target out
  prompt_value target "Target stack (single/nl1/nl2)" "$(_default_stack)"
  require_stack "${target}"
  out="$(stack_env_file "${target}")"
  [[ -f "${out}" ]] && { log_warn "Already exists: ${out}"; return; }
  if [[ "${target}" == "single" ]]; then
    _create_single_env
    return
  fi
  local pgpass apitoken domain
  pgpass="$(gen_secret 32)"
  apitoken="$(gen_secret 32)"
  prompt_value domain "Public domain" "media.example.com"
  sed -e "s|__PG_PASS__|${pgpass}|g" \
      -e "s|__API_TOKEN__|${apitoken}|g" \
      -e "s|__DOMAIN__|${domain}|g" \
      "${DEPLOY_DIR}/templates/env.template" >"${out}"
  log_ok "Wrote ${out}"
  log_warn "Set BOT_TOKEN and review the rest before starting."
}

opt_firewall() {
  log_step "Firewall setup"
  local target
  prompt_value target "Stack (single/nl1/nl2)" "$(_default_stack)"
  require_stack "${target}"
  if [[ "${target}" == "nl1" ]]; then
    prompt_value PRIVATE_NET "Private network CIDR (WireGuard subnet)" "10.10.0.0/24"
    sudo PRIVATE_NET="${PRIVATE_NET}" bash "${SCRIPT_DIR}/firewall_setup.sh" nl1
  else
    sudo bash "${SCRIPT_DIR}/firewall_setup.sh" "${target}"
  fi
}

opt_up()      { log_step "Starting"; auto_compose up -d; }
opt_down()    { log_step "Stopping"; auto_compose down; }
opt_restart() { log_step "Restarting"; auto_compose restart; }
opt_status()  { log_step "Status";  auto_compose ps; }
opt_logs()    { local svc=""; prompt_value svc "Service (empty = all)" ""; auto_compose logs -f --tail=200 ${svc}; }
opt_health()  { bash "${SCRIPT_DIR}/healthcheck.sh"; }
opt_certbot() { bash "${SCRIPT_DIR}/certbot_init.sh"; }
opt_backup()  { bash "${SCRIPT_DIR}/backup.sh"; }
opt_restore() { bash "${SCRIPT_DIR}/restore.sh"; }
opt_cleanup() { bash "${SCRIPT_DIR}/cleanup.sh"; }

opt_deploy_update() {
  local target
  prompt_value target "Stack (single/nl1/nl2)" "$(_default_stack)"
  bash "${SCRIPT_DIR}/deploy_update.sh" "${target}"
}

opt_install_autodeploy() {
  local target
  prompt_value target "Стек для автодеплоя (single/nl1/nl2)" "$(_default_stack)"
  sudo bash "${SCRIPT_DIR}/install_autodeploy.sh" "${target}"
}

# opt_autodeploy_now — best-effort "update this host to latest main".
#
# Why this exists separate from ``opt_deploy_update``:
#   - ``opt_deploy_update`` (== plain ``deploy_update.sh``) is a rolling
#     restart that uses the IMAGE_TAG (or IMAGE_API/IMAGE_WORKER)
#     **already in the host's .env**. If the .env is stale, it'll happily
#     redeploy old code without complaining. Operators repeatedly hit this
#     ("I clicked Deploy update but the bot still has the old bug"): the
#     menu doesn't fetch the latest sha; it just rolls.
#   - ``opt_autodeploy_now`` triggers ``dwtgbot-autodeploy.service``,
#     which queries GitHub for the head of ``main``, waits for green
#     CI + ``build-images``, exports the right ``IMAGE_API`` /
#     ``IMAGE_WORKER`` for that sha, checks out the matching tree, and
#     finally calls ``deploy_update.sh``. That's the path that actually
#     advances the host to ``origin/main``.
#
# Falls back to a clear error if the systemd unit isn't installed yet —
# the operator should install it via ``[17] Install autodeploy service``
# first. We don't auto-install because that requires root and a token.
opt_autodeploy_now() {
  log_step "Trigger autodeploy now (advance host to origin/main)"
  if ! has_command systemctl; then
    die "systemctl not available — autodeploy is systemd-based"
  fi
  if ! systemctl list-unit-files dwtgbot-autodeploy.service >/dev/null 2>&1 \
        || ! systemctl cat dwtgbot-autodeploy.service >/dev/null 2>&1; then
    log_error "dwtgbot-autodeploy.service is not installed on this host"
    log_info  "Install it first: menu option [17] Install autodeploy service"
    return 1
  fi

  log_info "Starting dwtgbot-autodeploy.service synchronously..."
  # ``systemctl start --wait`` blocks until the unit finishes, so the
  # operator sees the deploy finish (or fail) before the menu redraws.
  if sudo systemctl start --wait dwtgbot-autodeploy.service; then
    log_ok "Autodeploy unit completed"
  else
    log_error "Autodeploy unit failed (see journalctl)"
  fi
  log_info "Recent log:"
  sudo journalctl -u dwtgbot-autodeploy.service -n 80 --no-pager || true
}

# _default_stack — какой стек предложить по умолчанию для опций,
# которые требуют выбора стека. На реальных хостах присутствует только
# один ``.env`` (single, NL-1 или NL-2) — его и подставляем. На свежем
# хосте без ``.env`` предлагаем ``single`` (ADR-0011: топология по
# умолчанию для малого масштаба). Если есть nl1+nl2 (laptop dev) —
# nl1, как и раньше; при прочих комбинациях — single.
_default_stack() {
  case "$(_detect_stack)" in
    single) echo single ;;
    nl1)    echo nl1 ;;
    nl2)    echo nl2 ;;
    both)   echo nl1 ;;
    *)      echo single ;;
  esac
}

# Auto-pick whichever stack has an .env on this host. If several exist, ask.
auto_compose() {
  local stack
  stack="$(_detect_stack)"
  case "${stack}" in
    single|nl1|nl2)
      compose_stack "${stack}" "$@"
      ;;
    both|multiple)
      local pick
      prompt_value pick "Stack (single/nl1/nl2)" "$(_default_stack)"
      require_stack "${pick}"
      compose_stack "${pick}" "$@"
      ;;
    *)
      die "No .env found in deploy/single, deploy/nl1 or deploy/nl2"
      ;;
  esac
}

# ---------- menu ----------

# Keys are stable identifiers referenced from docs; [19] is listed second
# on purpose — single is the default topology for a fresh host (ADR-0011).
MENU_ITEMS=(
  "1"  "Install Docker"
  "19" "Prepare single server (all-in-one)"
  "2"  "Prepare server NL-1 (split)"
  "3"  "Prepare server NL-2 (split)"
  "4"  "Create .env from template"
  "5"  "Configure firewall"
  "6"  "Start containers"
  "7"  "Stop containers"
  "8"  "Restart containers"
  "9"  "Show status"
  "10" "Tail logs"
  "11" "Run healthchecks"
  "12" "Obtain SSL cert (single / NL-2)"
  "13" "Backup database (single / NL-1)"
  "14" "Restore database (single / NL-1)"
  "15" "Cleanup old files (single / NL-2)"
  "16" "Update to latest main (autodeploy now)"
  "17" "Rolling restart with current .env"
  "18" "Install autodeploy service"
  "0"  "Exit"
)

run_action() {
  case "$1" in
    1)  opt_install_docker ;;
    2)  opt_prepare_nl1 ;;
    3)  opt_prepare_nl2 ;;
    4)  opt_create_env ;;
    5)  opt_firewall ;;
    6)  opt_up ;;
    7)  opt_down ;;
    8)  opt_restart ;;
    9)  opt_status ;;
    10) opt_logs ;;
    11) opt_health ;;
    12) opt_certbot ;;
    13) opt_backup ;;
    14) opt_restore ;;
    15) opt_cleanup ;;
    16) opt_autodeploy_now ;;
    17) opt_deploy_update ;;
    18) opt_install_autodeploy ;;
    19) opt_prepare_single ;;
    0)  exit 0 ;;
    *)  log_warn "Unknown choice: $1" ;;
  esac
}

draw_whiptail() {
  local args=()
  local i=0
  while [[ $i -lt ${#MENU_ITEMS[@]} ]]; do
    args+=("${MENU_ITEMS[i]}" "${MENU_ITEMS[i+1]}")
    i=$((i + 2))
  done
  whiptail --title "DWTGBot TUI installer" \
           --menu "Pick an action:" 22 70 15 \
           "${args[@]}" 3>&1 1>&2 2>&3
}

# ---------- styled-plain rendering (banner + status + menu) ----------
#
# Layout (64-char inner panel width, fixed) so it fits an 80-col terminal
# with a small left margin and looks good over SSH from a phone too:
#
#     ┌──────── banner (block ASCII) ────────┐
#     │ DWTGBot — Telegram media bot menu    │
#     ├──────────────────────────────────────┤
#     │ Stack:    nl2     Status: ● RUNNING  │
#     │ Compose:  5/5     Branch: main@16efd │
#     │ Domain:   s1.dwt  Docker: 28.5.2     │
#     ├──────────────────────────────────────┤
#     │ [1] Install Docker                   │
#     │ ...                                  │
#     └──────────────────────────────────────┘
#
# No whiptail. Operator scrollback is preserved between actions, so
# previous ``ps``/``logs`` output stays visible above the menu.

PANEL_W=64  # inner content width (between │ and │)

_repeat() {
  # _repeat <char> <count>  -> prints <char> * <count>.
  # We avoid ``tr`` here because it operates on bytes and would mangle
  # multi-byte glyphs like ``─`` (3 bytes in UTF-8) into garbage.
  local ch="$1" n="$2" out=""
  while (( n > 0 )); do
    out+="${ch}"
    (( n-- ))
  done
  printf '%s' "${out}"
}

_pad() {
  # _pad "<text>" <width>  — right-pad to <width> *visible* chars,
  # ignoring ANSI escape sequences. Falls back to printf %-Ns when
  # the input has no escapes.
  local s="$1" w="$2"
  local stripped
  stripped="$(printf '%s' "${s}" | sed -E $'s/\x1b\\[[0-9;]*m//g')"
  local visible="${#stripped}"
  if (( visible >= w )); then
    printf '%s' "${s}"
  else
    printf '%s%*s' "${s}" $((w - visible)) ''
  fi
}

draw_banner() {
  local c="${C_BLUE}" r="${C_RESET}"
  printf '\n'
  printf '%s██████╗  ██╗    ██╗████████╗ ██████╗ ██████╗  ██████╗ ████████╗%s\n' "${c}" "${r}"
  printf '%s██╔══██╗ ██║    ██║╚══██╔══╝██╔════╝ ██╔══██╗██╔═══██╗╚══██╔══╝%s\n' "${c}" "${r}"
  printf '%s██║  ██║ ██║ █╗ ██║   ██║   ██║  ███╗██████╔╝██║   ██║   ██║   %s\n' "${c}" "${r}"
  printf '%s██║  ██║ ██║███╗██║   ██║   ██║   ██║██╔══██╗██║   ██║   ██║   %s\n' "${c}" "${r}"
  printf '%s██████╔╝ ╚███╔███╔╝   ██║   ╚██████╔╝██████╔╝╚██████╔╝   ██║   %s\n' "${c}" "${r}"
  printf '%s╚═════╝   ╚══╝╚══╝    ╚═╝    ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝   %s\n' "${c}" "${r}"
}

_panel_top()  { printf '%s┌%s┐%s\n' "${C_BLUE}" "$(_repeat '─' "${PANEL_W}")" "${C_RESET}"; }
_panel_sep()  { printf '%s├%s┤%s\n' "${C_BLUE}" "$(_repeat '─' "${PANEL_W}")" "${C_RESET}"; }
_panel_bot()  { printf '%s└%s┘%s\n' "${C_BLUE}" "$(_repeat '─' "${PANEL_W}")" "${C_RESET}"; }

_panel_line() {
  # _panel_line "<inner content with optional ANSI>"
  # Pads to PANEL_W minus the surrounding " " margins (2).
  local inner_w=$((PANEL_W - 2))
  local content
  content="$(_pad "$1" "${inner_w}")"
  printf '%s│%s %s %s│%s\n' "${C_BLUE}" "${C_RESET}" "${content}" "${C_BLUE}" "${C_RESET}"
}

_panel_kv2() {
  # _panel_kv2 "K1:" "V1" "K2:" "V2"
  # Two left-aligned columns. Each column is 31 visible chars wide
  # (inner_w=62 / 2). Keys/values may contain ANSI; padding is
  # visible-aware via _pad.
  local col_w=$(((PANEL_W - 2) / 2))
  local left right
  left="$(_pad "$(printf '%-9s ' "$1")$2" "${col_w}")"
  right="$(_pad "$(printf '%-9s ' "$3")$4" "${col_w}")"
  _panel_line "${left}${right}"
}

# Compute which compose stack is present here. A host typically runs
# exactly one of single / nl1 / nl2; a developer laptop may have several
# .env files ("both" = nl1+nl2, "multiple" = any other combination).
_detect_stack() { detect_stack; }

# Returns "<up>/<total>" or "n/a" without docker/compose.
_compose_count() {
  local stack="$1"
  if [[ -z "${DOCKER_BIN}" || ${#DOCKER_COMPOSE[@]} -eq 0 ]]; then
    echo "n/a"; return
  fi
  case "${stack}" in
    single) _compose_count_for compose_single ;;
    nl1)    _compose_count_for compose_nl1 ;;
    nl2)    _compose_count_for compose_nl2 ;;
    both)   echo "$(_compose_count nl1) / $(_compose_count nl2)" ;;
    *)      echo "no .env" ;;
  esac
}
_compose_count_for() {
  local fn="$1" total up
  total="$("${fn}" ps --services 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
  up="$("${fn}" ps --services --filter status=running 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
  printf '%s/%s' "${up}" "${total}"
}

_read_env_value() {
  # _read_env_value KEY <stack>  -> value or empty
  local key="$1" stack="$2"
  local file="${DEPLOY_DIR}/${stack}/.env"
  [[ -f "${file}" ]] || { echo ""; return; }
  awk -F= -v k="${key}" '$1==k { sub(/^[^=]*=/, ""); print; exit }' "${file}" \
    | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

_short_sha() { git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || echo "?"; }
_branch()    { git -C "${PROJECT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?"; }
_docker_ver() {
  [[ -z "${DOCKER_BIN}" ]] && { echo "not installed"; return; }
  docker version --format '{{.Server.Version}}' 2>/dev/null \
    || docker --version 2>/dev/null | awk '{print $3}' | tr -d , \
    || echo "?"
}

draw_status_panel() {
  local stack count branch sha domain dver
  stack="$(_detect_stack)"
  count="$(_compose_count "${stack}")"
  branch="$(_branch)"; sha="$(_short_sha)"
  dver="$(_docker_ver)"
  local stack_for_env="${stack}"
  [[ "${stack}" == "both" ]] && stack_for_env="nl2"
  if [[ "${stack}" == "multiple" ]]; then
    stack_for_env="$(find_stack_with media || echo single)"
  fi
  domain="$(_read_env_value SERVER_NAME "${stack_for_env}")"
  [[ -z "${domain}" ]] && domain="—"

  local status_pill
  if [[ "${count}" == "n/a" || "${count}" == "no .env" ]]; then
    status_pill="${C_YELLOW}● ${count}${C_RESET}"
  else
    local up="${count%%/*}" total="${count##*/}"
    if [[ "${up}" == "${total}" && "${up}" != "0" ]]; then
      status_pill="${C_GREEN}● RUNNING${C_RESET}"
    elif [[ "${up}" == "0" ]]; then
      status_pill="${C_RED}● STOPPED${C_RESET}"
    else
      status_pill="${C_YELLOW}● DEGRADED${C_RESET}"
    fi
  fi

  _panel_top
  _panel_line "${C_BOLD}DWTGBot${C_RESET} — Telegram media bot operator menu"
  _panel_sep
  _panel_kv2 "Stack:"   "${stack}"            "Status:" "${status_pill}"
  _panel_kv2 "Compose:" "${count}"            "Branch:" "${branch}@${sha}"
  _panel_kv2 "Domain:"  "${domain}"           "Docker:" "${dver}"
  _panel_kv2 "OS:"      "${OS_ID} ${OS_VER}"  "Mode:"   "plain"
  _panel_sep
  local i=0
  while [[ $i -lt ${#MENU_ITEMS[@]} ]]; do
    local key="${MENU_ITEMS[i]}" label="${MENU_ITEMS[i+1]}"
    # Pad single-digit keys so ``[1]`` and ``[17]`` align labels at the
    # same column (4 visible chars: ``[N] `` or ``[NN]``).
    local key_cell
    printf -v key_cell '[%2s]' "${key}"
    _panel_line "${C_BOLD}${key_cell}${C_RESET} ${label}"
    i=$((i + 2))
  done
  _panel_bot
  printf '%sdwtgbot installer%s | %s--whiptail switches to dialog UI%s\n' \
    "${C_DIM}" "${C_RESET}" "${C_DIM}" "${C_RESET}"
  printf '%sgithub.com/ariestop/DWTGBot%s\n\n' "${C_DIM}" "${C_RESET}"
}

draw_plain() {
  # main() captures our stdout via ``$()`` to read the operator's
  # choice. The banner + status panel are UI ("chrome") and must hit
  # the terminal directly, so we route them to stderr. ``read -p``
  # already prints its prompt to stderr by convention. Only the final
  # ``echo "${choice}"`` lands on stdout and becomes the function's
  # return value.
  draw_banner       >&2
  draw_status_panel >&2
  local choice
  read -r -p "Enter choice [0]: " choice
  echo "${choice}"
}

main() {
  # The styled-plain panel surfaces OS/Docker/Stack/Compose state, so we
  # don't duplicate them here. Only show a single warning if the host
  # OS is unsupported — that's actionable.
  if [[ "${OS_ID}" != "ubuntu" || "${OS_VER}" != "24.04" ]]; then
    log_warn "Unsupported OS detected (${OS_ID} ${OS_VER}); target is Ubuntu 24.04 LTS"
  fi

  while true; do
    if [[ "${USE_WHIPTAIL}" == "1" ]]; then
      choice="$(draw_whiptail || true)"
    else
      choice="$(draw_plain)"
    fi
    [[ -z "${choice}" ]] && exit 0
    run_action "${choice}" || log_error "Action failed: ${choice}"
    if [[ "${USE_WHIPTAIL}" != "1" ]]; then
      read -r -p "Press Enter to continue..." _
    fi
  done
}

main "$@"

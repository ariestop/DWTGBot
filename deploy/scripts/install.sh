#!/usr/bin/env bash
# =====================================================================
# DWTGBot interactive installer / operator menu.
#
# Run as a regular user (it will sudo when needed). Idempotent and safe
# to re-run. Uses whiptail when available; falls back to numbered menu.
#
#   bash deploy/scripts/install.sh
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

USE_WHIPTAIL=0
if has_command whiptail; then USE_WHIPTAIL=1; fi

# ---------- option implementations ----------

opt_install_docker() {
  log_step "Install Docker (Ubuntu 24.04 / noble, official apt repository)"
  detect_os
  require_ubuntu_2404
  if has_command docker && docker compose version >/dev/null 2>&1; then
    log_ok "Docker + compose plugin already installed"
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
}

require_ubuntu_2404() {
  if [[ "${OS_ID}" != "ubuntu" || "${OS_VER}" != "24.04" ]]; then
    log_warn "This installer targets Ubuntu 24.04 LTS. Detected: ${OS_ID} ${OS_VER}"
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

opt_create_env() {
  log_step "Create .env from generic template"
  local target out
  prompt_value target "Target stack" "nl1"
  out="${DEPLOY_DIR}/${target}/.env"
  [[ -f "${out}" ]] && { log_warn "Already exists: ${out}"; return; }
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
  prompt_value target "Stack" "nl1"
  if [[ "${target}" == "nl1" ]]; then
    prompt_value PRIVATE_NET "Private network CIDR (WireGuard subnet)" "10.10.0.0/24"
    sudo PRIVATE_NET="${PRIVATE_NET}" bash "${SCRIPT_DIR}/firewall_setup.sh" nl1
  else
    sudo bash "${SCRIPT_DIR}/firewall_setup.sh" nl2
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
  prompt_value target "Stack" "nl1"
  bash "${SCRIPT_DIR}/deploy_update.sh" "${target}"
}

# Auto-pick whichever stack has an .env on this host. If both exist, ask.
auto_compose() {
  local has1=0 has2=0
  [[ -f "${DEPLOY_DIR}/nl1/.env" ]] && has1=1
  [[ -f "${DEPLOY_DIR}/nl2/.env" ]] && has2=1
  if [[ "${has1}" == "1" && "${has2}" == "0" ]]; then compose_nl1 "$@"
  elif [[ "${has1}" == "0" && "${has2}" == "1" ]]; then compose_nl2 "$@"
  elif [[ "${has1}" == "1" && "${has2}" == "1" ]]; then
    local pick; prompt_value pick "Stack (nl1/nl2)" "nl1"
    [[ "${pick}" == "nl2" ]] && compose_nl2 "$@" || compose_nl1 "$@"
  else
    die "No .env found in deploy/nl1 or deploy/nl2"
  fi
}

# ---------- menu ----------

MENU_ITEMS=(
  "1"  "Install Docker"
  "2"  "Prepare server NL-1"
  "3"  "Prepare server NL-2"
  "4"  "Create .env from template"
  "5"  "Configure firewall"
  "6"  "Start containers"
  "7"  "Stop containers"
  "8"  "Restart containers"
  "9"  "Show status"
  "10" "Tail logs"
  "11" "Run healthchecks"
  "12" "Obtain SSL cert (NL-2)"
  "13" "Backup database (NL-1)"
  "14" "Restore database (NL-1)"
  "15" "Cleanup old files (NL-2)"
  "16" "Deploy update"
  "17" "Exit"
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
    16) opt_deploy_update ;;
    17) exit 0 ;;
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
  whiptail --title "DWTGBot installer" \
           --menu "Pick an action:" 22 70 15 \
           "${args[@]}" 3>&1 1>&2 2>&3
}

draw_plain() {
  printf '\n%sDWTGBot installer%s\n' "${C_BOLD}" "${C_RESET}"
  local i=0
  while [[ $i -lt ${#MENU_ITEMS[@]} ]]; do
    printf '  %2s) %s\n' "${MENU_ITEMS[i]}" "${MENU_ITEMS[i+1]}"
    i=$((i + 2))
  done
  local choice
  read -r -p "Choice: " choice
  echo "${choice}"
}

main() {
  log_info "OS: ${OS_ID} ${OS_VER} (target: ubuntu 24.04 LTS)"
  log_info "Docker: ${DOCKER_BIN:-not installed}"
  log_info "Compose: ${DOCKER_COMPOSE[*]:-not available}"
  if [[ "${OS_ID}" != "ubuntu" || "${OS_VER}" != "24.04" ]]; then
    log_warn "Unsupported OS detected; some actions may fail. Target is Ubuntu 24.04 LTS."
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

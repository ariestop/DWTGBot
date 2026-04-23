#!/usr/bin/env bash
# =====================================================================
# Установка systemd-сервиса автодеплоя.
#
# Usage:
#   sudo bash deploy/scripts/install_autodeploy.sh nl1
#   sudo bash deploy/scripts/install_autodeploy.sh nl2
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${1:-}"
CONFIG_DIR="/etc/dwtgbot"
ENV_DST="${CONFIG_DIR}/autodeploy.env"
SERVICE_DST="/etc/systemd/system/dwtgbot-autodeploy.service"
TIMER_DST="/etc/systemd/system/dwtgbot-autodeploy.timer"
STATE_DIR="/var/lib/dwtgbot/autodeploy"

GITHUB_OWNER=""
GITHUB_REPO=""
GHCR_REPO_PREFIX=""

prompt_target_if_needed() {
  if [[ -z "${TARGET}" ]]; then
    prompt_value TARGET "Целевой стек (nl1/nl2)" "nl1"
  fi
  [[ "${TARGET}" == "nl1" || "${TARGET}" == "nl2" ]] || die "Использование: install_autodeploy.sh nl1|nl2"
}

infer_repo_identity() {
  local remote
  local normalized
  local owner_lc
  local repo_lc

  remote="$(git -C "${PROJECT_ROOT}" remote get-url origin 2>/dev/null || true)"
  [[ -n "${remote}" ]] || die "Не удалось определить git remote origin из ${PROJECT_ROOT}"

  normalized="${remote%.git}"
  normalized="${normalized#git@github.com:}"
  normalized="${normalized#ssh://git@github.com/}"
  normalized="${normalized#https://github.com/}"
  normalized="${normalized#http://github.com/}"

  GITHUB_OWNER="${normalized%%/*}"
  GITHUB_REPO="${normalized##*/}"

  [[ -n "${GITHUB_OWNER}" && -n "${GITHUB_REPO}" ]] || die "Не удалось разобрать GitHub owner/repo из origin URL: ${remote}"

  owner_lc="$(printf '%s' "${GITHUB_OWNER}" | tr '[:upper:]' '[:lower:]')"
  repo_lc="$(printf '%s' "${GITHUB_REPO}" | tr '[:upper:]' '[:lower:]')"
  GHCR_REPO_PREFIX="ghcr.io/${owner_lc}/${repo_lc}"
}

install_units() {
  log_step "Установка systemd unit-файлов"
  install -d -m 0755 "${CONFIG_DIR}"
  install -d -m 0755 "${STATE_DIR}"
  install -m 0644 "${PROJECT_ROOT}/deploy/systemd/dwtgbot-autodeploy.service" "${SERVICE_DST}"
  install -m 0644 "${PROJECT_ROOT}/deploy/systemd/dwtgbot-autodeploy.timer" "${TIMER_DST}"
}

install_env_file_if_missing() {
  if [[ -f "${ENV_DST}" ]]; then
    log_ok "Конфиг уже существует: ${ENV_DST}"
    return
  fi

  log_step "Создание ${ENV_DST}"
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__TARGET__|${TARGET}|g" \
    -e "s|__REPO_PATH__|${PROJECT_ROOT}|g" \
    -e "s|__GITHUB_OWNER__|${GITHUB_OWNER}|g" \
    -e "s|__GITHUB_REPO__|${GITHUB_REPO}|g" \
    -e "s|__GHCR_REPO_PREFIX__|${GHCR_REPO_PREFIX}|g" \
    "${PROJECT_ROOT}/deploy/systemd/autodeploy.env.example" >"${tmp}"
  install -m 0600 "${tmp}" "${ENV_DST}"
  rm -f "${tmp}"
  log_warn "Заполните GITHUB_TOKEN в ${ENV_DST} перед первым запуском."
}

reload_systemd() {
  log_step "Перезагрузка конфигурации systemd"
  systemctl daemon-reload
}

enable_timer() {
  if ! confirm "Включить и запустить dwtgbot-autodeploy.timer сейчас?" "Y"; then
    log_warn "Таймер не включён. Позже его можно включить так:"
    echo "  systemctl enable --now dwtgbot-autodeploy.timer"
    return
  fi
  log_step "Включение таймера"
  systemctl enable --now dwtgbot-autodeploy.timer
  log_ok "Таймер включён"
}

print_next_steps() {
  cat <<EOF

Дальнейшие шаги:
  1. Отредактируйте ${ENV_DST} и задайте реальный GITHUB_TOKEN
  2. Проверьте таймер:
       systemctl status dwtgbot-autodeploy.timer
  3. Выполните одну ручную проверку:
       systemctl start dwtgbot-autodeploy.service
       journalctl -u dwtgbot-autodeploy.service -n 200 --no-pager
EOF
}

main() {
  require_root
  require_command systemctl
  require_command git

  prompt_target_if_needed
  infer_repo_identity
  install_units
  install_env_file_if_missing
  reload_systemd
  enable_timer
  print_next_steps
}

main "$@"

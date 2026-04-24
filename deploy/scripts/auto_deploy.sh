#!/usr/bin/env bash
# =====================================================================
# Автодеплой по таймеру systemd.
#
# Скрипт проверяет последний commit в GitHub, дожидается успешных
# `ci.yml` и `build-images.yml`, убеждается в доступности immutable
# образов `sha-<short>` и запускает существующий deploy_update.sh.
# Для NL-2 добавлена межсерверная координация: деплой разрешён только
# после успешного deployment status от NL-1 для того же SHA.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

TARGET="${DEPLOY_TARGET:-}"
GITHUB_API_URL="${GITHUB_API_URL:-https://api.github.com}"
GITHUB_OWNER="${GITHUB_OWNER:-}"
GITHUB_REPO="${GITHUB_REPO:-}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GHCR_REPO_PREFIX="${GHCR_REPO_PREFIX:-}"
CI_WORKFLOW_FILE="${CI_WORKFLOW_FILE:-ci.yml}"
BUILD_WORKFLOW_FILE="${BUILD_WORKFLOW_FILE:-build-images.yml}"
GITHUB_ENVIRONMENT_NL1="${GITHUB_ENVIRONMENT_NL1:-nl1-autodeploy}"
GITHUB_ENVIRONMENT_NL2="${GITHUB_ENVIRONMENT_NL2:-nl2-autodeploy}"
REPO_PATH="${REPO_PATH:-${PROJECT_ROOT}}"
AUTODEPLOY_STATE_DIR="${AUTODEPLOY_STATE_DIR:-/var/lib/dwtgbot/autodeploy}"
AUTODEPLOY_DRY_RUN="${AUTODEPLOY_DRY_RUN:-0}"

LOCK_FILE=""
LAST_SUCCESS_FILE=""
CANDIDATE_SHA=""
DEPLOYMENT_ID=""
DEPLOYMENT_ENVIRONMENT=""

require_env_var() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "Не задана обязательная переменная окружения: ${name}"
}

setup_paths() {
  mkdir -p "${AUTODEPLOY_STATE_DIR}"
  LOCK_FILE="${AUTODEPLOY_STATE_DIR}/${TARGET}.lock"
  LAST_SUCCESS_FILE="${AUTODEPLOY_STATE_DIR}/${TARGET}.last_successful_sha"
}

acquire_lock() {
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    log_warn "Автодеплой для ${TARGET} уже выполняется, пропускаю этот запуск."
    exit 0
  fi
}

github_api() {
  local method="$1"
  local endpoint="$2"
  local payload="${3:-}"
  local url="${GITHUB_API_URL}${endpoint}"
  local args=(
    --silent
    --show-error
    --fail
    --location
    --connect-timeout 10
    --max-time 60
    --retry 3
    --retry-delay 1
    -X "${method}"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
    -H "Authorization: Bearer ${GITHUB_TOKEN}"
  )
  if [[ -n "${payload}" ]]; then
    args+=(-H "Content-Type: application/json" --data "${payload}")
  fi
  curl "${args[@]}" "${url}"
}

github_repo_api() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"
  github_api "${method}" "/repos/${GITHUB_OWNER}/${GITHUB_REPO}${path}" "${payload}"
}

git_origin_url() {
  git -C "${REPO_PATH}" remote get-url origin 2>/dev/null || true
}

git_github_auth_header() {
  local token_b64=""
  token_b64="$(printf 'x-access-token:%s' "${GITHUB_TOKEN}" | base64 | tr -d '\n')"
  printf 'AUTHORIZATION: basic %s' "${token_b64}"
}

git_repo() {
  local args=(-C "${REPO_PATH}")
  local remote_url=""

  remote_url="$(git_origin_url)"
  if [[ "${remote_url}" == https://github.com/* ]]; then
    args+=(-c "http.https://github.com/.extraheader=$(git_github_auth_header)")
  fi

  git "${args[@]}" "$@"
}

branch_head_sha() {
  github_repo_api GET "/commits/${GITHUB_BRANCH}" | jq -r '.sha'
}

workflow_state_tsv() {
  local workflow_file="$1"
  local sha="$2"
  github_repo_api GET "/actions/workflows/${workflow_file}/runs?head_sha=${sha}&per_page=20" \
    | jq -r --arg sha "${sha}" '
        first(
          .workflow_runs[]
          | select(.head_sha == $sha)
          | [.status, (.conclusion // ""), (.html_url // "")]
          | @tsv
        ) // "missing\t\t"
      '
}

workflow_completed_successfully() {
  local workflow_file="$1"
  local label="$2"
  local sha="$3"
  local state
  local status
  local conclusion
  local url

  state="$(workflow_state_tsv "${workflow_file}" "${sha}")"
  status="${state%%$'\t'*}"
  state="${state#*$'\t'}"
  conclusion="${state%%$'\t'*}"
  url="${state#*$'\t'}"

  if [[ "${status}" == "completed" && "${conclusion}" == "success" ]]; then
    log_ok "${label} для ${sha} завершён успешно"
    return 0
  fi

  case "${status}" in
    missing)
      log_info "${label} для ${sha} ещё не найден в GitHub Actions"
      ;;
    completed)
      log_warn "${label} для ${sha} завершён с итогом '${conclusion}'"
      ;;
    *)
      log_info "${label} для ${sha} ещё выполняется (status='${status}')"
      ;;
  esac
  if [[ -n "${url}" ]]; then
    log_info "Ссылка на workflow: ${url}"
  fi
  return 1
}

short_sha() {
  local sha="$1"
  printf '%.7s' "${sha}"
}

prepare_image_env() {
  local image_tag=""
  image_tag="sha-$(short_sha "${CANDIDATE_SHA}")"

  export IMAGE_API="${GHCR_REPO_PREFIX}-api:${image_tag}"
  export IMAGE_WORKER="${GHCR_REPO_PREFIX}-worker:${image_tag}"

  if [[ "${TARGET}" == "nl1" ]]; then
    export IMAGE_BOT="${GHCR_REPO_PREFIX}-bot:${image_tag}"
    export IMAGE_BACKUP="${GHCR_REPO_PREFIX}-backup:${image_tag}"
  fi
}

required_images_for_target() {
  if [[ "${TARGET}" == "nl1" ]]; then
    printf '%s\n' "${IMAGE_BOT}" "${IMAGE_API}" "${IMAGE_BACKUP}"
  else
    printf '%s\n' "${IMAGE_API}" "${IMAGE_WORKER}"
  fi
}

preflight_images() {
  local image=""
  while IFS= read -r image; do
    [[ -n "${image}" ]] || continue
    log_info "Проверяю доступность образа ${image}"
    docker manifest inspect "${image}" >/dev/null
  done < <(required_images_for_target)
}

last_successful_sha() {
  if [[ -f "${LAST_SUCCESS_FILE}" ]]; then
    tr -d '[:space:]' <"${LAST_SUCCESS_FILE}"
  fi
}

current_repo_sha() {
  git -C "${REPO_PATH}" rev-parse HEAD 2>/dev/null || true
}

candidate_already_deployed() {
  local recorded_sha=""
  local current_sha=""

  recorded_sha="$(last_successful_sha)"
  [[ -n "${recorded_sha}" ]] || return 1
  [[ "${recorded_sha}" == "${CANDIDATE_SHA}" ]] || return 1

  current_sha="$(current_repo_sha)"
  if [[ "${current_sha}" == "${CANDIDATE_SHA}" ]]; then
    return 0
  fi

  log_warn "Файл состояния говорит, что ${CANDIDATE_SHA} уже развернут на ${TARGET}, но checkout сейчас на '${current_sha:-unknown}'. Повторяю deploy для выравнивания."
  return 1
}

mark_last_successful_sha() {
  local tmp_file=""
  tmp_file="$(mktemp "${LAST_SUCCESS_FILE}.XXXXXX")"
  printf '%s\n' "${CANDIDATE_SHA}" >"${tmp_file}"
  mv "${tmp_file}" "${LAST_SUCCESS_FILE}"
}

checkout_candidate_sha() {
  log_step "Подготовка репозитория к деплою"
  git_repo fetch --all --tags
  git_repo checkout --detach "${CANDIDATE_SHA}"
  log_ok "Репозиторий переключён на ${CANDIDATE_SHA}"
}

deployment_environment_for_target() {
  if [[ "${TARGET}" == "nl1" ]]; then
    printf '%s' "${GITHUB_ENVIRONMENT_NL1}"
  else
    printf '%s' "${GITHUB_ENVIRONMENT_NL2}"
  fi
}

create_deployment() {
  local environment="$1"
  local description="$2"
  local payload
  payload="$(
    jq -cn \
      --arg ref "${CANDIDATE_SHA}" \
      --arg environment "${environment}" \
      --arg description "${description}" \
      '{
        ref: $ref,
        environment: $environment,
        description: $description,
        auto_merge: false,
        required_contexts: [],
        transient_environment: false,
        production_environment: true
      }'
  )"
  github_repo_api POST "/deployments" "${payload}" | jq -r '.id'
}

set_deployment_status() {
  local deployment_id="$1"
  local state="$2"
  local description="$3"
  local payload
  payload="$(
    jq -cn \
      --arg state "${state}" \
      --arg description "${description}" \
      '{
        state: $state,
        description: $description
      }'
  )"
  github_repo_api POST "/deployments/${deployment_id}/statuses" "${payload}" >/dev/null
}

ensure_deployment_started() {
  DEPLOYMENT_ENVIRONMENT="$(deployment_environment_for_target)"
  DEPLOYMENT_ID="$(create_deployment "${DEPLOYMENT_ENVIRONMENT}" "Автодеплой ${TARGET} для ${CANDIDATE_SHA}")"
  log_info "Создан GitHub deployment ${DEPLOYMENT_ID} (${DEPLOYMENT_ENVIRONMENT})"
  set_deployment_status "${DEPLOYMENT_ID}" "in_progress" "Автодеплой ${TARGET} запущен"
}

deployment_success_exists() {
  local sha="$1"
  local environment="$2"
  local deployments
  local deployment_id=""
  local status_payload=""

  deployments="$(github_repo_api GET "/deployments?sha=${sha}&environment=${environment}&per_page=20")"
  while IFS= read -r deployment_id; do
    [[ -n "${deployment_id}" ]] || continue
    status_payload="$(github_repo_api GET "/deployments/${deployment_id}/statuses?per_page=20")"
    if printf '%s' "${status_payload}" | jq -e 'map(select(.state == "success")) | length > 0' >/dev/null; then
      return 0
    fi
  done < <(printf '%s' "${deployments}" | jq -r '.[].id')
  return 1
}

wait_for_nl1_success_gate() {
  if [[ "${TARGET}" != "nl2" ]]; then
    return 0
  fi
  if deployment_success_exists "${CANDIDATE_SHA}" "${GITHUB_ENVIRONMENT_NL1}"; then
    log_ok "GitHub подтверждает успешный автодеплой NL-1 для ${CANDIDATE_SHA}"
    return 0
  fi
  log_info "NL-1 ещё не подтвердил успешный автодеплой для ${CANDIDATE_SHA}, NL-2 пока ждёт"
  return 1
}

compose_for_target() {
  if [[ "${TARGET}" == "nl1" ]]; then
    compose_nl1 "$@"
  else
    compose_nl2 "$@"
  fi
}

dump_stack_state() {
  log_warn "Печатаю состояние стека ${TARGET} после ошибки"
  compose_for_target ps || true
  compose_for_target logs --tail=200 || true
}

run_stack_deploy() {
  log_step "Запуск deploy_update.sh для ${TARGET}"
  AUTODEPLOY_SKIP_GIT_PULL=1 bash "${SCRIPT_DIR}/deploy_update.sh" "${TARGET}"
}

on_autodeploy_error() {
  local line="$1"
  local code="${2:-1}"
  trap - ERR
  set +e
  log_error "Автодеплой ${TARGET} завершился с ошибкой на строке ${line} (exit code ${code})"
  if [[ -n "${DEPLOYMENT_ID}" ]]; then
    set_deployment_status "${DEPLOYMENT_ID}" "failure" "Автодеплой ${TARGET} завершился ошибкой" || true
  fi
  dump_stack_state
  exit "${code}"
}

validate_inputs() {
  [[ "${TARGET}" == "nl1" || "${TARGET}" == "nl2" ]] || die "DEPLOY_TARGET должен быть nl1 или nl2"
  require_env_var GITHUB_OWNER
  require_env_var GITHUB_REPO
  require_env_var GITHUB_TOKEN
  require_env_var GHCR_REPO_PREFIX
  require_env_var REPO_PATH
  [[ -d "${REPO_PATH}" ]] || die "Путь к репозиторию не существует: ${REPO_PATH}"
}

log_context() {
  log_info "Цель деплоя: ${TARGET}"
  log_info "Репозиторий: ${GITHUB_OWNER}/${GITHUB_REPO}"
  log_info "Ветка: ${GITHUB_BRANCH}"
  log_info "Путь к checkout: ${REPO_PATH}"
  if [[ "${AUTODEPLOY_DRY_RUN}" == "1" ]]; then
    log_warn "Включён dry-run режим"
  fi
}

main() {
  trap 'on_autodeploy_error ${LINENO} $?' ERR

  require_command curl
  require_command jq
  require_command git
  require_command docker
  require_command flock
  require_command base64

  validate_inputs
  setup_paths
  acquire_lock
  log_context

  CANDIDATE_SHA="$(branch_head_sha)"
  [[ -n "${CANDIDATE_SHA}" && "${CANDIDATE_SHA}" != "null" ]] || die "Не удалось определить SHA головы ветки"
  log_info "Последний commit в GitHub: ${CANDIDATE_SHA}"

  if candidate_already_deployed; then
    log_info "SHA ${CANDIDATE_SHA} уже успешно развернут на ${TARGET}, изменений нет"
    exit 0
  fi

  if ! workflow_completed_successfully "${CI_WORKFLOW_FILE}" "CI" "${CANDIDATE_SHA}"; then
    log_info "Автодеплой откладывается до успешного завершения CI"
    exit 0
  fi

  if ! workflow_completed_successfully "${BUILD_WORKFLOW_FILE}" "Сборка образов" "${CANDIDATE_SHA}"; then
    log_info "Автодеплой откладывается до публикации образов"
    exit 0
  fi

  if ! wait_for_nl1_success_gate; then
    exit 0
  fi

  prepare_image_env
  preflight_images

  if [[ "${AUTODEPLOY_DRY_RUN}" == "1" ]]; then
    log_ok "Dry-run: проверки пройдены, реальный деплой не выполняю"
    exit 0
  fi

  if [[ "${TARGET}" == "nl1" ]]; then
    ensure_deployment_started
  fi

  checkout_candidate_sha
  run_stack_deploy
  mark_last_successful_sha

  if [[ -n "${DEPLOYMENT_ID}" ]]; then
    set_deployment_status "${DEPLOYMENT_ID}" "success" "Автодеплой ${TARGET} завершён успешно"
  fi

  log_ok "Автодеплой ${TARGET} завершён успешно для ${CANDIDATE_SHA}"
}

main "$@"

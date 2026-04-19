#!/usr/bin/env bash
# =====================================================================
# Manual cleanup helper for the media plane (NL-2).
# - Removes leftover scratch directories under STORAGE_TMP_PATH older than N hours.
# - Triggers a one-shot DB cleanup cycle in the cleanup container.
#
# Usage:
#   bash deploy/scripts/cleanup.sh                        # tmp older than 24h
#   TMP_MAX_AGE_HOURS=2 bash deploy/scripts/cleanup.sh
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"

[[ -f "${DEPLOY_DIR}/nl2/.env" ]] || die "NL-2 .env not found (cleanup runs on NL-2)"
# shellcheck disable=SC1091
source "${DEPLOY_DIR}/nl2/.env"

TMP_MAX_AGE_HOURS="${TMP_MAX_AGE_HOURS:-24}"
TMP_PATH="${STORAGE_TMP_PATH:-/var/lib/dwtgbot/tmp}"

log_step "Removing scratch dirs older than ${TMP_MAX_AGE_HOURS}h under ${TMP_PATH}"
if [[ -d "${TMP_PATH}" ]]; then
  # We use minutes for portability with old find versions.
  minutes=$((TMP_MAX_AGE_HOURS * 60))
  find "${TMP_PATH}" -mindepth 1 -maxdepth 2 -type d -mmin "+${minutes}" -print -prune \
    -exec rm -rf {} + || true
  log_ok "Tmp cleanup done"
else
  log_warn "Tmp path missing: ${TMP_PATH}"
fi

log_step "Triggering one-shot DB+files cleanup cycle"
compose_nl2 run --rm cleanup python -c \
  "import asyncio; from app.workers.cleanup_worker import _run_cycle, build_api;
from app.config import get_settings;
from app.infrastructure.db.repositories.media_cache_repo_impl import SqlAlchemyMediaCacheRepository;
async def main():
    s = get_settings(); c = build_api(s)
    try:
        await _run_cycle(c, SqlAlchemyMediaCacheRepository(c.core.sessionmaker))
    finally:
        await c.aclose()
asyncio.run(main())"

log_ok "Cleanup finished"

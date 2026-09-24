#!/usr/bin/env bash
# =====================================================================
# Manual cleanup helper for the media plane (single or NL-2).
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

STACK="$(find_stack_with media)" \
  || die "No .env for a media-plane stack (cleanup runs on the single or NL-2 host)"
# shellcheck disable=SC1090
source "$(stack_env_file "${STACK}")"

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
# Audit fix A1: cleanup_worker was refactored (audit fix L13) to use
# ``build_cleanup`` (async) instead of the now-removed ``build_api``
# helper. ``_run_cycle`` takes ``orphan_age_seconds`` as a keyword-only
# arg and pulls MediaCacheRepo off the composition internally — no need
# to construct it here.
compose_stack "${STACK}" run --rm cleanup python -c \
  "import asyncio
from app.composition import build_cleanup
from app.config import get_settings
from app.workers.cleanup_worker import _run_cycle
async def main():
    s = get_settings()
    c = await build_cleanup(s)
    try:
        await _run_cycle(c, orphan_age_seconds=s.ORPHAN_JOB_AGE_SECONDS)
    finally:
        await c.aclose()
asyncio.run(main())"

log_ok "Cleanup finished"

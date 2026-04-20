#!/usr/bin/env bash
# ---------------------------------------------------------------------
# Load test — concurrency ramp (docs/37-load-and-capacity.md §6.2)
# Parallel enqueue at c=1,2,4,8,16 with a drain between steps. The
# system's practical ceiling is the level where throughput stops
# climbing.
#
# Each parallel slot uses a different synthetic user_id so the
# MAX_CONCURRENT_JOBS_PER_USER cap does NOT gate the test before
# upstream does. On the real worker side, that's what you want —
# you're measuring the system, not the cap.
#
# Requires redis-cli to wait for the queue to drain; fall back to a
# fixed sleep via DRAIN_SLEEP_SECONDS if redis-cli is unavailable.
# ---------------------------------------------------------------------
set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
URL="${URL:-https://www.youtube.com/watch?v=jNQXAC9IVRw}"
JOBS_PER_STEP="${JOBS_PER_STEP:-50}"
LEVELS="${LEVELS:-1 2 4 8 16}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
QUEUE_KEY="${QUEUE_KEY:-arq:queue}"
DRAIN_SLEEP_SECONDS="${DRAIN_SLEEP_SECONDS:-120}"
TOKEN="${INTERNAL_TEST_TOKEN:?set INTERNAL_TEST_TOKEN to use the test endpoint}"

have_redis_cli=0
if command -v redis-cli >/dev/null 2>&1; then
  have_redis_cli=1
fi

wait_for_drain() {
  if [[ "${have_redis_cli}" -eq 1 ]]; then
    echo "[ramp] waiting for queue ${QUEUE_KEY} to drain"
    while [[ "$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" LLEN "${QUEUE_KEY}")" -gt 0 ]]; do
      sleep 1
    done
  else
    echo "[ramp] redis-cli not available — sleeping ${DRAIN_SLEEP_SECONDS}s as fallback"
    sleep "${DRAIN_SLEEP_SECONDS}"
  fi
}

enqueue_one() {
  local uid=$1
  curl -fsS -X POST "${API_URL}/internal/test/enqueue" \
    -H "X-Internal-Test-Token: ${TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{
      \"user_id\": ${uid},
      \"chat_id\": 1,
      \"source_url\": \"${URL}\",
      \"platform\": \"youtube\",
      \"selected_option_key\": \"video_360\"
    }" > /dev/null
}
export -f enqueue_one
export API_URL URL TOKEN

for c in ${LEVELS}; do
  echo "=== concurrency=${c} ==="
  step_start=$(date +%s)
  seq 1 "${JOBS_PER_STEP}" \
    | xargs -n1 -P "${c}" -I{} bash -c 'enqueue_one $((1000 + RANDOM % 1000))'
  step_end=$(date +%s)
  echo "[ramp] enqueued ${JOBS_PER_STEP} jobs in $((step_end - step_start))s at c=${c}"
  wait_for_drain
done

echo "[ramp] done"
echo "[ramp] for each c level, read A4/A5 p95 + queue-drain time from dashboards"
echo "[ramp] plot jobs/h vs c — the plateau is your ceiling"

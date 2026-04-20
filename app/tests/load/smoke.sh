#!/usr/bin/env bash
# ---------------------------------------------------------------------
# Load test — smoke (docs/37-load-and-capacity.md §6.1)
# 100 jobs serially through /internal/test/enqueue. Watch the
# dashboards during and after, then record a row in
# docs/tests/load-history.md.
#
# Env knobs:
#   INTERNAL_TEST_TOKEN  (required)  — test endpoint auth
#   API_URL              (default http://localhost:8080)
#   URL                  (default YouTube "Me at the zoo" 18s clip)
#   USER_ID              (default 1)
#   CHAT_ID              (default 1)
#   COUNT                (default 100)
#   DELAY_MS             (default 0; set e.g. 100 to throttle)
# ---------------------------------------------------------------------
set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
URL="${URL:-https://www.youtube.com/watch?v=jNQXAC9IVRw}"
USER_ID="${USER_ID:-1}"
CHAT_ID="${CHAT_ID:-1}"
COUNT="${COUNT:-100}"
DELAY_MS="${DELAY_MS:-0}"
TOKEN="${INTERNAL_TEST_TOKEN:?set INTERNAL_TEST_TOKEN to use the test endpoint}"

echo "[smoke] enqueuing ${COUNT} jobs to ${API_URL} (url=${URL})"
start_ts=$(date +%s)

for i in $(seq 1 "${COUNT}"); do
  curl -fsS -X POST "${API_URL}/internal/test/enqueue" \
    -H "X-Internal-Test-Token: ${TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{
      \"user_id\": ${USER_ID},
      \"chat_id\": ${CHAT_ID},
      \"source_url\": \"${URL}\",
      \"platform\": \"youtube\",
      \"selected_option_key\": \"video_360\"
    }" > /dev/null
  echo "[smoke] enqueued ${i}/${COUNT}"
  if [[ "${DELAY_MS}" -gt 0 ]]; then
    sleep "$(awk "BEGIN { printf \"%.3f\", ${DELAY_MS}/1000 }")"
  fi
done

end_ts=$(date +%s)
elapsed=$(( end_ts - start_ts ))
echo "[smoke] done — ${COUNT} enqueued in ${elapsed}s"
echo "[smoke] now watch dashboards:"
echo "  - B3 (queue depth) should peak, then drain"
echo "  - B4 (worker active) should pin at WORKER_CONCURRENCY"
echo "  - A4/A5 p95 (avg_job_seconds) gives you observed jobs/h"
echo "[smoke] record result in docs/tests/load-history.md"

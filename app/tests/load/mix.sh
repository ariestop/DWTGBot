#!/usr/bin/env bash
# ---------------------------------------------------------------------
# Load test — weighted file-size mix (docs/37-load-and-capacity.md §6.3)
# Real traffic is 50% small / 30% medium / 20% large. This script
# samples URLs in that ratio; supply three URLs from your own corpus
# (do NOT load-test public platforms at volume, §11 row 7).
# ---------------------------------------------------------------------
set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
COUNT="${COUNT:-200}"
TOKEN="${INTERNAL_TEST_TOKEN:?set INTERNAL_TEST_TOKEN to use the test endpoint}"
URL_SMALL="${URL_SMALL:?set URL_SMALL (<10 MiB clip)}"
URL_MEDIUM="${URL_MEDIUM:?set URL_MEDIUM (~30 MiB)}"
URL_LARGE="${URL_LARGE:?set URL_LARGE (~150 MiB)}"

pick_url() {
  local r=$((RANDOM % 10))
  if (( r < 5 )); then
    echo "${URL_SMALL}:video_360"
  elif (( r < 8 )); then
    echo "${URL_MEDIUM}:video_720"
  else
    echo "${URL_LARGE}:video_1080"
  fi
}

echo "[mix] ${COUNT} jobs at ratio 5:3:2 (small:medium:large)"
for i in $(seq 1 "${COUNT}"); do
  pair="$(pick_url)"
  url="${pair%:*}"
  opt="${pair##*:}"
  curl -fsS -X POST "${API_URL}/internal/test/enqueue" \
    -H "X-Internal-Test-Token: ${TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{
      \"user_id\": $((1000 + i)),
      \"chat_id\": 1,
      \"source_url\": \"${url}\",
      \"platform\": \"youtube\",
      \"selected_option_key\": \"${opt}\"
    }" > /dev/null
  if (( i % 20 == 0 )); then
    echo "[mix] enqueued ${i}/${COUNT}"
  fi
done
echo "[mix] done"
echo "[mix] this is the closest synthetic to real traffic;"
echo "      observe A4 vs A5 split + storage growth curve"

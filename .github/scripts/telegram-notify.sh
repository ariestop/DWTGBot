#!/usr/bin/env bash
# =====================================================================
# Send a plain-text message through the Telegram Bot API.
#
#   TG_TOKEN=<bot token> TG_CHAT=<id>[,<id>...] telegram-notify.sh "text"
#
# Used by GitHub Actions (secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).
# A missing secret or a failed send is a warning, never a job failure:
# notifications must not block an update or a deploy.
# =====================================================================
set -Eeuo pipefail

text="${1:?usage: telegram-notify.sh \"text\"}"

if [[ -z "${TG_TOKEN:-}" || -z "${TG_CHAT:-}" ]]; then
  echo "::warning::TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, notification skipped"
  exit 0
fi

IFS=',' read -r -a chats <<<"${TG_CHAT}"
for chat in "${chats[@]}"; do
  chat="${chat// /}"
  [[ -n "${chat}" ]] || continue
  if ! curl -fsS --retry 3 --max-time 20 -o /dev/null \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=${text}" \
    --data "disable_web_page_preview=true" \
    "https://api.telegram.org/bot${TG_TOKEN}/sendMessage"; then
    echo "::warning::Telegram notification to chat ${chat} failed"
  fi
done

#!/bin/sh
set -e

notify() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" \
    -o /dev/null 2>&1 || true
}

# Ensure HuggingFace cache dir is writable (volume may have stale root ownership)
mkdir -p "$HOME/.cache/huggingface" 2>/dev/null || true

# Forward signals to the python process for clean shutdown
trap 'kill -TERM "$PID" 2>/dev/null' TERM INT

notify "Enki is online"

START=$(date +%s)
python main.py telegram &
PID=$!
wait "$PID"
CODE=$?

UPTIME=$(( $(date +%s) - START ))

# Only alert on crash if the process ran >30s (avoids spam on rapid boot failure loops)
if [ $UPTIME -gt 30 ]; then
  notify "Enki went offline (exit ${CODE}, uptime ${UPTIME}s)"
fi

exit $CODE

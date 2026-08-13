#!/bin/bash
set -e

if [ -n "$TELEGRAM_API_ID" ] && [ -n "$TELEGRAM_API_HASH" ]; then
    echo "Starting Local Bot API Server (uploads up to 2GB, no splitting)..."
    mkdir -p /app/botapi-data

    telegram-bot-api \
        --api-id="$TELEGRAM_API_ID" \
        --api-hash="$TELEGRAM_API_HASH" \
        --local \
        --http-port=8081 \
        --dir=/app/botapi-data &
    BOTAPI_PID=$!

    started=0
    for i in $(seq 1 30); do
        # If the process already died (bad binary, bad api-id/hash, etc.) stop
        # waiting immediately instead of burning the full 15s timeout.
        if ! kill -0 "$BOTAPI_PID" 2>/dev/null; then
            break
        fi
        if (echo > /dev/tcp/127.0.0.1/8081) 2>/dev/null; then
            started=1
            break
        fi
        sleep 0.5
    done

    if [ "$started" = "1" ]; then
        export TELEGRAM_BASE_URL="http://127.0.0.1:8081"
        echo "Local Bot API Server is up — uploads will use the 2GB limit."
    else
        # Do NOT export TELEGRAM_BASE_URL here — pointing the bot at a local
        # server that never came up would make every upload fail outright.
        # Falling through to the Cloud API (50MB) is the safe degraded mode.
        echo "Local Bot API Server failed to start within 15s — falling back to the standard Cloud Bot API (50MB upload limit). Check TELEGRAM_API_ID/TELEGRAM_API_HASH and the logs above for the actual error."
    fi
else
    echo "TELEGRAM_API_ID/TELEGRAM_API_HASH not set — using the standard Cloud Bot API (50MB upload limit)."
fi

exec python main.py

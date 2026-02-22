#!/bin/bash
# start.sh — Restart loop for the bot
# When the bot exits (e.g. /restart or crash), it auto-restarts after 5s

while true; do
    echo "━━━ Starting bot... ━━━"
    python3 -m f2lnk
    EXIT_CODE=$?
    echo "━━━ Bot exited with code $EXIT_CODE ━━━"
    echo "Restarting in 5 seconds..."
    sleep 5
done

#!/bin/bash
# start.sh — Start qBittorrent daemon + bot restart loop

# Start qBittorrent-nox in background on port 8090
echo "━━━ Starting qBittorrent daemon... ━━━"
qbittorrent-nox --daemon --webui-port=8090 2>/dev/null || echo "qBittorrent already running or not installed"
sleep 2

# Bot restart loop
while true; do
    echo "━━━ Starting bot... ━━━"
    python3 -m f2lnk
    EXIT_CODE=$?
    echo "━━━ Bot exited with code $EXIT_CODE ━━━"
    echo "Restarting in 5 seconds..."
    sleep 5
done

#!/bin/bash
# Auto-restart wrapper for the TikTok Extractor server
cd /home/z/my-project/tiktok_extractor
LOG=/home/z/my-project/tiktok_extractor/server.log

while true; do
    echo "[$(date '+%H:%M:%S')] Starting server..." >> "$LOG"
    PORT=3000 HOST=0.0.0.0 python3 app.py >> "$LOG" 2>&1 &
    SERVER_PID=$!
    echo "[$(date '+%H:%M:%S')] Server PID: $SERVER_PID" >> "$LOG"
    wait $SERVER_PID
    EXIT_CODE=$?
    echo "[$(date '+%H:%M:%S')] Server exited with code $EXIT_CODE, restarting in 2s..." >> "$LOG"
    sleep 2
done

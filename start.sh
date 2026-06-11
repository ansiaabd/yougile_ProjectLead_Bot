#!/bin/bash
cd "$(dirname "$0")"
PID_FILE="bot.pid"

if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
    echo "Bot already running (PID: $(cat $PID_FILE))"
    exit 1
fi

source venv/bin/activate
nohup python main.py > bot.log 2>&1 &
echo $! > "$PID_FILE"
echo "Bot started (PID: $!)"

#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
while true; do
    python main.py
    echo "$(date): Bot crashed, restarting in 3s..." >> restart.log
    sleep 3
done

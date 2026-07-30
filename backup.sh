#!/bin/bash
set -e

BACKUP_DIR="/home/admino/backups"
PROJECT_DIR="/home/admino/ProjectLead_Bot"

mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y-%m-%d)
FILE="$BACKUP_DIR/backup-$DATE.tar.gz"

tar -czf "$FILE" \
  -C "$PROJECT_DIR" \
  tasks.db .env yougile/.env

echo "Created: $FILE ($(du -h "$FILE" | cut -f1))"

# Keep last 5 backups (sorted by name = date)
BACKUPS=($(ls "$BACKUP_DIR"/backup-*.tar.gz 2>/dev/null | sort))
while [ ${#BACKUPS[@]} -gt 5 ]; do
  OLDEST="${BACKUPS[0]}"
  rm -f "$OLDEST"
  echo "Deleted old: $OLDEST"
  BACKUPS=("${BACKUPS[@]:1}")
done

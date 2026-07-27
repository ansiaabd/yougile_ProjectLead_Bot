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

# Keep: newest (always) + 5 days ago
NEWEST=$(ls -t "$BACKUP_DIR"/backup-*.tar.gz 2>/dev/null | head -1)
FIVE_DAYS=$(date -d "5 days ago" +%Y-%m-%d 2>/dev/null || date -v -5d +%Y-%m-%d)

for f in "$BACKUP_DIR"/backup-*.tar.gz; do
  [ -f "$f" ] || continue
  if [ "$f" = "$NEWEST" ]; then
    continue
  fi
  BASENAME=$(basename "$f" .tar.gz)
  FDATE=${BASENAME#backup-}
  if [ "$FDATE" = "$FIVE_DAYS" ]; then
    continue
  fi
  rm -f "$f"
  echo "Deleted old: $f"
done

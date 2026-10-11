#!/bin/bash
# Back up what GPR Studio cannot recreate: the database (accounts, sign-in log, verified labels,
# findings) and annotations/ (picks, reviews, field reports). Keeps the newest KEEP of each.
#   deploy/backup.sh                         -> backups/ in the project (gitignored)
#   BACKUP_DIR=/Volumes/Drive/GPR deploy/backup.sh   -> anywhere else, e.g. an external drive
# The radar files (Dataset/) are source data: keep the original deliveries safe separately.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-backups}"
KEEP="${KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST/db" "$DEST/annotations"

# A failed dump must fail the backup, not leave an empty file that looks like one.
db="$DEST/db/gpr-$STAMP.sql.gz"
if ! docker compose exec -T postgres pg_dump -U gpr gpr | gzip > "$db.part"; then
  rm -f "$db.part"; echo "backup FAILED: database dump (is Docker running?)" >&2; exit 1
fi
gzip -t "$db.part" && [ "$(gzip -dc "$db.part" | head -c 100 | wc -c)" -gt 0 ] || { rm -f "$db.part"; echo "backup FAILED: empty dump" >&2; exit 1; }
mv "$db.part" "$db"

ann="$DEST/annotations/annotations-$STAMP.tar.gz"
tar -czf "$ann.part" annotations && tar -tzf "$ann.part" > /dev/null && mv "$ann.part" "$ann"

for kind in db annotations; do  # newest KEEP stay
  ls -1t "$DEST/$kind" | grep -v '\.part$' | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$DEST/$kind/$old"; done
done
echo "backed up: $db ($(du -h "$db" | cut -f1)), $ann ($(du -h "$ann" | cut -f1))"

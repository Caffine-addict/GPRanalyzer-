#!/bin/sh
# Dump the gpr database to backups/gpr-<timestamp>.sql.gz (gitignored).
set -eu
cd "$(dirname "$0")/../.."
mkdir -p backups
docker compose exec -T postgres pg_dump -U gpr gpr | gzip > "backups/gpr-$(date +%Y%m%d-%H%M%S).sql.gz"
echo "written: $(ls -t backups | head -1)"

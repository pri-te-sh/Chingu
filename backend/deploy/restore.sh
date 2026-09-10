#!/usr/bin/env bash
# Restore a full backup set (destroys current data!):
#   deploy/restore.sh /opt/pixel/backups/pixel-YYYYMMDD-HHMM.dump [firmware-YYYYMMDD-HHMM.tgz] [env-YYYYMMDD-HHMM.bak]
# The dump is required; the firmware archive restores the binaries the dump's release rows point at; the env file is
# copied to .env only if you pass it (review it first - it contains secrets).
set -euo pipefail
cd "$(dirname "$0")/.."
F="${1:?dump file}"; FW="${2:-}"; ENVB="${3:-}"
read -r -p "This replaces the live database with $F. Type RESTORE to continue: " ok; [ "$ok" = RESTORE ]
docker compose stop brain
docker compose exec -T postgres psql -U pixel -d postgres -c "DROP DATABASE pixel WITH (FORCE)" -c "CREATE DATABASE pixel OWNER pixel"
docker compose exec -T postgres pg_restore -U pixel -d pixel --no-owner < "$F"
if [ -n "$FW" ]; then docker run --rm -v pixel_firmware:/fw -v "$(cd "$(dirname "$FW")" && pwd)":/in:ro alpine sh -c "rm -rf /fw/* && tar xzf /in/$(basename "$FW") -C /fw" && echo "firmware volume restored from $FW"; fi
if [ -n "$ENVB" ]; then install -m 600 "$ENVB" .env && echo ".env restored from $ENVB (restart with deploy/up.sh to apply)"; fi
docker compose start brain
echo restored

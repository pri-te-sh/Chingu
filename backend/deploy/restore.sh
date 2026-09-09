#!/usr/bin/env bash
# Restore a dump into the running Postgres (destroys current data!):  deploy/restore.sh /opt/pixel/backups/pixel-YYYYMMDD-HHMM.dump
set -euo pipefail
cd "$(dirname "$0")/.."
F="${1:?dump file}"
read -r -p "This replaces the live database with $F. Type RESTORE to continue: " ok; [ "$ok" = RESTORE ]
docker compose stop brain
docker compose exec -T postgres psql -U pixel -d postgres -c "DROP DATABASE pixel WITH (FORCE)" -c "CREATE DATABASE pixel OWNER pixel"
docker compose exec -T postgres pg_restore -U pixel -d pixel --no-owner < "$F"
docker compose start brain
echo restored

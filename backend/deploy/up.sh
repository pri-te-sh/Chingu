#!/usr/bin/env bash
# (Re)deploy the current checkout: pull, build, migrate, start with the prod profile (Caddy TLS + Uptime Kuma).
set -euo pipefail
cd "$(dirname "$0")/.."
# Deploy a specific tested revision when given (CI passes the SHA it tested); plain `up.sh` follows origin/main.
git fetch -q origin
PREV=$(git rev-parse HEAD)
if [ -n "${1:-}" ]; then git checkout -q --detach "$1"; else git checkout -q main && git merge -q --ff-only origin/main; fi
echo "deploying $(git rev-parse --short HEAD)"
docker compose --profile prod up -d --build --remove-orphans
# The Caddyfile is a bind-mounted *file*: git replaces it with a new inode, which the running container never sees
# (a reload re-reads the stale one). Recreate the container whenever the file changed in this deploy.
if ! git diff --quiet "$PREV" HEAD -- Caddyfile 2>/dev/null; then docker compose --profile prod up -d --force-recreate --no-deps caddy; fi
docker image prune -f >/dev/null
docker compose ps

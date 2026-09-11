#!/usr/bin/env bash
# (Re)deploy the current checkout: pull, build, migrate, start with the prod profile (Caddy TLS + Uptime Kuma).
set -euo pipefail
cd "$(dirname "$0")/.."
# Deploy a specific tested revision when given (CI passes the SHA it tested); plain `up.sh` follows origin/main.
git fetch -q origin
if [ -n "${1:-}" ]; then git checkout -q --detach "$1"; else git checkout -q main && git merge -q --ff-only origin/main; fi
echo "deploying $(git rev-parse --short HEAD)"
docker compose --profile prod up -d --build --remove-orphans
# the Caddyfile is bind-mounted: a changed file needs a reload, which `up` alone does not do
docker compose --profile prod exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || true
docker image prune -f >/dev/null
docker compose ps

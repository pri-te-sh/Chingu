#!/usr/bin/env bash
# (Re)deploy the current checkout: pull, build, migrate, start with the prod profile (Caddy TLS + Uptime Kuma).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose --profile prod up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose ps

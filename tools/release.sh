#!/usr/bin/env bash
# Build the Pixel-Lite firmware and publish it to the local brain as an OTA release.
#   tools/release.sh 0.3.1 "what changed"      (version must match PIXEL_FW_VERSION in platformio.ini)
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:?usage: tools/release.sh <version> [notes]}"; NOTES="${2:-}"
grep -q "PIXEL_FW_VERSION=\\\\\"$VER\\\\\"" platformio.ini || { echo "platformio.ini says a different PIXEL_FW_VERSION - bump it to $VER first"; exit 1; }
.venv/bin/pio run -s
BIN=.pio/build/cyd32/firmware.bin
echo "built $BIN ($(stat -f%z "$BIN") bytes)"
docker compose -f backend/docker-compose.yml exec -T brain python -m pixel.tools_cli.publish_fw --version "$VER" --device-type lite --notes "$NOTES" < "$BIN"
echo "devices will pick it up on their daily check; use PIXELS > DEVICE > UPDATE NOW in the portal to push it immediately"

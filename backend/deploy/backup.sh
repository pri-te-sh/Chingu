#!/usr/bin/env bash
# Nightly Postgres dump (custom format, compressed). Local retention 14 days; mirrored to Backblaze B2 when an rclone remote named "b2" is configured.
set -euo pipefail
cd /opt/pixel/backend
DIR=/opt/pixel/backups; mkdir -p "$DIR"
F="$DIR/pixel-$(date -u +%Y%m%d-%H%M).dump"
docker compose exec -T postgres pg_dump -U pixel -d pixel -Fc > "$F"
# firmware binaries (their metadata is in the dump) and the runtime config, so a restore is complete
docker run --rm -v pixel_firmware:/fw:ro -v "$DIR":/out alpine tar czf "/out/firmware-$(date -u +%Y%m%d-%H%M).tgz" -C /fw . 2>/dev/null || echo "  (firmware volume not archived)"
install -m 600 .env "$DIR/env-$(date -u +%Y%m%d-%H%M).bak"
find "$DIR" \( -name 'pixel-*.dump' -o -name 'firmware-*.tgz' -o -name 'env-*.bak' \) -mtime +14 -delete
echo "$(date -u +%FT%TZ) dumped $(stat -c%s "$F") bytes to $F"
if command -v rclone >/dev/null && rclone listremotes | grep -q '^b2:'; then
  rclone copy "$F" b2:pixel-backups/ --quiet && echo "  mirrored to b2:pixel-backups/"
else
  echo "  WARNING: no off-site mirror configured (rclone remote 'b2' missing) - backups exist only on this host"
fi

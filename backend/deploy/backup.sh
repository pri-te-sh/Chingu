#!/usr/bin/env bash
# Nightly Postgres dump (custom format, compressed). Local retention 14 days; mirrored to Backblaze B2 when an rclone remote named "b2" is configured.
set -euo pipefail
cd /opt/pixel/backend
DIR=/opt/pixel/backups; mkdir -p "$DIR"
F="$DIR/pixel-$(date -u +%Y%m%d-%H%M).dump"
docker compose exec -T postgres pg_dump -U pixel -d pixel -Fc > "$F"
# firmware binaries (their metadata is in the dump) and the runtime config, so a restore is complete
STAMP=$(date -u +%Y%m%d-%H%M); FW="$DIR/firmware-$STAMP.tgz"; ENVB="$DIR/env-$STAMP.bak"; STATUS=0
docker run --rm -v pixel_firmware:/fw:ro -v "$DIR":/out alpine tar czf "/out/firmware-$STAMP.tgz" -C /fw . || { echo "  ERROR: firmware volume archive failed"; STATUS=1; }
install -m 600 .env "$ENVB" || { echo "  ERROR: could not copy .env"; STATUS=1; }
find "$DIR" \( -name 'pixel-*.dump' -o -name 'firmware-*.tgz' -o -name 'env-*.bak' \) -mtime +14 -delete
echo "$(date -u +%FT%TZ) dumped $(stat -c%s "$F") bytes to $F"
if command -v rclone >/dev/null && rclone listremotes | grep -q '^b2:'; then
  for f in "$F" "$FW" "$ENVB"; do [ -f "$f" ] && { rclone copy "$f" b2:pixel-backups/ --quiet || { echo "  ERROR: mirror failed for $f"; STATUS=1; }; }; done
  [ $STATUS -eq 0 ] && echo "  mirrored dump + firmware + env to b2:pixel-backups/ (bucket must be private: env contains secrets)"
else
  echo "  WARNING: no off-site mirror configured (rclone remote 'b2' missing) - backups exist only on this host"
fi
[ $STATUS -eq 0 ] || { echo "$(date -u +%FT%TZ) BACKUP INCOMPLETE"; exit 1; }

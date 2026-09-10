# Pixel brain: production runbook (Hetzner CX23, Ubuntu 26.04, pixel.priteshbhavsar.com)

## First deploy
1. Create the server (Ashburn, CPX21, Ubuntu 24.04, your SSH key). Note its IP.
2. DNS: `A pixel.<yourdomain> -> <ip>` (TTL 300). Caddy needs it before it can get a certificate.
3. On the server as root:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/<you>/CYD_Claude/main/backend/deploy/bootstrap.sh | bash -s -- https://github.com/<you>/CYD_Claude.git pixel.<yourdomain>
   nano /opt/pixel/backend/.env        # OLLAMA_API_KEY, DEV_LOGIN_EMAIL, DEV_LOGIN_PASSWORD
   sudo -u pixel /opt/pixel/backend/deploy/up.sh
   ```
4. Open `https://pixel.<yourdomain>/portal`, sign in, PIXELS > FIRMWARE > publish the current build.
5. Move the board: publish a firmware whose `PIXEL_DEFAULT_BRAIN_HOST`/port/TLS point at the new domain (`platformio.ini`), OTA it from the *old* brain, then Setup > Change Wi-Fi is **not** needed - the device keeps its Wi-Fi and pairing token. It will show a pairing code once because the new database has no pixels: pair it again from the new portal.
6. Import history: `deploy/restore.sh` with a dump taken from the laptop (`docker compose exec -T postgres pg_dump -U pixel -d pixel -Fc > laptop.dump`, `scp` it over). Do this *before* step 5 so the device's row (device_id `lite-0365e8`) and token carry over, and no re-pairing is needed.

## CI/CD
`.github/workflows/deploy.yml`: every push to `main` touching `backend/` runs the tests (Postgres + Redis service containers), then SSHes in as
`pixel` with the `pixel-ci-deploy` key (GitHub secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY`) and runs `deploy/up.sh`, then smoke-tests `/health` over HTTPS.
Firmware is *not* built by CI: release it from the laptop with `tools/release.sh <ver> "<notes>"` (targets the local brain) or publish the .bin
in the portal (PIXELS > FIRMWARE), or on the box: `docker compose exec -T brain python -m pixel.tools_cli.publish_fw --version X --device-type lite < fw.bin`.

## Day 2
- Update code: `sudo -u pixel /opt/pixel/backend/deploy/up.sh`
- Logs: `docker compose logs -f brain` (JSON lines), metrics at `/metrics` (localhost only via `curl -s localhost:8765/metrics` inside the box)
- Backups: nightly 03:15 UTC to `/opt/pixel/backups`, 14 days. Off-site: `apt install rclone && rclone config` (remote name `b2`, bucket `pixel-backups`).
- Uptime Kuma: `ssh -L 3001:localhost:3001 pixel@<ip>` then http://localhost:3001; add an HTTP monitor for `https://pixel.<yourdomain>/api/health` and a keyword monitor for the portal.
- Google sign-in: create an OAuth client (web), redirect URI `https://pixel.<yourdomain>/auth/callback`, put ID/secret in `.env`, set `PIXEL_ALLOW_DEV_LOGIN=0`, `up.sh`.

## Security posture
- Only 22/80/443 open (ufw). Brain, Postgres, Redis and Kuma are bound to localhost/Docker network; Caddy terminates TLS.
- SSH keys only, fail2ban on sshd, unattended security upgrades.
- Devices authenticate with per-device tokens (hashed in DB); firmware binaries are public but SHA-256 pinned by the manifest.

First automated deployment: 2026-09-10 (CI run after the deploy key secret was fixed).

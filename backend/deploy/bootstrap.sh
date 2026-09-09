#!/usr/bin/env bash
# One-shot server setup for the Pixel brain on a fresh Ubuntu 24.04 VM (Hetzner CPX, or any x86/arm64 box).
# Run as root on the server:
#   curl -fsSL https://raw.githubusercontent.com/<you>/CYD_Claude/main/backend/deploy/bootstrap.sh | bash -s -- <git-clone-url> <domain>
# Then: edit /opt/pixel/backend/.env (secrets), and run:  sudo -u pixel /opt/pixel/backend/deploy/up.sh
set -euo pipefail
REPO="${1:?git clone url}"; DOMAIN="${2:?domain, e.g. pixel.example.com}"
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get update -q && apt-get install -y -q ca-certificates curl git ufw fail2ban unattended-upgrades apt-listchanges
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== docker"
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
systemctl enable --now docker

echo "== user + repo"
id pixel >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker pixel
install -d -o pixel -g pixel /opt/pixel
if [ ! -d /opt/pixel/.git ]; then sudo -u pixel git clone "$REPO" /opt/pixel; else sudo -u pixel git -C /opt/pixel pull --ff-only; fi
cd /opt/pixel/backend
if [ ! -f .env ]; then
  sudo -u pixel cp .env.example .env
  sudo -u pixel sed -i \
    -e "s/^PIXEL_ENV=.*/PIXEL_ENV=prod/" \
    -e "s/^PIXEL_DOMAIN=.*/PIXEL_DOMAIN=$DOMAIN/" \
    -e "s#^PIXEL_PUBLIC_URL=.*#PIXEL_PUBLIC_URL=https://$DOMAIN#" \
    -e "s/^PIXEL_BIND=.*/PIXEL_BIND=127.0.0.1/" \
    -e "s/^PIXEL_ALLOW_DEV_LOGIN=.*/PIXEL_ALLOW_DEV_LOGIN=1/" \
    -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" \
    -e "s/^SESSION_SECRET=.*/SESSION_SECRET=$(openssl rand -hex 32)/" \
    -e "s/^OLLAMA_HOST=.*/OLLAMA_HOST=https:\/\/ollama.com/" .env
  chmod 600 .env
  echo "!! .env created - fill in OLLAMA_API_KEY, DEV_LOGIN_EMAIL, DEV_LOGIN_PASSWORD (and Google keys later)"
fi

echo "== firewall (ssh, http, https only)"
ufw --force reset >/dev/null
ufw default deny incoming; ufw default allow outgoing
ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp
ufw --force enable
# Docker publishes ports around ufw; the compose file binds brain/postgres/kuma to 127.0.0.1 in prod, so only Caddy is reachable.

echo "== fail2ban (sshd jail) + ssh hardening"
cat > /etc/fail2ban/jail.local <<'J'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
J
systemctl enable --now fail2ban
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh || systemctl reload sshd

echo "== backups (nightly 03:15 UTC, keeps 14 days locally; ships to B2 if rclone remote 'b2' exists)"
install -m 755 deploy/backup.sh /usr/local/bin/pixel-backup
cat > /etc/cron.d/pixel-backup <<'C'
15 3 * * * pixel /usr/local/bin/pixel-backup >> /var/log/pixel-backup.log 2>&1
C
touch /var/log/pixel-backup.log; chown pixel /var/log/pixel-backup.log

echo
echo "Done. Next:"
echo "  1. nano /opt/pixel/backend/.env   (OLLAMA_API_KEY, DEV_LOGIN_*)"
echo "  2. sudo -u pixel /opt/pixel/backend/deploy/up.sh"
echo "  3. point DNS A record $DOMAIN -> this server, then open https://$DOMAIN/portal"

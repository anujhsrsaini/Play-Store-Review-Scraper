#!/usr/bin/env bash
# bootstrap-box.sh — provision a single-box multi-app host on Oracle Ampere (Ubuntu, ARM64).
# Idempotent: safe to re-run. Run with a sudo-capable user. See deploy/MULTI_APP_HOST.md.
#
# What it does NOT do (cloud-side / manual):
#   - open 80/443 in the OCI VCN *security list* (console or oci-cli)
#   - restrict SSH (22) source to your IP in the OCI security list
#   - create Route 53 records
set -euo pipefail

test "$(uname -m)" = "aarch64" || { echo "Expected aarch64/ARM64 (Ampere)"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "==> base packages"
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg fail2ban unattended-upgrades \
    iptables-persistent apt-transport-https

echo "==> Docker Engine + compose plugin (arm64)"
if ! command -v docker >/dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker

echo "==> non-root deploy user + /opt layout"
if ! id deploy >/dev/null 2>&1; then
  sudo adduser --disabled-password --gecos "" deploy
  sudo usermod -aG docker deploy
fi
sudo mkdir -p /opt/apps /opt/caddy/apps.d /opt/box/backups
sudo chown -R deploy:deploy /opt/apps /opt/caddy /opt/box

echo "==> Caddy on the host (arm64)"
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update && sudo apt-get install -y caddy
fi
# Point host Caddy at the central Caddyfile if you've placed one (see deploy/Caddyfile.host).
[ -f /opt/caddy/Caddyfile ] && sudo cp /opt/caddy/Caddyfile /etc/caddy/Caddyfile || true
sudo systemctl enable --now caddy

echo "==> 4 GB swap + swappiness"
if ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf >/dev/null
sudo sysctl --system >/dev/null

echo "==> Docker log rotation"
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
sudo systemctl restart docker

echo "==> unattended security upgrades"
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> open 80/443 in host iptables ABOVE Oracle's default REJECT"
REJECT_LINE=$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/{print $1; exit}')
for p in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT "${REJECT_LINE:-1}" -p tcp --dport "$p" -m state --state NEW -j ACCEPT
  fi
done
sudo netfilter-persistent save

echo
echo "Bootstrap done. NEXT (manual):"
echo "  1) OCI console: security list ingress 80/443 from 0.0.0.0/0; restrict 22 to your IP."
echo "  2) Place /opt/caddy/Caddyfile (deploy/Caddyfile.host) + /opt/box/PORTS.md + /opt/box/Makefile."
echo "  3) Harden SSH (key-only, no root, no password) in /etc/ssh/sshd_config.d/ — test a 2nd session first."
echo "  4) Deploy app #1 (Review Lens) per deploy/MULTI_APP_HOST.md §5."

#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu VM on Oracle Cloud for GhostOps:
# opens ports 80/443 in the VM's firewall (Oracle's Ubuntu image rejects everything but SSH),
# and installs Docker (the patch sandbox), Caddy (automatic HTTPS), Node 22 (the website) and Python tooling.
#
# Usage (on the VM, from the repo root):  bash deploy/oracle/bootstrap.sh
# Safe to re-run. You also need ports 80 and 443 open in the VM's Oracle network security list.
set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }

say "Opening ports 80 and 443 in the VM firewall"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    # Insert before the catch-all REJECT rule, otherwise the new rule would never match.
    line=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
    if [ -n "$line" ]; then
      sudo iptables -I INPUT "$line" -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    else
      sudo iptables -A INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    fi
  fi
done
sudo netfilter-persistent save >/dev/null

say "Installing base packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ca-certificates curl gnupg git python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https >/dev/null

if ! command -v docker >/dev/null; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sudo sh >/dev/null
fi
sudo usermod -aG docker "$(id -un)"

# Containers default to Oracle's resolver (169.254.169.254), which is the cloud metadata address.
# Orbit IDE (and any hardening) blocks that address for containers, which would leave builds and
# user containers without DNS. Give Docker public resolvers instead, unless the host already configured some.
if ! sudo grep -qs '"dns"' /etc/docker/daemon.json; then
  if [ -s /etc/docker/daemon.json ]; then
    echo "   /etc/docker/daemon.json exists without a dns setting; add \"dns\": [\"1.1.1.1\", \"8.8.8.8\"] to it yourself"
  else
    echo '{"dns": ["1.1.1.1", "8.8.8.8"]}' | sudo tee /etc/docker/daemon.json >/dev/null
    sudo systemctl restart docker
  fi
fi

if ! command -v caddy >/dev/null; then
  say "Installing Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
fi
# Every project adds its site as /etc/caddy-sites/<name>.caddy
sudo mkdir -p /etc/caddy-sites
printf 'import /etc/caddy-sites/*.caddy\n' | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl enable --now caddy >/dev/null
sudo systemctl reload caddy

if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  say "Installing Node 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs >/dev/null
fi

say "Done"
echo "Docker $(sudo docker --version | cut -d' ' -f3 | tr -d ,), Caddy $(caddy version | cut -d' ' -f1), Node $(node --version)"
echo "Log out and back in once so the docker group applies, then run: bash deploy/oracle/setup.sh"

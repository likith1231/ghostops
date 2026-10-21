#!/usr/bin/env bash
# Deploys GhostOps to a single Oracle Cloud VM:
#   * k3s with the monitoring stack (Prometheus, Alertmanager, Grafana, Jaeger) and the sample app;
#   * the real backend (Claude agents) on port 8000, fed by Alertmanager and never public;
#   * a PUBLIC copy of the backend in mock mode (no Claude key, no GitHub token, no PRs) on 127.0.0.1:8001;
#   * the website on 127.0.0.1:3100;
#   * HTTPS for all of it through Caddy: ghostops.<ip>.sslip.io (site), api-ghostops.<ip>.sslip.io
#     (public mock API: GET plus /api/simulate and /api/detect only), grafana.<ip>.sslip.io.
#
# Usage (on the VM, from the repo root):
#   bash deploy/oracle/bootstrap.sh          # once: firewall, Docker, Caddy, Node
#   cp deploy/oracle/env.example .env && nano .env
#   bash deploy/oracle/setup.sh
# Re-run it any time to apply the latest code.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO_DIR=$(pwd)

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "Missing .env. Run: cp deploy/oracle/env.example .env && nano .env"
set -a; . ./.env; set +a
[ -n "${ANTHROPIC_API_KEY:-}" ] || die "Set ANTHROPIC_API_KEY in .env"
command -v docker >/dev/null || die "Docker is not installed (or log in again for the docker group). Run: bash deploy/oracle/bootstrap.sh"

# ---------------------------------------------------------------- k3s (shared by all projects)
ensure_k3s() {
  if ! command -v k3s >/dev/null; then
    say "Installing k3s (lightweight Kubernetes)"
    # Traefik is disabled: Caddy already owns ports 80/443 on this VM.
    curl -sfL https://get.k3s.io | sudo INSTALL_K3S_EXEC="--disable traefik" sh -
  fi
  # Oracle's Ubuntu image rejects all inbound traffic except SSH, which also blocks pods
  # talking to the node (kubelet, metrics-server, host services). Allow the pod/service CIDRs.
  sudo tee /etc/systemd/system/k3s-oracle-firewall.service >/dev/null <<'UNIT'
[Unit]
Description=Allow k3s pod and service networks through the Oracle iptables rules
After=network-online.target
Before=k3s.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'for net in 10.42.0.0/16 10.43.0.0/16; do \
  iptables -C INPUT -s $net -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -s $net -j ACCEPT; \
  iptables -C FORWARD -s $net -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -s $net -j ACCEPT; \
  iptables -C FORWARD -d $net -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -d $net -j ACCEPT; done'

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now k3s-oracle-firewall.service >/dev/null
  mkdir -p "$HOME/.kube"
  sudo cat /etc/rancher/k3s/k3s.yaml > "$HOME/.kube/config"
  chmod 600 "$HOME/.kube/config"
  export KUBECONFIG="$HOME/.kube/config"
  for _ in $(seq 1 60); do kubectl get nodes 2>/dev/null | grep -q ' Ready' && return; sleep 2; done
  die "k3s did not become ready. Check: sudo journalctl -u k3s -n 50"
}

base_domain() {
  if [ -n "${BASE_DOMAIN:-}" ]; then echo "$BASE_DOMAIN"; return; fi
  local ip
  ip=$(curl -fsS --max-time 5 https://api.ipify.org || curl -fsS --max-time 5 https://ifconfig.me)
  [ -n "$ip" ] || die "Could not detect the public IP. Set BASE_DOMAIN in .env"
  echo "${ip//./-}.sslip.io"
}

# Adds a site to the VM's Caddy and reloads it. Caddy is the system service installed by
# bootstrap.sh, or a container (the Orbit IDE stack); both read /etc/caddy-sites/*.caddy.
caddy_site() {
  local name=$1 body=$2
  sudo mkdir -p /etc/caddy-sites
  printf '%s\n' "$body" | sudo tee "/etc/caddy-sites/$name.caddy" >/dev/null
  local caddy
  caddy=$(docker ps --format '{{.Names}}' | grep -m1 caddy || true)
  if [ -n "$caddy" ]; then
    docker exec "$caddy" caddy reload --config /etc/caddy/Caddyfile >/dev/null \
      || echo "   (Caddy reload failed; check: docker logs $caddy)"
  elif systemctl is-active --quiet caddy; then
    sudo systemctl reload caddy || echo "   (Caddy reload failed; check: journalctl -u caddy -n 30)"
  else
    echo "   Caddy is not running. Run bash deploy/oracle/bootstrap.sh, then this script again."
  fi
}

ensure_k3s
DOMAIN_BASE=$(base_domain)
# Address pods use to reach services on this VM (the backend on :8000).
NODE_IP=$(ip route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')

say "Building the sample app image"
docker build -t ghostops-sample-app:v4 sample-app
docker save ghostops-sample-app:v4 | sudo k3s ctr images import - >/dev/null

say "Deploying the monitoring stack and sample app"
kubectl apply -f infra/k8s/monitoring/namespace.yaml
# The manifests target a kind cluster, where the host is the Docker gateway 172.18.0.1.
for f in infra/k8s/monitoring/*.yaml; do
  sed "s/172\.18\.0\.1/$NODE_IP/g" "$f" | kubectl apply -f -
done
kubectl apply -f infra/k8s/sample-app.yaml

# Grafana ships with anonymous *admin* access for local dev; make the public instance read-only.
GRAFANA_PASSWORD=$(sudo cat /var/lib/ghostops-grafana-password 2>/dev/null || true)
if [ -z "$GRAFANA_PASSWORD" ]; then
  GRAFANA_PASSWORD=$(openssl rand -hex 12)
  echo "$GRAFANA_PASSWORD" | sudo tee /var/lib/ghostops-grafana-password >/dev/null
  sudo chmod 600 /var/lib/ghostops-grafana-password
fi
kubectl -n monitoring set env deploy/grafana \
  GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer GF_SECURITY_ADMIN_PASSWORD="$GRAFANA_PASSWORD" >/dev/null
for d in prometheus alertmanager grafana jaeger; do
  kubectl -n monitoring rollout status "deploy/$d" --timeout=300s
done
kubectl rollout status deploy/sample-app --timeout=300s

say "Installing the backend (Python + CrewAI)"
if ! python3 -m venv --help >/dev/null 2>&1 || ! dpkg -s python3-venv >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip >/dev/null
fi
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r backend/requirements.txt -r agents/requirements.txt

# GhostOps opens fix PRs from this checkout: give git an identity and the token to push branches.
git config user.name "GhostOps"
git config user.email "ghostops@users.noreply.github.com"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  git config credential.helper "store --file=$HOME/.ghostops-git-credentials"
  printf 'https://x-access-token:%s@github.com\n' "$GITHUB_TOKEN" > "$HOME/.ghostops-git-credentials"
  chmod 600 "$HOME/.ghostops-git-credentials"
fi

JAEGER_IP=$(kubectl -n monitoring get svc jaeger -o jsonpath='{.spec.clusterIP}')
sudo mkdir -p /etc/ghostops
sudo tee /etc/ghostops/runtime.env >/dev/null <<EOF
KUBECONFIG=$HOME/.kube/config
PROMETHEUS_URL=http://127.0.0.1:30090
OTEL_EXPORTER_OTLP_ENDPOINT=$JAEGER_IP:4317
VAULT_ADDR=http://127.0.0.1:8200
EOF

sudo tee /etc/systemd/system/ghostops-backend.service >/dev/null <<EOF
[Unit]
Description=GhostOps backend (Alertmanager webhook + AI agent pipeline)
After=network-online.target k3s.service docker.service

[Service]
User=$(id -un)
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
EnvironmentFile=/etc/ghostops/runtime.env
ExecStart=$REPO_DIR/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable ghostops-backend >/dev/null
sudo systemctl restart ghostops-backend
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/health >/dev/null || die "Backend did not start. Check: journalctl -u ghostops-backend -n 80"

say "Starting the public mock-mode API (no secrets) and the website"
# The public API must never hold your keys: the variables below are set empty, and the app only fills
# a variable from .env when it is not already set. Mock mode makes no Claude call and opens no PR.
PUBLIC_DB_DIR=/var/lib/ghostops-public
sudo mkdir -p "$PUBLIC_DB_DIR" && sudo chown "$(id -un)" "$PUBLIC_DB_DIR"
sudo tee /etc/systemd/system/ghostops-public-api.service >/dev/null <<EOF
[Unit]
Description=GhostOps public API (mock mode: no Claude key, no GitHub token, no PRs)
After=network-online.target docker.service

[Service]
User=$(id -un)
WorkingDirectory=$REPO_DIR
Environment=GHOSTOPS_MOCK_LLM=1
Environment=GHOSTOPS_DB_PATH=$PUBLIC_DB_DIR/incidents.db
Environment=GHOSTOPS_CORS_ORIGINS=https://ghostops.$DOMAIN_BASE
Environment=ANTHROPIC_API_KEY=
Environment=GOOGLE_API_KEY=
Environment=GITHUB_TOKEN=
Environment=OTEL_SDK_DISABLED=true
ExecStart=$REPO_DIR/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

command -v node >/dev/null || die "Node is not installed. Run: bash deploy/oracle/bootstrap.sh"
(cd web && npm ci --no-audit --no-fund --silent \
  && NEXT_PUBLIC_GHOSTOPS_API="https://api-ghostops.$DOMAIN_BASE" npm run build >/dev/null)
sudo tee /etc/systemd/system/ghostops-web.service >/dev/null <<EOF
[Unit]
Description=GhostOps website
After=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$REPO_DIR/web
Environment=NODE_ENV=production
ExecStart=$REPO_DIR/web/node_modules/.bin/next start -p 3100 -H 127.0.0.1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable ghostops-public-api ghostops-web >/dev/null
sudo systemctl restart ghostops-public-api ghostops-web
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8001/health >/dev/null || die "Public API did not start. Check: journalctl -u ghostops-public-api -n 80"
for _ in $(seq 1 30); do curl -fsS -o /dev/null http://127.0.0.1:3100 2>/dev/null && break; sleep 2; done
curl -fsS -o /dev/null http://127.0.0.1:3100 || die "Website did not start. Check: journalctl -u ghostops-web -n 80"

say "Publishing the website, the public API and Grafana over HTTPS"
caddy_site ghostops "grafana.$DOMAIN_BASE {
	encode zstd gzip
	reverse_proxy 127.0.0.1:30030
}

ghostops.$DOMAIN_BASE {
	encode zstd gzip
	reverse_proxy 127.0.0.1:3100
}

api-ghostops.$DOMAIN_BASE {
	# Public and read-mostly: anything that changes state is refused, except starting a mock-mode
	# simulation and running the detectors (no Claude calls, no PRs). Alerts and metrics stay internal.
	@internal path /metrics /webhook/*
	respond @internal 404
	@unsafe {
		not method GET HEAD OPTIONS
		not path /api/simulate /api/detect
	}
	respond @unsafe 403
	reverse_proxy 127.0.0.1:8001
}"

cat <<EOF

GhostOps is running:
  Website (share this): https://ghostops.$DOMAIN_BASE
  Public mock API:      https://api-ghostops.$DOMAIN_BASE/docs
  Grafana dashboards:   https://grafana.$DOMAIN_BASE   (admin / $GRAFANA_PASSWORD)
  (the first visit can take ~20 s while the HTTPS certificates are issued)

The real, Claude-powered backend listens on port 8000, which is closed to the internet. To use it
from your laptop: ssh -L 8000:127.0.0.1:8000 -i ~/.ssh/ghostops_oracle ubuntu@<this VM>

Trigger a demo incident (memory leak in the sample app):
  kubectl port-forward deploy/sample-app 5000:5000 &
  for i in \$(seq 1 10); do curl -s localhost:5000/leak >/dev/null; done

Useful commands:
  journalctl -u ghostops-backend -f       # watch the real agents work
  journalctl -u ghostops-public-api -f    # the public mock API
  systemctl status ghostops-web
  kubectl -n monitoring get pods          # monitoring stack
  bash deploy/oracle/setup.sh             # redeploy after a git pull
EOF

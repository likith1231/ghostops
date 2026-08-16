# GhostOps — Autonomous AIOps Platform

> **An AI-powered incident response pipeline that detects, diagnoses, patches, validates, and deploys fixes — autonomously.**

GhostOps is a solo portfolio project that demonstrates a full AIOps loop: a Kubernetes-native observability stack detects failures, a multi-agent AI pipeline diagnoses the root cause and generates a fix, a Docker sandbox validates the patch, and on success, a GitHub PR is auto-created and ArgoCD syncs the cluster to the merged state. No human intervention required from alert to fix.

&lt;!-- TODO: Add demo GIF/video showing the full loop in action --&gt;
&lt;!-- ![GhostOps Demo](docs/demo.gif) --&gt;

---

## Architecture

```
                          Kind Kubernetes Cluster
 +--------------+     +------------------+                    +--------+
 |  sample-app  |---->|   Prometheus     |                    | ArgoCD |
 |  (Flask +    |     |   (scrapes       |                    | (syncs |
 |   /metrics)  |     |    /metrics)     |                    | merged |
 +--------------+     +--------+---------+                    |  PRs)  |
                               | alert fires                  +--------+
                               v
                      +------------------+
                      |   Alertmanager   |
                      |   (routes to     |
                      |    webhook)      |
                      +--------+---------+
                               |
 +--------------+              | POST /webhook/alert
 |   Grafana    |              v
 |  (dashboard) |<---+--------------------------------------------+
 +--------------+    |         FastAPI Backend (host)               |
                     |                                              |
                     |  1. Parse Alertmanager payload               |
                     |  2. Enrich: pod logs + Prometheus metrics    |
                     |  3. Load source files                        |
                     |  4. Run CrewAI pipeline:                     |
                     |                                              |
                     |     +-------------------------------+        |
                     |     | Agent 1: Diagnostic Reasoner  |        |
                     |     | (root cause + confidence)     |        |
                     |     +---------------+---------------+        |
                     |                     v                        |
                     |     +-------------------------------+        |
                     |     | Agent 2: Patch Generator      |        |
                     |     | (unified diff + explanation)  |        |
                     |     +---------------+---------------+        |
                     |                     v                        |
                     |     +-------------------------------+        |
                     |     | Agent 3: Validation Officer   |        |
                     |     | (Docker sandbox + pytest)     |        |
                     |     +---------------+---------------+        |
                     |                     v                        |
                     |     +-------------------------------+        |
                     |     | Deployer: GitHub PR creation  |        |
                     |     | (branch > commit > push > PR) |        |
                     |     +-------------------------------+        |
                     |                                              |
                     |  5. Log incident to SQLite knowledge base    |
                     +----------------------------------------------+
```

&lt;!-- TODO: Replace ASCII diagram with a polished architecture diagram image --&gt;
&lt;!-- ![Architecture Diagram](docs/architecture.png) --&gt;

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Orchestration** | Kind (Kubernetes in Docker) | Local single-node K8s cluster |
| **Observability** | Prometheus + Alertmanager | Metrics scraping, alerting (plain YAML manifests, no Operator) |
| **Dashboards** | Grafana + JSON API plugin | Incident metrics dashboard backed by FastAPI endpoints |
| **GitOps** | ArgoCD | Auto-sync cluster state from merged PRs on `main` |
| **Backend** | FastAPI (Python 3.11+) | Webhook ingestion, log/metric enrichment, incident API |
| **AI Agents** | CrewAI | 3-agent sequential pipeline (Diagnostic Reasoner, Patch Generator, Validation Officer) |
| **LLM** | Gemini 1.5 Flash (primary), Claude 3.5 Sonnet (secondary) | Reasoning, code generation, validation judgment |
| **Sandbox** | Docker | Isolated patch validation with pytest |
| **Deployment** | GitHub REST API | Automated PR creation with branch/commit/push |
| **Knowledge Base** | SQLite | Logs every pipeline run for incident history and future context |
| **Sample App** | Flask + prometheus_client | Intentionally leaky app for demo/testing |

---

## Project Structure

```
ghostops/
├── agents/                          # CrewAI multi-agent pipeline
│   ├── config.py                    # Environment config (API keys, model selection)
│   ├── crew.py                      # Pipeline orchestrator (run_pipeline entry point)
│   ├── diagnostic_reasoner.py       # Agent 1: root cause analysis
│   ├── patch_generator.py           # Agent 2: unified diff generation
│   ├── validation_officer.py        # Agent 3: Docker sandbox + pytest
│   ├── deployer.py                  # GitHub PR automation (branch, commit, push, PR)
│   ├── knowledge_base.py            # SQLite incident logger
│   ├── requirements.txt
│   └── tests/
│       ├── test_deployer.py         # Safety gate tests (5 tests)
│       └── test_knowledge_base.py   # DB + logging tests (9 tests)
│
├── backend/                         # FastAPI ingestion service
│   ├── main.py                      # Webhook endpoint + incident API
│   └── requirements.txt
│
├── sample-app/                      # Demo Flask app (intentional memory leak)
│   ├── app.py                       # Flask app with /metrics, /leak, /health
│   ├── Dockerfile
│   └── requirements.txt
│
├── infra/
│   └── k8s/
│       ├── sample-app.yaml          # Deployment + Service for the demo app
│       ├── argocd/
│       │   └── ghostops-app.yaml    # ArgoCD Application (auto-sync from main)
│       └── monitoring/
│           ├── namespace.yaml
│           ├── prometheus-rbac.yaml
│           ├── prometheus-config.yaml    # Scrape configs + alert rules
│           ├── prometheus-deployment.yaml
│           ├── alertmanager-config.yaml  # Webhook route to FastAPI
│           ├── alertmanager-deployment.yaml
│           ├── grafana-deployment.yaml
│           ├── grafana-config.yaml       # Provisioned datasources
│           ├── grafana-dashboard.yaml    # Incident dashboard (7 panels)
│           └── README.md
│
├── .env                             # API keys (not committed)
├── .gitignore
└── README.md
```

---

## Setup Instructions

### Prerequisites

- **Docker** (for Kind, sandbox validation, and sample-app image builds)
- **Kind** (`go install sigs.k8s.io/kind@latest` or `brew install kind`)
- **kubectl** (compatible with your Kind cluster version)
- **Python 3.11+** with `pip`
- **API Keys** (see Configuration below)

### 1. Create the Kind Cluster

```bash
kind create cluster --name ghostops
```

**Important: fix inotify limits** (required for kube-proxy stability on Linux):

```bash
# Apply immediately
sudo sysctl fs.inotify.max_user_watches=1048576
sudo sysctl fs.inotify.max_user_instances=8192

# Persist across reboots
printf 'fs.inotify.max_user_watches=1048576\nfs.inotify.max_user_instances=8192\n' \
  | sudo tee /etc/sysctl.d/99-kind.conf
```

### 2. Deploy the Monitoring Stack

```bash
# Create namespace + RBAC
kubectl apply -f infra/k8s/monitoring/namespace.yaml
kubectl apply -f infra/k8s/monitoring/prometheus-rbac.yaml

# Prometheus (config + deployment)
kubectl apply -f infra/k8s/monitoring/prometheus-config.yaml
kubectl apply -f infra/k8s/monitoring/prometheus-deployment.yaml

# Alertmanager
kubectl apply -f infra/k8s/monitoring/alertmanager-config.yaml
kubectl apply -f infra/k8s/monitoring/alertmanager-deployment.yaml

# Grafana
kubectl apply -f infra/k8s/monitoring/grafana-config.yaml
kubectl apply -f infra/k8s/monitoring/grafana-dashboard.yaml
kubectl apply -f infra/k8s/monitoring/grafana-deployment.yaml

# Verify all pods are Running
kubectl get pods -n monitoring
```

### 3. Deploy the Sample App

```bash
# Build the Docker image and load into Kind
docker build -t ghostops-sample-app:v2 sample-app/
kind load docker-image ghostops-sample-app:v2 --name ghostops

# Deploy
kubectl apply -f infra/k8s/sample-app.yaml
```

### 4. Install ArgoCD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl apply -f infra/k8s/argocd/ghostops-app.yaml
```

### 5. Configuration

Create a `.env` file in the project root:

```env
# LLM: at least one required
GOOGLE_API_KEY=your-gemini-api-key        # Primary (Gemini 1.5 Flash)
ANTHROPIC_API_KEY=your-anthropic-key      # Secondary (Claude 3.5 Sonnet)

# GitHub: required for automated PR creation
GITHUB_TOKEN=your-github-pat
GITHUB_REPO=likith1231/ghostops
```

### 6. Install Python Dependencies

```bash
pip install -r agents/requirements.txt
pip install -r backend/requirements.txt
```

### 7. Start the Backend

```bash
cd ~/ghostops
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 8. Set Up Port Forwards (for local access)

```bash
# Prometheus UI
kubectl port-forward -n monitoring svc/prometheus 9090:9090

# Alertmanager UI
kubectl port-forward -n monitoring svc/alertmanager 9093:9093

# Grafana dashboards
kubectl port-forward -n monitoring svc/grafana 3000:3000

# ArgoCD UI
kubectl port-forward svc/argocd-server -n argocd 8080:443

# Sample app (for manual leak triggering)
kubectl port-forward svc/sample-app 5000:5000
```

### 9. Trigger a Test Incident

```bash
# Hit the /leak endpoint 10 times to trigger OOM alert
for i in $(seq 1 10); do curl -s http://localhost:5000/leak; done

# Wait 1-2 minutes for Prometheus to scrape, alert to fire, and
# Alertmanager to POST the webhook to FastAPI.
# Watch the FastAPI terminal for pipeline execution logs.
```

---

## Current Status

### Completed Phases

| Phase | Component | Status |
|-------|-----------|--------|
| **1** | CrewAI 3-Agent Pipeline | Done: Diagnostic Reasoner, Patch Generator, Validation Officer |
| **2** | Kubernetes Monitoring | Done: Prometheus + Alertmanager (plain YAML, no Operator) |
| **3** | FastAPI Webhook Backend | Done: Alertmanager webhook, enrichment, pipeline execution |
| **4** | Docker Sandbox Validation | Done: Isolated pytest runs in throwaway containers |
| **5** | Automated GitHub PRs | Done: Branch, commit, push, PR with diagnostic context |
| **6** | ArgoCD GitOps | Done: Auto-sync cluster from merged PRs on main |
| **7** | SQLite Knowledge Base | Done: Every pipeline run logged with full context |
| **8** | Grafana Dashboard | Done: 7-panel incident dashboard with JSON API datasource |
| **9** | Prometheus /metrics | Done: Sample app exposes custom gauges via prometheus_client |
| **10** | OPA Gatekeeper Policy Enforcement | Done: Evaluates AI patches against constraints via conftest |
| **13** | Infrastructure as Code | Done: Terraform modules replicating infra/k8s/ manifests |

### In Progress / Planned

| Phase | Component | Status |
|-------|-----------|--------|
| **11** | Past incident context injection | Planned: Wire get_past_incidents() into failure_context |
| **12** | Multi-service support | Planned: Extend beyond single sample-app |
| **14** | Demo video / architecture diagram | Planned: Record end-to-end walkthrough |

---

## Running Tests

```bash
cd ~/ghostops

# All tests
python -m pytest agents/tests/ -v

# Individual test files
python -m pytest agents/tests/test_deployer.py -v
python -m pytest agents/tests/test_knowledge_base.py -v
python -m pytest agents/tests/test_policy_enforcement.py -v
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook/alert` | Receives Alertmanager webhook payloads |
| `GET` | `/api/incidents/summary` | Aggregate stats (total, pass rate, PR count) |
| `GET` | `/api/incidents/recent?limit=20` | Recent incidents table |
| `GET` | `/api/incidents/timeseries` | Daily pass/fail counts |
| `GET` | `/health` | Backend health check |

---

## Known Limitations

- **local-path-provisioner CrashLoopBackOff**: This Kind system component occasionally crash-loops due to Linux inotify limits. Does not affect GhostOps functionality. Fix: ensure `/etc/sysctl.d/99-kind.conf` has both `max_user_watches` and `max_user_instances` set, then run `sudo sysctl --system`.

- **Gemini free-tier quota constraints**: The pipeline uses Gemini 1.5 Flash which has rate limits on the free tier. If you hit quota errors during rapid testing, wait 60 seconds between runs or switch to a paid API key.

- **No hot-reload for Prometheus config**: Plain ConfigMap mounts do not auto-reload. After editing `prometheus-config.yaml`, you must `kubectl apply` the ConfigMap then `kubectl rollout restart deployment/prometheus -n monitoring`.

- **ArgoCD applicationsets CRD warning**: The ApplicationSets CRD is too large for Kind's annotation limits. This is cosmetic and does not affect functionality. ApplicationSets is an optional feature not used by GhostOps.

- **Single-app scope**: Currently only handles the sample-app Flask service. The pipeline architecture supports multi-service, but alert routing and source file loading are hardcoded to one app.

- **LLM provider switching**: The system is designed for Claude 3.5 Sonnet as primary and Gemini as fallback, but currently runs on Gemini only (no Anthropic billing configured). Switching is a one-line change in `agents/config.py`.

---

## Supported Alert Types

| Alert Type | Scenario | Detection |
|------------|----------|-----------|
| `memory_high` (B1) | Flask app leaking memory, OOM kill | Prometheus: `container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.8` |
| `ci_failure` (C4) | GitHub Actions test failure | Webhook payload with test logs |

---

## License

This is a personal portfolio project. Not currently licensed for redistribution.

---

Built with coffee and too many `kubectl get pods` commands.

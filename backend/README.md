# GhostOps Backend — FastAPI Ingestion Service

Receives Alertmanager webhook payloads, enriches them with live data from the
Kubernetes cluster and Prometheus, then runs the full CrewAI agent pipeline
(Diagnostic Reasoner → Patch Generator → Validation Officer).

## Quick Start

### 1. Install dependencies

```bash
pip install -r backend/requirements.txt
pip install -r agents/requirements.txt   # if not already installed
```

### 2. Run the server

From the **project root** (`~/ghostops`):

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

> **Note:** Use `--host 0.0.0.0` (not the default `127.0.0.1`) so the server
> listens on all interfaces. This is required for the kind cluster to reach it
> — see the networking section below.

### 3. Verify it's running

```bash
curl http://localhost:8000/health
# → {"status":"ok","service":"ghostops-backend"}
```

---

## Wiring Alertmanager to this webhook

### The networking problem

Your Alertmanager pod runs **inside a kind cluster**, which itself runs inside
a Docker container. When Alertmanager sends a webhook to `http://localhost:8000`,
that `localhost` resolves to the pod's own loopback — **not** your host machine
where the FastAPI server is running.

```
┌─ Host machine ───────────────────────────────────┐
│  FastAPI server on 0.0.0.0:8000                  │
│                                                  │
│  ┌─ Docker container (kind node) ──────────────┐ │
│  │  ┌─ Pod: alertmanager ────────────────────┐ │ │
│  │  │  localhost:8000 → ❌ (pod loopback)    │ │ │
│  │  │  172.18.0.1:8000 → ✅ (Docker gateway) │ │ │
│  │  └────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

### Solution: use the Docker gateway IP

The kind node is connected to the Docker bridge network. The gateway IP of
that network (typically `172.18.0.1`) routes to the host machine.

Find your gateway IP:

```bash
docker network inspect kind --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'
# → 172.18.0.1
```

### Update the Alertmanager config

Edit `infra/k8s/monitoring/alertmanager-config.yaml` and replace the `"null"`
receiver with a real webhook receiver pointing to the host via the gateway IP:

```yaml
    route:
      receiver: "ghostops-webhook"
      group_by: ["alertname", "severity"]
      group_wait: 10s
      group_interval: 5m
      repeat_interval: 1h

    receivers:
      - name: "ghostops-webhook"
        webhook_configs:
          - url: "http://172.18.0.1:8000/webhook/alert"
            send_resolved: true
```

Then apply and restart:

```bash
kubectl apply -f infra/k8s/monitoring/alertmanager-config.yaml
kubectl rollout restart deployment alertmanager -n monitoring
```

---

## Testing manually with curl

You can simulate an Alertmanager webhook without waiting for a real alert:

```bash
curl -X POST http://localhost:8000/webhook/alert \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "status": "firing",
    "alerts": [
      {
        "status": "firing",
        "labels": {
          "alertname": "SampleAppHighMemory",
          "pod": "sample-app-YOUR-POD-NAME-HERE",
          "severity": "critical",
          "component": "sample-app"
        },
        "annotations": {
          "summary": "sample-app memory usage above 80% of limit",
          "description": "Pod sample-app-xxx is at risk of OOMKill"
        },
        "startsAt": "2026-08-08T04:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z"
      }
    ]
  }'
```

> **Tip:** Replace `sample-app-YOUR-POD-NAME-HERE` with an actual pod name:
>
> ```bash
> kubectl get pods -l app=sample-app -o name | head -1 | sed 's|pod/||'
> ```

### Testing with a non-actionable alert (should be ignored)

```bash
curl -X POST http://localhost:8000/webhook/alert \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "status": "resolved",
    "alerts": [
      {
        "status": "resolved",
        "labels": {"alertname": "SampleAppHighMemory", "pod": "sample-app-xxx"},
        "annotations": {},
        "startsAt": "2026-08-08T04:00:00Z"
      }
    ]
  }'
```

Expected response: `{"received": true, "alert": "SampleAppHighMemory", "status": "resolved", "action": "ignored"}`

---

## Configuration

All settings can be overridden via environment variables or `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus HTTP API base URL |
| `SAMPLE_APP_SOURCE` | `~/ghostops/sample-app/app.py` | Path to the source file included in `failure_context` |
| `POD_LOG_LINES` | `20` | Number of tail log lines to fetch from the pod |
| `POD_NAMESPACE` | `default` | Namespace where the sample-app pod runs |

## Architecture

```
Alertmanager (pod in kind)
    │
    │  POST /webhook/alert  (via 172.18.0.1:8000)
    ▼
┌─────────────────────────────────┐
│  FastAPI Ingestion Service      │
│  (runs on host, port 8000)     │
│                                 │
│  1. Parse Alertmanager payload  │
│  2. Fetch pod logs (k8s API)   │
│  3. Query Prometheus metrics   │
│  4. Read source file from disk │
│  5. Build failure_context      │
│  6. Call run_pipeline()        │
│  7. Log + return result        │
└─────────────────────────────────┘
    │
    ▼
CrewAI Agent Pipeline
  → Diagnostic Reasoner
  → Patch Generator
  → Validation Officer (Docker sandbox)
```

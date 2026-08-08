# GhostOps — Monitoring Stack (Plain Manifests)

## Why plain manifests instead of kube-prometheus-stack?

The `kube-prometheus-stack` Helm chart deploys the **Prometheus Operator**,
CRDs, admission webhooks, and dozens of sub-components. On a local **kind**
cluster with limited resources, this stack consistently crash-looped on
liveness/readiness probes — the Operator's startup machinery is simply too
heavy for a single-node dev environment.

This directory replaces all of that with **two plain Deployments** (Prometheus
+ Alertmanager) applied directly with `kubectl`. No Operator, no CRDs, no
admission webhooks, no Helm dependency.

## Files

| File | What it creates |
|------|-----------------|
| `namespace.yaml` | `monitoring` namespace |
| `prometheus-rbac.yaml` | ServiceAccount, ClusterRole, ClusterRoleBinding — minimum RBAC for k8s service discovery and kubelet scraping |
| `prometheus-config.yaml` | Two ConfigMaps: `prometheus-config` (scrape jobs + alerting config) and `prometheus-rules` (SampleAppHighMemory alert) |
| `prometheus-deployment.yaml` | Prometheus Deployment (1 replica, `prom/prometheus:v2.53.0`) + NodePort Service on 30090 |
| `alertmanager-config.yaml` | Alertmanager ConfigMap with a null receiver (placeholder) |
| `alertmanager-deployment.yaml` | Alertmanager Deployment (1 replica, `prom/alertmanager:v0.27.0`) + NodePort Service on 30080 |

## Apply everything

```bash
# From the project root:
kubectl apply -f infra/k8s/monitoring/
```

Order doesn't matter — `kubectl apply -f <directory>` processes all YAML files
in the directory and Kubernetes resolves dependencies internally.

## Verify

```bash
# 1. Check pods are Running (both should be 1/1 within ~60 s)
kubectl get pods -n monitoring

# 2. Check services have the right NodePorts
kubectl get svc -n monitoring

# 3. Open Prometheus in the browser
#    (kind maps NodePort 30090 → localhost:9090)
open http://localhost:9090

# 4. Check scrape targets are UP
#    Navigate to: Status → Targets  (or http://localhost:9090/targets)

# 5. Check the alert rule is loaded
#    Navigate to: Alerts  (or http://localhost:9090/alerts)
#    You should see "SampleAppHighMemory" in inactive state.

# 6. Open Alertmanager
#    (kind maps NodePort 30080 → localhost:8080)
open http://localhost:8080
```

## Port mapping reference

| Service | Container Port | NodePort | Host Port (via kind) |
|---------|---------------|----------|----------------------|
| Prometheus | 9090 | 30090 | 9090 |
| Alertmanager | 9093 | 30080 | 8080 |

These NodePorts match the `extraPortMappings` in `infra/kind/kind-config.yaml`.

## Known issue: inotify limits on kind

If kube-proxy crash-loops with `too many open files` after tearing down a
heavy workload (like `kube-prometheus-stack`), increase the inotify limits on
the kind node:

```bash
docker exec ghostops-control-plane sysctl -w fs.inotify.max_user_watches=524288
docker exec ghostops-control-plane sysctl -w fs.inotify.max_user_instances=512
kubectl rollout restart daemonset kube-proxy -n kube-system
```

These settings do not persist across kind cluster recreation. To make them
permanent on the host, add them to `/etc/sysctl.conf`:

```
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 512
```

## Next steps

- Deploy the sample-app with a `/metrics` endpoint so the `sample-app` scrape
  job goes from "0/0 up" to "1/1 up".
- Wire Alertmanager to the FastAPI webhook by adding a `webhook_configs` entry
  in `alertmanager-config.yaml`.

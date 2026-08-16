# GhostOps — Terraform Infrastructure as Code

> **Portfolio demonstration of IaC capability.** This Terraform configuration
> manages the **same** Kubernetes resources that `infra/k8s/` YAML manifests
> define, but declaratively through `terraform apply` instead of raw
> `kubectl apply`.

---

## ⚠️ Relationship to ArgoCD / `infra/k8s/`

| Mechanism | Purpose | Source of truth? |
|-----------|---------|:----------------:|
| `infra/k8s/` + `kubectl apply` | The original deployment path. ArgoCD watches this directory and auto-syncs the cluster on every merge to `main`. | **Yes** — for the live GitOps loop |
| `infra/terraform/` + `terraform apply` | An **additive, optional** IaC layer that demonstrates Terraform proficiency. Deploys identical resources using the `hashicorp/kubernetes` and `hashicorp/helm` providers. | **No** — portfolio demo only |

> **Do NOT run `terraform apply` while ArgoCD auto-sync is active against the
> same resources.** The two controllers will fight over resource ownership,
> causing drift loops. To safely use Terraform, first disable ArgoCD auto-sync
> or scale down the ArgoCD Application controller.

---

## What Terraform Manages

| Module | Resources | Count |
|--------|-----------|:-----:|
| `modules/monitoring` | Namespace, Prometheus (RBAC + ConfigMaps + Deployment + Service), Alertmanager (ConfigMap + Deployment + Service), Grafana (3× ConfigMap + Deployment + Service) | 16 |
| `modules/sample-app` | Deployment + Service for the Flask demo app | 2 |
| **Total** | | **18** |

---

## Prerequisites

- **Terraform >= 1.5.0** ([install guide](https://developer.hashicorp.com/terraform/install))
- **Kind cluster** named `ghostops` already running (`kind create cluster --name ghostops`)
- **Docker image** `ghostops-sample-app:v2` loaded into Kind (`kind load docker-image ghostops-sample-app:v2 --name ghostops`)
- **kubeconfig** at `~/.kube/config` with context `kind-ghostops`

---

## Quick Start

```bash
cd infra/terraform

# 1. Initialise providers (downloads hashicorp/kubernetes + hashicorp/helm)
terraform init

# 2. Review the plan — see exactly what would be created
terraform plan

# 3. Apply (only after reviewing the plan output!)
terraform apply
```

### Existing Cluster

On a cluster where the infrastructure is already running (e.g., your current setup), a single `terraform plan` is sufficient to see the differences.

---

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `kubeconfig_path` | `~/.kube/config` | Path to kubeconfig |
| `kubeconfig_context` | `kind-ghostops` | Kubernetes context |
| `sample_app_image` | `ghostops-sample-app:v2` | Sample app Docker image |
| `docker_bridge_gateway` | `172.18.0.1` | Docker bridge IP for host access from Kind |

Override via `terraform.tfvars` (copy from `terraform.tfvars.example`).

---

## Directory Structure

```
infra/terraform/
├── main.tf                          # Root — provider config + module calls
├── variables.tf                     # Input variables
├── outputs.tf                       # Key outputs from all modules
├── versions.tf                      # Provider + Terraform version pins
├── terraform.tfvars.example         # Example variable values
├── .gitignore                       # Ignores .terraform/, *.tfstate, etc.
├── README.md                        # This file
│
└── modules/
    ├── monitoring/                  # Prometheus + Alertmanager + Grafana
    │   ├── main.tf                  # 16 resources
    │   ├── variables.tf             # Image versions, Docker gateway IP
    │   ├── outputs.tf
    │   └── files/
    │       └── grafana-dashboard.json   # 7-panel dashboard (extracted from YAML)
    │
    ├── sample-app/                  # Flask demo app
    │   ├── main.tf                  # 2 resources
    │   ├── variables.tf
    │   └── outputs.tf
```

---

## No Cloud Dependencies

This Terraform configuration uses **only** the `hashicorp/kubernetes` and
`hashicorp/helm` providers, pointed at the local Kind cluster. There are:

- No AWS, GCP, or Azure provider blocks
- No cloud resources or billing
- No remote state backend (local state only)

The entire setup runs on your laptop.

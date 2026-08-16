# ============================================================================
# GhostOps — Terraform Root Module
# ============================================================================
# Manages the same Kubernetes infrastructure that infra/k8s/ YAML manifests
# define, but declaratively through Terraform.  This is an ADDITIVE portfolio
# demonstration of IaC — the existing kubectl / ArgoCD workflow is unchanged.
#
# Providers target the local Kind cluster only.  No cloud dependencies.
# ============================================================================

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kubeconfig_context
}

provider "helm" {
  kubernetes {
    config_path    = var.kubeconfig_path
    config_context = var.kubeconfig_context
  }
}

# ---------------------------------------------------------------------------
# Module: Monitoring Stack (Prometheus + Alertmanager + Grafana)
# ---------------------------------------------------------------------------
module "monitoring" {
  source = "./modules/monitoring"

  docker_bridge_gateway = var.docker_bridge_gateway
}

# ---------------------------------------------------------------------------
# Module: Sample App (Flask demo with intentional memory leak)
# ---------------------------------------------------------------------------
module "sample_app" {
  source = "./modules/sample-app"

  app_image = var.sample_app_image
}



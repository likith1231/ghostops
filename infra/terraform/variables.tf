# ============================================================================
# GhostOps — Root Input Variables
# ============================================================================

variable "kubeconfig_path" {
  description = "Path to the kubeconfig file for the Kind cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kubeconfig_context" {
  description = "Kubernetes context name for the Kind cluster."
  type        = string
  default     = "kind-ghostops"
}

variable "sample_app_image" {
  description = "Docker image for the sample-app (pre-loaded into Kind via 'kind load')."
  type        = string
  default     = "ghostops-sample-app:v2"
}

variable "docker_bridge_gateway" {
  description = "Docker bridge gateway IP for reaching the host from inside Kind. Verify with: docker network inspect kind --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'"
  type        = string
  default     = "172.18.0.1"
}

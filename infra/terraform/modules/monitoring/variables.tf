# ============================================================================
# Monitoring Module — Input Variables
# ============================================================================

variable "docker_bridge_gateway" {
  description = "Docker bridge gateway IP for host access from within Kind."
  type        = string
  default     = "172.18.0.1"
}

variable "prometheus_image" {
  description = "Prometheus container image."
  type        = string
  default     = "prom/prometheus:v2.53.0"
}

variable "alertmanager_image" {
  description = "Alertmanager container image."
  type        = string
  default     = "prom/alertmanager:v0.27.0"
}

variable "grafana_image" {
  description = "Grafana container image."
  type        = string
  default     = "grafana/grafana:11.1.0"
}

# ============================================================================
# Monitoring Module — Outputs
# ============================================================================

output "namespace_name" {
  description = "Name of the monitoring namespace."
  value       = kubernetes_namespace_v1.monitoring.metadata[0].name
}

output "prometheus_service" {
  description = "Prometheus service name and NodePort."
  value = {
    name      = kubernetes_service_v1.prometheus.metadata[0].name
    node_port = 30090
  }
}

output "alertmanager_service" {
  description = "Alertmanager service name and NodePort."
  value = {
    name      = kubernetes_service_v1.alertmanager.metadata[0].name
    node_port = 30080
  }
}

output "grafana_service" {
  description = "Grafana service name."
  value       = kubernetes_service_v1.grafana.metadata[0].name
}

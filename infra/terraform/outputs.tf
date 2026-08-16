# ============================================================================
# GhostOps — Root Outputs
# ============================================================================

output "monitoring_namespace" {
  description = "Name of the monitoring namespace."
  value       = module.monitoring.namespace_name
}

output "prometheus_service" {
  description = "Prometheus service name and NodePort."
  value       = module.monitoring.prometheus_service
}

output "alertmanager_service" {
  description = "Alertmanager service name and NodePort."
  value       = module.monitoring.alertmanager_service
}

output "grafana_service" {
  description = "Grafana service name."
  value       = module.monitoring.grafana_service
}

output "sample_app_service" {
  description = "Sample-app service name."
  value       = module.sample_app.service_name
}



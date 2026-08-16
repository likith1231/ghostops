# ============================================================================
# Sample App Module — Outputs
# ============================================================================

output "service_name" {
  description = "Name of the sample-app Service."
  value       = kubernetes_service_v1.sample_app.metadata[0].name
}

# ============================================================================
# Sample App Module — Input Variables
# ============================================================================

variable "app_image" {
  description = "Docker image for the sample-app (pre-loaded into Kind)."
  type        = string
  default     = "ghostops-sample-app:v2"
}

variable "memory_request" {
  description = "Memory request for the sample-app container."
  type        = string
  default     = "64Mi"
}

variable "memory_limit" {
  description = "Memory limit for the sample-app container."
  type        = string
  default     = "128Mi"
}

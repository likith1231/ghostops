# ============================================================================
# GhostOps — Sample App Module
# ============================================================================
# Deploys the Flask demo app (intentional memory leak) into the default
# namespace.  Mirrors infra/k8s/sample-app.yaml exactly.
#
# The image is pre-built and loaded into Kind via:
#   docker build -t ghostops-sample-app:v2 sample-app/
#   kind load docker-image ghostops-sample-app:v2 --name ghostops
# ============================================================================

resource "kubernetes_deployment_v1" "sample_app" {
  metadata {
    name      = "sample-app"
    namespace = "default"
    labels = {
      app = "sample-app"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "sample-app"
      }
    }

    template {
      metadata {
        labels = {
          app = "sample-app"
        }
      }

      spec {
        container {
          name              = "sample-app"
          image             = var.app_image
          image_pull_policy = "Never"

          resources {
            requests = {
              memory = var.memory_request
            }
            limits = {
              memory = var.memory_limit
            }
          }

          port {
            container_port = 5000
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "sample_app" {
  metadata {
    name      = "sample-app"
    namespace = "default"
  }

  spec {
    type = "ClusterIP"

    selector = {
      app = "sample-app"
    }

    port {
      port        = 5000
      target_port = "5000"
    }
  }
}

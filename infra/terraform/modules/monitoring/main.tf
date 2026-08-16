# ============================================================================
# GhostOps — Monitoring Module
# ============================================================================
# Manages: Namespace, Prometheus (RBAC + Config + Deployment + Service),
#          Alertmanager (Config + Deployment + Service),
#          Grafana (Datasources + Dashboard Provider + Dashboard + Deployment
#                   + Service)
#
# Faithfully mirrors every resource in infra/k8s/monitoring/*.yaml
# ============================================================================

# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }
}

# ============================= PROMETHEUS ===================================

# ---------------------------------------------------------------------------
# Prometheus RBAC — ServiceAccount, ClusterRole, ClusterRoleBinding
# ---------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }
}

resource "kubernetes_cluster_role_v1" "prometheus" {
  metadata {
    name = "prometheus"
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  # Service discovery — Prometheus needs to list/watch these to find
  # scrape targets via kubernetes_sd_configs.
  rule {
    api_groups = [""]
    resources  = ["nodes", "nodes/proxy", "services", "endpoints", "pods"]
    verbs      = ["get", "list", "watch"]
  }

  # Prometheus reads ConfigMaps for some service-discovery metadata.
  rule {
    api_groups = [""]
    resources  = ["configmaps"]
    verbs      = ["get"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "prometheus" {
  metadata {
    name = "prometheus"
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.prometheus.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.prometheus.metadata[0].name
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
  }
}

# ---------------------------------------------------------------------------
# Prometheus Configuration — main config + alert rules
# ---------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "prometheus_config" {
  metadata {
    name      = "prometheus-config"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "prometheus.yml" = <<-EOT
      # GhostOps Prometheus Configuration (managed by Terraform)
      global:
        scrape_interval: 15s
        evaluation_interval: 15s

      alerting:
        alertmanagers:
          - static_configs:
              - targets:
                  - "alertmanager.monitoring.svc.cluster.local:9093"

      rule_files:
        - /etc/prometheus/rules/*.yml

      scrape_configs:

        # ---------- Prometheus self-monitoring ----------
        - job_name: "prometheus"
          static_configs:
            - targets: ["localhost:9090"]

        # ---------- kubelet / cAdvisor ----------
        - job_name: "kubelet-cadvisor"
          scheme: https
          tls_config:
            ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
            insecure_skip_verify: true
          bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
          kubernetes_sd_configs:
            - role: node
          relabel_configs:
            - action: labelmap
              regex: __meta_kubernetes_node_label_(.+)
            - target_label: __address__
              replacement: kubernetes.default.svc:443
            - source_labels: [__meta_kubernetes_node_name]
              regex: (.+)
              target_label: __metrics_path__
              replacement: /api/v1/nodes/$${1}/proxy/metrics/cadvisor

        # ---------- sample-app ----------
        - job_name: "sample-app"
          kubernetes_sd_configs:
            - role: endpoints
              namespaces:
                names:
                  - default
          relabel_configs:
            - source_labels: [__meta_kubernetes_service_name]
              regex: sample-app
              action: keep
            - source_labels: [__meta_kubernetes_pod_name]
              target_label: pod

        # ---------- GhostOps Backend ----------
        - job_name: "ghostops-backend"
          static_configs:
            - targets: ["${var.docker_bridge_gateway}:8000"]
    EOT
  }
}

resource "kubernetes_config_map_v1" "prometheus_rules" {
  metadata {
    name      = "prometheus-rules"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "alert-rules.yml" = <<-EOT
      # GhostOps alert rules — evaluated by Prometheus every 15 s
      groups:
        - name: ghostops.rules
          rules:
            - alert: SampleAppHighMemory
              expr: |
                container_memory_working_set_bytes{pod=~"sample-app.*"}
                / container_spec_memory_limit_bytes{pod=~"sample-app.*"} > 0.8
              for: 30s
              labels:
                severity: critical
                component: sample-app
              annotations:
                summary: "sample-app memory usage above 80% of limit"
                description: "Pod {{ $labels.pod }} is at risk of OOMKill"
    EOT
  }
}

# ---------------------------------------------------------------------------
# Prometheus Deployment
# ---------------------------------------------------------------------------
resource "kubernetes_deployment_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "prometheus"
      }
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "prometheus"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.prometheus.metadata[0].name

        container {
          name  = "prometheus"
          image = var.prometheus_image

          args = [
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=7d",
            "--web.enable-lifecycle",
          ]

          port {
            name           = "http"
            container_port = 9090
          }

          resources {
            requests = {
              cpu    = "200m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            http_get {
              path = "/-/healthy"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 15
            timeout_seconds       = 10
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/-/ready"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 15
            timeout_seconds       = 10
            failure_threshold     = 3
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/prometheus/prometheus.yml"
            sub_path   = "prometheus.yml"
            read_only  = true
          }

          volume_mount {
            name       = "rules"
            mount_path = "/etc/prometheus/rules"
            read_only  = true
          }

          volume_mount {
            name       = "storage"
            mount_path = "/prometheus"
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map_v1.prometheus_config.metadata[0].name
          }
        }

        volume {
          name = "rules"
          config_map {
            name = kubernetes_config_map_v1.prometheus_rules.metadata[0].name
          }
        }

        volume {
          name = "storage"
          empty_dir {}
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Prometheus Service (NodePort 30090)
# ---------------------------------------------------------------------------
resource "kubernetes_service_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "prometheus"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    type = "NodePort"

    selector = {
      "app.kubernetes.io/name" = "prometheus"
    }

    port {
      name        = "http"
      port        = 9090
      target_port = "http"
      node_port   = 30090
    }
  }
}

# ============================= ALERTMANAGER =================================

# ---------------------------------------------------------------------------
# Alertmanager Configuration
# ---------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "alertmanager_config" {
  metadata {
    name      = "alertmanager-config"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "alertmanager"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "alertmanager.yml" = <<-EOT
      # Alertmanager — routes alerts to GhostOps FastAPI webhook
      global:
        resolve_timeout: 5m

      route:
        receiver: "ghostops-webhook"
        group_by: ["alertname", "severity"]
        group_wait: 10s
        group_interval: 5m
        repeat_interval: 1h

      receivers:
        - name: "ghostops-webhook"
          webhook_configs:
            # Docker bridge gateway — routes to host where FastAPI listens
            - url: "http://${var.docker_bridge_gateway}:8000/webhook/alert"
              send_resolved: true
    EOT
  }
}

# ---------------------------------------------------------------------------
# Alertmanager Deployment
# ---------------------------------------------------------------------------
resource "kubernetes_deployment_v1" "alertmanager" {
  metadata {
    name      = "alertmanager"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "alertmanager"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "alertmanager"
      }
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "alertmanager"
        }
      }

      spec {
        container {
          name  = "alertmanager"
          image = var.alertmanager_image

          args = [
            "--config.file=/etc/alertmanager/alertmanager.yml",
            "--storage.path=/alertmanager",
          ]

          port {
            name           = "http"
            container_port = 9093
          }

          resources {
            requests = {
              cpu    = "50m"
              memory = "64Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "256Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/-/healthy"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 15
            timeout_seconds       = 10
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/-/ready"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 15
            timeout_seconds       = 10
            failure_threshold     = 3
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/alertmanager/alertmanager.yml"
            sub_path   = "alertmanager.yml"
            read_only  = true
          }

          volume_mount {
            name       = "storage"
            mount_path = "/alertmanager"
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map_v1.alertmanager_config.metadata[0].name
          }
        }

        volume {
          name = "storage"
          empty_dir {}
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Alertmanager Service (NodePort 30080)
# ---------------------------------------------------------------------------
resource "kubernetes_service_v1" "alertmanager" {
  metadata {
    name      = "alertmanager"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "alertmanager"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    type = "NodePort"

    selector = {
      "app.kubernetes.io/name" = "alertmanager"
    }

    port {
      name        = "http"
      port        = 9093
      target_port = "http"
      node_port   = 30080
    }
  }
}

# ================================ GRAFANA ===================================

# ---------------------------------------------------------------------------
# Grafana Datasources (Prometheus + GhostOps JSON API)
# ---------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "grafana_datasources" {
  metadata {
    name      = "grafana-datasources"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "grafana"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "datasources.yaml" = <<-EOT
      apiVersion: 1
      datasources:
        # Prometheus — for cluster metrics (memory, CPU, alerts)
        - name: Prometheus
          type: prometheus
          access: proxy
          url: http://prometheus.monitoring.svc.cluster.local:9090
          isDefault: true
          editable: false

        # GhostOps API — JSON API datasource for incident data
        - name: GhostOps-API
          type: marcusolsson-json-datasource
          access: proxy
          url: http://${var.docker_bridge_gateway}:8000
          editable: false
          jsonData:
            queryString: ""
    EOT
  }
}

# ---------------------------------------------------------------------------
# Grafana Dashboard Provider
# ---------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "grafana_dashboard_provider" {
  metadata {
    name      = "grafana-dashboard-provider"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "grafana"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "dashboards.yaml" = <<-EOT
      apiVersion: 1
      providers:
        - name: "GhostOps"
          orgId: 1
          folder: "GhostOps"
          type: file
          disableDeletion: false
          editable: true
          options:
            path: /var/lib/grafana/dashboards
            foldersFromFilesStructure: false
    EOT
  }
}

# ---------------------------------------------------------------------------
# Grafana Dashboard JSON (7-panel incident dashboard)
# ---------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "grafana_dashboards" {
  metadata {
    name      = "grafana-dashboards"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "grafana"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  data = {
    "ghostops-incidents.json" = file("${path.module}/files/grafana-dashboard.json")
  }
}

# ---------------------------------------------------------------------------
# Grafana Deployment
# ---------------------------------------------------------------------------
resource "kubernetes_deployment_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "grafana"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "grafana"
      }
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "grafana"
        }
      }

      spec {
        container {
          name  = "grafana"
          image = var.grafana_image

          port {
            container_port = 3000
          }

          # Allow anonymous access (no login) for local dev
          env {
            name  = "GF_AUTH_ANONYMOUS_ENABLED"
            value = "true"
          }

          env {
            name  = "GF_AUTH_ANONYMOUS_ORG_ROLE"
            value = "Admin"
          }

          env {
            name  = "GF_SECURITY_ADMIN_PASSWORD"
            value = "ghostops"
          }

          # Install the JSON API datasource plugin on startup
          env {
            name  = "GF_INSTALL_PLUGINS"
            value = "marcusolsson-json-datasource"
          }

          volume_mount {
            name       = "datasource-config"
            mount_path = "/etc/grafana/provisioning/datasources"
            read_only  = true
          }

          volume_mount {
            name       = "dashboard-provider"
            mount_path = "/etc/grafana/provisioning/dashboards"
            read_only  = true
          }

          volume_mount {
            name       = "dashboard-json"
            mount_path = "/var/lib/grafana/dashboards"
            read_only  = true
          }

          resources {
            requests = {
              memory = "128Mi"
              cpu    = "100m"
            }
            limits = {
              memory = "256Mi"
              cpu    = "500m"
            }
          }
        }

        volume {
          name = "datasource-config"
          config_map {
            name = kubernetes_config_map_v1.grafana_datasources.metadata[0].name
          }
        }

        volume {
          name = "dashboard-provider"
          config_map {
            name = kubernetes_config_map_v1.grafana_dashboard_provider.metadata[0].name
          }
        }

        volume {
          name = "dashboard-json"
          config_map {
            name = kubernetes_config_map_v1.grafana_dashboards.metadata[0].name
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Grafana Service (ClusterIP)
# ---------------------------------------------------------------------------
resource "kubernetes_service_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "grafana"
      "app.kubernetes.io/part-of" = "ghostops"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      "app.kubernetes.io/name" = "grafana"
    }

    port {
      port        = 3000
      target_port = "3000"
    }
  }
}

# ==============================================================================
# T4 18/11: Service Bus, Container Apps Environment, Container Apps & Jobs
# ==============================================================================

# 6. Azure Service Bus Namespace & Queue (thay the RabbitMQ cuc bo)
resource "azurerm_servicebus_namespace" "main" {
  name                = "sb-${var.project_name}-${var.environment}-${random_string.suffix.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"

  tags = {
    project     = var.project_name
    environment = var.environment
  }
}

resource "azurerm_servicebus_queue" "jobs" {
  name                                 = "doc2pdf-jobs"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  max_delivery_count                   = 3
  dead_lettering_on_message_expiration = true
  lock_duration                        = "PT5M"
}

resource "azurerm_key_vault_secret" "servicebus_connection_string" {
  name         = "servicebus-connection-string"
  value        = azurerm_servicebus_namespace.main.default_primary_connection_string
  key_vault_id = azurerm_key_vault.main.id
}

# 7. Container Apps Environment
# Tat logs_destination = "none" de tranh loi ExpressAzureMonitorLogDestNotSupported tren goi Student
resource "azurerm_container_app_environment" "main" {
  name                = "cae-${var.project_name}-${var.environment}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  logs_destination    = "none"

  tags = {
    project     = var.project_name
    environment = var.environment
  }
}

# 8. Container App: API (FastAPI - tu dong scale ve 0 khi ranh de tiet kiem quy $100)
resource "azurerm_container_app" "api" {
  name                         = "ca-${var.project_name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  secret {
    name  = "database-url"
    value = azurerm_key_vault_secret.database_url.value
  }

  secret {
    name  = "storage-connection-string"
    value = azurerm_storage_account.main.primary_connection_string
  }

  secret {
    name  = "jwt-secret"
    value = random_password.jwt_secret.result
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "api"
      image  = var.placeholder_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection-string"
      }

      env {
        name  = "S3_BUCKET_SOURCES"
        value = azurerm_storage_container.sources.name
      }

      env {
        name  = "S3_BUCKET_RESULTS"
        value = azurerm_storage_container.results.name
      }

      env {
        name        = "JWT_SECRET"
        secret_name = "jwt-secret"
      }
    }
  }
}

# 9. Container App: Outbox Relay (chay nen day event tu PostgreSQL sang Service Bus)
resource "azurerm_container_app" "relay" {
  name                         = "ca-${var.project_name}-relay"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  secret {
    name  = "database-url"
    value = azurerm_key_vault_secret.database_url.value
  }

  secret {
    name  = "sb-connection-string"
    value = azurerm_servicebus_namespace.main.default_primary_connection_string
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "relay"
      image  = var.placeholder_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "SERVICEBUS_CONNECTION_STRING"
        secret_name = "sb-connection-string"
      }

      env {
        name  = "SERVICEBUS_QUEUE_NAME"
        value = azurerm_servicebus_queue.jobs.name
      }
    }
  }
}

# 10. Container Apps Job: Worker (kich hoat theo su kien queue Service Bus bang KEDA)
resource "azurerm_container_app_job" "worker" {
  name                         = "job-${var.project_name}-worker"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id

  replica_timeout_in_seconds = 600
  replica_retry_limit        = 1

  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = 5
      polling_interval_in_seconds = 10

      rules {
        name             = "servicebus-queue-rule"
        custom_rule_type = "azure-servicebus"
        metadata = {
          queueName    = azurerm_servicebus_queue.jobs.name
          messageCount = "1"
        }
        authentication {
          secret_name       = "sb-connection-string"
          trigger_parameter = "connection"
        }
      }
    }
  }

  secret {
    name  = "database-url"
    value = azurerm_key_vault_secret.database_url.value
  }

  secret {
    name  = "storage-connection-string"
    value = azurerm_storage_account.main.primary_connection_string
  }

  secret {
    name  = "sb-connection-string"
    value = azurerm_servicebus_namespace.main.default_primary_connection_string
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  template {
    container {
      name   = "worker"
      image  = var.placeholder_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection-string"
      }

      env {
        name        = "SERVICEBUS_CONNECTION_STRING"
        secret_name = "sb-connection-string"
      }

      env {
        name  = "SERVICEBUS_QUEUE_NAME"
        value = azurerm_servicebus_queue.jobs.name
      }

      env {
        name  = "S3_BUCKET_SOURCES"
        value = azurerm_storage_container.sources.name
      }

      env {
        name  = "S3_BUCKET_RESULTS"
        value = azurerm_storage_container.results.name
      }
    }
  }
}

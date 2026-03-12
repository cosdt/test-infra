variable "aws_region" {
  type        = string
  description = "AWS region"
}

variable "function_name" {
  type        = string
  description = "Lambda function name"
  default     = "cross-repo-ci-relay"
}

variable "zip_path" {
  type        = string
  description = "Path to deployment.zip built from aws/lambda/cross_repo_ci_relay"
  default     = "../deployment.zip"
}

variable "vpc_enabled" {
  type        = bool
  description = "Whether to attach the Lambda to a VPC (needed for ElastiCache / private ClickHouse)"
  default     = false
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnet IDs for Lambda (when vpc_enabled=true)"
  default     = []
}

variable "security_group_ids" {
  type        = list(string)
  description = "Security group IDs for Lambda (when vpc_enabled=true)"
  default     = []
}

# --- App configuration (non-secrets) ---
variable "upstream_repo" {
  type        = string
  description = "UPSTREAM_REPO (e.g. pytorch/pytorch)"
}

variable "whitelist_path" {
  type        = string
  description = "WHITELIST_PATH; for packaged whitelist.yaml use /var/task/whitelist.yaml"
  default     = "/var/task/whitelist.yaml"
}

variable "clickhouse_url" {
  type        = string
  description = "CLICKHOUSE_URL"
}

variable "clickhouse_user" {
  type        = string
  description = "CLICKHOUSE_USER"
}

variable "clickhouse_database" {
  type        = string
  description = "CLICKHOUSE_DATABASE"
}

variable "redis_url" {
  type        = string
  description = "REDIS_URL (optional). If empty, whitelist cache will not work."
  default     = ""
}

variable "whitelist_ttl_seconds" {
  type        = number
  description = "WHITELIST_TTL_SECONDS"
  default     = 1200
}

variable "log_level" {
  type        = string
  description = "LOG_LEVEL"
  default     = "INFO"
}

# --- Secrets: pass ARNs only ---
variable "github_app_private_key_secret_arn" {
  type        = string
  description = "Secrets Manager ARN for the GitHub App private key PEM"
}

variable "github_webhook_secret_secret_arn" {
  type        = string
  description = "[Preferred] Secrets Manager ARN for GITHUB_WEBHOOK_SECRET"
  default     = null
}

variable "clickhouse_password_secret_arn" {
  type        = string
  description = "[Preferred] Secrets Manager ARN for CLICKHOUSE_PASSWORD"
  default     = null
}

variable "redis_url_secret_arn" {
  type        = string
  description = "[Optional] Secrets Manager ARN for REDIS_URL"
  default     = null
}

# NOTE: These are still required by the app, but you should supply them via your
# deployment system (e.g. set them in the console, or use an out-of-band secrets
# injection mechanism) rather than Terraform state.
variable "github_app_id" {
  type        = string
  description = "GITHUB_APP_ID (not a secret)"
}

variable "github_webhook_secret" {
  type        = string
  description = "GITHUB_WEBHOOK_SECRET (secret; avoid putting real value in TF state)"
  sensitive   = true
  default     = null
}

variable "clickhouse_password" {
  type        = string
  description = "CLICKHOUSE_PASSWORD (secret; avoid putting real value in TF state)"
  sensitive   = true
  default     = null
}

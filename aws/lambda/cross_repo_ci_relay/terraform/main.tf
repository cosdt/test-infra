terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_iam_role" "lambda_role" {
  name = "${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "basic_exec" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "vpc_access" {
  count      = var.vpc_enabled ? 1 : 0
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_policy" "secrets_access" {
  name        = "${var.function_name}-secrets-access"
  description = "Allow Lambda to read the GitHub App private key from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.github_app_private_key_secret_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "secrets_access" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.secrets_access.arn
}

resource "aws_lambda_function" "relay" {
  function_name = var.function_name
  role          = aws_iam_role.lambda_role.arn

  runtime = "python3.10"
  handler = "lambda_function.lambda_handler"

  filename         = var.zip_path
  source_code_hash = filebase64sha256(var.zip_path)

  timeout     = 30
  memory_size = 512

  environment {
    variables = merge(
      {
        # GitHub
        GITHUB_APP_ID                     = var.github_app_id
        GITHUB_APP_PRIVATE_KEY_SECRET_ARN = var.github_app_private_key_secret_arn

        # Relay behavior
        UPSTREAM_REPO  = var.upstream_repo
        WHITELIST_PATH = var.whitelist_path

        # ClickHouse (non-secret parts)
        CLICKHOUSE_URL      = var.clickhouse_url
        CLICKHOUSE_USER     = var.clickhouse_user
        CLICKHOUSE_DATABASE = var.clickhouse_database

        # Redis (non-secret parts)
        WHITELIST_TTL_SECONDS = tostring(var.whitelist_ttl_seconds)

        # Logging
        LOG_LEVEL = var.log_level
      },
      # Optional plaintext secrets (avoid in real deployments; ends up in TF state)
      var.github_webhook_secret != null ? { GITHUB_WEBHOOK_SECRET = var.github_webhook_secret } : {},
      var.clickhouse_password != null ? { CLICKHOUSE_PASSWORD = var.clickhouse_password } : {},
      var.redis_url != "" ? { REDIS_URL = var.redis_url } : {},

      # Preferred: pass secret ARNs and let the Lambda entrypoint fetch secrets.
      var.github_webhook_secret_secret_arn != null ? { GITHUB_WEBHOOK_SECRET_SECRET_ARN = var.github_webhook_secret_secret_arn } : {},
      var.clickhouse_password_secret_arn != null ? { CLICKHOUSE_PASSWORD_SECRET_ARN = var.clickhouse_password_secret_arn } : {},
      var.redis_url_secret_arn != null ? { REDIS_URL_SECRET_ARN = var.redis_url_secret_arn } : {}
    )
  }

  dynamic "vpc_config" {
    for_each = var.vpc_enabled ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }
}

# Simplest public HTTPS endpoint for GitHub Webhooks:
resource "aws_lambda_function_url" "public" {
  function_name      = aws_lambda_function.relay.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "allow_function_url" {
  statement_id  = "FunctionUrlAllowPublic"
  action        = "lambda:InvokeFunctionUrl"
  function_name = aws_lambda_function.relay.function_name
  principal     = "*"

  function_url_auth_type = "NONE"
}

output "function_url" {
  value       = aws_lambda_function_url.public.function_url
  description = "Use this as the GitHub webhook URL (append /github/webhook)."
}

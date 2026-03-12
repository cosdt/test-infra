This folder contains example Terraform for deploying `cross_repo_ci_relay` to AWS Lambda.

In the PyTorch infra setup, the real Terraform likely lives in a separate infra repo.
Treat this as a copy/paste starting point.

Key points:
- Secrets: do NOT put raw private keys / passwords into Terraform variables (they end up in state).
  Prefer AWS Secrets Manager / SSM Parameter Store and pass only ARNs/names.
- This Lambda supports loading secrets at cold start via env vars:
  - `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` → writes `/tmp/...` and sets `GITHUB_APP_PRIVATE_KEY_PATH`
  - `GITHUB_WEBHOOK_SECRET_SECRET_ARN` → sets `GITHUB_WEBHOOK_SECRET`
  - `CLICKHOUSE_PASSWORD_SECRET_ARN` → sets `CLICKHOUSE_PASSWORD`
  - `REDIS_URL_SECRET_ARN` → sets `REDIS_URL`
- Networking: Redis (ElastiCache) is usually VPC-only. Lambda must run in the same VPC/subnets/SG.
  If ClickHouse is public (ClickHouse Cloud), avoid putting Lambda into private subnets without NAT.

# cross_repo_ci_relay

FastAPI service that:
- Accepts GitHub webhooks at `POST /github/webhook`
- Accepts CI results at `POST /ci/result`

## Lambda packaging
This folder follows the `aws/lambda/` convention. Build a deployment zip:

```bash
make deployment.zip
```

The handler is `lambda_function.lambda_handler`.

## Configuration
See `.env.example` for all environment variables.

### GitHub App private key on Lambda
Recommended: store the PEM in AWS Secrets Manager and set:
- `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` to the secret ARN

On cold start, the Lambda entrypoint will fetch the secret and write it to:
- `GITHUB_APP_PRIVATE_KEY_PATH` (defaults to `/tmp/github_app_private_key.pem`)

Avoid putting the PEM content directly into Terraform / Lambda env vars.

### Other secrets (recommended)
To avoid putting secret values into Terraform state, you can also provide these as
Secrets Manager ARNs:
- `GITHUB_WEBHOOK_SECRET_SECRET_ARN` (will populate `GITHUB_WEBHOOK_SECRET`)
- `CLICKHOUSE_PASSWORD_SECRET_ARN` (will populate `CLICKHOUSE_PASSWORD`)
- `REDIS_URL_SECRET_ARN` (will populate `REDIS_URL`, if you keep Redis auth in the URL)

### Redis / ClickHouse networking
- Redis (ElastiCache) is typically only reachable inside a VPC. If you use Redis,
  configure the Lambda `vpc_config` to place it into the same VPC/subnets/SG.
- If ClickHouse is a public endpoint (e.g. ClickHouse Cloud), do not put the
  Lambda into private subnets without a NAT gateway, otherwise it cannot reach
  the internet.

# cross_repo_ci_relay

An AWS Lambda function that relays GitHub webhook events from the upstream repository to downstream repositories.

For more information, please refer to this [RFC](https://github.com/pytorch/pytorch/issues/175022).

## Environment Variables

### `cross_repo_ci_relay`

| Variable | Required | Default | Description | Example |
|----------|----------|---------|-------------|---------|
| `GITHUB_APP_ID` | yes | — | GitHub App ID | `1234567` |
| `GITHUB_APP_SECRET` | yes* | — | GitHub webhook secret (or use `SECRET_STORE_ARN`) | `whsec_...` |
| `GITHUB_APP_PRIVATE_KEY` | yes* | — | GitHub App private key PEM (or use `SECRET_STORE_ARN`) | `-----BEGIN RSA...` |
| `SECRET_STORE_ARN` | yes* | — | AWS Secrets Manager ARN containing `GITHUB_APP_SECRET` and `GITHUB_APP_PRIVATE_KEY` | `arn:aws:secretsmanager:us-east-1:123456789012:secret:cross-repo-ci-relay/app-secrets-xxxxxx` |
| `REDIS_ENDPOINT` | yes | — | AWS ElastiCache endpoint hostname or `host:port` | `my-cache.xxxxxx.apse1.cache.amazonaws.com` |
| `REDIS_LOGIN` | no | — | Redis credentials in `username:password` format | `default:relay-password` |
| `REDIS_TLS` | no | `true` | Enable TLS (`rediss://`) for Redis connection | `true` |
| `UPSTREAM_REPO` | no | `pytorch/pytorch` | Upstream repository (`owner/repo`) to relay webhooks from | `pytorch/pytorch` |
| `WHITELIST_URL` | yes | — | GitHub blob URL to allowlist YAML | `https://github.com/<owner>/<repo>/blob/<ref>/allowlist.yaml` |
| `WHITELIST_TTL_SECONDS` | no | `1200` | Allowlist cache TTL in Redis (seconds) | `1200` |
| `LOG_LEVEL` | no | `INFO` | Python logging level | `DEBUG` |

\* Provide either `GITHUB_APP_SECRET` + `GITHUB_APP_PRIVATE_KEY` directly, or `SECRET_STORE_ARN` (Secrets Manager fallback).

Only `L1` allowlist entries are supported.

## Whitelist Format

`WHITELIST_URL` should point to a YAML file in GitHub blob format.

Example:

```yaml
L1:
  - org1/repo1
  - org2/repo2
L2:
  - org3/repo3
L3:
  - org4/repo4
L4:
  - org5/repo5: oncall1, oncall2
```

## Build and Deploy

### Make Targets

```bash
# Build the Lambda zip
make prepare

# Deploy webhook (build + aws lambda update-function-code)
make deploy

# Clean build artifacts
make clean
```

`make deploy` is equivalent to:

```bash
make prepare
aws lambda update-function-code --region us-east-1 --function-name cross_repo_ci_webhook --zip-file fileb://deployment.zip
```

You can override the deployment target if needed:

```bash
make deploy AWS_REGION=us-east-1 FUNCTION_NAME=cross_repo_ci_webhook
```

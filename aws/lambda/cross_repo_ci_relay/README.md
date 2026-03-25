# cross_repo_ci_relay

An AWS Lambda function that relays GitHub webhook events from the upstream repository to downstream repositories.

For more information, please refer to this [RFC](https://github.com/pytorch/pytorch/issues/175022).

## Environment Variables

### `cross_repo_ci_relay`

| Variable | Description | Example |
|----------|-------------|---------|
| `GITHUB_APP_ID` | GitHub App ID | `1234567` |
| `SECRET_STORE_ARN` | AWS Secrets Manager secret ARN for sensitive config | `arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:cross-repo-ci-relay/app-secrets-xxxxxx` |
| `UPSTREAM_REPO` | Upstream repository (`owner/repo`) | `pytorch/pytorch` |
| `WHITELIST_URL` | GitHub blob URL to whitelist YAML | `https://github.com/<owner>/<repo>/blob/<ref>/whitelist.yaml` |
| `LOG_LEVEL` | Logging level | `INFO` |

The Lambda currently loads the whitelist directly from `WHITELIST_URL` on each invocation. ElastiCache-based caching is temporarily disabled.

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
  - org5/repo5
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
aws lambda update-function-code --region ap-southeast-1 --function-name cross_repo_ci_webhook --zip-file fileb://deployment.zip
```

You can override the deployment target if needed:

```bash
make deploy AWS_REGION=ap-southeast-1 FUNCTION_NAME=cross_repo_ci_webhook
```

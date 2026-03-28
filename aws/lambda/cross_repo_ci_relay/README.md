# Cross Repo CI Relay

An AWS Lambda function that relays GitHub webhook events from the upstream repository to downstream repositories.

For more information, please refer to this [RFC](https://github.com/pytorch/pytorch/issues/175022).

## Allowlist Format

`ALLOWLIST_URL` must point to a YAML file hosted as a GitHub blob (e.g. `https://github.com/<owner>/<repo>/blob/<ref>/allowlist.yaml`).

All levels (L1–L4) are dispatched to. Dispatch targets are the union of all repositories across every level.

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

Each entry is either a plain `owner/repo` string or a `owner/repo: oncall1, oncall2` mapping. Duplicate repositories across levels are not allowed.

The allowlist is cached in Redis under the key `crcr:allowlist_yaml` with a TTL controlled by `ALLOWLIST_TTL_SECONDS`. On a Redis error the function falls back to fetching directly from GitHub.

## Build and Deploy

### Make Targets

```bash
# Build the Lambda zip (output: deployment.zip)
make deployment.zip

# Deploy to AWS Lambda (requires AWS CLI v2 configured with permissions)
make deploy

# Clean build artifacts
make clean
```

## Environment Variables

| Variable | Required | Default | Description | Example |
|----------|----------|---------|-------------|---------|
| `GITHUB_APP_ID` | yes | — | GitHub App ID | `1234567` |
| `GITHUB_APP_SECRET` | yes* | — | GitHub webhook secret (or use `SECRET_STORE_ARN`) | `whsec_...` |
| `GITHUB_APP_PRIVATE_KEY` | yes* | — | GitHub App private key PEM (or use `SECRET_STORE_ARN`) | `-----BEGIN RSA...` |
| `SECRET_STORE_ARN` | yes* | — | AWS Secrets Manager ARN containing `GITHUB_APP_SECRET` and `GITHUB_APP_PRIVATE_KEY` | `arn:aws:secretsmanager:us-east-1:123456789012:secret:cross-repo-ci-relay/app-secrets-xxxxxx` |
| `REDIS_ENDPOINT` | yes | — | AWS ElastiCache endpoint hostname or `host:port` | `my-cache.xxxxxx.apse1.cache.amazonaws.com` |
| `REDIS_LOGIN` | no | — | Redis credentials in `username:password` format | `default:relay-password` |
| `UPSTREAM_REPO` | no | `pytorch/pytorch` | Upstream repository (`owner/repo`) to relay webhooks from | `pytorch/pytorch` |
| `ALLOWLIST_URL` | yes | — | GitHub blob URL to allowlist YAML | `https://github.com/<owner>/<repo>/blob/<ref>/allowlist.yaml` |
| `ALLOWLIST_TTL_SECONDS` | no | `1200` | Allowlist cache TTL in Redis (seconds) | `1200` |

\* Provide either `GITHUB_APP_SECRET` + `GITHUB_APP_PRIVATE_KEY` directly, or `SECRET_STORE_ARN` (Secrets Manager fallback). Environment variables take priority over Secrets Manager.

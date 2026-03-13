# cross_repo_ci_relay

Two AWS Lambda functions that relay GitHub webhook events from the upstream repository (`cosdt/UpStream`) to downstream repositories, and write CI results to ClickHouse.

Splitting into two functions keeps webhook processing and result ingestion isolated, which simplifies log analysis and CloudWatch alarming.

## Repository Layout

```
cross_repo_ci_relay/
├── config.py                    # shared — RelayConfig dataclass
├── utils.py                     # shared — signature verification, GitHub helpers, RelayHTTPException
├── whitelist_redis_helper.py    # shared — Redis-backed whitelist cache
├── whitelist.yaml               # shared — allowlisted downstream repos
├── webhook/
│   ├── lambda_function.py       # entrypoint: handles POST /github/webhook
│   ├── webhook_handler.py       # relay logic: dispatch to downstream repos
│   ├── github_client_helper.py  # GitHub API client wrapper
│   └── requirements.txt
├── result/
│   ├── lambda_function.py       # entrypoint: handles POST /ci/result
│   ├── result_handler.py        # result ingestion logic → ClickHouse
│   ├── clickhouse_client_helper.py
│   └── requirements.txt
├── Makefile
└── README.md
```

## Architecture

```
GitHub App Webhook
       │  POST /github/webhook
       ▼
cross_repo_ci_webhook (Lambda Function URL)
       │
       └─► webhook_handler  →  workflow_dispatch to downstream repos


Downstream CI runner
       │  POST /ci/result
       ▼
cross_repo_ci_result (Lambda Function URL)
       │
       └─► result_handler   →  write row to ClickHouse oot_ci_results
```

## Environment Variables

### `cross_repo_ci_webhook`

| Variable | Description | Example |
|----------|-------------|---------|
| `GITHUB_APP_ID` | GitHub App ID | `2847493` |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Path to PEM private key (set automatically from Secrets Manager) | `/tmp/github_app_private_key.pem` |
| `UPSTREAM_REPO` | Upstream repository (`owner/repo`) | `cosdt/UpStream` |
| `WHITELIST_PATH` | Path to whitelist YAML | `whitelist.yaml` |
| `REDIS_URL` | Redis connection URL (set from Secrets Manager) | `redis://:pass@host:6379/0` |
| `WHITELIST_TTL_SECONDS` | Whitelist cache TTL in Redis (seconds) | `3600` |
| `LOG_LEVEL` | Logging level | `INFO` |

Secrets Manager ARN variables (Terraform sets these; the Lambda reads the actual secret at cold start):

| ARN env var | Secret path | Value fetched |
|-------------|-------------|---------------|
| `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` | `cross-repo-ci-relay/github-app-private-key` | PEM text |
| `GITHUB_WEBHOOK_SECRET_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` | `webhook_secret` |
| `REDIS_URL_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` | `redis_url` |

### `cross_repo_ci_result`

| Variable | Description | Example |
|----------|-------------|---------|
| `WHITELIST_PATH` | Path to whitelist YAML | `whitelist.yaml` |
| `CLICKHOUSE_URL` | ClickHouse HTTP endpoint | `http://111.119.217.84:8123` |
| `CLICKHOUSE_USER` | ClickHouse username | `admin` |
| `CLICKHOUSE_DATABASE` | ClickHouse database | `default` |
| `CLICKHOUSE_PASSWORD` | ClickHouse password (set from Secrets Manager) | — |
| `REDIS_URL` | Redis connection URL (set from Secrets Manager) | `redis://:pass@host:6379/0` |
| `WHITELIST_TTL_SECONDS` | Whitelist cache TTL in Redis (seconds) | `3600` |
| `LOG_LEVEL` | Logging level | `INFO` |

| ARN env var | Secret path | Value fetched |
|-------------|-------------|---------------|
| `CLICKHOUSE_PASSWORD_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` | `clickhouse_password` |
| `REDIS_URL_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` | `redis_url` |

For local development, create a `.env` file — `config.py` loads it automatically via `python-dotenv`.

## Secrets Manager Setup

Create two secrets in `us-east-1` before the first deploy:

```bash
# GitHub App private key (PEM text)
aws secretsmanager create-secret \
  --name cross-repo-ci-relay/github-app-private-key \
  --secret-string "$(cat your_private_key.pem)" \
  --region us-east-1

# JSON bundle for all other sensitive values
aws secretsmanager create-secret \
  --name cross-repo-ci-relay/app-secrets \
  --secret-string '{"webhook_secret":"...","clickhouse_password":"...","redis_url":"redis://:pass@host:6379/0"}' \
  --region us-east-1
```

## Whitelist Configuration

`whitelist.yaml` defines the downstream repositories allowed to receive relayed events, grouped by priority tier:

```yaml
L1:
  - repo: cosdt/DownStream2
    device: Device2
    url: https://github.com/cosdt/DownStream2
    oncall: []
L2:
  - repo: cosdt/DownStream1
    device: Device1
    url: https://github.com/cosdt/DownStream1
    oncall: [fffrog]
```

The whitelist is cached in Redis (TTL = `WHITELIST_TTL_SECONDS`). After updating `whitelist.yaml`, rebuild and redeploy both packages.

## Build and Deploy

### Make Targets

```bash
# Build both zips
make prepare

# Build only one
make prepare-webhook
make prepare-result

# Deploy both (build + aws lambda update-function-code)
make deploy

# Deploy only one
make deploy-webhook
make deploy-result

# Clean build artifacts
make clean
```

`make deploy-webhook` is equivalent to:

```bash
make prepare-webhook
aws lambda update-function-code --function-name cross_repo_ci_webhook --zip-file fileb://webhook/deployment.zip
```

### First Deploy via ci-infra Terraform

Infrastructure (IAM role, both Lambda functions, both Function URLs) is managed by Terraform in [ci-infra](https://github.com/cosdt/ci-infra) at `ali/aws/391835788720/us-east-1/cross_repo_ci_relay.tf`.

```bash
# 1. Build both zips in test-infra
cd test-infra/aws/lambda/cross_repo_ci_relay
make prepare

# 2. Copy zips to ci-infra assets directories
cp webhook/deployment.zip <ci-infra>/ali/assets/cross_repo_ci_webhook/deployment.zip
cp result/deployment.zip  <ci-infra>/ali/assets/cross_repo_ci_result/deployment.zip

# 3. Apply Terraform in ci-infra
cd <ci-infra>/ali/aws/391835788720/us-east-1
terraform init   # required on first run
terraform plan
terraform apply
```

Terraform outputs two Function URLs after apply:
- `cross_repo_ci_webhook_function_url` — use this for the GitHub App webhook
- `cross_repo_ci_result_function_url` — use this for downstream CI result posting

### Subsequent Code Updates

For routine code changes, use `make deploy` directly — no Terraform needed:

```bash
cd test-infra/aws/lambda/cross_repo_ci_relay
make deploy-webhook   # or deploy-result, or just deploy
```

If only one function changed, deploy only that one to save time.

## GitHub App Configuration

1. Go to the GitHub App settings (Settings → Developer settings → GitHub Apps)
2. Set the Webhook URL to the `cross_repo_ci_webhook_function_url` output, appending the path:
   ```
   https://<webhook-function-url>/github/webhook
   ```
3. Set the Webhook Secret to the same value as `GITHUB_WEBHOOK_SECRET`
4. Subscribe to events: **Pull requests**
5. Install the App on the upstream repository (`cosdt/UpStream`)

## Architecture

```
GitHub App Webhook
       │  POST /github/webhook
       ▼
 Lambda Function URL
       │
       ├─► webhook_handler  →  dispatch workflow_dispatch to downstream repos
       └─► result_handler   →  write CI result to ClickHouse
```

Routes:
- `POST /github/webhook` — receives GitHub App webhook events, verifies the signature, and relays `pull_request` events to whitelisted downstream repositories
- `POST /ci/result` — receives CI results from downstream and writes them to ClickHouse

## Environment Variables

All configuration is injected via environment variables. Non-sensitive variables are set directly in Terraform; sensitive variables are injected from AWS Secrets Manager at cold start (see below).

For local development, create a `.env` file in the project directory.

### Non-sensitive Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GITHUB_APP_ID` | GitHub App ID | `2847493` |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Path to the GitHub App private key PEM file | `/tmp/github_app_private_key.pem` |
| `UPSTREAM_REPO` | Upstream repository (`owner/repo`) | `cosdt/UpStream` |
| `WHITELIST_PATH` | Path to the whitelist YAML file | `whitelist.yaml` |
| `CLICKHOUSE_URL` | ClickHouse HTTP endpoint | `http://111.119.217.84:8123` |
| `CLICKHOUSE_USER` | ClickHouse username | `admin` |
| `CLICKHOUSE_DATABASE` | ClickHouse database | `default` |
| `WHITELIST_TTL_SECONDS` | TTL for whitelist cache in Redis (seconds) | `3600` |
| `LOG_LEVEL` | Logging level | `INFO` |

### Sensitive Variables (Injected from Secrets Manager)

At cold start, `lambda_function.py` automatically reads the following variables from Secrets Manager. If the corresponding `_SECRET_ARN` environment variable is set and the target variable is not already populated, it is filled in automatically.

| Variable | Secret ARN env var | Secrets Manager path |
|----------|--------------------|----------------------|
| `GITHUB_WEBHOOK_SECRET` | `GITHUB_WEBHOOK_SECRET_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` (key: `webhook_secret`) |
| `GITHUB_APP_PRIVATE_KEY_PATH` | `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` | `cross-repo-ci-relay/github-app-private-key` (PEM text) |
| `CLICKHOUSE_PASSWORD` | `CLICKHOUSE_PASSWORD_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` (key: `clickhouse_password`) |
| `REDIS_URL` | `REDIS_URL_SECRET_ARN` | `cross-repo-ci-relay/app-secrets` (key: `redis_url`) |

Two secrets must be created in Secrets Manager (`us-east-1`) before the first deploy:

```bash
# PEM private key
aws secretsmanager create-secret \
  --name cross-repo-ci-relay/github-app-private-key \
  --secret-string "$(cat your_private_key.pem)" \
  --region us-east-1

# JSON bundle for the remaining three sensitive values
aws secretsmanager create-secret \
  --name cross-repo-ci-relay/app-secrets \
  --secret-string '{"webhook_secret":"...","clickhouse_password":"...","redis_url":"redis://:pass@host:6379/0"}' \
  --region us-east-1
```

## Whitelist Configuration

`whitelist.yaml` defines the downstream repositories that are allowed to receive relayed events, grouped by priority tier:

```yaml
L1:
  - repo: cosdt/DownStream2
    device: Device2
    url: https://github.com/cosdt/DownStream2
    oncall: []
L2:
  - repo: cosdt/DownStream1
    device: Device1
    url: https://github.com/cosdt/DownStream1
    oncall: [fffrog]
```

The whitelist is cached in Redis with a TTL controlled by `WHITELIST_TTL_SECONDS`. After updating `whitelist.yaml`, rebuild and redeploy the package.

## Local Development

```bash
# Install dependencies
pip3 install -r requirements.txt

# Create a .env file with the variables listed above
# Then invoke the handler directly
python3 -c "
import json, lambda_function
event = {
  'requestContext': {'http': {'method': 'POST', 'path': '/github/webhook'}},
  'headers': {'x-github-event': 'ping', 'x-hub-signature-256': ''},
  'body': json.dumps({'zen': 'test'}),
  'isBase64Encoded': False
}
print(lambda_function.lambda_handler(event, None))
"
```

## Build and Deploy

### Make Targets

```bash
# Install dependencies and produce deployment.zip
make prepare

# Build and update the Lambda function code directly (requires AWS CLI)
make deploy

# Remove build artifacts
make clean
```

`make deploy` is equivalent to:

```bash
make prepare
aws lambda update-function-code --function-name cross_repo_ci_relay --zip-file fileb://deployment.zip
```

### First Deploy via ci-infra Terraform

The Lambda function and its supporting infrastructure (IAM role, Function URL) are managed by Terraform in the [ci-infra](https://github.com/cosdt/ci-infra) repository at `ali/aws/391835788720/us-east-1/cross_repo_ci_relay.tf`.

```bash
# 1. Build the zip in test-infra
cd test-infra/aws/lambda/cross_repo_ci_relay
make prepare

# 2. Copy the zip to the ci-infra assets directory
cp deployment.zip <ci-infra>/ali/assets/cross_repo_ci_relay/deployment.zip

# 3. Apply Terraform in ci-infra
cd <ci-infra>/ali/aws/391835788720/us-east-1
terraform init   # required on first run
terraform plan
terraform apply
```

The Terraform configuration:
- Creates an IAM role with `AWSLambdaBasicExecutionRole` and Secrets Manager read access
- Creates the Lambda function (`python3.10`, handler `lambda_function.lambda_handler`)
- Creates a Lambda Function URL (public, no API Gateway required)
- Outputs the Function URL to use when configuring the GitHub App webhook

### Subsequent Code Updates

For routine code changes, use `make deploy` directly — no Terraform needed:

```bash
cd test-infra/aws/lambda/cross_repo_ci_relay
make deploy
```

## GitHub App Configuration

1. Go to the GitHub App settings page (Settings → Developer settings → GitHub Apps)
2. Set the Webhook URL to the Function URL output by Terraform, appending the path:
   ```
   https://<function-url>/github/webhook
   ```
3. Set the Webhook Secret to the same value as `GITHUB_WEBHOOK_SECRET`
4. Subscribe to events: **Pull requests**
5. Install the App on the upstream repository (`cosdt/UpStream`)

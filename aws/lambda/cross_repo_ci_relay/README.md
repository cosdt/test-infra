# cross_repo_ci_relay

Two AWS Lambda functions that work together:

- `cross_repo_ci_webhook` receives `POST /github/webhook` from the upstream GitHub App and dispatches downstream workflows.
- `cross_repo_ci_result` receives `POST /ci/result` from downstream workflows and writes CI results to ClickHouse.

Splitting them keeps webhook processing and result ingestion isolated, which makes logs, alarms, and debugging much easier.

## Repository Layout

```text
cross_repo_ci_relay/
├── config.py
├── utils.py
├── whitelist_redis_helper.py
├── webhook/
│   ├── lambda_function.py
│   ├── webhook_handler.py
│   ├── github_client_helper.py
│   └── requirements.txt
├── result/
│   ├── lambda_function.py
│   ├── result_handler.py
│   ├── clickhouse_client_helper.py
│   └── requirements.txt
├── Makefile
└── README.md
```

## Request Flow

```text
GitHub App Webhook
       |
       | POST /github/webhook
       v
cross_repo_ci_webhook
       |
       +--> verifies webhook signature
       +--> loads whitelist from GitHub URL and Redis cache
       +--> dispatches downstream workflows


Downstream workflow
       |
       | POST /ci/result
       v
cross_repo_ci_result
       |
       +--> loads whitelist from GitHub URL and Redis cache
       +--> validates repo against allowlist
       +--> writes row to ClickHouse
```

## What Must Be Configured In AWS

You need four pieces in AWS:

1. Secrets Manager secrets
2. Two Lambda functions
3. Lambda environment variables
4. Two Lambda Function URLs

Terraform in `ci-infra` creates the Lambda functions, IAM role, and Function URLs. The Lambda code now expects direct environment variables like `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, and `REDIS_URL`.

## Secrets Manager

Create these secrets in `us-east-1` before the first deploy.

All of them should be plain-text string secrets.

```bash
aws secretsmanager create-secret \
  --name cross-repo-ci-relay/github-app-private-key \
  --secret-string "$(cat your_private_key.pem)" \
  --region us-east-1

aws secretsmanager create-secret \
  --name cross-repo-ci-relay/github-webhook-secret \
  --secret-string 'your-webhook-secret' \
  --region us-east-1

aws secretsmanager create-secret \
  --name cross-repo-ci-relay/redis-url \
  --secret-string 'redis://:password@host:6379/0' \
  --region us-east-1

aws secretsmanager create-secret \
  --name cross-repo-ci-relay/clickhouse-password \
  --secret-string 'your-clickhouse-password' \
  --region us-east-1
```

## Lambda Runtime Settings

Both Lambda functions use:

- Runtime: `python3.10`
- Handler: `lambda_function.lambda_handler`
- Timeout: `30`
- Memory size: `512`

## Lambda Environment Variables

### `cross_repo_ci_webhook`

Configure these environment variables:

| Variable | Required | Meaning |
|----------|----------|---------|
| `GITHUB_APP_ID` | yes | GitHub App ID |
| `UPSTREAM_REPO` | yes | Upstream repo, for example `cosdt/UpStream` |
| `WHITELIST_PATH` | yes | GitHub blob URL of `whitelist.yaml` |
| `GITHUB_WEBHOOK_SECRET` | yes | GitHub App webhook secret |
| `GITHUB_APP_PRIVATE_KEY` | yes | PEM contents of the GitHub App private key |
| `REDIS_URL` | yes | Full Redis URL |
| `WHITELIST_TTL_SECONDS` | optional | Default in code is `1200` |
| `LOG_LEVEL` | optional | Usually `INFO` |

If you still want to keep the source of truth in Secrets Manager, inject the secret values into these environment variables from Terraform or the Lambda console. The runtime no longer reads `GITHUB_WEBHOOK_SECRET_SECRET_ARN`, `GITHUB_APP_PRIVATE_KEY_SECRET_ARN`, or `REDIS_URL_SECRET_ARN`.

### `cross_repo_ci_result`

Configure these environment variables:

| Variable | Required | Meaning |
|----------|----------|---------|
| `WHITELIST_PATH` | yes | Same whitelist URL as webhook Lambda |
| `CLICKHOUSE_URL` | yes | Example: `http://host:8123` |
| `CLICKHOUSE_USER` | yes | ClickHouse username |
| `CLICKHOUSE_DATABASE` | yes | Usually `default` |
| `GITHUB_APP_ID` | yes | GitHub App ID for check run updates |
| `GITHUB_APP_PRIVATE_KEY` | yes | PEM contents of the GitHub App private key |
| `UPSTREAM_REPO` | yes | Upstream repo name |
| `REDIS_URL` | yes | Full Redis URL |
| `CLICKHOUSE_PASSWORD_SECRET_ARN` | yes | ARN of `cross-repo-ci-relay/clickhouse-password` |
| `WHITELIST_TTL_SECONDS` | optional | Default in code is `1200` |
| `LOG_LEVEL` | optional | Usually `INFO` |

At cold start, this Lambda still supports `CLICKHOUSE_PASSWORD_SECRET_ARN` and will populate `CLICKHOUSE_PASSWORD` if needed.

## Function URL Configuration

Each Lambda needs a Function URL with auth type `NONE`.

Expected paths are:

- webhook Lambda: `POST /github/webhook`
- result Lambda: `POST /ci/result`

If the Function URL auth type is `AWS_IAM`, public requests will fail with:

```json
{"Message":"Forbidden"}
```

## Terraform

Infrastructure is managed in `ci-infra` here:

`ali/aws/391835788720/us-east-1/cross_repo_ci_relay.tf`

That Terraform file currently:

- creates the shared IAM role
- grants `secretsmanager:GetSecretValue` for the remaining ClickHouse password bootstrap
- creates `cross_repo_ci_webhook`
- creates `cross_repo_ci_result`
- creates public Function URLs for both functions
- outputs both Function URLs

## Build Packages

```bash
cd /opt/test-infra/aws/lambda/cross_repo_ci_relay
make prepare
```

This produces:

- `webhook/deployment.zip`
- `result/deployment.zip`

You can also build them separately:

```bash
make prepare-webhook
make prepare-result
```

## First Deploy With Terraform

```bash
cd /opt/test-infra/aws/lambda/cross_repo_ci_relay
make prepare

mkdir -p /opt/ci-infra/ali/assets/cross_repo_ci_webhook
mkdir -p /opt/ci-infra/ali/assets/cross_repo_ci_result

cp webhook/deployment.zip /opt/ci-infra/ali/assets/cross_repo_ci_webhook/deployment.zip
cp result/deployment.zip  /opt/ci-infra/ali/assets/cross_repo_ci_result/deployment.zip

cd /opt/ci-infra/ali/aws/391835788720/us-east-1
terraform init
terraform plan
terraform apply
```

Terraform outputs:

- `cross_repo_ci_webhook_function_url`
- `cross_repo_ci_result_function_url`

Use them as:

- GitHub App webhook URL: `<webhook_function_url>/github/webhook`
- downstream result URL: `<result_function_url>/ci/result`

## Code Updates After First Deploy

For code-only updates, you can push zip updates directly with AWS CLI:

```bash
cd /opt/test-infra/aws/lambda/cross_repo_ci_relay
make deploy-webhook
make deploy-result
```

Or both:

```bash
make deploy
```

## GitHub App Setup

Configure the GitHub App with:

1. Webhook URL: `<cross_repo_ci_webhook_function_url>/github/webhook`
2. Webhook secret: the same value stored in `cross-repo-ci-relay/github-webhook-secret`
3. Event subscription: `Pull requests`
4. Install the App on the upstream repository

## Downstream Workflow Setup

Downstream workflows should post to:

```text
<cross_repo_ci_result_function_url>/ci/result
```

Recommended payload:

```json
{
  "url": "https://github.com/<owner>/<repo>/actions/runs/123456",
  "workflow_name": "test-ci",
  "upstream_repo": "cosdt/UpStream",
  "commit_sha": "abcdef123456",
  "status": "completed",
  "conclusion": "success"
}
```

## Whitelist

The relay does not read a bundled local `whitelist.yaml`. It reads the whitelist from `WHITELIST_PATH`, which should be a GitHub blob URL, and caches the contents in Redis.

Example:

```text
https://github.com/cosdt/UpStream/blob/main/whitelist.yaml
```

When the whitelist changes, Lambda picks it up after the Redis TTL expires, or immediately after deleting the Redis cache key:

```bash
redis-cli -h <host> -a <password> DEL oot:whitelist_yaml
```

## Local Development

For local testing, put values into `.env`. `config.py` loads it automatically.

See `.env.example` for the expected keys.

## Common Failure Cases

- `{"Message":"Forbidden"}`: Function URL auth type is `AWS_IAM` instead of `NONE`
- `{"detail":"Not found"}` on `/ci/result`: the wrong zip was deployed to that function
- `Secrets Manager response missing SecretString/SecretBinary`: the secret exists but does not contain a valid string value
- ClickHouse connection error: `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, or `CLICKHOUSE_PASSWORD` is wrong
- allowlist 403: the downstream repo URL is not present in the current whitelist

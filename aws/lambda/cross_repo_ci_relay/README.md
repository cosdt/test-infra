# cross_repo_ci_relay

Two AWS Lambda functions that work together:

- `cross_repo_ci_webhook` receives `POST /github/webhook` from the upstream GitHub App, dispatches downstream workflows, and creates GitHub check runs based on participation level.
- `cross_repo_ci_result` receives `POST /ci/result` from downstream workflows, writes CI results to ClickHouse, and updates GitHub check runs.

Splitting them keeps webhook processing and result ingestion isolated, which makes logs, alarms, and debugging much easier.

## Participation Levels

Each downstream repository declares a participation level in `whitelist.yaml`:

| Level | Behaviour |
|-------|-----------|
| L1 | Dispatch only — trigger downstream workflow and nothing else |
| L2 | Dispatch + write result row to ClickHouse |
| L3 | L2 + create a GitHub check run only when the PR carries a `ciflow/oot/<device>` label |
| L4 | L2 + always create a blocking GitHub check run (label not required) |

For L3, adding the label to an already-open PR retroactively surfaces check run status for any already-running downstream workflows.

## Repository Layout

```text
cross_repo_ci_relay/
├── config.py                    # RelayConfig dataclass (shared)
├── utils.py                     # Signature verification, allowlist helpers (shared)
├── redis_client_helper.py       # RedisClientFactory (shared)
├── github_client_helper.py      # GithubAppFactory (shared)
├── whitelist_redis_helper.py    # Whitelist YAML cache in Redis (shared)
├── pr_redis_helper.py           # Upstream PR info cache in Redis (shared)
├── checkrun_helper.py           # create_check_run / update_check_run (shared)
├── clickhouse_client_helper.py  # CHCliFactory (shared)
├── webhook/
│   ├── lambda_function.py
│   ├── webhook_handler.py
│   └── requirements.txt
├── result/
│   ├── lambda_function.py
│   ├── result_handler.py
│   └── requirements.txt
├── Makefile
└── README.md
```

The `SHARED` variable in the Makefile copies all root-level helper modules into both deployment zips so each Lambda is self-contained.

## Request Flow

```text
GitHub App Webhook (pull_request: opened/reopened/synchronize/labeled)
       |
       | POST /github/webhook
       v
cross_repo_ci_webhook
       |
       +--> verifies webhook signature
       +--> loads whitelist from GitHub URL and Redis cache
       |
       +--> opened/reopened/synchronize action:
       |      +--> caches PR info (pr_number, installation_id) in Redis
       |      +--> dispatches downstream workflow for each allowlisted device
       |
       +--> labeled action (ciflow/oot/<device>):
              +--> adds device to labeled set in Redis
              +--> queries ClickHouse for in-flight/completed downstream workflows
              +--> creates/updates GitHub check runs for matching workflows (L3)


Downstream workflow
       |
       | POST /ci/result
       v
cross_repo_ci_result
       |
       +--> loads whitelist from GitHub URL and Redis cache
       +--> validates repo against allowlist
       +--> writes result row to ClickHouse (L2+)
       +--> checks Redis for PR info and label state
       +--> creates or updates GitHub check run (L3/L4)
```

## What Must Be Configured In AWS

You need four pieces in AWS:

1. Secrets Manager secrets
2. Two Lambda functions
3. Lambda environment variables
4. Two Lambda Function URLs

Terraform in `ci-infra` creates the Lambda functions, IAM role, and Function URLs.

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

Each Lambda reads sensitive values directly from environment variables at cold start. As a convenience, if a `*_SECRET_ARN` environment variable is set and the corresponding plain-text variable is absent, the Lambda fetches the value from Secrets Manager and populates the variable automatically. This lets you store the value in Secrets Manager and pass only the ARN to Terraform, or just set the plain-text variable directly — whichever you prefer.

## Lambda Runtime Settings

Both Lambda functions use:

- Runtime: `python3.10`
- Handler: `lambda_function.lambda_handler`
- Timeout: `30`
- Memory size: `512`

## Lambda Environment Variables

### `cross_repo_ci_webhook`

| Variable | Required | Meaning |
|----------|----------|---------|
| `GITHUB_APP_ID` | yes | GitHub App ID |
| `UPSTREAM_REPO` | yes | Upstream repo, for example `cosdt/UpStream` |
| `WHITELIST_PATH` | yes | GitHub blob URL of `whitelist.yaml` |
| `GITHUB_WEBHOOK_SECRET` | yes¹ | GitHub App webhook secret |
| `GITHUB_WEBHOOK_SECRET_SECRET_ARN` | yes¹ | ARN fallback for `GITHUB_WEBHOOK_SECRET` |
| `GITHUB_APP_PRIVATE_KEY` | yes² | PEM contents of the GitHub App private key |
| `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` | yes² | ARN fallback for `GITHUB_APP_PRIVATE_KEY` |
| `REDIS_URL` | yes³ | Full Redis URL |
| `REDIS_URL_SECRET_ARN` | yes³ | ARN fallback for `REDIS_URL` |
| `WHITELIST_TTL_SECONDS` | optional | Whitelist cache TTL in seconds (default `1200`) |
| `PR_INFO_TTL_SECONDS` | optional | PR info cache TTL in seconds (default `604800`) |
| `GITHUB_API_TIMEOUT` | optional | GitHub API timeout in seconds (default `30`) |
| `LOG_LEVEL` | optional | Usually `INFO` |

¹ Set either `GITHUB_WEBHOOK_SECRET` or `GITHUB_WEBHOOK_SECRET_SECRET_ARN`.  
² Set either `GITHUB_APP_PRIVATE_KEY` or `GITHUB_APP_PRIVATE_KEY_SECRET_ARN`.  
³ Set either `REDIS_URL` or `REDIS_URL_SECRET_ARN`.

### `cross_repo_ci_result`

| Variable | Required | Meaning |
|----------|----------|---------|
| `WHITELIST_PATH` | yes | Same whitelist URL as webhook Lambda |
| `CLICKHOUSE_URL` | yes | Example: `http://host:8123` |
| `CLICKHOUSE_USER` | yes | ClickHouse username |
| `CLICKHOUSE_DATABASE` | yes | Usually `default` |
| `CLICKHOUSE_PASSWORD` | yes¹ | ClickHouse password |
| `CLICKHOUSE_PASSWORD_SECRET_ARN` | yes¹ | ARN fallback for `CLICKHOUSE_PASSWORD` |
| `GITHUB_APP_ID` | yes | GitHub App ID for check run creation/updates |
| `GITHUB_APP_PRIVATE_KEY` | yes² | PEM contents of the GitHub App private key |
| `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` | yes² | ARN fallback for `GITHUB_APP_PRIVATE_KEY` |
| `UPSTREAM_REPO` | yes | Upstream repo name |
| `REDIS_URL` | yes³ | Full Redis URL |
| `REDIS_URL_SECRET_ARN` | yes³ | ARN fallback for `REDIS_URL` |
| `WHITELIST_TTL_SECONDS` | optional | Whitelist cache TTL in seconds (default `1200`) |
| `PR_INFO_TTL_SECONDS` | optional | PR info cache TTL in seconds (default `604800`) |
| `GITHUB_API_TIMEOUT` | optional | GitHub API timeout in seconds (default `30`) |
| `LOG_LEVEL` | optional | Usually `INFO` |

¹ Set either `CLICKHOUSE_PASSWORD` or `CLICKHOUSE_PASSWORD_SECRET_ARN`.  
² Set either `GITHUB_APP_PRIVATE_KEY` or `GITHUB_APP_PRIVATE_KEY_SECRET_ARN`.  
³ Set either `REDIS_URL` or `REDIS_URL_SECRET_ARN`.

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
3. Event subscriptions: `Pull requests` (covers `opened`, `reopened`, `synchronize`, `labeled`)
4. Install the App on the upstream repository

## Downstream Workflow Setup

Downstream workflows should post to:

```text
<cross_repo_ci_result_function_url>/ci/result
```

Required payload fields:

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

The relay expects two calls per run:

1. **Call 1** — when the run starts: `status: "in_progress"`, `conclusion: "neutral"`. The relay creates the check run and stores its ID in ClickHouse.
2. **Call 2** — when the run finishes: `status: "completed"`, `conclusion: "success" | "failure"`. The relay looks up the stored check run ID and updates it.

## Whitelist

The relay reads the whitelist from `WHITELIST_PATH` (a GitHub blob URL) and caches its contents in Redis.

Example entry:

```yaml
- device: cuda
  level: 4
  repo: https://github.com/my-org/cuda-backend
  url: https://github.com/my-org/cuda-backend
  oncall: my-team
```

The `level` field controls check run behaviour (see [Participation Levels](#participation-levels)). For L3, downstream workflows are only surfaced as check runs on PRs that carry a `ciflow/oot/<device>` label.

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
- Check runs not appearing for L3: ensure the PR carries a `ciflow/oot/<device>` label matching the device name in `whitelist.yaml`
- Check run not updated on Call 2: Call 1 (`in_progress`) was never received so no check run ID was stored in ClickHouse; verify the downstream workflow posts both calls

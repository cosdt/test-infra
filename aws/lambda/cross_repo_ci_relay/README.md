# Cross Repo CI Relay

An AWS Lambda function that relays GitHub webhook events from the upstream repository to downstream repositories.

For more information, please refer to this [RFC](https://github.com/pytorch/pytorch/issues/175022).

## Overall Mechanism

This service receives webhook events from an upstream GitHub repository and acts as a relay for cross-repository CI signaling.

When a supported pull request event is received, the function validates the request, determines whether the event should be processed, and then resolves the downstream repositories that should receive the relay.

Those downstream targets are defined through an allowlist file which is pointed by a url (set with `ALLOWLIST_URL` and should be hosted as a GitHub blob, e.g. `https://github.com/pytorch/pytorch/blob/main/.github/allowlist.yaml`), whose format is described below.


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

All levels (L1–L4) are dispatched to. Dispatch targets are the union of all repositories across every level.

Each entry is either a plain `owner/repo` string or a `owner/repo: oncall1, oncall2` mapping. Duplicate repositories across levels are not allowed.

The allowlist is cached in Redis under the key `crcr:allowlist_yaml` with a TTL controlled by `ALLOWLIST_TTL_SECONDS`. On a Redis error the function falls back to fetching directly from GitHub.

## Reporting Results from Downstream CI

L2+ downstream repositories can report the status of their CI workflows back to the relay server using the [`cross-repo-ci-relay-callback`](../../../.github/actions/cross-repo-ci-relay-callback/action.yml) composite action.

### Security and the Relay/HUD boundary

The result endpoint is a **transparent proxy to HUD** with a single security
responsibility: identifying the calling repo.  Everything else is HUD's job.

- **Identity (Relay's job)**: the `Authorization: Bearer <oidc-token>` header
  is verified against GitHub's JWKS.  The OIDC `repository` claim is the
  only trusted identity for the caller and is used for the L2+ allowlist
  check.  Relay forwards this trusted value to HUD under
  `infra.verified_repo`; HUD should prefer it over anything self-reported
  in the body.
- **Schema / business validation (HUD's job)**: the callback body is passed
  through to HUD verbatim as a top-level `body` field.  Relay does **not**
  validate `status`, `conclusion`, `head_sha`, `pr_number`, `test_results`,
  or any other field — HUD owns the schema since it owns persistence.
  The HUD request looks like:

  ```json
  {
    "body": { ... workflow callback body verbatim ... },
    "verified_repo": "org/repo",
    "infra": { "queue_time": 1.23, "execution_time": 45.6 }
  }
  ```
- **Response (transparent)**: HUD's HTTP status is propagated back to the
  downstream workflow.  A 4xx from HUD surfaces the same 4xx to the
  caller, so schema bugs fail loudly in the workflow run.

#### Known limitations of this model

A compromised or malicious maintainer of an allowlisted repo can:

1. Fabricate status/conclusion values for upstream PRs their repo was never
   dispatched for — HUD will receive the row, but `infra.verified_repo`
   always identifies the true caller.
2. Replay an older dispatched payload against the result endpoint — there
   is no dispatch-side nonce.
3. Tamper with any body field, including `downstream_repo` — HUD must
   trust `infra.verified_repo`, not the body.

All three attacks are **scoped to the attacker's own OIDC-authenticated
repo identity** — OIDC guarantees they cannot impersonate another
allowlisted repo.  Mitigation is operational: every HUD row carries
`infra.verified_repo`, so misbehaviour is observable, and the offending
repo can be removed from `allowlist.yaml`.

If stronger guarantees are required later, the typical next step is a
signed callback token minted by the webhook side plus a one-shot state
machine in Redis keyed on `delivery_id`.  This was intentionally deferred
to keep the relay simple — see the PR description for the discussion.

### Prerequisites

- The downstream repository must be listed at level **L2 or higher** in the allowlist.
- The **calling job** must declare `permissions: id-token: write` so that the action can mint a GitHub OIDC token for authentication.

### Usage

When triggered by a relay `repository_dispatch`, `pr-number`, `head-sha`, and `upstream-repo` are **automatically resolved** from `github.event.client_payload` — only `status` (and `conclusion` for the final report) need to be provided explicitly.

```yaml
on:
  repository_dispatch:
    types: [pull_request]

jobs:
  my-ci-job:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # required for OIDC token minting
      contents: read
    steps:
      - name: Report in-progress to relay
        uses: pytorch/test-infra/.github/actions/cross-repo-ci-relay-callback@main
        with:
          status: in_progress

      # ... your CI steps ...

      - name: Report final result to relay
        if: always()
        uses: pytorch/test-infra/.github/actions/cross-repo-ci-relay-callback@main
        with:
          status: completed
          conclusion: ${{ job.status == 'success' && 'success' || 'failure' }}
```

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `status` | **yes** | — | `in_progress` or `completed` |
| `conclusion` | no | `''` | `success` or `failure` (required when `status=completed`) |
| `callback-url` | no | see `action.yml` | result callback url for local testing |

## Build, Deploy, and Test

### Make Targets

Build the Webhook Lambda zip (output: deployment.zip)
```bash
cd webhook
make deployment.zip
```

Build the Result Lambda zip (output: deployment.zip)
```bash
cd result
make deployment.zip
```

Deploy both zip to AWS Lambda (requires AWS CLI v2 configured with permissions)
```bash
make deploy AWS_REGION=us-east-1 WEBHOOK_FUNCTION_NAME=cross_repo_ci_webhook RESULT_FUNCTION_NAME=cross_repo_ci_result
```

Run all unit tests under tests/ folder
```bash
make test
```

Clean build artifacts
```bash
make clean
```

## Local Development

`local_server.py` wraps the Lambda handler in a FastAPI app so you can test the full cross-repo-ci-relay flow without deploying to AWS.

### Prerequisites

#### Local

- Python 3.13
- A running Redis instance:
  ```bash
  # Using the built-in "default" user with a password:
  docker run -d --name oot-redis \
    -p 6379:6379 \
    redis:7-alpine \
    redis-server --requirepass <your-password>
  ```
- [smee.io](https://smee.io)

  CLI to forward GitHub webhook events to localhost (paste this link to GitHub App webhook URL):
  ```bash
  npm install -g smee-client
  smee --url https://smee.io/<your-channel> --path /github/webhook --port 8000
  ```

  CLI to forward GitHub result callbacks to localhost (should set this url to `callback-url` in downstream workflow):
  ```bash
  npm install -g smee-client
  smee --url https://smee.io/<your-channel> --path /github/result --port 8000
  ```

#### Remote

- GitHub App settings (refer to this [RFC](https://github.com/pytorch/pytorch/issues/175022))
- An allowlist YAML GitHub URL with the specific format (refer to the same RFC above)
- An Upstream repo and Downstream repos with GitHub App installed and allowlist configured

### Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt fastapi uvicorn python-dotenv
   ```

2. Create a `.env` file in this directory:
   ```dotenv
   # GitHub App
   GITHUB_APP_ID=<app-id>
   GITHUB_APP_SECRET=<webhook-secret>
   GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
   <key content>
   -----END RSA PRIVATE KEY-----"

   # Relay
   UPSTREAM_REPO=<owner/repo>
   ALLOWLIST_URL=https://github.com/<owner>/<repo>/blob/main/allowlist.yaml
   MAX_DISPATCH_WORKERS=32

   # Redis (local, no TLS)
   REDIS_ENDPOINT=localhost:6379
   REDIS_LOGIN=default:<password>
   ALLOWLIST_TTL_SECONDS=1200
   ```
   **Note**: `ALLOWLIST_URL` is required for local development which should point to a GitHub URL that can be different from the real one.

3. Start the server:
   ```bash
   python3 local_server.py
   ```

4. Point your GitHub App's webhook URL to the smee.io channel, then open or update a pull request in the upstream repo to trigger a full relay cycle.

5. Check and see whether the workflow run status is uploaded through `callback-url`.

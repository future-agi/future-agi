# fi-simulate CLI

Command-line tool for running [FutureAGI Simulate](https://app.futureagi.com) test runs and gating CI/CD pipelines on pass-rate thresholds.

## Installation

```bash
pip install -e futureagi/
```

This puts `fi-simulate` on your `PATH`.

## Authentication

Two auth methods are supported via `--api-key` (or the `FI_API_KEY` env var):

| Format | Headers sent |
|---|---|
| `api_key:secret_key` | `X-Api-Key` + `X-Secret-Key` |
| `<jwt>` (no colon) | `Authorization: Bearer <jwt>` |

Set `FI_BASE_URL` to override the default API endpoint (`https://app.futureagi.com`).

## Usage

### Minimal

```bash
fi-simulate run \
  --test-id 1f2c3d4e-... \
  --api-key $FI_API_KEY
```

### Full options

```bash
fi-simulate run \
  --test-id 1f2c3d4e-... \
  --api-key mykey:mysecret \
  --scenario-ids a1b2,c3d4 \
  --simulator-id s1 \
  --threshold 0.85 \
  --timeout 1800 \
  --poll-interval 10 \
  --output text        # text | json | github
```

### Check status only

```bash
fi-simulate status \
  --test-id 1f2c3d4e-... \
  --api-key $FI_API_KEY
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Execution completed and `pass_rate >= threshold` |
| `1` | Execution completed but `pass_rate < threshold` (regression) |
| `2` | Execution failed, cancelled, or timed out |
| `3` | Usage / auth / network error |

## Output formats

### `--output text` (default)

Human-readable summary with a pass-rate bar printed to stdout.

### `--output json`

Stable JSON schema for machine parsing:

```json
{
  "run_test_id": "...",
  "execution_id": "...",
  "status": "completed",
  "pass_rate": 0.9,
  "threshold": 0.8,
  "passed": true,
  "calls": { "total": 10, "completed": 10, "failed": 0 },
  "eval_summary": [...]
}
```

### `--output github`

Writes a Markdown table to `$GITHUB_STEP_SUMMARY` (GitHub Actions step summary). Falls back to stdout if the env var is not set.

## GitHub Actions — quickstart

```yaml
- name: Run Simulate gate
  run: |
    pip install -e futureagi/
    fi-simulate run \
      --test-id ${{ vars.SIMULATE_TEST_ID }} \
      --api-key ${{ secrets.FUTURE_AGI_API_KEY }} \
      --threshold 0.85 \
      --output github
```

## GitHub Actions — matrix (multiple test IDs in parallel)

```yaml
jobs:
  simulate:
    strategy:
      matrix:
        test_id: [id-1, id-2, id-3]
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e futureagi/
      - run: fi-simulate run --test-id ${{ matrix.test_id }} --api-key ${{ secrets.FUTURE_AGI_API_KEY }}
```

## Self-hosted example

```bash
fi-simulate run \
  --test-id 1f2c... \
  --api-key $FI_API_KEY \
  --base-url https://your-instance.example.com
```

## Jenkinsfile example

```groovy
stage('Simulate gate') {
    steps {
        sh '''
            pip install -e futureagi/
            fi-simulate run \
              --test-id ${SIMULATE_TEST_ID} \
              --api-key ${FUTURE_AGI_API_KEY} \
              --threshold 0.85
        '''
    }
}
```

## All flags

### `fi-simulate run`

| Flag | Default | Description |
|---|---|---|
| `--test-id` | required | Run-test UUID |
| `--api-key` | `$FI_API_KEY` | Auth credential |
| `--base-url` | `$FI_BASE_URL` / `https://app.futureagi.com` | API base URL |
| `--scenario-ids` | all | Comma-separated scenario UUIDs |
| `--simulator-id` | — | Override simulator |
| `--threshold` | `0.8` | Min pass rate to exit 0 |
| `--timeout` | `1800` | Max seconds to wait |
| `--poll-interval` | `5` | Seconds between status polls |
| `--output` | `text` | `text`, `json`, or `github` |

### `fi-simulate status`

| Flag | Default | Description |
|---|---|---|
| `--test-id` | required | Run-test UUID |
| `--api-key` | `$FI_API_KEY` | Auth credential |
| `--base-url` | `$FI_BASE_URL` / `https://app.futureagi.com` | API base URL |

# fi-simulate

**CLI for running FutureAGI Simulate test runs from the command line.**

Gate CI/CD pipelines on simulation pass rates. Start an execution, poll until completion, and exit with a non-zero status if the aggregate eval pass rate falls below a threshold.

## Installation

```bash
# From the repo root:
cd futureagi
pip install -e .

# Verify:
fi-simulate --version
```

## Quick Start

```bash
# Minimal: run a test and block until done
fi-simulate run \
  --test-id 1f2c3d4e-5678-... \
  --api-key $FI_API_KEY \
  --secret-key $FI_SECRET_KEY

# Full: run specific scenarios, gate on pass rate
fi-simulate run \
  --test-id 1f2c3d4e-5678-... \
  --scenario-ids a1b2,c3d4,e5f6 \
  --simulator-id s1 \
  --threshold 0.85 \
  --timeout 1800 \
  --base-url https://app.futureagi.com \
  --api-key $FI_API_KEY \
  --secret-key $FI_SECRET_KEY \
  --output github

# Just check status
fi-simulate status \
  --test-id 1f2c... \
  --execution-id e1... \
  --api-key $FI_API_KEY \
  --secret-key $FI_SECRET_KEY
```

## Flags

### `run` subcommand

| Flag | Required | Default | Description |
|---|---|---|---|
| `--test-id` | ✅ | — | UUID of the RunTest to execute |
| `--api-key` | ✅ | `$FI_API_KEY` | FutureAGI API key |
| `--secret-key` | ✅ | `$FI_SECRET_KEY` | FutureAGI secret key |
| `--scenario-ids` | | all | Comma-separated scenario UUIDs |
| `--simulator-id` | | — | Simulator UUID |
| `--threshold` | | `0.0` | Minimum aggregate pass rate (0.0–1.0) |
| `--timeout` | | `3600` | Max seconds to wait |
| `--poll-interval` | | `5` | Seconds between status polls |
| `--base-url` | | `$FI_BASE_URL` or `https://app.futureagi.com` | API base URL |
| `--output` | | `text` | Output format: `text`, `json`, or `github` |

### `status` subcommand

| Flag | Required | Default | Description |
|---|---|---|---|
| `--test-id` | ✅ | — | UUID of the RunTest |
| `--execution-id` | ✅ | — | UUID of the execution |
| `--api-key` | ✅ | `$FI_API_KEY` | FutureAGI API key |
| `--secret-key` | ✅ | `$FI_SECRET_KEY` | FutureAGI secret key |
| `--base-url` | | `$FI_BASE_URL` | API base URL |
| `--output` | | `text` | Output format |

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Execution completed and aggregate pass rate ≥ threshold |
| `1` | Execution completed but pass rate < threshold (regression) |
| `2` | Execution failed or timed out |
| `3` | Usage / auth / network error |

## Environment Variables

| Variable | Maps to |
|---|---|
| `FI_API_KEY` | `--api-key` |
| `FI_SECRET_KEY` | `--secret-key` |
| `FI_BASE_URL` | `--base-url` |

## Output Formats

### `--output text` (default)

Human-readable summary with progress bar and per-eval breakdown table.

### `--output json`

Stable JSON schema for machine parsing:

```json
{
  "event": "summary",
  "execution_id": "exec-123",
  "status": "completed",
  "passed": true,
  "aggregate_pass_rate": 0.85,
  "threshold": 0.8,
  "evals": [
    {"name": "Accuracy", "passed": 8, "total": 10},
    {"name": "Relevance", "passed": 9, "total": 10}
  ]
}
```

### `--output github`

Markdown suitable for `$GITHUB_STEP_SUMMARY`.

## CI/CD Examples

### GitHub Actions

```yaml
name: Simulate Gate
on: [pull_request]

jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install fi-simulate
        run: |
          cd futureagi
          pip install -e .

      - name: Run simulation
        env:
          FI_API_KEY: ${{ secrets.FI_API_KEY }}
          FI_SECRET_KEY: ${{ secrets.FI_SECRET_KEY }}
        run: |
          fi-simulate run \
            --test-id ${{ vars.SIMULATE_TEST_ID }} \
            --threshold 0.85 \
            --timeout 1800 \
            --output github \
            >> $GITHUB_STEP_SUMMARY
```

### Jenkinsfile

```groovy
pipeline {
    agent any
    environment {
        FI_API_KEY     = credentials('fi-api-key')
        FI_SECRET_KEY  = credentials('fi-secret-key')
    }
    stages {
        stage('Simulate') {
            steps {
                sh '''
                    cd futureagi
                    pip install -e .
                    fi-simulate run \
                      --test-id ${SIMULATE_TEST_ID} \
                      --threshold 0.85 \
                      --timeout 1800 \
                      --output text
                '''
            }
        }
    }
}
```

## Development

```bash
# Run tests
cd futureagi
python -m pytest sdk/cli/tests/ -v

# Lint
black --check sdk/cli/
isort --check sdk/cli/
ruff check sdk/cli/
```

# fi-simulate GitHub Action

Composite action that runs a [FutureAGI Simulate](https://app.futureagi.com) test run and gates your CI pipeline on the pass-rate result.

## Quickstart

```yaml
- uses: future-agi/future-agi/.github/actions/fi-simulate@main
  with:
    test-id: ${{ vars.SIMULATE_TEST_ID }}
    api-key: ${{ secrets.FUTURE_AGI_API_KEY }}
    threshold: "0.85"
```

The step fails if the aggregate pass rate falls below `threshold`.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `test-id` | ✅ | — | Run-test UUID to execute |
| `api-key` | ✅ | — | API credential — Bearer JWT or `api_key:secret_key` pair. Use a repo/org secret. |
| `scenario-ids` | | (all) | Comma-separated scenario UUIDs |
| `simulator-id` | | — | Override the simulator |
| `threshold` | | `0.8` | Min pass rate to pass the step (0.0–1.0) |
| `timeout` | | `1800` | Max seconds to wait for completion |
| `poll-interval` | | `5` | Seconds between status polls |
| `base-url` | | `https://app.futureagi.com` | Override for self-hosted deployments |
| `python-version` | | `3.11` | Python version on the runner |

## Outputs

| Output | Description |
|---|---|
| `execution-id` | The execution ID (use for downstream steps) |
| `pass-rate` | Aggregate pass rate (0.0–1.0) |
| `summary-json` | Full eval summary as a JSON string |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass rate ≥ threshold |
| `1` | Pass rate < threshold (regression) |
| `2` | Execution failed, cancelled, or timed out |
| `3` | Auth / network / usage error |

## Examples

### Gate a PR on simulation results

```yaml
name: Simulate gate

on:
  pull_request:

jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run FutureAGI Simulate
        uses: future-agi/future-agi/.github/actions/fi-simulate@main
        with:
          test-id: ${{ vars.SIMULATE_TEST_ID }}
          api-key: ${{ secrets.FUTURE_AGI_API_KEY }}
          threshold: "0.85"
```

### Use outputs in subsequent steps

```yaml
- name: Run Simulate
  id: simulate
  uses: future-agi/future-agi/.github/actions/fi-simulate@main
  with:
    test-id: ${{ vars.SIMULATE_TEST_ID }}
    api-key: ${{ secrets.FUTURE_AGI_API_KEY }}

- name: Print results
  run: |
    echo "Execution: ${{ steps.simulate.outputs.execution-id }}"
    echo "Pass rate: ${{ steps.simulate.outputs.pass-rate }}"
```

### Run multiple tests in parallel (matrix)

```yaml
jobs:
  simulate:
    strategy:
      matrix:
        test_id: [id-1, id-2, id-3]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: future-agi/future-agi/.github/actions/fi-simulate@main
        with:
          test-id: ${{ matrix.test_id }}
          api-key: ${{ secrets.FUTURE_AGI_API_KEY }}
          threshold: "0.8"
```

### Self-hosted deployment

```yaml
- uses: future-agi/future-agi/.github/actions/fi-simulate@main
  with:
    test-id: ${{ vars.SIMULATE_TEST_ID }}
    api-key: ${{ secrets.FUTURE_AGI_API_KEY }}
    base-url: "https://your-instance.example.com"
```

## How it works

1. Sets up Python on the runner via `actions/setup-python@v5`
2. Installs `fi-simulate` via `pip`
3. Runs `fi-simulate run` with the provided inputs
4. Writes a Markdown summary table to `$GITHUB_STEP_SUMMARY`
5. Sets `execution-id`, `pass-rate`, and `summary-json` outputs
6. Exits with the CLI exit code — GitHub marks the step failed on exit code > 0

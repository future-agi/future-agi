# Future AGI Simulate Action

A GitHub Action to run simulation and evaluation tests via Future AGI.

## Usage

### Minimal Example

```yaml
steps:
  - name: Run Future AGI tests
    uses: future-agi/future-agi/.github/actions/simulate@main
    with:
      test-id: 'your-test-id'
      api-key: ${{ secrets.FI_API_KEY }}
```

### Self-Hosted Example

```yaml
steps:
  - name: Run Future AGI tests
    uses: future-agi/future-agi/.github/actions/simulate@main
    with:
      test-id: 'your-test-id'
      base-url: 'https://your-internal-futureagi.com'
      api-key: ${{ secrets.FI_API_KEY }}
```

### Matrix Example

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        test-id: ['test-id-1', 'test-id-2']
    steps:
      - name: Run Future AGI tests
        uses: future-agi/future-agi/.github/actions/simulate@main
        with:
          test-id: ${{ matrix.test-id }}
          api-key: ${{ secrets.FI_API_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `test-id` | Yes | - | Run-test ID to execute |
| `api-key` | Yes | - | API key |
| `scenario-ids` | No | (all) | Comma-separated scenario IDs |
| `simulator-id` | No | - | Override simulator |
| `threshold` | No | 0.8 | Pass-rate gate (0.0-1.0) |
| `timeout` | No | 1800 | Seconds to wait |
| `base-url` | No | `https://app.futureagi.com` | API base URL |
| `python-version` | No | 3.11 | Python version |

## Outputs

- `execution-id`: The execution ID.
- `pass-rate`: Aggregate pass rate.
- `summary-json`: The full eval summary as JSON.

## Exit Codes
- 0: Pass rate >= threshold
- 1: Pass rate < threshold (regression)
- 2: Timeout or execution failure
- 3: Usage / Auth error

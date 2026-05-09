---
id: ADR-034
title: fi-simulate CLI and MCP tool for headless simulation runs
status: accepted
date: 2026-05-09
related_issues: ["#80", "#81"]
tla_spec: docs/tla/SimulateCLI.tla
---

## Context

Issue #80 (requested by Salil): the platform has no first-class CLI for running
simulations from a terminal or CI pipeline. Users must either use the dashboard
UI or call the REST API directly — neither is scriptable without glue code.

Issue #81: no reusable GitHub Action exists, so CI integration requires users to
write their own `curl`/`jq` pipelines against undocumented endpoints.

Two usage modes need to be supported:

1. **Headless / CI**: exit code communicates pass/fail; JSON output is
   machine-readable; no interactive UI. This is the dominant use case for #81.
2. **Headed / interactive**: a rich TUI with a live progress spinner, color-coded
   status, and a human-readable summary table for local development.

The simulation backend already exposes the necessary REST endpoints
(`POST /simulate/run-test/`, `GET /simulate/run-test/{id}/status/`,
`GET /simulate/run-test/{id}/summary/`). The missing piece is a
client-side polling loop with correct timeout semantics.

Additionally, the existing MCP server (`futureagi/mcp_server/`) has a simulation
tool group but no tool that wraps the full run-to-completion lifecycle.

## Decision

### CLI structure (`futureagi/sdk/cli/`)

Two entry points sharing a common polling state machine:

```
futureagi/sdk/cli/
  main.py   — argument parsing, mode selection (headless vs headed), exit code
  poll.py   — polling state machine: authenticate → start → poll → summarize
```

The polling state machine is specified by `docs/tla/SimulateCLI.tla`. Key
invariants enforced at runtime:

- **NeverPollBeforeStart**: execution ID is required before any poll attempt.
- **SummaryOnlyAfterTerminal**: summary fetch only fires after run_status is
  terminal (`completed`, `failed`, or `cancelled`).
- **TimeoutBounded**: total elapsed time never exceeds `TIMEOUT_S`.
- **TerminalIsStable**: once the CLI reaches `done`, `failed`, or `timed_out`,
  it does not transition further.

Exit codes:
- `0` — execution completed and pass rate ≥ threshold (default 80%).
- `1` — execution failed, was cancelled, timed out, or pass rate < threshold.

### Headless mode

Writes a single JSON object to stdout on completion:

```json
{
  "execution_id": "...",
  "status": "completed",
  "pass_rate": 92,
  "exit_code": 0,
  "elapsed_s": 45
}
```

Suitable for CI pipelines and downstream `jq` processing.

### Headed mode

Uses `rich` to render a live spinner during polling and a summary table on
completion. Detects non-TTY environments and falls back to headless automatically.

### MCP tools (`futureagi/mcp_server/tools/simulate_run.py`)

Four tools added to the existing `category = "simulation"` group:

| Tool | Description |
|------|-------------|
| `fi_simulate_run` | Start a simulation run and poll to completion (wraps full lifecycle) |
| `fi_simulate_status` | Poll the status of an in-progress run by execution ID |
| `fi_simulate_results` | Fetch the final summary for a completed run |
| `fi_simulate_list` | List recent simulation runs for the current workspace |

These follow the existing `BaseTool` / `@register_tool` pattern.

### GitHub Action (`.github/actions/fi-simulate/action.yml`)

Composite action wrapping the CLI in headless mode. Inputs:

| Input | Description |
|-------|-------------|
| `api_key` | Future AGI API key (required, secret) |
| `suite_id` | Simulation suite to run (required) |
| `threshold` | Pass rate threshold 0–100 (default: 80) |
| `timeout` | Total timeout in seconds (default: 300) |
| `base_url` | API base URL (default: production) |

Sets output `pass_rate` and fails the step when exit code is 1.

## Alternatives considered

- **Single-mode CLI (headless only)**: simpler, but the PR description for #80
  specifically requested a "rich terminal UI." Adding `--json` flag for
  headless and defaulting to headed when stdout is a TTY gives both without
  mode selection complexity.
- **Standalone MCP server**: rejected — the existing `mcp_server/` already
  has a simulation category and a consistent `BaseTool` pattern. Fitting in
  avoids tool discovery fragmentation.
- **Polling in the GitHub Action itself** (shell script): brittle, requires
  re-implementing the state machine in bash. Using the CLI as the action's
  implementation gives correctness for free.

## Formal verification

- `docs/tla/SimulateCLI.tla` / `docs/tla/SimulateCLI.cfg` — TLA+ spec proving
  the polling state machine: always terminates, never polls before start, never
  fetches summary before terminal status, elapsed time is bounded.
- `futureagi/sdk/cli/formal_tests/test_simulate_cli_z3.py` — Z3 proofs:
  termination, bounded polling, exit code assignments, terminal phase stability.
- `futureagi/sdk/cli/formal_tests/test_simulate_cli_hypothesis.py` — Hypothesis:
  polling invariants, timeout correctness, exit code contract.

## Consequences

- `fi-simulate` becomes the canonical CI primitive for simulation pass/fail
  gating — it replaces ad-hoc `curl` pipelines.
- The GitHub Action (closes #81) can be used as `uses: future-agi/future-agi/.github/actions/fi-simulate@main`.
- The MCP tools enable AI agents (including this assistant) to trigger and monitor
  simulation runs programmatically.
- The `rich` dependency is added to the SDK extras (`pip install futureagi[cli]`);
  it is not required for the core library.

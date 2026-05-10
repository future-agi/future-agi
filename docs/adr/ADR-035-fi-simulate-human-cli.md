# ADR-035: fi-simulate Human CLI — Subcommands, Name Resolution, Failure Drill-Down

**Status:** Accepted  
**Date:** 2026-05-10  
**TLA+ spec:** `docs/tla/SimulateCLIHuman.tla`

## Context

The existing `fi-simulate` CLI was designed as a CI gate: it accepts a UUID, runs a simulation, and exits with code 0 or 1. Humans can't use it without knowing UUIDs, and the output only shows aggregate pass rates.

Two things block human usefulness:
1. **Discovery** — no way to find available simulation suites without the web UI
2. **Failure signal** — pass rate drops but you can't see *which* scenarios failed

## Decision

Extend `sdk/cli/main.py` to support three subcommands:

### `fi-simulate list`
Lists simulation suites by name with scenario count, last run time, and last pass rate.
Supports `--search <pattern>` for server-side filtering.
Exit code 0 always (read-only).

### `fi-simulate run <name-or-uuid>`
Accepts either:
- A UUID (direct, no API list call needed)
- A name string — server-side search against `GET /api/simulate/run-tests/?search=<name>`

Name resolution rules (modelled in `SimulateCLIHuman.tla`):
- 0 matches → error with "no suites match; run `fi-simulate list` to browse"
- 1 match → proceed (no user confirmation needed)
- >1 match → error listing all matches; user must be more specific or use UUID

After completion, show **failing scenarios** by name and score, not just aggregate metrics.
Preserves all existing `SimulateCLI.tla` invariants (NeverPollBeforeStart, SummaryOnlyAfterTerminal, etc.).

### `fi-simulate status <execution-id>`
Fetches and displays the current status of a running or completed execution.
Read-only: never dispatches a new execution.

## Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Add `--name` flag to existing command | Doesn't support discovery; still monolithic |
| Interactive TUI (prompt_toolkit / curses) | Over-engineered; adds a heavy dependency |
| Web UI link in output | Already exists; doesn't help terminal users |
| Fuzzy matching with auto-select on >1 match | Violates AmbiguousNameFails invariant — silent wrong selection is worse than an error |

## Consequences

- `main.py` restructures from a single argument set to `argparse` subparsers
- `poll.py` gains `list_suites(search, limit)` and `fetch_failures(execution_id)` methods
- Existing `--run-test-id`, `--json`, `--threshold`, `--timeout` flags move under the `run` subcommand
- The GitHub Action wrapper is unaffected (it calls `python -m sdk.cli.main run <uuid> --json`)
- CI backward-compatibility: `python -m sdk.cli.main run <uuid>` replaces the previous direct invocation

## API surface used

| Endpoint | Used by |
|----------|---------|
| `GET /api/simulate/run-tests/?search=<s>&limit=20` | `list`, name resolution in `run` |
| `POST /api/simulate/run-tests/<uuid>/execute/` | `run` (start execution) |
| `GET /api/simulate/test-executions/<uuid>/?execution_id=<id>` | `run` (poll status) |
| `GET /api/simulate/run-tests/<uuid>/eval-summary/` | `run` (results), `status` |

## Name resolution algorithm

```
matches = GET /run-tests/?search=query (case-insensitive server-side filter)
if len(matches) == 0: error("no suites match …")
if len(matches) > 1: error("ambiguous: found N suites — use UUID or be more specific")
run_test_id = matches[0]["id"]
```

All three cases are covered by Z3 proofs in `test_human_cli_z3.py` and property tests in `test_human_cli_hypothesis.py`.

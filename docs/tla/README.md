# TLA+ Specifications

## TestExecutionWorkflow

Formal model of `simulate/temporal/workflows/test_execution_workflow.py`.

### What it models

The five-phase Temporal workflow that drives simulation test runs:

| Phase | Action |
|-------|--------|
| Initializing | `setup_test_execution` activity — loads config, resolves agent_version |
| Launching | `create_call_execution_records` — creates `CallExecution` rows; `LaunchCall` per batch |
| Running | Waits for all calls to enter `analyzing` state (call audio/text received) |
| Evaluating | Waits for evals to complete; transitions each call to `completed`/`failed` |
| Finalizing | Sets `TestExecution.status` based on whether any call succeeded |

### Properties proved

**Safety invariants** (pure state predicates, checked for all reachable states):

| Property | Meaning |
|----------|---------|
| `CountIntegrity` | Once creation is done, `total_calls` always equals the actual number of `CallExecution` records |
| `FinalizationCorrect` | At `Done`: `tex_status = completed` iff at least one call completed; `failed` otherwise |

**Temporal safety properties** (box-action formulas, checked under `PROPERTIES`):

| Property | Meaning |
|----------|---------|
| `NoRollback` | `CallExecution` status is monotone — never goes backwards (e.g. completed → ongoing is impossible) |
| `TexStatusMonotone` | `TestExecution` status is monotone |
| `NoFailedToOngoing` | A call in any terminal state (including `failed` at creation — fix for issue #312) never transitions to `ongoing` |

**Liveness properties** (checked under the `Fairness` assumption):

| Property | Meaning |
|----------|---------|
| `EventuallyTerminates` | The workflow eventually reaches `Done`, `WorkflowFailed`, or `Cancelled` |
| `AllCallsEventuallyTerminal` | Every created `CallExecution` eventually reaches `completed`, `failed`, or `cancelled` |
| `TerminalCallsImplyFinalization` | Once all calls are terminal, finalization eventually fires |

### Running the checker

Install [TLA+ Toolbox](https://github.com/tlaplus/tlaplus/releases) or use the CLI:

```bash
# Download tlc2.jar from https://github.com/tlaplus/tlaplus/releases
java -jar tlc2.jar -config TestExecutionWorkflow.cfg TestExecutionWorkflow.tla
```

With `N_CALLS = 3`, TLC explores ~50,000 states in under a minute. Increase to 4–5 for deeper coverage (exponential growth).

### Fairness assumptions

Liveness properties require the `Fairness` conjunction in `Spec`:

- **Weak fairness** on workflow phase transitions (`SetupSucceeds`, `AllLaunched`, etc.) — if a transition is continuously enabled, it eventually fires.
- **Weak fairness** on `CreateCall(c)`, `LaunchCall(c)`, and `CallAnalyzing(c)` — each call is eventually created, launched, and transitions to the analyzing phase.
- **Strong fairness** on `EvalCompletes(c)` and `EvalFails(c)` — the external agent eventually responds (infinitely often enabled → eventually fires).

Without `Fairness`, liveness properties are unprovable (the model-checker can always find a stuttering run).

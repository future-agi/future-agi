# Reproducibility passports for simulation runs

Simulation and eval runs can fail for reasons that are hard to separate after
the fact: a prompt version changed, an eval mapping drifted, a scenario row was
filtered differently, or a simulator agent moved to a different model. A
reproducibility passport is a deterministic JSON artifact that records the
configuration surface needed to explain and replay a `TestExecution`.

The backend helper lives in:

```python
from simulate.services.reproducibility_passport import (
    build_replay_plan,
    build_reproducibility_report,
    build_test_execution_passport,
    capture_reproducibility_snapshot,
    diff_passports,
    explain_passport_drift,
    explain_replay_input_drift,
)
```

## What the passport captures

`build_test_execution_passport(test_execution)` returns:

- `execution`: run status, scenario ids, call counts, timestamps, and error reason
- `run_test`: source type, dataset row selection, workspace, and tool-eval flag
- `agent`: agent definition fields, agent version snapshot hash, and simulator hash
- `prompt`: prompt template/version ids and prompt config snapshot hash
- `scenarios`: scenario ids, dataset ids, metadata, and source hashes
- `eval_configs`: eval template/config/mapping/filter fields and criteria hash
- `execution_options`: execution metadata and selected dataset/scenario ids
- `section_hashes`: stable SHA-256 hashes for every section
- `input_fingerprint`: hash of replay-relevant inputs only
- `runtime_fingerprint`: hash of execution status, counters, and timestamps
- `passport_hash`: a top-level hash of the passport schema and section hashes

Secrets are recursively redacted from nested config objects before hashing or
returning the artifact. Free-text prompt and scenario bodies are represented by
hashes instead of copied into the passport.

## How to use it for replay

First build a replay plan:

```python
plan = build_replay_plan(test_execution)

if not plan["can_replay"]:
    logger.warning("simulation_replay_not_ready", extra={"issues": plan["issues"]})
```

The plan includes:

- `replay_key`: stable key for this replay baseline
- `can_replay`: false when required pinned state is missing
- `issues`: blocker/warning/info readiness checks with remediation text
- `replay_inputs`: run, prompt, agent, scenario, eval, and dataset-row ids
- `baseline`: passport hash, input fingerprint, and section hashes

The API-ready report combines the current passport, replay plan, stored
snapshots, input drift, full drift, and score-change diagnosis:

```python
report = build_reproducibility_report(test_execution)
```

The same report is available over HTTP:

```text
GET /simulate/test-executions/{test_execution_id}/reproducibility/
```

Then capture a passport before starting a rerun or regression investigation:

```python
original = build_test_execution_passport(test_execution)
```

After recreating or rerunning the execution, compare the new passport:

```python
rerun = build_test_execution_passport(rerun_execution)
drift = diff_passports(original, rerun)

if drift.has_drift:
    logger.info("simulation_replay_drift", extra=drift.as_dict())
```

For user-facing or self-healing workflows, use the explanation helper:

```python
explanation = explain_replay_input_drift(original, rerun)

if explanation["highest_severity"] == "blocker":
    logger.warning("simulation_replay_inputs_changed", extra=explanation)
```

`explain_replay_input_drift` ignores runtime-only changes such as status and
call counters. Use `explain_passport_drift` when the caller also wants to audit
runtime/output drift.

This split lets the caller decide whether a result changed because of model
behavior or because the replay inputs were no longer identical.

## Why this matters

The same artifact can support several product surfaces:

- rerun preflight checks before comparing eval scores
- self-healing workflows that explain which input section changed
- audit logs for prompt and eval version changes
- bug reports that are useful without exposing raw transcripts or credentials

## Stored snapshots

Execution lifecycle code stores best-effort `start` and terminal `completion`
snapshots in `TestExecution.execution_metadata["reproducibility_passports"]`.
The passport intentionally excludes that internal metadata key while hashing
execution options, so saving a snapshot does not make the next passport drift
against itself.

```python
capture_reproducibility_snapshot(test_execution, "start")
capture_reproducibility_snapshot(test_execution, "completion")
```

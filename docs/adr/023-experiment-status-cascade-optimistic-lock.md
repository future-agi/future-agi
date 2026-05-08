---
status: Known limitation — filed as issue #321
date: 2026-05-08
---

# ADR 023 — Experiment status cascade uses optimistic locking, not `select_for_update`

## Evidence

`futureagi/model_hub/views/experiment_runner.py:323-408`:

```python
# Read without locks - allows concurrent reads
experiment_dataset = ExperimentDatasetTable.objects.filter(id=experiment_dataset_id).first()
...
current_status = experiment_dataset.status      # line 331 — unlocked read
...
updated = ExperimentDatasetTable.objects.filter(
    id=experiment_dataset_id,
    status=current_status,                      # line 407 — optimistic lock
).update(status=StatusType.COMPLETED.value)
```

`futureagi/model_hub/views/eval_runner.py:2932-2935`: every completing eval worker
calls `check_and_update_experiment_dataset_status(self.experiment_dataset.id)`.

## Context

When an experiment runs multiple eval columns in parallel, every eval worker calls
`check_and_update_experiment_dataset_status` when its column finishes. Multiple workers
can arrive simultaneously when their columns complete at the same time.

## Decision

Optimistic locking (`filter(..., status=current_status).update(...)`) is used instead
of `select_for_update()`. Only the first writer that arrives with the correct
`current_status` succeeds (Django `.update()` returns the affected row count). Later
arrivals read a stale `current_status` and their update no-ops.

The cascade to `check_and_update_experiment_status` is intentionally **not** triggered
from within `check_and_update_experiment_dataset_status` for the normal completion path
(see comment at line 416: "We intentionally do NOT cascade").

## Why

`select_for_update()` inside a long-running task (Temporal activity / Celery worker)
held a database row lock for the duration of all sibling tasks, causing connection pool
exhaustion for large experiments. The optimistic lock avoids this.

## Consequences

- Multiple concurrent calls to `check_and_update_experiment_dataset_status` all read
  `current_status=RUNNING` and all run the full column-completeness check independently.
  This is redundant but not harmful — the optimistic write ensures only one succeeds.
- There is a write-after-completion window: a late worker that passes the
  `running_count > 0` check before a sibling's column is written can see a false
  `running_count == 0` and mark the dataset complete prematurely. The column later
  appears in a COMPLETED dataset while it is still RUNNING.
- Filed as issue #320.

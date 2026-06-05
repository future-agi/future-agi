# ADR-032: Dataset auto-eval configuration with debounce protocol

**Status**: Accepted  
**Date**: 2026-05-09  
**PR**: #356  
**Issue**: #74

---

## Context

Users import rows into `model_hub` datasets continuously. Each row insert should
trigger evaluation against the dataset's linked evaluation pipeline. The naive
approach — running an eval job synchronously per row — is prohibitively expensive
(cold-start overhead, LLM API calls, rate limits). We need a way to batch
row-level signals into a single evaluation run per configurable window.

Constraints:
- Evaluation runs are Celery tasks backed by RabbitMQ. They are idempotent but
  not cheap — an eval job cold-starts in ~2 s and may invoke external LLMs.
- Multiple concurrent dataset writes (bulk imports, streaming inserts) must not
  enqueue duplicate eval jobs for the same window.
- If the Celery worker crashes mid-eval, the next signal must still enqueue a new job.
- Different datasets may want different debounce windows and concurrency caps.

---

## Decision

Introduce `DatasetEvalConfig` — a per-dataset record holding eval pipeline
configuration — and a **Redis-based debounce** protocol on the Celery side.

### DatasetEvalConfig model

```python
class DatasetEvalConfig(BaseModel):
    dataset        = ForeignKey(Dataset, unique=True, on_delete=CASCADE)
    eval_pipeline  = ForeignKey(EvalPipeline, null=True, blank=True)
    debounce_seconds = PositiveIntegerField(default=30)  # [5, 3600]
    max_concurrent   = PositiveSmallIntegerField(default=3)  # [1, 50]
    is_active      = BooleanField(default=True)
    filter_tags    = ArrayField(CharField(50), default=list)
```

`DatasetEvalConfig` is org-scoped and enforces a unique active (dataset, pipeline)
pair at the DB level. Soft-delete (`is_active=False`) is used for disable-without-loss.

### Debounce protocol (Celery + Redis)

On each row-insert signal:

1. **Enqueue row ID** — `RPUSH eval:pending:{dataset_id} {row_id}`
2. **NX lock** — `cache.add(eval:lock:{dataset_id}, 1, timeout=debounce_seconds)`
   - If `True`: lock acquired, schedule `flush_eval_queue.apply_async(countdown=debounce_seconds)`
   - If `False`: lock held by a pending flush — skip scheduling, row is already in the list
3. **Flush task** — drains the Redis list atomically (`LRANGE` + `DEL`), groups
   rows into batches, enqueues one eval job per batch, releases lock.

This guarantees **exactly-one flush per debounce window** regardless of how many
concurrent writers call the signal handler.

### Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Synchronous eval per row | O(n) LLM calls; blocks import throughput |
| DB-level trigger + cron flush | Requires DB polling; coarse granularity |
| Celery `chord` / `group` per import batch | Only works for bulk imports — misses streaming row-by-row inserts |
| `django-celery-beat` periodic task | Fixed cadence, not dataset-specific debounce |

---

## Consequences

- **Row loss risk**: Minimal. The Redis list is the durable buffer. If the Celery
  worker crashes after lock acquisition but before flush, the NX lock expires and
  the next row signal reacquires it. The rows in the list survive (Redis AOF).
- **API surface**: `DatasetEvalConfigViewSet` at `/model-hub/dataset-eval-configs/`
  with standard CRUD plus `?dataset_id=` filter.
- **Backpressure**: `max_concurrent` limits eval parallelism per dataset. Without
  this, a large import could flood the eval pipeline.
- **Test coverage**: 6 Hypothesis properties in `model_hub/formal_tests/test_debounce_hypothesis.py`
  covering no-row-loss, exactly-one-flush, deduplication, requeue-after-failure,
  window independence, and empty-flush safety.

---

## Formal verification

### TLA+ spec: `docs/tla/DatasetAutoEval.tla`

Invariants:
- `TypeInvariant` — state variables typed throughout
- `NoRowLost` — every row that enters the pending list is either in a running eval
  or has completed
- `AtMostOneFlusherPerDataset` — the lock ensures at most one active flusher

Properties (liveness):
- `EventuallyFlushed` — `<>(∀ d ∈ ActiveDatasets: pending[d] = <<>>)`

> **TLC note**: Not wired into CI. Run manually:
> ```
> tlc docs/tla/DatasetAutoEval.tla -config docs/tla/DatasetAutoEval.cfg
> ```

### Z3 proofs: `model_hub/formal_tests/test_eval_dag_z3.py`

- `TestDagAcyclicity` — eval dependency graph is a DAG (UNSAT for cycle)
- `TestTopologicalOrder` — topological order exists and is consistent
- `TestLevelAssignment` — Kahn BFS levels are monotone
- `TestCriticalPath` — critical path length upper-bounds eval duration
- `TestDebounceProtocol` — debounce invariants encoded as Z3 constraints

### Hypothesis properties: `model_hub/formal_tests/test_debounce_hypothesis.py`

Six properties, 100–200 examples each:

| Property | Description |
|----------|-------------|
| `test_no_row_lost_across_n_signals` | Every enqueued row appears in the flush output |
| `test_exactly_one_flush_scheduled_per_window` | NX lock gates a single schedule |
| `test_deduplication_preserves_uniqueness` | Dedup does not drop rows |
| `test_requeue_after_workflow_failure_no_loss` | Lock expiry + new signal recovers |
| `test_successive_windows_are_independent` | Window N does not affect window N+1 |
| `test_flush_of_empty_list_is_safe` | Flush with no rows is a no-op |

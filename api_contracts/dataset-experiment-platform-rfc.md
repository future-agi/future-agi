# Dataset and experiment platform RFC

Status: draft for M0 review

Linear: [P-TH-67](https://linear.app/future-agi/project/dataset-and-experiment-platform-20-6328dd2a78cd)

Owner: Tech

Target: 2026-10-30 contract decision

## Scope and status

This RFC defines the smallest compatible contract for dataset lineage and
experiment results. It is a backend/API contract, not a UI approval.

The current stacked backend work is review-only draft work:

- DatasetVersion identity and its compatibility item projection: PR [#3461](https://github.com/future-agi/future-agi/pull/3461).
- Read-only version and provenance endpoints: PR [#3462](https://github.com/future-agi/future-agi/pull/3462).
- ExperimentRun and ExperimentRunItem model/serializer contract: PR [#3460](https://github.com/future-agi/future-agi/pull/3460).
- Storage-free analysis helpers and focused tests: PR [#3459](https://github.com/future-agi/future-agi/pull/3459).

These drafts are not merged or released. They do not provide experiment
execution, external result upload, an analysis route, SDK/MCP support, or a
public UI. Backend drafts remain reviewable while design work proceeds. UI
implementation requires actual Figma designs for the dataset, run, and
analysis flows attached to their Linear issues.

## Decision

Use three durable domain primitives and keep other behavior as adapters or
projections:

1. `DatasetVersion`: an immutable version record for one dataset state.
2. `ExperimentRun`: one execution attempt bound to one dataset version.
3. `ExperimentRunItem`: one case/trial result belonging to one run.

`DatasetVersionItem` is a persisted membership/projection table owned by a
version. Three domain primitives therefore means four database tables, not
three. Its nullable `legacy_row` links to the existing `Dataset -> Row -> Cell`
grid without copying every cell. Do not add separate durable models for splits,
slices, comparisons, trials, evidence, or feature-specific result types in this
MVP.

## Why this shape

Future AGI already has typed dataset tables, prompt and GraphVersion
experiments, evaluator versions, Temporal workers, trace analysis, and
annotation queues. The missing contract is stable version/run identity. Adding
one durable object for each competitor feature would increase migration and
query complexity without fixing that root problem.

## MVP data model

```text
DatasetVersion
  id, dataset_id, parent_version_id, created_at, created_by
  content_hash, description, metadata, source_revision
  items: DatasetVersionItem compatibility projection

DatasetVersionItem (projection attached to DatasetVersion)
  version_id, example_id, external_id, input, expected, metadata
  source_trace_id, source_span_id, content_hash, legacy_row

ExperimentRun
  id, experiment_id, dataset_version_id, task_kind
  status, created_at, started_at, completed_at, metadata
  model_config, prompt_version_id, agent_version_id, evaluator_versions

ExperimentRunItem
  run_id, example_id, trial_number, status, input, expected, output, error
  trace_id, root_span_id, latency_ms, token_usage, cost, score_refs, metadata
```

## Identity and version semantics

- `DatasetVersion.id` identifies one version record. Append-only history is the
  intended contract; the draft models alone do not enforce immutability for
  every ORM write path.
- `DatasetVersionItem.example_id` identifies an item within that version. A
  later import may carry an existing ID forward, but the current MVP does not
  implement cross-version upsert or identity reconciliation.
- Non-empty `external_id` values are unique among active items **within one
  DatasetVersion**. Null and empty values are optional, non-unique identities.
- `content_hash` is the canonical SHA-256 of the current item's `input`,
  `expected`, and `metadata` values. There is no separate attachment-digest
  contract; references embedded in those values are hashed as content.
- `DatasetVersion.parent_version` must refer to a version of the same dataset.
- A version and its active item records are read-only through the current
  compatibility API. Legacy dataset writes are unchanged.

### Splits and metadata selection

There is no `DatasetSplit` model, split-membership column, split endpoint, or
split persistence in this MVP. `DatasetVersionItem.metadata` is retained for
future selection and adapters may accept metadata filters, but the current
version API does not interpret metadata as named splits. Native splits,
persisted filtered views, and import split mapping are follow-up work and must
not be represented as shipped behavior in docs or UI.

## Task adapters and run status

`ExperimentRun.task_kind` reserves `prompt`, `graph`, and `external` adapter
values. The current model/serializer draft stores the run header and item
shape only. It does not execute any adapter, upload external results, or bind a
trace automatically.

When execution is implemented, existing Temporal/Celery workers and caller
environments remain adapters. The core still does not add a workflow engine.
Each adapter must write results through `ExperimentRunItem` and preserve
explicit item states: `pending`, `running`, `completed`, `failed`, or `skipped`.
Run headers use their own lifecycle states: `draft`, `queued`, `running`,
`partial`, `completed`, `failed`, or `cancelled`.

## Current read API

The current compatibility slice is read-only and tenant-scoped:

```text
GET /model-hub/datasets/{dataset_id}/versions/?limit=100&offset=0
GET /model-hub/datasets/{dataset_id}/versions/{version_id}/?limit=100&offset=0
```

The list response returns a bounded page of version metadata and active-item
counts. The detail response returns a deterministic bounded page of active
`DatasetVersionItem` records. Both expose `limit`, `offset`, and `has_more`. The item
payload can include `example_id`, `external_id`, `input`, `expected`,
`metadata`, `content_hash`, `legacy_row`, and optional source trace/span IDs.

Both endpoints require authentication and use the existing organization and
workspace dataset scope. Unknown, deleted, or out-of-scope records are not
exposed. `limit` is constrained to 1–500 and `offset` is non-negative.

The endpoints are present in a review draft, not a released public contract.
OpenAPI, contract coverage, generated frontend contracts, and the generated
MCP catalog must accompany the read API before it is ready for review. SDK
adapters remain follow-up work.

## Current analysis implementation

The current analysis draft is a pure, storage-free service at
`futureagi/model_hub/services/experiment_analysis.py`. It has no DRF route,
OpenAPI operation, SDK method, MCP tool, or UI.

It currently provides:

- filtered item and execution counts;
- assessed score count, average, minimum, maximum, optional pass count/rate,
  and status counts; and
- baseline/candidate alignment by `(example_id, trial_number)` with explicit `improved`,
  `regressed`, `tie`, `missing`, `duplicate`, `invalid`, `not_assessed`,
  `unknown`, `unsupported`, and `error` outcomes.

The service accepts an optional adapter-level `split_names` field for callers
that already have such metadata. This does not create or imply a persisted
split model or split API.

The following are future behavior, not current MVP output: percentiles, trial
mean/variance, trace-link queries, dataset readiness profiling, agent
tool/retrieval analysis, evaluator calibration, saved analysis views, and
case-level CI receipts. Add them only through a later API/data contract review.

## Evidence envelope (future extension)

The run item shape reserves `score_refs`; it does not add a separate score or
evidence table in this MVP. A later evaluator contract may carry an optional
JSON envelope such as:

```json
{
  "evidence_id": "ev_...",
  "evaluator_version_id": "...",
  "judge_trace_id": "...",
  "authority": "observed",
  "confidence": "high",
  "limitations": [],
  "status": "assessed"
}
```

If adopted, `status` must preserve `assessed`, `unknown`, `not_assessed`,
`unsupported`, and `error`. A missing score must not become an implicit zero.

## Compatibility and rollout

The intended rollout is:

1. Keep legacy `Dataset -> Row -> Cell` reads and writes unchanged.
2. Add the version and run records behind the normal migration and review
   process.
3. Introduce a backfill only after identity matching, idempotency, and rollback
   behavior are separately specified and tested.
4. Add execution adapters and API writes only after the read contract is
   accepted.
5. Compare new projections with legacy experiment output before switching any
   UI, SDK, or MCP consumer.
6. Retire duplicated paths only after migration receipts, staged rollout, and
   rollback evidence exist.

The current stack does not implement a backfill or feature flag, or establish
staged-rollout and rollback evidence.

## UI and Figma gate

The [design contract](dataset-experiment-platform-design.md) is a handoff
checklist, not a Figma file. Attach the actual Figma URL/node covering dataset
lineage, run setup, and analysis/comparison to the corresponding Linear issues
before frontend implementation. Include loading, empty, partial, error,
permission, and accessibility states. This does not prevent review of backend
drafts.

User-facing reference docs and runnable tutorials belong in the separate docs
repository, as specified by `CONTRIBUTING.md`. Publish those with the shipped
API, its supported SDK versions, a success example, and a failure-path example;
do not present the current draft endpoints as generally available.

## Non-goals for this MVP

- no new warehouse or analytics storage;
- no new orchestration engine;
- no split or filtered-view model;
- no external result-ingestion endpoint;
- no experiment execution API;
- no analysis API route or query language;
- no separate score/evidence/result tables;
- no mandatory sandbox provider; and
- no full statistical package before stable run identity works.

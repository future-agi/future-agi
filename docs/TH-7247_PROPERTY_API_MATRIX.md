# TH-7247 property, filter, and list API matrix

This is the select-only release-qualification matrix for the property,
filter-value, telemetry-list, and eval-task read subset changed by this PR. It
is deliberately not the complete 290-commit public-action inventory. The full
changed API and frontend-consumer inventory, including graph, navigation,
export, model-hub, annotation, and workflow actions that are not part of the
Cartesian production gate, is maintained in
[`TH7247_INTERACTIVE_READ_MATRIX.md`](TH7247_INTERACTIVE_READ_MATRIX.md). No
property catalog, materialized view, Kafka consumer, span-table rewrite, or
ingestion-path change is included.

The contracts below are implementation inventory and release acceptance
criteria, not evidence of a completed production run. At the superseded core
commit `6f70756d394cec5971db35507e3638f713de3fb0`, the prior isolated production
attempt produced no result JSON. Its artifacts were quarantined when `dev`
advanced, so the complete live Whatfix, Colektia, and Mudflap matrix must run
against the exact successor merge head.

## API inventory

| API | Changed contract/behavior | Pagination and search | Active frontend consumers |
| --- | --- | --- | --- |
| `GET /api/traces/span-attribute-keys/` | Bounded latest-state key discovery for one project or an authorized workspace. Workspace reads traverse at most 64 projects per physical request and preserve all observed key/type lanes across batches. | Signed cursor; `page_size=1..50`; exact-key `q`; `discovery_mode=filter\|eval_mapping`. Partial substring discovery is local over explicitly loaded retained pages. Each explicit Load-more or Retry action makes one physical request; an initial non-empty search can start independent retained and exact lanes. | LLM Tracing and Voice Basic/Query property pickers; Journey Attributes; eval mapping/test mode; Run Insights trace/span tabs; Widget Editor Trace Attributes; alert filters; Custom Columns; Sessions/Users/User Trace; eval-task create/edit drawers. |
| `GET /api/traces/span-attribute-values/` | Legacy compatibility suggestions for one project and exact attribute key. The response is explicitly sampled from a bounded recent six-hour slice; exhaustive retained-history values use dashboard `filter_values`. | No cursor. Required `project_id` and `key`; optional case-insensitive substring `q`; `limit=1..500`. | No current direct frontend caller was found. |
| `GET /api/traces/span-attribute-detail/` | Serves the last complete exact attribute snapshot over its fixed 365-day horizon and may schedule an out-of-band refresh when a snapshot is absent or refresh is requested. It is not an all-retained-history read. | No cursor. Required `project_id` and `key`; optional `refresh`, default `false`. | Journey Attributes. |
| `GET /tracer/dashboard/filter_values/` | One request-owned four-second wall covers authorization, PostgreSQL metadata, ClickHouse reads, and label hydration. Supports custom, system, project, session, eval, annotation, and annotator values without materializing a workspace-wide vocabulary. Workspace/large explicit scopes advance in authorized 64-project batches. JSON arrays resume within a cell, including arrays with more than 500 members. PASS_FAIL eval choices use the public `Passed`/`Failed` labels while structured and scalar output rows share one truth predicate. | Signed cursor; `page_size=1..50`; cursor use requires the same page size; server `search`; `attribute_type`. Each Load more/Retry makes one request. Loaded values survive a bounded fresh-chain Retry. Configured eval/annotation choices preserve typed JSON including `false` and `0`. | LLM Tracing and Voice Basic/Query value pickers; ComplexFilter autocomplete; Widget Editor values; saved filter labels; TaskFilterBar; annotation add-items dialogs. |
| `GET /tracer/dashboard/metrics/` | Callers that do not need custom attributes can set `exclude_custom_attributes=true`, avoiding the legacy capped ClickHouse attribute-catalog scan and workspace project materialization. Widget custom attributes now come from the signed key cursor. | Optional one-based `page`; `page_size=1..200`; `search`, `category`, and `source`; no cursor. Unpaged/unfiltered calls preserve the full cached legacy catalog. Cursor-backed custom attributes use `/span-attribute-keys/`. | Widget Editor, TraceFilterPanel catalog, tracing graphs, annotation rule dialog. |
| `GET /tracer/trace/list_traces/` | Bounded prototype trace selector and relational filter compilation for custom attributes, eval results, and annotation completeness. Metadata needed by classifiers is frozen once per operation instead of re-read per batch. | Numbered pages only: zero-based `page_number`; `page_size=1..500`; no cursor contract. Incomplete proof returns sanitized `503`; unsafe deep pages may return `422`. | Run Insights traces. |
| `GET /tracer/trace/list_traces_of_session/` | Same bounded trace filtering and eval/annotation semantics for Observe/session-scoped trace surfaces. | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`); no silent sampled success unless explicitly requested. | LLM Tracing TraceGrid; task/eval live and test previews; annotation add-items; session trace views. |
| `GET /tracer/trace/list_voice_calls/` | Voice now uses the same bounded property/eval/annotation semantics as traces, including all-configured-label annotation completeness and frozen eval metadata. | Signed row cursor/read-more plus one-based numbered compatibility (`page`, `page_size=1..500`); truthful sampled/degraded metadata. | Voice/Agents grid; eval-task preview; annotation add-items. |
| `GET /tracer/observation-span/list_spans/` | Bounded prototype span filters with authoritative eval/annotation metadata and explicit known-empty annotation semantics. | Numbered pages only: zero-based `page_number`; `page_size=1..500`; no cursor contract. Incomplete proof returns sanitized `503`; unsafe deep pages may return `422`. | Run Insights spans. |
| `GET /tracer/observation-span/list_spans_observe/` | Observe span list uses the same bounded relational filter semantics. | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`). | LLM Tracing SpanGrid; task/eval previews; annotation add-items. |
| `GET /tracer/trace-session/list_sessions/` | Bounded session filters, project-scoped session label hydration, and memoized eval metadata across classifier batches. Rollup-seeded cursor-mode default/date-only candidate ordering is explicitly inexact and disclosed; the numbered candidate-first path can remain exact. | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`). Rollup-seeded cursor responses publish `query_exact=false`, `ordering_exact=false`, and `spans_per_session_candidate` provenance. | Sessions grid and task/eval/annotation flows. |
| `GET /tracer/trace-session/get_session_filter_values/` | Compatibility lookup for distinct session-level values in one project. First/last-message suggestions use a fixed recent 30-day root-span window; session/user IDs come from live dimension tables and have no retained-history date window. | Numbered, zero-based `page`; `page_size=1..500`; optional `search`; no cursor. Columns are `session_id`, `user_id`, `first_message`, and `last_message`. Only first/last-message responses expose a boolean `next`; session/user ID responses return values without a terminal/read-more signal. | No current direct frontend caller was found; the former filter-panel call was removed. |
| `GET /tracer/trace/get_trace_export_data/` | Voice detection routes through the bounded voice list and preserves the legacy CSV schema with a truthful truncation marker. | One bounded page of at most 100 list rows; this is not an exhaustive all-row export. | Export/API consumer; no current direct frontend caller was found. |
| `GET /tracer/eval-task/list_eval_tasks/` | COUNT and OFFSET/LIMIT happen before hydration for exactly translatable numeric/datetime filters and sorts. Only page eval configs are prefetched. | Zero-based `page_number`; `page_size=1..500`; exact total. Arbitrary/text result filters deliberately retain the legacy compatibility fallback. | Eval Tasks list. |
| `GET /tracer/eval-task/list_eval_tasks_with_project_name/` | Same bounded ORM fast path with project name in the response. | Zero-based `page_number`; `page_size=1..500`; exact total. | Organization/workspace eval-task list. |
| `GET /tracer/eval-task/get_usage/` | Bounded newest-row usage aggregation with truthful sampling metadata and indexed task/time lookup. | Presets from 30 minutes through 365 days. Non-aggregation custom bounds must be paired and no wider than 366 days; eval/span aggregation compatibility also accepts one-sided span-date bounds. One-based `page=1..100`; `page_size=1..100` (or legacy `limit` alias); `has_more` cannot continue past page 100. | Eval Task Usage tab. |
| Eval-task historical and continuous row resolution | Span, trace, session, and voice candidates resolve configured eval IDs, eval output metadata, and annotation label IDs once per operation. Cursor reconciliation advances only after a complete classifier proof. | Finite candidate/query budgets; retryable sanitized failure, never partial cursor publication. | Eval-task create/update/unpause execution paths and live previews. |

## Schema and ingestion scope

There is no PostgreSQL span/trace table change and no ClickHouse span/trace
DDL. This PR adds exactly two concurrent partial PostgreSQL indexes on
`EvalLogger` for bounded usage reads:

- `eval_logger_task_created_idx` on
  `(eval_task_id, created_at, id)`;
- `eval_log_task_cfg_created_idx` on
  `(eval_task_id, custom_eval_config, created_at, id)`.

Both indexes apply only where `eval_task_id IS NOT NULL AND deleted = false`.
They add no table, column, or constraint.

The merged `dev` dependency also changes the existing ClickHouse
`usage_apicalllog.eval_score` materialized expression so scalar and structured
eval outputs share one numeric score and restores its `idx_eval_score` skip
index. ClickHouse requires the index to be dropped before `MODIFY COLUMN` and
re-added afterward. Historical parts are populated only by the separate,
explicitly authorized `backfill_eval_score` command; that command can issue
DROP/MODIFY/ADD/MATERIALIZE mutations and is **not** part of this release
qualifier. The SELECT-only pod must retain
`NO_STARTUP_DB_MUTATIONS=true` and `STARTUP_DB_MUTATION_MODE=disabled`, and the
harness must reject any mutation dispatch. Thus qualification neither applies
DDL nor runs/materializes the backfill; deployment/backfill is a distinct
reviewed operation.

## Release qualification acceptance matrix

The frozen successor harness statically schedules 503 endpoint-specific lanes:
all 15 direct GET route contracts, all applicable F0-F7 cells and seven windows,
prototype and Observe pagination, repeats, fencing, the 65-project boundary,
and within-cell continuation for arrays beyond 500 members and 65 KiB. Its
source map, execution graph, request/ClickHouse ceilings, and mutation
tripwires pass offline verification. That proves the test program, not the
production data outcome: there is still no successful successor-merge-head
production result JSON, so every live population, correctness, and latency
gate below remains pending until the one-shot SELECT-only run records it.

The API inventory above has 16 public GET paths because
`/api/traces/span-attribute-detail/` is intentionally inventory-only in this
gate: it can schedule an out-of-band snapshot refresh and is recorded as
`boundary.span_attribute_detail.not_exercised`. The other 15 paths are the
exact `DirectDRFClient.ROUTES` set. Thus “15” is a deliberate executable-route
count, not a missing API row.

### Route applicability

- The seven retained windows and F0-F7 apply to trace, span, session, and voice
  row-list routes where the target population exists. Prototype trace/span
  routes require a populated `project_version_id` and use numbered pages;
  Observe routes use signed cursors.
- `get_usage` must cover the same seven exact windows using paired frozen
  `start_date`/`end_date` values, but uses task/eval aggregation lanes rather
  than F0-F7.
- Attribute keys, dashboard values/metrics, compatibility session values,
  eval-task lists, and bounded export have endpoint-specific pagination and
  repeatability checks; they do not accept the same date/F0-F7 Cartesian
  matrix.
- Historical/continuous resolution is not a public GET route. SELECT-only
  qualification may exercise candidate/classifier reads, but must not call
  create/update/unpause, reconciliation, cursor advancement, or scheduling
  operations.

The applicable row-list and `get_usage` acceptance windows are:

| UI period | Qualification interval |
| --- | --- |
| Recent | 1 hour |
| Today | 24 hours |
| 7D | 7 days |
| 30D | 30 days |
| 3M | 90 days |
| 6M | 180 days |
| 12M | 365 days |

Required population profiles:

- **Dense:** Whatfix trace data; page one, page two, page four/former late-page
  failure boundary, and at least ten distinct key cursor advances.
- **Sparse:** Colektia/Colly trace data; empty advancing checkpoints, rare
  keys/values, truthful exhaustion, and repeatability.
- **Voice:** Mudflap voice data; property parity plus eval/annotation positive
  and negative membership.

Required filter combinations for applicable trace, span, session, and voice
row-list routes:

| ID | Filter cell |
| --- | --- |
| F0 | Date/project only |
| F1 | System field |
| F2 | Sparse custom property/value |
| F3 | Dense custom property/value |
| F4 | System plus custom property |
| F5 | `has_eval=true\|false` and exact eval value |
| F6 | `has_annotation=true\|false`; positive requires every configured label |
| F7 | Custom property plus eval plus annotation conjunction |

For each applicable row-list cell, release qualification must check the first
two positive pages and continue to pN or a truthful complete terminal. Signed
cursor routes must prove cursor advancement; numbered prototype routes must
prove stable page boundaries or a truthful finite `422`/`503`. Exact-order
routes must prove no duplicate/gap publication. When a session response carries
inexact flags and `spans_per_session_candidate` provenance, qualification must
instead validate its disclosed candidate ordering and the exact membership of
every published row. All routes must prove
project/workspace fencing, exact positive/negative filter membership, and an
identical repeat request. A missing positive configuration, conjunction, or
second page is a named population gap, not a passing cell.

## Latency and read-more contract

These are release gates, not claims that the pending live matrix has passed.

- Active property-key and dashboard-value picker **physical requests** must
  complete in under five seconds. The dashboard-value server read wall is four
  seconds; the active picker transport timeout is 4.8 seconds.
- Trace/span/session/voice list and eval/annotation filter requests must
  complete in under 9.8 seconds (the product requirement is under 10 seconds).
- Compatibility span values, dashboard metrics, compatibility session values,
  warm/read-only attribute detail, eval-task list/usage, and bounded export each
  require their own under-9.8-second endpoint gate where safely applicable; no
  successful live artifact currently proves those gates.
- Property-key and dashboard-value Load more/Retry actions are explicit,
  single-flight, one-physical-request actions. Observe list helpers may issue an
  initial request plus up to twelve bounded continuations inside one visible
  page attempt and its shared 9.5-second action wall. Window focus, remount,
  reconnect, inertial scrolling, and React Query refetch do not replay cached
  infinite pages.
- A malformed/repeated picker cursor fails safely, preserves already loaded
  rows, and offers one bounded fresh-chain retry. It never masquerades as
  exhaustion. Observe list continuation failures preserve published rows and
  expose their bounded Continue/Retry state.
- Retained cursor-catalog and exact row-list request/query/page budgets are work
  bounds, not hidden history or result caps. Cursor-capable endpoints carry a
  signed continuation when more retained data exists. Compatibility samples,
  fixed-horizon snapshots, usage samples, and bounded exports retain the
  explicit windows/caps documented above; numbered endpoints expose their
  documented bounded terminal or error contract.

## Explicit release boundaries

- Key `q` is an exact-key accelerator. Full substring discovery requires
  explicit retained-catalog pagination; this PR does not add a substring
  index.
- `/api/traces/span-attribute-values/` is a sampled recent six-hour
  compatibility endpoint, not retained-history value pagination.
- A cold `/api/traces/span-attribute-detail/` read is not safely qualifiable by
  the current SELECT-only harness: a cache miss may take a cache claim and
  dispatch exact-snapshot work even when `refresh=false`. The qualification
  tripwires must not be weakened; this route needs a pre-existing readable
  snapshot or a genuine no-schedule read mode before it can receive a live
  SELECT-only pass.
- `/tracer/trace-session/get_session_filter_values/` is numbered compatibility
  pagination with no active frontend caller, and trace export is one bounded
  CSV page rather than an exhaustive cursor walk.
- Cursor-backed categorical annotation values exhaust configured choices and
  truthfully report stored Score history as sampled. Exhaustive stored-only
  Score history needs the later composite-index/catalog PR.
- Dataset/simulation legacy value endpoints are outside this tracing/voice
  release matrix.
- The later PostHog-style stacked PR will add independent ingestion-fed lookup
  storage. It will not modify the existing spans table and will be designed to
  avoid the prior materialized-view ingestion OOM failure mode.

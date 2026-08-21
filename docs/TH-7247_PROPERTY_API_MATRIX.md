# TH-7247 property, filter, and list API matrix

This is the select-only release-qualification matrix for the property,
filter-value, telemetry-list, and eval-task read subset changed by this PR. It
is deliberately not the complete public-action inventory. The full changed API
and frontend-consumer inventory, including graph, navigation, export,
model-hub, annotation, and workflow actions that are not part of the Cartesian
production gate, is maintained in
[`TH7247_INTERACTIVE_READ_MATRIX.md`](TH7247_INTERACTIVE_READ_MATRIX.md).

> **Current freeze, 2026-08-18.** The reviewed DEV deployment reads canonical
> source database `futureagi` and writes only the fresh six-table target
> `th7247_catalog_dev_kartik_0817j`. Canonical PostgreSQL ownership resolved 90
> eligible workspaces and 263 projects; 289 legacy projects without a workspace
> were excluded. All 90 workspaces have active qualified catalog lineage. The
> six-table target contains 89,894 physical definition rows and 2,151,977
> physical value rows. DEV backend readers are enabled for that exact
> 90-workspace set. Source
> ClickHouse/PostgreSQL access remained SELECT-only and no existing application
> table or data was changed. No production deployment, backfill, DDL, or DML
> occurred. An isolated server-read-only production SELECT matrix was
> authorized and is summarized below; it is query-builder evidence, not the
> still-pending authenticated dense Whatfix, sparse Colektia, and Mudflap voice
> production release matrix. Authenticated DEV reads across Trace, Span,
> Session, User, Voice, and Prompt sources returned HTTP 200 in 1.02–1.22
> seconds; a custom-value page returned 17 values in 1.19 seconds.

## Logical property model

The product exposes one logical **Property Registry** and one common
property/value search contract. Every definition family is projected into one
ClickHouse definition table; high-cardinality facts and most values stay in
their native stores.

| Logical family                                  | Registry definition                                                                                                                                  | Exact value/filter source                                                                                                                             |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| System properties                               | `property_definition_catalog`; stable namespaced definitions such as `system_attribute:traces:model`                                                 | Canonical hot columns or native resource identity; observed hot values may be accelerated in `span_attribute_value_catalog`.                          |
| Custom span attributes                          | `property_definition_catalog`; project-observed namespaced key/type definitions                                                                      | `span_attribute_value_catalog` and canonical span attribute maps. A customer key named `model` remains distinct from `system_attribute:traces:model`. |
| Evals                                           | A project-bound eval configuration is `eval_config:<uuid>`; template-level catalogs use the deliberately distinct `eval_template:<uuid>` definition. | EvalLogger/native eval result tables and their exact relational filter compiler.                                                                      |
| Annotations                                     | Annotation label is the property definition                                                                                                          | Score/native annotation facts; categorical suggestions page the label's finite configured options without scanning Score history.                     |
| Dataset columns                                 | Dataset plus column identity is the property definition                                                                                              | Dataset Row/Column/Cell tables and the exact dataset snapshot reader.                                                                                 |
| Prompt, dataset, project, and similar resources | Usually a generic resource property whose values are canonical ID plus display label; the individual objects are not separate property definitions   | Their native PostgreSQL resource tables, preserving identity across renames.                                                                          |

The registry identity therefore needs a source namespace/kind, stable property
ID, data type/operator capabilities, authorization-derived organization,
workspace and optional project scope, monotonic source version, and tombstone.
A bare `deleted` boolean is insufficient for observed values shared by many
facts: an implementation that materializes an exact mutable vocabulary needs a
contribution/refcount ledger or an immutable epoch/fence. This implementation
avoids that requirement for annotations by treating configured label options
as definition metadata and keeping Score facts in their native store. Public
filters compile to the native source adapter; the registry is a
discovery/search read model, not a replacement fact store.

The current implementation makes that identity executable. Every cursor-mode
metric definition carries a stable `property_id` and `property_kind`.
`/tracer/dashboard/filter_values/` accepts the same `property_id`, verifies any
legacy `metric_name`/`metric_type` supplied beside it, and dispatches to the
native value adapter. The shared frontend hook sends the namespaced identity
for Trace/Span/Voice/Sessions/Users, Tasks, alerts, annotation queue pickers,
dashboard/widget value hydration, and dataset-column pickers. Persisted filters
retain legacy native fields for rolling compatibility. Unsupported
cross-adapter filters and breakdowns fail closed.

| Consumer family                                                                     | Common registry behavior                                                                                             | Deliberate native boundary                                                                                    |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Observe Trace/Span/Voice, Sessions/Users, Tasks, Alerts, annotation queue selection | Shared definition normalization plus namespaced value lookup; system/custom/eval/annotation collisions cannot alias. | Row membership still compiles against span hot columns, typed attribute maps, EvalLogger, and Score.          |
| Dashboard/widget property and value pickers                                         | Same `property_id` definitions and `/filter_values/` adapters.                                                       | Aggregation/statistics stay in the dashboard query engine.                                                    |
| Eval mapping and Journey attributes                                                 | Custom keys are normalized as `custom_attribute:<key>` and use the exact retained key cursor.                        | Mapping/detail reads need attribute type/statistics contracts absent from the generic registry.               |
| Dataset filters and Ground Truth                                                    | Dataset columns are `dataset_column:<column UUID>`; manual/query value lookups use the dataset-column adapter.       | Row/cell pagination and mutation fencing remain in the exact dataset snapshot reader.                         |
| Prompt/model/system dimensions                                                      | Stable system definition IDs include their catalog namespace, for example `system_attribute:traces:model`.           | Canonical filtering remains on the corresponding hot column or native resource identity.                      |
| AI grounding and internal RCA/context tools                                         | Consume the same logical kind/name and validate against authorized native results.                                   | They retain purpose-specific exact reads; occurrence counts and trace context are not property-registry data. |

`GET /tracer/dashboard/metrics/?cursor_mode=true` is the new unified definition
reader. It keyset-paginates one activated `property_definition_catalog`
snapshot and does not assemble definition families in the frontend. Optional
`role=metric|dimension` scopes both category counts and page membership, so a
dashboard metric search cannot count hidden dimension rows or page through an
unrenderable cursor chain. The
non-cursor response remains a deprecated compatibility shape and does not
inherit the cursor reader's scale claim. Dataset and simulation value adapters
remain native reads. Their Kartik-account DEV UI/API smoke is complete, while
the named release matrix remains pending.

### Registry synchronization strategy

The implemented least-change path is a hybrid:

- The collector publishes observed custom/system attribute values through the
  durable Kafka path after the canonical span write. The catalog consumer
  writes only the isolated value and delivery/control tables.
- System, attribute, eval-template, eval-config, simulation-eval,
  annotation-label, and dataset-column definitions are reconciled into
  `property_definition_catalog` through the same revision envelope.
- Initial backfill is an explicit, bounded operator action. Steady state uses
  exactly one default-off Temporal schedule at the env-backed
  `PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS` interval (default **120
  seconds**) on the
  dedicated `property_catalog_dev_sidecar` queue. A sidecar admits exactly one
  organization/workspace allowlist entry, replica one, and no overlapping run;
  another workspace requires a separate sidecar, queue, and durable volume.
- The scheduled request is resolved by the durable lifecycle in **AUTO** mode:
  normal runs are incremental, while a full repair is selected when the active
  lineage anchor is at least 24 hours old or the lineage reaches 2,048 active
  revisions. There is no second daily cron racing the incremental schedule.
- Dataset cells, trace facts, scores, and prompt executions remain in their
  native indexed stores. The common values API dispatches to the correct
  adapter; it does not duplicate every fact into the definition catalog.

The reviewed DEV rollout keeps its canonical spans source and isolated catalog
target in different databases. Before constructing a target client, inspecting
schema, reading source spans, acquiring a lease, or writing, it proves the exact
allowlisted project set in one read-only repeatable-read PostgreSQL snapshot
through `public.tracer_project` and `public.accounts_workspace`. Duplicate or
missing projects, a project organization/workspace mismatch, or a workspace
organization mismatch fails closed. The same ownership is revalidated inside
the revision snapshot and immediately before source, relational, publish,
fence, and activation boundaries; an immutable authorization/execution proof
binds that ownership to the reviewed request. Soft-deleted projects remain
eligible only so full repair can publish tombstones, and they must pass the same
exact ownership proof.

A tombstone is exact for a single definition such as a dataset column. It is
not exact for a shared observed value: deleting one trace containing a model
must not hide that value while other traces still contain it. Shared mutable
vocabularies therefore require a contribution/refcount ledger or a fenced
immutable epoch. Annotation suggestions deliberately avoid a materialized
mutable vocabulary and use finite configured label options.

## Current unified API additions

| API                                                                | Current behavior                                                                                                                                                         | Window/read-more contract                                                                                                                                                                                | Active frontend consumer                                                                                                                                                 |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET /api/traces/span-attribute-keys/`                             | Retained compatibility discovery for specialized attribute-only callers; it is not the unified definition source.                                                        | Signed cursor, `page_size<=50`; four-second public wall.                                                                                                                                                 | Eval mapping, Run Insights, and compatibility/fallback attribute consumers.                                                                                              |
| `GET /tracer/dashboard/metrics/?cursor_mode=true`                  | Reads all authorized system, custom-attribute, eval-template/config, annotation, and dataset-column definitions from one activated ClickHouse table.                     | Signed keyset cursor, env-bounded page size (reviewed default 50), filter/scope-bound activation snapshot, `page_size+1` proof, and separately env-bounded endpoint/browser walls.                       | `usePropertyCatalog` consumers: Trace/Span/Voice/Session/User property pickers, Primary Graph, Widget Editor, automation rules, Journey Attributes, and dataset pickers. |
| `GET /tracer/dashboard/filter_values/`                             | Accepts the selected stable `property_id` and dispatches to the authorized native adapter. `system_attribute:traces:model` and `custom_attribute:model` remain distinct. | Signed cursor, env-bounded page size (default 50); fixed and large project scopes; default four-second server wall. Dataset-column pickers expose each advancing cursor as an explicit Load more action. | Trace/Voice Basic and Query pickers, ComplexFilter, widgets and saved-value hydration, tasks/alerts/annotation queues, and dataset-column pickers.                       |
| `GET /model-hub/develops/{dataset_id}/get-dataset-table/`          | Adds `exact_snapshot` and a signed revision-bound cursor with deterministic row/column order and drift rejection.                                                        | One explicit env-bounded page per action; separately configurable server/FE walls; env-bounded column/cell/byte caps; `409` drift, `413` shape limit, `503` unavailable.                                 | Eval Ground Truth dataset import.                                                                                                                                        |
| `GET /simulate/run-tests/?summary=true`                            | Returns the compact run-test identity/configuration shape needed by selectors without hydrating nested execution/call payloads.                                          | Numbered `page`/`limit`, env-bounded `limit` with default 100, and one default 9.5-second list wall. DEV returned 25 rows in 103 ms and 50 rows in 120 ms.                                               | Run Simulation and Eval Settings simulation selectors.                                                                                                                   |
| `GET /simulate/run-tests/{run_test_id}/preview-executions/`        | New exact execution preview.                                                                                                                                             | Signed descending keyset cursor, env-bounded page size, immutable snapshot metadata, explicit Load more, and configured server/browser walls.                                                            | Simulation test mode.                                                                                                                                                    |
| `GET /simulate/test-executions/{test_execution_id}/preview-calls/` | New exact call preview, additionally bound to the selected `run_test_id`.                                                                                                | Same signed exact cursor/read-more contract as execution preview; `409` requires restart after drift.                                                                                                    | Simulation test mode.                                                                                                                                                    |
| `PUT/PATCH /model-hub/annotations-labels/{id}/`                    | Annotation label type is immutable, matching the existing UI and API contract.                                                                                           | Point mutation; no telemetry window or pagination claim.                                                                                                                                                 | Annotation label editors.                                                                                                                                                |
| `POST /model-hub/eval-playground/`                                 | A supplied `call_id` now requires and is tenant/workspace-bound to `run_test_id`.                                                                                        | Point evaluator action, not a list/read-more route; evaluator latency remains outside the under-ten-second read gate.                                                                                    | Simulation test mode and eval playground.                                                                                                                                |

`GET /model-hub/develops/{experiment_dataset_id}/get-experiment-dataset-table/`
shares the expanded response serializer, so generated clients see optional
exact-pagination metadata fields, but its runtime did **not** gain exact mode.
It now counts and slices the ordered PostgreSQL queryset before hydrating only
the requested cells, with `page_size<=100`, a 100,000-row offset ceiling,
projection/response caps, and one 8.5-second shrinking read wall. It still has
no signed cross-request snapshot cursor and remains pending endpoint-scale DEV
evidence.

### Runtime tuning

Operational latency, page, scan, and batch controls are parsed and fail-fast
validated in `tfc/settings/settings.py`. Catalog and dataset consumers build
immutable snapshots through the shared declarative
`tfc/settings/runtime_limit_loader.py`, so numeric parsing and range validation
are not duplicated. Each setting has the reviewed behavior as its default and
can be overridden by an environment variable without rebuilding the service.
Invalid or unsafe backend overrides fail during settings import; invalid
frontend overrides fall back to the documented default.

| Area                       | High-impact environment settings and defaults                                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Shared backend bounds      | `INTERACTIVE_READ_DEFAULT_WALL_MS=8500`; `INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS=9500`; `INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE=100`; `CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS=30000`                                             |
| Property and value pickers | `DASHBOARD_FILTER_VALUE_WALL_MS=4000`; `DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE=50`; `FILTER_VALUE_READ_TIMEOUT_MS=8000`; `FILTER_VALUE_CURSOR_MAX_QUERIES=6`; `FILTER_SELECTOR_QUERY_TIMEOUT_MS=2500`; `FILTER_SELECTOR_MAX_THREADS=1` |
| Graph and monitor reads    | `MONITOR_GRAPH_CH_TIMEOUT_CAP_MS=6000`; `MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS=1000`; `GRAPH_BACKGROUND_WALL_MS=30000`; `GRAPH_EVENT_LIMIT=2000`                                                                                  |
| Bulk annotation selection  | `BULK_SELECTION_DEADLINE_MS=15000`; `BULK_SELECTION_MAX_CAP=10000`; `ANNOTATION_QUEUE_DEADLINE_BULK_BATCH_SIZE=500`                                                                                                                  |
| Eval/simulation/dataset UI | `EVAL_METRIC_MAX_WINDOW_DAYS=365`; `SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE=50`; `SIMULATION_PREVIEW_MAX_PAGE_SIZE=50`; `DATASET_ROW_ADJACENCY_MAX_ROWS=50`                                                                             |
| Catalog reconciliation     | `PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS=120`; `PROPERTY_CATALOG_REVISION_LEASE_SECONDS=600`; `PROPERTY_CATALOG_DEV_STANDARD_MAX_WALL_MS=100000`; `PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS=1200000`                  |
| Catalog source/publisher   | `PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS=8.5`; `PROPERTY_CATALOG_INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS=540`; `PROPERTY_CATALOG_POSTGRES_STATEMENT_TIMEOUT_MS=8000`; `PROPERTY_CATALOG_PUBLISHER_WALL_MS=8500`            |
| Frontend runtime config    | Mirrors the relevant backend bounds with `VITE_` names, including request walls, max page/window sizes, simulation page size, ground-truth page size, and dataset adjacency rows.                                                    |

The frontend values are emitted into `window.__FUTURE_AGI_CONFIG__` by
`docker-entrypoint.sh`, so deployment overrides apply at container startup.
Paired backend/frontend bounds must be changed together in deployment settings;
malformed browser overrides fall back to their reviewed defaults.
Wire versions, cursor tuple widths, digest sizes, schema versions, and absolute
safety envelopes remain named code invariants: making those deployment knobs
would create incompatible cursors or weaken reviewed safety boundaries.

The 2026-08-19 local rerun includes 306 property-catalog/dataset-limit tests and
802 trace-filter/graph/deadline tests. The annotation/bulk matrix completed 295
tests with ten explicit ClickHouse integration skips; thirteen additional
dataset-add cases could not start because the isolated local ClickHouse service
was unavailable, not because of an assertion failure. Earlier current-tree
gates also include the 114-test offline qualifier suite. The Go property catalog
package passes under the race detector and `go vet`. The
machine inventory proves 65 impacted operations (32 direct plus 33
transitive-only) through 48 changed definitions. These local gates prove
implementation and harness contracts, not named-customer endpoint latency.

The final integration pass also has 200 request-wall/error-boundary tests, 166
bounded adjacent-read backend tests, 153 active frontend read-consumer tests,
and 112 generated contract tests green. Charts now performs runtime query
validation and publishes a shaped single/all-system/eval-series response under
one outer 9.5-second wall. Project, automation-rule, eval-metric, eval-task
detail/log, experiment-row, and dataset-point consumers use one env-configured
visible-action wall, preserve prior truth, reject malformed payloads, and offer
Retry. The 14 changed eval-task, automation, and simulation mutation
operations remain outside the interactive-read SLA pending mutation-specific
idempotency or asynchronous acceptance evidence.

The current `0817j` activation contains exactly the six isolated catalog
tables. Its project-scoped DEV smoke covered Observe property/value search,
cursor continuation, Primary Graph, Eval Settings, annotation automation,
dashboard/widget selectors, dataset selection, and both simulation selectors.
For project `2843b914-d1f7-4ea0-869d-77fa93ce45dc`, custom key
`ai_interruption_rate` and the three project-owned annotation labels were
present while unrelated project-only properties were absent. The frontend
picker regression suite passes 135 tests. On deployed review head
`df068126760952ab8bb69f15097d6d6ff121b5a9`, the Dashboard All picker exposes
one unified pagination action: one click advances both internal streams and
remains stable without replay. A controlled DEV pass also switched through
All, System, Evals, Annotations, Attributes, and back to All while preserving
the same server-provided category totals; selecting a category no longer
recomputes the sidebar counts from that category's page. The focused regression
suite is 19/19. A later authenticated DEV pass preserved
`All/System/Evals/Annotations/Attributes = 275/53/0/0/222` across every
category transition and exposed one dashboard continuation action. Dashboard
metric mode now sends `role=metric`, binding its exact count and signed cursor
to the same aggregatable rows the picker renders. The authenticated source-bound
dense/sparse/voice nine-window HTTP matrix, full named-population evidence, and
all production catalog deployment/backfill work remain pending. Production
changes require a complete exact-current-source API gate and separate user
approval.

The later DEV backend review head `7f8f833079f9405f5cb520d6c86b075b83a99149`
also closes an exact-id annotation-value scope mismatch. Project-scoped catalog
reads excluded a workspace-level annotation label unless an observability Score
created that project's visibility binding, but the compatibility value adapter
previously returned configured choices when a caller supplied the label UUID
from another project. The value adapter now performs a stop-after-one indexed
`Score(tracer_project_id, label_id)` existence SELECT and applies the same
visibility rule before publishing configured values. Fourteen directly focused
tests and 42 adjacent configured-value/batched-scope tests pass. Authenticated
Kartik-account DEV A/B calls for both categorical labels in
project `2843b914-d1f7-4ea0-869d-77fa93ce45dc` returned their two exact choices
in 2.956s and 2.874s; the same label IDs under unrelated project
`082b1901-0710-46d9-8332-a1e83537b61e` returned terminal exact empty pages in
3.139s and 3.020s. No catalog, Score, or application data was changed.

The supplementary current-source production SELECT matrix ran the exact
trace/span/session cursor routing for Whatfix dense and Colektia sparse across
all nine windows and default/custom/system/combined profiles. Its 493 recorded
primary/repeat/continuation lanes had zero failures, zero calls at or above ten
seconds, zero cursor overlaps, zero cursor stalls, zero unstable repeats, and
zero all-empty chains at the sample limit; the slowest lane was 7,357.06 ms.
The execution used a server-enforced read-only ClickHouse role, accepted only
`SELECT`/`WITH ... SELECT`, had no service-account token or PostgreSQL
credentials, and issued no DDL/DML. Result JSON SHA-256 is
`8f28e92fd3ec027de1303537c7b45e742ec364eea5843fb7c0b99f7775a4c9c3`.
Because the historical projects lack current PostgreSQL authorization rows,
this is reproducible query-builder/SELECT evidence rather than authenticated
public-API proof. See
[query_builder_matrix.py](../futureagi/scripts/th7247_current_select_qualifier/query_builder_matrix.py)
and the detailed evidence boundary in
[`TH7247_INTERACTIVE_READ_MATRIX.md`](TH7247_INTERACTIVE_READ_MATRIX.md#current-source-production-select-matrix-supplementary-not-http-sign-off).

DEV graph/dashboard preparation had a separate deployment-skew failure: the
backend scheduled `tracer.refresh_exact_aggregation_snapshot` to `tasks_xl`,
whose old worker had not registered that activity. DEV now routes it to the
dedicated additive `exact_aggregation` worker built from the matching core/EE
stack. A fresh filtered graph activity completed successfully; its individual
ClickHouse SELECTs took 4.12–49.84 ms and the full activity completed in about
0.3 seconds. The existing shared worker and database were not modified.

The base API optimization at `e25fcf1286d318bc9694e786b6fb2c3c26daa1a8`
did not include the unified catalog. Catalog implementation parent
`a0b7eb6f28471cd40996caf392934a077b95cedc` remains only a historical evidence
anchor; the current freeze must be qualified independently.

The contracts below are implementation inventory and release acceptance
criteria, not evidence of a completed production run. At the superseded core
commit `6f70756d394cec5971db35507e3638f713de3fb0`, the prior isolated production
attempt produced no result JSON. Its artifacts were quarantined when `dev`
advanced, so the complete live Whatfix, Colektia, and Mudflap matrix must run
against the exact current freeze after DEV qualification and separate approval.

## API inventory

| API                                                        | Changed contract/behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Pagination and search                                                                                                                                                                                                                                                                                                                                   | Active frontend consumers                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/traces/span-attribute-keys/`                     | Bounded latest-state key discovery for one project or an authorized workspace. Workspace reads traverse at most 64 projects per physical request and preserve all observed key/type lanes across batches.                                                                                                                                                                                                                                                                                                                                            | Signed cursor; `page_size=1..50`; exact-key `q`; `discovery_mode=filter\|eval_mapping`. Partial substring discovery is local over explicitly loaded retained pages. Each explicit Load-more or Retry action makes one physical request; an initial non-empty search can start independent retained and exact lanes.                                     | Compatibility and specialized attribute-only consumers: Journey attribute detail/mapping, eval mapping/test mode, Run Insights trace/span tabs, Custom Columns, Sessions/Users/User Trace, and eval-task create/edit drawers. Activated Trace/Span/Voice Basic and Query pickers no longer read this endpoint except during the typed rolling-deploy fallback. |
| `GET /api/traces/span-attribute-values/`                   | Legacy compatibility suggestions for one project and exact attribute key. The response is explicitly sampled from a bounded recent six-hour slice; exhaustive retained-history values use dashboard `filter_values`.                                                                                                                                                                                                                                                                                                                                 | No cursor. Required `project_id` and `key`; optional case-insensitive substring `q`; `limit=1..500`.                                                                                                                                                                                                                                                    | No current direct frontend caller was found.                                                                                                                                                                                                                                                                                                                   |
| `GET /api/traces/span-attribute-detail/`                   | Serves the last complete exact attribute snapshot over its fixed 365-day horizon and may schedule an out-of-band refresh when a snapshot is absent or refresh is requested. It is not an all-retained-history read.                                                                                                                                                                                                                                                                                                                                  | No cursor. Required `project_id` and `key`; optional `refresh`, default `false`.                                                                                                                                                                                                                                                                        | Journey Attributes.                                                                                                                                                                                                                                                                                                                                            |
| `GET /tracer/dashboard/filter_values/`                     | One request-owned four-second wall covers authorization, PostgreSQL metadata, ClickHouse reads, and label hydration. Supports custom, system, project, session, eval, annotation, and annotator values without materializing a workspace-wide vocabulary. Workspace/large explicit scopes advance in authorized 64-project batches. JSON arrays resume within a cell, including arrays with more than 500 members. PASS_FAIL eval choices use the public `Passed`/`Failed` labels while structured and scalar output rows share one truth predicate. | Signed cursor; `page_size=1..50`; cursor use requires the same page size; server `search`; `attribute_type`. Each Load more/Retry makes one request. Loaded values survive a bounded fresh-chain Retry. Configured eval/annotation choices preserve typed JSON including `false` and `0`.                                                               | LLM Tracing and Voice Basic/Query value pickers; ComplexFilter autocomplete; Widget Editor values; saved filter labels; TaskFilterBar; annotation add-items dialogs.                                                                                                                                                                                           |
| `GET /tracer/dashboard/metrics/`                           | `cursor_mode=true` is the unified definition contract and reads all definition families from one activated `property_definition_catalog`; the non-cursor assembler remains only for rolling compatibility.                                                                                                                                                                                                                                                                                                                                           | Unified mode: signed keyset cursor, `page_size=1..50`, server-side `search`, `category`, optional `role=metric                                                                                                                                                                                                                                          | dimension`, `source`, project/agent scope, exact scoped category breakdowns, and no expensive exact result total. Role is cursor-bound and scopes both rows and counts. Trace/Span/Voice Basic and Query pickers request 20 rows and expose one continuation action. Compatibility mode retains its one-based page/unpaged shape.                              | `usePropertyCatalog` powers activated Trace/Span/Voice Basic and Query pickers, Widget Editor, tracing graphs, Journey Attributes, automation rules, and dataset/property pickers. `/span-attribute-keys/` is reached only for the typed `property_catalog_not_ready` fallback or specialized attribute-only flows. |
| `GET /tracer/trace/list_traces/`                           | Bounded prototype trace selector and relational filter compilation for custom attributes, eval results, and annotation completeness. Metadata needed by classifiers is frozen once per operation instead of re-read per batch.                                                                                                                                                                                                                                                                                                                       | Numbered pages only: zero-based `page_number`; `page_size=1..500`; no cursor contract. Incomplete proof returns sanitized `503`; unsafe deep pages may return `422`.                                                                                                                                                                                    | Run Insights traces.                                                                                                                                                                                                                                                                                                                                           |
| `GET /tracer/trace/list_traces_of_session/`                | Same bounded trace filtering and eval/annotation semantics for Observe/session-scoped trace surfaces.                                                                                                                                                                                                                                                                                                                                                                                                                                                | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`); no silent sampled success unless explicitly requested.                                                                                                                                                                                          | LLM Tracing TraceGrid; task/eval live and test previews; annotation add-items; session trace views.                                                                                                                                                                                                                                                            |
| `GET /tracer/trace/list_voice_calls/`                      | Voice now uses the same bounded property/eval/annotation semantics as traces, including all-configured-label annotation completeness and frozen eval metadata.                                                                                                                                                                                                                                                                                                                                                                                       | Signed row cursor/read-more plus one-based numbered compatibility (`page`, `page_size=1..500`); truthful sampled/degraded metadata.                                                                                                                                                                                                                     | Voice/Agents grid; eval-task preview; annotation add-items.                                                                                                                                                                                                                                                                                                    |
| `GET /tracer/observation-span/list_spans/`                 | Bounded prototype span filters with authoritative eval/annotation metadata and explicit known-empty annotation semantics.                                                                                                                                                                                                                                                                                                                                                                                                                            | Numbered pages only: zero-based `page_number`; `page_size=1..500`; no cursor contract. Incomplete proof returns sanitized `503`; unsafe deep pages may return `422`.                                                                                                                                                                                    | Run Insights spans.                                                                                                                                                                                                                                                                                                                                            |
| `GET /tracer/observation-span/list_spans_observe/`         | Observe span list uses the same bounded relational filter semantics.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`).                                                                                                                                                                                                                                                 | LLM Tracing SpanGrid; task/eval previews; annotation add-items.                                                                                                                                                                                                                                                                                                |
| `GET /tracer/trace-session/list_sessions/`                 | Bounded session filters, project-scoped session label hydration, and memoized eval metadata across classifier batches. Rollup-seeded cursor-mode default/date-only candidate ordering is explicitly inexact and disclosed; the numbered candidate-first path can remain exact.                                                                                                                                                                                                                                                                       | Signed row cursor/read-more plus zero-based numbered compatibility (`page_number`, `page_size=1..500`). Rollup-seeded cursor responses publish `query_exact=false`, `ordering_exact=false`, and `spans_per_session_candidate` provenance.                                                                                                               | Sessions grid and task/eval/annotation flows.                                                                                                                                                                                                                                                                                                                  |
| `GET /tracer/trace-session/get_session_filter_values/`     | Compatibility lookup for distinct session-level values in one project. First/last-message suggestions use a fixed recent 30-day root-span window; session/user IDs come from live dimension tables and have no retained-history date window.                                                                                                                                                                                                                                                                                                         | Numbered, zero-based `page`; `page_size=1..500`; optional `search`; no cursor. Columns are `session_id`, `user_id`, `first_message`, and `last_message`. Only first/last-message responses expose a boolean `next`; session/user ID responses return values without a terminal/read-more signal.                                                        | No current direct frontend caller was found; the former filter-panel call was removed.                                                                                                                                                                                                                                                                         |
| `GET /tracer/trace/get_trace_export_data/`                 | Voice detection routes through the bounded voice list and preserves the legacy CSV schema with a truthful truncation marker.                                                                                                                                                                                                                                                                                                                                                                                                                         | One bounded page of at most 100 list rows; this is not an exhaustive all-row export.                                                                                                                                                                                                                                                                    | Export/API consumer; no current direct frontend caller was found.                                                                                                                                                                                                                                                                                              |
| `GET /tracer/eval-task/list_eval_tasks/`                   | COUNT and OFFSET/LIMIT happen before hydration for exactly translatable numeric/datetime filters and sorts. Only page eval configs are prefetched.                                                                                                                                                                                                                                                                                                                                                                                                   | Zero-based `page_number`; `page_size=1..100`; exact total. Arbitrary/text result filters deliberately retain the legacy compatibility fallback.                                                                                                                                                                                                         | Eval Tasks list.                                                                                                                                                                                                                                                                                                                                               |
| `GET /tracer/eval-task/list_eval_tasks_with_project_name/` | Same bounded ORM fast path with project name in the response.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Zero-based `page_number`; `page_size=1..100`; exact total.                                                                                                                                                                                                                                                                                              | Organization/workspace eval-task list.                                                                                                                                                                                                                                                                                                                         |
| `GET /tracer/eval-task/get_usage/`                         | Bounded newest-row usage aggregation with truthful sampling metadata and indexed task/time lookup.                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Presets include `30m`, `1h`, `6h`, `1d`, `7d`, `30d`, `90d`, `180d`, and `365d`. Non-aggregation custom bounds must be paired and no wider than 366 days; eval/span aggregation compatibility also accepts one-sided span-date bounds. One-based `page=1..100`; `page_size=1..100` (or legacy `limit` alias); `has_more` cannot continue past page 100. | Eval Task Usage tab; this flow alone opts the shared date picker into its 1-hour choice.                                                                                                                                                                                                                                                                       |
| Eval-task historical and continuous row resolution         | Span, trace, session, and voice candidates resolve configured eval IDs, eval output metadata, and annotation label IDs once per operation. Cursor reconciliation advances only after a complete classifier proof.                                                                                                                                                                                                                                                                                                                                    | Finite candidate/query budgets; retryable sanitized failure, never partial cursor publication.                                                                                                                                                                                                                                                          | Eval-task create/update/unpause execution paths and live previews.                                                                                                                                                                                                                                                                                             |

All six public Observe/prototype list actions now start one 9.5-second wall
before request validation and tenant/project PostgreSQL scope work. Every
PostgreSQL statement receives a shrinking transaction-local timeout; the same
deadline continues through ClickHouse, enrichment, continuation construction,
and response publication. The trace, span, and session graph actions and all
three user-graph actions follow the same one-wall rule across PostgreSQL
scope/config reads and graph execution. Expiry returns a sanitized retryable
`503`; a late result is never published as success.

For a successful single-project filtered Observe read, trace/span/session list
metadata and the voice top-level envelope publish
`query_applied_filter_version`, `query_applied_filter_sha256`, and
`query_applied_filter_count`. The digest is bound to the authorized project,
observe type, and exact non-window conjunction handed to the builder/cache
identity; an unfiltered/date-only response does not fabricate a non-time filter
attestation.

## Schema and ingestion scope

There is still no mutation or DDL on the canonical ClickHouse `spans` table.
The unified catalog uses exactly six isolated ClickHouse tables created by
three clean schema files:

1. `property_definition_catalog`
2. `span_attribute_value_catalog`
3. `property_catalog_checkpoints`
4. `property_catalog_activations`
5. `property_catalog_deliveries`
6. `property_catalog_source_streams`

Schema `025_property_catalog_data.sql` creates the definition and hot-value
tables, `026_property_catalog_state.sql` creates checkpoint/activation state,
and `027_property_catalog_delivery.sql` creates delivery/source-stream state.
Only the first table stores definitions; only the second stores observed span
attribute values; the remaining four are control-plane ledgers. No source
table, trigger, materialized view, or backfill is attached to canonical spans.

No PostgreSQL projection schema is installed. Categorical annotation pickers
page the label's finite configured options; they do not scan Score history.
Exact dataset-table continuation uses a fixed-cardinality, read-only lifecycle
fingerprint over the selected Dataset/Row/Column/Cell scope. The stack adds no
PostgreSQL table, column, trigger, materialized view, or backfill command.

The earlier API optimization also adds two concurrent partial PostgreSQL
indexes on `EvalLogger` for bounded usage reads:

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

The current-source qualifier statically schedules **15 reviewed read route
callbacks**: 11 GET routes and four SELECT-only POST graph/dashboard routes.
It runs as six sequential, source-bound shards (`whatfix`, `colektia`,
`mudflap`, `trace_system`, `whatfix_graphs`, and `colektia_graphs`) over one
frozen end time. Applicable row-list routes
cover default/date, one discovered dense or sparse custom property,
`has_eval=true|false`, one observed exact eval value,
`has_annotation=true|false`, one observed exact annotation value, and the F7
custom+eval+annotation conjunction across nine intervals. Discovery accepts an
eval or annotation value only from a complete, exact public `filter_values`
page; configured choices alone are not population evidence. The corresponding
long-window filtered list must also return a concrete row identity before an
exact eval, annotation, or F7 lane can qualify. Signed list and property
continuations, semantically stable dual-chain repeats, cursor fencing, exact
Model value pages, Users, and mandatory mutation-fenced dataset and simulation
previews remain covered. Timestamped cursor bytes may differ across repeat
requests; both tokens must independently yield the same ordered, disjoint
continuation. The exact dataset representative must have a live column; the metrics
catalog must select its exact `dataset_column:<column UUID>` definition and
publish the same redacted representative binding. A missing authorized
representative population now fails the release qualifier instead of becoming
an optional gap. The graph program separately uses the rediscovered Whatfix
dense and Colektia sparse targets and covers all 11 filter profiles across nine
windows and trace, span, session, and root-dashboard kinds for each (396 lanes
per target; 792 graph/dashboard lanes total). Every filtered Observe list/graph response must publish
`canonical-json-sha256-v1`, a lowercase 64-hex digest, and the nonnegative leaf
count for canonical JSON `{project_id, observe_type, filters}`. The attested
filters are the exact server-bound non-window conjunction used by the builder
or cache identity, sorted order-independently with integral numbers normalized.
Positive base-window bounds are excluded from that digest; every graph response
instead publishes its exact UTC query window and a complete, ordered, gap-free
bucket sequence. Datetime complements remain attested filter leaves. The
per-shard hard fuse is 600 callbacks; exact worst cases are 564, 564, 284, 544,
480, and 452, with the largest callback-only wall at 5,076 seconds below the
5,280-second harness wall. Its source map, execution graph, semantic
filter/window proof, response-size wall, forked-process hard deadline,
request/ClickHouse ceilings, and mutation tripwires pass 107 offline tests.
That proves the test program, not the named-customer data outcome: there is no
qualifying exact-current-source production route-matrix result, so the Whatfix,
Colektia, and Mudflap population/correctness/latency gates below remain pending.
An earlier source-bound US matrix stopped at named-target rediscovery before any
API request. The later isolated DEV attempt also stopped before qualifier API
callbacks when canonical PostgreSQL ownership disagreed with the requested
catalog tenant; its `0815i` catalog and smoke are failure/mechanics evidence,
not functional qualification.

The executable set is intentionally smaller than the 65-operation generated-
contract diff documented in
[`TH7247_INTERACTIVE_READ_MATRIX.md`](TH7247_INTERACTIVE_READ_MATRIX.md).
Mutation and evaluator operations are excluded. The sampled legacy attribute-
values endpoint cannot prove exactness; attribute detail can schedule a refresh;
prototype lists, eval-task usage/lists, and exports are inventory/local-test
lanes rather than callbacks in this current qualifier.
The exact 14 changed operations scheduled and 51 changed operations absent from
the live callback set are enumerated in the interactive matrix's
[exact live-qualifier boundary](TH7247_INTERACTIVE_READ_MATRIX.md#exact-live-qualifier-boundary).

### Route applicability

- The nine frozen windows apply to nine row-list profiles for
  Whatfix/Colektia trace, span, and session lists and Mudflap voice: default,
  custom, eval-present, eval-absent, observed exact eval, annotation-present,
  annotation-absent, observed exact annotation, and F7. Observe routes use
  signed timestamped cursors; each requested repeat must preserve order and
  continuation truth, and both issued tokens must independently resume to the
  same disjoint next page. Default Users lists are also exercised.
- Trace, span, session, and root dashboard queries run all 11
  default/custom/system/eval/annotation/conjunction profiles across all nine
  windows with refresh disabled, separately using the Whatfix dense and Colektia
  sparse targets. Date-only Observe responses may use the approved materialized
  rollup; filtered Observe responses must be exact and publish the response-bound
  canonical filter digest/count plus exact UTC window. Dashboard responses must
  be complete exact worker series for the same requested UTC window. Users graph
  remains a compiler/local-test lane and is not part of this SELECT-only program.
- Attribute keys, dashboard values/metrics, exact dataset rows, and simulation
  previews have endpoint-specific signed continuation and repeatability checks;
  they do not accept the same date/F0-F7 Cartesian matrix. Metrics must include
  and search the exact dataset-column definition chosen for the mandatory exact
  dataset lane, with both lanes publishing one representative binding.
- Prototype trace/span lists, eval-task usage/lists, compatibility session
  values, and bounded exports remain in the broader inventory but are not
  executable lanes in this qualifier. Their local or historical evidence must
  not be presented as a current-source live result.
- F5 eval positive/negative/exact, F6 annotation positive/negative/exact, and F7
  custom+eval+annotation are scheduled for applicable row-list routes and for
  each of the three graph kinds. Dashboard forms remain outside this qualifier.
- Historical/continuous resolution is not a public GET route. SELECT-only
  qualification may exercise candidate/classifier reads, but must not call
  create/update/unpause, reconciliation, cursor advancement, or scheduling
  operations.

The current qualifier's applicable telemetry acceptance windows are:

| Qualifier ID | Qualification interval |
| ------------ | ---------------------- |
| 30m          | 30 minutes             |
| 1h           | 1 hour                 |
| 6h           | 6 hours                |
| 24h          | 24 hours               |
| 7d           | 7 days                 |
| 30d          | 30 days                |
| 90d          | 90 days                |
| 180d         | 180 days               |
| 365d         | 365 days (12 months)   |

Required population profiles:

- **Dense:** Whatfix trace data; page one, page two, page four/former late-page
  failure boundary, and at least ten distinct key cursor advances.
- **Sparse:** Colektia/Colly trace data; empty advancing checkpoints, rare
  keys/values, truthful exhaustion, and repeatability.
- **Voice:** Mudflap voice data; property parity plus eval/annotation positive
  and negative membership.

Required filter combinations for applicable trace, span, session, and voice
row-list routes:

| ID  | Filter cell                                                            |
| --- | ---------------------------------------------------------------------- |
| F0  | Date/project only                                                      |
| F1  | System field                                                           |
| F2  | Sparse custom property/value                                           |
| F3  | Dense custom property/value                                            |
| F4  | System plus custom property                                            |
| F5  | `has_eval=true\|false` and exact eval value                            |
| F6  | `has_annotation=true\|false`; positive requires every configured label |
| F7  | Custom property plus eval plus annotation conjunction                  |

For each applicable row-list route, default/custom cells repeat and exercise
continuation in every window. To bound production load, new F5/F6/F7 cells use
one exact first-page callback from 30m through 180d and repeat plus an optional
advancing continuation at 365d. Signed cursor routes must prove cursor
advancement without overlap; numbered prototype routes must prove stable page
boundaries or a truthful finite `422`/`503`. Exact-order
routes must prove no duplicate/gap publication. When a session response carries
inexact flags and `spans_per_session_candidate` provenance, qualification must
instead validate its disclosed candidate ordering and the exact membership of
every published row. All routes must prove
project/workspace fencing, exact positive/negative filter membership, and an
identical repeat where the profile schedules one. A missing positive
configuration, observed value, conjunction, negative-complement witness, or
representative continuation is a named population gap, not a passing cell.

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
- Cursor-backed categorical annotation values page finite configured label
  options. Free-text labels accept exact input instead of scanning Score
  history for suggestions; native Score facts remain the authoritative filter
  source.
- Dataset/simulation value adapters use exact finite signed pagination when
  `page_size` is supplied. The no-page compatibility call returns a complete
  vocabulary only below its finite cap; wider inventories return `422` and
  require a more specific search. Kartik-account DEV UI/API smoke passed, but
  these adapters remain outside the formal dense/sparse/voice release matrix.
- The current implementation adds independent ingestion-fed lookup storage
  without modifying the existing spans table or using a materialized view.
  Local direct/Kafka protocol tests are green. The fresh `0817j` target has one
  contiguous activated build across the reviewed 33-project Kartik scope, and
  DEV readers are enabled only for that workspace. No DEV artifact authorizes
  a production rollout.

# CATALOG current-source SELECT-only qualifier

This package builds an inert, source-bound launch bundle and runs the changed
interactive read surfaces through their real DRF callbacks. It is a release
qualifier, not a deploy script: nothing in this directory logs in, calls a
public URL, creates a pod, starts a worker, runs a migration, or backfills data.

## What it proves

Every request has a 9.8-second in-view wall inside a stricter 9-second
supervisor process wall and must finish below 10 seconds. Each shard refuses to
start another callback after 5,280 cumulative seconds, before its Job's
5,400-second active deadline. One Job runs exactly one of
`whatfix`, `colektia`, `mudflap`, `trace_system`, `whatfix_graphs`, or
`colektia_graphs`; the six shards must run sequentially. Their exact offline
worst cases are 564, 564, 284, 544, 480, and 452 callbacks under a 600-request
per-shard fuse (2,888 total if every optional continuation exists). The largest
callback-only wall is 5,076 seconds, leaving 204 seconds of headroom for
bounded setup reads. These are
absolute load ceilings, not expected counts or permission to extend a 90-minute
shard wall.
PostgreSQL is opened with `default_transaction_read_only=on` and a 9.5-second
statement timeout. A lexical guard permits only `SELECT`/read-only `WITH` plus
the minimal transaction controls Django needs. ClickHouse permits only
`SELECT`/read-only `WITH`, forces `readonly=2`, and applies execution, thread,
memory, result, and read-byte ceilings. Redis, Celery, Temporal, scheduler, and
external-cache writes are tripwired. Activating any tripwire fails the run.
For a purpose-built server-locked ClickHouse credential, either
`CH_SERVER_ENFORCED_READONLY=true` or `CH25_SERVER_ENFORCED_READONLY=true`
preserves the client's credential-owned settings instead of attempting a
per-query override; lexical SELECT/WITH checks, mutation-method blockers, and
the ClickHouse read fuse remain active.

The frozen time matrix is `30m`, `1h`, `6h`, `24h`, `7d`, `30d`, `90d`,
`180d`, and `365d` (12 months). Whatfix is rediscovered as the named dense
attribute population and Colektia/Colly as the named sparse population; their
known project IDs are only anchors and tenant-name rediscovery must still
succeed. Mudflap covers the voice surface. Discovery examines at most four
projects per tenant, two catalog pages per category, and two value candidates
per category. Catalog candidates are sorted by `property_id`; each eval and
annotation candidate must come from a complete, exact public `/filter_values`
page. That vocabulary alone is not observation evidence: the value becomes
qualified only when a long-window list with its exact filter leaf returns and
hashes a concrete row identity. SCORE evals are eligible only when the public
value page supplies a finite numeric candidate. Stable, exact empty list pages
are valid for short windows. Every row-list profile below must still
return a positive page in at least one of `30d`/`90d`/`180d`/`365d`, preventing
an all-empty matrix from passing.

The eleven list profiles are date/project only, named sparse-or-dense custom
attribute, one observed namespaced Model system value, Model + custom,
`has_eval=true`, `has_eval=false`, one exact public eval value,
`has_annotation=true`, `has_annotation=false`, one exact public annotation
value, and custom + exact eval + annotation conjunction. F7 uses the same
value-bearing annotation leaf as the standalone F6 exact lane. Eval-exact and
annotation-exact identities must be disjoint from their respective negative
pages; F7 must be disjoint from both negatives. An exact empty
negative page is valid route behavior but is recorded separately as a missing
complement-population witness; because that witness is required, the shard
remains unqualified without it rather than misreporting a route failure.

List pages are repeated to prove stable physical order and continuation truth.
Timestamped signed cursors are intentionally not required to be byte-identical:
when `has_more=true`, the qualifier follows both page-one cursors independently
and requires the same ordered, disjoint page-two semantics. It rejects missing,
unbounded, non-advancing, empty, overlapping, or semantically divergent
continuations. Property keys, Model values, exact datasets, and simulation
previews get the same dual-chain page-one/repeat/read-more checks where their
populations expose continuation.
The metrics lane specifically requires the activated unified-property-catalog
cursor contract: it drains one activation-pinned chain, proves page-one
stability, rejects false totals and cross-page property-ID overlap, finds the
expected system/custom/eval/annotation definitions, and executes a fresh
search. Before that drain, one bounded SELECT chooses the same mandatory ready
dataset used by the exact-table lane plus one of its live columns. The metrics
lane must find that exact `dataset_column:<column UUID>` definition, select it
with a UUID search, and publish the same redacted representative binding as the
dataset lane. The Model value lane similarly requires activated-catalog provenance,
exact type-union metadata, one lineage across continuation, and value search;
typed legacy fallback cannot qualify either lane.
To keep cumulative production load bounded, default/custom/F1/F4 row-list cells
use that full protocol in every window; each F5/F6/F7 cell uses one exact first
page in `30m` through `180d` and the full repeat/continuation protocol at
`365d`. Thus every relational filter/window/list route gets latency and result-state
evidence while each new profile also gets a representative long-window
read-more proof. The F1/F4 trace, span, and session cells run on the separate
`trace_system` shard for both Whatfix and Colektia; Mudflap's smaller voice
matrix keeps them on its primary shard. Merge validation requires the same
rediscovered system/custom/eval/annotation binding across split shards.
Sampled, degraded, pending, failed, or incomplete responses fail closed.

The `whatfix_graphs` and `colektia_graphs` shards each run all eleven profiles
across all nine windows for trace, span, session, and root dashboard queries
(396 lanes per target; 792 total). The 297 Observe lanes per target may use the approved
`query_exact=false` + `query_provenance=materialized_rollup` pair; every
filtered Observe graph must be exact, complete, non-sampled, response-bound to its
canonical non-window filter digest/count, and publish the exact UTC query
window. All 99 dashboard lanes per target must return the exact worker result,
including a complete ordered series for the requested custom UTC window. For a
cold read, the qualifier replaces all three reviewed cache call sites with a
qualifier-only synchronous seam that invokes the worker's
tenant-reauthorized SELECT reader directly. It never claims, schedules,
publishes, or caches an exact snapshot; the same per-request supervised wall
and PG/ClickHouse guards still apply. The two matrices use the separately rediscovered **Whatfix dense** and
**Colektia/Colly sparse** targets. Every target/kind/profile must expose a
positive point in at least one long window. Users graph remains excluded from
the live route set and is a compiler/local-test lane.

## Qualified SELECT API inventory

| Key                     | Method and public path                                             | Frontend use covered by the contract                                                                                                                                                                                                                      |
| ----------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `property_keys`         | `GET /api/traces/span-attribute-keys/`                             | Compatibility and specialized attribute-only consumers: eval mapping/test mode, Run Insights, and Journey attribute detail/mapping; the activated Trace/Span/Voice pickers use `metrics`                                                                  |
| `filter_values`         | `GET /tracer/dashboard/filter_values/`                             | Trace/Voice, dashboard/widget, task, annotation, and dataset value pickers; activated Model value cursor plus search proof                                                                                                                                |
| `metrics`               | `GET /tracer/dashboard/metrics/?cursor_mode=true`                  | Activated Trace/Span/Voice Basic and Query property pickers plus shared registry, graph, widget, and automation consumers; 20-row UI pages, server search/category counts, activation-pinned cursor drain, exact same-dataset column presence, and search |
| `dashboard_query`       | `POST /tracer/dashboard/query/`                                    | Widget editor/chart root query across the same dense/sparse filters and 30m-through-12M custom windows                                                                                                                                                    |
| `trace_list`            | `GET /tracer/trace/list_traces_of_session/`                        | Observe trace grid and task/eval/annotation previews                                                                                                                                                                                                      |
| `span_list`             | `GET /tracer/observation-span/list_spans_observe/`                 | Observe span grid and task/eval/annotation previews                                                                                                                                                                                                       |
| `session_list`          | `GET /tracer/trace-session/list_sessions/`                         | Sessions grid and task/eval/annotation flows                                                                                                                                                                                                              |
| `voice_list`            | `GET /tracer/trace/list_voice_calls/`                              | Voice/Agents grid and preview flows                                                                                                                                                                                                                       |
| `users`                 | `GET /tracer/users/`                                               | Users grid                                                                                                                                                                                                                                                |
| `trace_graph`           | `POST /tracer/trace/get_graph_methods/`                            | Observe primary graph                                                                                                                                                                                                                                     |
| `span_graph`            | `POST /tracer/observation-span/get_graph_methods/`                 | Observe span graph                                                                                                                                                                                                                                        |
| `session_graph`         | `POST /tracer/trace-session/get_session_graph_data/`               | Sessions graph                                                                                                                                                                                                                                            |
| `dataset_exact`         | `GET /model-hub/develops/{dataset_id}/get-dataset-table/`          | Ground Truth dataset picker/import                                                                                                                                                                                                                        |
| `simulation_executions` | `GET /simulate/run-tests/{run_test_id}/preview-executions/`        | Simulation test-mode execution preview                                                                                                                                                                                                                    |
| `simulation_calls`      | `GET /simulate/test-executions/{test_execution_id}/preview-calls/` | Simulation test-mode call preview                                                                                                                                                                                                                         |

The four POST entries above run for both the Whatfix dense and Colektia sparse
targets behind the database and dispatch guards. Date-only Observe requests may
use the approved materialized rollup; every filtered Observe profile and every
dashboard query must use an exact read.
Mutation/execution APIs are intentionally
excluded. The sampled legacy attribute-values endpoint is excluded because it
cannot prove exactness. Attribute detail is excluded because its missing-data
path may schedule a refresh. The exact 51 changed operations absent from this
live callback set are enumerated in the
[interactive read matrix](../../../docs/FI_INTERACTIVE_READ_MATRIX.md#exact-live-qualifier-boundary).
Dataset and simulation lanes are mandatory: an absent authorized dataset with
a live catalogued column fails the release qualifier rather than appearing as
an optional gap. The exact dataset page and metrics catalog must share that
representative binding. Simulation preview pages must prove exact
snapshot progress, semantically stable dual-chain repeats, and truthful
continuation/terminal state.

## Source identity and assembly

`assemble.py` accepts only a digest-pinned base image. That image is treated as
a dependency carrier, not as trusted application source: the deterministic
overlay contains and hashes **every current regular file below `futureagi/`**,
including clean files. The manifest separately records the base commit, full
present-worktree inventory, dirty states, required runtime files, and dirty runtime
subset. The live entrypoint re-hashes every overlaid runtime file before Django
initialization. Intentional tracked deletions are recorded separately as one
sorted `runtime_deletions` list and an exact Git-base SHA-256 map. During image
build, a helper removes only a physical regular file whose bytes still equal
that pinned base digest; a missing file is already correct, while content drift,
a symlink, or a non-file fails closed. The live entrypoint then proves with
`lexists` semantics that no filesystem object remains at any deletion path.
Assembly refuses a drifting working tree, an unbound/non-regular deletion, any
other absent tracked backend file, unsafe paths, an output inside the
repository, an existing output, or an unpinned image.

Local, non-launching plan preview:

```sh
python3 futureagi/scripts/fi_current_select_qualifier/assemble.py \
  --repo . \
  --output /tmp/fresh-fi-bundle \
  --base-image registry.example/backend@sha256:<64-lowercase-hex> \
  --print-plan
```

Actual assembly creates files but performs no network call or launch. Building
and launching the derived digest is a separate authorized operation. The inert
job template still contains explicit image/secret placeholders and must not be
hand-edited or applied. Use the offline DEV materializers in
`deploy/dev/fi-select-qualifier`: the Kubernetes path validates an exact
DEV context/project/namespace, dedicated no-token ServiceAccount, purpose-built
read-only Secret reference, digest image, and NetworkPolicies; the separate SSH
path validates a pinned DEV host/key plus credential/egress attestations and
emits a non-executed container argv plan. Neither materializer obtains SOS
tokens or executes a cloud/container command. Do not substitute a general
backend secret such as `core-backend-secret`. The Kubernetes Job's read-only
root filesystem has bounded writable `emptyDir` mounts only for `/tmp` and
Django's `/app/backend/logs`; the pod receives no Kubernetes service-account
token.

This qualifier consumes an already valid activation; it never creates or
repairs one. The separately reviewed DEV rollout requires the canonical spans
source database and isolated catalog target database to differ. The next
approved plan uses source `futureagi` and fresh isolated target
`fi_catalog_dev_kartik_0816d`, without altering an existing application
table. Before any target client, schema inspection, source read, lease, or write,
that rollout proves the exact project allowlist through
`public.tracer_project` and `public.accounts_workspace` in a read-only
repeatable-read PostgreSQL snapshot. Missing/duplicate projects or any
project/workspace/organization mismatch fails closed. It revalidates ownership
at revision source, relational, publish, fence, and activation boundaries and
binds the result into an immutable authorization/execution proof. The current
remote state of the `0816d` plan is deliberately unclaimed pending read-only
reinspection.

The template contains one unresolved `__QUALIFIER_SHARD__`, not six YAML Job
documents, so it cannot bulk-launch the matrix. Materialize exactly one allowed
shard, wait for its terminal JSON, and only then materialize the next. All six
use the same `QUALIFIER_RUN_ID`, source identity, and
`QUALIFIER_END_UTC`; the frozen end may be at most ten hours old when a shard
starts and may not be future-dated. `validate_shard_result_set()` accepts a
release proof only when all six green results share the exact source/run
binding; Whatfix must match across `whatfix`, `trace_system`, and
`whatfix_graphs`, while Colektia must match across `colektia`, `trace_system`,
and `colektia_graphs`. Merge validation also rejects either incomplete
396-lane graph/dashboard matrix or a mismatched dataset/catalog representative binding.

## Offline verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 \
  futureagi/scripts/fi_current_select_qualifier/test_offline.py
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  futureagi/scripts/fi_current_select_qualifier/*.py
```

These tests use only the standard library and in-memory fakes. They make no
network, database, cloud, container, or deployment calls. Passing them proves
the harness contract and safety gates, not live Whatfix/Colektia performance;
only a source-bound result JSON with every required lane green can do that.

The same offline suite calls `openapi_inventory.py`, which compares the checked
Swagger contract with immutable qualifier base commit
`041084a8bfea8e5e7f66b87d7f6883c57659729b`. It requires exactly 65 impacted
operations (32 direct and 33 transitive-schema-only) and 48 changed shared
definitions. At this source freeze, the offline suite passes 107 tests. Run the
verifier alone with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 \
  futureagi/scripts/fi_current_select_qualifier/openapi_inventory.py
```

The preserved `0815i` DEV evidence records exactly six catalog tables and one
raw activation containing 2,330 live definitions, 1,612 tombstones, and 218,306
semantic values. Its earlier public callback smoke returned two disjoint
definition pages and a nonempty Model value page; the query-log delta was 34
SELECTs and zero non-SELECT statements. A later canonical PostgreSQL ownership
audit found that a requested catalog tenant did not own every allowlisted source
project and stopped before qualifier API callbacks. That failure invalidates the
activation and earlier smoke as tenant-valid functional or qualification
evidence; the raw counts remain preserved failure/mechanics evidence. The last
verified DEV preflight also found no Whatfix, Colektia/Colly, or Mudflap anchor
or fallback active-project name match, so the required dense/sparse/voice route
matrix remains pending. The approved isolated `0816d` retry is not assigned a
current remote state until read-only reinspection. Production remains outside
this package and requires a complete exact-source matrix plus separate user
approval.

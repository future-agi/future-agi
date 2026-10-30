Project SQL workbench
=====================

Requirement: TH-8685. This is a bounded analytical interface, not a database
console. Natural-language generation is explicitly outside this release.

Delivered surface
-----------------

* Observe → Query: ClickHouse SQL editor, schema browser and completion, asynchronous
  execution/cancellation, paged results, a one-measure bar chart, CSV and Parquet.
* Private or project-shared saved queries; sharing requires authentication and
  current project access. A link shares the definition, never the creator's results.
* A project-shared query can be added to a workspace dashboard. The widget submits
  the saved id to the same service and reauthorizes the viewer on each execution.
  It does not run SQL copied from dashboard JSON.
* MCP tools ``run_sql``, ``get_sql_result``, ``cancel_sql``, ``get_sql_catalog``.
  Their generated raw-envelope contract preserves id/state/error/result instead
  of unwrapping only rows. ``run_sql`` returns HTTP 202 and a personal execution
  id. Dashboard widgets retry temporary admission pressure with bounded backoff
  for at most 90 seconds; queued waits and superseded work are cancellable,
  without relaxing the server's one-active-query-per-actor rule.
* Operator tooling emits separate application-service and project-specific BI
  principals, explicit grants, locked resource profiles, scoped views and row policies.

Catalog version 1
-----------------

All six virtual datasets are scoped by a PostgreSQL-authorized project, organization
and non-null workspace. No legacy null-workspace fallback is allowed. Queries must
supply an aware UTC-compatible start/end window of at most 31 days. The backend
injects the window independently into every source, including CTEs, joins, unions
and scalar subqueries. Row grain and window semantics are important:

``spans``
  One current non-deleted span starting in the window. ``id``, ``trace_id``,
  ``session_id``, ``user_id``, name, timing, status, model/provider, tokens, cost and
  typed string/number attribute maps. Raw prompts, outputs, storage URLs and
  resource metadata are deliberately not exposed.
``traces``
  One current trace created in the window. The UUID ``id`` is different in type
  from the spans table's String trace id; cast explicitly when joining.
``sessions``
  Sessions *first seen* in the window, not every session active during the window.
  ``locale`` is the latest present span ``attrs_string['locale']`` in this same window
  (ties break deterministically by span id; missing keys do not erase a value).
  It is not an invented session property; absent attributes return an empty string.
``users``
  Users *first seen* in the window, not an active-users rollup. ``external_id`` is
  the external user label, while ``id`` is the internal UUID.
``eval_results``
  Current scores created in the window. The physical score store has no project
  column, so both a current project-owned evaluation configuration AND an owned
  typed project-owned span/trace/session target are mandatory. Candidate ids come
  from windowed eval rows; historical ownership lookups do not filter target
  timestamps or materialize every id in a project. Thus a recent backfill eval
  of a sixty-day-old span/trace or long-lived session is retained. Span targets
  match the (span id, trace id) pair, not a globally assumed span id. Null or
  foreign targets never grant access. At most 1,000 config ids are admitted.
  ``eval_task_id`` is the task key. ``run_id`` remains NULL until actual execution
  provenance is available; tasks are not runs. Typed nullable ``policy_version``,
  ``cohort_id``, ``cohort_frozen_at``, ``membership_mode``, ``sampling_unit``,
  ``sampling_unit_id``, ``stratum``, ``inclusion_probability``,
  ``sampling_window_start/end`` and ``population_size`` read
  ``output_metadata._task_selection.sampling``. Legacy/absent/malformed metadata
  remains NULL, never probability 1. Invalid probabilities are NULL.
``annotations``
  Current unified ``model_hub_score`` rows with exact ``organization_id``,
  ``workspace_id`` and ``tracer_project_id``; null legacy ownership is excluded.
  Requires that table's CDC/native mirror to be provisioned and healthy.

The catalog endpoint supplies actual column names/types and these descriptions;
there is no user-readable system-schema discovery.

``derived_metrics`` is an optional seventh dataset over the committed-attempt view
from the derived metrics feature. It preserves metric/run/result ids, version,
rollup/group key, value/numerator/denominator, status, unit/valid/missing/invalid
probability counts, UTC window/as-of, definition/source lineage and
``sampling_scope`` (``cohort_id:policy_version``, empty when unavailable). All three
org/workspace/project predicates apply. Only metric windows fully contained in
the selected window are returned; no FINAL is applied to the view. Enable
``SQL_WORKBENCH_DERIVED_METRICS_ENABLED=true`` after its migration and invoke
``provision_sql_service --include-derived-metrics`` for its view/result/commit
SELECT grants. The catalog performs a bounded LIMIT 0 schema/privilege probe;
missing dependencies are labelled unavailable and execution fails explicitly,
never as an invented empty result. Base datasets work without this feature.

Identity semantics are deliberately explicit: this initial catalog exposes raw
storage identifiers, including ``raw_id``, ``raw_session_id`` and ``raw_user_id``
aliases. ``session_id``/``user_id`` are also raw, NOT canonical survivor keys.
Unlike Observe's remap-aware readers, it does not consolidate historical merged
session/user identities. The catalog and a persistent Query UI notice disclose
this limitation. Joins/distinct counts across an identity merge can differ from
Observe; canonical consolidation remains a release requirement, not a claimed
capability.

Each dataset keeps its documented event-time meaning. A join intersects those
windows: recent ``eval_results`` retain old targets, while separately querying
``spans`` by a recent start-time window or ``sessions`` by recent first-seen time
still excludes old events. This is not used as an authorization shortcut.
Sampling ``sampling_unit_id`` is the producer's identity (potentially canonical),
not an alias for this catalog's raw session/user key.

Example (join, one result row per locale)::

  SELECT s.locale, count() AS evaluations, avg(e.score) AS mean_score
  FROM eval_results AS e
  JOIN sessions AS s ON e.session_id = s.id
  GROUP BY s.locale
  ORDER BY evaluations DESC

Only scores that actually carry ``session_id`` join in this example. Span scores
must be joined via ``span_id`` to ``spans.id`` first; no synthetic target keys are
filled in. Do not sum trace/session metrics across one-to-many score joins without
preaggregating at the desired grain.

Security and lifecycle
----------------------

* SQLGlot 28.10.1 parses the ClickHouse dialect. Exactly one SELECT/UNION statement,
  a closed AST-node set, explicit approved functions and unqualified virtual
  relations are accepted. Unknown future syntax/functions fail closed.
* Every physical source is replaced by a server-owned SELECT with explicit columns
  and scope predicates. Fact windows are distinct from candidate-target ownership
  predicates. Physical names are database-qualified so user CTE names
  cannot shadow them. User identifiers must be simple ASCII SQL identifiers.
* Writes, multiple statements, SETTINGS, FORMAT, OUTFILE, table functions,
  dictionaries, system/information_schema access, file/network functions, recursive
  CTEs, ARRAY/CROSS/GLOBAL/NATURAL joins and Cartesian ON expressions are rejected.
  JOIN ON supports one or more AND-combined equality keys between different
  aliases (or USING). Ordinary AND/OR filters, reviewed If aggregate combinators,
  now/percentiles and approved date units work; a Var is allowed only in the
  reviewed date/interval unit positions, never as unrestricted SQL text.
* Memberships must be active at admission, worker execution, completion, polling,
  history, cancellation, export and saved-query/widget operations. A viewer can
  read but cannot save, share, delete or attach queries. Organization-level viewer
  restrictions cannot be overridden by a stale workspace-member record.
* Saved definitions are project-scoped; execution snapshots are additionally
  creator-scoped. Removing a membership revokes access to previously completed
  results. API responses and downloads carry ``Cache-Control: no-store``.
* PostgreSQL locks serialize per-organization admission (two active jobs maximum,
  one per actor; 2-second admission lock and 5-second statement ceilings). Daily
  operational defaults allow 1,500 submissions per actor and 30,000 per
  organization per 24 hours, with 128 MiB/512 MiB result-body budgets. Operators
  configure these positive limits through ``SQL_WORKBENCH_MAX_ACTOR_QUERIES_24H``,
  ``SQL_WORKBENCH_MAX_ORG_QUERIES_24H``, ``SQL_WORKBENCH_MAX_ACTOR_RESULT_BYTES_24H``
  and ``SQL_WORKBENCH_MAX_ORG_RESULT_BYTES_24H``. They are resource protection,
  not pricing or edition entitlements. Admission reserves the maximum 8 MB
  result using one statement snapshot of completed output and in-flight jobs;
  a concurrent completion cannot disappear between separate count/sum reads.
  Counters use organization/actor timestamp indexes. A locked ClickHouse service profile
  additionally caps global service-principal concurrency at two. No
  process-local-only semaphore.
* Jobs expire in the queue after 90 seconds. Execution is capped at 30 seconds,
  10 million scanned rows, 1 GB scanned bytes, 256 MiB memory, 64 MiB join state,
  one million/64 MiB candidate-set entries,
  two threads, 10,000 output rows and 8 MB output. Leaf-node limits apply as well.
  Native cancellation addresses the worker's own connection, never admin KILL SQL.
  SQL uses the existing Temporal ``exact_aggregation`` worker: 45-second activity,
  45-second queue wait, 100-second workflow run and 120-second workflow execution
  ceilings, with zero retries and deterministic job-id dispatch. No Celery service
  is needed. Native/socket bounds remain necessary because a Temporal timeout
  does not kill a blocked Python thread. Only the main thread reads native packets;
  the cancellation watchdog sends a non-consuming cancel packet.
* ``readonly=1`` and resource ceilings must be enforced on the database user.
  The runner verifies every server setting before executing user queries and sends
  no per-query settings (immutable readonly profiles reject such assignments).
  It never falls back to the normal application's admin connection. Distributed
  reads also lock ``optimize_distributed_group_by_sharding_key=0``,
  ``optimize_skip_unused_shards=0``, ``distributed_group_by_no_merge=0``,
  ``prefer_global_in_and_join=1``, ``distributed_product_mode=global``,
  ``max_parallel_replicas=1`` and ``skip_unavailable_shards=0``. These preserve
  initiator merging/global lookups without pretending sessions/users are shard-local.
* Results are readable/downloadable for 24 hours. The existing Temporal worker
  erases expired terminal result bodies in batches of at most 1,000 every 15
  minutes. Audit metadata is preserved by default: no policy means no hard
  deletion. An operator may explicitly set ``SQL_WORKBENCH_AUDIT_RETENTION_DAYS``
  to a positive day count (for example, 90). Only expired terminal records older
  than that configured cutoff are eligible; queued/running jobs are never deleted
  by cleanup. ``purge_sql_results`` invokes this same policy manually. Audit
  metadata includes actor, org/workspace/project, original SQL, window, state and
  timestamps. SQL text can contain sensitive literals and is not written to
  application logs. Driver errors/physical schema details are not returned.
* CSV export neutralizes spreadsheet formulas. Both exports contain the same
  bounded snapshot as the table, not an unbounded second scan. Very wide numeric
  values are strings to avoid JavaScript precision loss. Nonfinite floats are null.

Deployment
----------

Apply Django migration ``tracer/0117_sql_workbench`` using the normal bootstrap job.
Install the locked backend dependencies. The existing always-on Temporal
``exact_aggregation`` worker imports ``tracer.sql_workbench.tasks`` from the shared
activity registry; no extra worker process or Celery deployment is introduced.

Application-service principal (operator only)::

  export SQL_WORKBENCH_CH_PASSWORD='<random password of at least 24 characters>'
  python manage.py provision_sql_service --client-cidr 10.20.0.0/24 > sql-service.sql
  # Review, then apply with the operator's ClickHouse client, not an app API.

Configure the backend and worker with ``SQL_WORKBENCH_CH_USER`` (the default emitted
principal is ``futureagi_sql_service``), ``SQL_WORKBENCH_CH_PASSWORD``, and optionally
``SQL_WORKBENCH_CH_SECURE=true`` for native TLS. Host/port/database come from the
existing ``CLICKHOUSE_V2`` configuration. TLS certificate verification remains on.
The service account is application-only, cross-project, read-only and limited to
six physical sources. Never give this credential to a person, notebook or BI tool.
Tenant authorization for this account is the AST+application boundary.

Project-specific direct BI/notebook access (self-hosted operator only)::

  export SQL_DIRECT_PASSWORD='<a different random password, at least 24 characters>'
  python manage.py provision_sql_reader \
    --organization '<org UUID>' --workspace '<workspace UUID>' --project '<project UUID>' \
    --principal analyst_team --client-cidr 10.30.0.0/24 --window-days 31 > sql-reader.sql
  # Review policies, source-table availability and existing cluster policy rules.
  # Apply with the operator's ClickHouse client in a maintenance/change window.

This emits a non-login ``HOST NONE`` definer, explicit per-source row policies, a
``sql_analyst_team`` database with six SQL SECURITY DEFINER views, and an invoker
with SELECT on those views only. Physical row policies use intrinsic ownership,
not fact timestamps or recursive candidate queries. Eval policies restrict the
project-owned config set; the immutable published eval view additionally enforces
typed candidate-target ownership across history. No wildcard, write, SOURCES or access-management
grants are issued. The password is represented only as a SHA-256 credential hash
in emitted DDL; protect that output too. Restricted network CIDRs are mandatory.
The views have a rolling bounded window. Evaluation configuration ids are frozen
at provisioning; reprovision when the authorized configuration set changes.
Existing names deliberately fail rather than reuse users with unknown privileges.

Connect a ClickHouse JDBC client to ``jdbc:clickhouse:https://<host>:8443/sql_analyst_team``
or a native client to the server's configured TLS port (commonly 9440), using the
project-specific invoker. HTTP/JDBC/native listeners and TLS are deployment-owned;
this command does not open ports, publish passwords or modify cluster topology.

Direct database credentials are NOT browser viewers and cannot consult Django
memberships at each query. Operators must revoke/rotate them when access changes,
for example ``DROP USER analyst_team``. Row policies and locked limits still apply
when the client submits arbitrary SQL directly. Applying row policies can affect
existing users without matching policies depending on ClickHouse server policy
configuration: review/test on an isolated instance before production. Do not claim
this command has changed a running installation; it prints DDL only.

Verification and unqualified requirements
-----------------------------------------

Reproducible repository commands::

  cd futureagi
  uv run pytest tracer/sql_workbench/tests
  cd ..
  scripts/generate-openapi-schema.sh
  python scripts/generate-mcp-tools.py
  cd frontend
  yarn contracts:generate
  yarn test:run src/sections/projects/SQLQuery
  cd ../e2e
  yarn typecheck && yarn catalog

The adversarial unit suite covers joins/CTEs/UNION/subqueries, hidden table
functions, write/settings/schema escape attempts, null and malicious identifiers,
profile ceilings, output bounds, cancellation and least-privilege DDL. ORM tests
cover cross-organization/workspace/project access, viewers, revocation,
creator-only results, exports and private/project sharing.

Local bounded evidence: ClickHouse 25.3.14.14 ran the six base catalog sources,
join/CTE/UNION/subquery tenant checks, native locked service-profile execution,
nullable sampling metadata and task/run lineage checks, session locale handling,
and the actual E2E fixture bootstrap twice (including write/settings denial).
These were tiny per-test fixtures in an assigned test database. The E2E-only
bootstrap uses the production profile builder, gates backend and the existing
exact-aggregation worker after schema bootstrap, and does not add an application
admin fallback. Its dependency/profile/guard tests and Compose rendering were
checked; the browser flow itself still needs execution and fallibility proofs.

The actual PostgreSQL 17 migration graph, API/MCP permission paths and concurrent
admission locking also passed against the isolated SQL test database; its existing
repository flush fixture handles legacy unmanaged-table foreign keys.

Live release gates remain explicit: optional
derived-metric integration and direct-user policy/privilege probes;
Temporal-dispatched native cancellation under a running scan; complete browser flow and independent
fallibility perturbations; distributed-table behavior; 1-billion-span latency and
24-hour endurance. Unit tests or SQLite ORM checks are not evidence for those
claims. No seconds-at-1B performance claim is made. This bounded lane intentionally
rejects scans beyond its resource profile rather than silently launching an
unbounded scan or returning partial aggregate answers.

Scope boundaries
----------------

All virtual datasets require a concrete authorized project, including in joins
and nested queries. Bounded equality joins, UNION and asynchronous execution
operate within the documented SQL allow-list and resource profile. CSV and
Parquet downloads export the bounded interactive result snapshot. This feature
does not provide unrestricted workspace-wide raw SQL or unbounded asynchronous
scans; full-scale performance remains an unexecuted release gate. No broader SQL
or CLI capabilities are claimed. Warehouse synchronization is outside this
feature.

Dedicated full-scale profile (not executed locally)
---------------------------------------------------

``futureagi/scripts/sql_workbench/profile.json`` declares the one-billion-span
project / one-hour query window / 24-hour, single-client profile. The benchmark
client uses the normal authenticated asynchronous API, requires nonempty join
results, polls personal results, cancels failed/timed-out jobs and records
timing/status metadata without result values. It never increases backend limits, seeds data, or independently proves the
fixture's claimed size. Supply a manifest from the ingestion fixture's verified
row-count evidence (``project_id``, ``span_count``), and retain that evidence with
the measurements. Keep ingesting matching session/evaluation fixtures during the
rolling-window soak; an empty join is a failure, not a fast-query result. Do not run
this load on a shared developer database::

  export SQL_BENCHMARK_TOKEN='<scoped API bearer token>'
  python futureagi/scripts/sql_workbench/benchmark.py \
    --base-url https://dedicated-validation.example \
    --organization '<UUID>' --workspace '<UUID>' --project '<UUID>' \
    --fixture-manifest /secure/verified-project-fixture.json \
    --allow-benchmark-requests > /secure/sql-benchmark.jsonl

Measured local checks are tiny fixtures, not this profile. No billion-span or
24-hour result is represented by their unit/integration test timings.

Cluster coordinator provisioning
--------------------------------

The provisioning commands print single-coordinator DDL. On a sharded/replicated
installation, apply that same reviewed DDL on **every possible read-only
coordinator**, including principals, profiles, grants, row policies and views.
Use each node's operator client; these commands do not discover or mutate cluster
topology. Sources must remain the logical Distributed tables, never ``*_local``.
The cluster deployment owns immutable routing, consistent per-node schema and
Keeper paths. A multi-node runtime qualification is still required; the local
single-node checks do not establish it.

The E2E-only Compose dependency graph and account bootstrap were verified
separately; the browser flow and live Temporal-to-browser round trip have not run. Direct BI row-policy probes use an intentionally broad
permissive policy against one source table and verify the additional tenant
policy remains restrictive. The ClickHouse policy mechanism's documented
OR-combination of permissive policies is why the emitted policy explicitly uses
``AS RESTRICTIVE``; without that distinction, an existing broad policy could
bypass a tenant filter.

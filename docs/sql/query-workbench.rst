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

Capability matrix
-----------------

These are bounded capabilities, verified by the repository tests below on
ClickHouse 25.3.14.14 with SQLGlot 28.10.1. They do not imply unrestricted SQL,
large-fixture performance or cluster qualification. The live fixtures contain
two tenants and independently computed expected values.

.. list-table:: Tested SQL capabilities
   :header-rows: 1
   :widths: 22 48 30

   * - Capability
     - Tested behavior
     - Evidence in ``tracer/sql_workbench/tests``
   * - Scoped windows
     - ``row_number``, ``rank``, ``dense_rank``, ``ntile``, ``percent_rank``,
       ``cume_dist``, ``lag``, ``lead``, ``first_value``, ``last_value`` and
       ``sum/avg/count/min/max OVER``. Inline partitions, ordering and bounded
       ``ROWS``/``RANGE`` frames; nested sources remain scoped. Named windows,
       ``GROUPS`` frames and unreviewed window functions are rejected.
     - ``test_analytics_clickhouse.py``: four window semantic/isolation tests;
       ``test_compiler.py``: accepted and rejected window forms.
   * - Analytical functions
     - Every name in the function allow-list has a compile probe and a live
       value check, including quantiles, distinct counts, conditional
       aggregates, JSON attributes, date buckets, math and strings. Collection
       aggregates require reviewed bounds (at most 1,000 elements); quantile
       lists contain at most 32 literal levels in [0, 1]. Table/file/network,
       dictionary, side-effect and unreviewed functions remain rejected.
     - ``test_every_approved_function_runs_with_the_expected_value`` and
       ``test_compiler.py`` function probes.
   * - Advisory pre-run estimate
     - Authorized, rate-limited ``POST /tracer/sql-query/estimate/`` compiles
       the same scoped SQL and executes ``EXPLAIN ESTIMATE`` with a separate
       locked read-only estimate principal. No execution record or execution
       principal slot is consumed. Missing estimate credentials, contention
       and timeouts yield an unavailable advisory; Run remains available.
     - Live estimate and principal-contention tests in
       ``test_analytics_clickhouse.py``; permission/limiter tests in
       ``test_permissions.py``; ``QueryEstimate.test.jsx`` and the editor tests.
   * - Example gallery
     - Twelve one-click SQL examples, each compiled and executed with exact
       column/value oracles: model cost/tokens, daily latency percentiles and
       errors, slow traces, sessions per user, evaluator/locale scores,
       annotation comparisons, derived metrics, session-score joins, user
       spend ranks, sampled/all comparison and retention-style cohorts.
     - ``test_every_gallery_example_returns_the_expected_rows``;
       repeated-evaluation regression and editor insertion tests.
   * - Personal result reuse
     - An identical ad-hoc request can return its existing completed snapshot
       without a new scan. Keys include actor, organization, workspace,
       project, current memberships, normalized SQL, exact UTC interval,
       catalog version, source connection, evaluation scope and limits.
       Saved/dashboard runs preserve fresh-execution behavior.
     - ``test_permissions.py`` reuse isolation/invalidation tests;
       ``test_result_reuse_answers_without_a_scan_and_only_for_the_same_person``
       on PostgreSQL and ClickHouse.
   * - Regenerable capability corpus
     - Fixed accepted and rejected SQL plus submit/worker/poll, queued cancel,
       saved definitions, CSV/Parquet, estimate and reuse. Accepted queries
       must produce seeded, nonempty results. Rejections include a reason.
     - ``test_capability_matrix_is_regenerated_live`` and
       ``test_capability_check.py``.

The estimate is an index-granule upper bound, not a billing quote or a count of
matching rows. Sources whose actual primary index does not lead with project
ownership withhold parts/marks/row estimates; raw granule row counts are never
returned. ``ORDER BY`` alone is not sufficient: an explicitly shorter or empty
``PRIMARY KEY`` is withheld even when the sorting key contains the project.
If any source is withheld, the query-wide ``exceeds_scan_bound`` is ``null``
("scan limit not checked" in the editor); withheld row counts never contribute
an over/under-limit signal. Fully estimated queries retain the advisory scan
threshold check. Neither case changes the server's enforced read limit.
Project-keyed sources report parts, marks and marks times index granularity.
Boundary granules can still include adjacent projects: the live regression
measures unchanged counts for foreign-only growth outside that key range and
bounded boundary-granule changes, **not** universal independence from other
projects' storage. Estimate/actual accuracy within a particular ratio is not
claimed. ``SQL_WORKBENCH_ESTIMATES_PER_MINUTE`` defaults to 30 per actor.

``SQL_WORKBENCH_RESULT_CACHE_SECONDS`` defaults to 60 (0 disables reuse; maximum
3,600). This changes only repeated ad-hoc submissions; authorization, scan,
result and concurrency limits remain unchanged. A hit retains the original
execution id, exact window, rows and timestamps; it is not a fresh read, and
changes within the TTL may not appear. Both endpoints must match exactly after
UTC normalization, including microseconds: disjoint or shifted windows never
reuse each other's rows. Rolling editor windows therefore normally scan anew.
An unchanged selected saved query uses ``run-saved`` and always scans anew;
edited SQL uses the ad-hoc route with the requested exact interval, not the
stored definition. Membership or catalog changes cause misses;
changes detected between admission, worker start and completion prevent that
execution from populating the old cache key. No cache entry is shared across
actors, tenants or projects, and expired/failed/cancelled results are not reused.

The annotation-agreement example counts each (trace, span) once **within each
label/evaluator pair**. It selects the latest human annotation per label and
latest non-null span pass result per evaluator, ordered by ``(created_at, id)``
within the requested window. Shares have a separate denominator per pair;
counts from different pairs must not be added as unique spans. Multiple labels,
evaluators, repeated runs and conflicting latest values have live value oracles.

The sampled/all example de-duplicates repeated evaluations by (trace, span),
using the latest known inclusion probability. It is illustrative weighted
arithmetic for one sampling design, not evidence of unbiased pooling across
runs or of confidence-interval coverage.

To regenerate the machine-readable capability matrix in an isolated test
environment with real PostgreSQL and ClickHouse::

  python futureagi/scripts/sql_workbench/capability_check.py --out capability-matrix.json
  # Add --reuse-db only for a verified test database owned by this test run.

Stdout is the JSON matrix; test diagnostics go to stderr. A mismatched status,
failed query or missing matrix exits nonzero. Dispatch uses the real activity
body on a local worker thread: this does not qualify the Temporal service,
HTTP transport or browser end to end.

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
  foreign targets never grant access. Ownership comes from every configuration
  the project owns, *including soft-deleted ones*: a later deletion does not
  silently drop historical scores. Those rows are kept and flagged with
  ``config_deleted = 1`` (Observe hides them); filter on it to match Observe.
  At most 1,000 configurations (deleted ones count) are admitted. A project
  with more never gets a partial dataset: the catalog marks ``eval_results``
  unavailable with that reason, and a query that reads it is rejected with
  HTTP 400 and the same message. The other datasets keep working.
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

* SQLGlot 28.10.1 (the pinned version) parses the ClickHouse dialect. Exactly one SELECT/UNION statement,
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
* Names on the right of ``IN`` are a table read in ClickHouse, not a column:
  ``x IN spans``, ``x IN (spans)``, ``x IN db.table``, ``x GLOBAL IN t``, a table
  alias or a CTE name each read a whole relation. SQLGlot parses them as an
  ``IN`` over a column, so they are never rewritten to a scope. The right side
  of ``IN``/``NOT IN`` therefore has a positive allow-list: a SELECT subquery,
  or a list/tuple/array of constant expressions (literals, casts and scalar
  functions over literals). ``GLOBAL IN`` and the function forms (``in``,
  ``globalIn``, ``notIn``, ``nullIn`` ...) are rejected, as are ``FROM (name)``,
  ``EXISTS``, ``ANY``/``ALL``, lambdas, ``ARRAY JOIN``, ``SAMPLE``/``FINAL`` and
  every function that takes a table, dictionary or setting name (``dictGet*``,
  ``dictHas``, ``joinGet*``, ``hasColumnInTable``, ``getSetting``, table
  functions), in every clause (WHERE, SELECT, HAVING, ON, ORDER/GROUP BY,
  window and CASE positions, CTEs, UNION branches, scalar subqueries).
* Every user CTE is renamed to a generated ``__sql_cte_N`` name before
  emission, so no user-chosen name reaches ClickHouse as a table identifier.
  After compiling, the emitted text is verified independently: each
  server-generated scoped source must appear exactly as many times as it was
  referenced, and the remaining (user) part is re-parsed with the same dialect
  and must contain no table except a generated CTE name defined in the same
  statement, no IN-table form, no table-name function, no qualified column and
  no unreviewed syntax. Any doubt fails closed with ``UnsafeQuery``. A compiled
  statement larger than the profile's 256 KiB ``max_query_size`` is rejected
  before it is sent (for example, many references to ``eval_results`` with
  1,000 configurations).
* Server-side second barrier: each execution also sends ClickHouse
  ``additional_table_filters`` with the scope's intrinsic ownership predicate
  for every relation the service principal can be granted (the six sources,
  both evaluation stores, the derived-metrics view and its two tables, and the
  ``<table>_local`` shard tables of a cluster installation). ClickHouse applies
  it to every read of those tables in the query, independently of the AST
  rewrite. On the owned ClickHouse 25.3.14.14 server it filtered bare, quoted,
  parenthesized and database-qualified ``IN``-table reads, ``GLOBAL IN``, the
  ``in()``/``globalIn()`` functions, alias and CTE names on the right of ``IN``,
  ``FINAL``, joins, ``EXISTS``, scalar subqueries and reads through a view (the
  live test ``test_server_table_filters_block_unscoped_sql_under_the_locked_profile``).
  ``test_server_table_filters_cover_distributed_and_shard_reads`` repeats raw
  reads, ``FINAL``, ``IN``/``GLOBAL IN`` subqueries and a join against a
  Distributed ``spans`` over ``spans_local`` on a one-node cluster of the same
  server, reading both relations directly; a multi-node cluster has not been
  tested. The predicates
  deliberately omit deletion flags and event times, so a filter can never hide
  the newest version of a row before ``FINAL`` merges it. Limits: the barrier
  covers only the listed relations (the principal is granted nothing else), a
  query-level ``SETTINGS`` clause could replace it (the AST rejects every
  SETTINGS clause), and it is an ownership filter, not the time window.
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
* Jobs expire in the queue after 90 seconds. When a worker starts a job, its
  deadline is re-anchored to that start (45 seconds, the activity limit), so a
  long queue wait never shortens or kills the execution itself. Cancelling a
  queued job frees its slot at once. Cancelling a running job marks it
  cancelled but keeps its admission slot until the worker confirms the native
  query stopped (the watchdog polls every 0.5 s) or the execution deadline
  passes; the cancel request waits up to 3 seconds for that confirmation, and
  a lost worker cannot hold the slot beyond the deadline. A malformed
  execution or saved-query id, in a URL or an MCP tool argument, returns 404;
  it never reaches a database query. Execution is capped at 30 seconds,
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
  The runner verifies every server setting before executing user queries and
  sends exactly one per-query setting, the scope's ``additional_table_filters``.
  The provisioned profile marks only that setting ``CHANGEABLE_IN_READONLY``;
  every other assignment is still refused by the server (code 164), and the
  runner refuses to execute without the filter. Existing service principals
  provisioned before this setting must be re-provisioned (``ALTER USER`` with the
  emitted profile); otherwise execution fails with the locked-profile message.
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

Apply Django migrations through ``tracer/0119_sql_execution_result_reuse`` using
the normal bootstrap job (including ``0117_sql_workbench`` and its merge migration).
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

Advisory estimates require a **different** application-only principal. Without
it the estimate is unavailable; the execution connection is never a fallback::

  export SQL_WORKBENCH_ESTIMATE_CH_PASSWORD='<another random password, at least 24 characters>'
  python manage.py provision_sql_service --estimate --principal futureagi_sql_estimate \
    --client-cidr 10.20.0.0/24 > sql-estimate.sql
  # Review and apply with the operator's client, as for the execution account.

Configure ``SQL_WORKBENCH_ESTIMATE_CH_USER=futureagi_sql_estimate`` and
``SQL_WORKBENCH_ESTIMATE_CH_PASSWORD`` on the backend. TLS/host/port/database are
the same as execution. The estimator gets the same explicit source SELECT grants
and scope-filter barrier, but only **one concurrent statement**, one thread and a
five-second server ceiling. Overlapping estimates fail fast instead of waiting or
occupying execution's two per-user slots. No execution ceiling or retry window is
raised. This isolates admission slots, not the server's CPU, memory or global
limits; shared-server latency isolation is not claimed. Existing deployments
must provision the separate account to retain estimates. The generator prints
DDL only; it does not change running users or grant privileges automatically.

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

The compiler suite also runs a seeded property test
(``tracer/sql_workbench/tests/test_compiler_fuzz.py``): 1,500 variants of IN
operators, right-hand sides, quoting, case, comments, whitespace, database
qualification and query positions must each be rejected or compile to a
statement whose only table reads are the generated scoped sources. To check a
SQLGlot upgrade before it lands, run the compiler suites with the newer SQLGlot
first on ``PYTHONPATH``; 28.10.1, 30.23.0 and 30.24.0 each passed the
compiler and property suites unchanged (1,391 variants rejected, 109 compiled
and verified as fully scoped, identical across the three). The live suite
(``test_clickhouse.py``) compiles and executes the whole adversarial corpus
against two tenants with sentinel rows and asserts no foreign value is
returned. On the owned 25.3.14.14 server (2026-10-22) its 1,682 queries split
into 1,557 rejected by the compiler, 118 executed and 7 refused by the server
itself; none returned a foreign value.

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
fallibility perturbations; multi-node distributed-table behavior (only a
one-node Distributed table on a single server is tested); 1-billion-span latency and
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

Infrastructure this profile needs (none of it exists locally, so it has not
been run):

* a dedicated deployment of this release: API, the Temporal
  ``exact_aggregation`` worker, PostgreSQL and a production-shaped ClickHouse
  (the cluster topology under test, with its Distributed sources), not a
  shared developer database;
* the SQL service principal provisioned with ``provision_sql_service`` on every
  coordinator, and ``SQL_WORKBENCH_CH_USER``/``SQL_WORKBENCH_CH_PASSWORD`` set;
* one project holding at least 1,000,000,000 spans, with sessions and
  evaluation scores ingested continuously into the rolling one-hour window for
  the full 24 hours (the profile query joins ``eval_results`` to ``sessions``);
* a fixture manifest (``project_id``, ``span_count``) produced by the ingestion
  fixture's own verified row count, kept with the results;
* an API bearer token of a member of that project (``SQL_BENCHMARK_TOKEN``) and
  a client host that can reach the API for 24 hours.

The client exits non-zero if any sample fails, never treats an empty join as a
success, and reports ``fixture_count_independently_verified: false`` because it
cannot count the fixture itself. ``tracer/sql_workbench/tests/test_benchmark_harness.py``
runs the client against a local fake API to keep it runnable; that is not a
measurement.

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

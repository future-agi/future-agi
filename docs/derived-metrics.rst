Persisted derived metrics
=========================

Scope
-----

TH-8696 adds versioned formulas over persisted evaluation outputs and numeric
span fields. This is a generic enterprise feature, not a definition of any
customer-specific business metric. The example is a resolution ratio::

    ratio(resolved == 1 and escalated == 0, resolved >= 0)

The two aliases can bind separate evaluators (including their exact
``config_hash``) or numeric/boolean span attributes. Empty evaluator hashes
explicitly select legacy unversioned results; they do not mean “any version”.

Product path
------------

Observe navigation -> Derived metrics supports creating, validating, saving a new
version, previewing a closed window, running/persisting, reading paginated
session/user/cohort/day results, and recomputing a frozen run. The form exposes
selection unit, inverse-probability weighting, cohort attribute, exact dimension
filters, and an optional evaluation-task identifier. A saved run can be added to
an existing dashboard engine as a metric card. Monitors use existing alert logs,
threshold checks and email/Slack delivery, rather than a separate alert engine.

A dashboard metric pins a completed run. The plotted point is the value of the
entire run window, placed at its end; it is never a mean of daily ratios. Global
date filters select this observation, not a different population. To change the
population or formula, create a new run/version. The adapter rejects mixing
frozen results with live-source filters/breakdowns or re-aggregating the scalar.

Semantics
---------

* ``ratio(a, b)`` is ``sum(a / p) / sum(b / p)`` over complete cases.
* ``mean(x)`` is ``sum(x / p) / sum(1 / p)``.
* ``sum(x)`` is ``sum(x / p)``; ``count_if(predicate)`` is
  ``sum(indicator(predicate) / p)``. These are estimated totals when weighted.
* ``weighting=none`` explicitly sets ``p=1``. Weighted runs require persisted
  evaluation sampling metadata; missing/zero/negative/non-finite/greater-than-one
  probabilities do not fall back to 1. Inputs must agree on inclusion probability
  and selection unit. Conflicting probabilities invalidate the affected rollup.
  The exact shared metadata path is ``output_metadata._task_selection.sampling``;
  ``sampling_unit_id`` must equal ``<unit>:<canonical survivor id>``. Every input
  and observed unit must share the same frozen ``cohort_id`` and ``policy_version``.
  Equal probabilities from independent cohorts are insufficient and are rejected.
  Membership mode, a normalized sampling window covering the requested window,
  and closed-cohort freeze/population provenance must also agree. Sampling scope
  appends a digest of that selection lineage; equal cohort IDs alone are not proof.
  Live-rate policies may have an explicit JSON-null window end; missing, empty or
  malformed end values are not treated as an open window. Closed cohorts still
  require a finite end and recorded freeze/population evidence.
  ``sampling_scope`` persists this cohort/version identity for SQL consumers.
* Each input is independently reduced per selection unit (latest/mean/min/max),
  before inputs are joined. Repeated evaluator executions are collapsed by
  persisted result identity, then by target/config/version. A session-target
  evaluator joins one distinct session membership row, never every child span.
* All formula input aliases must be used. A unit with absent/non-finite inputs or
  undefined row arithmetic is excluded as a complete-case omission and counted in
  ``missing_count``. This is explicit missing-data treatment, not imputation and
  not a guarantee against missing-not-at-random bias.
* Empty input populations, missing inputs, invalid probabilities and zero
  denominators have distinct statuses and null values. A valid zero remains 0.
  Numerator, denominator, unit count and validity diagnostics are persisted.
* Day attribution is the UTC date of the unit's earliest in-window span. This is
  not the evaluation completion date. A unit spanning days is counted once.
* Spans are identified by the pair ``(trace_id, span_id)`` within the project,
  including version collapse, membership, frozen span units and span-target eval
  joins. Reusing a span ID in another trace cannot collapse or cross-attach it.
  If persisted sampling metadata identifies only a bare span ID that occurs in
  multiple traces in the population, weighted span metrics fail closed rather
  than guess the sampler's draw. Session/user sampling remains canonical-unit
  scoped.
* Session/user IDs use the existing canonical-survivor remap helpers. A session
  assigned to multiple users is not guessed when joining a session evaluation to
  a user selection unit. Finer-grained rollups that cannot represent a whole
  selected unit are absent, rather than duplicating that unit.
* A cohort is an exact string span attribute. A unit with conflicting cohort
  values has a typed mixed-cohort key; value/missing/mixed keys are encoded
  separately so arbitrary literal strings cannot collide. An empty cohort key uses the persisted sampling
  stratum when weighted, and one all-unit cohort when unweighted.

Reproducibility and storage
---------------------------

PostgreSQL stores tenant/project ownership, immutable definition versions, the
recipe hash, request idempotency keys, execution leases and run manifests.
ClickHouse stores immutable per-attempt unit-input snapshots, calculated rollups,
and a committed-attempt pointer. No Python loop scans raw spans or eval results.
All population reduction, weighting and rollup computation is database pushdown.

A first execution captures persisted source values under a server-owned capture
watermark. ``as_of`` is the capture observation cutoff, NOT a claim of historical
time travel. ReplacingMergeTree may have already discarded earlier versions of a
mutable source row. Consequently historical replay only reads the run's frozen
input snapshot. It reuses the exact definition/evaluator versions, unit population,
input values, probabilities, filters and original window. New late arrivals require
a new idempotency key/run and therefore a new snapshot. Window boundaries retain microseconds. Snapshots deliberately have
no TTL while their runs remain available.

Failed attempts are unpublished. A retry writes to a new attempt ID and a
successful replay atomically selects one result attempt in the application
manifest. Publication uses a durable per-run sequence, not wall-clock ordering.
Rollups read frozen units in unit-ID order on one query thread. Schema/compiler
versions and the captured definition hash must match before a replay can proceed.
A failed or in-flight replay retains access to the previous committed result.
The ``derived_metrics`` ClickHouse view joins the committed-attempt
table, hiding abandoned/partial attempt rows and older replay attempts. A
PostgreSQL/ClickHouse process failure at the publication boundary remains an
operational reconciliation consideration: this is not a distributed transaction.
Orphan attempt retention/garbage collection is deliberately not automatic in this
change; no deletion job may discard inputs needed for replay.

The SQL workbench adapter (TH-8685) consumes the logical ``derived_metrics`` view,
scoped by ``org_id``, ``workspace_id`` and authorized ``project_id`` values.
The compatibility field ``source_run_id`` is the source evaluation-task ID; the
metric run ID, frozen snapshot and sampling scope carry execution evidence. Its
catalog must report a missing schema dependency explicitly, not silently present
an empty dataset. Metric definitions, direct results and dashboards work without
that sibling feature. Weighted base definitions fail explicitly on absent
sampling metadata rather than depending silently on TH-8689/TH-8690.
Attribute/contact or trace sampling units are not accepted as session/span
probabilities: those additional selection-unit adapters remain pending.

Safety, scale and operations
----------------------------

The expression parser accepts only a small bounded AST: numeric/boolean constants,
input aliases, arithmetic, comparisons and boolean operators under one supported
aggregate. There is no eval/exec, attribute access, indexing, arbitrary functions,
Python callback or user-provided SQL. All values, filters and attribute keys are
bound parameters. Inputs, expression nodes/depth, filters and source windows are
bounded (16 inputs, 128 nodes, depth 16, 10 run filters, 31 days).

Definitions/results/jobs are scoped to current organization/workspace/project
access. Viewers read; workspace members write/run. Workers reauthorize before
computation and again before publication so moved/deleted projects cannot retain
an old workspace's access. Dashboard and monitor adapters reauthorize saved
references. Monitor evaluations commit a durable unique ``(monitor, run)`` ledger
row alongside their alert logs under a monitor-row lock. The last-run field is a
status pointer, not the idempotency key: run A -> B -> recompute A does not emit A
again. Healthy numeric evaluations are also consumed once; missing values are
not consumed and never alert as zero. An exception rolls back the ledger and log
so a subsequent delivery can retry. Notifications reuse the existing transport behavior;
this change does not claim exactly-once external email or Slack delivery.

The single-slot exact-aggregation worker executes each bounded plan with native
ClickHouse abort budgets: 60 seconds/statement, 512 MiB query memory, 20 million
source rows / 8 GiB read, one million groups and two threads. Abort modes are “throw”,
never partial-success truncation. Interactive previews use the same budgets.
Results are keyset-paginated, at most 100 rows. Opaque cursors bind the committed
attempt and rollup, including empty cohort keys. Cohort keys above 2048 bytes
fail explicitly rather than truncating or merging categories. Broad workloads that exceed the
budget must use smaller closed windows. This is a correctness-preserving workload
limit, not a claim to have benchmarked a billion-span population.

Deployment applies additive PostgreSQL migrations and
``028_derived_metrics.sql`` through the existing schema runner. Existing raw span
and evaluation schemas are not rewritten. Provision the ClickHouse schema before
enabling writes, and register the existing exact-aggregation task queue. An
application writer connection is required for snapshot persistence; readonly-only
deployments fail explicitly. PostgreSQL migrations add new empty tables and one
nullable monitor field, without backfilling historical data. Apply the delivery
ledger migration before enabling derived monitors; historical delivery identities
are not fabricated from a last-run pointer. Application rollback
leaves ClickHouse evidence intact; do not drop snapshot tables to roll back code.

Verification
------------

From ``futureagi/`` with an isolated test PostgreSQL database and the repository's
guarded ClickHouse test sidecar::

    DJANGO_SETTINGS_MODULE=tfc.settings.test pytest \
      tracer/tests/test_derived_metric_formula.py \
      tracer/tests/test_derived_metric_clickhouse.py \
      tracer/tests/test_derived_metric_api.py
    NO_STARTUP_DB_MUTATIONS=false DJANGO_SETTINGS_MODULE=tfc.settings.test \
      python manage.py makemigrations --check --dry-run

The executable SQL/API fixtures can also run with ``DERIVED_METRIC_TEST_CHDB=1``
and an installed ``chdb`` package. This is an explicit local-only opt-in, not a
mock SQL engine. The default path retains the conftest sidecar guard. Embedded
fixtures use a minimal physical source schema with matching column types; they do
not replace full production-topology and deployment-schema verification.

From the repository root / frontend as appropriate::

    scripts/generate-openapi-schema.sh
    cd frontend && yarn contracts:generate && yarn contracts:check
    yarn lint

Pending acceptance evidence must be recorded in the pull request: real browser
flow and fallibility runs, full deployment topology/scale testing, and cross-PR
SQL/sampling integration. A passing small fixture is not full-scale evidence.

Acceptance checklist
--------------------

Implemented:

* Saved immutable formula versions and exact evaluator-hash bindings.
* Safe arithmetic/condition compiler; ratios, means, sums and conditional counts.
* Frozen canonical-unit inputs; independent evaluator reduction before joining.
* Inverse-probability weighting with coherent persisted cohort/policy identity.
* Persisted overall/session/user/cohort/day results and bounded keyset reads.
* Idempotent runs, lease recovery, retained committed history on failed replay.
* Create/edit/validate/preview/run/recompute UI with explicit window and filters.
* Stored values in the existing dashboard renderer and existing alert transports.
* Tenant/workspace/project and writer/requester authorization, including indirect
  monitor writes and reauthorization before job publication.
* Official schema/client generation, executable SQL and API integration tests.

Not claimed by this change:

* Historical time travel over overwritten raw ClickHouse records.
* Pooling independently sampled cohorts with unknown union probabilities.
* Attribute/contact and trace sampling-unit adapters (mismatches fail closed).
* Automatic rolling recomputation schedules or a new alert-delivery engine.
* A distributed PostgreSQL/ClickHouse transaction or automatic orphan cleanup.
* Billion-span performance, deployment-topology qualification, or exactly-once
  external notifications.

Browser E2E and its fallibility proof remain an acceptance gate, not something
inferred from backend/API tests. The pull request must carry the actual execution
result, including any missing stack dependency or unexecuted scenario.

Approved browser flow
---------------------

``e2e/flows/dashboards/derived-metric.spec.ts`` defines DASH-E2E-017. It drives
formula validation/save, monitor creation, preview/run, persisted-result display,
dashboard creation/reload and a scoped alert log. The four ``EXPECTED_*``
constants are assertion-only fallibility anchors: alter each independently,
observe the corresponding failure, restore it and rerun green. They never feed
seeding or requests. This standalone flow is unweighted and does not establish
weighted sibling integration.

Reproducible gates::

    cd e2e && yarn typecheck && yarn catalog:check
    bin/e2e test flows/dashboards/derived-metric.spec.ts

The second command runs from the repository root against branch-built images.
The managed browser run and all four one-sided fallibility runs must be reported
separately from typechecking/catalog generation. PR marker: ``E2E: new DASH-E2E-017``.

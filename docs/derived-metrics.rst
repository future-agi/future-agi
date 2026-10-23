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

The editor binds an evaluation input through an evaluator picker backed by
``GET /tracer/derived-metrics/evaluators/?project_id=…`` (project-scoped,
server-side name search, at most 100 rows with an explicit ``truncated`` flag).
Selecting an evaluator fills BOTH ``config_id`` and the ``config_hash`` that the
evaluator's CURRENT definition persists its results under (the same
``resolved_config_hash`` the evaluation runner writes). Results produced under an
earlier definition carry a different hash; the editor warns when a bound hash no
longer matches the listed definition. The raw ID/hash fields remain available as
an advanced fallback. No previously existing API exposed the resolved hash, which
is why this narrow read endpoint exists.

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
* Span measures distinguish "measured" from "not reported". The v2 ``spans``
  store keeps ``latency_ms``, ``cost`` and the token counts in non-Nullable
  ``DEFAULT 0`` columns, so a stored 0 alone cannot say whether anything was
  measured. The rules are:

  - ``latency_ms`` is missing when the span has no ``end_time`` (it never
    ended) and no latency was stored for it; the collector writes a latency
    only for an ended span, so that 0 is the column default. A span that ended
    with 0 ms keeps a real 0, and a non-zero latency stored without an end time
    (a provider import for a call with no end timestamp yet) is kept as a
    reported measurement.
  - ``cost``, ``total_tokens``, ``prompt_tokens`` and ``completion_tokens``
    treat a stored 0 as NOT REPORTED (missing); only non-zero stored values are
    measurements. Tool/chain spans and unpriced models therefore no longer pull
    means toward 0, and sums are unchanged. A genuinely free call or an empty
    completion is also excluded; to count explicit zeros, bind an
    ``attribute_number`` input to the reported attribute (for example
    ``gen_ai.cost.total``): an absent attribute is missing and a present 0 is a 0.
  - Missing measures are complete-case omissions counted in ``missing_count``
    (an all-missing population reports ``missing_inputs`` with a null value),
    never averaged in as zero. Per-unit ``mean``/``min``/``max`` reduce over the
    spans that reported a value. ``latest`` is the latest span's own value and
    does not fall back to an earlier span, so a session whose latest span
    reported nothing is missing for that input.
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
* Definitions that can only produce empty data are rejected at validate/save
  time with a field-specific error, rather than saved and later reported as
  all-missing: inverse-probability weighting without an evaluation input,
  inverse-probability weighting with an evaluation input whose ``config_hash`` is
  empty (it would read only legacy unversioned results, which carry no sampling
  metadata; the error names each such alias), and two different exact values for
  the same filter field (filters are ANDed). A run or preview whose filters
  contradict the saved version's own exact filter is rejected the same way.

Uncertainty
-----------

Every persisted rollup row carries a design-based standard error and a two-sided
95% interval, or ``ci_status = 'not_estimable'`` with a ``ci_reason``. The
estimators, with the selection unit as the primary sampling unit, are documented
in ``tracer/services/derived_metrics/uncertainty.py``:

* ``stratified_wor_fpc`` (closed sampling cohorts): a closed window draws
  ``n_h = p_h * N_h`` units per stratum without replacement, and both the inclusion
  probability and the stratum ``population_size`` are frozen with each unit
  (``derived_metric_inputs.sampling_design`` / ``population_size``). Ratios and
  means are linearized (``u = y - R*x``); the finite-population correction
  ``1 - p_h`` applies; sampled units outside a rollup group (a narrower window, a
  filter, another group, missing inputs) contribute ``u = 0`` (domain estimation).
* ``poisson`` (live policies, which keep each unit independently with
  probability ``p``): ``V = sum((1 - p) * (u/p)^2) / X^2``.
* ``iid_units`` (unweighted ratios and means): a model-based interval that treats
  the window's units as independent draws of the underlying process. An
  unweighted total of every unit is an exact count and has no interval.

Rates (a 0/1 numerator inside a 0/1 denominator) use a Wilson score interval at
the design-effective sample size ``p(1-p)/V``; other metrics use
``value +/- t * SE``. Both use ``t(0.975, units - strata)``. A weighted window whose
units were all kept with certainty is a census with a zero-width interval.

No interval is produced when the design cannot support one: the snapshot predates
design capture, designs are mixed, a closed stratum has a single sampled unit or
no population size or an inconsistent probability, fewer than two units are
valid, the value itself is missing, or expanded second moments suffer numerical
cancellation (``unstable_variance``). The same reason withholds non-census,
non-rate intervals when the estimated variance is zero, including all-zero
``sum`` and ``count_if`` samples. No observed events do not prove a zero population
total: the zero point estimate remains available, but cannot trigger a confident
alert without an interval. A boundary-aware total interval is not implemented.
The rare-event regression reports withheld draws separately from coverage among
issued intervals; withholding an interval is not counted as covering the truth.
A stratum with more valid units than its recorded sample size is inconsistent. Intervals are conditional on the documented
sampling design (or the explicit independent-unit model); evaluator error,
nonresponse bias, and dependence between distinct selection units are not modelled.
They are fixed-window, approximate intervals, not confidence sequences.

The interval is persisted (``std_error``, ``ci_low``, ``ci_high``, ``effective_n``
(Kish), ``ci_df``, ``ci_method``, ``ci_interval``, ``ci_status``, ``ci_reason``),
readable through the ``derived_metrics`` SQL view, returned on previews and, with
``include_uncertainty=true``, on persisted results, and shown on dashboard metric
cards as text beside the value. Persisted rows from before this column existed
report ``not_computed``.

Calibration is measured, not assumed. ``tracer/tests/test_derived_metric_uncertainty.py``
draws 2,000 seeded replicates per design from known synthetic populations,
computes each replicate with the production rollup SQL on ClickHouse, checks
every replicate against an independent NumPy reference to 1e-9 relative, and
requires empirical coverage within [92%, 98%], bias within 4 Monte Carlo standard
errors and mean estimated variance within 15% of the empirical variance. With
seed 20261023 and its four successive seeds on ClickHouse 25.3, measured coverage
was 93.10% (closed stratified rate), 93.85% (live continuous ratio), 94.65%
(closed total), 93.90% (unweighted process mean) and 95.35% (near-boundary rate).
These are calibration results for those fixtures, not guaranteed coverage for
arbitrary populations. The closed-rate weighted bias was 0.00028525 versus
-0.17443587 for the unweighted estimator on the same draws.

Contributor evidence and version comparison
-------------------------------------------

``GET /tracer/derived-metric-runs/{id}/contributors/`` reads only the authorized
run's frozen snapshot. Select ``rollup`` and ``group_key``; ``limit`` is 1–100
(default 20). Units are ranked by absolute leave-one-out influence, with unit ID
as a deterministic tie-break. The response includes weight, raw and weighted
numerator/denominator, trace/session identity, missing-input counts and bounded
example IDs. ``truncated`` reports an incomplete top-K list; counts cover all
units. Removing the last denominator produces no influence, not zero. The result
page's optional contributor table leaves the existing result columns unchanged.

``GET /tracer/derived-metric-runs/{candidate}/compare/?baseline_run_id={baseline}``
compares two committed runs of the same metric, window, filters, task and unit
grain, including different formula versions. It returns candidate minus baseline
and both committed attempt IDs. It never re-reads live telemetry. Identical
frozen formulas and verified census results have exact delta intervals. Census
verification reads both authorized frozen snapshots under the existing query
budgets: every unit must have a captured closed/live design, valid probability
exactly one, and the unit count must match the persisted overall result. Zero
standard error or a zero-width marginal interval alone is never census proof;
legacy, absent or partially retained snapshots without that proof are not
estimable. Other deltas return ``paired_covariance_unavailable``: marginal intervals alone cannot identify
covariance between overlapping samples. General paired delta intervals are not
implemented, and independence is never assumed silently.

Confidence-aware monitors
-------------------------

``alert_when`` defaults to ``point``, preserving existing threshold behavior.
For derived metrics only, ``confident`` requires the whole interval beyond a
static above/below threshold. Above compares the lower bound; below compares the
upper bound. Critical and warning precedence and the existing per-run delivery
ledger remain unchanged. An absent interval cannot trigger a confident alert.
This is a fixed-window test; repeated and overlapping windows have no sequential
or multiple-testing correction. Email/Slack delivery remains best-effort.

``test_derived_monitor_confidence.py`` measures both noise suppression and power:
seed 20261024, 2,000 independent 50-unit windows per state, true rate 0.92 versus
a 0.90 threshold produced 397 point alerts and 17 confident alerts. A shift to
0.75 was detected within three windows in 99.40% of 666 disjoint three-window
replicates. This uses the production estimator and threshold code; notification
transport is stubbed. It does not establish delivery performance or a sequential
false-positive guarantee.

Reproducibility and storage
---------------------------

PostgreSQL stores tenant/project ownership, immutable definition versions, the
recipe hash, request idempotency keys, execution leases and run manifests.
ClickHouse stores immutable per-attempt unit-input snapshots, calculated rollups,
and a committed-attempt pointer. No Python loop scans raw spans or eval results.
All population reduction, weighting and rollup computation is database pushdown.
ClickHouse inlines a ``WITH`` clause at every reference, and every read of one
query shares that query's read, memory and time budget. The snapshot therefore
reads the evaluation log once, through one keyed span/session target map, and
bounds its evaluation prefilter with span-only keys rather than re-reading the
remapped membership relation. A regression test pins these read counts.

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

Retry and ordering. A run notifies its monitors when it commits. Each scheduled
monitor tick then inspects only the HEAD: the newest committed run of the
current version (``as_of`` then id order) whose window has ended. It is delivered
when the monitor has no ledger row for it and either its window ended inside
the monitor's ``alert_frequency`` (the original rule) or it committed while the
monitor existed and less than 48 hours ago (a recompute commits again). The
second branch is what retries a delivery that failed at commit time even when
the window ended long before ``alert_frequency``. An older run is never
delivered once the monitor evaluated a newer run of the same version, on either
path, so the status pointer and alerts never step backwards; a newest run with a
missing value alerts nothing and does not fall back to an older value. Each tick
reads at most one stored result.

List order. Metric and run lists are newest first by ``created_at`` with the id
only as a tie-break; ``before`` stays the id of the last row of the previous page
and is resolved inside the caller's scope, so pages neither skip nor repeat rows
created while paging, and an unknown or out-of-scope cursor is rejected.

The single-slot exact-aggregation worker executes each bounded plan with native
ClickHouse abort budgets: 60 seconds/statement, 512 MiB query memory, 20 million
source rows / 8 GiB read, one million groups and two threads. Abort modes are “throw”,
never partial-success truncation. For a session or user metric with evaluation
inputs, the evaluation-target map holds one group per span plus one per session,
so the group budget covers both in the window. Uncertainty first aggregates by
(rollup group, sampling stratum); that intermediate cardinality is subject to the
same group budget and fails closed rather than dropping strata.

Interactive previews run synchronously on a web worker, so they are bounded more
tightly: 15 seconds, 256 MiB memory, 5 million source rows / 2 GiB read, 250,000
groups and 10 result rows (all “throw”), and they return only the overall row. A
preview that exceeds its budget returns HTTP 400 on ``window`` with guidance to
narrow the window or run and persist it; no partial value is returned. A
cache-backed admission guard (Redis in deployed settings, so it holds across
web processes; per-process with the local-memory cache) allows one concurrent
preview per user and four per organization; a refused preview returns HTTP 429
with ``Retry-After`` and never reaches ClickHouse. Leases expire 30 seconds
after acquisition, so a crashed worker cannot hold a slot indefinitely.
Results are keyset-paginated, at most 100 rows. Opaque cursors bind the committed
attempt and rollup, including empty cohort keys. Cohort keys above 2048 bytes
fail explicitly rather than truncating or merging categories. Broad workloads that exceed the
budget must use smaller closed windows. This is a correctness-preserving workload
limit, not a claim to have benchmarked a billion-span population.

Deployment applies additive PostgreSQL migrations and
``028_derived_metrics.sql``, ``029_derived_metric_uncertainty.sql``, then
``030_derived_metric_view_refresh.sql`` through the existing schema runner. 029
adds columns idempotently and refreshes the committed-only SQL view; it has its
own receipt, so installations that already applied 028 upgrade without changing
its checksum. The replicated runner distributes ordinary view creation and
replacement with the configured ``ON CLUSTER``. 030 has a new receipt to repair a
stale projection even where an older runner already recorded 029 as applied.
Neither earlier file is rewritten. Local runner execution and replicated emitted
DDL are tested separately; multi-replica deployment remains a qualification gate.
Existing raw span and evaluation schemas are not rewritten. Provision the ClickHouse schema before
enabling writes, and register the existing exact-aggregation task queue. An
application writer connection is required for snapshot persistence; readonly-only
deployments fail explicitly. PostgreSQL migrations add new empty tables and one
nullable monitor field, without backfilling historical data. Apply the delivery
ledger migration before enabling derived monitors; historical delivery identities
are not fabricated from a last-run pointer. Application rollback
leaves ClickHouse evidence intact; do not drop snapshot tables to roll back code.

Public competitive references
-----------------------------

Checked against primary public documentation on 2026-10-21:

* Arize documents AQL custom metrics, weighted composites and ratios, usable in
  dashboards and monitors. This feature must NOT be described as something Arize
  cannot do: https://arize.com/docs/ax/observe/projects/custom-metrics-api
* Arize documents the custom-metric monitor source:
  https://arize.com/docs/ax/observe/production-monitoring
* Galileo documents composite metrics and ``required_metrics`` dependencies:
  https://docs.galileo.ai/concepts/metrics/custom-metrics/composite-metrics
* Galileo documents custom-code evaluation at session/trace/span levels:
  https://docs.galileo.ai/concepts/metrics/custom-metrics/custom-metrics-ui-code
* Galileo's release notes document configurable span/trace/session rollups:
  https://docs.galileo.ai/release-notes

Behaviour-level references for missing data, alert retry and evaluator binding,
checked against primary public documentation on 2026-10-22:

* Langfuse alerts default to “Treat missing data as 0” (alternatives: keep the
  previous severity, show NO_DATA, notify after sustained NO_DATA):
  https://langfuse.com/docs/observability/features/alerts . Its blob-export
  reference documents ``total_cost`` as ``0`` when no cost was recorded and
  ``latency`` as nullable:
  https://langfuse.com/docs/api-and-data-platform/features/blob-storage-export-fields
* Datadog monitors offer “Evaluate as zero”, “Show last known status”, “Show NO
  DATA”, “Show NO DATA and notify” and “Show OK” for missing data:
  https://docs.datadoghq.com/monitors/configuration . Datadog webhooks retry on
  internal errors or 5XX responses, with missed connections retried 5 times:
  https://docs.datadoghq.com/integrations/webhooks
* Arize AQL custom metrics return one number per query (no ``GROUP BY``); null
  exclusion is written by the author, e.g. ``COUNT(*) FILTER (WHERE … != null)``
  in its rate pattern: https://arize.com/docs/ax/observe/projects/custom-metrics-api
* LangSmith alerts cover average feedback score, average latency and errors, and
  its dashboards chart cost with sum/average/percentile:
  https://docs.langchain.com/langsmith/alerts and
  https://docs.langchain.com/langsmith/dashboards

Design choices made here in that light, each covered by tests in this
repository rather than by a comparison: missing span measures are a counted,
visible status instead of an implicit zero; a missing newest value never alerts
and never falls back to an older value; a failed delivery is retried for a
bounded period without re-emitting an older observation; and a weighted metric
cannot be saved with an evaluator binding that could only read unsampled rows.

These sources establish feature existence, not comparative accuracy, performance,
pricing or implementation details. No unsupported parity or superiority claim is
made.

Verification
------------

From ``futureagi/`` with an isolated test PostgreSQL database and the repository's
guarded ClickHouse test sidecar::

    DJANGO_SETTINGS_MODULE=tfc.settings.test pytest \
      tracer/tests/test_derived_metric_formula.py \
      tracer/tests/test_derived_metric_clickhouse.py \
      tracer/tests/test_derived_metric_api.py \
      tracer/tests/test_derived_metric_hardening.py
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

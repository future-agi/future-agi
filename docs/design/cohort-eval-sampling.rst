Contact-consistent evaluation sampling
======================================

Scope
-----

This implements TH-8689 and TH-8690 as an additive policy on evaluation tasks.
A task's evaluation row (span, trace, session) is independent of its sampling
unit (span, trace, canonical session, canonical user, or a contact attribute).
A span task sampling sessions evaluates every eligible span of a selected
session, including spans in different traces. It does not replace those spans
with one session-level evaluation.

The legacy ``sampling_rate`` / ``spans_limit`` resolver is unchanged when
``sampling_policy`` is null. For policy tasks, per-stratum controls replace those
legacy controls: a row limit must not silently cut a contact in half.

Implemented requirements
------------------------

* Task create/edit/read round-trips a version-1 policy; the existing task page
  contains its policy editor and an authorized population preview.
* Ordered slices support conjunctions of attribute predicates; the first
  matching slice wins. An optional Cartesian group-by (up to four attributes)
  subdivides each slice. Units are counted once, not once in every overlapping
  slice. A final default slice keeps representative controls or explicitly
  selects zero units.
* Attribute sources are typed span attributes (including attributes carrying
  session context), overflow span attributes, resource attributes, and span
  metadata. Attribute keys/values are parameters, never SQL identifiers.
  TraceSession's curated identity table has no arbitrary attribute bag; this
  does not invent a separate native session-metadata store.
* A closed cell supports percentage or target-n, minimum, maximum, and a
  rare-cell census threshold. A maximum takes precedence over a minimum or
  census; no minimum can manufacture nonexistent units.
* A live cell supports percentage only. Exact quotas, floors, caps and census
  thresholds are rejected for live mode rather than represented as exact.
* Canonical session/user identities resolve many-old-to-one-new remaps before
  hashing and grouping. Custom contact IDs can use a project default attribute;
  that mapping is resolved into the policy at save, so subsequent project edits
  do not reinterpret saved policies.
* Missing IDs explicitly exclude a row or sample it independently using a
  namespaced row fallback. There is no accidental shared empty-ID contact.
* Selected membership, stratum, probability, population and immutable policy
  context are persisted. The same metadata survives evaluation result writes.
* Continuous reconciles admit late-arriving eligible rows of previously selected
  units. Closed cohorts never rerank or evict existing members on later polls.
* Queries aggregate units in ClickHouse and stream blocks of at most 1,000 rows.
  Closed expansion transfers only selected member batches, bounded by unit
  count and parameter bytes. Evaluator fanout is also capped per insert batch.
  Membership lookups/inserts and evaluation entries are batched; there is no
  PostgreSQL query for each span.

Precise population contract
---------------------------

Closed ``window`` is an explicit, half-open **event-time eligibility** interval
``[start, end)``, at most 31 days. Live percentage policies instead require
``end: null`` and a continuous task: new contacts remain eligible indefinitely
from ``start`` onward, including after the preview and across future event days.
Task filters further restrict evaluation rows. The policy does not silently
read activity before its start or outside a closed interval. Trace evaluations use root-span attributes; session
identity is represented by the earliest eligible root. Finer sampling units
than the evaluation row are rejected (for example span-unit session evaluation).
Voice-call policy sampling is not enabled by this change.

Each slice's predicates must match one eligible row. A unit is assigned the
lowest matching rule index across its eligible rows. For group-by values the
witness is ordered by rule index, event time and row identity. This makes mixed
attribute contacts deterministic and prevents overlapping cells from inflating
population counts. Unmatched units use the default rule. Missing attributes and
present-but-empty attributes are distinct group-by values.

Closed windows must end in the past. At first successful reconciliation, the
eligible units visible before ``frozen_at`` are ranked within their assigned
cell by the hexadecimal SHA-256 of ``policy_version + ':' + sampling_unit_id``
(with unit ID as a collision tie-breaker). The first k are frozen, where::

    initial = target_n, or ceil(N * rate / 100)
    if N <= rare_below: initial = N
    k = min(N, max_n if present else N, max(min_n, initial))
    inclusion_probability = k / N

Only selected closed members need durable rows. New units first arriving after
that cohort's freeze are excluded even if their event times lie in the window.
Late rows of already selected units keep the original stratum and k/N. A failed
or incomplete stream rolls back cohort membership and entries together; no
partially materialized first page can masquerade as an exact quota.

Live mode uses the same stable SHA-256, compared with the exact integer
threshold ``floor(rate / 100 * 2**256)``. The inclusion probability is rate/100.
Both accepted and rejected first-admission decisions are retained, so a later
span's changed category cannot flip a contact's original decision. Live previews cover the last 24 hours (reported as ``preview_window``); they
are prospective snapshots, not forecasts of an unlimited stream's exact count.
Each live poll discovers changed event days from the project-scoped physical
``_version`` arrival range, then reads only those bounded day partitions. A
backdated span still triggers its old event day. Decisions and policy/cohort
identity persist across those scans; daily scanning does not resample contacts.
Catch-up touching more than 366 event days fails without advancing the cursor.
Live row filters currently support span data; evaluation/annotation and mutable
profile-dependent filters require a closed policy rather than silently missing
non-span change triggers.

There are no automatically recurring exact quotas in this revision: create an
explicit new closed window when another cohort is needed. This restriction does
not stop ongoing percentage sampling, whose policy has no end date. A preview is not a reservation;
the population can change before the first successful execution. No dollar price
is fabricated: the preview returns ``estimated_cost: null`` and explains that
actual evaluator token usage/provider pricing are not fixed by a sampling policy.

Persistence and downstream metrics
----------------------------------

``EvalSamplingCohort`` is unique by task and policy digest and stores the policy,
freeze timestamp and immutable selection context (organization, workspace,
project, evaluation row type, task filters and algorithm). ``EvalSamplingMember``
is unique by cohort and SHA-256 of the canonical/namespaced unit ID. Database
checks require probabilities in [0,1] and strictly positive probabilities for
selected members. Task/config locks serialize edits and pollers.

Every selected ``EvalLogger`` carries ``output_metadata._task_selection.sampling``::

    policy_version           policy + project + row type + filters digest
    cohort_id                immutable population/cohort identity
    cohort_frozen_at         UTC cutoff for closed mode; null for live mode
    membership_mode          closed | live
    sampling_unit            actual unit: span | trace | session | user | attribute
    requested_sampling_unit  configured policy unit
    missing_id_fallback      true when an independent row replaced a missing ID
    sampling_unit_id         namespaced canonical identity
    stratum                  stable JSON cell identity
    inclusion_probability    0 < p <= 1
    population_size          N for closed cells; null for live mode
    window                   {start, end}, UTC

The existing task-selection result-write context preserves this metadata through
success, errors and retries. Trace-filter witnesses are retained alongside it,
so explicit/unique filter-bound evaluation mappings still read the span that
proved the trace predicate, not an arbitrary child. Changing a sampling population requires explicit Delete & rerun (or duplicating
the task to preserve the prior result view). Ordinary edit_rerun is rejected for
policy/filter population changes, and generic PATCH cannot bypass this once
results exist. Fresh reruns archive old entries without rewriting their
probabilities or cohort IDs; all new visible entries have the new population's
provenance. Re-running an unchanged closed policy reuses its frozen membership.
Do not combine multiple cohort versions as if they were one randomized sample.

For a contact-level pass rate, first aggregate evaluation rows to the chosen
unit/cohort, then compute ``sum(pass / p) / sum(1 / p)``. Weighting duplicated spans
before forming contact-level outcomes would give large contacts extra influence.
Zero-rate cells are intentional exclusions, not evidence for an unbiased
whole-population estimate. Missing-ID row fallbacks carry their actual row unit
and an explicit fallback flag; do not combine them into contact-level metrics
without an explicit estimand. Legacy results without a policy have no inferred probability: consumers must
choose an explicit legacy/unweighted policy rather than fabricate metadata.

Security and scale
------------------

The preview resolves its project through the same organization/workspace scoped
queryset as task creation. No policy accepts another tenant's policy/project/
workspace reference. Referenced evaluator configurations retain existing project
checks. The planner receives the authorized project, uses fixed table names,
and binds all external values. Remap expansion is restricted to IDs touched by
that project's finite source window, avoiding a global remap fanout.

Sampling freezes and forward watermarks use the ClickHouse server's UTC clock,
the authority for physical arrival versions, rather than the API host's clock.
Policy cursors are not clamped to PostgreSQL task creation time, so host/DB clock
skew cannot create an arrival gap. Native reads retain microseconds by binding
UTC DateTime64 text: native driver
``datetime`` interpolation otherwise truncates the fractional part. The sampling
proof explicitly opts out of application mode that removes abort limits. Reads
have 30-second wall/server budgets, 512 MiB query memory, spill thresholds,
2 threads, 20-million rows/8 GiB read ceilings and 2-million rows/128 MiB result
ceilings, with overflow set to throw. The preview rejects more than 1,000 cells;
unit IDs and group values are bounded without lossy truncation. Policies are limited to 64 KiB and compiled statements to 2 MiB. Locked
read-only server profiles must independently provide reviewed resource limits
when they disallow per-query settings; the native wall timeout still applies.
These are safety limits, not a claim that every permitted 31-day workload fits them. A failed
proof does not advance the continuous cursor. Actual one-billion-row throughput
has not been established by the correctness fixtures.

Preview boundaries
------------------

The preview is a population snapshot, not a reservation, and returns
``estimated_cost: null`` rather than a provider-price estimate. Closed cohorts
retain their frozen membership; late eligible rows of already selected units
can be admitted on later polls.

Verification and rollout
------------------------

Apply ``tracer/0117_cohort_sampling`` and
``tracer/0117_sampling_project_db_default`` before deploying writers. They are
additive, contain no backfill of guessed probabilities, and have no enterprise
dependency. Pause/drain ingestion while applying both migrations, then resume
collectors: do not leave writers running between the original field addition
and the default repair. The project contact-attribute field retains an
empty-string database default as well as its Django default, so existing Go
collectors can insert projects outside the ORM after the schema update.
Collector upserts must preserve any configured contact attribute. The regression
suite executes the collector's actual INSERT against the Django-migrated schema.
Keep the private cloud schema-generation source outside Git.

Reproducible commands, using the project's backend environment and Node 22.18::

    cd futureagi
    python -m pytest tracer/tests/test_eval_sampling_policy.py \
      tracer/tests/test_eval_sampling_engine.py \
      tracer/tests/test_eval_sampling_clickhouse.py
    python manage.py makemigrations --check --dry-run
    cd ..
    python scripts/verify-eval-sampling-native.py \
      --clickhouse /path/to/verified/clickhouse \
      --backend-python /path/to/backend/python
    scripts/generate-openapi-schema.sh
    cd frontend
    npm run contracts:generate
    npm run contracts:check
    npx vitest run src/sections/tasks/components/SamplingPolicyEditor.test.jsx \
      src/sections/tasks/__tests__/TaskCreatePage.mixed-filters.test.jsx \
      src/sections/common/EvalsTasks/__tests__/getDefaultTaskValues.test.js

The native script executes the production query compiler on real ClickHouse
25.8+ with bounded synthetic fixtures (at most 220 spans per case), including a
5-by-4 cell population, overlap, rare-case census, independently ranked members,
remapped session/user IDs, missing IDs and span/trace/session row types. It is not
an ingestion/load benchmark. The database engine tests include a 5,000-row
streaming and statement-count assertion, partial-stream rollback, late members,
first-admission stability, tenant rejection and create/edit/result round-trips.
An isolated SQLite smoke harness is useful for logic checks but does not qualify
PostgreSQL migrations, row locks, constraints or concurrency.

The approved browser flow is EVAL-E2E-020 in the generated E2E catalog. Release
gates remain the full PostgreSQL + CH25 integration suite, managed execution of
that create/edit/preview/late-selection flow and a one-sided fallibility run
per backend assertion. Catalog/typecheck and mocked HTTP component tests are not
substitutes for those gates. Automatic recurring windows, unbounded contact
history outside the explicit event interval, voice policy support and measured
provider-price estimation are deliberately not claimed here.

Contact-consistent evaluation sampling
=====================================

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
  count and parameter bytes, and pushes each batch's unit IDs (with their
  remap aliases) into every physical ``spans`` read, so a batch replays only
  the traces of its own members instead of rescanning the whole window.
  ClickHouse inlines a CTE at every reference, so the canonical session/user
  remap maps take the ids they expand from an id-only physical scan of the
  same scope rather than from the replayed latest state: a
  statement replays the latest state once per ``sampling_source`` reference
  (eligibility and projection, plus one for the legacy ``observation_type``
  filter), never once more per remap map.
  Evaluator fanout is also capped per insert batch. Membership lookups/inserts
  and evaluation entries are batched; there is no PostgreSQL query for each
  span.

Precise population contract
---------------------------

Closed ``window`` is an explicit, half-open **event-time eligibility** interval
``[start, end)``, at most 31 days. Live percentage policies instead require
``end: null`` and a continuous task: new contacts remain eligible indefinitely
from ``start`` onward, including after the preview and across future event days.
Task filters further restrict evaluation rows. The policy does not silently
read activity before its start or outside a closed interval.

The policy window is the **only** event-time range of a policy task. The
historical task form's ``date_range``/``date_preset`` are not sent with a
policy, and the API removes them explicitly at create, edit (including older
edit drawers) and preview, so they never intersect the policy window and an
unchanged population is not reported as edited. Explicit ``start_time`` /
``created_at`` time filters (and the legacy ``created_at`` key) are rejected
for policy tasks rather than intersected. A stored policy task that still
carries a task date range fails closed until it is saved again. Preview and
execution therefore read the same interval. Trace evaluations use root-span attributes; session
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
Late rows of already selected units keep the original stratum and k/N. A cohort
is certified (``complete``) only after its whole membership stream has been
written; a failed or interrupted stream deletes the uncertified cohort, and a
later attempt that finds an uncertified cohort whose lease has lapsed discards
it before freezing again. No partially materialized first page can masquerade
as an exact quota.

``frozen_at`` carries a configurable **settle margin** ``S``
(``EVAL_SAMPLING_CLOSED_SETTLE_SECONDS``, default 15, 0-3600): the freeze
counts only rows whose physical arrival is before ``clock - S`` and waits
(outside any transaction, at most ``2S``) until that cutoff is at least ``S``
after the window end. In-window rows that arrive within ``S`` of the window's
end are therefore counted, and a replica lagging by less than ``S`` reads the
same population. A certified cohort never waits again. Previews do not apply
the margin; they count what is visible now and are not reservations.

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
A first-admission proof classifies each page of new units across their whole
eligible history, but every physical seed scan is bounded to the eligibility
hours ``[start, snapshot]`` (immutable key coordinates, so partitions outside
are never read) and to arrivals before the snapshot. Two configured ceilings
bound it further: ``EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS`` (default 366) for the
policy start's distance from the snapshot and
``EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS`` (default and maximum 20 million rows
read). Exceeding either fails explicitly with a non-retryable error; that
page's decisions are not written, earlier pages' complete decisions stay, and
the cursor does not advance. Start a new live policy with a later start.
Live row filters currently support span data; evaluation/annotation and mutable
profile-dependent filters require a closed policy rather than silently missing
non-span change triggers.

There are no automatically recurring exact quotas in this revision: create an
explicit new closed window when another cohort is needed. This restriction does
not stop ongoing percentage sampling, whose policy has no end date. A preview is not a reservation;
the population can change before the first successful execution. No dollar price
is fabricated: the preview returns ``estimated_cost: null`` and explains that
actual evaluator token usage/provider pricing are not fixed by a sampling policy.

Coordinated (permanent random number) sampling
----------------------------------------------

``coordination`` chooses the string every unit hash is seeded with:

* ``independent`` (the default, stored without the key): the seed is the
  policy version, exactly as before. Each policy draws its own sample, so an
  edited rate or quota re-draws every unit. Existing tasks and policy versions
  are unchanged: the policy test pins two pre-coordination versions byte for
  byte, and an explicit ``"independent"`` normalizes to the same stored form.
* ``project``: the seed is ``prn-sha256-v1:<project id>:<panel>``. It omits
  rates, quotas, strata, filters and the window, so every unit keeps one
  permanent random number (PRN) across every coordinated policy of the
  project (and the optional ``panel``). Each edit is still its own cohort and
  policy version (lineage is unchanged); only the random numbers are shared.

On the hash circle read from ``prn_start`` (percent, default 0), the closed
sample is the ``k`` smallest ``(hash - start) mod 2**256`` per stratum and the
live sample is every unit whose key is below the rate threshold. Therefore,
for a fixed seed and start:

* raising a rate or a quota keeps every unit already selected (nested), within
  the same stratum for closed quotas;
* two tasks (or evaluators) with the same unit and rate select the same units;
* live intervals ``[start, start + rate)`` that do not overlap give disjoint
  samples, and adjacent ones union to the wider interval (rotation);
* decisions are per unit, so input order and pagination never change them.

Inclusion probabilities stay ``k/N`` (closed) and the rate (live). They are
marginal: results of coordinated policies share random numbers, so their
inclusion events are dependent across tasks. Each such result carries
``coordination: "project"`` and its cohort's ``selection_context`` records the
seed, panel and start. Combining coordinated tasks in one estimator needs their
joint design (for nested samples the smaller sample is a subset of the larger).

Evidence: ``test_prn_nesting_overlap_and_rotation`` (10,000 ids: nested rates
and quotas, 100% cross-task agreement against about 1% for independent
policies, disjoint rotations, order/pagination stability),
``test_prn_hash_uniformity_chi_square_100k`` (100,000 ids in 100 bins at two
start points, chi-square p > 0.001) and the real-ClickHouse
``test_coordinated_prn_sql_matches_reference_nests_and_persists`` (closed
bottom-k and two-rule live Bernoulli, including an interval that wraps past the
top of the circle, equal the Python reference unit for unit; a deliberately
broken wrap branch makes it fail). Re-using completed results after a quota
increase (evaluating only the new units) is not implemented: an edited
population still needs Delete & rerun.

Sampling audit
--------------

``GET /tracer/eval-task/sampling_audit/?eval_task_id=<id>[&cohort_id=<id>]``
audits the cohort of the task's current policy (or the named cohort, for
example an archived one). The task page shows it in a read-only "Sampling
audit" tab, present only for policy tasks.

* Closed cohorts: the realized sample is the persisted membership. The
  population of every stratum is re-read once at the cohort's ``frozen_at``
  with the preview statement (one bounded ClickHouse read under the sampling
  read budget and the per-actor preview admission), so strata whose quota is
  zero are reported too. A stratum whose re-read population or quota no longer
  equals the persisted ``population_size`` and member count is marked
  ``changed`` (deleted rows or identity merges since the freeze); the persisted
  ``N`` is kept because the stored weights used it. An uncertified cohort
  (still freezing) is refused with 409.
* Live cohorts: every first decision is persisted (selected and rejected), so
  the audit reads PostgreSQL aggregates only. Each stratum gets an exact
  two-sided binomial sample-ratio-mismatch (SRM) test of selected against
  decided units at its rate, flagged at p < 0.0005.

Per stratum it reports population ``N``, target (the rule's quota before the
population cap), expected size (``k = min(N, target)``, or ``N x rate``),
achieved ``n``, shortage, inclusion probability, design weight ``1/p`` and the
exactness/SRM check. The summary reports the Kish effective sample size
``(sum w)^2 / sum w^2``, the design effect ``n / n_eff = 1 + CV^2``, the weight
CV, the weighted population (for closed cohorts it equals the represented
population exactly: ``sum w = sum N`` over strata with a sampled unit) and the
unrepresented strata. The balance check covers each declared stratum
attribute (the slice and every group-by key): population, sample and weighted
shares per category, standardized differences and a Pearson chi-square
goodness-of-fit of the sampled counts to the population shares. Unweighted
differences are expected under disproportionate quotas; a weighted imbalance
(|standardized difference| > 0.1) means part of the population is
unrepresented.

Evidence: ``test_eval_sampling_audit.py`` checks the report against
hand-computed values (for example ``n_eff = 1536/277`` and ``deff = 831/512``
for weights 2.5 x 2, 10 x 4 and 1 x 3; chi-square ``271/15`` on 3 df) and the
endpoint statuses (404 unknown task, other workspace or no cohort, 400 no
policy, 409 uncertified cohort). The real-ClickHouse
``test_closed_audit_endpoint_reports_frozen_design_weights_and_balance``
freezes a five-cell design, checks every per-cell number, ``n_eff =
13824/1693``, the weighted population of 48, the locale balance and the
unsampled stratum, then deletes one selected-cell contact after the freeze and
sees that stratum flagged ``changed``. ``test_srm_false_alarm_and_power``
measured, with seeded simulation, 7 false alarms in 10,000 correct runs
(2,000 units at 10%) and 1,000 of 1,000 detections of a 5% relative
under-selection in a 200,000-unit stratum at 10%.

Persistence and downstream metrics
----------------------------------

``EvalSamplingCohort`` is unique by task and policy digest and stores the policy,
freeze timestamp and immutable selection context (organization, workspace,
project, evaluation row type, task filters and algorithm). ``EvalSamplingMember``
is unique by cohort and SHA-256 of the canonical/namespaced unit ID. Database
checks require probabilities in [0,1] and strictly positive probabilities for
selected members.

Reconcile concurrency: no PostgreSQL transaction, and no task or evaluator row
lock, spans a ClickHouse stream. A pass takes a cohort **lease** (token plus
expiry, ``EVAL_SAMPLING_LEASE_SECONDS``, default 300) in a short transaction;
a concurrent pass for the same cohort fails with a retryable "busy" error and
writes nothing. Each bounded page (member block, live decision page, row
block, pending-proof page, final cursor update) is then written in its own
short transaction that locks the task and evaluator rows, proves the task
revision the pass started from and renews the lease only while it still holds
the token. An edited task or a lost lease stops the pass before its next write;
an expired holder is fenced out. Pending-proof pages keep their row locks for
one bounded point read, so the drain can never claim a row whose negative proof
is in flight. Idempotent materialization and the forward-only cursor (updated
last) make a retried pass safe.

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

Identity merges: a decision is keyed by the canonical unit ID at decision
time and is never rewritten. If a later remap makes a different alias the
canonical ID of the same contact, rows resolve to that new canonical ID, which
is treated as a unit first seen after the decision: a closed cohort excludes it
(pending entries of the moved rows are dropped, completed results keep their
provenance) and a live policy records a new first-admission decision for it.
Use an explicit fresh run after large identity merges if a single cohort must
span them.

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
queryset as task creation. Its statuses are precise: invalid policies,
unsupported task filters, user-relative filter principals, explicit time
filters, oversized identities, an over-budget compiled statement and read
ceilings return 400 with the cause;
time/memory pressure, a refused/reset/timed-out/dropped connection or a ClickHouse
connection/concurrency exhaustion returns a sanitized 503; anything else,
including any other ClickHouse server error, returns a sanitized 500. Each actor (organization and user) may
have one preview in flight and 12 per minute (``EVAL_SAMPLING_PREVIEW_CONCURRENCY``
and ``EVAL_SAMPLING_PREVIEW_PER_MINUTE``); excess requests get 429 with
``Retry-After``, and the guard fails closed (503) if its cache is unavailable. No policy accepts another tenant's policy/project/
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
ceilings, with overflow set to throw; per-statement settings may only tighten
them. The preview rejects more than 1,000 cells. Unit IDs (2,048 bytes) and
group-by values (1,024 bytes) are bounded without lossy truncation, hashing or
silent exclusion: an oversized value fails the read with an explicit error that
names the contact attribute or group-by attribute (never the value). Policies are limited to 64 KiB and compiled statements to 2 MiB. Locked
read-only server profiles must independently provide reviewed resource limits
when they disallow per-query settings; the native wall timeout still applies.
These are safety limits, not a claim that every permitted 31-day workload fits them. A failed
proof does not advance the continuous cursor. Actual one-billion-row throughput
has not been established by the correctness fixtures.

Public competitive context
--------------------------

Documentation checked 2026-10-21:

* Galileo documents top-down metadata rules: the first matching rule supplies a
  percentage and unmatched traces use a default rate. It also says traces are
  evaluated for all configured metrics or none. This is a useful ordered-rule
  comparison, not evidence of exact, frozen per-cell target-n quotas.
  https://docs.galileo.ai/concepts/logging/configure-metrics/configure-metrics
* Galileo sessions group traces, events and spans. Grouping/session-level
  evaluation alone is not the same contract as evaluating every eligible span
  of a sampled contact while keeping span-level results.
  https://docs.galileo.ai/concepts/logging/sessions/sessions-overview
* LangSmith documents thread filters, percentage sampling and project-level idle
  time for multi-turn evaluations; its documentation lists a 500-thread batch
  limit and seven-day run eligibility. Those are thread evaluation semantics,
  not evidence for this span-result/contact-membership contract.
  https://docs.langchain.com/langsmith/online-evaluations-multi-turn

* Langfuse's 2026-08-05 changelog documents consistent evaluator sampling of
  matching observations. Its separate SDK ingestion sampler retains/drops
  whole traces and associated scores. Neither statement establishes
  cross-trace contact sampling or exact stratified quotas.
  https://langfuse.com/changelog/2026-08-05-deterministic-evaluator-sampling
  https://langfuse.com/docs/observability/features/sampling

Additional documentation checked 2026-10-22:

* Arize AX applies a trace/session task's sampling rate per unit (10% of
  sessions, not 10% of spans) and gathers a unit's spans from 24 hours before
  the task window; its session evaluator judges the unit as a whole. That is
  unit-level sampling for unit-level judgements, not frozen per-cell quotas
  with stored inclusion probabilities on every span result.
  https://www.arize.com/docs/ax/evaluate/trace-and-session-evals
* Braintrust online scoring samples matching traces after they go idle; for
  grouped scoring the rate decides whether a group is scored and, per its
  docs, does not decide which traces of the group are included.
  https://www.braintrust.dev/docs/evaluate/score-online
* Datadog LLM Observability offers span, trace and session scopes with an
  optional sampling rate; a session evaluation runs after 30 idle minutes and
  spans arriving more than 30 minutes after the previous span are not
  included. This implementation instead admits late eligible rows of an
  already selected unit on later polls (tested in the real-ClickHouse suite).
  https://docs.datadoghq.com/llm_observability/investigate/evaluations/llm_as_a_judge_evaluations/session_level_evaluations
* Langfuse evaluation rules show the past seven days' matching volume and an
  estimated cost before enabling a rule. This preview reports exact per-cell
  eligible/selected units and rows and expected evaluation runs, but
  deliberately no dollar estimate.
  https://langfuse.com/docs/evaluation/get-started/online

These sources support the distinctions above. They do not establish that no
other product implements stratification or contact-consistent sampling, and
no throughput or cost comparison with them has been measured.

Verification and rollout
------------------------

Apply ``tracer/0117_cohort_sampling``,
``tracer/0117_sampling_project_db_default`` and the nullable lease columns of
``tracer/0120_sampling_cohort_lease`` before deploying writers. They are
additive, contain no backfill of guessed probabilities, and have no enterprise
dependency. Pause/drain ingestion while applying the two 0117 migrations, then resume
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

The real-ClickHouse suite also proves, through ``system.query_log``, that
adding 24 non-member contacts (one part each) grows the full-window read by at
least their 96 spans while a closed member page returns identical rows and its
own read grows by less than an eighth of that, and that a live proof never
reads a 3,000-span history 40 days before the policy start, and that a preview
reads less than three latest-state replays of a 12-span, 8 KiB-per-span
attribute payload (measured 204,370 uncompressed bytes against 98,304 per
replay; the previous remap form read 606,950). It covers min/max/
census/shortage quotas compiled to SQL, remapped sessions expanded for trace and
session rows, the settle margin, explicit oversized-identity errors, explicit
proof bounds and identity merges after a decision. These are correctness and
read-scope fixtures, not throughput benchmarks.

An interleaved local A/B of the previous and current remap forms on one
synthetic 10,000-span set (2,500 sessions, twenty cells, 1 KiB attribute
payload, five runs of each form on a heavily loaded shared host) returned
identical rows. The preview's median read fell from 75,961,784 to 26,961,640
uncompressed bytes and its median server time from 4.8 s to 1.7 s; with the
legacy ``observation_type`` filter, from 113,942,676 to 40,442,460 bytes and
from 5.5 s to 3.2 s. Read bytes are deterministic; timings on that host are
noisy and are not a production benchmark.

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

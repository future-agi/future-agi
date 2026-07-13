"""Named budget constants for the eval-read stress suite.

Every budget assertion in tests/stress references a constant here — never an
inline number. Red-baseline budgets (xfail until their fix lands) encode the
post-fix target; green budgets pin current behavior against regression.
Baselines below were measured 2026-07-07 against current code at
STRESS_SCALE=0.1 (target 8,000 spans / noise 58,777 / ~69k total in CH).
"""

# ── S4 / A1-A2: root lookup (`list_root_spans_by_trace_ids`) ────────────────
# read_rows ceiling as a factor of the *target project's* span count: a
# project-scoped lookup reads ~the target rows; an unscoped one also scans the
# noise project and fails loudly (scale-invariance trick, design §3.1).
# Measured baseline: 1,000-trace lookup reads 100,292 rows (the whole spans
# table) at 69 MB — budget 8,000 × 1.5 = 12,000 rows → red on read_rows.
# Granule floor — CH reads whole 8 192-row granules, so a scoped read of an
# N-row project can round up to ~2 granules per part; 2.5× stays far below
# the unscoped full-table read.
ROOT_LOOKUP_MAX_READ_ROWS_FACTOR = 2.5
ROOT_LOOKUP_MAX_MEMORY = 500 * 2**20

# ── S12 / B1-B3: 500-entry drain batch ──────────────────────────────────────
# Measured baseline: 500 run_entry calls issue 1,000 CH queries (2/entry),
# 100,292 read_rows EACH (full-table scan per span lookup), peak 719 MB.
# CH queries per drained batch (B1 prefetch: O(1) regardless of batch size).
DRAIN_BATCH_MAX_CH_QUERIES = 6
# PG queries per entry (B3: claim/terminal writes only; config loads batched).
DRAIN_PER_ENTRY_MAX_PG_QUERIES = 3

# ── S6 / A4: worker memory while loading one fat trace ──────────────────────
# Design initial was 64 MiB, calibrated down: current (unfixed) load of one
# voice trace (8 spans, one 1.2 MiB transcript in attributes_extra) peaks at
# 2.93 MiB measured — a 64 MiB ceiling could never go red at this trace
# fatness. 1 MiB sits below a single transcript, so the ceiling is
# payload-independent: red today, green once A4 loads lean.
TRACE_LOAD_MAX_PY_PEAK = 2**20

# ── S10 / A7: reconciler requeue UPDATE fan-out ─────────────────────────────
# One requeue UPDATE + one drop UPDATE, independent of config count.
# Baseline: one UPDATE per stale config group (3 with a 3-eval task) → red.
RECONCILE_REQUEUE_MAX_PG_UPDATES = 2

# ── S7 / A5: session resolve (`resolve_session_fields`) remap scoping ───────
# At CI scale `trace_sessions` holds only ~60 rows (all in ONE 8192-row
# granule), so read_rows cannot discriminate scoped vs. unscoped — both read
# the same single granule.  S7 therefore verifies A5 STRUCTURALLY via
# `EXPLAIN indexes=1`: the planner must show `project_id` in the PrimaryKey
# condition (not `Condition: true`) when the WHERE pins `project_id`.  This
# is scale-independent (the planner recognises the sort-key prefix regardless
# of how many granules are actually skipped at runtime).  No read_rows budget
# constant needed; the EXPLAIN assertion lives in test_session_lookup.py.

# ── S13 (green): desired-row stream over the mixed/fat-attrs project ────────
# Pins the already-project-scoped `iter_desired_rows` scan: reads ≈ the
# project's own rows, and the id sort stays well under the server limit.
# Measured: 67,935 read_rows for 58,777 desired ids (1.16×), peak 8.4 MB.
DESIRED_STREAM_MAX_READ_ROWS_FACTOR = 1.5
DESIRED_STREAM_MAX_MEMORY = 512 * 2**20

# ── S15 (green): reap + progress + finalize PG statement floor ──────────────
# reap = 2 UPDATEs, has_undrained_work = 1 EXISTS, finalize = fetch + EXISTS
# + UPDATE (+ savepoint bookkeeping under the test transaction).
REAP_PROGRESS_FINALIZE_MAX_PG_QUERIES = 8

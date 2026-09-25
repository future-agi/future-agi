"""Single source of truth for bounded, operator-tunable numeric settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

Numeric = int | float
NumericType = type[int] | type[float]
SpecRow = tuple[str, Numeric, Numeric, Numeric]

_MISSING = object()


@dataclass(frozen=True, slots=True)
class NumericSettingSpec:
    """A numeric setting's default, parser, and reviewed safety bounds."""

    value_type: NumericType
    default: Numeric
    minimum: Numeric
    maximum: Numeric

    def parse(self, name: str, raw_value: object = _MISSING) -> Numeric:
        value_source = self.default if _is_missing(raw_value) else raw_value
        if isinstance(value_source, bool):
            raise ValueError(f"{name} must be numeric, not boolean")
        if self.value_type is int and isinstance(value_source, float):
            raise ValueError(f"{name} must be an integer")
        try:
            value = self.value_type(value_source)
        except (TypeError, ValueError) as exc:
            expected = "an integer" if self.value_type is int else "numeric"
            raise ValueError(f"{name} must be {expected}") from exc
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{name} must be between {self.minimum} and {self.maximum}"
            )
        return value


def _is_missing(value: object) -> bool:
    return (
        value is _MISSING
        or value is None
        or (isinstance(value, str) and not value.strip())
    )


def _specs(
    rows: tuple[SpecRow, ...],
    *,
    value_type: NumericType = int,
    prefix: str = "",
) -> dict[str, NumericSettingSpec]:
    return {
        f"{prefix}{name}": NumericSettingSpec(
            value_type=value_type,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )
        for name, default, minimum, maximum in rows
    }


PROPERTY_CATALOG_RUNTIME_SETTING_SPECS = {
    **_specs(
        (
            ("MAX_PAGE_SIZE", 50, 1, 200),
            ("MAX_SEARCH_BYTES", 512, 1, 4096),
            ("QUERY_WALL_MS", 10_000, 100, 30_000),
            ("READ_POOL_SIZE", 4, 1, 32),
            ("READ_MAX_THREADS", 2, 1, 16),
            ("READ_MAX_CONCURRENT_QUERIES_PER_USER", 4, 1, 16),
            ("READ_MAX_BYTES", 512 * 1024**2, 1024**2, 1024**4),
            ("READ_MAX_MEMORY_BYTES", 512 * 1024**2, 1024**2, 16 * 1024**3),
            ("READ_MAX_RESULT_BYTES", 8 * 1024**2, 64 * 1024, 256 * 1024**2),
            (
                "READ_EXTERNAL_GROUP_BY_BYTES",
                128 * 1024**2,
                32 * 1024**2,
                8 * 1024**3,
            ),
            (
                "READ_EXTERNAL_SORT_BYTES",
                128 * 1024**2,
                32 * 1024**2,
                8 * 1024**3,
            ),
            ("CURSOR_MAX_AGE_SECONDS", 24 * 60 * 60, 60, 7 * 24 * 60 * 60),
            # A keyset contains canonical value bytes; do not depend on JSON
            # compression to fit an eligible 16 KiB string after escaping.
            ("CURSOR_MAX_BYTES", 256 * 1024, 1024, 256 * 1024),
        ),
        prefix="PROPERTY_CATALOG_",
    ),
    **_specs(
        (("READ_TRANSPORT_TIMEOUT_SECONDS", 10.0, 0.1, 30.0),),
        value_type=float,
        prefix="PROPERTY_CATALOG_",
    ),
}

DATASET_READ_SETTING_SPECS = {
    **_specs(
        (
            ("TABLE_CURSOR_MAX_AGE_SECONDS", 30 * 60, 60, 86_400),
            ("TABLE_EXACT_MAX_COLUMNS", 128, 1, 1_024),
            ("TABLE_EXACT_MAX_CELLS", 12_800, 1, 1_000_000),
            ("TABLE_EXACT_MAX_CELL_VALUE_BYTES", 256 * 1024, 1_024, 16 * 1024**2),
            (
                "TABLE_EXACT_MAX_CELL_VARIABLE_BYTES",
                6 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("TABLE_EXACT_MAX_SCHEMA_BYTES", 1024**2, 64 * 1024, 16 * 1024**2),
            ("TABLE_EXACT_MAX_SERIALIZED_BYTES", 8 * 1024**2, 64 * 1024, 64 * 1024**2),
            ("INTERACTIVE_MAX_PAGE_SIZE", 100, 1, 500),
            ("INTERACTIVE_MAX_OFFSET_ROWS", 100_000, 1_000, 1_000_000),
            ("ROW_ADJACENCY_MAX_ROWS", 50, 1, 500),
        ),
        prefix="DATASET_",
    ),
    **_specs(
        (("TABLE_SERVER_WALL_SECONDS", 8.5, 0.5, 30.0),),
        value_type=float,
        prefix="DATASET_",
    ),
}

# Out-of-band recovery for eval tasks whose per-task workflow stopped.
#
# These two constants mirror ``_RUN_ENTRY_TIMEOUT`` and
# ``RUN_ENTRY_RETRY_POLICY.maximum_attempts`` in
# ``tfc.temporal.eval_tasks.workflows``, which cannot be imported at
# settings-load time. ``test_the_mirrored_run_entry_ceiling_matches_the_workflow``
# pins them against the real values, so a change there fails a test here
# rather than silently loosening the bounds below.
RUN_ENTRY_CEILING_SECONDS = 1_800
RUN_ENTRY_MAX_ATTEMPTS = 3
# The longest a run the sweep can still meet may legitimately last. The sweep
# asks Temporal before it reaps and skips a task whose workflow is progressing,
# so the only run it can overlap belongs to an execution that has since closed:
# one activity attempt already in flight on a worker, bounded by the run-entry
# start-to-close ceiling, with no retries because a closed execution dispatches
# none. The retry count is kept in the product as headroom rather than as the
# bound it models, so the floor stays conservative if that gate ever moves.
#
# This deliberately does NOT model a claim waiting in the queue. An entry is
# ``RUNNING`` from the moment ``claim_pending_batch`` stamps its batch, and the
# drain runs ``max_concurrent`` of a ``batch_size`` batch at a time, so a batch
# tail can hold a frozen claim stamp for several waves — a span no threshold in
# this range would cover. The describe-first gate is what makes that safe: a
# task with a queued tail has a progressing workflow, so the sweep never reaps
# it. See ``tracer.services.eval_tasks.recovery.recover_task``.
LONGEST_RUNNING_ENTRY_SECONDS = RUN_ENTRY_CEILING_SECONDS * RUN_ENTRY_MAX_ATTEMPTS

# ``SWEEP_STALE_RUNNING_SECONDS`` is the threshold of the sweep's *own* reap,
# and stays above that bound at every value an operator can configure, so that
# reap never requeues an entry whose run is still in flight from a closed
# execution. It does not bound the recovery as a whole. The workflow the sweep
# then restarts reaps first at ``ReapInput``'s 600 s, on the evidence of the
# same describe, which the sweep hands to the starter rather than letting it
# describe again (``RESTART_REAP_SECONDS`` in
# ``tracer.services.eval_tasks.recovery``), so a claim older than ten minutes
# is reclaimed as soon as that run starts, whatever this is set to — and a run
# of the closed execution can still be in flight under it. What keeps the row
# correct there is the claim-epoch fence, which refuses that run's write; the
# cost is one evaluation paid for twice and one of the entry's three reclaims.
# Raising this setting does not prevent that. It only moves which reap
# reclaims a row, and so what the tick's ``entries_requeued`` counts.
EVAL_EXECUTION_SETTING_SPECS = {
    **_specs(
        (
            (
                "SWEEP_STALE_RUNNING_SECONDS",
                7_200,
                LONGEST_RUNNING_ENTRY_SECONDS + 1,
                86_400,
            ),
            # 0 is the off switch. A Temporal pause is the immediate lever,
            # but ``register_temporal_schedules`` runs on every backend
            # container start and re-registers the schedule with
            # ``ScheduleState`` rebuilt from config, so a manual pause does not
            # survive the next deploy, restart or scale-up. A setting does.
            ("SWEEP_MAX_TASKS", 25, 0, 500),
        ),
        prefix="EVAL_TASK_",
    ),
}

INTERACTIVE_READ_SETTING_SPECS = {
    **_specs(
        (
            ("INTERACTIVE_READ_DEFAULT_WALL_MS", 30_000, 100, 60_000),
            ("INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS", 30_000, 100, 60_000),
            # Per-route acquisition wall for a cursor-capable list page: the
            # walk that decides which rows are on the page stops here and
            # publishes the rows found so far plus a resumable cursor.
            # Numbered pages, hydration, navigation and pickers keep the
            # interactive analytics wall above.
            ("SPAN_LIST_PAGE_WALL_MS", 5_000, 100, 60_000),
            ("TRACE_LIST_PAGE_WALL_MS", 5_000, 100, 60_000),
            ("SESSION_LIST_PAGE_WALL_MS", 5_000, 100, 60_000),
            ("INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE", 100, 1, 500),
            ("ANALYTICS_DEFAULT_LOOKBACK_DAYS", 30, 1, 3_660),
            ("PG_CONNECT_TIMEOUT_SECONDS", 1, 1, 5),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_BYTES",
                1024**4,
                64 * 1024**2,
                2 * 1024**4,
            ),
            ("CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS", 4, 1, 16),
            ("CLICKHOUSE_APPLICATION_READ_MAX_THREADS", 8, 1, 32),
            ("CLICKHOUSE_APPLICATION_READ_MAX_RESULT_ROWS", 1_000_000, 1, 10_000_000),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_RESULT_BYTES",
                512 * 1024**2,
                64 * 1024,
                2 * 1024**3,
            ),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS", 25, 0, 10_000),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS", 75, 0, 10_000),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_THIRD_MS", 150, 0, 10_000),
            ("EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION", 31, 1, 366),
            ("EXACT_GRAPH_MIN_REMAINING_MS", 25, 1, 1_000),
            ("EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE", 1_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS", 1_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS", 2, 1, 168),
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS", 1, 1, 168),
            ("EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 2, 1, 16),
            ("EXACT_GRAPH_TRACE_ANCHOR_PAGE_SIZE", 50_000, 1, 1_000_000),
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_DAYS", 30, 1, 3_660),
            ("EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE", 512, 1, 100_000),
            ("EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS", 5 * 60, 1, 86_400),
            ("EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS", 30, 1, 86_400),
            (
                "EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS",
                2 * 24 * 60 * 60,
                1,
                31 * 24 * 60 * 60,
            ),
            ("EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS", 2_000, 1, 60_000),
            ("EXACT_GRAPH_SPAN_MAX_PARTITION_HOURS", 24, 1, 31 * 24),
            ("EXACT_GRAPH_SPAN_INITIAL_PARTITION_HOURS", 1, 1, 31 * 24),
            ("EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS", 250, 1, 60_000),
            ("EXACT_GRAPH_READ_BLOCK_SIZE", 512, 1, 65_536),
            (
                "EXACT_GRAPH_READ_PREFERRED_BLOCK_BYTES",
                4 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            (
                "EXACT_GRAPH_READ_EXTERNAL_SPILL_BYTES",
                32 * 1024**2,
                64 * 1024,
                4 * 1024**3,
            ),
            ("EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS", 8, 1, 32),
            (
                "INTERACTIVE_READ_DEFAULT_MAX_RESPONSE_UNITS",
                2 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("DASHBOARD_FILTER_VALUE_WALL_MS", 30_000, 100, 60_000),
            ("DASHBOARD_TRACE_READ_MAX_THREADS", 4, 1, 16),
            ("DASHBOARD_TRACE_REPLICA_SHARD_COUNT", 3, 1, 16),
            ("DASHBOARD_WEEKLY_AGGREGATION_AFTER_DAYS", 90, 1, 3_660),
            (
                "DASHBOARD_TRACE_READ_MAX_BYTES",
                1024**4,
                64 * 1024**2,
                2 * 1024**4,
            ),
            (
                "DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            ("DASHBOARD_TRACE_READ_MAX_RESULT_ROWS", 250_000, 1, 1_000_000),
            (
                "DASHBOARD_TRACE_READ_MAX_RESULT_BYTES",
                64 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            ("DASHBOARD_TRACE_MAX_CONCURRENT_METRICS", 2, 1, 8),
            ("DASHBOARD_BREAKDOWN_MAX_SERIES", 100, 1, 10_000),
            ("DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE", 50, 1, 200),
            ("DASHBOARD_FILTER_VALUE_FINITE_MAX", 5_000, 1, 50_000),
            ("DASHBOARD_FILTER_VALUE_LEGACY_MAX", 500, 1, 5_000),
            (
                "DASHBOARD_FILTER_VALUE_MAX_RESULT_BYTES",
                64 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            (
                "ATTRIBUTE_READ_MAX_RESULT_BYTES",
                64 * 1024**2,
                16 * 1024**2,
                512 * 1024**2,
            ),
            ("DASHBOARD_ROLLUP_MAX_QUERIES", 2, 1, 16),
            ("DASHBOARD_ROLLUP_MAX_POINTS", 10_000, 100, 100_000),
            (
                "DASHBOARD_ROLLUP_MAX_RESULT_BYTES",
                32 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            ("DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE", 20, 1, 200),
            ("DASHBOARD_FILTER_VALUE_COMPAT_LOOKBACK_DAYS", 365, 1, 3_660),
            ("DASHBOARD_METRICS_ATTRIBUTE_KEY_LIMIT", 2_000, 1, 100_000),
            ("DASHBOARD_METRICS_ATTRIBUTE_WORKERS", 1, 1, 8),
            ("DASHBOARD_METRICS_CATALOG_DEFAULT_PAGE_SIZE", 50, 1, 500),
            ("DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE", 200, 1, 500),
            ("DASHBOARD_METRICS_CATALOG_SEARCH_MAX_CHARS", 256, 1, 4_096),
            ("DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS", 5_000, 100, 60_000),
            ("DASHBOARD_METRICS_EVAL_USAGE_LOOKBACK_DAYS", 90, 1, 3_660),
            ("OBSERVABILITY_LIST_MAX_BLOCK_SIZE", 8192, 1, 65_536),
            ("OBSERVABILITY_LIST_MAX_BYTES", 1024**4, 64 * 1024**2, 2 * 1024**4),
            (
                "OBSERVABILITY_LIST_CELL_PREVIEW_MAX_BYTES",
                16 * 1024,
                1_024,
                1024**2,
            ),
            (
                "OBSERVABILITY_LIST_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            ("OBSERVABILITY_LIST_MAX_RESULT_ROWS", 5_001, 1, 100_000),
            ("CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS", 180_000, 100, 180_000),
            ("MONITOR_GRAPH_CH_TIMEOUT_CAP_MS", 6_000, 100, 60_000),
            ("MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS", 1_000, 100, 10_000),
            ("GRAPH_BACKGROUND_WALL_MS", 180_000, 1_000, 180_000),
            ("GRAPH_EVENT_LIMIT", 2_000, 1, 100_000),
            ("GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT", 40, 1, 4_096),
            ("GRAPH_SPAN_METRIC_BATCH_SIZE", 1_024, 1, 4_096),
            ("FILTER_VALUE_READ_TIMEOUT_MS", 30_000, 100, 60_000),
            ("FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS", 5, 1, 300),
            # A five-minute system-value continuation read crossed 1 GiB and
            # the four-second picker wall on a production-scale project. Start
            # at the existing exact five-second floor and grow only after a
            # complete empty/duplicate-only slice proves it is safe.
            ("FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS", 5, 1, 86_400),
            (
                "FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS",
                60 * 24 * 60 * 60,
                60,
                366 * 24 * 60 * 60,
            ),
            ("FILTER_VALUE_CURSOR_MAX_QUERIES", 6, 1, 128),
            ("FILTER_VALUE_CURSOR_SCAN_LIMIT", 201, 2, 10_001),
            # A span-attribute-filtered Users page walks witnessed spans
            # newest-first in time slices, certifies each slice's users and
            # stops on its own wall or statement budget with a cursor. The
            # unfiltered Users page does not read these.
            ("USER_LIST_PAGE_WALL_MS", 5_000, 100, 60_000),
            ("USER_LIST_WALK_MAX_STATEMENTS", 24, 1, 256),
            # A walk that has published nothing is not ended by its statement
            # count while its page wall lasts: the count grows one budget at a
            # time, up to this many budgets. A dense witness whose users are
            # all rejected spends 24 fast statements in a fraction of the
            # wall; ending there returned empty pages for request after
            # request. 1 keeps the plain budget.
            ("USER_LIST_WALK_EMPTY_PAGE_BUDGETS", 4, 1, 16),
            ("USER_LIST_WALK_INITIAL_SLICE_SECONDS", 60 * 60, 1, 7 * 24 * 60 * 60),
            # A slice asks the server to stop it at half of what is left of
            # the request's analytics wall and is then retried a quarter as
            # wide, so a width too dense for the wall costs retries, not a
            # stall: a one-day slice of a common value on the largest tenant
            # measured about two seconds at eight threads. Wider slices trade
            # that against the statements an empty result needs to prove
            # itself.
            (
                "USER_LIST_WALK_MAX_SLICE_SECONDS",
                24 * 60 * 60,
                60,
                366 * 24 * 60 * 60,
            ),
            ("USER_LIST_WALK_SLICE_USER_LIMIT", 200, 2, 10_001),
            # A slice that fails on a read budget is retried at a quarter of
            # its width down to this floor; below it the failure propagates.
            ("USER_LIST_WALK_MIN_SLICE_SECONDS", 60, 1, 7 * 24 * 60 * 60),
            # Users certified per enrichment statement and replayed per
            # materialisation; the enrichment result is bounded by this times
            # the requested keys.
            ("USER_LIST_WALK_CERTIFY_BATCH_SIZE", 25, 1, 1_000),
            # The rows a walk's tail existence statement may knowingly read.
            # After an empty slice whose tail does not fit the statement
            # budget at the slice cap, the walk asks EXPLAIN ESTIMATE how many
            # rows the blooms leave in the whole tail and issues the one
            # existence statement only when that count fits here; otherwise
            # it keeps slicing at the cap. Rows, not bytes: neither the
            # estimate nor the transport's result carries bytes. Basis: on the
            # largest tenant an uncosted tail statement read 1.38M rows =
            # 4.7 GB in 1.66 s (rig run r2b); a million rows there is about
            # 3.3 GB and 1.2 s at eight threads, a fifth of the page wall, and
            # a slice of a common value at the one-day cap reads 2.4M.
            ("USER_LIST_WALK_PROBE_TARGET_READ_ROWS", 1_000_000, 8_192, 50_000_000),
            # The wall the estimate and the existence statement share, inside
            # the page wall. The estimate is the existence statement's own
            # index analysis under the same read settings; the existence
            # statement repeats it before reading a row, so it is issued only
            # when the estimate's observed time fits what is left here (at
            # most half the wall). Basis: the twelve-month text estimate on
            # the largest tenant at eight threads measured 135-456 ms server
            # (95 parts, 16k marks; 3.2 s at one thread on a cold index), the
            # boolean-key estimate 114-117 ms (395 parts, 33k marks).
            ("USER_LIST_WALK_PROBE_WALL_MS", 1_000, 25, 60_000),
            ("FILTER_VALUE_READ_MAX_THREADS", 2, 1, 16),
            ("FILTER_SELECTOR_QUERY_TIMEOUT_MS", 2_500, 25, 10_000),
            ("FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS", 3_000, 25, 30_000),
            ("FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS", 30_000, 25, 120_000),
            ("FILTER_SELECTOR_MAX_THREADS", 1, 1, 8),
            # Workers for a seed statement over a slice wider than one day, the
            # doubling walk's 32 h and 48 h steps. Measured read-only against
            # production on the span list's 48 h seeds (2-13 parts, 26-48
            # marks per slice): four workers took the heaviest statement from
            # 1.64 s to 0.49 s reading the same 311k rows / 957 MB, and eight
            # gained nothing over four because a slice has only that many
            # independent mark ranges. Rows, bytes and results never depend on
            # this number; peak memory per statement roughly doubles.
            ("FILTER_SELECTOR_WIDE_SEED_MAX_THREADS", 4, 1, 8),
            # Rows one short exact-string seed statement should read. That
            # seed's cost tracks the rows inside its slice, not the slice's
            # width, and its child witness is time-unbounded, so read rows
            # chiefly measure the ROOTS inside the slice through a trace-id
            # bloom false-positive scan (~1 - 0.999 ** roots of a ~107M-row
            # history; 52.4M rows / 4.29 GB measured once, over a dense
            # fifteen-minute window at k=676 roots - a single point, not a
            # measured saturation curve). The selector doubles a slice that
            # reads under a quarter of this budget and halves one that
            # overruns it, but never below the lane's own floor, which is the
            # four-hour fixed ceiling this budget replaced. So in practice the
            # knob decides how far the seed may WIDEN across near-empty
            # history (four hours of sparse history cost 110-220 ms at any
            # width); anywhere results actually live it holds at four hours,
            # i.e. at least what the fixed ceiling gave. That floor is
            # provisional, and bounding the dense statement itself is the
            # pending owner decision on the child-witness contract, not this
            # setting.
            (
                "FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS",
                2_000_000,
                100_000,
                50_000_000,
            ),
            # Hours of slack the same seed's child-witness scan is allowed
            # around the roots one statement can publish. The default is 1 h,
            # the approved bounded-witness contract. ZERO is the legacy
            # any-span escape hatch: that scan then carries no time bound at
            # all - a trace is a candidate when ANY raw span of it carries the
            # value, whenever that span started - and the generated SQL and its
            # parameters are byte-identical to what shipped before this
            # setting, so an operator can restore the old contract without a
            # deploy.
            #
            # Above zero the seed statement additionally requires a witness to
            # start inside the envelope
            #     [hour_floor(slice_start) - slack, hour_ceil(slice_end) + slack)
            # where the roots that statement can publish are the ones inside
            # the slice, tightened on a keyset continuation to the cursor's own
            # position - so the envelope is the tightest one that still carries
            # every publishable root's own witness. Every published row is
            # still an exact any-span match: the latest-state classifier
            # (``build_filter_match_query``) stays UNBOUNDED, so the switch can
            # only OMIT a trace whose sole witness lies outside the envelope
            # and can never admit one the unbounded contract would reject.
            #
            # Why it is a switch and not a tuning knob: the unbounded witness
            # was measured (read-only, against production) to cost a flat
            # ~4-5 GB bloom false-positive scan per seed statement whatever the
            # slice's width, with no measured path to a page under five
            # seconds; the bounded shape's cost is instead LINEAR in envelope
            # hours (~0.46-0.49 GB per hour - a dense four-hour slice reads
            # 3.66M rows / 4.47 GB in 3.8 s at ``max_threads`` 1 and 1.76 s at
            # 2, sparse hours 30-116 MB / 0.4 s), and at one hour of slack it
            # reproduced the unbounded results exactly on the measured cohort
            # (3 of 3 page digests, 150 of 150 classifier rows). What one hour
            # rests on there: the root itself carried the value in 165 of 165
            # matching traces, the largest child-witness lag was 468 s, and 0
            # of 1,000 sampled traces held a span more than two days from their
            # root. That is one project over one burst - bounds, not
            # guarantees - which is why narrowing the contract was an owner
            # decision, taken as the 1 h default; a per-project override is a
            # follow-up. The 168 h ceiling is one week;
            # beyond that the envelope stops bounding this lane's own windows.
            # See ``filter_seed_width_policy`` for the width schedule each mode
            # uses, which differs because only the bounded shape's cost tracks
            # the slice.
            # Default one hour as of the bounded-witness owner decision. The
            # measured cohort puts the largest child-witness lag at 468 s and
            # carries the value on the root itself in 165 of 165 matching
            # traces, so one hour of envelope omitted nothing there while
            # reading 28.7x fewer bytes than the unbounded shape (1.14 MB vs
            # 32.8 MB over the same sparse hours; one 4 h unbounded slice read
            # 521,441 rows where the bounded 1 h slice read 1,233). ZERO
            # remains the legacy escape hatch and emits no envelope at all, so
            # a tenant whose spans really do arrive more than an hour after
            # their root can be put back on the old contract without a deploy.
            # FOLLOW-UP: this is one global number for a property that is
            # per-tenant (how long after its root a trace's spans may still
            # arrive). A per-project override belongs here, so the escape hatch
            # does not have to be pulled for the whole install.
            ("FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS", 1, 0, 168),
            # The same envelope, row budget and density probe on the OTHER two
            # trace candidate seed lanes - numeric (``span_attr_num`` value
            # bloom) and long text (schema 023's concatenated-lowercase LIKE
            # index). Both open at the FULL request window in one statement
            # today, which is the 12M-shaped hazard the short lane's row budget
            # already removed; the numeric lane's witness CTE additionally
            # carries no time restriction at all, so one statement scans the
            # project's whole retained history.
            #
            # ZERO - the default - is today's contract exactly: no envelope, no
            # width policy, no probe, and SQL, parameters and cursor payload
            # byte-identical to what ships without this setting. Above zero the
            # lane adopts the short lane's bounded schedule: the witness must
            # start inside
            #     [hour_floor(slice_start) - slack, hour_ceil(slice_end) + slack)
            # the seed opens at one hour under the row budget, and a width above
            # the unprobed cap must first be costed by ``EXPLAIN ESTIMATE``.
            #
            # It is a SEPARATE setting because it is a SEPARATE contract. The
            # approved 1 h slack was argued from a measured cohort of the short
            # exact-string lane (largest child-witness lag 468 s; the root
            # itself carried the value in 165 of 165 matching traces); nothing
            # was measured about how long after its root a trace's spans may
            # still carry a matching NUMBER or a matching long literal, and a
            # numeric attribute written on a closing span is the concrete
            # failure. Off until an owner approves the contract for these lanes
            # on their own evidence; the trace-list approval does not transfer.
            ("FILTER_SELECTOR_NUMERIC_LONG_TEXT_SEED_WITNESS_SLACK_HOURS", 0, 0, 168),
            # Broad key-only span population proofs read thin raw columns;
            # their CPU budget is separate from the normal seed/classifier.
            ("FILTER_SELECTOR_POPULATION_MAX_THREADS", 2, 1, 4),
            # The SPAN list's own two row budgets. The span lane has two
            # statements whose cost tracks the rows inside an interval rather
            # than the interval's width, and they read DIFFERENT columns, so
            # one number cannot serve both.
            #
            # THE SEED replays the typed Map of every physical row inside its
            # slice - 3.73 KB of ``attrs_string`` per row measured (755,996
            # rows = 2.82 GB), about 0.3M rows/s at one worker - so 500,000
            # rows is roughly 1.7 s of that Map walk.
            #
            # THE POPULATION-DISCOVERY PROOF reads ``start_time`` plus the thin
            # Map ``.keys`` stream its witness evaluates, and no Map VALUE at
            # all. Measured read-only against production at one worker:
            # 831,771 rows in 0.567 s (36.5 MB, 43.9 B/row), i.e. ~1.5M rows/s,
            # so 2,000,000 rows is about 1.4 s - less at this proof's own
            # two-worker budget above. The number is calibrated for the witness
            # the proof actually CARRIES: a proof reading ``start_time`` alone
            # walks more than an order of magnitude faster, and a budget chosen
            # for that shape would not bound this statement.
            #
            # Both are consumed as ``EXPLAIN ESTIMATE`` rows, which are an
            # upper bound twice over - whole granules, and every physical
            # version inside them - so both errors point at a NARROWER issued
            # interval. Narrowing either never skips history: intervals are
            # contiguous and half-open and the remainder is the next adjacent
            # interval's work.
            (
                "FILTER_SELECTOR_SPAN_SEED_TARGET_READ_ROWS",
                500_000,
                50_000,
                50_000_000,
            ),
            (
                "FILTER_SELECTOR_SPAN_POPULATION_DISCOVERY_TARGET_READ_ROWS",
                2_000_000,
                100_000,
                2_000_000_000,
            ),
            ("FILTER_SELECTOR_MAX_NUMBERED_PAGE_WORK_ROWS", 5_000, 1, 100_000),
            ("OBSERVABILITY_NAVIGATION_CANDIDATE_LIMIT", 4_095, 1, 65_535),
            ("OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE", 200, 1, 1_000),
            ("OBSERVABILITY_NAVIGATION_MAX_QUERIES", 128, 1, 1_024),
            ("TRACE_LIST_ENRICHMENT_CHUNK_SIZE", 100, 1, 500),
            ("TRACE_LIST_ENRICHMENT_MAX_WORKERS", 2, 1, 16),
            ("TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT", 50_000, 1, 1_000_000),
            ("VOICE_CONTENT_MAX_QUERY_ATTEMPTS", 64, 1, 1_024),
            ("VOICE_CONTENT_MAX_BATCH_SIZE", 200, 1, 5_000),
            # Page hydration is part of the same request-owned 30-second wall.
            # Do not impose a smaller per-statement cap: wide but finite voice
            # rows can legitimately need more than 1.5 seconds to hydrate.
            ("VOICE_CONTENT_MIN_REMAINING_MS", 30_000, 1, 60_000),
            ("VOICE_LIST_DEFAULT_PAGE_SIZE", 10, 1, 5_000),
            ("VOICE_FILTER_CLASSIFY_FALLBACK_BATCH_SIZE", 50, 1, 5_000),
            ("VOICE_FILTER_EXPENSIVE_CLASSIFIER_CHUNKS", 4, 1, 64),
            ("VOICE_FILTER_LIGHT_CLASSIFIER_CHUNKS", 8, 1, 64),
            ("VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE", 512, 1, 5_000),
            # Voice's own witness slack for the short exact-string seed lane,
            # read instead of FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS.
            # The default is ZERO - today's contract - because a voice call
            # writes its ``call.*`` attributes on the span that CLOSES the
            # call, so a long conversation's only matching witness can start
            # many hours after its root and the trace list's approved one-hour
            # envelope would drop it from a filtered page. Above zero the seed
            # additionally requires a witness to start inside
            #     [hour_floor(slice_start) - slack, hour_ceil(slice_end) + slack)
            # which is candidacy only: the unbounded latest-state classifier
            # still decides membership. Raise it per install only after
            # measuring that tenant's voice child-witness lag.
            ("VOICE_FILTER_TEXT_SEED_WITNESS_SLACK_HOURS", 0, 0, 168),
            ("SESSION_LIST_READ_MAX_THREADS", 2, 1, 16),
            ("SESSION_LIST_MAX_RESULT_BYTES", 32 * 1024**2, 64 * 1024, 512 * 1024**2),
            ("SESSION_LIST_FILTER_MAX_CANDIDATES", 200, 1, 5_000),
            # Whether the bounded session seed narrows candidacy by the
            # filter's own any-span witness, and how many hours of slack that
            # witness scan is allowed around the roots one seed statement can
            # publish.
            #
            # NEGATIVE (the default) is today's contract: the seed carries no
            # attribute predicate at all and groups every root span of its
            # slice, so the generated SQL and its parameters are byte-identical
            # to what shipped before this setting.
            #
            # ZERO seeds the identity superset with a time-UNBOUNDED witness: a
            # session is a candidate when any raw span of it carries the value,
            # whenever that span started. That publishes exactly the same rows
            # as the default - the witness is a necessary condition of a match
            # and ``build_filter_match_query`` stays authoritative - but its
            # cost is unmeasured on this surface and the trace lane's
            # equivalent scan read tens of millions of rows per statement.
            #
            # ABOVE ZERO additionally requires the witness to start inside
            #     [hour_floor(slice_start) - slack, hour_ceil(slice_end) + slack)
            # On sessions this is WEAKER than the trace list's bounded-witness
            # contract and is NOT approved. A session is discovered by any of
            # its roots but ranked by its oldest, and a continuation hop
            # resumes at the rank the previous page last published, C, so every
            # envelope that hop emits ends at or below the end of the hour
            # holding C plus the slack (its first slice ends at C + 1us). A
            # session is therefore dropped when every trace of it that carries
            # a witnessing span is rooted above C: the loss is governed by the
            # session's root-to-root spread, which can be as wide as the
            # request window, so no slack short of the window closes it. Zero
            # stays exact. Needs the owner's decision before a deployment moves
            # off the default.
            ("SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS", -1, -1, 168),
            ("SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS", 24, 1, 512),
            ("SESSION_LIST_FILTER_MAX_QUERIES", 48, 1, 1_024),
            ("ANNOTATION_QUEUE_ADD_ITEMS_SYNC_MAX", 1_000, 1, 10_000),
            ("ANNOTATION_QUEUE_EXPORT_SYNC_MAX_ITEMS", 1_000, 1, 10_000),
            (
                "ANNOTATION_QUEUE_AUTOMATION_MAX_RESPONSE_UNITS",
                1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("ANNOTATION_QUEUE_DEADLINE_CHECK_INTERVAL", 128, 1, 10_000),
            ("ANNOTATION_QUEUE_DEADLINE_BULK_BATCH_SIZE", 500, 1, 10_000),
            ("ANNOTATION_QUEUE_AUTOMATION_DEFAULT_PAGE_SIZE", 25, 1, 500),
            ("ANNOTATION_QUEUE_AUTOMATION_MAX_PAGE_SIZE", 100, 1, 500),
            ("BULK_SELECTION_MAX_CAP", 10_000, 1, 100_000),
            ("BULK_SELECTION_DEADLINE_MS", 15_000, 100, 60_000),
            ("BULK_SELECTION_MAX_SEED_ATTEMPTS", 64, 1, 512),
            ("BULK_SELECTION_MAX_QUERY_COUNT", 128, 1, 1024),
            ("BULK_SELECTION_MAX_CANDIDATES", 200, 1, 5_000),
            ("BULK_SELECTION_CLASSIFY_BATCH_SIZE", 200, 1, 5_000),
            ("BULK_SELECTION_MAX_RAW_PAGE_SIZE", 12_799, 1, 100_000),
            ("BULK_SELECTION_MAX_EXCLUDE_COUNT", 12_797, 0, 100_000),
            ("SMART_FILTER_REQUEST_WALL_MS", 9_000, 100, 60_000),
            ("SMART_FILTER_VALUE_READ_WALL_MS", 4_000, 100, 10_000),
            ("SMART_FILTER_VALUE_LIMIT", 100, 1, 1_000),
            ("SMART_FILTER_SEARCH_MAX_BYTES", 256, 1, 4_096),
            ("SMART_FILTER_PROJECT_SCOPE_LIMIT", 1_000, 1, 10_000),
            ("SMART_FILTER_GROUNDED_VALUE_LIMIT", 20, 1, 200),
            ("EVAL_LOG_MAX_OFFSET", 1_000_000, 1_000, 10_000_000),
            ("EVAL_LOG_DEFAULT_PAGE_SIZE", 10, 1, 500),
            ("EVAL_METRIC_MAX_WINDOW_DAYS", 365, 1, 3660),
            ("EVAL_METRIC_MAX_CHOICE_SCORES", 100, 1, 1_000),
            ("EVAL_METRIC_CHOICE_LABEL_MAX_UTF8_BYTES", 1_024, 1, 16 * 1024),
            ("EVAL_METRIC_NUMERIC_TEXT_MAX_CHARS", 64, 1, 512),
            ("EVAL_METRIC_ABS_SCORE_LIMIT", 1_000_000, 1, 1_000_000_000),
            ("EVAL_METRIC_BUCKET_DEADLINE_CHECK_INTERVAL", 32, 1, 10_000),
            ("EVAL_LOG_MAX_SEARCH_LENGTH", 512, 1, 4_096),
            ("EVAL_LOG_MAX_SEARCH_COLUMNS", 64, 1, 512),
            ("EVAL_LOG_MAX_SORT_COLUMNS", 3, 1, 16),
            ("EVAL_LOG_MAX_COLUMNS", 200, 1, 1_000),
            ("EVAL_LOG_COLUMN_NAME_MAX_CHARS", 2_000, 1, 16 * 1024),
            ("EVAL_LOG_REQUIRED_KEYS_LIMIT", 100, 1, 1_000),
            ("EVAL_LOG_ROW_DEADLINE_CHECK_INTERVAL", 8, 1, 10_000),
            ("EVAL_LOG_COLUMN_DEADLINE_CHECK_INTERVAL", 32, 1, 10_000),
            ("EVAL_TASK_USAGE_DETAIL_TEXT_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_USAGE_JSON_PREVIEW_MAX_CHARS", 2 * 1024, 256, 64 * 1024),
            ("EVAL_TASK_USAGE_MAPPING_PATH_LIMIT", 16, 1, 256),
            ("EVAL_TASK_USAGE_MAPPING_ENTRY_LIMIT", 16, 1, 256),
            ("EVAL_TASK_USAGE_MAPPING_JSON_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_USAGE_OMITTED_FIELDS_LIMIT", 24, 1, 512),
            ("EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_CHARS", 64 * 1024, 1024, 1024**2),
            (
                "EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_UNITS",
                512 * 1024,
                1024,
                8 * 1024**2,
            ),
            ("EVAL_TASK_ERROR_TEXT_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_WARNING_KEY_LIMIT", 32, 1, 512),
            ("EVAL_TASK_WARNING_KEY_MAX_CHARS", 128, 1, 4_096),
            ("EVAL_TASK_WARNING_MESSAGE_MAX_CHARS", 1024, 1, 64 * 1024),
            ("EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT", 1_000, 1, 100_000),
            ("EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT", 5_000, 1, 500_000),
            ("EVAL_TASK_LIST_MAX_OFFSET", 50_000, 1_000, 1_000_000),
            ("EVAL_TASK_LIST_DEFAULT_PAGE_SIZE", 30, 1, 500),
            ("EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE", 10, 1, 500),
            ("EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE", 25, 1, 500),
            ("EVAL_TASK_USAGE_MAX_PAGE_SIZE", 100, 1, 500),
            ("EVAL_TASK_USAGE_MAX_PAGE_NUMBER", 100, 1, 10_000),
            (
                "EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS",
                512 * 1024,
                64 * 1024,
                8 * 1024**2,
            ),
            ("EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS", 1024**2, 64 * 1024, 16 * 1024**2),
            ("EVAL_TASK_USAGE_MAX_CHART_POINTS", 367, 1, 3_660),
            ("EVAL_TASK_USAGE_AGGREGATION_ROW_LIMIT", 5_000, 1, 100_000),
            ("EVAL_TASK_ERROR_GROUPS_LIMIT", 50, 1, 1_000),
            ("EVAL_TASK_WARNING_GROUPS_LIMIT", 20, 1, 1_000),
            ("EVAL_TASK_WARNING_LOG_SCAN_LIMIT", 1_000, 1, 100_000),
            ("PROMPT_METRICS_MAX_EVAL_COLUMNS", 50, 1, 500),
            ("PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES", 512, 1, 16 * 1024),
            ("PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES", 16 * 1024, 1, 1024**2),
            ("PROMPT_METRICS_MAX_OFFSET", 50_000, 1_000, 1_000_000),
            (
                "PROMPT_METRICS_SPAN_PAGE_DB_PAYLOAD_BYTES",
                1_500_000,
                64 * 1024,
                64 * 1024**2,
            ),
            ("SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE", 50, 1, 500),
            ("SIMULATION_PREVIEW_MAX_PAGE_SIZE", 50, 1, 500),
            ("SIMULATION_PREVIEW_CURSOR_MAX_AGE_SECONDS", 60 * 60, 60, 24 * 60 * 60),
        )
    ),
    **_specs(
        (
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION", 0.25, 0.01, 1.0),
            ("REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS", 1.0, 0.05, 2.0),
            ("REDIS_CACHE_SOCKET_TIMEOUT_SECONDS", 1.0, 0.05, 2.0),
        ),
        value_type=float,
    ),
}

RUNTIME_NUMERIC_SETTING_SPECS = {
    **PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
    **DATASET_READ_SETTING_SPECS,
    **INTERACTIVE_READ_SETTING_SPECS,
    **EVAL_EXECUTION_SETTING_SPECS,
}

if len(RUNTIME_NUMERIC_SETTING_SPECS) != sum(
    map(
        len,
        (
            PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
            DATASET_READ_SETTING_SPECS,
            INTERACTIVE_READ_SETTING_SPECS,
            EVAL_EXECUTION_SETTING_SPECS,
        ),
    )
):
    raise RuntimeError("runtime numeric setting names must be unique")


def load_numeric_settings(
    specs: Mapping[str, NumericSettingSpec],
    *,
    source: object,
    fallback: object | None = None,
) -> dict[str, Numeric]:
    """Resolve a complete bounded setting group from mappings or objects."""

    return {
        name: spec.parse(name, _read_value(source, fallback, name))
        for name, spec in specs.items()
    }


def _read_value(source: object, fallback: object | None, name: str) -> object:
    value = _lookup(source, name)
    if not _is_missing(value):
        return value
    return _lookup(fallback, name) if fallback is not None else _MISSING


def _lookup(source: object, name: str) -> object:
    if isinstance(source, Mapping):
        return source.get(name, _MISSING)
    return getattr(source, name, _MISSING)


def validate_property_catalog_settings(values: Mapping[str, Numeric]) -> None:
    """Validate relationships that cannot be expressed by one field's bounds."""

    def value(name: str) -> Numeric:
        return values[f"PROPERTY_CATALOG_{name}"]

    _require_at_most(
        value("READ_MAX_THREADS"),
        value("READ_POOL_SIZE"),
        "ClickHouse read threads cannot exceed the read pool size",
    )
    _require_at_most(
        value("READ_MAX_RESULT_BYTES"),
        min(value("READ_MAX_BYTES"), value("READ_MAX_MEMORY_BYTES")),
        "ClickHouse result bytes cannot exceed read or memory bytes",
    )
    _require_at_most(
        value("READ_EXTERNAL_GROUP_BY_BYTES"),
        value("READ_MAX_MEMORY_BYTES"),
        "ClickHouse external group-by threshold cannot exceed read memory",
    )
    _require_at_most(
        value("READ_EXTERNAL_SORT_BYTES"),
        value("READ_MAX_MEMORY_BYTES"),
        "ClickHouse external sort threshold cannot exceed read memory",
    )


def validate_dataset_read_settings(values: Mapping[str, Numeric]) -> None:
    """Validate dataset limits that must remain internally consistent."""

    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_COLUMNS"],
        values["DATASET_TABLE_EXACT_MAX_CELLS"],
        "dataset column limit cannot exceed the cell limit",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES"],
        "single dataset cell bytes cannot exceed the variable-data budget",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES"],
        "dataset variable-data budget cannot exceed the serialized budget",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES"],
        "dataset schema budget cannot exceed the serialized budget",
    )
    _require_at_most(
        values["DATASET_ROW_ADJACENCY_MAX_ROWS"],
        values["DATASET_INTERACTIVE_MAX_PAGE_SIZE"],
        "dataset adjacency rows cannot exceed the interactive page maximum",
    )


def validate_interactive_read_settings(values: Mapping[str, Numeric]) -> None:
    """Validate cross-field relationships for interactive read controls."""

    _require_at_most(
        values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "interactive analytics wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "interactive read wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "default ClickHouse read threads cannot exceed the application maximum",
    )
    if not (
        values["CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS"]
        <= values["CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS"]
        <= values["CLICKHOUSE_READ_ADMISSION_RETRY_THIRD_MS"]
    ):
        raise ValueError("ClickHouse read-admission retry delays must be ordered")
    _require_at_most(
        values["EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS"],
        values["EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE"],
        "exact-graph candidate rows cannot exceed the selector page size",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE"],
        values["EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE"],
        "exact-graph root verification batch cannot exceed the classifier batch",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS"],
        values["EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS"],
        "exact-graph minimum anchor partition cannot exceed its starting width",
    )
    if not (
        values["EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS"]
        <= values["EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS"]
        <= values["EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS"]
    ):
        raise ValueError(
            "exact-graph trace slices must satisfy minimum <= initial <= maximum"
        )
    _require_at_most(
        values["EXACT_GRAPH_SPAN_INITIAL_PARTITION_HOURS"],
        values["EXACT_GRAPH_SPAN_MAX_PARTITION_HOURS"],
        "exact-graph initial span partition cannot exceed its maximum width",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "exact-graph classifier threads cannot exceed the application maximum",
    )
    _require_at_most(
        values["ANALYTICS_DEFAULT_LOOKBACK_DAYS"],
        values["EVAL_METRIC_MAX_WINDOW_DAYS"],
        "default analytics lookback cannot exceed the eval-metric maximum window",
    )
    _require_at_most(
        values["MONITOR_GRAPH_CH_TIMEOUT_CAP_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "MONITOR_GRAPH_CH_TIMEOUT_CAP_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    _require_at_most(
        values["MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    if (
        not values["FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS"]
        <= values["FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS"]
        <= values["FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS"]
    ):
        raise ValueError(
            "filter value cursor segment limits must satisfy minimum <= initial <= maximum"
        )
    _require_at_most(
        values["USER_LIST_PAGE_WALL_MS"],
        values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"],
        "USER_LIST_PAGE_WALL_MS cannot exceed INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS",
    )
    _require_at_most(
        values["USER_LIST_WALK_INITIAL_SLICE_SECONDS"],
        values["USER_LIST_WALK_MAX_SLICE_SECONDS"],
        "USER_LIST_WALK_INITIAL_SLICE_SECONDS cannot exceed USER_LIST_WALK_MAX_SLICE_SECONDS",
    )
    _require_at_most(
        values["USER_LIST_WALK_MIN_SLICE_SECONDS"],
        values["USER_LIST_WALK_INITIAL_SLICE_SECONDS"],
        "USER_LIST_WALK_MIN_SLICE_SECONDS cannot exceed USER_LIST_WALK_INITIAL_SLICE_SECONDS",
    )
    _require_at_most(
        values["USER_LIST_WALK_PROBE_WALL_MS"],
        values["USER_LIST_PAGE_WALL_MS"],
        "USER_LIST_WALK_PROBE_WALL_MS cannot exceed USER_LIST_PAGE_WALL_MS",
    )
    _require_at_most(
        values["FILTER_SELECTOR_QUERY_TIMEOUT_MS"],
        values["FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS"],
        "FILTER_SELECTOR_QUERY_TIMEOUT_MS cannot exceed FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS",
    )
    _require_at_most(
        values["FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS"],
        values["FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS"],
        "FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS cannot exceed FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS",
    )
    if (
        values["BULK_SELECTION_MAX_EXCLUDE_COUNT"]
        >= values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_EXCLUDE_COUNT must be less than BULK_SELECTION_MAX_RAW_PAGE_SIZE"
        )
    if (
        values["BULK_SELECTION_MAX_CAP"] + 1
        > values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_RAW_PAGE_SIZE must fit the capped result plus its overflow sentinel"
        )
    bulk_prefix_rows = values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"] + 1
    bulk_seed_queries = _ceil_div(
        bulk_prefix_rows,
        values["BULK_SELECTION_MAX_CANDIDATES"],
    )
    if bulk_seed_queries > values["BULK_SELECTION_MAX_SEED_ATTEMPTS"]:
        raise ValueError(
            "BULK_SELECTION_MAX_SEED_ATTEMPTS cannot prove the configured raw page"
        )
    if (
        bounded_bulk_worst_case_query_count(
            raw_page_size=int(values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]),
            max_candidates=int(values["BULK_SELECTION_MAX_CANDIDATES"]),
            classify_batch_size=int(values["BULK_SELECTION_CLASSIFY_BATCH_SIZE"]),
        )
        > values["BULK_SELECTION_MAX_QUERY_COUNT"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_QUERY_COUNT cannot prove the configured raw page"
        )
    _require_at_most(
        values["SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE"],
        values["SIMULATION_PREVIEW_MAX_PAGE_SIZE"],
        "SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE cannot exceed SIMULATION_PREVIEW_MAX_PAGE_SIZE",
    )
    _require_at_most(
        values["DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    _require_at_most(
        values["DASHBOARD_METRICS_CATALOG_DEFAULT_PAGE_SIZE"],
        values["DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE"],
        "dashboard metrics catalog default page cannot exceed its maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_WALL_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "filter-value wall cannot exceed the interactive request wall",
    )
    _require_at_most(
        values["FILTER_VALUE_READ_TIMEOUT_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "filter-value read timeout cannot exceed the interactive request wall",
    )
    _require_at_most(
        values["GRAPH_BACKGROUND_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "background graph wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE"],
        values["DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE"],
        "filter-value search page cannot exceed the page maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE"],
        values["DASHBOARD_FILTER_VALUE_FINITE_MAX"],
        "filter-value page maximum cannot exceed the finite-value maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_LEGACY_MAX"],
        values["DASHBOARD_FILTER_VALUE_FINITE_MAX"],
        "legacy filter-value maximum cannot exceed the finite-value maximum",
    )
    _require_at_most(
        values["DASHBOARD_TRACE_MAX_CONCURRENT_METRICS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "dashboard metric concurrency cannot exceed ClickHouse read threads",
    )
    _require_at_most(
        values["SMART_FILTER_VALUE_READ_WALL_MS"],
        values["SMART_FILTER_REQUEST_WALL_MS"],
        "smart-filter value wall cannot exceed the request wall",
    )
    _require_at_most(
        values["BULK_SELECTION_CLASSIFY_BATCH_SIZE"],
        values["BULK_SELECTION_MAX_CANDIDATES"],
        "bulk-selection classify batch cannot exceed the candidate bound",
    )
    _require_at_most(
        values["PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES"],
        values["PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES"],
        "one prompt choice cannot exceed the total choice-byte budget",
    )
    _require_at_most(
        values["REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS"],
        values["REDIS_CACHE_SOCKET_TIMEOUT_SECONDS"],
        "Redis connect timeout cannot exceed its socket timeout",
    )
    _require_at_most(
        values["ANNOTATION_QUEUE_AUTOMATION_DEFAULT_PAGE_SIZE"],
        values["ANNOTATION_QUEUE_AUTOMATION_MAX_PAGE_SIZE"],
        "annotation automation default page size cannot exceed its maximum",
    )
    _require_at_most(
        values["EVAL_LOG_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "EVAL_LOG_DEFAULT_PAGE_SIZE cannot exceed INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE",
    )
    _require_at_most(
        values["EVAL_LOG_REQUIRED_KEYS_LIMIT"],
        values["EVAL_LOG_MAX_COLUMNS"],
        "eval-log required key limit cannot exceed its column limit",
    )
    _require_at_most(
        values["EVAL_TASK_LIST_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task default page cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task project page cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE"],
        values["EVAL_TASK_USAGE_MAX_PAGE_SIZE"],
        "eval-task usage default page cannot exceed its maximum",
    )
    _require_at_most(
        values["EVAL_TASK_USAGE_MAX_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task usage page maximum cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["VOICE_FILTER_CLASSIFY_FALLBACK_BATCH_SIZE"],
        values["VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE"],
        "voice classifier fallback batch cannot exceed the public page maximum",
    )
    _require_at_most(
        values["VOICE_LIST_DEFAULT_PAGE_SIZE"],
        values["VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE"],
        "voice default page cannot exceed the public page maximum",
    )
    _require_at_most(
        values["VOICE_CONTENT_MIN_REMAINING_MS"],
        values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"],
        "voice content reserve cannot exceed the interactive analytics wall",
    )


def validate_eval_execution_settings(values: Mapping[str, Numeric]) -> None:
    """Validate the eval-execution knobs against the workflow's own ceilings.

    The spec bound already carries this relation, but it carries it as a
    literal a future edit can loosen. This checks the *resolved* value against
    the mirrored constants, so loosening the bound alone is not enough to ship
    a configuration that lets the sweep race a live worker.
    """

    _require_at_least(
        values["EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS"],
        LONGEST_RUNNING_ENTRY_SECONDS + 1,
        "the eval-task sweep's stale threshold must exceed a running entry's "
        "longest legitimate life",
    )


def validate_runtime_numeric_settings(values: Mapping[str, Numeric]) -> None:
    validate_property_catalog_settings(values)
    validate_dataset_read_settings(values)
    validate_interactive_read_settings(values)
    validate_eval_execution_settings(values)
    _require_at_most(
        values["PROPERTY_CATALOG_MAX_PAGE_SIZE"],
        values["DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE"],
        "property catalog cursor page cannot exceed the dashboard catalog maximum",
    )


def _require_at_most(left: Numeric, right: Numeric, message: str) -> None:
    if left > right:
        raise ValueError(message)


def _require_at_least(left: Numeric, right: Numeric, message: str) -> None:
    if left < right:
        raise ValueError(message)


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def bounded_bulk_worst_case_query_count(
    *,
    raw_page_size: int,
    max_candidates: int,
    classify_batch_size: int,
) -> int:
    """Count seed and classifier reads needed to prove a raw result prefix."""

    if raw_page_size < 0 or max_candidates < 1 or classify_batch_size < 1:
        raise ValueError("bulk-selection query-count inputs must be positive")
    prefix_needed = raw_page_size + 1
    full_seed_pages, final_seed_rows = divmod(prefix_needed, max_candidates)
    classifiers_per_full_seed = _ceil_div(max_candidates, classify_batch_size)
    query_count = full_seed_pages * (1 + classifiers_per_full_seed)
    if final_seed_rows:
        query_count += 1 + _ceil_div(final_seed_rows, classify_batch_size)
    return query_count

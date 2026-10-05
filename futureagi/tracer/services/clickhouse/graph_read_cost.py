"""Cost the filtered graph's raw span scan before the statement is issued.

A filtered Observe graph reads every physical span in its window and folds
trace membership in ClickHouse. On the highest-volume reference tenant that
window is hundreds of millions of rows, and the read learns it only by
running: the interactive wall expires, the dispatcher returns a degraded
payload, and the background worker then re-runs the identical SQL. The wall is
spent and nothing is published any sooner.

This module answers the same question from part metadata. ``EXPLAIN ESTIMATE``
over the statement's own key condition reports the rows the part index says
that scan will touch without reading a single part; a measured scan rate turns
those rows into milliseconds, compared against the deadline the request
actually has left. Nothing here decides membership, prunes a candidate or
reaches a published point: it decides which lane runs the statement, and the
statement is byte-identical either way, so the answer a user finally sees is
the same value an ungated read would have produced.

The estimate is an upper bound in principle - whole granules, and every
physical ``ReplacingMergeTree`` version inside them - and the scan window it
costs is the widest one the builder can emit, so its error points at
scheduling a read that might have fitted rather than running one that cannot.
In practice it is not loose: on the reference tenant the index says 311,867,981
rows over twelve months and the statement then reads 311,867,844, a difference
of 137.

What the gate must never do is treat NOT KNOWING as knowing. A probe that
cannot answer leaves the read uncosted, and an uncosted read is the one least
safe to issue on a wall whose expiry costs a user their whole request. Absence
of proof means schedule, not scan - see ``raw_graph_scan_fits_wall``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

# The column names ClickHouse returns for ``EXPLAIN ESTIMATE``. They are the
# discriminator that tells a genuine estimate from a transport that answered
# something else entirely; the row count cannot do that job.
_ESTIMATE_COLUMNS = frozenset({"database", "table", "parts", "rows", "marks"})
_ESTIMATE_TABLE = "spans"
# The raw graph statement scans ``[start - 1 day, end + 1 day)`` whenever it
# carries row predicates, so that - not the request window - is what a cost
# estimate must cover. A span graph scans the narrower request window; costing
# it at the wider one keeps a single statement honest for both families and
# errs toward scheduling.
_SCAN_WINDOW_MARGIN = timedelta(days=1)
# Measured on production against the highest-volume reference tenant, running
# this surface's own unseeded filtered trace statement at
# DASHBOARD_TRACE_READ_MAX_THREADS workers. A rate has to be taken from a read
# that FINISHED, and at the window it is used to predict; both are what the
# previous calibration here lacked.
#
#   thirty days, run to completion: 87,413,844 physical spans in 48,677 ms of
#   server time = 1,796 rows/ms;
#   twelve months, the same statement over five abutting scan windows whose
#   union is exactly the one the whole statement reads: 311,867,844 spans and
#   756.4 GB in 170,148 ms = 1,833 rows/ms.
#
# The slower of the two is the constant. Both are whole-window numbers, and
# they agree to two percent, which is the point: the statement's cost per row
# is stable even though its cost per BYTE is not - the same tenant's rows are
# roughly 1.0 kB in an older month and 3.4 kB in a recent one, and the read
# sustains 4.0-4.6 GB/s throughout.
#
# The calibration this replaces was 968 rows/ms, taken from a 30-day read that
# was CUT by the measuring profile's 12 GiB ceiling after 5,756 ms: at that
# length the statement's fixed start-up dominates, so it measured a read
# getting going rather than a read running. Predicting with it roughly doubled
# every window's cost, which is how twelve months on this tenant came to be
# declared unreachable on any wall - measured, it is 170 s against a 180 s
# background wall. Erring slow is not free: it refuses charts that would have
# rendered.
#
# This is a throughput calibration, not a window: the affordable row count is
# always this rate multiplied by the milliseconds the request still has, so a
# longer wall admits a proportionally larger scan and no window length is
# written down anywhere.
_RAW_SCAN_ROWS_PER_MS = 1_796

# The Voice chart's "exclude simulation calls" toggle adds a predicate that
# reads every span's raw_log - the ``attributes_extra`` JSON and the
# ``attrs_string`` map - to find simulator phone numbers. That arm costs the
# BYTES of those columns, and a voice call's raw_log is a whole call record,
# so the span count above cannot price it. Measured read-only on dev against
# project 5272afb0, this surface's own statement at
# DASHBOARD_TRACE_READ_MAX_THREADS workers, first run of each window:
#
#   toggle off: 90 days 4.44 MiB in 708 ms, 180 days 7.52 MiB in 524 ms;
#   toggle on:  90 days 5.72 GiB in 4,579 ms, 180 days 15.92 GiB in 14,117 ms
#   (47,824 and 69,587 estimated spans, which the rate above prices at 27 ms
#   and 39 ms).
#
# Restricting the parse to VAPI/Retell conversation roots in PREWHERE read the
# same 5.72 GiB: the calls interleave with other fat roots in the same
# granules, and a granule is the unit ClickHouse reads.
#
# So the arm is priced by granules. ``spans`` closes a granule at 64 MiB
# (``index_granularity_bytes``, v2/schema/002_spans_v2.sql), which bounds the
# bytes any column subset can read from it; the measured reads stayed under
# that cap at 12-45 MiB per granule. The throughput is the slowest whole read,
# the 180-day one, whose older half sits on the cold storage tier: 17,095,283,420
# bytes in 14,117 ms. One full granule at that rate is 55.4 ms, rounded up.
# The price is additive with the span rate above (both are the one statement's
# work, so this errs toward scheduling), an upper bound of every measured
# window, and it is coupled to that granule cap and to
# DASHBOARD_TRACE_READ_MAX_THREADS: re-measure if either moves.
# Production, read-only on 2026-09-28 (largest voice project, 7/30/90 days,
# 82/203/420 granules): 3.7-7.9 ms a granule, so this price is 7-15 times
# production's. It errs toward the worker; recalibrate it on production.
_RAW_LOG_GRANULE_SCAN_MS = 56

# The eval and annotation charts carry the same toggle in a different
# statement: ``latest_span_membership_source_sql`` over ``spans FINAL`` at
# EXACT_GRAPH_READ_SETTINGS (FILTER_SELECTOR_MAX_THREADS = 1 worker), which
# collects every trace in the window whose live root is a simulator call and
# drops it with ``trace_id NOT IN``. Measured read-only on dev on 2026-09-26,
# first run of each window (bytes read / granules / duration; granule counts
# drift as parts merge: the 90-day eval window was 148 granules on 09-28):
#
#   the eval chart statement (EvalMetricsQueryBuilderV2, project 2843b914):
#     30 days 977 MiB / 96 / 1,206 ms; 90 days 4.81 GiB / 302 / 6,628 ms;
#     180 days killed by the 20 s read-only cap after 4.73 GiB at 2.19 GiB of
#     memory (toggle off 1,209 ms). Its older half on the cold storage tier:
#     03-30..05-14 3.26 GiB / 107 / 15,479 ms, 05-14..06-28 2.26 GiB / 85 /
#     11,781 ms;
#   one annotation membership batch of 14 traces (project 5272afb0): 135 days
#   3.87 GiB / 276 / 13,786 ms; 180 days killed at 20 s (toggle off 436 ms).
#
# That is 13-22 ms a granule on the hot tier and 139-145 ms on the cold one.
# The constant is the cold rate rounded up, so it over-prices a hot window by
# up to twelve times: the 90-day eval chart above (6.6 s) is priced at 45 s
# and renders through the background worker. That is the direction this gate
# errs - a rate the hot tier licenses would admit the 180-day read, which did
# not finish in 20 s, to the 30 s wall. It is coupled to
# FILTER_SELECTOR_MAX_THREADS, to FINAL and to the storage tiering, and was
# measured on dev, not production: re-measure there. The annotation chart
# issues this membership once per batch of Score rows per output partition;
# the gate prices one.
_RAW_LOG_MEMBERSHIP_GRANULE_SCAN_MS = 150


def raw_graph_scan_window(
    start_date: datetime | None,
    end_date: datetime | None,
) -> tuple[datetime, datetime] | None:
    """Return the widest window the raw graph statement can scan."""

    if start_date is None or end_date is None or start_date >= end_date:
        return None
    return start_date - _SCAN_WINDOW_MARGIN, end_date + _SCAN_WINDOW_MARGIN


def reduce_spans_estimate(
    rows: Iterable[Mapping[str, Any]] | None,
    columns: Iterable[str] | None,
    *,
    field: str = "rows",
) -> int | None:
    """Reduce one ``EXPLAIN ESTIMATE`` result to the rows the scan would read.

    ``field="marks"`` reduces the granules instead, by the same rules.

    Three shapes have to be told apart, and the ``columns`` the transport
    reports are what separate the last one - not the row count:

    * the estimate table with part rows is their summed ``rows``;
    * the estimate table with NO rows is zero. For the statements this reads
      (the raw graph's scan and the Sessions root read) that is unambiguous:
      their key condition is ``project_id`` and a half-open ``start_time``
      range over a table partitioned by ``toDate(start_time)``, with no
      subquery and no step that could vanish, so "no part selected" means
      "nothing to read" and the scan is affordable;
    * anything else - a transport that answered something other than this
      statement, or a server whose estimate table changed shape - is ``None``,
      meaning unknown.
    """

    try:
        names = {str(name) for name in (columns or ())}
        candidate_rows = list(rows or ())
    except TypeError:
        # A transport that answered something other than a result set at all.
        # "Unknown" covers that too; raising here would turn a routing
        # optimisation into a failed request.
        return None
    if not _ESTIMATE_COLUMNS.issubset(names):
        return None
    estimate = 0
    for row in candidate_rows:
        if not isinstance(row, Mapping):
            return None
        if str(row.get("table") or "") != _ESTIMATE_TABLE:
            return None
        counted = row.get(field)
        if isinstance(counted, bool) or not isinstance(counted, (int, float)):
            return None
        estimate += max(0, int(counted))
    return estimate


def estimate_raw_graph_scan_rows(
    *,
    analytics: Any,
    project_id: str,
    scan_start: datetime,
    scan_end: datetime,
    timeout_ms: int,
) -> int | None:
    """Estimate the physical spans one filtered graph statement would read.

    Deliberately the cheapest statement that answers the question, and for the
    same reasons the trace list's own density probe gives:

    * the key condition is ``project_id`` plus the half-open ``start_time``
      range the statement itself scans, and nothing else. ``is_deleted`` is not
      in the primary key, so it could not narrow an index estimate; leaving it
      out keeps the statement honest about what it measures - every physical
      row in the granules the scan touches, tombstones and stale versions
      included, which is what the graph statement has to walk past;
    * no attribute predicate and no ``indexHint``. Measured on production, the
      attribute witness this surface's seed probe carries prunes 0.08% of the
      estimated rows (87.41M to 87.35M) and 109 of 14,532 granules,
      and costs 831 ms of single-worker skip-index analysis at 30 days and
      2,777 ms at twelve months - over that probe's own 1,500 ms budget. The
      pruning is not worth the money at any window;
    * no ``IN`` subquery: ``EXPLAIN`` executes a scalar/``IN`` subquery to plan
      around it, which would put a real read back inside a statement whose
      whole purpose is not to have one;
    * ``optimize_use_projections`` is pinned OFF. ``spans`` carries projections
      the optimizer will route a bare ``count()`` to, and ``rows`` would then
      describe that projection rather than the base table the graph statement
      reads. Measured on the same 30-day window, leaving projections on
      reports 10,846 marks where the base table needs 14,532, and
      ``force_optimize_projection=1`` does not throw on this statement - so a
      projection really is available to be chosen, and pinning it off is
      load-bearing rather than defensive.

    It answers a COST question only: which lane runs the statement, never what
    the statement returns. Measured at
    ``DASHBOARD_TRACE_READ_MAX_THREADS`` workers on the reference tenant it
    costs 27 ms of server time at thirty days and 90 ms at twelve months.
    """

    estimate = _explain_raw_graph_scan(
        analytics=analytics,
        project_id=project_id,
        scan_start=scan_start,
        scan_end=scan_end,
        timeout_ms=timeout_ms,
    )
    if estimate is None:
        return None
    return reduce_spans_estimate(*estimate)


def estimate_raw_log_graph_scan(
    *,
    analytics: Any,
    project_id: str,
    scan_start: datetime,
    scan_end: datetime,
    timeout_ms: int,
) -> tuple[int, int] | None:
    """Estimate the spans and granules a statement that parses raw_log reads.

    The probe ``estimate_raw_graph_scan_rows`` issues also reports the granules
    its key condition selects, and a raw_log parse costs granules - see
    ``_RAW_LOG_GRANULE_SCAN_MS``. One probe answers both. ``None`` when either
    count is missing: an uncounted parse is unknown, never small.
    """

    estimate = _explain_raw_graph_scan(
        analytics=analytics,
        project_id=project_id,
        scan_start=scan_start,
        scan_end=scan_end,
        timeout_ms=timeout_ms,
    )
    if estimate is None:
        return None
    rows = reduce_spans_estimate(*estimate)
    marks = reduce_spans_estimate(*estimate, field="marks")
    if rows is None or marks is None:
        return None
    return rows, marks


def _explain_raw_graph_scan(
    *,
    analytics: Any,
    project_id: str,
    scan_start: datetime,
    scan_end: datetime,
    timeout_ms: int,
) -> tuple[Any, Any] | None:
    """Issue the raw-scan cost probe; ``None`` when it cannot answer."""

    probe_settings = {
        "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
        "optimize_use_projections": 0,
    }
    query = """
        EXPLAIN ESTIMATE
        SELECT count()
        FROM spans
        WHERE project_id = toUUID(%(graph_cost_project_id)s)
          AND start_time >= %(graph_cost_scan_start)s
          AND start_time < %(graph_cost_scan_end)s
    """
    params = {
        "graph_cost_project_id": str(project_id),
        "graph_cost_scan_start": scan_start,
        "graph_cost_scan_end": scan_end,
    }
    try:
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=int(timeout_ms),
            settings=probe_settings,
        )
    except Exception:
        # ``None`` is "unknown", not "small". It is the caller's job to route an
        # unknown read to a wall that can survive being wrong about it, and
        # ``raw_graph_scan_fits_wall`` refuses to call it affordable, so a probe
        # that cannot answer can never license the interactive full-window scan.
        logger.info("graph_raw_scan_estimate_unavailable", exc_info=True)
        return None
    return getattr(result, "data", None), getattr(result, "columns", None)


def raw_graph_scan_fits_wall(
    estimated_rows: int | None,
    *,
    remaining_ms: int,
    raw_log_marks: int | None = None,
) -> bool:
    """Whether a scan of *estimated_rows* is PROVEN to complete in *remaining_ms*.

    ``raw_log_marks`` is the granule count of a statement that parses raw_log
    (the Voice chart's simulator toggle, see ``_RAW_LOG_GRANULE_SCAN_MS``).
    Those granules spend the wall first, and the spans must fit in what is
    left of it.

    An unknown estimate does not fit. A read nobody could cost is exactly the
    read least safe to issue unbounded: the shape this gate exists to remove is
    a probe that cannot answer, followed by the full-window statement spending
    the whole interactive wall and publishing nothing. Treating "unknown" as
    "affordable" reproduces that shape one probe earlier, and it does so
    precisely when the system knows least about the read.

    Absence of proof therefore means SCHEDULE, not SCAN. Callers that own a
    wider wall must say so themselves rather than read a ``True`` here: see
    ``_schedule_unaffordable_graph_read``, where an uncosted read is handed to
    the bounded background worker instead of being refused outright.
    """

    return _scan_fits_wall(
        estimated_rows,
        remaining_ms=remaining_ms,
        raw_log_marks=raw_log_marks,
        raw_log_ms=int(raw_log_marks or 0) * _RAW_LOG_GRANULE_SCAN_MS,
    )


def raw_log_membership_fits_wall(
    estimated_rows: int | None,
    *,
    remaining_ms: int,
    raw_log_marks: int | None = None,
) -> bool:
    """Whether an exact membership read that parses raw_log is PROVEN to fit.

    The eval and annotation charts' form of the simulator toggle, priced at
    ``_RAW_LOG_MEMBERSHIP_GRANULE_SCAN_MS`` a granule. It has
    ``raw_graph_scan_fits_wall``'s shape so ``_schedule_unaffordable_graph_read``
    can ask it about the background wall. The membership parses raw_log by
    definition, so an uncounted granule set is unknown, and unknown never fits.
    """

    if raw_log_marks is None:
        logger.info("graph_raw_log_membership_granules_unknown_not_affordable")
        return False
    return _scan_fits_wall(
        estimated_rows,
        remaining_ms=remaining_ms,
        raw_log_marks=raw_log_marks,
        raw_log_ms=int(raw_log_marks) * _RAW_LOG_MEMBERSHIP_GRANULE_SCAN_MS,
    )


def _scan_fits_wall(
    estimated_rows: int | None,
    *,
    remaining_ms: int,
    raw_log_marks: int | None,
    raw_log_ms: int,
) -> bool:
    """The raw_log granules spend the wall first; the spans get the rest."""

    if estimated_rows is None:
        logger.info("graph_raw_scan_estimate_unknown_not_affordable")
        return False
    span_ms = max(0, int(remaining_ms)) - raw_log_ms
    affordable_rows = max(0, span_ms) * _RAW_SCAN_ROWS_PER_MS
    if span_ms >= 0 and estimated_rows <= affordable_rows:
        return True
    logger.info(
        "graph_raw_scan_predicted_over_wall",
        estimated_rows=int(estimated_rows),
        raw_log_marks=raw_log_marks,
        affordable_rows=int(affordable_rows),
        remaining_ms=int(remaining_ms),
    )
    return False


# The aggregate users graph is a different statement from the raw filtered
# graph - one ordered latest-state pass (``argMax`` over nine columns, GROUP BY
# the sorting key in order, HAVING on the winner) over every physical span in
# its window - and it has its own cost per row. Measured on production against
# the highest-volume reference tenant, this surface's own statement at its own
# ``EXACT_GRAPH_USER_READ_SETTINGS`` (DASHBOARD_TRACE_READ_MAX_THREADS
# workers), at the windows the gate is asked about:
#
#   six months, one statement, four workers, read-only profile: 66.38M
#   physical spans and 9.43 GB in 120,005 ms of server time (the profile's
#   own 120 s ceiling; the window holds 311.3M rows, so the statement was
#   21% through) = 553 rows/ms, steady across eight 15 s progress samples;
#   the same statement's first thirty seconds at six and twelve months in the
#   production sweep read 16.9M rows each = 552 and 563 rows/ms.
#
# This statement costs about 3.2x more per row than the raw filtered graph
# statement (1,796 rows/ms above): it decompresses nine columns into an
# ``argMax`` tuple and retires every identity through an in-order GROUP BY,
# where the raw statement reads and folds. One constant cannot serve both.
#
# The consequence, stated: at 553 rows/ms the 180 s background wall affords
# 99.5M rows, about thirty days of the reference tenant's traffic, and the
# 30 s interactive wall 16.6M rows, about a week. A six-month window on that
# tenant (311M rows, predicted 563 s) is refused in the time the probe takes
# instead of after thirty seconds inline and a failed three-minute worker
# attempt. Like the raw constant this is a throughput, not a window:
# affordable rows are this rate multiplied by the milliseconds the request
# still has.
#
# Two caveats on the calibration. The measured run was cut at 21% of its
# window, so the tail - merging the external-aggregation parts the scan
# spilled (12.7k of them at the 32 MiB EXACT_GRAPH_READ_EXTERNAL_SPILL_BYTES
# threshold) - is unmeasured and 563 s is a floor, not the whole statement;
# "thirty days fits the background wall" is this arithmetic, not a measured
# thirty-day read. And the rate is a property of the statement AT its
# settings: it is coupled to DASHBOARD_TRACE_READ_MAX_THREADS and to that
# spill threshold, and must be re-measured if either moves.
_USER_GRAPH_SCAN_ROWS_PER_MS = 553


def user_graph_scan_window(
    start_date: datetime,
    end_date: datetime,
) -> tuple[datetime, datetime]:
    """Return the identity-hour window the users graph statement scans.

    ``UserTimeSeriesQueryBuilderV2.build`` bounds its scan by complete
    identity hours (the replacement key holds ``toStartOfHour(start_time)``):
    the start floored to the hour, the end rounded up to the next hour when
    it is not already on one. The gate costs exactly that window, and a test
    pins the two against each other.
    """

    scan_start = start_date.replace(minute=0, second=0, microsecond=0)
    scan_end = end_date.replace(minute=0, second=0, microsecond=0)
    if scan_end < end_date:
        scan_end += timedelta(hours=1)
    return scan_start, scan_end


def estimate_user_graph_scan_rows(
    *,
    analytics: Any,
    project_id: str,
    scan_start: datetime,
    scan_end: datetime,
    timeout_ms: int,
) -> int | None:
    """Estimate the physical spans the aggregate users graph statement scans.

    The statement's key condition is ``project_id`` plus a half-open range on
    ``toStartOfHour(start_time)`` - whole identity hours, because the
    replacement key holds the hour, not the timestamp - and that is what this
    probe costs, and nothing else: no ``end_users`` dimension join, no remap
    subquery (``EXPLAIN`` would execute one to plan around it), projections
    pinned off for the reason given on the raw probe. Measured at four workers
    on the reference tenant it costs about 100 ms at six and twelve months.
    """

    probe_settings = {
        "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
        "optimize_use_projections": 0,
    }
    query = """
        EXPLAIN ESTIMATE
        SELECT count()
        FROM spans
        WHERE project_id = toUUID(%(graph_cost_project_id)s)
          AND toStartOfHour(start_time) >= %(graph_cost_scan_start)s
          AND toStartOfHour(start_time) < %(graph_cost_scan_end)s
    """
    params = {
        "graph_cost_project_id": str(project_id),
        "graph_cost_scan_start": scan_start,
        "graph_cost_scan_end": scan_end,
    }
    try:
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=int(timeout_ms),
            settings=probe_settings,
        )
    except Exception:
        # Unknown, never small: ``user_graph_scan_fits_wall`` refuses to call
        # an uncosted read affordable.
        logger.info("user_graph_scan_estimate_unavailable", exc_info=True)
        return None
    return reduce_spans_estimate(
        getattr(result, "data", None), getattr(result, "columns", None)
    )


def user_graph_scan_fits_wall(estimated_rows: int | None, *, remaining_ms: int) -> bool:
    """Whether the users graph statement is PROVEN to complete in *remaining_ms*.

    Same contract as ``raw_graph_scan_fits_wall``, at this statement's own
    rate: an unknown estimate does not fit, and absence of proof means
    schedule, not scan.
    """

    if estimated_rows is None:
        logger.info("user_graph_scan_estimate_unknown_not_affordable")
        return False
    affordable_rows = max(0, int(remaining_ms)) * _USER_GRAPH_SCAN_ROWS_PER_MS
    if estimated_rows <= affordable_rows:
        return True
    logger.info(
        "user_graph_scan_predicted_over_wall",
        estimated_rows=int(estimated_rows),
        affordable_rows=int(affordable_rows),
        remaining_ms=int(remaining_ms),
    )
    return False


__all__ = [
    "estimate_raw_graph_scan_rows",
    "estimate_raw_log_graph_scan",
    "estimate_user_graph_scan_rows",
    "raw_graph_scan_fits_wall",
    "raw_graph_scan_window",
    "raw_log_membership_fits_wall",
    "reduce_spans_estimate",
    "user_graph_scan_fits_wall",
    "user_graph_scan_window",
]

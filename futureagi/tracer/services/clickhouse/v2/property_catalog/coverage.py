"""Does the observed index actually cover the retained source history?

The catalog is populated only by spans ingested after its tables were created,
and by whatever ``fi-observed-catalog-backfill`` has since replayed. An install
upgrading onto the observed read path therefore starts with an index that knows
nothing about its existing spans. Without this module the read endpoints would
report that empty answer as ``query_complete: true``, which is indistinguishable
from a project that genuinely has no custom attributes.

The verdict is derived from data, never from operator-managed state: no flag, no
revision, no epoch. When an operator runs the backfill the floors move back on
their own and the endpoints start reporting complete without anyone toggling
anything.

This depends on one property of the backfill: ``--source spans`` replays the
NEWEST hour first and works backwards (cmd/fi-observed-catalog-backfill,
``lastHour``/``progress.Hour.Add(-time.Hour)``). Only then does the floor reach
the oldest span at the moment the scan actually finishes, which is what makes
the check below mean "covered" rather than merely "something old was indexed".
Replaying oldest-first publishes the oldest span in page one and every verdict
here reads complete for the rest of the run. If that order ever changes, this
module stops being correct -- ``TestSpanScanReplaysNewestHourFirst`` pins it.

Both probes ignore spans that ARRIVED within ``_COVERAGE_MARGIN`` (``created_at``,
server-assigned at insert), whatever the span's own ``start_time`` says: a source
row the consumer has not caught up to yet is ingestion in flight, not a gap.
Without that, every freshly created project answered ``partial`` for the length
of the consumer lag -- a race the e2e suite hit on roughly one run in two.

Three limits are worth stating plainly. ``_COVERAGE_MARGIN`` below means the
last hour of a scan is not distinguishable from a finished one. The arrival
margin means an index that never receives rows -- a consumer that is down --
reads as covered for the first hour after spans start arriving, the same
tolerance an indexed project already had for spans above its floor. And ``--source
legacy`` pages by identity cursor rather than by time, so it has no such
ordering and this check cannot bound its progress.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

from .reader import ObservedRead, observed_table

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client = None


def _source_client():
    """One pooled, concurrency-safe handle for the source spans table.

    Constructing a client per request cost ~700 ms of the measured latency
    below; the pooled wrapper is the same one the catalog reader already shares
    across concurrent dashboard requests.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from tracer.services.clickhouse.client import ClickHouseClient

                # allow_query_settings_with_server_readonly is REQUIRED: without
                # it, ClickHouseClient drops `settings` entirely on a
                # server-readonly client, silently discarding the bounded read
                # settings below.
                _client = ClickHouseClient(
                    server_enforced_readonly=True,
                    allow_query_settings_with_server_readonly=True,
                )
    return _client


# `LIMIT 1` does not short-circuit under ClickHouse's default parallelism: the
# reader fills many granules concurrently and the limit only applies once they
# land. Measured on a 2M-row, 25-part fixture shaped like production (one tenant
# at 95% of volume), probing a project whose whole history sits below the floor:
#
#     default settings                      409,600 rows   9.38 MiB
#     max_threads=1, max_block_size=1024      2,048 rows  48.00 KiB
#
# 2,048 is two blocks — a read-unit constant, not a function of table size. The
# un-backfilled state is exactly where this probe runs on every request, so the
# 200x difference is load-bearing.
#
# These only take effect through `execute_read`, and only on a client built with
# allow_query_settings_with_server_readonly=True. `ClickHouseClient.execute()`
# sets `settings = None` unconditionally on a server-readonly client (client.py),
# so routing this probe through `execute()` silently discards every one of them —
# which is exactly what an earlier revision of this module did.
_PROBE_SETTINGS = {"max_threads": 1, "max_block_size": 1024}

# The probe reads at most a couple of blocks, but a source cluster under load can
# still be slow. Coverage is advisory metadata, not the answer itself, so it gets
# a small slice of the request budget and degrades to "unknown" rather than
# spending the wall the caller needs for the page it actually asked for.
_PROBE_WALL_MS = 1500

# The floor is the earliest span carrying a catalog-eligible attribute, but the
# probe can only ask about spans. Spans without such attributes -- a child span,
# one carrying only system fields -- legitimately predate it, so an exact
# comparison reports "partial" on a perfectly healthy install.
#
# Measured on a stack with 846 indexed projects:
#     gap <= 0            831 projects   (floor is the oldest span)
#     gap <= 1 second      15 projects   (intra-trace timestamp jitter)
#     1 second .. 1 hour    0 projects
#     > 1 hour              0 projects
#
# The distribution is bimodal with nothing in between, so a margin separates
# jitter from real absence cleanly. One hour is not arbitrary: it is the unit
# the observed backfill itself pages in (cmd/fi-observed-catalog-backfill
# advances its checkpoint one hour-bucket at a time), so it is the finest
# granularity at which "this period was indexed" is even meaningful.
#
# The cost of the margin is bounded and self-correcting: at worst it calls an
# install covered while under an hour of history is missing, and live ingestion
# closes that window on its own. An un-backfilled upgrade is missing days or
# months, orders of magnitude past this.
_COVERAGE_MARGIN = "INTERVAL 1 HOUR"

# The margin is measured on ARRIVAL, not on the span's own clock. ``created_at``
# is assigned by the server at insert (002_spans_v2.sql: DEFAULT now64) and the
# collector never sets it, whereas ``start_time`` is whatever the producer said
# and is routinely hours or days in the past for a span that landed a second
# ago. A span that arrived within the margin has been written to the source but
# may not yet have been written to the index -- that is the collector-to-
# consumer window, and it closes on its own -- so neither probe below counts it
# as a gap. A span that arrived an hour ago and is still not indexed is one.
# 024_spans_created_at_index.sql already floors on arrival for the same reason
# and gives this predicate a minmax skip index.
_SETTLED_BEFORE = f"now64(6, 'UTC') - {_COVERAGE_MARGIN}"

# A span is a gap candidate only if the catalog would have indexed anything
# from it. The collector publishes custom attributes from the three typed maps
# and attributes_extra, plus the one system attribute ``model``
# (fi-collector/pkg/observedcatalog/extract.go); a span carrying none of those
# produces no index row by design, so a project made only of such spans has
# nothing missing however old it is. Without this, the first such project in
# a workspace turned every workspace-scoped picker ``partial`` for good once
# its spans were an hour old.
#
# The map arms name the ``.size0`` subcolumn explicitly: ``length(map)`` is
# normally rewritten to it, but not on this table, whose ``mapKeys()`` bloom
# indexes block the rewrite, and the difference measured tenfold in bytes read.
# Both probes keep this predicate in WHERE behind a PREWHERE of the cheap
# columns, so the maps and attributes_extra are read only for rows that the
# time and arrival predicates already accept -- on a covered scope, none.
#
# The cost falls on a project made ONLY of such spans with no index rows: the
# index-less probe reads it end to end looking for one indexable span,
# measured at 43-77 ms per million rows at max_threads=1 (String and JSON
# attributes_extra respectively), failing closed at _PROBE_WALL_MS. That is
# the same ``partial`` verdict such a project produced before, at a latency
# cost; every attributed span stops the scan at its own block.
_INDEXABLE = (
    "("
    "attrs_string.size0 > 0 OR attrs_number.size0 > 0 OR attrs_bool.size0 > 0 "
    "OR model != '' OR toString(attributes_extra) != '{}'"
    ")"
)

# Tombstones are source rows the backfill never replays
# (cmd/fi-observed-catalog-backfill buildPage skips is_deleted = 1), so a span
# deleted before the upgrade can never be indexed and must not count as a
# gap. Without FINAL the pre-merge original still matches until parts merge;
# that turns "forever" into "until the next merge", which is the honest cost
# of not paying for FINAL on every request.
_LIVE_ROW = "is_deleted = 0"

# Floor arms per statement. Each project gets its own arm, so a workspace of a
# few hundred projects is one statement of a few hundred arms; wider scopes
# are probed in chunks rather than in one unbounded statement.
_FLOOR_ARMS_PER_STATEMENT = 200


# The public vocabulary of ``coverage_reason``. The response serializers declare
# exactly this tuple, and ``Coverage`` refuses any other value, so the wire
# contract and the verdicts cannot drift apart.
COVERAGE_REASONS = (
    "empty_scope",
    "covered",
    "floor_unavailable",
    "project_unindexed",
    "source_predates_index",
    "probe_unavailable",
)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Whether the index is known to cover everything the source retains.

    ``complete`` is deliberately three-valued through its companion ``reason``:
    True only when every project in scope was checked and none has source spans
    older than what the index holds. Any doubt -- a probe that failed, a deadline
    that ran out -- yields False, because claiming completeness we cannot support
    is the defect this module exists to prevent.
    """

    complete: bool
    reason: str
    floor: str | None = None

    def __post_init__(self):
        if self.reason not in COVERAGE_REASONS:
            raise ValueError(f"undeclared coverage reason: {self.reason!r}")

    @property
    def status(self) -> str:
        # `partial` is a member of the query_status vocabulary
        # (complete/sampled/pending/degraded/partial), declared by the response
        # serializers and by the frontend's status-pair validator
        # (utils/queryReadState.js), which renders it as the degraded caveat.
        return "complete" if self.complete else "partial"


def _floors(observed: ObservedRead, scope) -> dict[str, str]:
    """Earliest observation the index holds, per project in scope.

    ``first_seen`` is the span's own ``start_time`` (the collector copies it
    verbatim, and backfill pages by it), so index and source share one clock and
    the comparison below is meaningful.
    """
    params = ObservedRead.scope_params(scope)
    sql = f"""
SELECT toString(k.project_id) AS project_id, min(k.first_seen) AS floor
FROM {observed_table(observed.database, "observed_attribute_keys")} AS k
PREWHERE k.organization_id = %(organization_id)s
    AND k.workspace_id = %(workspace_id)s
    AND k.project_id IN %(project_ids)s
GROUP BY k.project_id
"""
    rows = observed.execute(sql, params, max(len(scope["project_ids"]), 1))
    # Keep the driver's native datetime. str() on a tz-aware value yields
    # "... +00:00", which ClickHouse refuses to parse back into DateTime64
    # ("Cannot convert string ... to type DateTime64(6, 'UTC')"), and the
    # resulting probe failure is invisible because coverage fails closed.
    return {
        str(row["project_id"]): row["floor"]
        for row in rows
        if row.get("project_id") and row.get("floor") is not None
    }


def _ch_timestamp(value) -> str:
    """Render a floor as a literal ClickHouse parses as DateTime64(6, 'UTC').

    The driver returns tz-aware datetimes whose default string form carries a
    "+00:00" offset that DateTime64 rejects.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    text = str(value).strip()
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.replace("T", " ")


def _any_project_with_settled_spans(client, settings, project_ids) -> bool:
    """Do any of these (index-less) projects hold a span that arrived before the margin?

    A project whose every span arrived within ``_COVERAGE_MARGIN`` is ingestion
    in flight: the source row exists, the index row is seconds behind. Treating
    that as a gap made every freshly created project report ``partial`` for the
    length of the consumer lag, whatever the spans' own timestamps said. A
    project holding an indexable, live span that arrived an hour ago and still
    has no index row is the un-backfilled upgrade this module exists to catch.

    One query for the whole set: ``project_id`` is the sorting-key prefix, so
    the IN-set is an index lookup and ``LIMIT 1`` stops at the first hit. The
    cheap columns sit in PREWHERE so the attribute columns behind
    ``_INDEXABLE`` are read only for rows that arrived before the margin. The
    ``created_at`` minmax index (024) prunes a fresh project's recent parts
    once it has been materialised; until then a fresh project is scanned, and
    a fresh project is by definition under an hour of ingest.
    """
    if not project_ids:
        return False
    rows, _, _ = client.execute_read(
        "SELECT 1 FROM spans "
        "PREWHERE project_id IN %(project_ids)s "
        f"AND created_at < {_SETTLED_BEFORE} "
        f"AND {_LIVE_ROW} "
        f"WHERE {_INDEXABLE} "
        "LIMIT 1",
        {"project_ids": list(project_ids)},
        timeout_ms=_PROBE_WALL_MS,
        settings=settings,
    )
    return bool(rows)


def _any_project_predating_its_floor(client, settings, floors) -> str | None:
    """Is any project holding spans materially older than its own floor?

    One statement for a chunk of projects rather than one per project. The
    earlier per-project loop was linear in scope size -- 765 ms for one
    project, 5.3 s for fifteen -- which is exactly the interactive cost this
    feature exists to remove.

    Every project is its own arm, ``project_id = p AND start_time < floor_p -
    margin``, with its floor bound as a parameter and cast to
    ``DateTime64(6, 'UTC')`` (a bare ``String - INTERVAL`` is read in the
    session time zone). That shape matters: an earlier revision located each
    row's floor with ``arrayElement(floors, indexOf(ids, project_id))``, a
    per-row bound that ClickHouse's partition and primary-key analysis cannot
    see through, so a COVERED project was read end to end on every request.
    Explicit arms are key-prefix ranges the analysis does understand: on the
    real key (project_id, observation_type, service_name, hour, ...) a covered
    project costs a handful of granules at type and service boundaries, not
    its history, whatever the other floors in the scope are.

    Arrival and eligibility gate the match (see ``_SETTLED_BEFORE`` and
    ``_INDEXABLE``); ``_LIVE_ROW`` keeps tombstones out. The cheap columns sit
    in PREWHERE so the attribute columns are read only for rows below a floor.
    """
    if not floors:
        return None
    ids = list(floors)
    for start in range(0, len(ids), _FLOOR_ARMS_PER_STATEMENT):
        chunk = ids[start : start + _FLOOR_ARMS_PER_STATEMENT]
        params = {}
        arms = []
        for i, pid in enumerate(chunk):
            params[f"p{i}"] = pid
            params[f"f{i}"] = _ch_timestamp(floors[pid])
            arms.append(
                f"(project_id = %(p{i})s "
                f"AND start_time < toDateTime64(%(f{i})s, 6, 'UTC') - {_COVERAGE_MARGIN})"
            )
        rows, _, _ = client.execute_read(
            "SELECT toString(project_id) FROM spans "
            "PREWHERE (" + " OR ".join(arms) + ") "
            f"AND created_at < {_SETTLED_BEFORE} "
            f"AND {_LIVE_ROW} "
            f"WHERE {_INDEXABLE} "
            "LIMIT 1",
            params,
            timeout_ms=_PROBE_WALL_MS,
            settings=settings,
        )
        if rows:
            return str(rows[0][0])
    return None


def observed_scope_coverage(*, scope, deadline, observed=None, client=None) -> Coverage:
    """Report whether the index covers the source history for this scope."""
    project_ids = [str(p) for p in (scope.get("project_ids") or ())]
    if not project_ids:
        # No project in scope means no rows either way; there is no history the
        # caller could be missing, so the empty answer is genuinely complete.
        return Coverage(True, "empty_scope")

    try:
        if observed is None:
            from django.conf import settings as django_settings

            observed = ObservedRead(
                catalog_database=django_settings.PROPERTY_CATALOG_DATABASE,
                deadline=deadline,
            )
        floors = _floors(observed, scope)
    except (ReadDeadlineExceeded, Exception):
        logger.warning("observed_catalog_coverage_floor_failed", exc_info=True)
        return Coverage(False, "floor_unavailable")

    try:
        if client is None:
            # The pooled wrapper, not end_user_dict_reader's module-level
            # clickhouse_connect handle: that one carries a session, and two
            # concurrent requests sharing it raise "Attempt to execute
            # concurrent queries within the same session". The probe runs on
            # every catalog read, so it is concurrent by definition.
            client = _source_client()
        probe_settings = {
            **_PROBE_SETTINGS,
            "max_execution_time": max(_PROBE_WALL_MS, 1) / 1000.0,
        }
        # A project with no index rows is only a gap if it actually has spans;
        # an empty project has nothing to index, and treating it as suspicious
        # dragged every scope containing one to "partial".
        unindexed = [pid for pid in project_ids if pid not in floors]
        if _any_project_with_settled_spans(client, probe_settings, unindexed):
            return Coverage(False, "project_unindexed")

        uncovered = _any_project_predating_its_floor(client, probe_settings, floors)
        if uncovered is not None:
            return Coverage(
                False, "source_predates_index", _ch_timestamp(floors[uncovered])
            )
    except Exception:
        # Failing closed is correct, but silence here is not: a broken probe and
        # a genuinely un-backfilled index produce the same response, so without
        # this the difference is undiagnosable from outside.
        logger.warning("observed_catalog_coverage_probe_failed", exc_info=True)
        return Coverage(False, "probe_unavailable")

    floor = min(floors.values()) if floors else None
    return Coverage(True, "covered", _ch_timestamp(floor) if floor else None)

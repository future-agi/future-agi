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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

from .reader import ObservedRead, observed_table

logger = logging.getLogger(__name__)

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
# 200x difference is load-bearing. Do not drop these settings.
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

    @property
    def status(self) -> str:
        # `partial` is a new member of the existing query_status vocabulary
        # (complete/sampled/pending/degraded/stale). The frontend's
        # getQueryReadState already maps any `query_complete: false` that is not
        # `sampled` to its degraded presentation, so this renders as a visible
        # caveat without a frontend change.
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


def _has_span_below(client, settings, project_id: str, floor) -> bool:
    """Does the source retain any span materially older than the floor?

    "Materially" is `_COVERAGE_MARGIN`; see that constant for why an exact
    comparison is wrong here.

    Partition pruning on ``toDate(start_time)`` means a covered project touches
    no partition at all and reads zero bytes; an uncovered one stops at the first
    matching block.
    """
    return bool(
        client.execute(
            "SELECT 1 FROM spans "
            "WHERE project_id = %(project_id)s "
            f"AND start_time < toDateTime64(%(floor)s, 6, 'UTC') - {_COVERAGE_MARGIN} "
            "LIMIT 1",
            {"project_id": project_id, "floor": _ch_timestamp(floor)},
            settings=settings,
        )
    )


def _has_any_span(client, settings, project_id: str) -> bool:
    """Does this project have any span at all? A primary-key point lookup."""
    return bool(
        client.execute(
            "SELECT 1 FROM spans WHERE project_id = %(project_id)s LIMIT 1",
            {"project_id": project_id},
            settings=settings,
        )
    )


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
            from tracer.services.clickhouse.client import ClickHouseClient

            client = ClickHouseClient(server_enforced_readonly=True)
        probe_settings = {
            **_PROBE_SETTINGS,
            "max_execution_time": max(_PROBE_WALL_MS, 1) / 1000.0,
        }
        for project_id in project_ids:
            floor = floors.get(project_id)
            if floor is None:
                # No index rows for this project. That is only a gap if the
                # project actually has spans -- an empty project has nothing to
                # index, and treating it as suspicious dragged every scope
                # containing one to "partial".
                if _has_any_span(client, probe_settings, project_id):
                    return Coverage(False, "project_unindexed")
                continue
            if _has_span_below(client, probe_settings, project_id, floor):
                return Coverage(
                    False, "source_predates_index", _ch_timestamp(floor)
                )
    except Exception:
        # Failing closed is correct, but silence here is not: a broken probe and
        # a genuinely un-backfilled index produce the same response, so without
        # this the difference is undiagnosable from outside.
        logger.warning("observed_catalog_coverage_probe_failed", exc_info=True)
        return Coverage(False, "probe_unavailable")

    floor = min(floors.values()) if floors else None
    return Coverage(True, "covered", _ch_timestamp(floor) if floor else None)

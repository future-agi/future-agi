"""
v2 SessionList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite, same as v2/span_list.py and v2/trace_list.py.
The v1 SessionList builder aggregates spans by trace_session_id; v2's
materialized `trace_session_id` column is queried unchanged. `V2RewriteMixin`
routes every inherited `build*` method's SQL through the v2 rewriter at one
boundary. All of this builder's queries target the migrated `spans` schema, so
only the native CH25 span-attribute query is excluded from rewriting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from tracer.selectors.filter_seed_width import (
    EMPTY_DENSITY_ESTIMATE,
    EmptyDensityEstimate,
)
from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.query_builders.filter_seed_witness import (
    ceil_hour,
    floor_hour,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.id_remap_sql import resolved_id_expr
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    _append_v2_settings,
)


class SessionListQueryBuilderV2(V2RewriteMixin, SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""

    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    def _physical_identity_fields(self) -> tuple[tuple[str, str], ...]:
        # CH25 replaces by storage hour, not the mutable microsecond timestamp.
        return (
            ("project_id", "project_id"),
            ("observation_type", "observation_type"),
            ("service_name", "service_name"),
            ("toStartOfHour(start_time)", "start_hour"),
            ("trace_id", "trace_id"),
            ("id", "id"),
        )

    def _physical_time_bounds_sql(
        self,
        *,
        start_param: str = "start_date_us",
        end_param: str = "end_date_us",
    ) -> tuple[str, str]:
        lower, _upper = super()._physical_time_bounds_sql(
            start_param=start_param, end_param=end_param
        )
        # end is exclusive; use its preceding microsecond to avoid reading an
        # extra hour when the request already ends on an hour boundary.
        return (
            f"toStartOfHour({lower})",
            f"toStartOfHour(fromUnixTimestamp64Micro(%({end_param})s - 1, 'UTC'))"
            " + INTERVAL 1 HOUR",
        )

    def build_candidate_slice_density_probe_query(
        self, *, slice_start: datetime, slice_end: datetime
    ) -> tuple[str, dict[str, Any]]:
        """Cost a candidate slice from the primary index, reading no data.

        The candidate page statement replays latest state over everything its
        window contains, so its cost tracks the ROWS inside the slice, not the
        slice's duration. Production measurement of one tenant's thirty-day
        window: the newest nine days hold 0.3% of the window's rows and the day
        before them holds four million, so a width chosen by doubling alone
        lands on dense history and reads a statement's worth of it. This probe
        is what makes that impossible - a candidate width is costed before the
        statement is issued.

        ``EXPLAIN ESTIMATE`` answers from the primary index: for each part the
        key condition selects it reports parts, marks and the rows a real
        statement WOULD read. Measured, it costs one row of 54 bytes.

        Deliberately the cheapest statement that answers the question: project
        plus a half-open ``start_time`` range on whole hours (the granularity
        of the ``toStartOfHour(start_time)`` key component), rounded OUT so the
        estimate stays an upper bound on the slice it approves. No tombstone
        predicate - ``is_deleted`` is not in the primary key, so it could not
        narrow an index estimate, and leaving it out keeps the answer honest
        about every physical version the statement would walk past. No ``IN``
        subquery either: ``EXPLAIN`` executes one to plan around it, which
        would put a real read inside a statement whose whole point is not to
        have one.

        DO NOT respell the bounds as ``toStartOfHour(start_time)``. It looks
        like the tighter form of the key component and it is the one change
        that is unsafe: ``spans`` carries aggregate projections keyed on that
        expression, and a predicate the optimizer can answer from one of them
        reports the PROJECTION's aggregate rows - orders of magnitude below
        the slice, which would approve the widest possible width. Raw bounds on
        whole hours prune identically through the key expression's
        monotonicity, plus the table's ``PARTITION BY toDate(start_time)``.

        The estimate is an upper bound twice over - whole granules, and every
        version inside them - and it is roughly a THIRD of what the candidate
        statement actually reads, because that statement inlines its CTEs and
        touches the window about four times. Both facts belong to the caller's
        budget, which is stated on this probe's scale.

        It answers a cost question only. Narrowing a slice never changes which
        rows are exact: the two-phase re-resolution decides that.
        """

        request_start, request_end = self.parse_time_range(self.filters)
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("candidate density probe must stay inside the window")
        probe_start = max(request_start, floor_hour(slice_start))
        probe_end = min(request_end, ceil_hour(slice_end))
        params = {
            **self.params,
            "candidate_density_start_us": _unix_microseconds(probe_start),
            "candidate_density_end_us": _unix_microseconds(probe_end),
        }
        return (
            f"""
            EXPLAIN ESTIMATE
            SELECT count()
            FROM {self.TABLE}
            WHERE {self.project_filter_sql()}
              AND start_time >= fromUnixTimestamp64Micro(%(candidate_density_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(candidate_density_end_us)s)
            """,
            params,
        )

    def candidate_slice_density_estimate(
        self,
        rows: Iterable[Mapping[str, Any]],
        columns: Iterable[str] | None = None,
    ) -> int | EmptyDensityEstimate | None:
        """Reduce one estimate result to a row bound, an empty, or unknown.

        The statement returns one row per part it would read - ``database``,
        ``table``, ``parts``, ``rows``, ``marks`` - and several part rows for
        this table SUM. Three answers have to stay distinct, and the reported
        ``columns`` are what separate the last one:

        * part rows are their summed ``rows``;
        * the estimate table with NO part rows is ``EMPTY_DENSITY_ESTIMATE``,
          explicitly not the integer zero: a key condition that selected no
          part and a plan that carried no readable step give the same answer
          here and call for opposite widths, so this method refuses to pick;
        * anything else is ``None``, meaning unknown.

        The caller resolves the empty reading, and for this lane it can do so
        from its own first statement: the probe over the FULL request window
        runs before any sub-slice is proposed, so a readable answer there
        proves the shape is readable, and an empty answer for a narrower key
        condition of the identical statement is then a genuine zero.
        """

        names = {str(name) for name in (columns or ())}
        if not {"table", "rows"}.issubset(names):
            return None
        counted_any = False
        estimate = 0
        for row in rows or ():
            counted_any = True
            if not isinstance(row, Mapping):
                return None
            if str(row.get("table") or "") != self.TABLE:
                return None
            counted = row.get("rows")
            if isinstance(counted, bool) or not isinstance(counted, (int, float)):
                return None
            estimate += max(0, int(counted))
        return estimate if counted_any else EMPTY_DENSITY_ESTIMATE

    # This method already emits native CH25 SQL. The generic rewrite would
    # reinterpret the compatibility alias `span_attributes_raw`.  The density
    # probe begins ``EXPLAIN``, which the rewrite boundary leaves alone.
    _v2_rewrite_exclude = frozenset(
        {"build_span_attributes_query", "build_candidate_slice_density_probe_query"}
    )

    def build_span_attributes_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "", {}
        if len(ids) > 200:
            raise ValueError("attribute session page exceeds bounded limit")

        # The bounded endpoint does not call ``build`` before page hydration.
        # Bind its exact request window here and apply it to both candidate
        # acquisition and the authoritative storage-key latest-state replay.
        attr_start_date, attr_end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "attr_session_ids": ids,
            "attr_start_date": attr_start_date,
            "attr_end_date": attr_end_date,
        }
        physical_time_scope = self._physical_time_scope_sql()
        latest_time_scope = self._latest_time_scope_sql(
            params, param_prefix="session_attr_latest_time"
        )
        ts_map_ctes = self._candidate_survivor_map_ctes(params, ids)
        resolved_ts = resolved_id_expr("latest_trace_session_id", "ts_remap")
        sql = f"""
        WITH
        {ts_map_ctes},
        candidate_root_identities AS (
            SELECT DISTINCT {self._physical_identity_select_sql()}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{physical_time_scope}
              AND (
                  trace_session_id IN %(attr_session_ids)s
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN %(attr_session_ids)s
                  )
              )
              AND (parent_span_id IS NULL OR parent_span_id = '')
        ),
        latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                argMax(start_time, _version) AS latest_start_time,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _version).1 AS latest_trace_session_id,
                argMax(tuple(attributes_extra), _version).1 AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{physical_time_scope}
              AND ({self._physical_group_by_sql()}) IN (
                  SELECT {self._physical_identity_names_sql()}
                  FROM candidate_root_identities
              )
            GROUP BY {self._physical_group_by_sql()}
        )
        SELECT
            {resolved_ts} AS session_id,
            latest_attributes_extra AS span_attributes_raw,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool
        FROM latest_roots
        LEFT JOIN ts_survivor_map AS ts_remap
            ON latest_trace_session_id = ts_remap.any_id
        WHERE latest_is_deleted = 0{latest_time_scope}
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          AND (
            (latest_attributes_extra != '{{}}' AND latest_attributes_extra != '')
            OR length(mapKeys(latest_attrs_string)) > 0
            OR length(mapKeys(latest_attrs_number)) > 0
            OR length(mapKeys(latest_attrs_bool)) > 0
          )
          AND {resolved_ts} IN %(attr_session_ids)s
        """
        return _append_v2_settings(sql), params


__all__ = ["SessionListQueryBuilderV2"]

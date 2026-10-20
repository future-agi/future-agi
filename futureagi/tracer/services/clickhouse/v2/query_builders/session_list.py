"""
v2 SessionList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite, same as v2/span_list.py and v2/trace_list.py.
The v1 SessionList builder aggregates spans by trace_session_id; v2's
materialized `trace_session_id` column is queried unchanged. `V2RewriteMixin`
routes every inherited `build*` method's SQL through the v2 rewriter at one
boundary, so only the span-attribute columns of the page hydration — which
have no legacy counterpart for the boolean map — are declared here.
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
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


class SessionListQueryBuilderV2(V2RewriteMixin, SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""

    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    # CH25 keeps booleans in their own typed map, so the page hydration carries
    # one array the legacy schema has no source for.
    PAGE_ATTRIBUTE_ARRAY_COLUMNS: tuple[tuple[str, str], ...] = (
        ("session_attribute_json_list", "span_attributes_raw"),
        ("session_attribute_string_list", "attrs_string"),
        ("session_attribute_number_list", "attrs_number"),
        ("session_attribute_bool_list", "attrs_bool"),
    )

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

    # The density probe begins ``EXPLAIN``, which the rewrite boundary leaves
    # alone. (#2801 fused the native CH25 attribute hydration into
    # ``build_page_hydration_query``, so ``build_span_attributes_query`` no
    # longer exists on this builder and is not listed.)
    _v2_rewrite_exclude = frozenset({"build_candidate_slice_density_probe_query"})

    def _page_attribute_fragments(self) -> dict[str, str]:
        """Native CH25 attribute columns for the fused page hydration.

        These carry no legacy token, so the single rewrite boundary leaves
        them untouched while it still translates the rest of the statement.
        """
        return {
            "latest": """
                argMax(tuple(attributes_extra), _version).1 AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
            """.strip(),
            "projection": """
                latest_attributes_extra AS session_attribute_json,
                latest_attrs_string AS session_attribute_string,
                latest_attrs_number AS session_attribute_number,
                latest_attrs_bool AS session_attribute_bool,
            """.strip(),
            "present": """
                (latest_attributes_extra != '{}' AND latest_attributes_extra != '')
                OR length(mapKeys(latest_attrs_string)) > 0
                OR length(mapKeys(latest_attrs_number)) > 0
                OR length(mapKeys(latest_attrs_bool)) > 0
            """.strip(),
            "arrays": """
            groupArrayIf(session_attribute_json, has_span_attributes) AS session_attribute_json_list,
            groupArrayIf(session_attribute_string, has_span_attributes) AS session_attribute_string_list,
            groupArrayIf(session_attribute_number, has_span_attributes) AS session_attribute_number_list,
            groupArrayIf(session_attribute_bool, has_span_attributes) AS session_attribute_bool_list
            """.strip(),
        }


__all__ = ["SessionListQueryBuilderV2"]

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

    def _physical_time_bounds_sql(self) -> tuple[str, str]:
        lower, _upper = super()._physical_time_bounds_sql()
        # end is exclusive; use its preceding microsecond to avoid reading an
        # extra hour when the request already ends on an hour boundary.
        return (
            f"toStartOfHour({lower})",
            "toStartOfHour(fromUnixTimestamp64Micro(%(end_date_us)s - 1, 'UTC')) + INTERVAL 1 HOUR",
        )

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

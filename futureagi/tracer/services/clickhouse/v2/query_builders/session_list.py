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

from typing import Any

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    _append_v2_settings,
)


class SessionListQueryBuilderV2(V2RewriteMixin, SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""

    # This method already emits native CH25 SQL. The generic rewrite would
    # reinterpret the compatibility alias `span_attributes_raw`.
    _v2_rewrite_exclude = frozenset({"build_span_attributes_query"})

    def build_span_attributes_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        if not session_ids:
            return "", {}

        params = {**self.params, "attr_session_ids": tuple(session_ids)}
        ts_join = remap_left_join(
            "s.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("s.trace_session_id", "ts_remap")
        sql = f"""
        SELECT
            {resolved_ts} AS session_id,
            attributes_extra AS span_attributes_raw,
            attrs_string,
            attrs_number
        FROM {self.TABLE} AS s
        {ts_join}
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND s.trace_session_id IN %(attr_session_ids)s
          AND (
            (attributes_extra != '{{}}' AND attributes_extra != '')
            OR length(mapKeys(attrs_string)) > 0
            OR length(mapKeys(attrs_number)) > 0
          )
          AND {resolved_ts} IN %(attr_session_ids)s
        LIMIT 500
        """
        return _append_v2_settings(sql), params


__all__ = ["SessionListQueryBuilderV2"]

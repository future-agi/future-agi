"""
v2 SessionList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite, same as v2/span_list.py and v2/trace_list.py.
The v1 SessionList builder aggregates spans by trace_session_id; v2's
materialized `trace_session_id` column is queried unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class SessionListQueryBuilderV2(SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_count_query()
        return rewrite_and_apply_v2_settings(sql), params

    def build_content_query(self, session_ids: List[str]) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_content_query(session_ids)
        return rewrite_and_apply_v2_settings(sql), params

    def build_span_attributes_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_span_attributes_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["SessionListQueryBuilderV2"]

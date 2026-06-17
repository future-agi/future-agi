"""
v2 VoiceCallList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. Voice calls are LLM agent calls with a specific
attribute shape (call.total_turns, call.talk_ratio, etc.) — these live in
`attrs_number` in v2 (was `span_attr_num` in v1) and are queried heavily
by the voice observability surface.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class VoiceCallListQueryBuilderV2(VoiceCallListQueryBuilder):
    """Drop-in v2 VoiceCallList builder."""

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_count_query()
        return rewrite_and_apply_v2_settings(sql), params

    def build_content_query(self, span_ids: List[str]) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_content_query(span_ids)
        return rewrite_and_apply_v2_settings(sql), params

    def build_child_spans_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_child_spans_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["VoiceCallListQueryBuilderV2"]

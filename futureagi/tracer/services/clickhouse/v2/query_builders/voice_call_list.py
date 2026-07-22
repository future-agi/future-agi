"""
v2 VoiceCallList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. Voice calls are LLM agent calls with a specific
attribute shape (call.total_turns, call.talk_ratio, etc.) — these live in
`attrs_number` in v2 (was `span_attr_num` in v1) and are queried heavily
by the voice observability surface. `V2RewriteMixin` routes every inherited
`build*` method's SQL through the v2 rewriter at one boundary.

`build_eval_query` / `build_annotation_query` read the legacy
`tracer_eval_logger` / `model_hub_score` tables (not part of the CH 25.3
migration; still carry `_peerdb_is_deleted`) and are excluded from the rewrite.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class VoiceCallListQueryBuilderV2(V2RewriteMixin, VoiceCallListQueryBuilder):
    """Drop-in v2 VoiceCallList builder."""

    _v2_rewrite_exclude = frozenset({"build_eval_query", "build_annotation_query"})


__all__ = ["VoiceCallListQueryBuilderV2"]

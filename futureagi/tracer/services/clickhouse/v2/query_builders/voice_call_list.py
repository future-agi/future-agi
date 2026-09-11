"""
v2 VoiceCallList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. Voice calls are LLM agent calls with a specific
attribute shape (call.total_turns, call.talk_ratio, etc.) — these live in
`attrs_number` in v2 (was `span_attr_num` in v1) and are queried heavily
by the voice observability surface. `V2RewriteMixin` routes every inherited
`build*` method's SQL through the v2 rewriter at one boundary.

`build_eval_query` pins the direct-write eval table; `build_annotation_query`
reads `model_hub_score`. Both are excluded from the span-schema token rewrite
because neither query targets `spans`.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallFilterBuilder,
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    _TraceListQueryBuilderV2Core,
    _TraceRootReplayV2,
)


class VoiceCallFilterBuilderV2(ClickHouseFilterBuilderV2):
    """CH25 compiler carrying only the voice-list normalized aliases."""

    VOICE_SYSTEM_METRIC_EXPRS = VoiceCallFilterBuilder.VOICE_SYSTEM_METRIC_EXPRS
    VOICE_SYSTEM_METRIC_STR_MAP = VoiceCallFilterBuilder.VOICE_SYSTEM_METRIC_STR_MAP
    VOICE_SYSTEM_METRIC_STR_EXPRS = VoiceCallFilterBuilder.VOICE_SYSTEM_METRIC_STR_EXPRS


class _VoiceTraceReplayBuilder(_TraceRootReplayV2, TraceListQueryBuilder):
    """Use the CH25 physical winner for voice membership and page content."""

    def _root_replay_content_fields(self) -> list[tuple[str, str]]:
        return [
            ("provider", "provider"),
            (
                "concat('{', arrayStringConcat(arrayMap("
                "kv -> concat(toJSONString(kv.1), ':', kv.2), "
                "arrayFilter(kv -> kv.1 != 'call_logs', "
                "JSONExtractKeysAndValuesRaw(toJSONString(attributes_extra)))), ','), '}')",
                "span_attributes",
            ),
            ("mapFilter((k, v) -> k != 'call_logs', attrs_string)", "attrs_string"),
            ("attrs_number", "attrs_number"),
            ("attrs_bool", "attrs_bool"),
        ]

    def _root_replay_content_extra_select(self) -> list[str]:
        return ["root_span_id AS span_id"]

    def _root_replay_content_join_sql(self) -> str:
        return ""


class VoiceCallListQueryBuilderV2(V2RewriteMixin, VoiceCallListQueryBuilder):
    """Drop-in v2 VoiceCallList builder."""

    _v2_rewrite_exclude = frozenset({"build_eval_query", "build_annotation_query"})
    _FILTER_BUILDER_CLS = VoiceCallFilterBuilderV2
    _NORMAL_TIME_WHERE = (
        "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
    )

    def _bounded_delegate(
        self,
        *,
        candidate_full_state: bool = False,
        public_candidate_witness: bool = False,
        trace_builder_cls: type[TraceListQueryBuilder] = _VoiceTraceReplayBuilder,
    ) -> _VoiceTraceReplayBuilder:
        # Share schema-aware logic before this voice builder's sole SQL rewrite.
        # Internal scan policy and the conversation-root predicate stay intact.
        return super()._bounded_delegate(
            candidate_full_state=candidate_full_state,
            public_candidate_witness=public_candidate_witness,
            trace_builder_cls=trace_builder_cls,
        )

    def content_root_identities_for_rows(self, rows):
        return self._bounded_delegate().content_root_identities_for_rows(rows)

    def content_root_rows_match(self, expected, actual) -> bool:
        return self._bounded_delegate().content_root_rows_match(expected, actual)

    def build_content_query(self, span_ids, *, root_identities=None):
        if not span_ids:
            return "", {}
        if not root_identities or any(len(item) != 8 for item in root_identities):
            raise ValueError(
                "v2 voice content replay requires complete root identities"
            )
        if {str(item[2]) for item in root_identities} != set(map(str, span_ids)):
            raise ValueError("v2 voice content replay identity escaped requested spans")
        return self._bounded_delegate().build_content_query(
            list(dict.fromkeys(str(item[1]) for item in root_identities)),
            root_identities=root_identities,
        )

    def _long_text_candidate_delegate(self) -> _TraceListQueryBuilderV2Core | None:
        """Reuse CH25's required long-text plan for public voice pages.

        The legacy finite witness is optional and cannot run when application
        reads disable speculative statement caps. Acquire the exhaustive raw
        text candidates first instead. The existing voice classifier still
        decides latest-state membership and the canonical conversation root.
        """
        if (
            self._bounded_internal_scan
            or self._bounded_identity_only
            or self._bounded_sampling_rate is not None
        ):
            return None
        delegate = self._bounded_delegate(
            public_candidate_witness=True,
            trace_builder_cls=_TraceListQueryBuilderV2Core,
        )
        if (
            delegate._positive_exact_end_user_seed_filter() is not None
            or delegate._positive_relational_seed_filter() is not None
            or TraceListQueryBuilder._public_scalar_candidate_seed_plan(delegate)
            is not None
            or delegate._public_long_text_candidate_seed_plan() is None
        ):
            return None
        return delegate

    def supports_filter_candidate_seed_page(self) -> bool:
        return bool(
            super().supports_filter_candidate_seed_page()
            or self._long_text_candidate_delegate() is not None
        )

    def supports_filter_anchor_probe(self) -> bool:
        # A skipped legacy anchor must not replace the required text seed
        # with an unrelated ordered-root scan in the selector's fallback.
        return bool(
            self._long_text_candidate_delegate() is None
            and super().supports_filter_anchor_probe()
        )

    def build_filter_candidate_seed_page(self, **kwargs):
        if super().supports_filter_candidate_seed_page():
            return super().build_filter_candidate_seed_page(**kwargs)
        delegate = self._long_text_candidate_delegate()
        if delegate is None:
            raise ValueError("voice candidate seed is unavailable")
        # Emit legacy tokens through the raw builder, just as the CH25 trace
        # candidate plan does. This voice builder's outer rewrite translates
        # the complete statement exactly once. Child witnesses retain all
        # history; only raw roots are scoped to the requested interval.
        return TraceListQueryBuilder.build_filter_ordered_seed_page(
            delegate,
            **kwargs,
            _positive_scalar_candidate_first=True,
            _restrict_scalar_root_population=True,
        )


__all__ = ["VoiceCallFilterBuilderV2", "VoiceCallListQueryBuilderV2"]

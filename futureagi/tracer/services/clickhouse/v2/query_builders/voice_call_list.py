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

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings

from tracer.selectors.filter_seed_width import (
    EmptyDensityEstimate,
    FilterSeedWidthPolicy,
)
from tracer.services.clickhouse.list_cursor import MAX_CURSOR_WITNESS_SLACK_HOURS
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

    def _short_text_candidate_delegate(self) -> _TraceListQueryBuilderV2Core | None:
        """Reuse CH25's short exact-string seed lane for public voice pages.

        The sibling of ``_long_text_candidate_delegate`` for the other typed
        string regime. A short literal anchors no ``LIKE`` index, but exact
        membership carries the compiler's value companion over
        ``mapValues(span_attr_str)``, which is a necessary condition and may
        therefore prune raw granules; the unbounded latest-state classifier
        still decides membership and still re-applies the conversation-root
        invariant, which also rides in the delegate's own filters and in the
        seed statement's root predicate.

        Because the delegate is a ``_TraceListQueryBuilderV2Core`` the row
        budget, the density probe and the witness envelope arrive with it. The
        hooks below forward to it so the bounded selector reaches them by its
        normal duck typing, with no voice branch in the selector.

        ``_uses_short_text_candidate_seed`` is the whole admission test: it is
        true only when no legacy scalar, long-text, end-user or relational seed
        plan applies, which is exactly the set this delegate must not displace.
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
        if not delegate._uses_short_text_candidate_seed():
            return None
        delegate.pin_filter_seed_witness_slack_hours(self._voice_witness_slack_hours())
        return delegate

    def _voice_witness_slack_hours(self) -> int:
        """The witness slack a voice seed statement runs with.

        Voice is not the trace list, and the difference is where the value
        lives. A conversation's ``call.*`` attributes are written by the span
        that CLOSES the call, so a long call's only matching witness can start
        many hours after its root - the exact case the trace list's approved
        one-hour envelope would drop from a filtered page. Voice therefore
        reads its own setting, whose default is zero: no envelope is emitted,
        the witness scan stays time-unbounded, and candidacy is byte-identical
        to what voice pages do today. Raising it is an operator decision that
        needs a per-tenant measurement of voice child-witness lag first.

        A running pagination overrides the setting with the slack its first hop
        was minted with, so an operator turning the knob cannot move the
        candidacy boundary underneath a half-published page.
        """

        pinned = getattr(self, "_pinned_voice_witness_slack_hours", None)
        if pinned is not None:
            return pinned
        return int(settings.VOICE_FILTER_TEXT_SEED_WITNESS_SLACK_HOURS)

    def pin_filter_seed_witness_slack_hours(self, hours: int | None) -> None:
        """Hold the slack on THIS builder, not on a throwaway delegate.

        ``_bounded_delegate`` constructs a fresh delegate on every hook call,
        so a pin written onto one of them is gone before the seed statement is
        built. The outer builder is the only object that survives a request, so
        it keeps the pin and stamps it onto every delegate it makes.

        ``None`` clears the pin - what a cursor minted before this field
        existed resolves to - and returns the read to the voice setting.
        """

        if hours is not None and (
            isinstance(hours, bool)
            or not isinstance(hours, int)
            or not 0 <= hours <= MAX_CURSOR_WITNESS_SLACK_HOURS
        ):
            raise ValueError("pinned witness slack is outside the supported range")
        self._pinned_voice_witness_slack_hours = hours

    def filter_seed_witness_slack_hours(self) -> int | None:
        """The slack this read used, for its continuation to carry forward.

        ``None`` whenever the short exact-string lane is not the active plan:
        those voice cursors stay byte-identical to the ones minted before this
        field existed.
        """

        delegate = self._short_text_candidate_delegate()
        return None if delegate is None else delegate.filter_seed_witness_slack_hours()

    def filter_seed_width_policy(self) -> FilterSeedWidthPolicy | None:
        delegate = self._short_text_candidate_delegate()
        return None if delegate is None else delegate.filter_seed_width_policy()

    def supports_filter_seed_density_probe(self) -> bool:
        return self._short_text_candidate_delegate() is not None

    def build_filter_seed_density_probe_query(
        self, *, slice_start: datetime, slice_end: datetime
    ) -> tuple[str, dict[str, Any]]:
        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            raise ValueError("voice seed density probe is unavailable")
        return delegate.build_filter_seed_density_probe_query(
            slice_start=slice_start, slice_end=slice_end
        )

    def filter_seed_density_probe_estimate(
        self,
        rows: Iterable[Mapping[str, Any]],
        columns: Iterable[str] | None = None,
    ) -> int | EmptyDensityEstimate | None:
        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return None
        return delegate.filter_seed_density_probe_estimate(rows, columns)

    def recommended_filter_initial_slice_width(self) -> timedelta | None:
        # The inherited relation-first rule reads the whole frozen window in one
        # slice, which is the wrong opening for a row-budgeted lane: this one
        # opens at its policy's floor and lets the budget widen it.
        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return super().recommended_filter_initial_slice_width()
        return delegate.recommended_filter_initial_slice_width()

    def recommended_filter_max_slice_width(self) -> timedelta | None:
        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return super().recommended_filter_max_slice_width()
        return delegate.recommended_filter_max_slice_width()

    def recommended_filter_classify_batch_size(self) -> int | None:
        # Voice's ten-row batch would spend twenty classifier statements on one
        # full seed slice. The seed lane's own rule sizes the batch to the
        # ordered prefix the page must prove, plus a quarter.
        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return super().recommended_filter_classify_batch_size()
        return delegate.recommended_filter_classify_batch_size()

    def recommended_filter_max_query_count(self) -> int | None:
        """Leave the density probe the headroom it is paid from.

        The inherited hook forwards the candidate-WITNESS delegate's answer,
        which reserves the whole 128-statement read contract for the sparse
        exact-value fallback. On this lane that reservation is not merely
        unused, it is disabling: the selector funds density probes from
        ``query_contract_limit - max_query_count``, so reserving the contract
        leaves an allowance of zero, every probe is refused before it reaches
        the transport, and a width the probe alone can approve is pinned at the
        unprobed four-hour cap for the whole request. Measured offline with the
        product's own budget defaults on an empty 365-day window: 0 probes and
        24 slices of 4 h covering 4 days, against 11 probes and 12 slices
        widening 4 h to 4096 h covering all 365 once the headroom exists.

        Voice does not take the witness-probe path on this shape either -
        ``prefer_filter_candidate_witness_probe_first()`` is False here - so
        the delegate's own answer is the right one, and it is the trace list's:
        no reservation, leaving the selector its 48-statement default and 48
        probes, 96 of the 128 the contract allows. Every off-lane voice shape
        keeps the inherited answer unchanged.
        """

        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return super().recommended_filter_max_query_count()
        return delegate.recommended_filter_max_query_count()

    def recommended_filter_query_timeout_ms(self) -> int | None:
        """Give one seed statement a share of the wall, not the whole wall.

        The inherited answer is the entire voice-list request deadline, so a
        statement that overruns takes the request with it: the selector's
        recovery for a slow slice is to halve it and retry, and that recovery
        cannot fire when the per-statement timeout and the request deadline are
        the same number. This lane may issue up to 24 seed statements plus
        their classifiers, which is exactly the shape that recovery exists for,
        so it takes the delegate's answer - the trace list's 9.5 s share on the
        identical filters. Off-lane voice reads keep the whole wall.
        """

        delegate = self._short_text_candidate_delegate()
        if delegate is None:
            return super().recommended_filter_query_timeout_ms()
        return delegate.recommended_filter_query_timeout_ms()

    def supports_filter_candidate_seed_page(self) -> bool:
        return bool(
            super().supports_filter_candidate_seed_page()
            or self._long_text_candidate_delegate() is not None
            or self._short_text_candidate_delegate() is not None
        )

    def supports_filter_anchor_probe(self) -> bool:
        # A skipped legacy anchor must not replace the required text seed
        # with an unrelated ordered-root scan in the selector's fallback.
        return bool(
            self._long_text_candidate_delegate() is None
            and self._short_text_candidate_delegate() is None
            and super().supports_filter_anchor_probe()
        )

    def build_filter_candidate_seed_page(self, **kwargs):
        if super().supports_filter_candidate_seed_page():
            return super().build_filter_candidate_seed_page(**kwargs)
        delegate = (
            self._long_text_candidate_delegate()
            or self._short_text_candidate_delegate()
        )
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

"""
Voice Call List Query Builder for ClickHouse.

Replaces the ``list_voice_calls()`` method in ``tracer.views.trace`` with a
multi-phase ClickHouse query strategy:

Phase 1 -- Paginated root conversation spans from the denormalized ``spans``
table (``WHERE parent_span_id IS NULL AND observation_type = 'conversation'``).

Phase 2 -- Candidate-scoped latest eval scores for those trace IDs.

Phase 3 -- Annotations from ``model_hub_score FINAL`` for those trace IDs.

Phase 4 -- Child spans for those trace IDs (for the observation_span field).

The result sets are merged in Python, with raw_log processing delegated to
the existing ``ObservabilityService.process_raw_logs()``.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.simulator_phones import SIMULATOR_PHONE_NUMBERS

# Backward-compatible public name used by existing callers and tests.
VAPI_PHONE_NUMBERS = SIMULATOR_PHONE_NUMBERS


def _unix_microseconds(value: datetime) -> int:
    """Encode a DateTime64(6) identity without driver precision loss."""

    utc_value = (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    )
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


_VOICE_ROOT_FILTER = {
    "column_id": "observation_type",
    "filter_config": {
        "col_type": "INTERNAL_ROOT_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "conversation",
    },
    # The latest-state compiler deliberately requires an unforgeable internal
    # marker before treating observation_type as a root-only trace predicate.
    # Voice calls use the same invariant as eval-task trace selection.
    "_eval_task_trace_root": True,
}


class VoiceCallFilterBuilder(ClickHouseFilterBuilder):
    """Voice-list filter compiler using the shared normalized public aliases."""


class VoiceCallListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated voice call list view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of calls per page.
        filters: Frontend filter list.
        eval_config_ids: Eval config UUID strings for Phase 2.
        remove_simulation_calls: Whether to exclude simulator calls.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    ANNOTATION_TABLE = "model_hub_score"
    _FILTER_BUILDER_CLS = VoiceCallFilterBuilder
    # Keep the legacy table's arrival-time partition guard. CH25 overrides
    # this because only ``start_time`` can prune its direct-write spans table.
    _NORMAL_TIME_WHERE = (
        "AND created_at >= %(start_date)s - INTERVAL 1 DAY "
        "AND start_time >= %(start_date)s "
        "AND start_time < %(end_date)s"
    )
    # Legacy/default behavior follows the rollout setting. The V2 subclass
    # injects the direct-write helper explicitly.
    _EVAL_LOGGER_SOURCE = staticmethod(eval_logger_source)

    def __init__(
        self,
        project_id: str,
        page_number: int = 0,
        page_size: int = settings.VOICE_LIST_DEFAULT_PAGE_SIZE,
        filters: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        remove_simulation_calls: bool = False,
        annotation_label_ids: list[str] | None = None,
        eval_filter_metadata: dict[str, Any] | None = None,
        bounded_internal_scan: bool = False,
        bounded_identity_only: bool = False,
        bounded_sampling_salt: str | None = None,
        bounded_sampling_rate: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self._eval_config_ids_known = eval_config_ids is not None
        self.eval_config_ids = eval_config_ids or []
        self.remove_simulation_calls = remove_simulation_calls
        self._annotation_label_set_known = annotation_label_ids is not None
        self.annotation_label_ids = annotation_label_ids or []
        self.eval_filter_metadata = eval_filter_metadata
        self._bounded_internal_scan = bool(bounded_internal_scan)
        self._bounded_identity_only = bool(bounded_identity_only)
        if (bounded_sampling_salt is None) != (bounded_sampling_rate is None):
            raise ValueError(
                "bounded_sampling_salt and bounded_sampling_rate must be paired"
            )
        if bounded_sampling_rate is not None and not (
            0 <= float(bounded_sampling_rate) <= 100
        ):
            raise ValueError("bounded_sampling_rate must be between 0 and 100")
        self._bounded_sampling_salt = bounded_sampling_salt
        self._bounded_sampling_rate = bounded_sampling_rate
        # ``parse_time_range([])`` derives its default end from ``utcnow``.
        # The bounded selector asks for the request range and then delegates
        # each seed/classifier query; recomputing that implicit range would
        # move it forward by microseconds and reject the selector's exact
        # slice. Pin one request window for the lifetime of this builder.
        self._bounded_request_window = BaseQueryBuilder.parse_time_range(
            self.filters, strict=True
        )

    def parse_time_range(
        self, filters: list[dict]
    ) -> tuple[datetime | None, datetime | None]:
        if filters is self.filters or filters == self.filters:
            return self._bounded_request_window
        return BaseQueryBuilder.parse_time_range(filters, strict=True)

    # ------------------------------------------------------------------
    # Bounded latest-state page selection
    # ------------------------------------------------------------------

    def _bounded_delegate(
        self,
        *,
        candidate_full_state: bool = False,
        public_candidate_witness: bool = False,
    ) -> TraceListQueryBuilder:
        """Build the trace selector used by every voice-list page.

        A voice call is a trace whose canonical live root is a conversation
        span. Reusing the trace selector keeps text/Map/JSON/eval/annotation
        filter semantics identical to the trace list while adding that root
        invariant as an internal predicate. The delegate emits legacy column
        tokens intentionally; ``VoiceCallListQueryBuilderV2`` rewrites the
        returned statement exactly once at its normal builder boundary.
        """

        request_start, request_end = self._bounded_request_window
        if candidate_full_state:
            # Continuous arrival/change seeding is separate from membership.
            # Retain explicit user time filters, but do not synthesize the
            # delegate's implicit default request window.
            delegate_filters = list(self.filters)
        else:
            delegate_filters = [
                filter_item
                for filter_item in self.filters
                if (filter_item.get("column_id") or filter_item.get("columnId"))
                not in {"created_at", "start_time"}
                or BaseQueryBuilder.is_datetime_complement_filter(filter_item)
            ]
            delegate_filters.append(
                {
                    "column_id": "start_time",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [request_start, request_end],
                    },
                }
            )
        delegate = TraceListQueryBuilder(
            project_id=self.project_id,
            project_ids=self.project_ids,
            page_number=self.page_number,
            page_size=self.page_size,
            filters=[*delegate_filters, _VOICE_ROOT_FILTER],
            eval_config_ids=(
                self.eval_config_ids if self._eval_config_ids_known else None
            ),
            annotation_label_ids=(
                self.annotation_label_ids if self._annotation_label_set_known else None
            ),
            eval_filter_metadata=self.eval_filter_metadata,
            # The voice wrapper is normally an internal trace-selector proxy.
            # Candidate-witness capability checks must retain the public list
            # planning contract, otherwise the trace builder intentionally
            # disables its interactive finite prefilter. The private root
            # marker remains in the filters and the final classifier still
            # enforces the conversation-root invariant.
            bounded_internal_scan=(
                self._bounded_internal_scan or not public_candidate_witness
            ),
            bounded_identity_only=self._bounded_identity_only,
        )
        delegate.TABLE = self.TABLE
        # Residual end-user/eval/annotation filters must use the same schema
        # compiler as this builder (v1 locally, v2 after dispatch).
        delegate._FILTER_BUILDER_CLS = self._FILTER_BUILDER_CLS
        return delegate

    def _candidate_witness_delegate(self) -> TraceListQueryBuilder:
        """Return the trace planner with public voice-list capabilities."""

        return self._bounded_delegate(public_candidate_witness=True)

    def supports_bounded_filter_scan(self) -> bool:
        """Voice pages always use finite latest-state selection."""

        return self._bounded_delegate().supports_bounded_filter_scan()

    def supports_filter_candidate_seed_page(self) -> bool:
        """Use a positive eval/annotation relation before ordered voice roots.

        Public voice pages delegate through an internal trace selector solely
        to inject the canonical conversation-root invariant.  Keep historical
        and identity-only readers on their established chronological scan;
        only an interactive request with a positive-witness relational leaf
        may use this candidate-first acquisition path. Additional ``AND``
        leaves stay authoritative in the finite candidate classifier.
        """

        return bool(
            not self._bounded_internal_scan
            and not self._bounded_identity_only
            and self._bounded_sampling_rate is None
            and self._bounded_delegate()._positive_relational_seed_filter() is not None
        )

    @staticmethod
    def filter_candidate_seed_proves_result_order() -> bool:
        """Relation membership is followed by canonical ordered-root LIMIT."""

        return True

    def build_filter_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build one exact relation-narrowed, root-ordered voice seed page."""

        if not self.supports_filter_candidate_seed_page():
            raise ValueError("voice relation candidate seed is unavailable")
        delegate = self._bounded_delegate()
        return TraceListQueryBuilder.build_filter_ordered_seed_page(
            delegate,
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            before_start_time=before_start_time,
            before_id=before_id,
            _positive_relation_candidate_first=True,
        )

    def recommended_filter_initial_slice_width(self) -> timedelta | None:
        """Read the frozen window once when an exact relation narrows roots."""

        if self.supports_filter_candidate_seed_page():
            request_start, request_end = self._bounded_request_window
            return request_end - request_start
        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return (
            self._candidate_witness_delegate().recommended_filter_initial_slice_width()
        )

    def recommended_filter_max_slice_width(self) -> timedelta | None:
        """Keep relation-first continuation on the same full-window contract."""

        if self.supports_filter_candidate_seed_page():
            return self.recommended_filter_initial_slice_width()
        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return self._candidate_witness_delegate().recommended_filter_max_slice_width()

    def allow_filter_anchor_probe_for_initial_continuation(self) -> bool:
        """Let fresh cursor pages use the trace selector's sparse proof."""

        return self._bounded_delegate().allow_filter_anchor_probe_for_initial_continuation()

    def supports_filter_anchor_probe(self) -> bool:
        """Expose only complete-population trace witnesses to voice pages.

        Voice pages delegate membership to ``TraceListQueryBuilder`` but used
        to omit its anchor capability hooks.  A long-range ``status=ERROR``
        request therefore walked the complete window in resumable time slices
        even when the indexed global error witness was empty.  Forwarding the
        global error witness lets that safe population proof terminate the
        voice page.  Ordinary temporal attribute anchors are only positive
        accelerators and cannot prove voice result order; running one before
        the canonical root seed adds a redundant ClickHouse scan before the
        normal finite classifier enforces the conversation-root invariant.
        """

        delegate = self._bounded_delegate()
        return bool(
            delegate.supports_filter_anchor_probe()
            and delegate.filter_anchor_probe_proves_complete_population()
        )

    def filter_anchor_probe_proves_complete_population(self) -> bool:
        return self._bounded_delegate().filter_anchor_probe_proves_complete_population()

    def skip_full_window_filter_anchor_probe(self) -> bool:
        return self._bounded_delegate().skip_full_window_filter_anchor_probe()

    def recommended_filter_anchor_probe_limit(self) -> int | None:
        return self._bounded_delegate().recommended_filter_anchor_probe_limit()

    def recommended_filter_anchor_probe_timeout_ms(self) -> int | None:
        return self._bounded_delegate().recommended_filter_anchor_probe_timeout_ms()

    def recommended_filter_anchor_probe_strata(self) -> int | None:
        return self._bounded_delegate().recommended_filter_anchor_probe_strata()

    def recommended_filter_anchor_probe_max_bytes_to_read(self) -> int | None:
        return (
            self._bounded_delegate().recommended_filter_anchor_probe_max_bytes_to_read()
        )

    def build_filter_anchor_probe(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return self._bounded_delegate().build_filter_anchor_probe(**kwargs)

    def bounded_filter_degraded_error_code(self) -> str | None:
        return self._bounded_delegate().bounded_filter_degraded_error_code()

    def filter_seed_proves_result_order(self) -> bool:
        return self._bounded_delegate().filter_seed_proves_result_order()

    def recommended_filter_seed_batch_size(self) -> int:
        """Acquire the same finite root batch as the delegated trace selector."""

        if self.prefer_filter_candidate_witness_probe_first():
            return (
                self._candidate_witness_delegate().recommended_filter_seed_batch_size()
            )
        return self._bounded_delegate().recommended_filter_seed_batch_size()

    def recommended_filter_cursor_seed_batch_size(self) -> int:
        """Amortize sparse voice cursor scans without widening one classifier.

        A cursor used to force every generic root seed down to ``page_size + 1``.
        For a 15-row page that meant two ClickHouse statements per 16 roots,
        even though the identity classifier is explicitly chunked and can
        safely consume a larger finite seed. Keep structured/JSON filters to
        four qualified classifier chunks and allow lighter filters eight; the
        selector still enforces its query, candidate, memory, byte and wall
        limits and checkpoints only fully classified roots.
        """

        if self.prefer_filter_candidate_witness_probe_first():
            # The candidate probe is capped to this same finite 512-root
            # envelope. Amortize selective exact values in one witness query
            # instead of repeating a ten-root classifier across many HTTP
            # continuation requests.
            return self.recommended_filter_seed_batch_size()

        delegate = self._bounded_delegate()
        classify_batch_size = int(
            self.recommended_filter_classify_batch_size()
            or settings.VOICE_FILTER_CLASSIFY_FALLBACK_BATCH_SIZE
        )
        expensive_classifier = bool(
            delegate._custom_span_attribute_filter_count()
            or delegate._unindexed_positive_micro_seed_plan() is not None
        )
        classifier_chunks = (
            settings.VOICE_FILTER_EXPENSIVE_CLASSIFIER_CHUNKS
            if expensive_classifier
            else settings.VOICE_FILTER_LIGHT_CLASSIFIER_CHUNKS
        )
        requested = max(
            self.page_size + 1,
            classify_batch_size * classifier_chunks,
        )
        return min(self.recommended_filter_seed_batch_size(), requested)

    def recommended_filter_query_timeout_ms(self) -> int | None:
        """Share the public endpoint's 9.5-second wall across required reads."""

        if not self._bounded_internal_scan and not self._bounded_identity_only:
            return settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
        return None

    def recommended_filter_classify_batch_size(self) -> int | None:
        """Retain the trace selector's filter-shape-specific safety ceiling."""

        return self._bounded_delegate().recommended_filter_classify_batch_size()

    def prefer_filter_candidate_witness_probe_first(self) -> bool:
        """Prefilter only selective long exact text on public voice lists.

        Voice lists already have tuned finite classifier batches for nested
        numeric/array paths. Keep those established plans stable and use the
        public witness only for long URL/transcript values that would otherwise
        require many sparse continuation requests.
        """

        if self._bounded_sampling_rate is not None:
            return False
        delegate = self._candidate_witness_delegate()
        return bool(
            delegate.prefer_filter_candidate_witness_probe_first()
            and any(
                delegate._candidate_witness_filter_is_selective_exact_text(item)
                for item in self.filters
                if isinstance(item, dict)
            )
        )

    def recommended_filter_max_query_count(self) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_max_query_count()

    def recommended_filter_candidate_witness_probe_strata(self) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_candidate_witness_probe_strata()

    def filter_candidate_witness_replays_global_membership(self) -> bool:
        return self._candidate_witness_delegate().filter_candidate_witness_replays_global_membership()

    def recommended_filter_candidate_witness_probe_timeout_ms(self) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_candidate_witness_probe_timeout_ms()

    def recommended_filter_candidate_witness_probe_max_bytes(self) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_candidate_witness_probe_max_bytes()

    def recommended_filter_candidate_witness_probe_total_ms(self) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_candidate_witness_probe_total_ms()

    def recommended_filter_candidate_witness_fallback_classify_batch_size(
        self,
    ) -> int | None:
        return self._candidate_witness_delegate().recommended_filter_candidate_witness_fallback_classify_batch_size()

    def build_filter_candidate_witness_probe(
        self,
        seed_rows: list[dict[str, Any]],
        *,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return self._candidate_witness_delegate().build_filter_candidate_witness_probe(
            seed_rows,
            slice_start=slice_start,
            slice_end=slice_end,
        )

    def recommended_filter_unindexed_micro_seed_width(self) -> timedelta | None:
        """Expose the delegated bounded JSON probe for provider call type."""

        return self._bounded_delegate().recommended_filter_unindexed_micro_seed_width()

    def recommended_filter_unindexed_micro_seed_strata(self) -> int | None:
        return self._bounded_delegate().recommended_filter_unindexed_micro_seed_strata()

    def build_filter_unindexed_micro_seed_page(
        self, **kwargs: Any
    ) -> tuple[str, dict[str, Any]]:
        return self._bounded_delegate().build_filter_unindexed_micro_seed_page(**kwargs)

    def filter_unindexed_micro_seed_proves_result_order(self) -> bool:
        return (
            self._bounded_delegate().filter_unindexed_micro_seed_proves_result_order()
        )

    def _filter_exact_zero_probe_plans(self) -> tuple[Any, list[Any]] | None:
        """Return scalar Map leaves accepted by the legacy temporal probe.

        The SQL remains buildable for compatibility and diagnostics, but its
        request-window child branches are not a global membership proof. The
        selector consults ``filter_exact_zero_probe_proves_global_membership``
        before execution and therefore never uses this query to terminate a
        page. Restrict construction to the original two-or-more positive
        scalar equality/IN shapes.
        """

        if (
            self._bounded_internal_scan
            or self._bounded_identity_only
            or self.project_ids is not None
            or not self.project_id
        ):
            return None

        delegate = self._bounded_delegate()
        plans, residual_filters = delegate._partition_trace_filter_plans(
            delegate._bounded_filters()
        )
        if residual_filters:
            return None
        root_plans = [plan for plan in plans if plan.scope == "root"]
        any_span_plans = [plan for plan in plans if plan.scope == "any"]
        # The only root predicate must be the private conversation invariant
        # injected by ``_bounded_delegate``.  Extra root/residual semantics stay
        # on the ordinary exact selector rather than broadening this fast path.
        if (
            len(root_plans) != 1
            or "observation_type" not in root_plans[0].seed_predicate
            or not any(
                value == "conversation" for value in root_plans[0].params.values()
            )
            or len(any_span_plans) < 2
        ):
            return None

        for plan in any_span_plans:
            raw_witness = str(plan.raw_witness_predicate or "")
            seed_predicate = str(plan.seed_predicate or "")
            if (
                plan.raw_witness_rank != 0
                or len(plan.aggregates) != 2
                or not raw_witness
                or "JSONExtract" in raw_witness
                or "mapContains(span_attr_" not in seed_predicate
            ):
                return None
        return root_plans[0], any_span_plans

    def supports_filter_exact_zero_probe(self) -> bool:
        """Whether the legacy temporal witness query can be constructed."""

        return self._filter_exact_zero_probe_plans() is not None

    @staticmethod
    def filter_exact_zero_probe_proves_global_membership() -> bool:
        """Reject the legacy request-window child witness as a negative proof.

        The voice-call datetime filter binds the canonical conversation root,
        while an attribute filter may be satisfied by any current descendant
        regardless of that descendant's timestamp.  The compatibility SQL
        below restricts every child branch to the root window, so an empty
        result cannot prove that no matching call exists.  The shared bounded
        selector therefore skips it and uses ordered roots plus all-history
        candidate classification.
        """

        return False

    @staticmethod
    def recommended_filter_exact_zero_probe_timeout_ms() -> int:
        return 1_500

    @staticmethod
    def recommended_filter_exact_zero_probe_max_bytes() -> int:
        return 256 * 1024 * 1024

    def build_filter_exact_zero_probe(self) -> tuple[str, dict[str, Any]]:
        """Build the compatibility witness for independent any-span leaves.

        The outer GROUP BY preserves sibling-span conjunction semantics, but
        the request-window child bounds mean absence is not conclusive. This
        builder is retained for backwards compatibility; the bounded selector
        skips it until a global candidate-scoped proof replaces it.
        """

        probe_plans = self._filter_exact_zero_probe_plans()
        if probe_plans is None:
            raise ValueError("exact-zero probe is unavailable for this filter shape")
        root_plan, any_span_plans = probe_plans

        request_start, request_end = self._bounded_request_window
        params: dict[str, Any] = {
            **self.params,
            "exact_zero_start_us": _unix_microseconds(request_start),
            "exact_zero_end_us": _unix_microseconds(request_end),
        }
        params.update(root_plan.params)
        branches: list[str] = [
            f"""
            SELECT trace_id, toUInt16(0) AS witness_kind
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) >= toDate(fromUnixTimestamp64Micro(%(exact_zero_start_us)s))
              AND toDate(start_time) <= toDate(fromUnixTimestamp64Micro(%(exact_zero_end_us)s))
              AND start_time >= fromUnixTimestamp64Micro(%(exact_zero_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(exact_zero_end_us)s)
            WHERE (parent_span_id IS NULL OR parent_span_id = '')
              AND ({root_plan.seed_predicate})
            GROUP BY trace_id
            """
        ]
        witness_conditions: list[str] = ["countIf(witness_kind = 0) > 0"]
        for witness_index, plan in enumerate(any_span_plans, start=1):
            params.update(plan.params)
            branches.append(
                f"""
                SELECT trace_id, toUInt16({witness_index}) AS witness_kind
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND toDate(start_time) >= toDate(fromUnixTimestamp64Micro(%(exact_zero_start_us)s))
                  AND toDate(start_time) <= toDate(fromUnixTimestamp64Micro(%(exact_zero_end_us)s))
                  AND start_time >= fromUnixTimestamp64Micro(%(exact_zero_start_us)s)
                  AND start_time < fromUnixTimestamp64Micro(%(exact_zero_end_us)s)
                WHERE {plan.raw_witness_predicate}
                GROUP BY trace_id
                """
            )
            witness_conditions.append(f"countIf(witness_kind = {witness_index}) > 0")

        query = f"""
        SELECT trace_id
        FROM (
            {" UNION ALL ".join(branches)}
        ) AS raw_filter_witnesses
        GROUP BY trace_id
        HAVING {" AND ".join(witness_conditions)}
        LIMIT 1
        """
        return query, params

    def recommended_filter_classify_read_settings(self) -> dict[str, int] | None:
        """Retain the trace classifier's structured-attribute block ceiling."""

        return self._bounded_delegate().recommended_filter_classify_read_settings()

    def use_identity_only_filter_classification(self) -> bool:
        """Hydrate presentation columns only after an interactive page is proven.

        Historical/internal selectors can request thousands of identities and
        deliberately retain their one-phase membership projection. Public voice
        pages are bounded to the trace hydration contract and benefit from the
        same lightweight candidate classifier used by the trace grid.
        """

        return bool(
            not self._bounded_internal_scan
            and not self._bounded_identity_only
            and self.page_size <= settings.VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE
        )

    def fill_bounded_cursor_page_across_slices(self) -> bool:
        """Fill one public voice page before returning a cursor checkpoint.

        Voice roots can be sparse across adjacent time slices. Publishing each
        fully classified slice separately is exact, but makes the browser pay
        one HTTP round trip for every handful of calls. Public interactive
        reads may retain those classified roots and continue within the
        selector's existing query, memory, and wall-clock limits. Historical,
        sampled, and internal scans retain the smaller chunk contract.
        """

        return bool(
            not self._bounded_internal_scan
            and not self._bounded_identity_only
            and self._bounded_sampling_rate is None
            and self.page_size <= settings.VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE
        )

    @staticmethod
    def recommended_filter_page_hydration_reserve_ms() -> int:
        return TraceListQueryBuilder.recommended_filter_page_hydration_reserve_ms()

    def build_filter_seed_page(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return self._bounded_delegate().build_filter_seed_page(**kwargs)

    def build_filter_ordered_seed_page(
        self, **kwargs: Any
    ) -> tuple[str, dict[str, Any]]:
        return self._bounded_delegate().build_filter_ordered_seed_page(**kwargs)

    def bounded_filter_seed_identity(self, row: dict[str, Any]) -> Any:
        return self._bounded_delegate().bounded_filter_seed_identity(row)

    def bounded_filter_seed_order_token(self, row: dict[str, Any]) -> Any:
        return self._bounded_delegate().bounded_filter_seed_order_token(row)

    def bounded_filter_row_identity(self, row: dict[str, Any]) -> Any:
        return self._bounded_delegate().bounded_filter_row_identity(row)

    def bounded_filter_row_order_token(self, row: dict[str, Any]) -> Any:
        return self._bounded_delegate().bounded_filter_row_order_token(row)

    def bounded_filter_page_hydration_identity(self, row: dict[str, Any]) -> Any:
        return self._bounded_delegate().bounded_filter_page_hydration_identity(row)

    def build_filter_match_query_from_seed_rows(
        self, candidate_rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        query, params = (
            self._bounded_delegate().build_filter_match_query_from_seed_rows(
                candidate_rows
            )
        )
        return self._apply_bounded_voice_constraints(query, params)

    def build_filter_identity_match_query_from_seed_rows(
        self, candidate_rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        query, params = (
            self._bounded_delegate().build_filter_identity_match_query_from_seed_rows(
                candidate_rows
            )
        )
        return self._apply_bounded_voice_constraints(query, params)

    def build_filter_page_hydration_query(
        self, candidate_rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        return self._bounded_delegate().build_filter_page_hydration_query(
            candidate_rows
        )

    def build_filter_match_query(
        self,
        candidate_ids: list[str],
        *,
        candidate_full_state: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        query, params = self._bounded_delegate(
            candidate_full_state=candidate_full_state
        ).build_filter_match_query(
            candidate_ids,
            candidate_full_state=candidate_full_state,
        )
        if not query:
            return query, params

        return self._apply_bounded_voice_constraints(query, params)

    def _apply_bounded_voice_constraints(
        self,
        query: str,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Apply voice-only simulator/sampling predicates to a finite classifier."""

        if not query:
            return query, params

        # The delegated trace classifier has already validated and de-duplicated
        # this finite identity set.  Preserve its full result envelope through
        # the voice-only wrappers: bulk queue reads intentionally grow simulator
        # batches above fifty to prove a 10k+sentinel prefix within the fixed
        # query budget.  A hard-coded outer LIMIT 50 silently turned every later
        # identity in such a batch into a false non-match.
        candidate_trace_ids = params.get("candidate_trace_ids")
        if not isinstance(candidate_trace_ids, (list, tuple)):
            raise ValueError("voice classifier requires finite candidate identities")
        bounded_voice_limit = len(candidate_trace_ids)
        if not 1 <= bounded_voice_limit <= 1_000:
            raise ValueError("voice classifier candidate limit is invalid")

        if self.remove_simulation_calls:
            # Simulator exclusion used to happen after pagination in Python.
            # That returned short/incorrect pages whenever a simulator occupied
            # a page slot. Keep the expensive raw-log JSON work candidate-scoped:
            # only the finite trace IDs in this classifier batch are inspected,
            # and every physical root is reduced to its latest version before
            # the predicate.
            params = {**params, "simulator_phone_numbers": tuple(VAPI_PHONE_NUMBERS)}
            simulator_phone = """
            coalesce(
                nullIf(JSONExtractString(
                    latest_raw_log_json, 'customer', 'number'
                ), ''),
                nullIf(JSONExtractString(
                    latest_raw_log_text, 'customer', 'number'
                ), ''),
                nullIf(JSONExtractString(
                    latest_span_attr_str['raw_log'], 'customer', 'number'
                ), '')
            )
        """
            retell_phone = """
            coalesce(
                nullIf(JSONExtractString(
                    latest_raw_log_json, 'from_number'
                ), ''),
                nullIf(JSONExtractString(
                    latest_raw_log_text, 'from_number'
                ), ''),
                nullIf(JSONExtractString(
                    latest_span_attr_str['raw_log'], 'from_number'
                ), '')
            )
        """
            simulator_time_scope = (
                """
                  AND start_time >= %(candidate_start_date)s
                  AND start_time < %(candidate_end_date)s
            """
                if "candidate_start_date" in params
                else ""
            )
            query = f"""
        SELECT *
        FROM ({query}) AS bounded_voice_candidates
        WHERE trace_id NOT IN (
            SELECT grouped_trace_id
            FROM (
                SELECT
                    trace_id AS grouped_trace_id,
                    id AS grouped_id,
                    start_time AS grouped_start_time,
                    argMax(tuple(parent_span_id), _peerdb_version).1
                        AS latest_parent_span_id,
                    argMax(observation_type, _peerdb_version)
                        AS latest_observation_type,
                    argMax(provider, _peerdb_version) AS latest_provider,
                    argMax(tuple(JSONExtractRaw(
                        span_attributes_raw, 'raw_log'
                    )), _peerdb_version).1 AS latest_raw_log_json,
                    argMax(tuple(JSONExtractString(
                        span_attributes_raw, 'raw_log'
                    )), _peerdb_version).1 AS latest_raw_log_text,
                    argMax(span_attr_str, _peerdb_version)
                        AS latest_span_attr_str,
                    argMax(_peerdb_is_deleted, _peerdb_version)
                        AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND trace_id IN %(candidate_trace_ids)s
                  {simulator_time_scope}
                GROUP BY project_id, trace_id, id, start_time
            ) AS latest_voice_roots
            WHERE latest_is_deleted = 0
              AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
              AND latest_observation_type = 'conversation'
              AND (
                    (
                        lowerUTF8(latest_provider) = 'vapi'
                        AND ({simulator_phone}) IN %(simulator_phone_numbers)s
                    )
                    OR (
                        lowerUTF8(latest_provider) = 'retell'
                        AND ({retell_phone}) IN %(simulator_phone_numbers)s
                    )
              )
        )
        ORDER BY start_time DESC, trace_id DESC
        LIMIT {bounded_voice_limit}
        """

        if self._bounded_sampling_rate is not None:
            # Historical voice tasks expose the canonical root span ID, not
            # the trace ID. Apply their deterministic hash only after the
            # finite candidate classifier has resolved that root. Seeding on
            # trace IDs remains an unsampled safe superset, so sparse samples
            # continue across adjacent batches instead of returning short.
            params = {
                **params,
                "bounded_sampling_salt": str(self._bounded_sampling_salt),
                "bounded_sampling_rate": float(self._bounded_sampling_rate),
            }
            query = f"""
            SELECT *
            FROM ({query}) AS bounded_sampled_voice_candidates
            WHERE modulo(
                cityHash64(
                    %(bounded_sampling_salt)s,
                    toString(root_span_id)
                ),
                100
            ) < %(bounded_sampling_rate)s
            ORDER BY start_time DESC, trace_id DESC
            LIMIT {bounded_voice_limit}
            """
        return query, params

    # ------------------------------------------------------------------
    # Phase 1: Paginated root conversation spans
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated voice call data."""
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        offset = self.page_number * self.page_size
        self.params["limit"] = (
            self.page_size + 1
        )  # fetch one extra for has_more detection
        self.params["offset"] = offset

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        simulation_filter = self._build_simulation_filter()

        # Light columns only — heavy span_attributes_raw fetched via
        # build_content_query() after pagination to avoid CH OOM.
        query = f"""
        SELECT
            trace_id,
            id AS span_id,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            provider
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          {self._NORMAL_TIME_WHERE}
          {filter_fragment}
          {simulation_filter}
        ORDER BY start_time DESC
        LIMIT 1 BY trace_id
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_id_query(
        self,
        *,
        created_at_floor: datetime | None = None,
        created_at_ceiling: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Filtered conversation-root span ids only — same predicate/window as
        build(), no pagination/order. Lets the eval resolver select the same
        voice calls this list endpoint returns.

        ``created_at_floor``/``created_at_ceiling`` (continuous eval tasks only):
        window the scan by CH arrival (``created_at``), not event time, so calls
        whose root span reached CH long after they started (Vapi emits at
        end-of-call) are still picked up. ``None`` keeps the ``start_time`` window
        used by the UI list and historical tasks.
        """
        start_date, end_date = self.parse_time_range(self.filters)
        if created_at_floor is not None:
            self.params["created_at_floor"] = created_at_floor
            time_where = "AND created_at >= %(created_at_floor)s"
            if created_at_ceiling is not None:
                self.params["created_at_ceiling"] = created_at_ceiling
                time_where += " AND created_at < %(created_at_ceiling)s"
        else:
            time_where = self._NORMAL_TIME_WHERE
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        query = f"""
        SELECT id
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          {time_where}
          {filter_fragment}
        ORDER BY start_time DESC
        LIMIT 1 BY trace_id
        """
        return query, self.params

    def build_content_query(
        self,
        span_ids: list[str],
        *,
        root_identities: list[tuple[str, str, str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate only the selected physical roots at their latest state.

        A bare span ID is not globally unique and ``FINAL`` collapses on the
        table sorting key rather than the application's physical identity.
        The page selector supplies ``(project, trace, id, start_time)`` tuples;
        use exact epoch-microsecond tuples plus partition dates, then resolve
        versions with ``argMax``. ``call_logs`` is removed inside ClickHouse so
        the list never transfers the ~900 KiB detail-only payload.
        """

        if not span_ids:
            return "", {}
        identities = tuple(
            dict.fromkeys(
                (
                    str(project_id),
                    str(trace_id),
                    str(span_id),
                    _unix_microseconds(start_time),
                )
                for project_id, trace_id, span_id, start_time in (root_identities or [])
                if project_id
                and trace_id
                and span_id
                and isinstance(start_time, datetime)
            )
        )
        if root_identities is not None and len(identities) != len(root_identities):
            raise ValueError("voice root identities are incomplete")
        if len(identities) > settings.VOICE_CONTENT_MAX_BATCH_SIZE:
            raise ValueError("voice content batch exceeds bounded limit")

        params = {**self.params, "content_span_ids": tuple(dict.fromkeys(span_ids))}
        identity_fragment = ""
        if identities:
            params["content_root_identities"] = identities
            params["content_trace_ids"] = tuple(
                dict.fromkeys(trace_id for _, trace_id, _, _ in identities)
            )
            params["content_root_dates"] = tuple(
                dict.fromkeys(
                    start_time.date()
                    for _, _, _, start_time in (root_identities or [])
                    if isinstance(start_time, datetime)
                )
            )
            identity_fragment = """
              AND trace_id IN %(content_trace_ids)s
            WHERE toDate(start_time) IN %(content_root_dates)s
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(start_time)
              ) IN %(content_root_identities)s
            """

        query = f"""
        SELECT
            toString(grouped_project_id) AS project_id,
            grouped_trace_id AS trace_id,
            grouped_id AS span_id,
            grouped_start_time AS start_time,
            latest_provider AS provider,
            concat(
                '{{',
                arrayStringConcat(
                    arrayMap(
                        kv -> concat('\"', kv.1, '\":', kv.2),
                        arrayFilter(
                            kv -> kv.1 != 'call_logs',
                            JSONExtractKeysAndValuesRaw(
                                latest_span_attributes_raw
                            )
                        )
                    ),
                    ','
                ),
                '}}'
            ) AS span_attributes,
            mapFilter(
                (k, v) -> k != 'call_logs', latest_span_attr_str
            ) AS attrs_string,
            latest_span_attr_num AS attrs_number,
            latest_span_attr_bool AS attrs_bool
        FROM (
            SELECT
                project_id AS grouped_project_id,
                trace_id AS grouped_trace_id,
                id AS grouped_id,
                start_time AS grouped_start_time,
                argMax(provider, _peerdb_version) AS latest_provider,
                argMax(tuple(span_attributes_raw), _peerdb_version).1
                    AS latest_span_attributes_raw,
                argMax(span_attr_str, _peerdb_version) AS latest_span_attr_str,
                argMax(span_attr_num, _peerdb_version) AS latest_span_attr_num,
                argMax(span_attr_bool, _peerdb_version) AS latest_span_attr_bool,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE id IN %(content_span_ids)s
              AND {self.project_filter_sql()}
              {identity_fragment}
            GROUP BY project_id, trace_id, id, start_time
        ) AS latest_voice_content
        WHERE latest_is_deleted = 0
        ORDER BY grouped_start_time DESC, grouped_id DESC
        LIMIT {settings.VOICE_CONTENT_MAX_BATCH_SIZE}
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching voice calls."""
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(self.filters)
        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        simulation_filter = self._build_simulation_filter()

        query = f"""
        SELECT uniqExact(trace_id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          {self._NORMAL_TIME_WHERE}
          {filter_fragment}
          {simulation_filter}
        """
        return query, params

    def _build_simulation_filter(self) -> str:
        """Build SQL fragment to exclude simulator calls.

        The legacy broad Phase-1 query still keeps this fragment empty. The
        bounded classifier applies simulator exclusion only to its <=50 trace
        candidates, and Python retains a final defensive check after hydration.
        """
        return ""

    # ------------------------------------------------------------------
    # Python-side simulation filter (used after Phase 1b)
    # ------------------------------------------------------------------

    @staticmethod
    def is_simulator_call(span_attrs: dict, provider: str) -> bool:
        """Return True if the call comes from a known simulator phone number.

        Called after Phase 1b as a defensive parity check.
        """
        raw_log = span_attrs.get("raw_log") or {}
        if provider == "vapi":
            phone = (raw_log.get("customer") or {}).get("number", "")
        elif provider == "retell":
            phone = raw_log.get("from_number", "")
        else:
            return False
        return phone in VAPI_PHONE_NUMBERS

    # ------------------------------------------------------------------
    # Phase 2: Eval scores
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build eval-scores query for a page of trace IDs."""
        if not trace_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        table, _ = self._EVAL_LOGGER_SOURCE()
        is_v2 = table.endswith("_v2")
        version = "_version" if is_v2 else "_peerdb_version"
        status_aggregate = "'completed'" if is_v2 else f"argMax(status, {version})"
        skipped_reason_aggregate = (
            "CAST(NULL AS Nullable(String))"
            if is_v2
            else f"argMax(tuple(skipped_reason), {version}).1"
        )
        deleted_aggregate = (
            f"argMax(is_deleted, {version})"
            if is_v2
            else (
                f"greatest(argMax(_peerdb_is_deleted, {version}), "
                f"coalesce(argMax(deleted, {version}), 0))"
            )
        )

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score nor
        # masquerades as a real value. The per-status counts let the shared
        # pivot pick one cell state by the precedence
        # completed > errored > skipped > running > pending; ``success_count``
        # excludes non-terminal/skipped/errored rows via ``status NOT IN (...)``
        # (a bare ``error = 0`` guard also matches pending/running/skipped
        # rows). NOT-IN keeps legacy rows whose mirrored ``status`` is
        # empty/NULL counted as completed.
        # Column order must match what ``pivot_eval_results`` expects:
        # trace_id, eval_config_id, avg_score, pass_rate, success_count,
        # error_count, eval_count, str_lists — new per-status columns are
        # appended after ``str_lists`` so the pivot's positional fallbacks hold.
        query = f"""
        SELECT
            latest_trace_id AS trace_id,
            toString(latest_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN,
            -- which json.dumps(allow_nan=False) rejects. NULL serializes as null.
            ifNotFinite(avgIf(
                latest_output_float,
                latest_error = 0 AND ifNull(latest_output_str, '') != 'ERROR' AND latest_status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS avg_score,
            ifNotFinite(avgIf(
                CASE WHEN latest_output_bool = 1 THEN 100.0 ELSE 0.0 END,
                latest_error = 0 AND ifNull(latest_output_str, '') != 'ERROR' AND latest_status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS pass_rate,
            countIf(
                latest_error = 0 AND ifNull(latest_output_str, '') != 'ERROR' AND latest_status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS success_count,
            countIf(
                latest_error = 1 OR ifNull(latest_output_str, '') = 'ERROR' OR latest_status = 'errored'
            ) AS error_count,
            count() AS eval_count,
            groupArrayIf(
                latest_output_str_list,
                latest_error = 0 AND ifNull(latest_output_str, '') != 'ERROR' AND latest_status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS str_lists,
            countIf(latest_status = 'skipped') AS skipped_count,
            countIf(latest_status = 'running') AS running_count,
            countIf(latest_status = 'pending') AS pending_count,
            anyIf(latest_skipped_reason, latest_status = 'skipped') AS skipped_reason
        FROM (
            SELECT
                id AS grouped_eval_id,
                argMax(trace_id, {version}) AS latest_trace_id,
                argMax(custom_eval_config_id, {version}) AS latest_eval_config_id,
                argMax(tuple(output_float), {version}).1 AS latest_output_float,
                argMax(tuple(output_bool), {version}).1 AS latest_output_bool,
                argMax(tuple(output_str), {version}).1 AS latest_output_str,
                argMax(output_str_list, {version}) AS latest_output_str_list,
                argMax(error, {version}) AS latest_error,
                {status_aggregate} AS latest_status,
                {skipped_reason_aggregate} AS latest_skipped_reason,
                {deleted_aggregate} AS latest_is_deleted
            FROM {table}
            PREWHERE trace_id IN %(trace_ids)s
              AND custom_eval_config_id IN %(eval_config_ids)s
            GROUP BY id
        ) AS latest_voice_evals
        WHERE latest_is_deleted = 0
        GROUP BY latest_trace_id, latest_eval_config_id
        ORDER BY latest_trace_id ASC, latest_eval_config_id ASC
        LIMIT 5001
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations
    # ------------------------------------------------------------------

    def build_annotation_query(
        self,
        trace_ids: list[str],
        annotation_label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build annotation query for a page of trace IDs.

        Returns per-annotator rows so the view can build the structured
        annotation format expected by the frontend:
        ``{score: N, annotators: {userId: {userId, userName, score}}}``
        """
        if not trace_ids or not annotation_label_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "label_ids": tuple(annotation_label_ids),
        }

        query = f"""
        SELECT
            if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
            ) AS trace_id,
            toString(s.label_id) AS label_id,
            toString(s.annotator_id) AS user_id,
            s.value
        FROM {self.ANNOTATION_TABLE} AS s FINAL
        LEFT JOIN {self.TABLE} AS sp
          ON sp.id = s.observation_span_id
         AND sp._peerdb_is_deleted = 0
        WHERE s._peerdb_is_deleted = 0
          AND s.deleted = false
          AND if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
              ) IN %(trace_ids)s
          AND s.label_id IN %(label_ids)s
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 4: Child spans per trace
    # ------------------------------------------------------------------

    def build_child_spans_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build query to fetch child spans for voice call traces."""
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            "project_id": self.project_id,
            "trace_ids": tuple(trace_ids),
        }

        query = f"""
        SELECT
            id,
            trace_id,
            name,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            model,
            provider,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost,
            input,
            output,
            parent_span_id,
            span_attributes_raw,
            span_attr_str,
            span_attr_num,
            span_attr_bool,
            metadata_map,
            status_message,
            tags
        FROM {self.TABLE}
        WHERE project_id = %(project_id)s
          AND is_deleted = 0
          AND trace_id IN %(trace_ids)s
          AND parent_span_id IS NOT NULL
        ORDER BY start_time ASC
        """
        return query, params

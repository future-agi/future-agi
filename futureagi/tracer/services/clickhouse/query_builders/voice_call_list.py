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

from datetime import UTC, datetime
from typing import Any

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder

# Hardcoded simulator phone numbers (must match FilterEngine)
VAPI_PHONE_NUMBERS = [
    "+18568806998",
    "+17755715840",
    "+13463424590",
    "+12175683677",
    "+12175696753",
    "+12175683493",
    "+12175681887",
    "+12176018447",
    "+12176018280",
    "+12175696862",
    "+19168660414",
    "+19163473349",
    "+18563161617",
    "+13463619738",
    "+19847339395",
]


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


def _raw_log_has_key(key: str) -> str:
    """Return a code-owned predicate for a key in either raw-log encoding."""

    return (
        "(JSONHas(span_attributes_raw, 'raw_log', '"
        f"{key}') OR "
        "JSONHas(JSONExtractString(span_attributes_raw, 'raw_log'), '"
        f"{key}') OR "
        "JSONHas(span_attr_str['raw_log'], '"
        f"{key}'))"
    )


def _raw_log_number(path: tuple[str, ...]) -> str:
    """Read one provider number from object, encoded-string, or Map raw_log."""

    quoted_path = ", ".join(f"'{part}'" for part in path)
    nested_path = f"'raw_log', {quoted_path}"
    encoded_raw_log = "JSONExtractString(span_attributes_raw, 'raw_log')"
    map_raw_log = "span_attr_str['raw_log']"
    return (
        "coalesce("
        f"if(JSONHas(span_attributes_raw, {nested_path}), "
        f"JSONExtractFloat(span_attributes_raw, {nested_path}), null), "
        f"if(JSONHas({encoded_raw_log}, {quoted_path}), "
        f"JSONExtractFloat({encoded_raw_log}, {quoted_path}), null), "
        f"if(JSONHas({map_raw_log}, {quoted_path}), "
        f"JSONExtractFloat({map_raw_log}, {quoted_path}), null)"
        ")"
    )


def _raw_log_string(path: tuple[str, ...]) -> str:
    """Read one provider string from object or encoded-string raw_log."""

    quoted_path = ", ".join(f"'{part}'" for part in path)
    nested_path = f"'raw_log', {quoted_path}"
    encoded_raw_log = "JSONExtractString(span_attributes_raw, 'raw_log')"
    map_raw_log = "span_attr_str['raw_log']"
    return (
        "coalesce("
        f"nullIf(JSONExtractString(span_attributes_raw, {nested_path}), ''), "
        f"nullIf(JSONExtractString({encoded_raw_log}, {quoted_path}), ''), "
        f"nullIf(JSONExtractString({map_raw_log}, {quoted_path}), '')"
        ")"
    )


_RAW_RETELL_COST_CENTS = _raw_log_number(("call_cost", "combined_cost"))
_RAW_VAPI_COST_DOLLARS = _raw_log_number(("cost",))
_RAW_ELEVEN_LABS_COST_CENTS = _raw_log_number(("metadata", "cost"))
_RAW_PRICE_DOLLARS = _raw_log_number(("price",))
_VOICE_PROVIDER = "lowerUTF8(toString(provider))"
_VOICE_GEN_AI_SYSTEM = (
    "lowerUTF8(toString(if(mapContains(span_attr_str, 'gen_ai.system'), "
    "span_attr_str['gen_ai.system'], '')))"
)
_VOICE_RESOLVED_PROVIDER = (
    "multiIf("
    f"{_VOICE_PROVIDER} IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio'), "
    f"{_VOICE_PROVIDER}, "
    f"{_VOICE_GEN_AI_SYSTEM} IN "
    "('vapi', 'retell', 'eleven_labs', 'bland', 'twilio'), "
    f"{_VOICE_GEN_AI_SYSTEM}, 'vapi')"
)
_VOICE_COST_CENTS_EXPR = (
    "coalesce("
    f"({_RAW_RETELL_COST_CENTS}), "
    f"nullIf(({_RAW_VAPI_COST_DOLLARS}), 0) * 100, "
    f"({_RAW_ELEVEN_LABS_COST_CENTS}), "
    f"abs(({_RAW_PRICE_DOLLARS})) * 100, "
    "if(mapContains(span_attr_num, 'combined_cost'), "
    "span_attr_num['combined_cost'], null), "
    "if(mapContains(span_attr_num, 'cost_breakdown.total'), "
    "span_attr_num['cost_breakdown.total'] * 100, null), "
    "multiIf("
    f"{_VOICE_PROVIDER} IN ('retell', 'eleven_labs'), toFloat64(cost), "
    f"{_VOICE_PROVIDER} IN ('vapi', 'bland', 'twilio'), "
    "abs(toFloat64(cost)) * 100, "
    "CAST(NULL AS Nullable(Float64))))"
)

_VOICE_RAW_STATUS = (
    "if(mapContains(span_attr_str, 'call.status'), "
    "nullIf(span_attr_str['call.status'], ''), null)"
)
_VOICE_CANONICAL_RAW_STATUS = (
    "multiIf("
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})) IN "
    "('ended', 'done', 'complete', 'completed', 'success', 'succeeded', 'ok'), "
    "'completed', "
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})) IN "
    "('in-progress', 'in_progress', 'ongoing', 'started', 'ringing', "
    "'queued', 'pending'), 'in-progress', "
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})) IN "
    "('failed', 'failure', 'error', 'errored'), 'failed', "
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})) IN "
    "('dropped', 'cancelled', 'canceled', 'aborted', 'hung-up', 'hung_up'), "
    "'dropped', "
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})) IN "
    "('not-connected', 'not_connected', 'no-answer', 'no_answer', "
    "'unanswered', 'busy'), 'not-connected', "
    f"lowerUTF8(toString({_VOICE_RAW_STATUS})))"
)
_VOICE_HAS_RAW_LOG = (
    "(JSONHas(span_attributes_raw, 'raw_log') OR mapContains(span_attr_str, 'raw_log'))"
)
_VOICE_HAS_NONEMPTY_RAW_LOG = (
    "((JSONHas(span_attributes_raw, 'raw_log') AND "
    "JSONExtractRaw(span_attributes_raw, 'raw_log') NOT IN "
    "('', '{}', 'null', '\"\"', '\"{}\"', '\"null\"')) OR "
    "(mapContains(span_attr_str, 'raw_log') AND "
    "span_attr_str['raw_log'] NOT IN ('', '{}', 'null')))"
)
_VOICE_IS_RETELL = _raw_log_has_key("call_status")
_VOICE_IS_ELEVEN_LABS = _raw_log_has_key("conversation_id")
_VOICE_IS_BLAND = _raw_log_has_key("call_length")
_VOICE_IS_TWILIO = _raw_log_has_key("sid")
_VOICE_IS_VAPI = f"({_raw_log_has_key('startedAt')} OR {_raw_log_has_key('createdAt')})"
_VOICE_OTLP_CALL_ID = (
    "nullIf(JSONExtractString(span_attributes_raw, 'metadata', "
    "'call_execution_id'), '')"
)
_VOICE_CALL_ID_EXPR = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, {_VOICE_OTLP_CALL_ID}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', {_raw_log_string(('call_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'eleven_labs', "
    f"{_raw_log_string(('conversation_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'bland', {_raw_log_string(('call_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'twilio', {_raw_log_string(('sid',))}, "
    f"{_raw_log_string(('id',))})"
)
_VOICE_CALL_STATUS_EXPR = (
    "multiIf("
    f"NOT {_VOICE_HAS_RAW_LOG}, "
    f"coalesce({_VOICE_CANONICAL_RAW_STATUS}, 'completed'), "
    f"({_VOICE_IS_RETELL} OR {_VOICE_IS_VAPI} "
    f"OR {_VOICE_PROVIDER} IN ('retell', 'vapi')), "
    f"if({_VOICE_CANONICAL_RAW_STATUS} = 'completed', "
    f"'completed', 'in-progress'), "
    f"({_VOICE_IS_ELEVEN_LABS} OR {_VOICE_PROVIDER} = 'eleven_labs'), "
    f"{_VOICE_CANONICAL_RAW_STATUS}, "
    f"({_VOICE_IS_BLAND} OR {_VOICE_IS_TWILIO} "
    f"OR {_VOICE_PROVIDER} IN ('bland', 'twilio')), "
    f"{_VOICE_CANONICAL_RAW_STATUS}, "
    f"{_VOICE_CANONICAL_RAW_STATUS})"
)

# Public, code-owned expressions used by the voice list and its filter-value
# suggestions.  They intentionally retain legacy column tokens here; the CH25
# compiler rewrites those tokens once at its normal schema boundary.
VOICE_CALL_STATUS_FILTER_EXPRESSION = _VOICE_CALL_STATUS_EXPR
VOICE_COST_CENTS_FILTER_EXPRESSION = _VOICE_COST_CENTS_EXPR
VOICE_CALL_ID_FILTER_EXPRESSION = _VOICE_CALL_ID_EXPR


class VoiceCallFilterBuilder(ClickHouseFilterBuilder):
    """Voice-list-only aliases matching the normalized list response."""

    VOICE_SYSTEM_METRIC_EXPRS = {
        **ClickHouseFilterBuilder.VOICE_SYSTEM_METRIC_EXPRS,
        "cost_cents": VOICE_COST_CENTS_FILTER_EXPRESSION,
    }
    VOICE_SYSTEM_METRIC_STR_MAP = {
        key: value
        for key, value in ClickHouseFilterBuilder.VOICE_SYSTEM_METRIC_STR_MAP.items()
        if key != "call_status"
    }
    VOICE_SYSTEM_METRIC_STR_EXPRS = {
        **ClickHouseFilterBuilder.VOICE_SYSTEM_METRIC_STR_EXPRS,
        "call_status": VOICE_CALL_STATUS_FILTER_EXPRESSION,
        "call_id": VOICE_CALL_ID_FILTER_EXPRESSION,
    }


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
        page_size: int = 10,
        filters: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        remove_simulation_calls: bool = False,
        annotation_label_ids: list[str] | None = None,
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
        self.eval_config_ids = eval_config_ids or []
        self.remove_simulation_calls = remove_simulation_calls
        self.annotation_label_ids = annotation_label_ids or []
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
        self._bounded_request_window = BaseQueryBuilder.parse_time_range(self.filters)

    def parse_time_range(
        self, filters: list[dict]
    ) -> tuple[datetime | None, datetime | None]:
        if filters is self.filters or filters == self.filters:
            return self._bounded_request_window
        return BaseQueryBuilder.parse_time_range(filters)

    # ------------------------------------------------------------------
    # Bounded latest-state page selection
    # ------------------------------------------------------------------

    def _bounded_delegate(
        self, *, candidate_full_state: bool = False
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
            eval_config_ids=self.eval_config_ids,
            annotation_label_ids=self.annotation_label_ids,
            bounded_internal_scan=True,
            bounded_identity_only=self._bounded_identity_only,
        )
        delegate.TABLE = self.TABLE
        # Residual end-user/eval/annotation filters must use the same schema
        # compiler as this builder (v1 locally, v2 after dispatch).
        delegate._FILTER_BUILDER_CLS = self._FILTER_BUILDER_CLS
        return delegate

    def supports_bounded_filter_scan(self) -> bool:
        """Voice pages always use finite latest-state selection."""

        return self._bounded_delegate().supports_bounded_filter_scan()

    def bounded_filter_degraded_error_code(self) -> str | None:
        return self._bounded_delegate().bounded_filter_degraded_error_code()

    def filter_seed_proves_result_order(self) -> bool:
        return self._bounded_delegate().filter_seed_proves_result_order()

    def recommended_filter_seed_batch_size(self) -> int:
        """Acquire the same finite root batch as the delegated trace selector."""

        return self._bounded_delegate().recommended_filter_seed_batch_size()

    def recommended_filter_classify_batch_size(self) -> int | None:
        """Retain the trace selector's filter-shape-specific safety ceiling."""

        return self._bounded_delegate().recommended_filter_classify_batch_size()

    def _filter_exact_zero_probe_plans(self) -> tuple[Any, list[Any]] | None:
        """Return scalar Map leaves eligible for an exact negative proof.

        The probe reads physical raw witnesses, which are a superset of
        latest-live membership.  Therefore an exhausted intersection proves
        exact zero, while any returned trace merely falls back to the normal
        latest-state selector.  Restrict the optimization to the two-or-more
        positive scalar equality/IN shapes whose compiler explicitly exposes
        that exhaustive raw witness.
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
        """Whether this public voice page can prove a zero-result conjunction."""

        return self._filter_exact_zero_probe_plans() is not None

    @staticmethod
    def recommended_filter_exact_zero_probe_timeout_ms() -> int:
        return 1_500

    @staticmethod
    def recommended_filter_exact_zero_probe_max_bytes() -> int:
        return 256 * 1024 * 1024

    def build_filter_exact_zero_probe(self) -> tuple[str, dict[str, Any]]:
        """Build an exact-zero proof for independent any-span Map leaves.

        Each UNION branch is an exhaustive *raw* witness for one positive
        latest-state filter.  The outer GROUP BY intersects those witnesses by
        trace, preserving the documented semantics that separate sibling spans
        may satisfy separate leaves.  Returning no row is conclusive because a
        latest-live match must have a corresponding physical raw witness.
        Returning a row is intentionally inconclusive (it may be stale or
        deleted) and the caller continues through the exact latest-state path.
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
            and self.page_size <= 512
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

        if self.remove_simulation_calls:
            # Simulator exclusion used to happen after pagination in Python.
            # That returned short/incorrect pages whenever a simulator occupied
            # a page slot. Keep the expensive raw-log JSON work candidate-scoped:
            # at most the 50 trace IDs in this classifier batch are inspected,
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
        LIMIT 50
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
            LIMIT 50
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
        if len(identities) > 200:
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
        LIMIT 200
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

"""
ClickHouse Filter Builder.

Translates the frontend filter JSON format into ClickHouse WHERE clause
fragments with parameterized values.  This module is the ClickHouse
counterpart of ``tracer.utils.filters.FilterEngine`` which operates on
Django ORM querysets.
"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from tracer.utils.constants import (
    LIST_OPS,
    NO_VALUE_OPS,
    RANGE_OPS,
    SPAN_ATTR_ALLOWED_OPS,
)

from tracer.utils.filter_operators import normalize_filter_op

_SAFE_ATTR_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


def _sanitize_key(key: str) -> str:
    """Validate a key is safe for use in ClickHouse expressions."""
    if not key or not _SAFE_ATTR_KEY_RE.match(key):
        raise ValueError(f"Invalid attribute key: {key!r}")
    return key


def _coerce_strict_bool(v: Any) -> int:
    """Native bool only; reject strings and ints."""
    if isinstance(v, bool):
        return 1 if v else 0
    raise ValueError(
        f"Invalid boolean filter value: {v!r} (expected native true/false)"
    )


_SPAN_ATTR_TYPE_META: Dict[str, Tuple[str, Callable[[Any], Any]]] = {
    "text":    ("span_attr_str",  lambda v: v if isinstance(v, str) else str(v)),
    "number":  ("span_attr_num",  lambda v: float(v)),
    "boolean": ("span_attr_bool", _coerce_strict_bool),
}

class ClickHouseFilterBuilder:
    """Translates frontend filter format to ClickHouse WHERE clauses.

    The frontend sends filters as a list of dicts::

        [
            {
                "column_id": "model",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4"
                }
            },
            ...
        ]

    This class translates each filter into a SQL fragment with ``%(param)s``
    style placeholders and collects the parameter values into a dict.

    Usage::

        fb = ClickHouseFilterBuilder(table="spans")
        where_clause, params = fb.translate(filters)
        # where_clause: "model = %(col_1)s AND cost > %(col_2)s"
        # params: {"col_1": "gpt-4", "col_2": 0.01}
    """

    # Column type constants matching ColType enum from filters.py
    NORMAL = "NORMAL"
    TRACE_END_USER = "TRACE_END_USER"
    SYSTEM_METRIC = "SYSTEM_METRIC"
    EVAL_METRIC = "EVAL_METRIC"
    SPAN_ATTRIBUTE = "SPAN_ATTRIBUTE"
    ANNOTATION = "ANNOTATION"

    # Query mode — whether the caller is paginating traces (root spans
    # only — wrap filters in `trace_id IN (...)` so child-span attributes
    # match the parent trace) or individual spans (no wrap; the filter
    # should apply to each span row directly).
    QUERY_MODE_TRACE = "trace"
    QUERY_MODE_SPAN = "span"

    # Numeric per-trace metrics where the trace list displays the
    # **root span**'s value. In QUERY_MODE_TRACE we restrict the inner
    # `trace_id IN (...)` subquery to root spans for these columns so
    # the filter result matches what the user sees in the row — without
    # this, a trace whose root has no tokens but a child LLM span does
    # would silently pass a `total_tokens > N` filter (TH-4044).
    ROOT_ONLY_SYSTEM_METRICS = {
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "avg_cost",
        "latency_ms",
        "avg_latency",
        "name",  # trace name = root span name; restrict to root spans to avoid child-span false positives
    }

    # System metric column mappings (frontend name -> ClickHouse column)
    #
    # The frontend may send either the simple column name (e.g.
    # ``total_tokens``) or the underlying OTel / openinference attribute
    # key (e.g. ``gen_ai.usage.total_tokens``, ``llm.token_count.total``).
    # Both refer to the same data — the ingest writer denormalises the
    # attribute into a top-level Int32 column. Aliasing here routes both
    # forms through ``_build_column_condition`` (which honours
    # ``ROOT_ONLY_SYSTEM_METRICS``) instead of falling through to
    # ``_build_span_attr_condition`` and matching any-span (TH-4044).
    SYSTEM_METRIC_MAP: Dict[str, str] = {
        "avg_latency": "latency_ms",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "avg_cost": "cost",
        "cost": "cost",
        "tokens": "total_tokens",
        "total_tokens": "total_tokens",
        "input_tokens": "prompt_tokens",
        "prompt_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "completion_tokens": "completion_tokens",
        # OTel gen_ai semconv aliases
        "gen_ai.usage.total_tokens": "total_tokens",
        "gen_ai.usage.prompt_tokens": "prompt_tokens",
        "gen_ai.usage.input_tokens": "prompt_tokens",
        "gen_ai.usage.completion_tokens": "completion_tokens",
        "gen_ai.usage.output_tokens": "completion_tokens",
        # openinference aliases
        "llm.token_count.total": "total_tokens",
        "llm.token_count.prompt": "prompt_tokens",
        "llm.token_count.completion": "completion_tokens",
        "model": "model",
        "provider": "provider",
        "status": "status",
        "observation_type": "observation_type",
        "span_kind": "observation_type",
        "node_type": "observation_type",
        "user": "end_user_id",
        "name": "name",
        "span_name": "name",
        "trace_name": "trace_name",
        "start_time": "start_time",
        "end_time": "end_time",
        "created_at": "created_at",
        "project_id": "project_id",
    }

    # Voice system metrics — use typed Map columns (span_attr_num) instead of
    # simpleJSONExtractFloat which fails on JSON with spaces after colons.
    VOICE_SYSTEM_METRIC_EXPRS: Dict[str, str] = {
        "turn_count": (
            "if(mapContains(span_attr_num, 'call.total_turns'), "
            "round(span_attr_num['call.total_turns']), null)"
        ),
        # Agent talk percentage: derived from call.talk_ratio.
        # talk_ratio = bot_talk_time / user_talk_time
        # percentage = ratio / (ratio + 1) * 100
        "agent_talk_percentage": (
            "if(mapContains(span_attr_num, 'call.talk_ratio') "
            "AND span_attr_num['call.talk_ratio'] > 0, "
            "round(span_attr_num['call.talk_ratio'] / "
            "(span_attr_num['call.talk_ratio'] + 1) * 100), null)"
        ),
        "avg_agent_latency_ms": (
            "if(mapContains(span_attr_num, 'avg_agent_latency_ms'), "
            "round(span_attr_num['avg_agent_latency_ms']), null)"
        ),
        "bot_wpm": (
            "if(mapContains(span_attr_num, 'call.bot_wpm'), "
            "round(span_attr_num['call.bot_wpm']), null)"
        ),
        "user_wpm": (
            "if(mapContains(span_attr_num, 'call.user_wpm'), "
            "round(span_attr_num['call.user_wpm']), null)"
        ),
        "user_interruption_count": (
            "if(mapContains(span_attr_num, 'user_interruption_count'), "
            "round(span_attr_num['user_interruption_count']), null)"
        ),
        "ai_interruption_count": (
            "if(mapContains(span_attr_num, 'ai_interruption_count'), "
            "round(span_attr_num['ai_interruption_count']), null)"
        ),
    }

    # Voice system metrics that map to string span attributes
    VOICE_SYSTEM_METRIC_STR_MAP: Dict[str, str] = {
        "ended_reason": "ended_reason",
        "call_status": "call.status",
    }

    # Voice system metrics using expressions on span_attributes_raw JSON
    VOICE_SYSTEM_METRIC_STR_EXPRS: Dict[str, str] = {
        "call_type": (
            "if(JSONExtractString(span_attributes_raw, 'raw_log', 'type') = 'inboundPhoneCall', "
            "'inbound', 'outbound')"
        ),
    }

    # Filter operation -> SQL operator
    OP_MAP: Dict[str, str] = {
        "equals": "=",
        "not_equals": "!=",
        "greater_than": ">",
        "less_than": "<",
        "greater_than_or_equal": ">=",
        "less_than_or_equal": "<=",
        "contains": "LIKE",
        "not_contains": "NOT LIKE",
        "starts_with": "LIKE",
        "ends_with": "LIKE",
        "is_null": "IS NULL",
        "is_not_null": "IS NOT NULL",
    }

    def __init__(
        self,
        table: str = "spans",
        annotation_label_ids: Optional[List[str]] = None,
        query_mode: str = QUERY_MODE_TRACE,
        project_id: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        score_date_scope: bool = True,
    ) -> None:
        self.table = table
        self.annotation_label_ids = annotation_label_ids or []
        self.query_mode = query_mode
        # Track which mode the outer caller bound in their params dict.
        # The outer ``BaseQueryBuilder`` binds either ``project_id`` (scalar)
        # OR ``project_ids`` (tuple), not both — see ``base.py``. The
        # filter builder must mirror that choice when emitting placeholders
        # inside score subqueries, otherwise execution fails with a
        # missing-parameter error. Don't collapse the two into a single list.
        self._org_scoped = project_ids is not None
        self.project_ids = (
            [str(p) for p in project_ids]
            if project_ids
            else ([str(project_id)] if project_id else None)
        )
        # When True, score subqueries (annotator / has_annotation /
        # my_annotations / per-label annotator) inject a lower-bound filter
        # on s.created_at using ``%(start_date)s`` from the outer params.
        # Callers that don't populate ``%(start_date)s`` must pass False.
        self.score_date_scope = score_date_scope
        self._param_counter: int = 0
        self._params: Dict[str, Any] = {}

    def _score_date_filter(self, alias: str = "s") -> str:
        """Return a lower-bound ``created_at`` filter for ``model_hub_score``.

        ``model_hub_score`` is ``PARTITION BY toYYYYMM(created_at)``; the
        lower bound prunes to the partitions in the visible window. We
        deliberately do **not** filter on ``s.project_id`` because in prod
        100% of score rows have ``project_id = 0000…`` (the column is
        Nullable and was never backfilled). Project scoping is enforced
        downstream via the spans join + the outer ``trace_id IN (…)``
        wrapper.
        """
        if not self.score_date_scope:
            return ""
        return f" AND {alias}.created_at >= %(start_date)s - INTERVAL 1 DAY"

    def _scoped_spans_subquery(
        self,
        *,
        select_cols: str,
        extra_where: str = "",
        score_side_where: str = "",
    ) -> str:
        """Return a ``spans`` subquery pre-filtered to the current project + date window.

        Wrapping spans in a subquery (vs. adding the same predicates to a
        ``LEFT JOIN spans ON …`` clause) is what actually prunes the spans
        partitions. ON-clause predicates filter *after* the read, so the
        full 12M+ row spans table is still scanned. Wrapping in
        ``SELECT … FROM spans WHERE project_id = X AND created_at >= Y``
        unlocks partition pruning by ``project_id`` and ``toYYYYMM(created_at)``.

        When ``score_side_where`` is provided, also gate on
        ``id IN (SELECT observation_span_id FROM model_hub_score WHERE … {score_side_where})``.
        For trace-only score data (where 100% of scores have
        ``observation_span_id = ''``) this collapses the inner set to zero
        and the JOIN becomes free — getting the annotator filter to ~65 ms
        end-to-end vs ~12 s without it. For span-scoped data it bounds the
        spans-side read to only span ids that actually have a matching
        score for this annotator / label / etc.
        """
        # Mirror the placeholder shape the caller put in self.params. When
        # constructed in org-scoped mode the outer query exposes
        # ``%(project_ids)s`` (a tuple) and never ``%(project_id)s``, even
        # for a single-project org. Using ``project_id =`` with a single-
        # element ``project_ids`` was a real bug: the binding lookup
        # missed ``project_id`` at execution time. Track which mode the
        # outer query bound rather than inferring from list length.
        if self._org_scoped:
            project_pred = "project_id IN %(project_ids)s"
        else:
            project_pred = "project_id = %(project_id)s"
        date_pred = (
            "AND created_at >= %(start_date)s - INTERVAL 1 DAY"
            if self.score_date_scope
            else ""
        )
        extra = f" AND {extra_where}" if extra_where else ""
        if score_side_where:
            score_date = self._score_date_filter()
            id_filter = (
                f" AND id IN ("
                f"SELECT observation_span_id FROM model_hub_score AS s FINAL "
                f"WHERE s._peerdb_is_deleted = 0 AND s.deleted = false "
                f"AND notEmpty(s.observation_span_id)"
                f"{score_date}"
                f" {score_side_where})"
            )
        else:
            id_filter = ""
        return (
            f"(SELECT {select_cols} FROM spans "
            f"WHERE {project_pred} "
            f"{date_pred} "
            f"AND _peerdb_is_deleted = 0"
            f"{extra}"
            f"{id_filter})"
        )

    def _next_param(self, prefix: str = "p") -> str:
        """Generate a unique parameter name."""
        self._param_counter += 1
        return f"{prefix}_{self._param_counter}"

    def _uuid_in_clause(self, values: Any, prefix: str) -> Optional[str]:
        """Return a ClickHouse UUID IN-list with individually bound params."""
        clean_values = [str(v) for v in values if v]
        if not clean_values:
            return None
        placeholders = []
        for value in clean_values:
            param = self._next_param(prefix)
            self._params[param] = value
            placeholders.append(f"toUUID(%({param})s)")
        return ", ".join(placeholders)

    @classmethod
    def _sql_op(cls, filter_op: Optional[str]) -> Optional[str]:
        """Return a SQL comparison operator for canonical filter ops only."""
        if not filter_op:
            return None
        return cls.OP_MAP.get(filter_op)

    @staticmethod
    def _eval_choice_array_expr() -> str:
        """ClickHouse stores eval choices as a JSON string; parse before membership."""
        return "JSONExtract(output_str_list, 'Array(String)')"

    @staticmethod
    def _score_trace_id_expr() -> str:
        """Resolve a Score row to the trace id rendered by the spans table."""
        return (
            "if(isNull(s.trace_id) "
            "OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'), "
            "sp.trace_id, toString(s.trace_id))"
        )

    def _score_trace_select(
        self,
        extra_where: str = "",
        *,
        alias: str = "trace_id",
        distinct: bool = True,
    ) -> str:
        """Return a Score subquery that resolves span-backed annotations.

        Unified Score rows created from inline/span annotations often leave
        ``trace_id`` empty and only populate ``observation_span_id``. Resolve
        through ``spans`` so trace filters match the same annotations the UI
        renders in the trace row.
        """
        score_trace_expr = self._score_trace_id_expr()
        select_keyword = "SELECT DISTINCT" if distinct else "SELECT"
        extra_clause = f" {extra_where}" if extra_where else ""
        date_clause = self._score_date_filter("s")
        # Wrap spans in a project + date-scoped subquery, also gated by
        # ``id IN (score rows matching extra_where)`` — see
        # ``_scoped_spans_subquery``. For trace-only scoring (100% empty
        # observation_span_id) this collapses the spans read to zero rows
        # and the annotator filter returns in ~65 ms.
        spans_subq = self._scoped_spans_subquery(
            select_cols="id, trace_id",
            score_side_where=extra_where,
        )
        return (
            f"{select_keyword} {score_trace_expr} AS {alias} "
            f"FROM model_hub_score AS s FINAL "
            f"LEFT JOIN {spans_subq} AS sp "
            f"ON sp.id = s.observation_span_id "
            f"WHERE s._peerdb_is_deleted = 0 "
            f"AND s.deleted = false "
            f"AND isNotNull({score_trace_expr}) "
            f"AND {score_trace_expr} != ''"
            f"{date_clause}"
            f"{extra_clause}"
        )

    @staticmethod
    def _score_span_id_expr() -> str:
        """Resolve a Score row to the span id it should filter in span mode."""
        return (
            "if(ifNull(s.observation_span_id, '') != '', "
            "s.observation_span_id, root_sp.id)"
        )

    def _score_span_select(
        self,
        extra_where: str = "",
        *,
        alias: str = "span_id",
        distinct: bool = True,
    ) -> str:
        """Return a Score subquery scoped to the visible span row.

        Span annotations match their exact ``observation_span_id``. Trace-level
        annotations fall back to the root span only; otherwise filtering the
        spans tab by an annotation on one trace leaks every child span from that
        trace into the result.
        """
        score_span_expr = self._score_span_id_expr()
        select_keyword = "SELECT DISTINCT" if distinct else "SELECT"
        extra_clause = f" {extra_where}" if extra_where else ""
        date_clause = self._score_date_filter("s")
        # Same rationale as ``_score_trace_select``: pre-filter spans via a
        # subquery so the root-span lookup actually prunes partitions.
        # Restrict to root spans (parent_span_id IS NULL/'') inside the
        # subquery so the LEFT JOIN's ``trace_id =`` match doesn't have to
        # scan every span row for that trace.
        spans_subq = self._scoped_spans_subquery(
            select_cols="id, trace_id",
            extra_where="(parent_span_id IS NULL OR parent_span_id = '')",
            score_side_where=extra_where,
        )
        return (
            f"{select_keyword} {score_span_expr} AS {alias} "
            f"FROM model_hub_score AS s FINAL "
            f"LEFT JOIN {spans_subq} AS root_sp "
            f"ON root_sp.trace_id = toString(s.trace_id) "
            f"WHERE s._peerdb_is_deleted = 0 "
            f"AND s.deleted = false "
            f"AND isNotNull({score_span_expr}) "
            f"AND {score_span_expr} != ''"
            f"{date_clause}"
            f"{extra_clause}"
        )

    def _score_entity_select(
        self,
        extra_where: str = "",
        *,
        alias: str = "entity_id",
        distinct: bool = True,
    ) -> str:
        if self.query_mode == self.QUERY_MODE_SPAN:
            return self._score_span_select(
                extra_where, alias=alias, distinct=distinct
            )
        return self._score_trace_select(extra_where, alias=alias, distinct=distinct)

    def _score_entity_column(self) -> str:
        return "id" if self.query_mode == self.QUERY_MODE_SPAN else "trace_id"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def translate(self, filters: List[Dict]) -> Tuple[str, Dict[str, Any]]:
        """Translate a filter list to ClickHouse WHERE clause fragments.

        Returns only the filter conditions **without** the ``WHERE`` keyword.
        Multiple conditions are joined with ``AND``.

        Datetime filters on ``created_at`` / ``start_time`` are skipped here
        because the base query builder handles date-range scoping separately.

        Args:
            filters: The list of filter dicts from the frontend.

        Returns:
            A ``(conditions_string, params_dict)`` tuple.  The conditions
            string is empty if no filters apply.
        """
        conditions: List[str] = []
        self._params = {}
        self._param_counter = 0

        for f in filters:
            col_id = f.get("column_id") or f.get("columnId")
            config = f.get("filter_config") or f.get("filterConfig", {})
            col_type = config.get("col_type") or config.get("colType") or self.NORMAL

            if not col_id or not config:
                continue

            filter_type = config.get("filter_type") or config.get("filterType")
            filter_op = config.get("filter_op") or config.get("filterOp")
            filter_value = config.get("filter_value", config.get("filterValue"))

            # Skip date filters (handled by BaseQueryBuilder.parse_time_range)
            if col_id in ("created_at", "start_time") and filter_type in (
                "datetime",
                "date",
            ):
                continue

            # Handle special annotation-related column_ids that are
            # independent of col_type (mirrors PG FilterEngine logic).
            if col_id == "my_annotations":
                cond = self._build_my_annotations_condition(filter_value, config)
                if cond:
                    conditions.append(cond)
                continue

            if col_id == "annotator" and col_type != self.ANNOTATION:
                cond = self._build_annotator_condition(filter_value)
                if cond:
                    conditions.append(cond)
                continue

            # Handle has_eval filter — subquery against tracer_eval_logger
            if col_id == "has_eval":
                cond = self._build_has_eval_condition(filter_value)
                if cond:
                    conditions.append(cond)
                continue

            # Handle has_annotation filter — subquery against model_hub_score
            if col_id == "has_annotation":
                cond = self._build_has_annotation_condition(filter_value)
                if cond:
                    conditions.append(cond)
                continue

            condition = self._build_condition(
                col_id, col_type, filter_type, filter_op, filter_value
            )
            if condition:
                conditions.append(condition)

        where = " AND ".join(conditions) if conditions else ""
        return where, self._params

    def translate_sort(
        self,
        sort_params: List[Dict],
        field_map: Optional[Dict[str, str]] = None,
    ) -> str:
        """Translate sort parameters to an ``ORDER BY`` clause.

        Args:
            sort_params: List of sort specification dicts with
                ``column_id`` and ``direction`` keys.
            field_map: Optional mapping from frontend column names to
                ClickHouse column names.

        Returns:
            An ``ORDER BY ...`` string, or an empty string if no sort
            params are provided.
        """
        if not sort_params:
            return ""

        order_parts: List[str] = []
        for s in sort_params:
            col = s.get("column_id") or s.get("columnId")
            if not col:
                continue
            direction = s.get("direction", "desc").upper()
            if direction not in ("ASC", "DESC"):
                direction = "DESC"
            # Map column names if field_map provided
            if field_map and col in field_map:
                col = field_map[col]
            else:
                # Validate column name to prevent SQL injection via ORDER BY
                try:
                    col = _sanitize_key(col)
                except ValueError:
                    continue  # skip invalid column names
            order_parts.append(f"{col} {direction}")

        return "ORDER BY " + ", ".join(order_parts) if order_parts else ""

    # ------------------------------------------------------------------
    # Internal condition builders
    # ------------------------------------------------------------------

    def _build_condition(
        self,
        col_id: str,
        col_type: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Dispatch to the appropriate condition builder based on column type."""
        if col_type != self.SPAN_ATTRIBUTE:
            filter_op = normalize_filter_op(filter_op)

        # The dashboard/metrics + get_span_attributes_list endpoints can
        # surface the same logical metric (e.g. ``gen_ai.usage.total_tokens``)
        # under both ``system_metric`` and ``custom_attribute`` categories,
        # so the frontend may tag the same filter as either SYSTEM_METRIC
        # or SPAN_ATTRIBUTE depending on which list the user picked it
        # from. When the col_id is a known SYSTEM_METRIC alias, route it
        # through the SYSTEM_METRIC path regardless of the tag — that's
        # where the denormalised column lives and the root-only
        # restriction is enforced (TH-4044).
        if col_id in self.SYSTEM_METRIC_MAP and col_type != self.SYSTEM_METRIC:
            col_type = self.SYSTEM_METRIC

        # ``user`` filters on ``end_user_id`` which is only set on the
        # user-facing child span (not on root spans, not on LLM spans).
        # Route through the TRACE_END_USER handler so it wraps in a
        # ``trace_id IN (...)`` subquery — matches all spans/traces where
        # any span belongs to the given user.
        if col_id == "user" and col_type == self.SYSTEM_METRIC:
            col_type = self.TRACE_END_USER

        # ``user_id`` is a structural filter injected by the cross-project
        # user-detail page (LLMTracingView ``userScopeFilter`` in user
        # mode). The value is the ``tracer_enduser.user_id`` **string**
        # (e.g. "9281" or "user-11771490488.8493178"), not the end_user
        # UUID — so we cannot reuse the ``TRACE_END_USER`` handler as-is
        # (it expects UUIDs on ``end_user_id``). Resolve the string to
        # end-user UUIDs via a subquery on ``tracer_enduser`` and wrap
        # the trace-id IN (...) filter around it. (TH-4436)
        #
        # NOTE: we match on ``col_id`` alone, not on ``col_type``, because
        # the frontend's ``userScopeFilter`` omits ``col_type`` so it
        # arrives as NORMAL — falling through to ``_build_column_condition``
        # and trying to resolve ``user_id`` as a literal column on ``spans``,
        # which doesn't exist. There is no legitimate other reading of
        # ``col_id == "user_id"`` on these tables; always route it here.
        if col_id == "user_id":
            if filter_value is None or filter_value == "":
                return None
            values = filter_value if isinstance(filter_value, list) else [filter_value]
            values = [str(v) for v in values if v not in (None, "")]
            if not values:
                return None
            # ``user_id`` is an exact identifier, so only membership ops
            # are meaningful. ``equals``/``in`` → traces owned by the
            # listed users; ``not_equals``/``not_in`` → traces NOT owned
            # by them. Other ops (``contains``, ``starts_with``, …) fall
            # back to equals-style membership, which matches how the
            # frontend ``userScopeFilter`` always sends ``equals``.
            negate = filter_op in ("not_equals", "not_in", "!=")
            outer_op = "NOT IN" if negate else "IN"
            param = self._next_param("uid_s")
            self._params[param] = tuple(values)
            return (
                f"trace_id {outer_op} ("
                f"SELECT trace_id FROM {self.table} "
                f"WHERE end_user_id IN ("
                f"SELECT id FROM tracer_enduser FINAL "
                f"WHERE user_id IN %({param})s "
                f"AND _peerdb_is_deleted = 0"
                f") AND _peerdb_is_deleted = 0)"
            )

        # Inverse safety: frontend may tag an eval_template_id as
        # SYSTEM_METRIC (stale filter state, or picker category
        # mismatch). If the col_id is a UUID that matches an eval
        # template in the current project, route to EVAL_METRIC.
        if col_type == self.SYSTEM_METRIC and col_id not in self.SYSTEM_METRIC_MAP:
            try:
                import uuid as _uuid

                _uuid.UUID(str(col_id))
                from model_hub.models.evals_metric import EvalTemplate

                if EvalTemplate.no_workspace_objects.filter(
                    id=col_id, deleted=False
                ).exists():
                    col_type = self.EVAL_METRIC
            except (ValueError, AttributeError, Exception):
                pass

        if col_type == self.TRACE_END_USER:
            # `end_user_id` is only set on the user-facing child span, not
            # the root span. Wrap the equality in a subquery so the trace
            # matches if ANY of its spans points at one of the end-users.
            if filter_value is None:
                return None
            ids = filter_value if isinstance(filter_value, list) else [filter_value]
            ids = [str(v) for v in ids if v]
            if not ids:
                return None
            param = self._next_param("eu")
            self._params[param] = tuple(ids)
            return (
                f"trace_id IN ("
                f"SELECT trace_id FROM {self.table} "
                f"WHERE end_user_id IN %({param})s "
                f"AND _peerdb_is_deleted = 0)"
            )

        if col_type == self.SPAN_ATTRIBUTE:
            return self._build_span_attr_condition(
                col_id, filter_type, filter_op, filter_value
            )

        if col_type == self.SYSTEM_METRIC:
            # project_id is a root-span column — filter it directly on the
            # outer query instead of wrapping in a trace_id subquery (which
            # the generic SYSTEM_METRIC path below does for child-span
            # columns like `model` or `cost`). Wrapping is unnecessary here
            # and also breaks in org-scoped mode where the builder params
            # use `project_ids`, not `project_id`.
            if col_id == "project_id":
                return self._build_column_condition(
                    "project_id", filter_type, filter_op, filter_value
                )

            if col_id in self.VOICE_SYSTEM_METRIC_EXPRS:
                expr = self.VOICE_SYSTEM_METRIC_EXPRS[col_id]
                inner = self._build_expr_condition(expr, filter_op, filter_value)
            elif col_id in self.VOICE_SYSTEM_METRIC_STR_MAP:
                # String voice metrics stored in span_attr_str
                attr_key = self.VOICE_SYSTEM_METRIC_STR_MAP[col_id]
                return self._build_span_attr_condition(
                    attr_key, "text", filter_op, filter_value
                )
            elif col_id in self.VOICE_SYSTEM_METRIC_STR_EXPRS:
                expr = self.VOICE_SYSTEM_METRIC_STR_EXPRS[col_id]
                inner = self._build_expr_condition(expr, filter_op, filter_value)
            elif col_id in self.SYSTEM_METRIC_MAP:
                ch_col = self.SYSTEM_METRIC_MAP[col_id]
                inner = self._build_column_condition(
                    ch_col, filter_type, filter_op, filter_value
                )
            else:
                # Unknown system metric — treat as span attribute
                return self._build_span_attr_condition(
                    col_id, filter_type, filter_op, filter_value
                )
            if not inner:
                return None
            # In span-list mode the caller wants the filter to apply to
            # each span row directly — no trace-level expansion.
            if self.query_mode == self.QUERY_MODE_SPAN:
                return inner
            # Trace-list mode: wrap in trace_id subquery so filters on
            # child-span columns (model, etc.) match the parent trace.
            # For numeric metrics that the trace list renders from the
            # root span (tokens / cost / latency), restrict the subquery
            # to root spans so the filter result matches the displayed
            # value — see ROOT_ONLY_SYSTEM_METRICS for context (TH-4044).
            # Check both the original col_id and the mapped ClickHouse
            # column so OTel attribute aliases (e.g.
            # ``gen_ai.usage.total_tokens``) are caught.
            mapped_col = self.SYSTEM_METRIC_MAP.get(col_id)
            is_root_only = col_id in self.ROOT_ONLY_SYSTEM_METRICS or (
                col_id != "span_name"
                and mapped_col is not None
                and mapped_col in self.ROOT_ONLY_SYSTEM_METRICS
            )
            root_clause = (
                "AND (parent_span_id IS NULL OR parent_span_id = '') "
                if is_root_only
                else ""
            )
            return (
                f"trace_id IN ("
                f"SELECT trace_id FROM {self.table} "
                f"WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0 "
                f"{root_clause}"
                f"AND {inner})"
            )

        if col_type == self.EVAL_METRIC:
            return self._build_eval_condition(col_id, filter_op, filter_value)

        if col_type == self.ANNOTATION:
            return self._build_annotation_condition(
                col_id, filter_type, filter_op, filter_value
            )

        # Default: NORMAL column -- direct column reference
        return self._build_column_condition(
            col_id, filter_type, filter_op, filter_value
        )

    def _build_span_attr_condition(
        self,
        attribute_key: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a SPAN_ATTRIBUTE predicate; raises ValueError on contract violations.

        Negation ops use ``exists AND value NOT …`` so MV-gap rows are excluded.
        """
        attribute_key = _sanitize_key(attribute_key)

        normalized_filter_type, map_column, value_coercer = (
            self._resolve_span_attr_type(filter_type)
        )
        self._require_op_allowed_for_type(normalized_filter_type, filter_op)

        normalized_value = self._normalize_span_attr_value(
            filter_op, value_coercer, filter_value
        )
        exists_predicate = f"mapContains({map_column}, '{attribute_key}')"
        inner_predicate = self._span_attr_inner(
            map_column,
            attribute_key,
            exists_predicate,
            filter_op,
            normalized_value,
        )
        if not inner_predicate:
            return None

        if self.query_mode == self.QUERY_MODE_SPAN:
            return inner_predicate
        return (
            f"trace_id IN ("
            f"SELECT trace_id FROM {self.table} "
            f"WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0 "
            f"AND {inner_predicate})"
        )

    @staticmethod
    def _resolve_span_attr_type(
        filter_type: Optional[str],
    ) -> Tuple[str, str, Callable[[Any], Any]]:
        """Resolve filter_type to (normalized_type, map_col, coerce_fn)."""
        normalized_filter_type = (filter_type or "").strip().lower()
        if normalized_filter_type not in _SPAN_ATTR_TYPE_META:
            raise ValueError(
                f"Unsupported span_attr filter_type: {filter_type!r}. "
                f"Expected one of {sorted(_SPAN_ATTR_TYPE_META)}."
            )
        map_column, value_coercer = _SPAN_ATTR_TYPE_META[normalized_filter_type]
        return normalized_filter_type, map_column, value_coercer

    @staticmethod
    def _require_op_allowed_for_type(
        normalized_filter_type: str, filter_op: Optional[str]
    ) -> None:
        """Reject filter_ops not allowed for the resolved filter_type."""
        allowed_ops = SPAN_ATTR_ALLOWED_OPS[normalized_filter_type]
        if filter_op not in allowed_ops:
            raise ValueError(
                f"filter_op {filter_op!r} not allowed for filter_type "
                f"{normalized_filter_type!r}. Allowed: {sorted(allowed_ops)}."
            )

    @staticmethod
    def _normalize_span_attr_value(
        filter_op: str,
        value_coercer: Callable[[Any], Any],
        filter_value: Any,
    ) -> Any:
        """Validate value shape per op and coerce each scalar."""
        if filter_op in NO_VALUE_OPS:
            return None

        if filter_op in RANGE_OPS:
            if not isinstance(filter_value, list) or len(filter_value) != 2:
                raise ValueError(
                    f"{filter_op!r} requires a 2-element list, got {filter_value!r}"
                )
            return [value_coercer(filter_value[0]), value_coercer(filter_value[1])]

        if filter_op in LIST_OPS:
            if not isinstance(filter_value, list) or not filter_value:
                raise ValueError(
                    f"{filter_op!r} requires a non-empty list, got {filter_value!r}"
                )
            return [value_coercer(v) for v in filter_value]

        if filter_value is None:
            raise ValueError(f"{filter_op!r} requires a value, got None")
        return value_coercer(filter_value)

    def _span_attr_inner(
        self,
        map_column: str,
        attribute_key: str,
        exists_predicate: str,
        filter_op: str,
        normalized_value: Any,
    ) -> Optional[str]:
        """Emit the row-level predicate; negation ops require key present."""
        column_access = f"{map_column}['{attribute_key}']"

        if filter_op == "is_null":
            return f"NOT {exists_predicate}"
        if filter_op == "is_not_null":
            return exists_predicate

        if filter_op == "equals":
            param = self._next_param("attr")
            self._params[param] = normalized_value
            return f"{exists_predicate} AND {column_access} = %({param})s"
        if filter_op == "not_equals":
            param = self._next_param("attr")
            self._params[param] = normalized_value
            return f"{exists_predicate} AND {column_access} != %({param})s"

        if filter_op == "in":
            param = self._next_param("attr")
            self._params[param] = tuple(normalized_value)
            return f"{exists_predicate} AND {column_access} IN %({param})s"
        if filter_op == "not_in":
            param = self._next_param("attr")
            self._params[param] = tuple(normalized_value)
            return f"{exists_predicate} AND {column_access} NOT IN %({param})s"

        if filter_op == "contains":
            param = self._next_param("attr")
            self._params[param] = f"%{normalized_value}%"
            return f"{exists_predicate} AND {column_access} LIKE %({param})s"
        if filter_op == "not_contains":
            param = self._next_param("attr")
            self._params[param] = f"%{normalized_value}%"
            return f"{exists_predicate} AND {column_access} NOT LIKE %({param})s"
        if filter_op == "starts_with":
            param = self._next_param("attr")
            self._params[param] = f"{normalized_value}%"
            return f"{exists_predicate} AND {column_access} LIKE %({param})s"
        if filter_op == "ends_with":
            param = self._next_param("attr")
            self._params[param] = f"%{normalized_value}"
            return f"{exists_predicate} AND {column_access} LIKE %({param})s"

        if filter_op == "between":
            param_lo = self._next_param("lo")
            param_hi = self._next_param("hi")
            self._params[param_lo] = normalized_value[0]
            self._params[param_hi] = normalized_value[1]
            return (
                f"{exists_predicate} AND {column_access} "
                f"BETWEEN %({param_lo})s AND %({param_hi})s"
            )
        if filter_op == "not_between":
            param_lo = self._next_param("lo")
            param_hi = self._next_param("hi")
            self._params[param_lo] = normalized_value[0]
            self._params[param_hi] = normalized_value[1]
            return (
                f"{exists_predicate} AND {column_access} "
                f"NOT BETWEEN %({param_lo})s AND %({param_hi})s"
            )

        # Comparison ops (number-only by contract).
        comparison_sql_op = {
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }.get(filter_op)
        if comparison_sql_op is not None:
            param = self._next_param("attr")
            self._params[param] = normalized_value
            return (
                f"{exists_predicate} AND {column_access} "
                f"{comparison_sql_op} %({param})s"
            )

        raise ValueError(f"Unhandled filter_op {filter_op!r}")

    # Columns whose stored values vary in case across ingest paths — OTel
    # writes lowercase ('ok'/'error'/'unset'), older provider integrations
    # wrote uppercase, and the TraceFilterPanel's static enum choices send
    # uppercase labels. Matches must be case-insensitive on both sides.
    _CASE_INSENSITIVE_COLUMNS = {"status", "observation_type"}

    def _build_column_condition(
        self,
        column: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition for a direct column reference."""
        param = self._next_param("col")
        ci = column in self._CASE_INSENSITIVE_COLUMNS

        if filter_op == "is_null":
            return f"({column} IS NULL OR {column} = '')"
        elif filter_op == "is_not_null":
            return f"({column} IS NOT NULL AND {column} != '')"
        elif filter_op == "contains":
            self._params[param] = f"%{filter_value}%"
            return f"{column} LIKE %({param})s"
        elif filter_op == "not_contains":
            self._params[param] = f"%{filter_value}%"
            return f"{column} NOT LIKE %({param})s"
        elif filter_op == "starts_with":
            self._params[param] = f"{filter_value}%"
            return f"{column} LIKE %({param})s"
        elif filter_op == "ends_with":
            self._params[param] = f"%{filter_value}"
            return f"{column} LIKE %({param})s"
        elif filter_op == "between" and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"{column} BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op == "not_between" and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"{column} NOT BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op == "in":
            values = (
                list(filter_value) if isinstance(filter_value, list) else [filter_value]
            )
            # ClickHouse rejects IN (). Keep empty-set semantics explicit:
            # value IN [] matches nothing.
            if not values:
                return "0 = 1"
            if ci:
                values = [str(v).lower() for v in values]
                self._params[param] = tuple(values)
                return f"lower({column}) IN %({param})s"
            self._params[param] = tuple(values)
            return f"{column} IN %({param})s"
        elif filter_op == "not_in":
            values = (
                list(filter_value) if isinstance(filter_value, list) else [filter_value]
            )
            # value NOT IN [] should not restrict results.
            if not values:
                return "1 = 1"
            if ci:
                values = [str(v).lower() for v in values]
                self._params[param] = tuple(values)
                return f"lower({column}) NOT IN %({param})s"
            self._params[param] = tuple(values)
            return f"{column} NOT IN %({param})s"
        else:
            op = self._sql_op(filter_op)
            if op is None:
                return "0 = 1"
            if ci and op in ("=", "!=") and isinstance(filter_value, str):
                self._params[param] = filter_value.lower()
                return f"lower({column}) {op} %({param})s"
            self._params[param] = filter_value
            return f"{column} {op} %({param})s"

    def _build_expr_condition(
        self,
        expr: str,
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition using a SQL expression (e.g. JSONExtract).

        Unlike ``_build_column_condition`` which references a column name
        directly, this wraps an arbitrary SQL expression in parentheses and
        applies the requested comparison operator.
        """
        param = self._next_param("expr")

        if filter_op == "between" and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"({expr}) BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op == "not_between" and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"({expr}) NOT BETWEEN %({p_lo})s AND %({p_hi})s"
        else:
            op = self._sql_op(filter_op)
            if op is None:
                return "0 = 1"
            self._params[param] = filter_value
            return f"({expr}) {op} %({param})s"

    def _build_eval_condition(
        self,
        eval_id: str,
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition that filters traces by eval metric value.

        ``eval_id`` is the eval_template_id sent by the frontend. Resolves to
        the matching ``CustomEvalConfig`` id(s) for the current project and
        dispatches on the template's output type (SCORE / PASS_FAIL / CHOICE)
        to compare the correct column in ``tracer_eval_logger``.
        """
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig

        project_ids = getattr(self, "project_ids", None)

        # Resolve either custom_eval_config_id (what Observe metrics usually
        # emit) or eval_template_id (older saved filters) to config ids.
        config_ids = []
        output_type = "SCORE"
        try:
            cfg_qs = CustomEvalConfig.objects.filter(id=eval_id, deleted=False)
            if not cfg_qs.exists():
                cfg_qs = CustomEvalConfig.objects.filter(
                    eval_template_id=eval_id, deleted=False
                )
            if project_ids:
                cfg_qs = cfg_qs.filter(project_id__in=project_ids)
            config_ids = [str(x) for x in cfg_qs.values_list("id", flat=True)]

            template_id = (
                cfg_qs.values_list("eval_template_id", flat=True).first()
                if config_ids
                else eval_id
            )
            tmpl = (
                EvalTemplate.no_workspace_objects.filter(
                    id=template_id, deleted=False
                )
                .values("config")
                .first()
            )
            if tmpl and isinstance(tmpl.get("config"), dict):
                ot = (
                    (tmpl["config"].get("output") or "")
                    .upper()
                    .replace("/", "_")
                    .replace(" ", "_")
                )
                if ot in ("PASS_FAIL", "CHOICE", "CHOICES", "SCORE"):
                    output_type = ot
        except Exception:
            pass

        if not config_ids:
            # No matching config — build a condition that matches nothing so
            # the filter is applied (rather than silently dropped).
            return "trace_id IN (SELECT toUUID('00000000-0000-0000-0000-000000000000'))"

        param_cfg = self._next_param("eval_cfg")
        self._params[param_cfg] = tuple(config_ids)

        op_aliases = {
            "is": "equals",
            "is_not": "not_equals",
            "equal_to": "equals",
            "not_equal_to": "not_equals",
            "inBetween": "between",
            "not_in_between": "not_between",
        }
        filter_op = op_aliases.get(filter_op, filter_op)

        _fv = filter_value
        values = (
            list(_fv)
            if isinstance(_fv, (list, tuple))
            else ([] if _fv in (None, "") else [_fv])
        )
        values = [v for v in values if v not in (None, "")]
        single_value = values[0] if values else _fv

        # Exclude errored eval rows from all value-match filters — an errored
        # eval has no meaningful Passed/Failed/score/choice value, so it
        # should never match a specific value. Traces/spans without an eval
        # row at all are naturally excluded by the outer IN subquery.
        error_clause = "AND error = 0"

        # Span-list mode: match the span whose ``id`` has the eval value.
        # Trace-list mode: match any trace that has at least one span with
        # the eval value (existing behaviour).
        if self.query_mode == self.QUERY_MODE_SPAN:
            outer_col = "id"
            inner_col = "observation_span_id"
        else:
            outer_col = "trace_id"
            inner_col = "trace_id"

        def eval_value_subquery(
            match_condition: str,
            *,
            negate_outer: bool = False,
        ) -> str:
            outer_operator = "NOT IN" if negate_outer else "IN"
            return (
                f"{outer_col} {outer_operator} ("
                f"SELECT {inner_col} FROM tracer_eval_logger FINAL "
                f"WHERE custom_eval_config_id IN %({param_cfg})s "
                f"AND _peerdb_is_deleted = 0 "
                f"{error_clause} "
                f"AND {match_condition}"
                f")"
            )

        negative_ops = {"not_equals", "not_in", "not_contains", "ne", "!="}

        if filter_op in ("is_null", "is_not_null"):
            if output_type == "PASS_FAIL":
                exists_condition = "output_bool IS NOT NULL"
            elif output_type in ("CHOICE", "CHOICES"):
                choice_array = self._eval_choice_array_expr()
                exists_condition = (
                    f"(notEmpty({choice_array}) "
                    "OR (output_str IS NOT NULL AND output_str != ''))"
                )
            else:
                exists_condition = "output_float IS NOT NULL"
            return eval_value_subquery(
                exists_condition,
                negate_outer=(filter_op == "is_null"),
            )

        if output_type == "PASS_FAIL":
            # UI sends "Passed"/"Failed" — map to output_bool.
            bool_values = []
            for value in values:
                token = str(value).strip().lower()
                if token in ("passed", "pass", "true", "1"):
                    bool_values.append(1)
                elif token in ("failed", "fail", "false", "0"):
                    bool_values.append(0)
            bool_values = list(dict.fromkeys(bool_values))
            if not bool_values:
                return "0 = 1"
            param_bool = self._next_param("eval_bool")
            self._params[param_bool] = tuple(bool_values)
            cmp = (
                f"output_bool NOT IN %({param_bool})s"
                if filter_op in negative_ops
                else f"output_bool IN %({param_bool})s"
            )
            return eval_value_subquery(cmp)

        if output_type in ("CHOICE", "CHOICES"):
            # output_str_list is a JSON string column containing a serialized
            # list; output_str holds the canonical single-value fallback.
            # Parse output_str_list before membership checks so choice filters
            # are exact and the CH query stays valid.
            if not values:
                return (
                    "1 = 1"
                    if filter_op in negative_ops
                    else "0 = 1"
                )
            choice_array = self._eval_choice_array_expr()
            choice_exists = (
                f"(notEmpty({choice_array}) "
                "OR (output_str IS NOT NULL AND output_str != ''))"
            )
            choice_conditions = []
            for value in values:
                param = self._next_param("eval_choice")
                if filter_op in ("contains", "not_contains"):
                    self._params[param] = f"%{value}%"
                    choice_conditions.append(
                        f"(arrayExists(x -> x ILIKE %({param})s, {choice_array}) "
                        f"OR output_str ILIKE %({param})s)"
                    )
                elif filter_op == "starts_with":
                    self._params[param] = f"{value}%"
                    choice_conditions.append(
                        f"(arrayExists(x -> x ILIKE %({param})s, {choice_array}) "
                        f"OR output_str ILIKE %({param})s)"
                    )
                elif filter_op == "ends_with":
                    self._params[param] = f"%{value}"
                    choice_conditions.append(
                        f"(arrayExists(x -> x ILIKE %({param})s, {choice_array}) "
                        f"OR output_str ILIKE %({param})s)"
                    )
                else:
                    self._params[param] = str(value)
                    choice_conditions.append(
                        f"(has({choice_array}, %({param})s) "
                        f"OR output_str = %({param})s)"
                    )
            combined = " OR ".join(choice_conditions)
            if filter_op in negative_ops:
                combined = f"{choice_exists} AND NOT ({combined})"
            return eval_value_subquery(combined)

        # SCORE (default) — numeric on output_float. UI displays scores as
        # 0-100, raw storage is 0-1; divide user-supplied value by 100.
        if (
            filter_op in ("between", "not_between")
            and isinstance(filter_value, (list, tuple))
            and len(filter_value) == 2
        ):
            try:
                lo = float(filter_value[0]) / 100.0
                hi = float(filter_value[1]) / 100.0
            except (ValueError, TypeError):
                return "0 = 1"
            p_lo = self._next_param("eval_lo")
            p_hi = self._next_param("eval_hi")
            self._params[p_lo] = lo
            self._params[p_hi] = hi
            range_op = "NOT BETWEEN" if filter_op == "not_between" else "BETWEEN"
            return eval_value_subquery(
                f"output_float {range_op} %({p_lo})s AND %({p_hi})s"
            )

        if filter_op in ("in", "not_in"):
            try:
                raw_values = tuple(float(value) / 100.0 for value in values)
            except (ValueError, TypeError):
                return "0 = 1"
            if not raw_values:
                return "1 = 1" if filter_op == "not_in" else "0 = 1"
            param = self._next_param("eval")
            self._params[param] = raw_values
            sql_op = "NOT IN" if filter_op == "not_in" else "IN"
            return eval_value_subquery(f"output_float {sql_op} %({param})s")

        op = self._sql_op(filter_op)
        if op is None:
            return "0 = 1"
        param = self._next_param("eval")
        try:
            raw_val = (
                float(single_value)
                if not isinstance(single_value, (int, float))
                else single_value
            )
            self._params[param] = raw_val / 100.0
        except (ValueError, TypeError):
            self._params[param] = filter_value
        return eval_value_subquery(f"output_float {op} %({param})s")

    def _build_annotation_condition(
        self,
        col_id: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition that filters by annotation value.

        Generates a subquery against the ``model_hub_score`` CDC table.
        Trace and voice queries match by ``trace_id``; span queries match by
        span ``id`` so one annotated span does not pull in sibling spans.

        ``col_id`` may contain a ``**`` separator for sub-field access
        (e.g. ``uuid**thumbs_up``); the base UUID is extracted as the
        annotation label id.
        """
        # Parse optional sub_field from col_id
        sub_field = None
        annotation_label_id = col_id
        if "**" in col_id:
            annotation_label_id, sub_field = col_id.split("**", 1)

        param_label = self._next_param("ann_label")
        self._params[param_label] = annotation_label_id
        target_column = self._score_entity_column()
        base_where = self._score_entity_select(
            f"AND s.label_id = toUUID(%({param_label})s)"
        )
        score_value = "s.value"
        score_annotator = "s.annotator_id"

        if filter_type == "number":
            param = self._next_param("ann")

            if (
                filter_op == "between"
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                p_lo = self._next_param("lo")
                p_hi = self._next_param("hi")
                self._params[p_lo] = filter_value[0]
                self._params[p_hi] = filter_value[1]
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND if(JSONHas({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'value')) BETWEEN %({p_lo})s AND %({p_hi})s)"
                )
            elif (
                filter_op == "not_between"
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                p_lo = self._next_param("lo")
                p_hi = self._next_param("hi")
                self._params[p_lo] = filter_value[0]
                self._params[p_hi] = filter_value[1]
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND if(JSONHas({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'value')) NOT BETWEEN %({p_lo})s AND %({p_hi})s)"
                )
            elif filter_op in ("in", "not_in"):
                raw_values = (
                    filter_value if isinstance(filter_value, list) else [filter_value]
                )
                values = []
                for value in raw_values:
                    try:
                        values.append(float(value))
                    except (ValueError, TypeError):
                        return "0 = 1"
                if not values:
                    return "1 = 1" if filter_op == "not_in" else "0 = 1"
                self._params[param] = tuple(values)
                sql_op = "NOT IN" if filter_op == "not_in" else "IN"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND if(JSONHas({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'value')) {sql_op} %({param})s)"
                )
            else:
                op = self._sql_op(filter_op)
                if op is None:
                    return "0 = 1"
                self._params[param] = filter_value
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND if(JSONHas({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'rating'), "
                    f"JSONExtractFloat({score_value}, 'value')) {op} %({param})s)"
                )

        elif filter_type == "boolean":
            # Thumbs up/down: filter_value is "up"/"down"/"Thumbs Up"/"Thumbs Down"/True/False
            if isinstance(filter_value, str):
                val = filter_value.lower().replace(" ", "_")
                bool_match = "'up'" if val in ("up", "true", "thumbs_up") else "'down'"
            elif isinstance(filter_value, bool):
                bool_match = "'up'" if filter_value else "'down'"
            else:
                return None
            return (
                f"{target_column} IN ({base_where} "
                f"AND JSONExtractString({score_value}, 'value') = {bool_match})"
            )

        elif filter_type == "thumbs":
            # Thumbs labels are stored as {"value": "up"|"down"} on the
            # Score row — distinct from categorical's {"selected": [...]}.
            # Multi-select on the FE arrives as an array of display labels;
            # normalize to the storage tokens before querying.
            _TOKENS = {
                "thumbs up": "up",
                "thumbs down": "down",
                "thumbs_up": "up",
                "thumbs_down": "down",
                "up": "up",
                "down": "down",
            }
            raw_values = (
                filter_value if isinstance(filter_value, list) else [filter_value]
            )
            tokens = []
            for v in raw_values:
                if v is None:
                    continue
                t = _TOKENS.get(str(v).strip().lower())
                if t is not None and t not in tokens:
                    tokens.append(t)
            if not tokens:
                return None
            param = self._next_param("ann")
            self._params[param] = tuple(tokens)
            negate = filter_op in ("not_in", "not_equals")
            sql_op = "NOT IN" if negate else "IN"
            return (
                f"{target_column} IN ({base_where} "
                f"AND JSONExtractString({score_value}, 'value') {sql_op} %({param})s)"
            )

        elif filter_type == "text":
            param = self._next_param("ann")
            text_expr = f"JSONExtractString({score_value}, 'text')"
            if filter_op == "contains":
                self._params[param] = f"%{filter_value}%"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op == "not_contains":
                self._params[param] = f"%{filter_value}%"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} NOT ILIKE %({param})s)"
                )
            elif filter_op == "equals":
                self._params[param] = filter_value
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND lower({text_expr}) = lower(%({param})s))"
                )
            elif filter_op == "not_equals":
                self._params[param] = filter_value
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND lower({text_expr}) != lower(%({param})s))"
                )
            elif filter_op == "starts_with":
                self._params[param] = f"{filter_value}%"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op == "ends_with":
                self._params[param] = f"%{filter_value}"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op in ("in", "not_in"):
                raw_values = (
                    filter_value if isinstance(filter_value, list) else [filter_value]
                )
                values = tuple(
                    str(value).lower()
                    for value in raw_values
                    if value not in (None, "")
                )
                if not values:
                    return "1 = 1" if filter_op == "not_in" else "0 = 1"
                self._params[param] = values
                sql_op = "NOT IN" if filter_op == "not_in" else "IN"
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND lower({text_expr}) {sql_op} %({param})s)"
                )
            else:
                op = self._sql_op(filter_op)
                if op is None:
                    return "0 = 1"
                self._params[param] = filter_value
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {text_expr} {op} %({param})s)"
                )

        elif filter_type in ("array", "categorical"):
            # Categorical annotations: value JSON has a "selected" key
            # containing an array like ["choice1","choice2"].
            # Use has() on the extracted array to check membership.
            #
            # Backward-compat shim: legacy saved views stored thumbs filters
            # as filter_type="categorical" with values like "Thumbs Up" /
            # "Thumbs Down". The canonical path is now filter_type="thumbs"
            # (FE auto-migrates on panel open), but until those views are
            # re-applied, we OR-in a check against the thumbs storage shape
            # ({"value":"up"|"down"}) so the first page load still matches.
            # Mirrors _THUMBS_MAP in tracer/utils/filters.py and can be
            # removed once no in-flight payloads use this combination.
            selected_expr = f"JSONExtract({score_value}, 'selected', 'Array(String)')"
            value_expr = f"JSONExtractString({score_value}, 'value')"
            _LEGACY_THUMBS = {
                "thumbs up": "up",
                "thumbs down": "down",
                "thumbs_up": "up",
                "thumbs_down": "down",
            }

            def _build_one(v: Any) -> str:
                p = self._next_param("ann")
                self._params[p] = v
                cond = f"has({selected_expr}, %({p})s)"
                thumbs = (
                    _LEGACY_THUMBS.get(v.strip().lower())
                    if isinstance(v, str)
                    else None
                )
                if thumbs is not None:
                    tp = self._next_param("ann")
                    self._params[tp] = thumbs
                    cond = f"({cond} OR {value_expr} = %({tp})s)"
                return cond

            values = filter_value if isinstance(filter_value, list) else [filter_value]
            # Empty categorical selections should not produce invalid IN () SQL.
            if not values:
                if filter_op in ("not_equals", "not_in", "not_contains"):
                    return "1 = 1"
                return "0 = 1"
            sub_conditions = [_build_one(v) for v in values]
            combined = " OR ".join(sub_conditions)
            if filter_op in ("not_equals", "not_in", "not_contains"):
                return f"{target_column} IN ({base_where} AND NOT ({combined}))"
            return f"{target_column} IN ({base_where} AND ({combined}))"

        elif filter_type == "annotator":
            # Per-label annotator filter: check if specific user(s) annotated
            # this label.
            if isinstance(filter_value, list):
                uuid_list = self._uuid_in_clause(filter_value, "ann")
                if not uuid_list:
                    return None
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {score_annotator} IN ({uuid_list}))"
                )
            elif filter_value:
                param = self._next_param("ann")
                self._params[param] = str(filter_value)
                return (
                    f"{target_column} IN ({base_where} "
                    f"AND {score_annotator} = toUUID(%({param})s))"
                )
            return None

        else:
            # Fallback: existence check — trace has any annotation with
            # this label.
            return f"{target_column} IN ({base_where})"

    # ------------------------------------------------------------------
    # Boolean metric filter handlers (has_eval, has_annotation)
    # ------------------------------------------------------------------

    def _build_has_eval_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle ``has_eval`` filter: check if the trace has eval results.

        Generates a ``trace_id IN (SELECT ...)`` subquery against the
        ``tracer_eval_logger`` CDC table.
        """
        if isinstance(filter_value, str):
            filter_value = filter_value.lower() == "true"
        if not filter_value:
            return None
        # ``tracer_eval_logger`` has no ``project_id`` column, so scope the
        # subquery by INNER JOIN to the spans table (which does) — otherwise
        # we would match trace_ids from *every* project. The outer query
        # builder already exposes ``%(project_id)s`` in its params dict
        # (seeded by ``BaseQueryBuilder.__init__``), matching the pattern
        # used by ``_build_span_attr_condition`` above.
        # toString() casts UUID → String to match spans.trace_id (String type).
        return (
            "trace_id IN ("
            "SELECT DISTINCT toString(el.trace_id) FROM tracer_eval_logger AS el FINAL "
            f"INNER JOIN {self.table} AS sp ON sp.trace_id = toString(el.trace_id) "
            "WHERE el._peerdb_is_deleted = 0 AND el.trace_id IS NOT NULL "
            "AND sp._peerdb_is_deleted = 0 "
            "AND sp.project_id = %(project_id)s)"
        )

    def _build_has_annotation_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle ``has_annotation`` filter using annotation completeness.

        "Non annotated" (filter_value=false) means the trace is missing at
        least one of the project's configured annotation labels.

        Score.trace_id is often empty because inline/span annotations are
        stored against observation_span_id. Resolve through ``spans`` so this
        filter sees the same annotations rendered in trace rows.
        """
        if isinstance(filter_value, str):
            filter_value = filter_value.lower() == "true"

        # Common subquery: resolve trace_id from Score rows even when the
        # annotation is attached to a span instead of directly to a trace.
        target_column = self._score_entity_column()
        score_entity_sq = self._score_entity_select(alias="entity_id")

        label_ids = self.annotation_label_ids
        if not label_ids:
            # Fallback: simple existence check
            op = "IN" if filter_value else "NOT IN"
            return f"{target_column} {op} ({score_entity_sq})"

        # Completeness check: fully annotated = has scores for ALL labels
        label_params = []
        for lid in label_ids:
            p = self._next_param("lbl")
            self._params[p] = str(lid)
            label_params.append(f"toUUID(%({p})s)")
        label_list = ", ".join(label_params)
        total = len(label_ids)

        fully_annotated_sq = (
            self._score_entity_select(
                f"AND s.label_id IN ({label_list})",
                alias="entity_id",
                distinct=False,
            )
            + f" GROUP BY entity_id HAVING uniq(s.label_id) >= {total}"
        )
        op = "IN" if filter_value else "NOT IN"
        return f"{target_column} {op} ({fully_annotated_sq})"

    # ------------------------------------------------------------------
    # Special annotation column handlers
    # ------------------------------------------------------------------

    def _build_my_annotations_condition(
        self,
        filter_value: Any,
        config: Dict,
    ) -> Optional[str]:
        """Handle ``my_annotations`` filter: check if the current user has
        any annotation on the trace.  ``filter_value`` should be truthy and
        the user_id is expected inside ``config``."""
        if isinstance(filter_value, str):
            filter_value = filter_value.lower() == "true"
        if not filter_value:
            return None
        user_id = config.get("user_id")
        if not user_id:
            return None
        param = self._next_param("uid")
        self._params[param] = str(user_id)
        user_clause = f"AND s.annotator_id = toUUID(%({param})s)"
        return f"{self._score_entity_column()} IN ({self._score_entity_select(user_clause)})"

    def _build_annotator_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle global ``annotator`` filter (across all annotation labels):
        check if any annotation by the given user(s) exists on the trace."""
        if not filter_value:
            return None
        if isinstance(filter_value, list):
            uuid_list = self._uuid_in_clause(filter_value, "uid")
            if not uuid_list:
                return None
            user_clause = f"AND s.annotator_id IN ({uuid_list})"
            return (
                f"{self._score_entity_column()} IN "
                f"({self._score_entity_select(user_clause)})"
            )
        else:
            param = self._next_param("uid")
            self._params[param] = str(filter_value)
            user_clause = f"AND s.annotator_id = toUUID(%({param})s)"
            return (
                f"{self._score_entity_column()} IN "
                f"({self._score_entity_select(user_clause)})"
            )

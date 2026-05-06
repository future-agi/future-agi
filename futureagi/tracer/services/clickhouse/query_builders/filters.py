"""
ClickHouse Filter Builder.

Translates the frontend filter JSON format into ClickHouse WHERE clause
fragments with parameterized values.  This module is the ClickHouse
counterpart of ``tracer.utils.filters.FilterEngine`` which operates on
Django ORM querysets.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

_SAFE_ATTR_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


def _sanitize_key(key: str) -> str:
    """Validate a key is safe for use in ClickHouse expressions."""
    if not key or not _SAFE_ATTR_KEY_RE.match(key):
        raise ValueError(f"Invalid attribute key: {key!r}")
    return key


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
        "latency",
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
        "total_tokens": "total_tokens",
        "prompt_tokens": "prompt_tokens",
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
    # Includes frontend-friendly aliases (`equal_to`, `not_equal_to`) so that
    # callers sending those names hit the right operator instead of falling
    # through to the `=` default in `_build_column_condition`.
    OP_MAP: Dict[str, str] = {
        "equals": "=",
        "equal_to": "=",
        "not_equals": "!=",
        "not_equal_to": "!=",
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
    ) -> None:
        self.table = table
        self.annotation_label_ids = annotation_label_ids or []
        self.query_mode = query_mode
        self._param_counter: int = 0
        self._params: Dict[str, Any] = {}

    def _next_param(self, prefix: str = "p") -> str:
        """Generate a unique parameter name."""
        self._param_counter += 1
        return f"{prefix}_{self._param_counter}"

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

            # Skip datetime filters (handled by BaseQueryBuilder.parse_time_range).
            # Frontend now sends offset-aware ISO strings with filter_type
            # "datetime"; "date" is accepted for backwards-compat with stale
            # saved views but the saved-view loader normalises it to
            # "datetime" on read, so this should rarely fire.
            if col_id in ("created_at", "start_time") and filter_type in (
                "datetime",
                "date",
            ):
                continue

            # Backwards-compat: legacy filters with column_id="my_annotations"
            # used to be a checkbox toggle that meant "annotated by me". The
            # canonical form today is `annotator = [current_user_id]` — the
            # frontend rewrites the toggle on emit and saved-view load. This
            # shim keeps stale requests from no-op'ing silently.
            if col_id == "my_annotations":
                if isinstance(filter_value, str):
                    truthy = filter_value.lower() == "true"
                else:
                    truthy = bool(filter_value)
                if not truthy:
                    continue
                legacy_user_id = config.get("user_id")
                if not legacy_user_id:
                    continue
                cond = self._build_annotator_condition([legacy_user_id])
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
        """Dispatch to the appropriate condition builder based on column type.

        Separation of concerns: the caller's ``col_type`` is the contract.
        ``SPAN_ATTRIBUTE`` reads only from the ``span_attr_*`` Map columns;
        ``SYSTEM_METRIC`` reads only from denormalised columns. If the user
        wants both they compose two filters. The dashboard ``/metrics``
        endpoint is responsible for surfacing each logical metric under one
        canonical category — see USER_ID_FILTER_REFACTOR.md / dashboard
        cleanup notes.
        """
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
            values = (
                filter_value if isinstance(filter_value, list) else [filter_value]
            )
            values = [str(v) for v in values if v not in (None, "")]
            if not values:
                return None
            # ``user_id`` is an exact identifier, so only membership ops
            # are meaningful. ``equals``/``in`` → traces owned by the
            # listed users; ``not_equals``/``not_in`` → traces NOT owned
            # by them. Other ops (``contains``, ``starts_with``, …) fall
            # back to equals-style membership, which matches how the
            # frontend ``userScopeFilter`` always sends ``equals``.
            negate = filter_op in ("not_equals", "not_in", "!=", "is_not")
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
                # Strict separation: SYSTEM_METRIC means a known denormalised
                # column. Unknown column_id with this col_type is a contract
                # violation (frontend tagged something as SYSTEM_METRIC that
                # the backend doesn't recognise). Drop the filter rather than
                # falling through to span_attr_* — that fall-through used to
                # silently change the storage layer the predicate ran against.
                # If the user actually wants a Map-column lookup, they should
                # tag the filter as SPAN_ATTRIBUTE.
                return None
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
            is_root_only = (
                col_id in self.ROOT_ONLY_SYSTEM_METRICS
                or (mapped_col is not None and mapped_col in self.ROOT_ONLY_SYSTEM_METRICS)
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
        key: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition for Map-column span attributes.

        The denormalized ``spans`` table stores typed attributes in Map
        columns: ``span_attr_str``, ``span_attr_num``, ``span_attr_bool``.

        Uses a ``trace_id IN (SELECT ...)`` subquery so the filter matches
        the *trace* if ANY of its spans has the attribute — not just the
        root span.  This is critical for voice call lists where filters
        like ``llm.model_name`` live on child LLM spans, not the root
        conversation span.
        """
        # Sanitize key to prevent SQL injection via map access
        key = _sanitize_key(key)

        # Determine map column based on filter_type
        if filter_type == "number":
            map_col = "span_attr_num"
        elif filter_type == "boolean":
            map_col = "span_attr_bool"
        else:
            map_col = "span_attr_str"

        param = self._next_param("attr")
        exists = f"mapContains({map_col}, '{key}')"

        # Build the inner condition on the Map column
        if filter_op == "is_null":
            inner = f"NOT {exists}"
        elif filter_op == "is_not_null":
            inner = exists
        elif filter_op == "contains":
            self._params[param] = f"%{filter_value}%"
            inner = f"{exists} AND {map_col}['{key}'] LIKE %({param})s"
        elif filter_op == "not_contains":
            self._params[param] = f"%{filter_value}%"
            inner = f"(NOT {exists} OR {map_col}['{key}'] NOT LIKE %({param})s)"
        elif filter_op == "starts_with":
            self._params[param] = f"{filter_value}%"
            inner = f"{exists} AND {map_col}['{key}'] LIKE %({param})s"
        elif filter_op == "ends_with":
            self._params[param] = f"%{filter_value}"
            inner = f"{exists} AND {map_col}['{key}'] LIKE %({param})s"
        elif filter_op in ("between", "inBetween") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            inner = f"{exists} AND {map_col}['{key}'] BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op in ("not_in_between", "not_between") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            inner = f"({exists} AND {map_col}['{key}'] NOT BETWEEN %({p_lo})s AND %({p_hi})s)"
        elif filter_op == "in" and isinstance(filter_value, list):
            self._params[param] = tuple(filter_value)
            inner = f"{exists} AND {map_col}['{key}'] IN %({param})s"
        elif filter_op == "not_in" and isinstance(filter_value, list):
            self._params[param] = tuple(filter_value)
            inner = f"(NOT {exists} OR {map_col}['{key}'] NOT IN %({param})s)"
        else:
            op = self.OP_MAP.get(filter_op, "=")
            self._params[param] = filter_value
            inner = f"{exists} AND {map_col}['{key}'] {op} %({param})s"

        # In span-list mode the caller wants the filter to apply to
        # each span row directly — return the inner condition unwrapped.
        if self.query_mode == self.QUERY_MODE_SPAN:
            return inner

        # Trace-list mode: wrap in a trace_id subquery so the filter
        # matches the trace if ANY span has the attribute, not just the
        # queried span.
        return (
            f"trace_id IN ("
            f"SELECT trace_id FROM {self.table} "
            f"WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0 "
            f"AND {inner})"
        )

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
        elif filter_op in ("between", "inBetween") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"{column} BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op in ("not_in_between", "not_between") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"{column} NOT BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op == "in":
            values = (
                list(filter_value) if isinstance(filter_value, list) else [filter_value]
            )
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
            if ci:
                values = [str(v).lower() for v in values]
                self._params[param] = tuple(values)
                return f"lower({column}) NOT IN %({param})s"
            self._params[param] = tuple(values)
            return f"{column} NOT IN %({param})s"
        else:
            op = self.OP_MAP.get(filter_op, "=")
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

        if filter_op in ("between", "inBetween") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"({expr}) BETWEEN %({p_lo})s AND %({p_hi})s"
        elif filter_op in ("not_in_between", "not_between") and isinstance(filter_value, list):
            p_lo = self._next_param("lo")
            p_hi = self._next_param("hi")
            self._params[p_lo] = filter_value[0]
            self._params[p_hi] = filter_value[1]
            return f"({expr}) NOT BETWEEN %({p_lo})s AND %({p_hi})s"
        else:
            op = self.OP_MAP.get(filter_op, "=")
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
        from tracer.models.custom_eval_config import CustomEvalConfig
        from model_hub.models.evals_metric import EvalTemplate

        project_ids = getattr(self, "project_ids", None)

        # Resolve eval_template_id → [custom_eval_config_id, ...] for project
        config_ids = []
        output_type = "SCORE"
        try:
            cfg_qs = CustomEvalConfig.objects.filter(
                eval_template_id=eval_id, deleted=False
            )
            if project_ids:
                cfg_qs = cfg_qs.filter(project_id__in=project_ids)
            config_ids = [str(x) for x in cfg_qs.values_list("id", flat=True)]

            tmpl = EvalTemplate.no_workspace_objects.filter(
                id=eval_id, deleted=False
            ).values("config").first()
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

        op = self.OP_MAP.get(filter_op, "=")
        _fv = filter_value
        if isinstance(_fv, (list, tuple)):
            _fv = _fv[0] if _fv and _fv[0] not in (None, "") else _fv

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

        if output_type == "PASS_FAIL":
            # UI sends "Passed"/"Failed" — map to output_bool.
            bool_val = str(_fv).strip().lower() in ("passed", "pass", "true", "1")
            if filter_op in ("not_equals", "ne", "!="):
                cmp = f"output_bool != {1 if bool_val else 0}"
            else:
                cmp = f"output_bool = {1 if bool_val else 0}"
            return (
                f"{outer_col} IN ("
                f"SELECT {inner_col} FROM tracer_eval_logger FINAL "
                f"WHERE custom_eval_config_id IN %({param_cfg})s "
                f"AND _peerdb_is_deleted = 0 "
                f"{error_clause} "
                f"AND {cmp}"
                f")"
            )

        if output_type in ("CHOICE", "CHOICES"):
            # output_str_list is a String column containing a serialized list;
            # output_str holds the canonical single value. Match against both.
            param_like = self._next_param("eval_like")
            param_eq = self._next_param("eval_eq")
            self._params[param_like] = f"%{_fv}%"
            self._params[param_eq] = str(_fv)
            return (
                f"{outer_col} IN ("
                f"SELECT {inner_col} FROM tracer_eval_logger FINAL "
                f"WHERE custom_eval_config_id IN %({param_cfg})s "
                f"AND _peerdb_is_deleted = 0 "
                f"{error_clause} "
                f"AND (output_str_list LIKE %({param_like})s OR output_str = %({param_eq})s)"
                f")"
            )

        # SCORE (default) — numeric on output_float. UI displays scores as
        # 0-100, raw storage is 0-1; divide user-supplied value by 100.
        param = self._next_param("eval")
        try:
            raw_val = float(_fv) if not isinstance(_fv, (int, float)) else _fv
            self._params[param] = raw_val / 100.0
        except (ValueError, TypeError):
            self._params[param] = filter_value
        return (
            f"{outer_col} IN ("
            f"SELECT {inner_col} FROM tracer_eval_logger FINAL "
            f"WHERE custom_eval_config_id IN %({param_cfg})s "
            f"AND _peerdb_is_deleted = 0 "
            f"{error_clause} "
            f"AND output_float {op} %({param})s"
            f")"
        )

    def _build_annotation_condition(
        self,
        col_id: str,
        filter_type: Optional[str],
        filter_op: Optional[str],
        filter_value: Any,
    ) -> Optional[str]:
        """Build a condition that filters by annotation value.

        Generates a ``trace_id IN (SELECT ...)`` subquery against the
        ``model_hub_score`` CDC table.  Handles all annotation filter
        types: number, boolean, text, array (categorical), and annotator.

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
        base_where = (
            f"SELECT trace_id "
            f"FROM model_hub_score FINAL "
            f"WHERE label_id = toUUID(%({param_label})s) "
            f"AND _peerdb_is_deleted = 0 AND deleted = false "
            f"AND trace_id != toUUID('00000000-0000-0000-0000-000000000000')"
        )

        if filter_type == "number":
            param = self._next_param("ann")
            op = self.OP_MAP.get(filter_op, "=")

            if (
                filter_op in ("between", "inBetween")
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                p_lo = self._next_param("lo")
                p_hi = self._next_param("hi")
                self._params[p_lo] = filter_value[0]
                self._params[p_hi] = filter_value[1]
                return (
                    f"trace_id IN ({base_where} "
                    f"AND if(JSONHas(value, 'rating'), "
                    f"JSONExtractFloat(value, 'rating'), "
                    f"JSONExtractFloat(value, 'value')) BETWEEN %({p_lo})s AND %({p_hi})s)"
                )
            elif (
                filter_op in ("not_in_between", "not_between")
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                p_lo = self._next_param("lo")
                p_hi = self._next_param("hi")
                self._params[p_lo] = filter_value[0]
                self._params[p_hi] = filter_value[1]
                return (
                    f"trace_id IN ({base_where} "
                    f"AND if(JSONHas(value, 'rating'), "
                    f"JSONExtractFloat(value, 'rating'), "
                    f"JSONExtractFloat(value, 'value')) NOT BETWEEN %({p_lo})s AND %({p_hi})s)"
                )
            else:
                self._params[param] = filter_value
                return (
                    f"trace_id IN ({base_where} "
                    f"AND if(JSONHas(value, 'rating'), "
                    f"JSONExtractFloat(value, 'rating'), "
                    f"JSONExtractFloat(value, 'value')) {op} %({param})s)"
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
                f"trace_id IN ({base_where} "
                f"AND JSONExtractString(value, 'value') = {bool_match})"
            )

        elif filter_type == "text":
            param = self._next_param("ann")
            text_expr = "JSONExtractString(value, 'text')"
            if filter_op == "contains":
                self._params[param] = f"%{filter_value}%"
                return (
                    f"trace_id IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op == "not_contains":
                self._params[param] = f"%{filter_value}%"
                return (
                    f"trace_id NOT IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op == "equals":
                self._params[param] = filter_value
                return (
                    f"trace_id IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND lower({text_expr}) = lower(%({param})s))"
                )
            elif filter_op == "not_equals":
                self._params[param] = filter_value
                return (
                    f"trace_id NOT IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND lower({text_expr}) = lower(%({param})s))"
                )
            elif filter_op == "starts_with":
                self._params[param] = f"{filter_value}%"
                return (
                    f"trace_id IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            elif filter_op == "ends_with":
                self._params[param] = f"%{filter_value}"
                return (
                    f"trace_id IN ({base_where} "
                    f"AND {text_expr} != '' "
                    f"AND {text_expr} ILIKE %({param})s)"
                )
            else:
                self._params[param] = filter_value
                op = self.OP_MAP.get(filter_op, "=")
                return (
                    f"trace_id IN ({base_where} " f"AND {text_expr} {op} %({param})s)"
                )

        elif filter_type in ("array", "categorical"):
            # Categorical annotations: value JSON has a "selected" key
            # containing an array like ["choice1","choice2"].
            # Use has() on the extracted array to check membership.
            selected_expr = "JSONExtract(value, 'selected', 'Array(String)')"
            if isinstance(filter_value, list):
                sub_conditions = []
                for value in filter_value:
                    p = self._next_param("ann")
                    self._params[p] = value
                    sub_conditions.append(f"has({selected_expr}, %({p})s)")
                combined = " OR ".join(sub_conditions)
                return f"trace_id IN ({base_where} AND ({combined}))"
            else:
                param = self._next_param("ann")
                self._params[param] = filter_value
                return (
                    f"trace_id IN ({base_where} "
                    f"AND has({selected_expr}, %({param})s))"
                )

        elif filter_type == "annotator":
            # Per-label annotator filter: check if specific user(s) annotated
            # this label.
            if isinstance(filter_value, list):
                param = self._next_param("ann")
                self._params[param] = tuple(filter_value)
                return f"trace_id IN ({base_where} " f"AND annotator_id IN %({param})s)"
            elif filter_value:
                param = self._next_param("ann")
                self._params[param] = str(filter_value)
                return (
                    f"trace_id IN ({base_where} "
                    f"AND annotator_id = toUUID(%({param})s))"
                )
            return None

        else:
            # Fallback: existence check — trace has any annotation with
            # this label.
            return f"trace_id IN ({base_where})"

    # ------------------------------------------------------------------
    # Boolean metric filter handlers (has_eval, has_annotation)
    # ------------------------------------------------------------------

    def _build_has_eval_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle ``has_eval`` filter: check if the row has eval results.

        Trace mode: ``trace_id IN (...)`` — match traces with any eval.
        Span mode: ``id IN (SELECT observation_span_id ...)`` — match spans
        that themselves have evals (sibling spans don't qualify).

        Filters by the denormalised ``project_id`` column on
        ``tracer_eval_logger`` (added in tracer migration 0074). Rows that
        predate the backfill have ``project_id IS NULL``; the migration's
        RunPython step backfills them, but during rollout we tolerate NULL
        by INNER JOIN to spans for those rows. Once the migration has
        finished + CDC has caught up across all environments, the JOIN
        fallback can be removed.
        """
        if isinstance(filter_value, str):
            filter_value = filter_value.lower() == "true"
        if not filter_value:
            return None
        # Primary path: project_id is denormalised onto tracer_eval_logger,
        # CDC-replicated to ClickHouse. Direct column comparison, no JOIN.
        if self.query_mode == self.QUERY_MODE_SPAN:
            return (
                "id IN ("
                "SELECT DISTINCT toString(observation_span_id) "
                "FROM tracer_eval_logger FINAL "
                "WHERE _peerdb_is_deleted = 0 "
                "AND observation_span_id IS NOT NULL "
                "AND project_id = toUUID(%(project_id)s)"
                ")"
            )
        return (
            "trace_id IN ("
            "SELECT DISTINCT toString(trace_id) "
            "FROM tracer_eval_logger FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND trace_id IS NOT NULL "
            "AND project_id = toUUID(%(project_id)s)"
            ")"
        )

    def _build_has_annotation_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle ``has_annotation`` filter using annotation completeness.

        "Non annotated" (filter_value=false) means the trace is missing at
        least one of the project's configured annotation labels.

        Score.trace_id is often NULL — annotations are stored on spans,
        not traces directly.  We resolve trace_id via the span:
        ``model_hub_score.observation_span_id → tracer_observation_span.trace_id``.
        We COALESCE with Score.trace_id in case some records DO have it set.
        """
        if isinstance(filter_value, str):
            filter_value = filter_value.lower() == "true"

        # Span mode: filter by spans that themselves have any annotation.
        # No completeness check at the span level — each annotation is
        # independent of the others on a per-span basis. The "fully
        # annotated for all configured labels" semantic only makes sense
        # at the trace level, where the UI groups labels per trace.
        if self.query_mode == self.QUERY_MODE_SPAN:
            op = "IN" if filter_value else "NOT IN"
            return (
                f"id {op} ("
                f"SELECT DISTINCT toString(observation_span_id) "
                f"FROM model_hub_score FINAL "
                f"WHERE _peerdb_is_deleted = 0 "
                f"AND observation_span_id IS NOT NULL"
                f")"
            )

        # Common subquery: resolve trace_id from score records.
        # Score.trace_id is often NULL; join via span to get the real trace_id.
        score_trace_sq = (
            "SELECT DISTINCT "
            "  toString(coalesce(s.trace_id, toNullable(sp.trace_id))) AS tid "
            "FROM model_hub_score AS s FINAL "
            "LEFT JOIN tracer_observation_span AS sp "
            "  ON sp.id = s.observation_span_id AND sp._peerdb_is_deleted = 0 "
            "WHERE s._peerdb_is_deleted = 0 "
            "AND coalesce(s.trace_id, toNullable(sp.trace_id)) IS NOT NULL"
        )

        label_ids = self.annotation_label_ids
        if not label_ids:
            # Fallback: simple existence check
            op = "IN" if filter_value else "NOT IN"
            return f"trace_id {op} ({score_trace_sq})"

        # Completeness check: fully annotated = has scores for ALL labels
        label_params = []
        for lid in label_ids:
            p = self._next_param("lbl")
            self._params[p] = str(lid)
            label_params.append(f"toUUID(%({p})s)")
        label_list = ", ".join(label_params)
        total = len(label_ids)

        fully_annotated_sq = (
            f"SELECT toString(coalesce(s.trace_id, toNullable(sp.trace_id))) AS tid "
            f"FROM model_hub_score AS s FINAL "
            f"LEFT JOIN tracer_observation_span AS sp "
            f"  ON sp.id = s.observation_span_id AND sp._peerdb_is_deleted = 0 "
            f"WHERE s._peerdb_is_deleted = 0 "
            f"AND coalesce(s.trace_id, toNullable(sp.trace_id)) IS NOT NULL "
            f"AND s.label_id IN ({label_list}) "
            f"GROUP BY tid HAVING uniq(s.label_id) >= {total}"
        )
        op = "IN" if filter_value else "NOT IN"
        return f"trace_id {op} ({fully_annotated_sq})"

    # ------------------------------------------------------------------
    # Special annotation column handlers
    # ------------------------------------------------------------------

    def _build_annotator_condition(
        self,
        filter_value: Any,
    ) -> Optional[str]:
        """Handle global ``annotator`` filter (across all annotation labels):
        check if any annotation by the given user(s) exists on the row.

        Trace mode: ``trace_id IN (...)``.
        Span mode: ``id IN (SELECT observation_span_id ...)`` so the filter
        means "spans annotated by the given user(s)" rather than "spans whose
        sibling was annotated by them".
        """
        if not filter_value:
            return None

        outer_col = "id" if self.query_mode == self.QUERY_MODE_SPAN else "trace_id"
        inner_col = (
            "observation_span_id"
            if self.query_mode == self.QUERY_MODE_SPAN
            else "trace_id"
        )

        if isinstance(filter_value, list):
            param = self._next_param("uid")
            self._params[param] = tuple(filter_value)
            return (
                f"{outer_col} IN ("
                f"SELECT toString({inner_col}) FROM model_hub_score FINAL "
                f"WHERE _peerdb_is_deleted = 0 "
                f"AND {inner_col} IS NOT NULL "
                f"AND annotator_id IN %({param})s)"
            )
        else:
            param = self._next_param("uid")
            self._params[param] = str(filter_value)
            return (
                f"{outer_col} IN ("
                f"SELECT toString({inner_col}) FROM model_hub_score FINAL "
                f"WHERE _peerdb_is_deleted = 0 "
                f"AND {inner_col} IS NOT NULL "
                f"AND annotator_id = toUUID(%({param})s))"
            )

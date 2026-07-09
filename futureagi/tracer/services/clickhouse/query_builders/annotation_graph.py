"""
Annotation Graph Query Builder for ClickHouse.

Replaces ``get_annotation_graph_data()`` from
``tracer.utils.graphs_optimized`` with ClickHouse-native queries against
the ``model_hub_score`` CDC table.

Supports annotation output types:
- **float:** ``avg(JSONExtractFloat(value, 'value'))`` per time bucket.
- **bool:** ``avg(CASE WHEN JSONExtractString(value, 'value') = 'up' THEN 100 ELSE 0 END)``
  per time bucket (percentage matching the requested value).
- **str_list:** ``countIf(has(JSONExtract(JSONExtractString(value, 'selected'), 'Array(String)'), choice)) * 100 / count()``
  per time bucket.
- **text:** ``count()`` per time bucket (count of annotations).
"""

from datetime import datetime, timedelta
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.expressions import (
    annotation_numeric_value_expr,
)


class AnnotationGraphQueryBuilder(BaseQueryBuilder):
    """Build time-series annotation metric queries.

    The output matches the shape produced by ``_aggregate_annotation_data``::

        {
            "name": "Annotation Name",
            "data": [{"timestamp": "...", "value": 42.5}, ...],
        }

    Args:
        project_id: Project UUID string (used for base class; not directly
            filtered on model_hub_score since that table lacks project_id).
        annotation_label_id: UUID string of the annotation label.
        annotation_name: Human-readable name of the annotation label.
        start_date: Start of the time range.
        end_date: End of the time range.
        interval: Time bucket interval.
        output_type: One of ``"float"``, ``"bool"``, ``"str_list"``, ``"text"``.
        value: The value to match for bool/str_list types.
    """

    TABLE = "model_hub_score"

    def __init__(
        self,
        project_id: str,
        annotation_label_id: str,
        annotation_name: str = "",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        interval: str = "hour",
        output_type: str = "float",
        value: Any = None,
        filters: list[dict] | None = None,
        observe_type: str = "trace",
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.annotation_label_id = annotation_label_id
        self.annotation_name = annotation_name or "Unknown"
        self.interval = interval
        self.output_type = output_type
        self.value = value
        self.filters = filters or []
        self.observe_type = observe_type

        # Graph endpoints historically default to a compact 7-day window.
        # BaseQueryBuilder's list-view fallback is intentionally much wider,
        # so only use parse_time_range here when the caller supplied filters.
        if start_date is None or end_date is None:
            if self.filters:
                parsed_start, parsed_end = self.parse_time_range(self.filters)
            else:
                parsed_end = datetime.utcnow()
                parsed_start = parsed_end - timedelta(days=7)
            self.start_date = start_date or parsed_start
            self.end_date = end_date or parsed_end
        else:
            self.start_date = start_date
            self.end_date = end_date

        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date
        self.params["label_id"] = self.annotation_label_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the annotation graph query."""
        if self.output_type == "float":
            return self._build_float_query()
        elif self.output_type == "bool":
            return self._build_bool_query()
        elif self.output_type == "str_list":
            return self._build_str_list_query()
        elif self.output_type == "text":
            return self._build_text_query()
        else:
            # Fallback to float
            return self._build_float_query()

    def format_result(
        self,
        rows: list[tuple],
        columns: list[str],
    ) -> dict[str, Any]:
        """Format the query results into the standard annotation graph response.

        Returns:
            A dict with ``name`` and ``data`` keys matching the PG format.
        """
        data_points = self.format_time_series(
            rows=rows,
            columns=columns,
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value"],
        )

        # Build the name with suffix for bool/str_list types
        name = self.annotation_name
        if self.output_type == "str_list" and self.value:
            name = f"{name} - {self.value}"
        elif self.output_type == "bool":
            if isinstance(self.value, str):
                bool_val = self.value.lower() == "true"
            else:
                bool_val = self.value if self.value is not None else True
            name = f"{name} - {'True' if bool_val else 'False'}"

        return {
            "name": name,
            "data": data_points,
        }

    # ------------------------------------------------------------------
    # Query builders per output type
    # ------------------------------------------------------------------

    def _filtered_spans_subquery(self, select_expr: str) -> str:
        """Return the row set that defines the currently visible Observe rows."""
        # Lazy import: a module-level import would form a v1↔v2 circular import.
        from tracer.services.clickhouse.v2.query_builders.filters import (
            ClickHouseFilterBuilderV2 as ClickHouseFilterBuilder,
        )

        query_mode = (
            ClickHouseFilterBuilder.QUERY_MODE_SPAN
            if self.observe_type == "span"
            else ClickHouseFilterBuilder.QUERY_MODE_TRACE
        )
        fb = ClickHouseFilterBuilder(
            table="spans",
            project_id=self.project_id,
            query_mode=query_mode,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        extra_clause = f"AND {extra_where}" if extra_where else ""
        return f"""
            SELECT DISTINCT {select_expr}
            FROM spans
            WHERE project_id = %(project_id)s
              AND is_deleted = 0
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {extra_clause}
        """

    def _entity_scope_clause(self) -> str:
        """Scope annotation scores to the same trace/span set as the grid."""
        if self.observe_type == "span":
            span_subquery = self._filtered_spans_subquery("toString(id)")
            return f"AND observation_span_id IN ({span_subquery})"

        trace_subquery = self._filtered_spans_subquery("trace_id")
        span_subquery = self._filtered_spans_subquery("toString(id)")
        return f"""
          AND (
            (isNotNull(trace_id) AND toString(trace_id) IN ({trace_subquery}))
            OR observation_span_id IN ({span_subquery})
          )
        """

    def _build_float_query(self) -> tuple[str, dict[str, Any]]:
        """Average numeric/star annotation value per time bucket."""
        bucket_fn = self.time_bucket_expr(self.interval)
        # Use the nullable extractor so rows whose JSON payload is missing
        # the ``rating`` / ``value`` key return NULL and are skipped by
        # ``avg()`` instead of silently contributing 0.0.
        nullable_expr = annotation_numeric_value_expr(nullable=True)
        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            avg({nullable_expr}) AS value
        FROM {self.TABLE} FINAL
        WHERE is_deleted = 0
          AND deleted = 0
          AND label_id = toUUID(%(label_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {self._entity_scope_clause()}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_bool_query(self) -> tuple[str, dict[str, Any]]:
        """Percentage of annotations matching the requested bool value."""
        bucket_fn = self.time_bucket_expr(self.interval)

        # Determine the bool value to match
        value_to_match = self.value
        if isinstance(value_to_match, str):
            value_to_match = value_to_match.lower() == "true"
        if value_to_match is None:
            value_to_match = True

        # In model_hub_score, thumbs up/down is stored as
        # JSONExtractString(value, 'value') = 'up' / 'down'
        match_str = "up" if value_to_match else "down"
        self.params["bool_match"] = match_str

        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            avg(CASE WHEN JSONExtractString(value, 'value') = %(bool_match)s THEN 100.0 ELSE 0.0 END) AS value
        FROM {self.TABLE} FINAL
        WHERE is_deleted = 0
          AND deleted = 0
          AND label_id = toUUID(%(label_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {self._entity_scope_clause()}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_str_list_query(self) -> tuple[str, dict[str, Any]]:
        """Percentage of annotations containing the requested choice."""
        bucket_fn = self.time_bucket_expr(self.interval)

        if not self.value:
            # No choice specified; return empty
            return self._build_float_query()

        self.params["choice_value"] = self.value

        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            avg(
                CASE
                    WHEN has(
                        JSONExtract(value, 'selected', 'Array(String)'),
                        %(choice_value)s
                    ) THEN 100.0
                    ELSE 0.0
                END
            ) AS value
        FROM {self.TABLE} FINAL
        WHERE is_deleted = 0
          AND deleted = 0
          AND label_id = toUUID(%(label_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {self._entity_scope_clause()}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_text_query(self) -> tuple[str, dict[str, Any]]:
        """Count of annotations per time bucket."""
        bucket_fn = self.time_bucket_expr(self.interval)
        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            count() AS value
        FROM {self.TABLE} FINAL
        WHERE is_deleted = 0
          AND deleted = 0
          AND label_id = toUUID(%(label_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {self._entity_scope_clause()}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

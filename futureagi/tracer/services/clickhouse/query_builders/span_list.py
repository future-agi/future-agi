"""
Span List Query Builder for ClickHouse.

Replaces the ``list_spans_observe()`` method in ``tracer.views.observation_span``
with a three-phase ClickHouse query strategy:

Phase 1 -- Paginated spans from the denormalized ``spans`` table (all spans,
not just root spans).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for the page of
span IDs, grouped by ``(observation_span_id, custom_eval_config_id)``.

Phase 3 -- Annotations from ``model_hub_score FINAL`` for the page of
span IDs, grouped by ``(observation_span_id, label_id)``.

The three result sets are merged in Python to produce the final response.
"""

from typing import Any

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.eval_status import (
    non_terminal_eval_marker,
)
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_builders.latest_attributes import (
    build_latest_span_probe_predicate,
    is_latest_span_probe_filter,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)

_CANDIDATE_EXTERNAL_SPAN_COLUMNS = frozenset(
    {"my_annotations", "annotator", "has_annotation"}
)


def _is_candidate_external_span_filter(item: dict[str, Any]) -> bool:
    """Whether a filter is exact in a candidate-scoped secondary lookup."""

    column_id = str(item.get("column_id") or item.get("columnId") or "")
    config = item.get("filter_config") or item.get("filterConfig") or {}
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    return column_id in _CANDIDATE_EXTERNAL_SPAN_COLUMNS or col_type in {
        "EVAL_METRIC",
        "ANNOTATION",
    }


class SpanListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated span list (observe) view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of spans per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        eval_config_ids: List of ``CustomEvalConfig`` UUID strings.
        annotation_label_ids: List of ``AnnotationsLabels`` UUID strings.
    """

    TABLE = "spans"
    ANNOTATION_TABLE = "model_hub_score"
    # Filter compiler class; the v2 list builder overrides this to the v2
    # builder so it reads the v2 dimension tables (end_users, etc.).
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilder

    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "start_time",
        "start_time": "start_time",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "cost": "cost",
        "total_tokens": "total_tokens",
        "name": "name",
        "span_name": "name",
        "status": "status",
    }

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        annotation_label_ids: list[str] | None = None,
        end_user_id: str | None = None,
        project_version_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.eval_config_ids = eval_config_ids or []
        self.annotation_label_ids = annotation_label_ids or []
        self.end_user_id = end_user_id
        self.project_version_id = project_version_id

    # ------------------------------------------------------------------
    # Phase 1: Paginated span list
    # ------------------------------------------------------------------

    def requires_bounded_filter_scan(self) -> bool:
        """Return whether this request should use bounded time slices.

        Every span-attribute value lives in a raw Map column, regardless of
        whether its declared type is text, number, or boolean. Those predicates
        can turn a multi-day list request into a wide Map scan, so route all of
        them through the bounded executor. Unknown SYSTEM_METRIC keys also fall
        back to span attributes in the filter compiler and need the same bound.
        Physical IDs and denormalized system columns stay on their direct,
        indexed query path.
        """
        for filter_item in self.filters:
            column_id = filter_item.get("column_id") or filter_item.get("columnId")
            if column_id in {"created_at", "start_time"}:
                continue
            config = (
                filter_item.get("filter_config")
                or filter_item.get("filterConfig")
                or {}
            )
            col_type = config.get("col_type") or config.get("colType")
            normalized_col_type = str(col_type or "").strip().upper()
            if normalized_col_type == "SPAN_ATTRIBUTE":
                return True
            if (
                normalized_col_type == "SYSTEM_METRIC"
                and column_id not in self._FILTER_BUILDER_CLS.SYSTEM_METRIC_MAP
                and column_id not in self._FILTER_BUILDER_CLS.VOICE_SYSTEM_METRIC_EXPRS
                and column_id
                not in self._FILTER_BUILDER_CLS.VOICE_SYSTEM_METRIC_STR_MAP
                and column_id
                not in self._FILTER_BUILDER_CLS.VOICE_SYSTEM_METRIC_STR_EXPRS
                and column_id not in self._FILTER_BUILDER_CLS._ENDUSER_STRING_COLUMNS
            ):
                return True
        return False

    def has_string_filter(self) -> bool:
        """Backward-compatible alias for older internal callers and tests."""
        return self.requires_bounded_filter_scan()

    def build(
        self,
        since: Any = None,
        *,
        slice_end: Any = None,
        limit: int | None = None,
        before_start_time: Any = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated span data.

        Args:
            since: Optional narrowed window start (datetime) for progressive
                time-slice pagination.
            slice_end: Optional exclusive window end. Together with ``since``
                this produces one adjacent ``[slice_start, slice_end)`` slice.
                Every row in a newer slice sorts before every row in an older
                one, so concatenating complete slices in newest-first order
                preserves the exact global prefix.
            limit: Optional raw-prefix limit for this slice. The bounded
                executor lowers it as rows are collected so it never transfers
                more than the one global prefix needed for the requested page.
            before_start_time: Optional keyset timestamp for continuing a
                saturated slice in canonical newest-first order.
            before_id: Span-id tiebreak paired with ``before_start_time``.
                Both keyset values are required together.

        The regular ``start_date``/``end_date`` params remain bound to the full
        requested window so the count query still describes the full request.
        """
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            span_latest_state=self.requires_bounded_filter_scan(),
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            # `id DESC` tiebreak: ClickHouse's parallel sort is not stable, so
            # equal start_time rows can permute between requests. Prefix-dedup
            # pagination (page_dedup.py) slices consecutive pages out of what
            # must be ONE stable global order — without the tiebreak a row can
            # appear on two pages or be skipped. Deterministic order also makes
            # the progressive-slice prefix (see `since`) reproducible.
            order_clause = "ORDER BY start_time DESC, id DESC"

        self.params.pop("slice_start", None)
        self.params.pop("slice_end", None)
        self.params.pop("keyset_start_time", None)
        self.params.pop("keyset_id", None)
        slice_fragment = ""
        if since is not None:
            self.params["slice_start"] = since
            slice_fragment = "AND start_time >= %(slice_start)s"
            if slice_end is not None:
                self.params["slice_end"] = slice_end
                slice_fragment += " AND start_time < %(slice_end)s"
        elif slice_end is not None:
            raise ValueError("slice_end requires since")

        if (before_start_time is None) != (before_id is None):
            raise ValueError(
                "before_start_time and before_id must be provided together"
            )
        keyset_fragment = ""
        if before_start_time is not None:
            self.params["keyset_start_time"] = before_start_time
            self.params["keyset_id"] = str(before_id)
            keyset_fragment = """
              AND (
                  start_time < %(keyset_start_time)s
                  OR (
                      start_time = %(keyset_start_time)s
                      AND id < %(keyset_id)s
                  )
              )
            """

        # Prefix-fetch pagination: read the sorted prefix [0, offset +
        # 2*page_size) in ONE bounded top-K pass and let the view dedup by
        # span id then slice [offset, offset + page_size) — see
        # tracer/services/clickhouse/page_dedup.py. This preserves the
        # global-dedup semantics `LIMIT 1 BY id` provided (a key can never
        # appear on two pages) without its O(window) full sort; the 2x
        # page_size margin keeps pages exact for up to page_size duplicate
        # rows in the prefix. No SQL OFFSET — slicing happens in Python.
        offset = self.page_number * self.page_size
        prefix_limit = offset + 2 * self.page_size if limit is None else int(limit)
        if prefix_limit <= 0:
            raise ValueError("limit must be greater than zero")
        self.params["limit"] = prefix_limit

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        end_user_fragment = ""
        if self.end_user_id:
            end_user_fragment = "AND end_user_id = %(end_user_id)s"
            self.params["end_user_id"] = self.end_user_id

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        # P3b step1.5 id-remap resolution (DESIGN §3 / id_remap_sql): this is the
        # per-user span list — `end_user_id` is passed as the OLD curated id
        # (obs_span view resolves `user_id` → `EndUser.objects.get(...).id`). A
        # cross-cutover straddler's NEW (deterministic-id) spans carry
        # `end_user_id = new_id`, so resolve each span new→old through
        # `end_user_id_remap` BEFORE the user filter, and re-project the resolved
        # id AS `end_user_id` so the displayed column also reads under the OLD
        # identity. The non-user predicates (project / time / version / generic
        # `{filter_fragment}`) stay on the bare `{self.TABLE}` inner scan (they
        # may reference span columns this wrap does not project); only the
        # identity resolve+filter moves to the wrapped layer. `resolved_id_expr`
        # is the zero-uuid-guarded new→old map — NOT a COALESCE; an unmatched
        # LEFT JOIN fills `old_id` with the zero-uuid, not NULL (see id_remap_sql).
        # Gated on `self.end_user_id`: a non-user span list keeps the committed
        # bare-`spans` query verbatim (out of scope). Pre-flip even the user path
        # is byte-identical — NO span matches a `new_id`, so the resolved id ==
        # the span's own id (gate B).
        if self.end_user_id:
            remap_join = remap_left_join("rs.end_user_id", "end_user_id_remap")
            resolved_eu = resolved_id_expr("rs.end_user_id")
            span_source = (
                f"{self.TABLE} FINAL"
                if self.requires_bounded_filter_scan()
                else self.TABLE
            )
            inner_scan = f"""
            SELECT
                id,
                trace_id,
                name,
                observation_type,
                status,
                start_time,
                end_time,
                latency_ms,
                cost,
                total_tokens,
                prompt_tokens,
                completion_tokens,
                model,
                provider,
                end_user_id,
                created_at
            FROM {span_source}
            {self.project_where()}
              AND created_at >= %(start_date)s - INTERVAL 1 DAY
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {slice_fragment}
              {keyset_fragment}
              {pv_fragment}
              {filter_fragment}
            """
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
                cost,
                total_tokens,
                prompt_tokens,
                completion_tokens,
                model,
                provider,
                resolved_end_user_id AS end_user_id,
                created_at
            FROM (
                SELECT
                    rs.*,
                    {resolved_eu} AS resolved_end_user_id
                FROM ({inner_scan}) AS rs
                {remap_join}
            )
            WHERE resolved_end_user_id = %(end_user_id)s
            {order_clause}
            LIMIT %(limit)s
            """
            return query, dict(self.params)

        # Light columns only — input/output fetched via build_content_query().
        #
        # PERF: no `LIMIT 1 BY id`. On a wide time window that clause forced CH
        # to read + full-sort EVERY matching row (the whole window) to dedup by
        # id before applying ORDER BY … LIMIT — O(rows-in-window) memory that
        # OOM-crashed the server at ~10M+ rows. Dropping it lets `ORDER BY
        # start_time DESC LIMIT n` run as a bounded top-N (a size-n priority
        # queue, O(n) memory), so the page returns without materializing the
        # window. ReplacingMergeTree duplicate span versions are rare + transient
        # (collapsed on the next merge); the view dedups the returned page by
        # span_id in Python to keep one row per span. `is_deleted = 0` (from
        # project_where) still excludes soft-deleted rows.
        span_source = (
            f"{self.TABLE} FINAL" if self.requires_bounded_filter_scan() else self.TABLE
        )
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
            cost,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            model,
            provider,
            end_user_id,
            created_at
        FROM {span_source}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {slice_fragment}
          {keyset_fragment}
          {end_user_fragment}
          {pv_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT %(limit)s
        """
        return query, dict(self.params)

    def build_id_query(
        self,
        *,
        limit: int | None = None,
        sampling_salt: str | None = None,
        sampling_rate: float | None = None,
        order_by_recent_minute: bool = False,
        latest_state: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Filtered span ids only — same filter/time window as ``build()``.

        A capped query is a deterministic bounded top-K. The default order is
        by id; eval-task resolution can request a newest-minute/id order that
        composes exactly with its adjacent minute fallback. It deliberately
        does not use ``LIMIT 1 BY id``: ClickHouse applies that de-duplication
        before the plain ``LIMIT``, forcing a full-window sort/materialization.
        The eval-task caller fetches a bounded duplicate margin and
        de-duplicates ids in Python instead.

        Optional deterministic hash sampling is applied before the top-K. This
        lets task row limits retain their "sample matching rows, then stop at
        the limit" semantics without over-scanning by ``1 / sampling_rate``.
        With ``limit=None`` the query remains streaming and unordered.
        """
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            span_latest_state=latest_state,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        if (sampling_salt is None) != (sampling_rate is None):
            raise ValueError(
                "sampling_salt and sampling_rate must be provided together"
            )
        sampling_fragment = ""
        if sampling_rate is not None:
            rate = float(sampling_rate)
            if not 0 <= rate <= 100:
                raise ValueError("sampling_rate must be between 0 and 100")
            self.params["id_sampling_salt"] = str(sampling_salt)
            self.params["id_sampling_rate"] = rate
            sampling_fragment = (
                "AND modulo("
                "cityHash64(%(id_sampling_salt)s, toString(id)), 100"
                ") < %(id_sampling_rate)s"
            )

        order_fragment = ""
        limit_fragment = ""
        if limit is not None:
            if int(limit) <= 0:
                raise ValueError("limit must be greater than zero")
            order_fragment = (
                "ORDER BY toStartOfMinute(start_time) DESC, id"
                if order_by_recent_minute
                else "ORDER BY id"
            )
            limit_fragment = "LIMIT %(id_limit)s"
            self.params["id_limit"] = int(limit)

        select_fragment = (
            "id, start_time AS eval_order_start_time"
            if order_by_recent_minute
            else "id"
        )
        span_source = f"{self.TABLE} FINAL" if latest_state else self.TABLE
        query = f"""
        SELECT {select_fragment}
        FROM {span_source}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {pv_fragment}
          {filter_fragment}
          {sampling_fragment}
        {order_fragment}
        {limit_fragment}
        """
        return query, self.params

    def build_content_query(self, span_ids: list) -> tuple[str, dict[str, Any]]:
        """Point-hydrate the visible page from latest span versions.

        Phase 1 deliberately selects only ``(id, start_time)``. Keeping the
        display columns out of the slice-wide ``GROUP BY`` matters on ingest
        heavy projects: a minute can contain hundreds of thousands of ids,
        while a visible page contains only a few dozen.
        """
        if not span_ids:
            return "", {}
        if "start_date" not in self.params or "end_date" not in self.params:
            start_date, end_date = self.parse_time_range(self.filters)
            self.params["start_date"] = start_date
            self.params["end_date"] = end_date
        params = {**self.params, "content_span_ids": tuple(span_ids)}
        query = f"""
        SELECT
               id,
               argMax(trace_id, _peerdb_version) AS trace_id,
               argMax(name, _peerdb_version) AS name,
               argMax(observation_type, _peerdb_version) AS observation_type,
               argMax(status, _peerdb_version) AS status,
               argMax(start_time, _peerdb_version) AS start_time,
               argMax(tuple(end_time), _peerdb_version).1 AS end_time,
               argMax(latency_ms, _peerdb_version) AS latency_ms,
               argMax(cost, _peerdb_version) AS cost,
               argMax(total_tokens, _peerdb_version) AS total_tokens,
               argMax(prompt_tokens, _peerdb_version) AS prompt_tokens,
               argMax(completion_tokens, _peerdb_version) AS completion_tokens,
               argMax(model, _peerdb_version) AS model,
               argMax(provider, _peerdb_version) AS provider,
               argMax(tuple(end_user_id), _peerdb_version).1 AS end_user_id,
               argMax(created_at, _peerdb_version) AS created_at,
               argMax(input, _peerdb_version) AS input,
               argMax(output, _peerdb_version) AS output,
               argMax(attributes_extra, _peerdb_version) AS attributes_extra,
               argMax(span_attr_str, _peerdb_version) AS attrs_string,
               argMax(span_attr_num, _peerdb_version) AS attrs_number,
               argMax(span_attr_bool, _peerdb_version) AS attrs_bool
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND id IN %(content_span_ids)s
          AND start_time >= %(start_date)s - INTERVAL 1 DAY
          AND start_time < %(end_date)s + INTERVAL 1 DAY
        GROUP BY id
        HAVING argMax(is_deleted, _peerdb_version) = 0
        """
        return query, params

    def build_preview_hydration_query(
        self, span_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate preview rows without unrelated content or attributes.

        Task creation needs the selected filter attributes in the preview so
        the mapping can be verified. It does not need input/output or every
        custom attribute. The id set is page-bounded (preview caps it at ten),
        and returned maps contain only the typed keys present in the filters.
        """
        bounded_ids = [str(span_id) for span_id in span_ids if span_id]
        if not bounded_ids:
            return "", {}
        if "start_date" not in self.params or "end_date" not in self.params:
            start_date, end_date = self.parse_time_range(self.filters)
            self.params["start_date"] = start_date
            self.params["end_date"] = end_date

        keys_by_type: dict[str, list[str]] = {
            "text": [],
            "number": [],
            "boolean": [],
        }
        for item in self.filters:
            key = item.get("column_id") or item.get("columnId")
            if not key or key in {"created_at", "start_time"}:
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            col_type = str(
                config.get("col_type") or config.get("colType") or ""
            ).upper()
            if col_type != "SPAN_ATTRIBUTE":
                continue
            filter_type = str(
                config.get("filter_type") or config.get("filterType") or ""
            ).lower()
            normalized_type = "text" if filter_type == "string" else filter_type
            if (
                normalized_type in keys_by_type
                and str(key) not in keys_by_type[normalized_type]
            ):
                keys_by_type[normalized_type].append(str(key))

        params: dict[str, Any] = {
            **self.params,
            "preview_span_ids": tuple(bounded_ids),
        }
        selected_map_aggregates: list[str] = []
        selected_map_projections: list[str] = []
        for filter_type, source_column, alias in (
            ("text", "span_attr_str", "attrs_string"),
            ("number", "span_attr_num", "attrs_number"),
            ("boolean", "span_attr_bool", "attrs_bool"),
        ):
            keys = keys_by_type[filter_type]
            if not keys:
                continue
            param_name = f"preview_{filter_type}_keys"
            params[param_name] = tuple(keys)
            latest_alias = f"latest_{alias}"
            selected_map_aggregates.append(
                "mapFilter((key, value) -> key IN "
                f"%({param_name})s, argMax({source_column}, _peerdb_version)) "
                f"AS {latest_alias}"
            )
            selected_map_projections.append(f"{latest_alias} AS {alias}")
        selected_map_aggregate_fragment = (
            ",\n                    "
            + ",\n                    ".join(selected_map_aggregates)
            if selected_map_aggregates
            else ""
        )
        selected_map_projection_fragment = (
            ",\n               " + ",\n               ".join(selected_map_projections)
            if selected_map_projections
            else ""
        )

        query = f"""
        SELECT
               grouped_id AS id,
               latest_trace_id AS trace_id,
               latest_name AS name,
               latest_observation_type AS observation_type,
               latest_status AS status,
               latest_start_time AS start_time,
               latest_end_time AS end_time,
               latest_latency_ms AS latency_ms,
               latest_cost AS cost,
               latest_total_tokens AS total_tokens,
               latest_prompt_tokens AS prompt_tokens,
               latest_completion_tokens AS completion_tokens,
               latest_model AS model,
               latest_provider AS provider,
               latest_end_user_id AS end_user_id,
               latest_created_at AS created_at
               {selected_map_projection_fragment}
        FROM (
            SELECT
                    id AS grouped_id,
                    argMax(trace_id, _peerdb_version) AS latest_trace_id,
                    argMax(name, _peerdb_version) AS latest_name,
                    argMax(observation_type, _peerdb_version)
                        AS latest_observation_type,
                    argMax(status, _peerdb_version) AS latest_status,
                    argMax(tuple(start_time), _peerdb_version).1
                        AS latest_start_time,
                    argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                    argMax(latency_ms, _peerdb_version) AS latest_latency_ms,
                    argMax(cost, _peerdb_version) AS latest_cost,
                    argMax(total_tokens, _peerdb_version) AS latest_total_tokens,
                    argMax(prompt_tokens, _peerdb_version) AS latest_prompt_tokens,
                    argMax(completion_tokens, _peerdb_version)
                        AS latest_completion_tokens,
                    argMax(model, _peerdb_version) AS latest_model,
                    argMax(provider, _peerdb_version) AS latest_provider,
                    argMax(tuple(end_user_id), _peerdb_version).1
                        AS latest_end_user_id,
                    argMax(created_at, _peerdb_version) AS latest_created_at,
                    argMax(is_deleted, _peerdb_version) AS latest_is_deleted
                    {selected_map_aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND id IN %(preview_span_ids)s
              AND start_time >= %(start_date)s - INTERVAL 1 DAY
              AND start_time < %(end_date)s + INTERVAL 1 DAY
            GROUP BY id
        )
        WHERE latest_is_deleted = 0
        """
        return query, params

    def supports_latest_attribute_page(self) -> bool:
        """Whether a scalar latest-state page can represent this request.

        The historical name is retained for callers, but the bounded reducer is
        no longer attribute-only.  Every supported physical system metric is
        reduced with the same per-id ``argMax`` state as typed attributes, so a
        later value change, explicit NULL, or tombstone cannot resurrect an
        older matching span.
        """
        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        return (
            not self.sort_params
            and self.end_user_id is None
            and all(is_latest_span_probe_filter(item) for item in active_filters)
        )

    def supports_latest_candidate_page(self) -> bool:
        """Whether bounded candidates can represent every active filter.

        Eval and annotation filters do not live on ``spans`` and therefore
        cannot be scalar ``argMax`` states.  They are still exact once a small
        span-id batch has been selected: the classifier scopes the secondary
        FINAL lookup to that batch and combines it with the scalar predicates.
        """

        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        return (
            not self.sort_params
            and self.end_user_id is None
            and all(
                is_latest_span_probe_filter(item)
                or _is_candidate_external_span_filter(item)
                for item in active_filters
            )
        )

    def build_latest_attribute_page(
        self,
        *,
        slice_start: Any,
        slice_end: Any,
        limit: int,
        before_start_time: Any = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a skinny latest-version page without table-level ``FINAL``.

        Attribute Map values are reduced to scalar ``argMax`` states per span
        before filtering. The query therefore preserves ReplacingMergeTree
        latest-state semantics without retaining whole Maps in merge state.
        """
        if not self.supports_latest_attribute_page():
            raise ValueError("latest scalar span page does not support this request")
        if int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_id is None):
            raise ValueError(
                "before_start_time and before_id must be provided together"
            )

        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)
        self.params.update(
            {
                "start_date": request_start,
                "end_date": request_end,
                "slice_start": slice_start,
                "slice_end": slice_end,
                "limit": int(limit),
            }
        )
        plans = [
            build_latest_span_probe_predicate(item, index=index)
            for index, item in enumerate(
                item
                for item in self.filters
                if (item.get("column_id") or item.get("columnId"))
                not in {"created_at", "start_time"}
            )
        ]
        for plan in plans:
            self.params.update(plan.params)
        attribute_aggregates = [
            aggregate for plan in plans for aggregate in plan.aggregates
        ]
        project_version_fragment = ""
        if self.project_version_id:
            self.params["project_version_id"] = str(self.project_version_id)
            attribute_aggregates.append(
                "argMax(tuple(project_version_id), _peerdb_version).1 "
                "AS latest_project_version_id"
            )
            project_version_fragment = (
                " AND latest_project_version_id = %(project_version_id)s"
            )
        aggregate_fragment = (
            ",\n                    "
            + ",\n                    ".join(attribute_aggregates)
            if attribute_aggregates
            else ""
        )
        predicate_fragment = (
            " AND " + " AND ".join(plan.predicate for plan in plans) if plans else ""
        )
        keyset_fragment = ""
        if before_start_time is not None:
            self.params["keyset_start_time"] = before_start_time
            self.params["keyset_id"] = str(before_id)
            keyset_fragment = """
              AND (
                  latest_start_time < %(keyset_start_time)s
                  OR (
                      latest_start_time = %(keyset_start_time)s
                      AND grouped_id < %(keyset_id)s
                  )
              )
            """

        query = f"""
        SELECT
            grouped_id AS id,
            latest_trace_id AS trace_id,
            latest_name AS name,
            latest_observation_type AS observation_type,
            latest_status AS status,
            latest_start_time AS start_time,
            latest_end_time AS end_time,
            latest_latency_ms AS latency_ms,
            latest_cost AS cost,
            latest_total_tokens AS total_tokens,
            latest_prompt_tokens AS prompt_tokens,
            latest_completion_tokens AS completion_tokens,
            latest_model AS model,
            latest_provider AS provider,
            latest_end_user_id AS end_user_id,
            latest_created_at AS created_at
        FROM (
            SELECT
                id AS grouped_id,
                argMax(trace_id, _peerdb_version) AS latest_trace_id,
                argMax(name, _peerdb_version) AS latest_name,
                argMax(observation_type, _peerdb_version) AS latest_observation_type,
                argMax(status, _peerdb_version) AS latest_status,
                argMax(start_time, _peerdb_version) AS latest_start_time,
                argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                argMax(latency_ms, _peerdb_version) AS latest_latency_ms,
                argMax(cost, _peerdb_version) AS latest_cost,
                argMax(total_tokens, _peerdb_version) AS latest_total_tokens,
                argMax(prompt_tokens, _peerdb_version) AS latest_prompt_tokens,
                argMax(completion_tokens, _peerdb_version) AS latest_completion_tokens,
                argMax(model, _peerdb_version) AS latest_model,
                argMax(provider, _peerdb_version) AS latest_provider,
                argMax(tuple(end_user_id), _peerdb_version).1
                    AS latest_end_user_id,
                argMax(created_at, _peerdb_version) AS latest_created_at,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted
                {aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND start_time >= %(slice_start)s
              AND start_time < %(slice_end)s
            GROUP BY id
        )
        WHERE latest_is_deleted = 0
          {predicate_fragment}
          {project_version_fragment}
          {keyset_fragment}
        ORDER BY latest_start_time DESC, grouped_id DESC
        LIMIT %(limit)s
        """
        return query, dict(self.params)

    def build_latest_attribute_list_ids(
        self,
        *,
        slice_start: Any,
        slice_end: Any,
        limit: int,
        before_start_time: Any = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return an exact, skinny newest-first list prefix.

        Only the latest ordering timestamp, creation timestamp, tombstone, and
        requested scalar attributes are retained per id. ``created_at`` keeps
        the page-bounded eval/annotation reads partition-pruned; every display
        and content column is point-hydrated after the bounded prefix has been
        selected, instead of retaining sixteen aggregate states for every id
        in a high-volume time slice.
        """
        if not self.supports_latest_attribute_page():
            raise ValueError("latest scalar span page does not support this request")
        if int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_id is None):
            raise ValueError(
                "before_start_time and before_id must be provided together"
            )

        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "start_date": request_start,
            "end_date": request_end,
            "slice_start": slice_start,
            "slice_end": slice_end,
            "limit": int(limit),
        }
        plans = [
            build_latest_span_probe_predicate(item, index=index)
            for index, item in enumerate(
                item
                for item in self.filters
                if (item.get("column_id") or item.get("columnId"))
                not in {"created_at", "start_time"}
            )
        ]
        for plan in plans:
            params.update(plan.params)

        aggregates = [aggregate for plan in plans for aggregate in plan.aggregates]
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = str(self.project_version_id)
            aggregates.append(
                "argMax(tuple(project_version_id), _peerdb_version).1 "
                "AS latest_project_version_id"
            )
            project_version_fragment = (
                " AND latest_project_version_id = %(project_version_id)s"
            )
        aggregate_fragment = (
            ",\n                    " + ",\n                    ".join(aggregates)
            if aggregates
            else ""
        )
        predicate_fragment = (
            " AND " + " AND ".join(plan.predicate for plan in plans) if plans else ""
        )
        keyset_fragment = ""
        if before_start_time is not None:
            params["keyset_start_time"] = before_start_time
            params["keyset_id"] = str(before_id)
            keyset_fragment = """
              AND (
                  latest_start_time < %(keyset_start_time)s
                  OR (
                      latest_start_time = %(keyset_start_time)s
                      AND grouped_id < %(keyset_id)s
                  )
              )
            """

        query = f"""
        SELECT
            grouped_id AS id,
            latest_start_time AS start_time,
            latest_created_at AS created_at
        FROM (
            SELECT
                id AS grouped_id,
                argMax(tuple(start_time), _peerdb_version).1 AS latest_start_time,
                argMax(created_at, _peerdb_version) AS latest_created_at,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted
                {aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND start_time >= %(slice_start)s
              AND start_time < %(slice_end)s
            GROUP BY id
        )
        WHERE latest_is_deleted = 0
          {predicate_fragment}
          {project_version_fragment}
          {keyset_fragment}
        ORDER BY latest_start_time DESC, grouped_id DESC
        LIMIT %(limit)s
        """
        return query, params

    def build_latest_attribute_candidate_seed_page(
        self,
        *,
        slice_start: Any,
        slice_end: Any,
        limit: int,
        before_start_time: Any = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a physical candidate page for bounded list verification.

        This query establishes only identity and an upper-bound ordering key.
        Attribute predicates are applied to physical versions as a safe
        candidate prefilter: every latest matching span necessarily has a
        matching physical version, while an older stale match remains only a
        false-positive. It deliberately does not evaluate tombstones or trust
        the physical match as final. Callers must mark every seeded id seen,
        classify it inside the slice, and prove whether physical history exists
        outside that slice. Cross-slice ids must then be classified across the
        complete request window so a newer key clear, non-match, or tombstone
        rejects an older raw match.
        """

        if not self.supports_latest_candidate_page():
            raise ValueError("latest scalar span seed does not support this request")
        if int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_id is None):
            raise ValueError(
                "before_start_time and before_id must be provided together"
            )

        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)

        def _without_timezone(value):
            return (
                value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value
            )

        slice_start = _without_timezone(slice_start)
        slice_end = _without_timezone(slice_end)
        if slice_start >= slice_end:
            raise ValueError("slice_start must be before slice_end")
        if slice_start < request_start or slice_end > request_end:
            raise ValueError("candidate slice must stay inside request window")

        params: dict[str, Any] = {
            **self.params,
            "start_date": request_start,
            "end_date": request_end,
            "candidate_slice_start": slice_start,
            "candidate_slice_end": slice_end,
            "candidate_seed_limit": int(limit),
        }
        attribute_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
            and is_latest_span_probe_filter(item)
        ]
        filter_fragment = ""
        if attribute_filters:
            fb = self._FILTER_BUILDER_CLS(
                table=self.TABLE,
                query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
                project_id=self.project_id,
                project_ids=self.project_ids,
            )
            physical_where, physical_params = fb.translate(attribute_filters)
            params.update(physical_params)
            if physical_where:
                filter_fragment = f"AND ({physical_where})"
        keyset_fragment = ""
        if before_start_time is not None:
            before_start_time = _without_timezone(before_start_time)
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("candidate keyset must stay inside its slice")
            params["candidate_before_start_time"] = before_start_time
            params["candidate_before_id"] = str(before_id)
            keyset_fragment = """
              AND (
                  start_time < %(candidate_before_start_time)s
                  OR (
                      start_time = %(candidate_before_start_time)s
                      AND id < %(candidate_before_id)s
                  )
              )
            """

        query = f"""
        SELECT
            id,
            start_time
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND start_time >= %(candidate_slice_start)s
          AND start_time < %(candidate_slice_end)s
          {filter_fragment}
          {keyset_fragment}
        ORDER BY start_time DESC, id DESC
        LIMIT %(candidate_seed_limit)s
        """
        return query, params

    def build_latest_attribute_candidate_matches(
        self,
        candidate_span_ids: list[str],
        *,
        window_start: Any = None,
        window_end: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Classify bounded candidates against latest state in one window.

        By default the window is the complete original request. Callers may
        provide a contained local slice and separately prove that an accepted
        id has no physical history outside it. Each candidate is reduced once
        before tombstone, project-version, and attribute predicates are
        applied, so the returned ordering key is canonical for that window.
        """

        if not self.supports_latest_candidate_page():
            raise ValueError(
                "latest scalar span classifier does not support this request"
            )
        bounded_ids = list(
            dict.fromkeys(str(span_id) for span_id in candidate_span_ids if span_id)
        )
        if not bounded_ids:
            return "", {}

        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)
        classifier_start = request_start if window_start is None else window_start
        classifier_end = request_end if window_end is None else window_end
        if getattr(classifier_start, "tzinfo", None) is not None:
            classifier_start = classifier_start.replace(tzinfo=None)
        if getattr(classifier_end, "tzinfo", None) is not None:
            classifier_end = classifier_end.replace(tzinfo=None)
        if (
            classifier_start < request_start
            or classifier_end > request_end
            or classifier_start >= classifier_end
        ):
            raise ValueError("candidate classifier must stay inside request window")
        params: dict[str, Any] = {
            **self.params,
            "start_date": classifier_start,
            "end_date": classifier_end,
            "candidate_span_ids": tuple(bounded_ids),
        }
        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        scalar_filters = [
            item for item in active_filters if is_latest_span_probe_filter(item)
        ]
        external_filters = [
            item for item in active_filters if _is_candidate_external_span_filter(item)
        ]
        plans = [
            build_latest_span_probe_predicate(item, index=index)
            for index, item in enumerate(scalar_filters)
        ]
        for plan in plans:
            params.update(plan.params)
        aggregates = [aggregate for plan in plans for aggregate in plan.aggregates]
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = str(self.project_version_id)
            aggregates.append(
                "argMax(tuple(project_version_id), _peerdb_version).1 "
                "AS latest_project_version_id"
            )
            project_version_fragment = (
                " AND latest_project_version_id = %(project_version_id)s"
            )
        aggregate_fragment = (
            ",\n                    " + ",\n                    ".join(aggregates)
            if aggregates
            else ""
        )
        predicate_fragment = (
            " AND " + " AND ".join(plan.predicate for plan in plans) if plans else ""
        )
        external_predicate = ""
        if external_filters:
            filter_builder = self._FILTER_BUILDER_CLS(
                table=self.TABLE,
                annotation_label_ids=self.annotation_label_ids,
                query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
                project_id=self.project_id,
                project_ids=self.project_ids,
                score_date_scope=True,
                span_date_scope=True,
                span_latest_state=True,
                candidate_entity_scope=True,
            )
            try:
                external_predicate, external_params = filter_builder.translate(
                    external_filters
                )
            except (TypeError, ValueError) as exc:
                raise UnsupportedFilterShapeError(
                    "unsupported span filter shape"
                ) from exc
            params.update(external_params)
        external_fragment = f"WHERE {external_predicate}" if external_predicate else ""

        query = f"""
        SELECT
            id,
            start_time,
            created_at
        FROM (
            SELECT
                grouped_id AS id,
                latest_start_time AS start_time,
                latest_created_at AS created_at
            FROM (
                SELECT
                    id AS grouped_id,
                    argMax(tuple(start_time), _peerdb_version).1
                        AS latest_start_time,
                    argMax(created_at, _peerdb_version) AS latest_created_at,
                    argMax(is_deleted, _peerdb_version) AS latest_is_deleted
                    {aggregate_fragment}
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND id IN %(candidate_span_ids)s
                  AND start_time >= %(start_date)s
                  AND start_time < %(end_date)s
                GROUP BY id
            )
            WHERE latest_is_deleted = 0
              {predicate_fragment}
              {project_version_fragment}
        )
        {external_fragment}
        ORDER BY start_time DESC, id DESC
        LIMIT {len(bounded_ids)}
        """
        return query, params

    def build_cross_slice_candidate_ids(
        self,
        candidate_span_ids: list[str],
        *,
        slice_start: Any,
        slice_end: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Find candidates with physical history outside one classified slice."""
        bounded_ids = list(
            dict.fromkeys(str(span_id) for span_id in candidate_span_ids if span_id)
        )
        if not bounded_ids:
            return "", {}
        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)

        def _without_timezone(value):
            return (
                value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value
            )

        slice_start = _without_timezone(slice_start)
        slice_end = _without_timezone(slice_end)
        if (
            slice_start < request_start
            or slice_end > request_end
            or slice_start >= slice_end
        ):
            raise ValueError("cross-slice probe must stay inside request window")
        params: dict[str, Any] = {
            **self.params,
            "cross_slice_span_ids": tuple(bounded_ids),
            "cross_slice_request_start": request_start,
            "cross_slice_request_end": request_end,
            "cross_slice_start": slice_start,
            "cross_slice_end": slice_end,
            "cross_slice_limit": len(bounded_ids),
        }
        query = f"""
        SELECT DISTINCT id
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND id IN %(cross_slice_span_ids)s
          AND start_time >= %(cross_slice_request_start)s
          AND start_time < %(cross_slice_request_end)s
        WHERE start_time < %(cross_slice_start)s
           OR start_time >= %(cross_slice_end)s
        LIMIT %(cross_slice_limit)s
        """
        return query, params

    def build_latest_attribute_id_page(
        self,
        *,
        slice_start: Any,
        slice_end: Any,
        limit: int | None,
        sampling_salt: str | None = None,
        sampling_rate: float | None = None,
        exclude_span_ids: list[str] | tuple[str, ...] | set[str] | None = None,
        after_span_id: str | None = None,
        changed_since_version: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return exact latest-state span IDs without a table ``FINAL``.

        This ID-only form is shared by eval-row materialization and filtered
        task creation.  Sampling, exclusions, and the keyset are all applied
        after per-span latest-version/tombstone resolution but before LIMIT,
        so they cannot consume the requested cap or resurrect stale matches.
        """

        if not self.supports_latest_attribute_page():
            raise ValueError("latest scalar span id page does not support this request")
        if limit is None and changed_since_version is None:
            raise ValueError(
                "unbounded latest scalar span ids require a version watermark"
            )
        if limit is not None and int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if getattr(slice_start, "tzinfo", None) is not None:
            slice_start = slice_start.replace(tzinfo=None)
        if getattr(slice_end, "tzinfo", None) is not None:
            slice_end = slice_end.replace(tzinfo=None)
        if slice_start >= slice_end:
            raise ValueError("slice_start must be before slice_end")
        if (sampling_salt is None) != (sampling_rate is None):
            raise ValueError(
                "sampling_salt and sampling_rate must be provided together"
            )

        request_start = self.params.get("start_date")
        request_end = self.params.get("end_date")
        if request_start is None or request_end is None:
            request_start, request_end = self.parse_time_range(self.filters)
        if slice_start < request_start or slice_end > request_end:
            raise ValueError(
                "latest scalar span id page must stay inside request window"
            )

        params: dict[str, Any] = {
            **self.params,
            "start_date": request_start,
            "end_date": request_end,
            "latest_span_slice_start": slice_start,
            "latest_span_slice_end": slice_end,
        }
        if limit is not None:
            params["latest_span_limit"] = int(limit)
        plans = [
            build_latest_span_probe_predicate(item, index=index)
            for index, item in enumerate(
                item
                for item in self.filters
                if (item.get("column_id") or item.get("columnId"))
                not in {"created_at", "start_time"}
            )
        ]
        for plan in plans:
            params.update(plan.params)

        aggregates = [aggregate for plan in plans for aggregate in plan.aggregates]
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = str(self.project_version_id)
            aggregates.append(
                "argMax(tuple(project_version_id), _peerdb_version).1 "
                "AS latest_project_version_id"
            )
            project_version_fragment = (
                " AND latest_project_version_id = %(project_version_id)s"
            )
        aggregate_fragment = (
            ",\n                    " + ",\n                    ".join(aggregates)
            if aggregates
            else ""
        )
        predicate_fragment = (
            " AND " + " AND ".join(plan.predicate for plan in plans) if plans else ""
        )

        sampling_fragment = ""
        if sampling_rate is not None:
            rate = float(sampling_rate)
            if not 0 <= rate <= 100:
                raise ValueError("sampling_rate must be between 0 and 100")
            params["latest_span_sampling_salt"] = str(sampling_salt)
            params["latest_span_sampling_rate"] = rate
            sampling_fragment = (
                " AND modulo(cityHash64(%(latest_span_sampling_salt)s, "
                "toString(grouped_id)), 100) < %(latest_span_sampling_rate)s"
            )

        excluded = tuple(
            sorted(str(value) for value in (exclude_span_ids or ()) if value)
        )
        exclusion_fragment = ""
        if excluded:
            params["latest_span_excluded_ids"] = excluded
            exclusion_fragment = " AND grouped_id NOT IN %(latest_span_excluded_ids)s"

        keyset_fragment = ""
        if after_span_id is not None:
            if limit is None:
                raise ValueError("span-id keyset requires a bounded page")
            if (slice_end - slice_start).total_seconds() > 60:
                raise ValueError("span-id-only keyset requires a one-minute slice")
            params["latest_span_after_id"] = str(after_span_id)
            keyset_fragment = " AND grouped_id > %(latest_span_after_id)s"

        changed_candidate_fragment = ""
        if changed_since_version is not None:
            if int(changed_since_version) < 0:
                raise ValueError("changed_since_version must be non-negative")
            params["latest_span_changed_since_version"] = int(changed_since_version)
            # Continuous tasks advance a write-version watermark. First identify
            # ids with a new physical version, then classify those ids against
            # every version in the task's start-time window. This catches a late
            # update/tombstone without resurrecting an older matching value.
            changed_candidate_fragment = f"""
              AND id IN (
                  SELECT id
                  FROM {self.TABLE}
                  PREWHERE {self.project_filter_sql()}
                    AND start_time >= %(latest_span_slice_start)s
                    AND start_time < %(latest_span_slice_end)s
                    AND _peerdb_version >= %(latest_span_changed_since_version)s
                  GROUP BY id
              )
            """

        order_limit_fragment = ""
        if limit is not None:
            order_limit_fragment = """
        ORDER BY toStartOfMinute(latest_start_time) DESC, grouped_id ASC
        LIMIT %(latest_span_limit)s
            """

        query = f"""
        SELECT
            grouped_id AS id,
            latest_start_time AS eval_order_start_time
        FROM (
            SELECT
                id AS grouped_id,
                argMax(tuple(start_time), _peerdb_version).1 AS latest_start_time,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
                {aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND start_time >= %(latest_span_slice_start)s
              AND start_time < %(latest_span_slice_end)s
              {changed_candidate_fragment}
            GROUP BY id
        )
        WHERE latest_is_deleted = 0
          {predicate_fragment}
          {project_version_fragment}
          {sampling_fragment}
          {exclusion_fragment}
          {keyset_fragment}
        {order_limit_fragment}
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a count query for total matching spans.

        PERF: uses ``count()`` rather than ``uniqExact(id)``. ``uniqExact`` built
        an exact hash set of every matching span id (tens of millions of 16-char
        strings) — hundreds of MB to GBs of unbounded memory that OOM-crashed the
        server on large windows. ``count()`` reads only the filter columns and
        needs O(1) memory. The pagination total is a display value; the only
        difference is that a transient un-merged ReplacingMergeTree duplicate is
        counted once extra, which is immaterial and self-heals on the next merge
        (and matches the list, which no longer de-dups via ``LIMIT 1 BY id``).
        """
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(self.filters)
        params = dict(self.params)
        params.pop("slice_start", None)
        params.pop("slice_end", None)
        params.pop("keyset_start_time", None)
        params.pop("keyset_id", None)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        end_user_fragment = ""
        if self.end_user_id:
            end_user_fragment = "AND end_user_id = %(end_user_id)s"
            params["end_user_id"] = self.end_user_id

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            params["project_version_id"] = self.project_version_id

        # P3b step1.5 id-remap resolution (DESIGN §3 / id_remap_sql): MUST mirror
        # `build()` exactly — resolve `end_user_id` new→old and count on the
        # resolved id, else a straddler's count splits from the list and
        # has_more/pagination lies. Non-user predicates stay on the bare inner
        # scan; pre-flip a byte-identical no-op (gate B). Gated on
        # `self.end_user_id` like `build()`.
        if self.end_user_id:
            remap_join = remap_left_join("rs.end_user_id", "end_user_id_remap")
            resolved_eu = resolved_id_expr("rs.end_user_id")
            query = f"""
            SELECT count() AS total
            FROM (
                SELECT rs.id AS id, {resolved_eu} AS resolved_end_user_id
                FROM (
                    SELECT id, end_user_id
                    FROM {self.TABLE}
                    {self.project_where()}
                      AND created_at >= %(start_date)s - INTERVAL 1 DAY
                      AND start_time >= %(start_date)s
                      AND start_time < %(end_date)s
                      {pv_fragment}
                      {filter_fragment}
                ) AS rs
                {remap_join}
            )
            WHERE resolved_end_user_id = %(end_user_id)s
            """
            return query, params

        query = f"""
        SELECT count() AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {end_user_fragment}
          {pv_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 2: Eval scores for a set of span IDs
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        span_ids: list[str],
        created_after: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-2 eval-scores query for a page of span IDs.

        Args:
            created_after: Optional datetime lower bound for partition pruning.
                The eval table is ``PARTITION BY toYYYYMM(created_at)``; without
                a ``created_at`` predicate the span-id probe touches EVERY
                monthly partition. An eval row cannot be created before its span
                row exists, so bounding by the page's oldest ``created_at``
                (minus a 7-day safety margin) prunes to the relevant partitions
                — measured 55x fewer rows read at 10M eval rows.
        """
        if not span_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "span_ids": tuple(span_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }
        created_fragment = ""
        if created_after is not None:
            params["evals_created_after"] = created_after
            created_fragment = (
                "AND created_at >= %(evals_created_after)s - INTERVAL 7 DAY"
            )

        eval_table, eval_not_deleted = eval_logger_source(
            include_cdc_tombstone_guard=True
        )
        # ReplacingMergeTree version column: v2 uses `_version`, the legacy CDC
        # mirror uses `_peerdb_version`. Used to keep the newest row per eval id
        # when de-duplicating without FINAL (see the FROM clause below).
        eval_version_col = (
            "_version" if eval_table.endswith("_v2") else "_peerdb_version"
        )

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score or
        # masquerades as a real value. The per-status counts let the pivot pick
        # one cell state per (span, config) by the precedence
        # completed > errored > skipped > running > pending.
        # ``success_count`` excludes the non-terminal / skipped / errored
        # states via ``status NOT IN (...)``: a bare ``error = 0`` guard also
        # matches pending/running/skipped rows (they carry ``error = 0`` and a
        # NULL output), which would collapse the pivot's "is there a real
        # score?" test. A NOT-IN (rather than ``status = 'completed'``) keeps
        # legacy rows whose mirrored ``status`` is empty/NULL counted as
        # completed, so historical scores don't blank out.
        # ``str_lists`` keeps every completed ``output_str_list`` so the pivot
        # can compute per-choice percentages for CHOICES evals (column shape:
        # ``{config_id}**{choice}``).
        # ``output_str`` is Nullable(String); ClickHouse 3-valued logic makes
        # ``NULL != 'ERROR'`` NULL (not TRUE), so use ``ifNull(...)`` to keep
        # the comparison NULL-safe.
        query = f"""
        SELECT
            observation_span_id,
            toString(custom_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN, which
            -- json.dumps(allow_nan=False) rejects. NULL serializes as null.
            ifNotFinite(avgIf(
                output_float,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS avg_score,
            ifNotFinite(avgIf(
                CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS pass_rate,
            countIf(
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS success_count,
            countIf(
                error = 1 OR ifNull(output_str, '') = 'ERROR' OR status = 'errored'
            ) AS error_count,
            countIf(status = 'skipped') AS skipped_count,
            countIf(status = 'running') AS running_count,
            countIf(status = 'pending') AS pending_count,
            anyIf(skipped_reason, status = 'skipped') AS skipped_reason,
            count() AS eval_count,
            groupArrayIf(
                output_str_list,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS str_lists
        -- PERF: no table-level FINAL. FINAL forces a merge across the WHOLE eval
        -- table before the WHERE is applied, so a page of ~50 span ids dragged a
        -- merge over tens of millions of rows — GBs of memory that OOM-crashed
        -- the server. Instead we de-dup only the tiny page-scoped slice: the
        -- inner scan is pruned to the page's span ids (idx_observation_span_id
        -- bloom) + the config ids, then ORDER BY the version col DESC + LIMIT 1
        -- BY id keeps the newest version of each eval row — verified identical
        -- to FINAL for live rows (status transitions collapse to the newest
        -- version), at O(rows-for-this-page) cost. One accepted divergence:
        -- the not-deleted WHERE runs BEFORE dedup, so an eval whose newest
        -- un-merged version is a soft-delete marker transiently surfaces its
        -- previous version until the next merge collapses the parts (FINAL
        -- merged first and hid it immediately).
        FROM (
            SELECT
                observation_span_id,
                custom_eval_config_id,
                output_float,
                output_bool,
                output_str,
                output_str_list,
                error,
                status,
                skipped_reason
            FROM {eval_table}
            WHERE {eval_not_deleted}
              AND observation_span_id IN %(span_ids)s
              AND custom_eval_config_id IN %(eval_config_ids)s
              {created_fragment}
            ORDER BY {eval_version_col} DESC
            LIMIT 1 BY id
        )
        GROUP BY observation_span_id, custom_eval_config_id
        SETTINGS max_bytes_before_external_group_by = 1073741824, max_bytes_before_external_sort = 1073741824
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of span IDs
    # ------------------------------------------------------------------

    def build_annotation_query(
        self,
        span_ids: list[str],
        created_after: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-3 annotation query for a page of span IDs.

        Args:
            created_after: Optional datetime lower bound — same partition-prune
                rationale as ``build_eval_query`` (``model_hub_score`` is also
                ``PARTITION BY toYYYYMM(created_at)``; a score row cannot
                pre-date its span row).
        """
        if not span_ids or not self.annotation_label_ids:
            return "", {}

        params: dict[str, Any] = {
            "span_ids": tuple(span_ids),
            "label_ids": tuple(self.annotation_label_ids),
        }
        created_fragment = ""
        if created_after is not None:
            params["anns_created_after"] = created_after
            created_fragment = (
                "AND created_at >= %(anns_created_after)s - INTERVAL 7 DAY"
            )

        # PERF: no table-level FINAL (same OOM risk as the eval phase — FINAL
        # merges the whole model_hub_score table before the page filter). De-dup
        # only the page-scoped slice: prune to the page's span ids + labels, keep
        # the newest version per score id via `ORDER BY _peerdb_version DESC
        # LIMIT 1 BY id`, then `anyLast(value)` per (span, label).
        query = f"""
        SELECT
            observation_span_id,
            toString(label_id) AS label_id,
            anyLast(value) AS value
        FROM (
            SELECT observation_span_id, label_id, value
            FROM {self.ANNOTATION_TABLE}
            WHERE _peerdb_is_deleted = 0
              AND deleted = false
              AND observation_span_id IN %(span_ids)s
              AND label_id IN %(label_ids)s
              {created_fragment}
            ORDER BY _peerdb_version DESC
            LIMIT 1 BY id
        )
        GROUP BY observation_span_id, label_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    @staticmethod
    def pivot_eval_results(
        eval_rows: list[dict],
    ) -> dict[str, dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by span_id.

        Returns:
            ``{span_id: {eval_config_id: cell_value}}``. The value is a number
            for completed evals, ``{"error": True}`` when all rows errored, or a
            ``{"status": "skipped"|"running"|"pending"}`` marker (with
            ``skipped_reason`` when skipped) when the (span, config) pair has no
            completed result yet. For CHOICES evals (non-empty ``str_lists``) the
            value is a ``{choice: pct}`` dict the caller spreads into
            ``{config_id}**{choice}`` keys.
        """
        import json as _json

        result: dict[str, dict[str, Any]] = {}
        for row in eval_rows:
            span_id = str(row.get("observation_span_id", ""))
            config_id = str(row.get("eval_config_id", ""))
            avg_score = row.get("avg_score")
            pass_rate = row.get("pass_rate")
            success_count = row.get("success_count", 0) or 0
            error_count = row.get("error_count", 0) or 0
            str_lists = row.get("str_lists") or []

            # All rows errored — surface an explicit error marker so the
            # UI can render an error state (distinct from "no eval run").
            if success_count == 0 and error_count > 0:
                result.setdefault(span_id, {})[config_id] = {"error": True}
                continue

            # CHOICES eval: compute per-choice percentage across all
            # non-errored eval rows for this (span, config) pair.
            #
            # ClickHouse stores ``output_str_list`` as ``String DEFAULT '[]'``,
            # so non-CHOICES evals (Pass/Fail, score) come back as the string
            # ``'[]'`` — truthy, slipping past the ``if not sl`` guard. Only
            # treat entries with actual choice values as CHOICES data; empty
            # inner lists must fall through to ``avg_score``/``pass_rate``.
            parsed = []
            for sl in str_lists:
                if not sl:
                    continue
                if isinstance(sl, list):
                    if sl:
                        parsed.append([str(x) for x in sl])
                elif isinstance(sl, str) and sl.startswith("["):
                    try:
                        p = _json.loads(sl)
                        if isinstance(p, list) and p:
                            parsed.append([str(x) for x in p])
                    except _json.JSONDecodeError:
                        continue
            if parsed:
                total = len(parsed)
                counts: dict[str, int] = {}
                for lst in parsed:
                    for choice in set(lst):
                        counts[choice] = counts.get(choice, 0) + 1
                per_choice = {k: round(100.0 * v / total, 2) for k, v in counts.items()}
                result.setdefault(span_id, {})[config_id] = per_choice
                continue

            # Determine the score value matching PG format
            if avg_score is not None and avg_score != 0:
                score = round(avg_score * 100, 2)
            elif pass_rate is not None:
                score = round(pass_rate, 2)
            else:
                score = None

            # No completed score: surface a non-terminal / skipped lifecycle
            # marker (skipped > running > pending) so the cell renders a
            # loading/pending/skipped state instead of a misleading blank.
            if score is None:
                result.setdefault(span_id, {})[config_id] = non_terminal_eval_marker(
                    row
                )
            else:
                result.setdefault(span_id, {})[config_id] = score

        return result

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: list[dict],
        label_types: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Pivot annotation query results into a nested dict keyed by span_id.

        Args:
            annotation_rows: Rows from the Phase-3 query.
            label_types: Optional mapping of label_id -> annotation type
                (NUMERIC, STAR, THUMBS_UP_DOWN, CATEGORICAL).

        Returns:
            ``{span_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: dict[str, dict[str, Any]] = {}
        for row in annotation_rows:
            span_id = str(row.get("observation_span_id", ""))
            label_id = str(row.get("label_id", ""))
            label_type = label_types.get(label_id, "").lower()

            raw_val = row.get("value", "{}")
            if isinstance(raw_val, str):
                try:
                    val = json.loads(raw_val)
                except (json.JSONDecodeError, TypeError):
                    val = {}
            else:
                val = raw_val if isinstance(raw_val, dict) else {}

            if label_type in ("numeric", "star"):
                value_key = "value" if label_type == "numeric" else "rating"
                value = val.get(value_key) if isinstance(val, dict) else val
            elif label_type == "thumbs_up_down":
                thumb_val = val.get("value") if isinstance(val, dict) else val
                value = thumb_val in (True, "up", 1, "true")
            elif label_type == "categorical":
                value = val.get("selected", []) if isinstance(val, dict) else val
            elif label_type == "text":
                value = val.get("text", val) if isinstance(val, dict) else val
            else:
                value = val

            result.setdefault(span_id, {})[label_id] = value

        return result

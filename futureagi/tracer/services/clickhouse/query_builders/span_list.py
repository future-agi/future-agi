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
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


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

    def build(self, since: Any = None) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated span data.

        Args:
            since: Optional narrowed window start (datetime) for progressive
                time-slice pagination. When set, an additional
                ``start_time >= %(slice_start)s`` predicate restricts the scan
                to the newest slice — because the sort is ``start_time DESC``,
                every row in a newer slice sorts before every older row, so a
                slice that yields the full prefix IS the global prefix and the
                caller can stop without scanning the whole window. The regular
                ``start_date``/``end_date`` params are left untouched so
                ``build_count_query()`` still counts the full window.
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

        slice_fragment = ""
        if since is not None:
            self.params["slice_start"] = since
            slice_fragment = "AND start_time >= %(slice_start)s"

        # Prefix-fetch pagination: read the sorted prefix [0, offset +
        # 2*page_size) in ONE bounded top-K pass and let the view dedup by
        # span id then slice [offset, offset + page_size) — see
        # tracer/services/clickhouse/page_dedup.py. This preserves the
        # global-dedup semantics `LIMIT 1 BY id` provided (a key can never
        # appear on two pages) without its O(window) full sort; the 2x
        # page_size margin keeps pages exact for up to page_size duplicate
        # rows in the prefix. No SQL OFFSET — slicing happens in Python.
        offset = self.page_number * self.page_size
        self.params["limit"] = offset + 2 * self.page_size

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
            FROM {self.TABLE}
            {self.project_where()}
              AND created_at >= %(start_date)s - INTERVAL 1 DAY
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {slice_fragment}
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
            return query, self.params

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
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {slice_fragment}
          {end_user_fragment}
          {pv_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT %(limit)s
        """
        return query, self.params

    def build_id_query(self, *, limit: int | None = None) -> tuple[str, dict[str, Any]]:
        """Filtered span ids only — same filter/time window as build(), no pivots.

        With ``limit=None`` (default) the full matching id set is returned
        unordered, which is what the eval resolver wants. Pass ``limit`` for a
        capped "select all matching" resolve: the ids are then ordered
        newest-first (``start_time DESC, id DESC`` — same as the observe list and
        the PG bulk-select) so the cap keeps the most recent rows deterministically.
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
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        order_fragment = ""
        limit_fragment = ""
        if limit is not None:
            order_fragment = "ORDER BY start_time DESC, id DESC"
            limit_fragment = "LIMIT %(id_limit)s"
            self.params["id_limit"] = int(limit)

        query = f"""
        SELECT id
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {pv_fragment}
          {filter_fragment}
        {order_fragment}
        LIMIT 1 BY id
        {limit_fragment}
        """
        return query, self.params

    def build_content_query(self, span_ids: list) -> tuple[str, dict[str, Any]]:
        """Fetch input/output + typed attr maps for a page of span IDs."""
        if not span_ids:
            return "", {}
        params = {**self.params, "content_span_ids": tuple(span_ids)}
        query = f"""
        SELECT id, input, output, attributes_extra,
               span_attr_str AS attrs_string,
               span_attr_num AS attrs_number,
               span_attr_bool AS attrs_bool
        FROM {self.TABLE}
        PREWHERE id IN %(content_span_ids)s
        WHERE {self.project_filter_sql()} AND is_deleted = 0
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

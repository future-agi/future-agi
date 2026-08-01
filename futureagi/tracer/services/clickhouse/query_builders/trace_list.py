"""
Trace List Query Builder for ClickHouse.

Replaces the ``list_traces()`` method in ``tracer.views.trace`` with a
two-phase ClickHouse query strategy:

Phase 1 -- Paginated trace IDs + root span data from the denormalized
``spans`` table (``WHERE parent_span_id IS NULL``).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for those
trace IDs, grouped by ``(trace_id, custom_eval_config_id)``.

The two result sets are merged in Python.
"""

import math
from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.eval_status import (
    non_terminal_eval_marker,
)
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    UnsupportedFilterShapeError,
    build_literal_text_predicate,
)
from tracer.services.clickhouse.query_builders.latest_attributes import (
    build_latest_trace_probe_predicate,
    is_latest_trace_probe_filter,
    is_latest_trace_root_probe_filter,
)

# On the v2 schema (PARTITION BY toDate(start_time), PK on toStartOfHour(
# start_time)) start_time prunes partitions and the PK; created_at prunes
# nothing and scans the whole project.
TIME_FILTER_COLUMN = "start_time"  # Options: "created_at" | "start_time"

_CANDIDATE_EXTERNAL_TRACE_COLUMNS = frozenset(
    {
        "tag",
        "tags",
        "my_annotations",
        "annotator",
        "has_annotation",
        "has_eval",
    }
)


def _is_candidate_external_trace_filter(item: dict[str, Any]) -> bool:
    """Whether a non-scalar filter is exact after trace-id candidate scoping."""

    column_id = str(item.get("column_id") or item.get("columnId") or "")
    config = item.get("filter_config") or item.get("filterConfig") or {}
    col_type = str(config.get("col_type") or config.get("colType") or "").upper()
    return column_id in _CANDIDATE_EXTERNAL_TRACE_COLUMNS or col_type in {
        "EVAL_METRIC",
        "ANNOTATION",
        "SYSTEM_METRIC",
        "TRACE_END_USER",
    }


def _literal_trace_name_search(params: dict[str, Any], search: str | None) -> str:
    """Return a bound literal contains predicate for trace-name search."""

    if not search:
        return ""
    params["search"] = str(search)
    predicate = build_literal_text_predicate(
        "trace_name",
        "search",
        "contains",
        case_insensitive=True,
    )
    return f"AND {predicate}"


class TraceListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated trace list view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of traces per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        eval_config_ids: List of ``CustomEvalConfig`` UUID strings to
            fetch eval scores for.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    # The legacy table admitted both NULL and the empty string for a root.
    # CH25 overrides this with the physical, non-nullable representation.  The
    # candidate seed deliberately reads only this predicate plus project/time:
    # it is a cheap superset whose mutable/deleted rows are revalidated by the
    # point-scoped latest-state query before anything reaches the response.
    ROOT_SEED_PARENT_PREDICATE = "(parent_span_id IS NULL OR parent_span_id = '')"
    # Filter compiler class; the v2 list builder overrides this to the v2
    # builder so it reads the v2 dimension tables (end_users, etc.).
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilder

    # Mapping from sort column names the frontend sends to actual
    # ClickHouse column names on the root span.
    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "start_time",
        "start_time": "start_time",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "cost": "cost",
        "total_tokens": "total_tokens",
        "name": "trace_name",
        "trace_name": "trace_name",
        "status": "status",
    }

    # All available light columns for configurable column selection.
    AVAILABLE_COLUMNS: list[str] = [
        "trace_id",
        "trace_name",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "trace_session_id",
        "project_id",
    ]

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        project_version_id: str | None = None,
        search: str | None = None,
        columns: list[str] | None = None,
        annotation_label_ids: list[str] | None = None,
        candidate_trace_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.eval_config_ids = eval_config_ids or []
        self.project_version_id = project_version_id
        self.search = search.strip() if search else None
        self.columns = columns
        self.annotation_label_ids = annotation_label_ids or []
        self.candidate_trace_ids = [
            str(trace_id) for trace_id in (candidate_trace_ids or []) if trace_id
        ]
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None

    def _span_time_window(
        self, params: dict[str, Any], column: str = "start_time"
    ) -> str:
        """Bound a page-scoped span probe to the request window ± 1 day.

        Page trace_ids come from the windowed page scan; every span of an
        in-window trace starts within the window ± max trace duration (prod
        max ≈ 5h « 1d). Empty when no build() ran (standalone callers).
        """
        if self.start_date is None:
            return ""
        params["start_date"] = self.start_date
        params["end_date"] = self.end_date
        return (
            f"AND {column} >= %(start_date)s - INTERVAL 1 DAY\n"
            f"          AND {column} < %(end_date)s + INTERVAL 1 DAY"
        )

    # ------------------------------------------------------------------
    # Phase 1: Paginated trace list
    # ------------------------------------------------------------------

    def build(
        self,
        since: datetime | None = None,
        *,
        before_start_time: datetime | None = None,
        before_trace_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated root-span trace data.

        ``since`` narrows only the page read to a recent prefix. Because the
        default order is newest-first, a slice that fills the requested prefix
        is also the exact global prefix; the caller can avoid scanning older
        partitions. Attribute-membership subqueries share ``start_date``, so
        they are narrowed with the outer root-span scan as well.

        Returns:
            A ``(query_string, params)`` tuple.  The query returns one row
            per trace with root-span metadata.
        """
        requested_start, self.end_date = self.parse_time_range(self.filters)
        self.start_date = (
            max(requested_start, since) if since is not None else requested_start
        )
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate attribute / metric filters
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
            # Candidate-batched filtered pages must apply the same trace-id
            # bound inside any-span membership subqueries. Without this, the
            # outer page is small but ClickHouse still builds a project-wide
            # raw attribute set before intersecting it.
            span_trace_id_scope=bool(self.candidate_trace_ids),
            span_latest_state=bool(self.candidate_trace_ids),
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        # Sorting
        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = "ORDER BY start_time DESC, trace_id DESC"

        # Prefix-fetch pagination: read the sorted prefix [0, offset +
        # 2*page_size) in ONE bounded top-K pass and let the view dedup by
        # trace id then slice [offset, offset + page_size) — see
        # tracer/services/clickhouse/page_dedup.py. Preserves the global
        # dedup `LIMIT 1 BY trace_id` provided (a trace — even a multi-root
        # one whose roots sort pages apart — can never appear on two pages)
        # without its O(window) full sort. No SQL OFFSET; slicing in Python.
        offset = self.page_number * self.page_size
        self.params["limit"] = offset + 2 * self.page_size

        # Build optional filter fragment
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        candidate_fragment = ""
        candidate_dedup_fragment = ""
        if self.candidate_trace_ids:
            self.params["candidate_trace_ids"] = tuple(self.candidate_trace_ids)
            candidate_fragment = "AND trace_id IN %(candidate_trace_ids)s"
            # Candidate probes contain at most 50 point-scoped trace IDs. It is
            # therefore safe to deduplicate root versions here, and necessary:
            # otherwise duplicate versions for one trace can consume the
            # bounded LIMIT and hide another matching candidate.
            candidate_dedup_fragment = "LIMIT 1 BY trace_id"

        if (before_start_time is None) != (before_trace_id is None):
            raise ValueError(
                "before_start_time and before_trace_id must be provided together"
            )
        keyset_fragment = ""
        if before_start_time is not None:
            self.params["keyset_start_time"] = before_start_time
            self.params["keyset_trace_id"] = str(before_trace_id)
            keyset_fragment = """
              AND (
                  start_time < %(keyset_start_time)s
                  OR (
                      start_time = %(keyset_start_time)s
                      AND trace_id < %(keyset_trace_id)s
                  )
              )
            """

        # Optional project_version_id filter (used by prototype tab)
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        # Search filter on trace_name
        search_fragment = _literal_trace_name_search(self.params, self.search)

        # Configurable columns — only SELECT requested columns.
        # trace_id is always included.
        if self.columns:
            valid = [c for c in self.columns if c in self.AVAILABLE_COLUMNS]
            if "trace_id" not in valid:
                valid.insert(0, "trace_id")
            # Alias 'name' to 'span_name' for backward compatibility
            select_cols = []
            for c in valid:
                if c == "name":
                    select_cols.append("name AS span_name")
                else:
                    select_cols.append(c)
            select_clause = ",\n            ".join(select_cols)
        else:
            select_clause = """trace_id,
            trace_name,
            name AS span_name,
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
            trace_session_id,
            project_id"""

        # Phase 1: light columns only (no input/output/attrs/metadata).
        # Heavy columns are fetched in build_content_query() for just the
        # returned trace_ids — avoids OOM on large tables.
        #
        # PERF: no `LIMIT 1 BY trace_id`. That clause deduped multi-root /
        # duplicate-version traces, but forced CH to read + full-sort EVERY
        # root span in the window before applying ORDER BY … LIMIT —
        # O(roots-in-window) memory that OOM-crashed the server at millions
        # of traces. Without it, `ORDER BY … LIMIT n` runs as a bounded
        # top-N (size-n heap, O(n) memory). Duplicate trace_ids on a page
        # (multi-root traces, un-merged ReplacingMergeTree versions) are
        # rare; the view dedups the returned page by trace_id in Python,
        # keeping the first occurrence — the same row `LIMIT 1 BY` kept.
        span_source = f"{self.TABLE} FINAL" if self.candidate_trace_ids else self.TABLE
        query = f"""
        SELECT
            {select_clause}
        FROM {span_source}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {candidate_fragment}
          {keyset_fragment}
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        {order_clause}
        {candidate_dedup_fragment}
        LIMIT %(limit)s
        """
        return query, self.params

    def build_root_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_trace_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a skinny, bounded root-candidate page.

        This is an ordering/identity seed, not a latest-state result.  It must
        remain compatible with the root projection, so it intentionally reads
        only physical project/root/delete/time columns and returns only
        ``trace_id`` plus ``start_time``.  ReplacingMergeTree versions can make
        the result a superset; callers must point-verify every candidate across
        the complete request window before returning it.

        Adjacent slices are traversed newest first.  A saturated slice resumes
        with the exact ``(start_time, trace_id)`` descending keyset, including a
        deterministic trace-id tie breaker.
        """

        if int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_trace_id is None):
            raise ValueError(
                "before_start_time and before_trace_id must be provided together"
            )

        def _without_timezone(value: datetime) -> datetime:
            return value.replace(tzinfo=None) if value.tzinfo is not None else value

        slice_start = _without_timezone(slice_start)
        slice_end = _without_timezone(slice_end)
        if slice_start >= slice_end:
            raise ValueError("slice_start must be before slice_end")

        if self.start_date is not None and self.end_date is not None:
            request_start, request_end = self.start_date, self.end_date
        else:
            request_start, request_end = self.parse_time_range(self.filters)
        if slice_start < request_start or slice_end > request_end:
            raise ValueError("root candidate slice must stay inside request window")

        self.start_date = request_start
        self.end_date = request_end
        self.params["start_date"] = request_start
        self.params["end_date"] = request_end
        params: dict[str, Any] = {
            **self.params,
            "root_seed_slice_start": slice_start,
            "root_seed_slice_end": slice_end,
            "root_seed_limit": int(limit),
        }

        keyset_fragment = ""
        if before_start_time is not None:
            before_start_time = _without_timezone(before_start_time)
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("root candidate keyset must stay inside its slice")
            params["root_seed_before_start_time"] = before_start_time
            params["root_seed_before_trace_id"] = str(before_trace_id)
            keyset_fragment = """
              AND (
                  start_time < %(root_seed_before_start_time)s
                  OR (
                      start_time = %(root_seed_before_start_time)s
                      AND trace_id < %(root_seed_before_trace_id)s
                  )
              )
            """

        query = f"""
        SELECT
            trace_id,
            start_time
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND {self.ROOT_SEED_PARENT_PREDICATE}
        WHERE start_time >= %(root_seed_slice_start)s
          AND start_time < %(root_seed_slice_end)s
          {keyset_fragment}
        ORDER BY start_time DESC, trace_id DESC
        LIMIT %(root_seed_limit)s
        """
        return query, params

    def build_filtered_root_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        filters: list[dict[str, Any]] | None = None,
        before_start_time: datetime | None = None,
        before_trace_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a bounded physical-row seed for canonical-root filters.

        This is deliberately a *superset* seed.  Predicates are evaluated on
        physical live root versions so a latest matching canonical root is
        guaranteed to appear, while stale matching versions may also appear.
        The caller must therefore reclassify every returned trace id across the
        complete request window before exposing it.  Applying the predicate at
        this cheap stage is nevertheless important for selective and no-match
        filters: the unfiltered root stream would otherwise have to classify
        every trace in the project merely to prove that no match exists.

        Only canonical-root predicates are accepted.  Any-span filters require
        the ordinary unfiltered root-order seed because child-span time is not
        a safe frontier for trace ordering.
        """

        if int(limit) <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_trace_id is None):
            raise ValueError(
                "before_start_time and before_trace_id must be provided together"
            )

        active_filters = [
            item
            for item in (filters if filters is not None else self.filters)
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        if not active_filters or not all(
            is_latest_trace_root_probe_filter(item) for item in active_filters
        ):
            raise ValueError("filtered root seed requires root-only predicates")

        def _without_timezone(value: datetime) -> datetime:
            return value.replace(tzinfo=None) if value.tzinfo is not None else value

        slice_start = _without_timezone(slice_start)
        slice_end = _without_timezone(slice_end)
        if slice_start >= slice_end:
            raise ValueError("slice_start must be before slice_end")

        # Reuse the request bounds parsed by the enclosing bounded-list
        # protocol.  Re-parsing an implicit ``now`` window here can advance the
        # lower bound by a few microseconds between the seed calculation and
        # this validation, incorrectly rejecting the first slice as outside
        # the request window.
        if self.start_date is not None and self.end_date is not None:
            request_start, request_end = self.start_date, self.end_date
        else:
            request_start, request_end = self.parse_time_range(self.filters)
        if slice_start < request_start or slice_end > request_end:
            raise ValueError("filtered root seed slice must stay inside request window")

        self.start_date = request_start
        self.end_date = request_end
        self.params["start_date"] = request_start
        self.params["end_date"] = request_end

        filter_builder = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            span_date_scope=True,
        )
        predicate, predicate_params = filter_builder.translate(active_filters)
        if not predicate:
            raise ValueError("filtered root seed requires a compiled predicate")

        params: dict[str, Any] = {
            **self.params,
            **predicate_params,
            "root_seed_slice_start": slice_start,
            "root_seed_slice_end": slice_end,
            "root_seed_limit": int(limit),
        }
        keyset_fragment = ""
        if before_start_time is not None:
            before_start_time = _without_timezone(before_start_time)
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("filtered root keyset must stay inside its slice")
            params["root_seed_before_start_time"] = before_start_time
            params["root_seed_before_trace_id"] = str(before_trace_id)
            keyset_fragment = """
              AND (
                  start_time < %(root_seed_before_start_time)s
                  OR (
                      start_time = %(root_seed_before_start_time)s
                      AND trace_id < %(root_seed_before_trace_id)s
                  )
              )
            """

        query = f"""
        SELECT
            trace_id,
            start_time
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND {self.ROOT_SEED_PARENT_PREDICATE}
          AND start_time >= %(root_seed_slice_start)s
          AND start_time < %(root_seed_slice_end)s
        WHERE {predicate}
          {keyset_fragment}
        ORDER BY start_time DESC, trace_id DESC
        LIMIT %(root_seed_limit)s
        """
        return query, params

    def build_latest_filter_match_query(
        self,
        candidate_trace_ids: list[str],
        *,
        filters: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Match point-scoped trace candidates against latest span versions.

        One scalar ``argMax`` state is retained for the requested Map element;
        no table-level ``FINAL`` and no full Map aggregation is involved. Each
        attribute filter is evaluated independently and the resulting trace-id
        sets are intersected, preserving the existing any-span semantics when
        several attributes may live on different child spans.
        """
        trace_ids = [str(trace_id) for trace_id in candidate_trace_ids if trace_id]
        if not trace_ids:
            return "", {}

        active_filters = [
            item
            for item in (filters if filters is not None else self.filters)
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        if not active_filters:
            return "", {}
        if not self.supports_latest_filter_match(active_filters):
            raise ValueError("unsupported latest-state trace probe filter")

        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "candidate_trace_ids": tuple(trace_ids),
            "candidate_start_date": start_date,
            "candidate_end_date": end_date,
        }
        scalar_filters = [
            item for item in active_filters if is_latest_trace_probe_filter(item)
        ]
        external_filters = [
            item
            for item in active_filters
            if not is_latest_trace_probe_filter(item)
            and _is_candidate_external_trace_filter(item)
        ]
        indexed_plans = []
        for index, item in enumerate(scalar_filters):
            plan = build_latest_trace_probe_predicate(item, index=index)
            params.update(plan.params)
            indexed_plans.append((index, item, plan))

        root_plans = [
            (index, plan)
            for index, item, plan in indexed_plans
            if is_latest_trace_root_probe_filter(item)
        ]
        any_span_plans = [
            (index, plan)
            for index, item, plan in indexed_plans
            if not is_latest_trace_root_probe_filter(item)
        ]

        def _any_span_branch(index, plan):
            aggregates = ",\n                        ".join(plan.aggregates)
            return f"""
                SELECT trace_id
                FROM (
                    SELECT
                        trace_id,
                        id,
                        argMax(is_deleted, _peerdb_version)
                            AS latest_is_deleted,
                        {aggregates}
                    FROM {self.TABLE}
                    PREWHERE {self.project_filter_sql()}
                      AND trace_id IN %(candidate_trace_ids)s
                      AND created_at >= %(candidate_start_date)s - INTERVAL 1 DAY
                      AND start_time >= %(candidate_start_date)s - INTERVAL 1 DAY
                      AND start_time < %(candidate_end_date)s + INTERVAL 1 DAY
                    GROUP BY trace_id, id
                )
                WHERE latest_is_deleted = 0
                  AND {plan.predicate}
                GROUP BY trace_id
            """

        any_span_branches = [
            _any_span_branch(index, plan) for index, plan in any_span_plans
        ]
        scalar_query = ""
        if root_plans:
            # A trace row is defined by its canonical root: the newest live
            # in-window root after resolving each root span's latest version.
            # final_status is evaluated only after that canonical root is
            # selected, so an older matching root cannot make a newer
            # non-matching trace row pass.
            root_aggregates = ",\n                        ".join(
                aggregate for _, plan in root_plans for aggregate in plan.aggregates
            )
            root_predicate = " AND ".join(plan.predicate for _, plan in root_plans)
            root_query = f"""
                SELECT grouped_trace_id AS trace_id
                FROM (
                    SELECT *
                    FROM (
                        SELECT
                            trace_id AS grouped_trace_id,
                            id AS grouped_id,
                            argMax(tuple(parent_span_id), _peerdb_version).1
                                AS latest_parent_span_id,
                            argMax(start_time, _peerdb_version)
                                AS latest_start_time,
                            argMax(is_deleted, _peerdb_version)
                                AS latest_is_deleted,
                            {root_aggregates}
                        FROM {self.TABLE}
                        PREWHERE {self.project_filter_sql()}
                          AND trace_id IN %(candidate_trace_ids)s
                          AND start_time >= %(candidate_start_date)s
                          AND start_time < %(candidate_end_date)s
                        GROUP BY trace_id, id
                    )
                    WHERE latest_is_deleted = 0
                      AND (
                          latest_parent_span_id IS NULL
                          OR latest_parent_span_id = ''
                      )
                    ORDER BY latest_start_time DESC, grouped_id DESC
                    LIMIT 1 BY grouped_trace_id
                )
                WHERE {root_predicate}
            """
            membership = "".join(
                f" AND trace_id IN ({branch})" for branch in any_span_branches
            )
            scalar_query = f"""
            SELECT trace_id
            FROM ({root_query})
            WHERE 1 = 1 {membership}
            LIMIT {len(trace_ids)}
            """
        elif any_span_branches:
            params["candidate_filter_count"] = len(any_span_branches)
            tagged_branches = [
                f"SELECT trace_id, toUInt16({index}) AS filter_index FROM ({branch})"
                for (index, _), branch in zip(
                    any_span_plans, any_span_branches, strict=True
                )
            ]
            scalar_query = f"""
            SELECT trace_id
            FROM ({" UNION ALL ".join(tagged_branches)})
            GROUP BY trace_id
            HAVING uniqExact(filter_index) = %(candidate_filter_count)s
            LIMIT {len(trace_ids)}
            """
        else:
            # External-only filters still need to discard candidates whose
            # latest root was tombstoned. The set is already point-scoped, so
            # FINAL remains bounded and never expands into a tenant-wide merge.
            scalar_query = f"""
            SELECT trace_id
            FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND trace_id IN %(candidate_trace_ids)s
              AND start_time >= %(candidate_start_date)s
              AND start_time < %(candidate_end_date)s
            WHERE is_deleted = 0
              AND {self.ROOT_SEED_PARENT_PREDICATE}
            GROUP BY trace_id
            """

        if external_filters:
            filter_builder = self._FILTER_BUILDER_CLS(
                table=self.TABLE,
                annotation_label_ids=self.annotation_label_ids,
                query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
                project_id=self.project_id,
                project_ids=self.project_ids,
                score_date_scope=True,
                span_date_scope=True,
                span_trace_id_scope=True,
                span_latest_state=True,
                candidate_entity_scope=True,
                tag_query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
            )
            try:
                external_predicate, external_params = filter_builder.translate(
                    external_filters
                )
            except (TypeError, ValueError) as exc:
                raise UnsupportedFilterShapeError(
                    "unsupported trace filter shape"
                ) from exc
            params.update(external_params)
            if not external_predicate:
                raise ValueError("unsupported empty trace filter predicate")
            query = f"""
            SELECT trace_id
            FROM ({scalar_query})
            WHERE {external_predicate}
            LIMIT {len(trace_ids)}
            """
        else:
            query = scalar_query
        return query, params

    def supports_latest_filter_match(
        self,
        filters: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Whether the bounded classifier can represent every active filter."""

        active_filters = [
            item
            for item in (filters if filters is not None else self.filters)
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        return not self.sort_params and all(
            is_latest_trace_probe_filter(item)
            or _is_candidate_external_trace_filter(item)
            for item in active_filters
        )

    # Compatibility name retained for internal callers added during the SOS
    # rollout. The implementation now covers both attribute Maps and the common
    # physical any-span columns.
    def build_latest_attribute_match_query(
        self,
        candidate_trace_ids: list[str],
        *,
        filters: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return self.build_latest_filter_match_query(
            candidate_trace_ids,
            filters=filters,
        )

    def supports_latest_root_id_page(self) -> bool:
        """Whether the trace-id set can be read as scalar canonical roots.

        This deliberately accepts only time filters plus predicates that are
        evaluated on the canonical trace root.  Any-span, eval, annotation,
        tag, search, project-version, and custom-sort shapes stay on their
        established bounded paths; silently approximating one of those here
        would create the wrong evaluation/annotation task.
        """

        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        return (
            not self.sort_params
            and self.search is None
            and self.project_version_id is None
            and not self.candidate_trace_ids
            and all(is_latest_trace_root_probe_filter(item) for item in active_filters)
        )

    def build_latest_root_id_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int | None,
        sampling_salt: str | None = None,
        sampling_rate: float | None = None,
        exclude_trace_ids: list[str] | tuple[str, ...] | set[str] | None = None,
        after_trace_id: str | None = None,
        trace_id_desc: bool = False,
        order_by_recent_minute: bool = True,
        changed_since_version: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a bounded canonical-root trace-id page without ``FINAL``.

        Each root span is first reduced to its newest version with scalar
        ``argMax`` states, including the tombstone.  The newest live root is
        then chosen for each trace *before* root predicates such as
        ``final_status`` are evaluated.  That order is important: an older
        matching root must not make a newer non-matching trace pass.

        The explicit slice bounds keep the aggregation partition-prunable.
        Callers that scan adjacent slices must remember every candidate they
        classify (including non-matches), or use this method only as a seed and
        verify candidates against the full requested window.
        """

        if not self.supports_latest_root_id_page():
            raise ValueError("latest scalar root page does not support this request")
        if limit is None and changed_since_version is None:
            raise ValueError(
                "unbounded latest scalar root ids require a version watermark"
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

        request_start, request_end = self.parse_time_range(self.filters)
        if slice_start < request_start or slice_end > request_end:
            raise ValueError("latest scalar root page must stay inside request window")

        params: dict[str, Any] = {
            **self.params,
            # Compatibility aliases for bounded eval callers that use the
            # original request window to drive adjacent-slice traversal.
            "start_date": request_start,
            "end_date": request_end,
            "latest_root_slice_start": slice_start,
            "latest_root_slice_end": slice_end,
        }
        if limit is not None:
            params["latest_root_limit"] = int(limit)
        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        plans = [
            build_latest_trace_probe_predicate(item, index=index)
            for index, item in enumerate(active_filters)
        ]
        for plan in plans:
            params.update(plan.params)

        aggregates = [aggregate for plan in plans for aggregate in plan.aggregates]
        aggregate_fragment = (
            ",\n                            "
            + ",\n                            ".join(aggregates)
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
            params["latest_root_sampling_salt"] = str(sampling_salt)
            params["latest_root_sampling_rate"] = rate
            sampling_fragment = (
                " AND modulo(cityHash64(%(latest_root_sampling_salt)s, "
                "toString(grouped_trace_id)), 100) "
                "< %(latest_root_sampling_rate)s"
            )

        excluded = tuple(
            sorted(str(value) for value in (exclude_trace_ids or ()) if value)
        )
        exclusion_fragment = ""
        if excluded:
            params["latest_root_excluded_trace_ids"] = excluded
            exclusion_fragment = (
                " AND grouped_trace_id NOT IN %(latest_root_excluded_trace_ids)s"
            )

        keyset_fragment = ""
        if after_trace_id is not None:
            if limit is None:
                raise ValueError("trace-id keyset requires a bounded page")
            if trace_id_desc or not order_by_recent_minute:
                raise ValueError("ascending trace-id keyset requires ascending order")
            if (slice_end - slice_start).total_seconds() > 60:
                raise ValueError("trace-id-only keyset requires a one-minute slice")
            params["latest_root_after_trace_id"] = str(after_trace_id)
            keyset_fragment = " AND grouped_trace_id > %(latest_root_after_trace_id)s"

        changed_candidate_fragment = ""
        if changed_since_version is not None:
            if int(changed_since_version) < 0:
                raise ValueError("changed_since_version must be non-negative")
            params["latest_root_changed_since_version"] = int(changed_since_version)
            # The cursor follows physical direct-write versions, while trace
            # identity follows the canonical root. Seed changed trace ids from
            # root writes, then reclassify every root for those traces across the
            # complete task-time window. An updated older root therefore cannot
            # displace a newer canonical root.
            changed_candidate_fragment = f"""
                  AND trace_id IN (
                      SELECT trace_id
                      FROM {self.TABLE}
                      PREWHERE {self.project_filter_sql()}
                        AND start_time >= %(latest_root_slice_start)s
                        AND start_time < %(latest_root_slice_end)s
                        AND _peerdb_version >= %(latest_root_changed_since_version)s
                      WHERE parent_span_id IS NULL OR parent_span_id = ''
                      GROUP BY trace_id
                  )
            """

        id_direction = "DESC" if trace_id_desc else "ASC"
        order_time = (
            "toStartOfMinute(latest_start_time)"
            if order_by_recent_minute
            else "latest_start_time"
        )
        order_limit_fragment = ""
        if limit is not None:
            order_limit_fragment = f"""
        ORDER BY
            {order_time} DESC,
            grouped_trace_id {id_direction}
        LIMIT %(latest_root_limit)s
            """
        query = f"""
        SELECT
            grouped_trace_id AS trace_id,
            latest_start_time AS eval_order_start_time
        FROM (
            SELECT *
            FROM (
                SELECT
                    trace_id AS grouped_trace_id,
                    id AS grouped_id,
                    argMax(tuple(parent_span_id), _peerdb_version).1
                        AS latest_parent_span_id,
                    argMax(tuple(start_time), _peerdb_version).1
                        AS latest_start_time,
                    argMax(_peerdb_is_deleted, _peerdb_version)
                        AS latest_is_deleted
                    {aggregate_fragment}
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND start_time >= %(latest_root_slice_start)s
                  AND start_time < %(latest_root_slice_end)s
                  {changed_candidate_fragment}
                GROUP BY trace_id, id
            )
            WHERE latest_is_deleted = 0
              AND (
                  latest_parent_span_id IS NULL
                  OR latest_parent_span_id = ''
              )
            ORDER BY latest_start_time DESC, grouped_id DESC
            LIMIT 1 BY grouped_trace_id
        )
        WHERE 1 = 1
          {predicate_fragment}
          {sampling_fragment}
          {exclusion_fragment}
          {keyset_fragment}
        {order_limit_fragment}
        """
        return query, params

    def build_candidate_hydration_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate only matched roots using scalar latest-version states."""
        bounded_ids = [str(trace_id) for trace_id in trace_ids if trace_id]
        if not bounded_ids:
            return "", {}
        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "candidate_trace_ids": tuple(bounded_ids),
            "candidate_start_date": start_date,
            "candidate_end_date": end_date,
        }
        query = f"""
        SELECT
            grouped_trace_id AS trace_id,
            latest_trace_name AS trace_name,
            latest_name AS span_name,
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
            latest_trace_session_id AS trace_session_id,
            latest_project_id AS project_id,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool
        FROM (
            SELECT
                trace_id AS grouped_trace_id,
                id AS grouped_id,
                argMax(tuple(parent_span_id), _peerdb_version).1
                    AS latest_parent_span_id,
                argMax(trace_name, _peerdb_version) AS latest_trace_name,
                argMax(name, _peerdb_version) AS latest_name,
                argMax(observation_type, _peerdb_version)
                    AS latest_observation_type,
                argMax(status, _peerdb_version) AS latest_status,
                argMax(start_time, _peerdb_version) AS latest_start_time,
                argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                argMax(latency_ms, _peerdb_version) AS latest_latency_ms,
                argMax(cost, _peerdb_version) AS latest_cost,
                argMax(total_tokens, _peerdb_version) AS latest_total_tokens,
                argMax(prompt_tokens, _peerdb_version) AS latest_prompt_tokens,
                argMax(completion_tokens, _peerdb_version)
                    AS latest_completion_tokens,
                argMax(model, _peerdb_version) AS latest_model,
                argMax(provider, _peerdb_version) AS latest_provider,
                argMax(tuple(trace_session_id), _peerdb_version).1
                    AS latest_trace_session_id,
                argMax(project_id, _peerdb_version) AS latest_project_id,
                argMax(span_attr_str, _peerdb_version) AS latest_attrs_string,
                argMax(span_attr_num, _peerdb_version) AS latest_attrs_number,
                argMax(span_attr_bool, _peerdb_version) AS latest_attrs_bool,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND trace_id IN %(candidate_trace_ids)s
              AND start_time >= %(candidate_start_date)s
              AND start_time < %(candidate_end_date)s
            GROUP BY trace_id, id
        )
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
        ORDER BY latest_start_time DESC, grouped_trace_id DESC, grouped_id DESC
        LIMIT 1 BY grouped_trace_id
        """
        return query, params

    def build_id_query(
        self,
        *,
        limit: int | None = None,
        sampling_salt: str | None = None,
        sampling_rate: float | None = None,
        order_by_recent_minute: bool = False,
        latest_state: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Filtered trace ids only — same root-span predicate/window as build(),
        no pagination/order by default. The eval resolver can request a sampled,
        bounded newest-minute top-K so a historical task never materializes
        every matching trace id before applying its row limit."""
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
            # Eval-task fallback probes pass a bounded trace-id candidate set.
            # Apply that same bound inside any-span membership subqueries.
            span_trace_id_scope=bool(self.candidate_trace_ids),
            span_latest_state=latest_state or bool(self.candidate_trace_ids),
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        candidate_fragment = ""
        if self.candidate_trace_ids:
            self.params["candidate_trace_ids"] = tuple(self.candidate_trace_ids)
            candidate_fragment = "AND trace_id IN %(candidate_trace_ids)s"

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        search_fragment = _literal_trace_name_search(self.params, self.search)

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
                "cityHash64(%(id_sampling_salt)s, toString(trace_id)), 100"
                ") < %(id_sampling_rate)s"
            )

        if limit is not None:
            if int(limit) <= 0:
                raise ValueError("limit must be greater than zero")
            self.params["id_limit"] = int(limit)
            # Avoid LIMIT 1 BY for the bounded path: it retains a key for every
            # matching trace before applying LIMIT. A small duplicate margin is
            # fetched and de-duplicated by the eval row resolver instead.
            dedup_fragment = ""
            order_fragment = (
                "ORDER BY toStartOfMinute(start_time) DESC, trace_id"
                if order_by_recent_minute
                else "ORDER BY trace_id"
            )
            order_limit_fragment = f"{order_fragment}\n        LIMIT %(id_limit)s"
        else:
            dedup_fragment = "LIMIT 1 BY trace_id"
            order_limit_fragment = ""

        select_fragment = (
            "trace_id, start_time AS eval_order_start_time"
            if order_by_recent_minute
            else "trace_id"
        )
        span_source = (
            f"{self.TABLE} FINAL"
            if latest_state or self.candidate_trace_ids
            else self.TABLE
        )
        query = f"""
        SELECT {select_fragment}
        FROM {span_source}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {candidate_fragment}
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
          {sampling_fragment}
        {dedup_fragment}
        {order_limit_fragment}
        """
        return query, self.params

    def build_content_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch heavy columns (input, output, attributes) for a page of traces.

        Uses PREWHERE on trace_id for fast point lookups — avoids scanning
        heavy columns for the entire table.
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "content_trace_ids": tuple(trace_ids),
        }

        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            trace_id,
            input,
            output,
            attrs_string,
            attrs_number,
            attrs_bool,
            attributes_extra,
            toJSONString(metadata) AS metadata,
            dictGetOrDefault('trace_dict', 'tags', toUUID(trace_id), '[]') AS trace_tags
        FROM {self.TABLE} FINAL
        PREWHERE trace_id IN %(content_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {span_window}
        LIMIT 1 BY trace_id
        """
        return query, params

    def build_span_attributes_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Aggregate span attributes across all spans of each trace.

        Returns one row per trace with groupArrayDistinct for each attribute key.
        Skips raw/large content keys.
        """
        if not trace_ids:
            return "", {}

        params = {**self.params, "attr_trace_ids": tuple(trace_ids)}
        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            trace_id,
            attributes_extra
        FROM {self.TABLE} FINAL
        PREWHERE trace_id IN %(attr_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND attributes_extra != '{{}}'
          AND attributes_extra != ''
          {span_window}
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching traces (for pagination).

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        # ``build(since=...)`` may have narrowed ``self.params`` for
        # progressive page lookup. Counts always cover the user's original
        # requested range, so re-parse and overwrite those bindings here.
        count_start, count_end = self.parse_time_range(self.filters)

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        # Merge params -- reuse the same start/end dates
        params = dict(self.params)
        params["start_date"] = count_start
        params["end_date"] = count_end
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # Optional project_version_id filter
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            params["project_version_id"] = self.project_version_id

        # Search filter (reuse from build())
        search_fragment = _literal_trace_name_search(params, self.search)

        query = f"""
        SELECT uniq(trace_id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Span count per trace (optional — only if columns include span_count)
    # ------------------------------------------------------------------

    def build_span_count_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Count spans and errors per trace for a page of trace IDs."""
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "sc_trace_ids": tuple(trace_ids),
        }
        query = f"""
        SELECT
            trace_id,
            count() AS span_count,
            countIf(status = 'ERROR') AS error_count
        FROM {self.TABLE}
        WHERE {self.project_filter_sql()}
          AND trace_id IN %(sc_trace_ids)s
          AND is_deleted = 0
        GROUP BY trace_id
        """
        return query, params

    @staticmethod
    def pivot_span_count_results(
        data: list[dict],
    ) -> dict[str, dict[str, int]]:
        """Pivot span count results into ``{trace_id: {span_count, error_count}}``."""
        result: dict[str, dict[str, int]] = {}
        for row in data:
            tid = str(row.get("trace_id", ""))
            if tid:
                result[tid] = {
                    "span_count": row.get("span_count", 0),
                    "error_count": row.get("error_count", 0),
                }
        return result

    # ------------------------------------------------------------------
    # Phase 2: Eval scores for a set of trace IDs
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-2 eval-scores query for a page of trace IDs.

        Queries ``tracer_eval_logger FINAL`` grouped by
        ``(trace_id, custom_eval_config_id)`` to produce one aggregated
        score row per (trace, eval config) pair.

        Args:
            trace_ids: List of trace ID strings from Phase 1.

        Returns:
            A ``(query_string, params)`` tuple.  Returns empty query if
            no trace_ids or no eval_config_ids.
        """
        if not trace_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        # Partition-prune `tracer_eval_logger` (PARTITION BY toYYYYMM(created_at))
        # so the FINAL merge can skip months that cannot match this page.
        # The page of trace_ids was selected by build() within the user's
        # [start_date, end_date] window on `start_time`, so the matching eval
        # rows' `created_at` falls inside that window plus ingestion skew. A
        # lower-bound-only filter with a 1-day skew buffer (identical to the
        # mitigation in build()/build_count_query()) prunes old partitions
        # without dropping any legitimately-matching eval row. Guarded on
        # self.start_date so callers that invoke build_eval_query() without a
        # prior build() (e.g. unit tests) keep their current behavior.
        created_at_fragment = ""
        if self.start_date is not None:
            params["start_date"] = self.start_date
            created_at_fragment = "AND created_at >= %(start_date)s - INTERVAL 1 DAY"

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score nor
        # masquerades as a real value. The per-status counts let the pivot pick
        # one cell state per (trace, config) by the precedence
        # completed > errored > skipped > running > pending.
        # ``success_count`` excludes non-terminal/skipped/errored rows via
        # ``status NOT IN (...)``: a bare ``error = 0`` guard also matches
        # pending/running/skipped rows (they carry ``error = 0`` and a NULL
        # output). NOT-IN (rather than ``status = 'completed'``) keeps legacy
        # rows whose mirrored ``status`` is empty/NULL counted as completed.
        # ``str_lists`` keeps every completed ``output_str_list`` so the pivot
        # can compute per-choice percentages for CHOICES evals.
        # ``output_str`` is Nullable(String); ClickHouse 3-valued logic makes
        # ``NULL != 'ERROR'`` NULL (not TRUE), so use ``ifNull(...)`` to keep
        # the comparison NULL-safe.
        # New per-status columns are appended after ``str_lists`` so the pivot's
        # positional column fallbacks (0..7) stay valid.
        query = f"""
        SELECT
            trace_id,
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
            count() AS eval_count,
            groupArrayIf(
                output_str_list,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS str_lists,
            countIf(status = 'skipped') AS skipped_count,
            countIf(status = 'running') AS running_count,
            countIf(status = 'pending') AS pending_count,
            anyIf(skipped_reason, status = 'skipped') AS skipped_reason
        -- PERF: no table-level FINAL. FINAL forced a merge across the WHOLE
        -- eval table before the WHERE was applied, so a page of ~50 trace ids
        -- dragged a merge over tens of millions of rows — GBs of memory that
        -- OOM-crashed the server. Instead de-dup only the page-scoped slice:
        -- the inner scan is pruned to the page's trace ids (idx_trace_id
        -- bloom) + config ids + the created_at partition bound, then ORDER BY
        -- _peerdb_version DESC + LIMIT 1 BY id keeps the newest version of
        -- each eval row — verified identical to FINAL for live rows (status
        -- transitions collapse to the newest version). One accepted
        -- divergence: the not-deleted WHERE runs BEFORE dedup, so an eval
        -- whose newest un-merged version is a soft-delete marker transiently
        -- surfaces its previous version until the next merge.
        FROM (
            SELECT
                trace_id,
                custom_eval_config_id,
                output_float,
                output_bool,
                output_str,
                output_str_list,
                error,
                status,
                skipped_reason
            FROM {self.EVAL_TABLE}
            WHERE _peerdb_is_deleted = 0
              AND (deleted = 0 OR deleted IS NULL)
              AND trace_id IN %(trace_ids)s
              AND custom_eval_config_id IN %(eval_config_ids)s
              {created_at_fragment}
            ORDER BY _peerdb_version DESC
            LIMIT 1 BY id
        )
        GROUP BY trace_id, custom_eval_config_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of trace IDs
    # ------------------------------------------------------------------

    ANNOTATION_TABLE = "model_hub_score"

    def build_annotation_query(
        self,
        trace_ids: list[str],
        annotation_label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build annotation query for a page of trace IDs."""
        if not trace_ids or not annotation_label_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "label_ids": tuple(annotation_label_ids),
        }
        # Bound only the spans (sp) join side; the score (s) side keeps no
        # upper bound so annotations created after the window still resolve.
        sp_window = self._span_time_window(params, column="sp.start_time")

        query = f"""
        SELECT
            if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
            ) AS trace_id,
            toString(s.label_id) AS label_id,
            anyLast(s.value) AS value,
            toString(anyLast(s.annotator_id)) AS annotator_id
        FROM {self.ANNOTATION_TABLE} AS s FINAL
        LEFT JOIN {self.TABLE} AS sp
          ON sp.id = s.observation_span_id
         AND sp._peerdb_is_deleted = 0
         {sp_window}
        WHERE s._peerdb_is_deleted = 0
          AND s.deleted = false
          AND if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
              ) IN %(trace_ids)s
          AND s.label_id IN %(label_ids)s
        GROUP BY trace_id, label_id
        """
        return query, params

    def build_user_id_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch user_id strings from ClickHouse for a page of trace IDs.

        Uses enduser_dict to resolve end_user_id UUIDs to user_id strings
        in a single query. Returns one user_id per trace (uses `any()`
        aggregation to pick the first non-null value across all spans).
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "user_trace_ids": tuple(trace_ids),
        }
        span_window = self._span_time_window(params)

        query = f"""
        SELECT trace_id, user_id
        FROM (
            SELECT
                trace_id,
                dictGetOrDefault('enduser_dict', 'user_id', any(end_user_id), '') AS user_id
            FROM {self.TABLE}
            PREWHERE trace_id IN %(user_trace_ids)s
            WHERE {self.project_filter_sql()}
              AND _peerdb_is_deleted = 0
              AND end_user_id IS NOT NULL
              AND end_user_id != toUUID('00000000-0000-0000-0000-000000000000')
              {span_window}
            GROUP BY trace_id
        )
        WHERE user_id != ''
        """
        return query, params

    def resolve_user_ids(self, trace_ids: list[str], analytics) -> dict[str, str]:
        """Resolve user_id strings for a page of trace IDs.

        Single-query lookup using ClickHouse enduser_dict:
        - Queries ClickHouse for user_id strings via dictionary lookup (~50-100ms)
        - No PostgreSQL round-trip needed

        Args:
            trace_ids: List of trace ID strings to resolve users for.
            analytics: Analytics service instance for executing CH queries.

        Returns:
            Dict mapping trace_id → user_id string.
        """
        if not trace_ids:
            return {}

        user_query, user_params = self.build_user_id_query(trace_ids)
        if not user_query:
            return {}

        result = analytics.execute_ch_query(
            user_query,
            user_params,
            timeout_ms=750,
            settings={"max_threads": 2, "max_result_rows": 2000},
        )

        # Build trace_id → user_id mapping (filter already applied in query)
        user_id_map = {
            str(row.get("trace_id", "")): row.get("user_id")
            for row in result.data
            if row.get("user_id")
        }

        return user_id_map

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: list[dict],
        label_types: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Pivot annotation results keyed by trace_id.

        Returns:
            ``{trace_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: dict[str, dict[str, Any]] = {}
        for row in annotation_rows:
            trace_id = str(row.get("trace_id", ""))
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

            result.setdefault(trace_id, {})[label_id] = value

        return result

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    @staticmethod
    def pivot_eval_results(
        eval_rows: list[tuple],
        eval_columns: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by trace_id.

        Args:
            eval_rows: Rows from the Phase-2 eval query.
            eval_columns: Column names for those rows.

        Returns:
            A dict of ``{trace_id: {eval_config_id: score_dict}}``.
        """
        result: dict[str, dict[str, Any]] = {}
        col_idx = {name: i for i, name in enumerate(eval_columns)}

        def _get(row, key, idx, default=None):
            if isinstance(row, dict):
                return row.get(key, default)
            return (
                row[col_idx.get(key, idx)]
                if len(row) > col_idx.get(key, idx)
                else default
            )

        import json as _json

        for row in eval_rows:
            trace_id = str(_get(row, "trace_id", 0, ""))
            config_id = str(_get(row, "eval_config_id", 1, ""))
            avg_score = _get(row, "avg_score", 2)
            pass_rate = _get(row, "pass_rate", 3)
            success_count = _get(row, "success_count", 4, 0) or 0
            error_count = _get(row, "error_count", 5, 0) or 0
            str_lists = _get(row, "str_lists", 7, []) or []

            # All rows errored — surface an explicit error marker so the
            # UI can render an error state (distinct from "no eval run").
            if success_count == 0 and error_count > 0:
                result.setdefault(trace_id, {})[config_id] = {"error": True}
                continue

            # CHOICES eval: compute per-choice percentage across all
            # non-errored eval rows for this (trace, config) pair. Caller
            # spreads into ``{config_id}**{choice}`` columns.
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
                result.setdefault(trace_id, {})[config_id] = {
                    "per_choice": per_choice,
                }
                continue

            # ClickHouse ``avgIf`` returns NaN when no rows pass the
            # condition (or when all matching values are NULL). Python's
            # ``bool(float('nan'))`` is True, so a plain ``if avg_score``
            # guard leaks NaN into the JSON response and trips DRF's
            # strict encoder. Filter non-finite values explicitly.
            def _finite(v):
                return (
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                )

            avg_val = round(avg_score * 100, 2) if _finite(avg_score) else None
            pass_val = round(pass_rate, 2) if _finite(pass_rate) else None

            # No completed score: surface a non-terminal / skipped lifecycle
            # marker (skipped > running > pending) so the cell renders a
            # loading/pending/skipped state instead of a misleading blank.
            if avg_val is None and pass_val is None:
                marker = non_terminal_eval_marker(
                    {
                        "skipped_count": _get(row, "skipped_count", 8, 0) or 0,
                        "running_count": _get(row, "running_count", 9, 0) or 0,
                        "pending_count": _get(row, "pending_count", 10, 0) or 0,
                        "skipped_reason": _get(row, "skipped_reason", 11, None),
                    }
                )
                if marker is not None:
                    result.setdefault(trace_id, {})[config_id] = marker
                    continue

            score_data = {
                "avg_score": avg_val,
                "pass_rate": pass_val,
                "count": _get(row, "eval_count", 6, 0) or 0,
            }
            result.setdefault(trace_id, {})[config_id] = score_data

        return result

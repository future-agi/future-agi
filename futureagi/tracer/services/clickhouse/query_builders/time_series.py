"""
Time-Series Query Builder for ClickHouse.

Replaces ``get_all_system_metrics()`` and ``get_system_metric_data()`` from
``tracer.utils.graphs_optimized`` with ClickHouse-native queries.

Strategy:
- Unfiltered dashboard queries read from the ``spans_hourly_rollup``
  pre-aggregated AggregatingMergeTree (v2 schema 010) using ``countMerge`` /
  ``sumMerge`` / ``quantilesTDigestMerge`` combinators. The rollup is fed
  directly from the v2 typed-JSON ``spans`` table via an incremental MV.
- When attribute filters are present, falls back to scanning the v2
  ``spans`` table directly.

CH25 close-out (2026-05-28): cut over from the legacy ``span_metrics_hourly``
(fed by ``spans_mv`` ← ``tracer_observation_span`` CDC mirror) to
``spans_hourly_rollup``. Removes the last dashboard read-path dependency on
the legacy CDC-based aggregate.
"""

import re
from datetime import datetime, timedelta
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

_SAFE_CLUSTER_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")


class TimeSeriesQueryBuilder(BaseQueryBuilder):
    """Build time-series metric queries for the dashboard.

    Returns all four metrics in a single query: latency, tokens, cost,
    and traffic.  The output format matches the dict returned by
    ``get_all_system_metrics()``::

        {
            "latency": [{"timestamp": "...", "value": 0, "latency": 0}, ...],
            "tokens":  [{"timestamp": "...", "value": 0, "tokens": 0}, ...],
            "cost":    [{"timestamp": "...", "value": 0, "cost": 0}, ...],
            "traffic": [{"timestamp": "...", "traffic": 0}, ...],
        }

    Args:
        project_id: Project UUID string.
        filters: Frontend filter list (may be empty).
        interval: Time bucket interval (``"hour"``, ``"day"``, ``"week"``,
            ``"month"``).
        system_metric_filters: Additional keyword filters (currently unused;
            reserved for future per-model breakdowns).
    """

    # Pre-aggregated table (AggregatingMergeTree)
    # CH25 close-out (2026-05-28): switched from the legacy
    # `span_metrics_hourly` (fed by `spans_mv` ← `tracer_observation_span` CDC
    # mirror) to the v2 `spans_hourly_rollup` (fed directly from the v2 typed-
    # JSON `spans` table — no CDC). The v2 rollup uses AggregateFunction
    # columns + `*Merge()` combinators (real AggregatingMergeTree pattern)
    # whereas the legacy table stored already-summed Int64s.
    AGG_TABLE = "spans_hourly_rollup"
    # Denormalized raw table (for filtered queries)
    RAW_TABLE = "spans"

    def __init__(
        self,
        project_id: str,
        filters: list[dict] | None = None,
        interval: str = "hour",
        system_metric_filters: dict[str, Any] | None = None,
        exact_snapshot: bool = False,
        observe_type: str = "span",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        annotation_label_ids: list[str] | tuple[str, ...] | None = None,
        resolve_span_versions: bool = True,
        raw_replica_shard_cluster: str = "",
        raw_replica_shard_count: int = 1,
        raw_trace_candidate_predicate: str = "",
        raw_trace_candidate_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.filters = filters or []
        self.interval = interval
        self.system_metric_filters = system_metric_filters or {}
        self.exact_snapshot = bool(exact_snapshot)
        self.observe_type = str(observe_type or "span").strip().lower()
        if self.observe_type not in {"trace", "span"}:
            raise ValueError("observe_type must be trace or span")
        self.start_date = start_date
        self.end_date = end_date
        self.annotation_label_ids = (
            None if annotation_label_ids is None else tuple(annotation_label_ids)
        )
        self.resolve_span_versions = bool(resolve_span_versions)
        self.raw_replica_shard_cluster = str(raw_replica_shard_cluster or "").strip()
        self.raw_replica_shard_count = int(raw_replica_shard_count)
        self.raw_trace_candidate_predicate = str(
            raw_trace_candidate_predicate or ""
        ).strip()
        self.raw_trace_candidate_params = dict(raw_trace_candidate_params or {})
        if (
            self.raw_replica_shard_cluster
            and _SAFE_CLUSTER_NAME_RE.fullmatch(self.raw_replica_shard_cluster) is None
        ):
            raise ValueError("invalid graph replica-shard cluster")
        if not 1 <= self.raw_replica_shard_count <= 16:
            raise ValueError("invalid graph replica-shard count")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the time-series query.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        # Lazy import: a module-level import would form a v1↔v2 circular import.
        from tracer.services.clickhouse.v2.query_builders.filters import (
            ClickHouseFilterBuilderV2 as ClickHouseFilterBuilder,
        )

        if self.start_date is None or self.end_date is None:
            self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Determine if we have attribute filters that prevent using the
        # pre-aggregated table.
        # Exact graphs compile every supported filter to the current span row;
        # the query below supplies trace-level any-sibling semantics without a
        # second mutable table read.
        if self.exact_snapshot:
            from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
                compile_exact_graph_row_predicates,
            )

            exact_filter_plan = compile_exact_graph_row_predicates(
                self.filters,
                project_id=str(self.project_id),
                observe_type=self.observe_type,
                annotation_label_ids=self.annotation_label_ids,
            )
            extra_params = exact_filter_plan.params
        else:
            filter_builder = ClickHouseFilterBuilder(
                table=self.RAW_TABLE,
                project_id=self.project_id,
                project_ids=self.project_ids,
                span_date_scope=True,
                query_mode=self.observe_type,
            )
            extra_where, extra_params = filter_builder.translate(self.filters)
        self.params.update(extra_params)

        if self.exact_snapshot:
            return self._build_exact_raw_query(
                exact_filter_plan.predicates,
                exact_filter_plan.output_window_only,
                exact_filter_plan.required_matches,
                exact_filter_plan.match_condition_groups,
                exact_filter_plan.contribution_predicates,
            )
        if extra_where:
            return self._build_raw_query(extra_where)
        return self._build_agg_query()

    def format_result(
        self,
        rows: list[tuple],
        columns: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Post-process raw ClickHouse rows into the standard response dict.

        Expected columns from the query:
        ``time_bucket, avg_latency, total_tokens, avg_cost, traffic_count``

        Args:
            rows: Rows returned by ClickHouse.
            columns: Column name list.

        Returns:
            Dict with keys ``latency``, ``tokens``, ``cost``, ``traffic``.
        """
        assert self.start_date is not None and self.end_date is not None

        # Build per-metric data lists
        latency_data: list[dict[str, Any]] = []
        tokens_data: list[dict[str, Any]] = []
        cost_data: list[dict[str, Any]] = []
        traffic_data: list[dict[str, Any]] = []

        for row in rows:
            # Support both dict rows (from execute_ch_query) and tuple rows
            if isinstance(row, dict):
                ts = row.get(
                    "time_bucket", row.get(columns[0] if columns else "time_bucket")
                )
                avg_lat = row.get("avg_latency", 0)
                total_tok = row.get("total_tokens", 0)
                avg_cst = row.get("avg_cost", 0)
                count = row.get("traffic_count", 0)
            else:
                ts = row[0]
                avg_lat = row[1] if len(row) > 1 else 0
                total_tok = row[2] if len(row) > 2 else 0
                avg_cst = row[3] if len(row) > 3 else 0
                count = row[4] if len(row) > 4 else 0
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

            latency_data.append(
                {
                    "timestamp": ts_str,
                    "value": round(avg_lat, 2) if avg_lat else 0,
                    "latency": round(avg_lat, 2) if avg_lat else 0,
                }
            )
            tokens_data.append(
                {
                    "timestamp": ts_str,
                    "value": round(total_tok, 2) if total_tok else 0,
                    "tokens": round(total_tok, 2) if total_tok else 0,
                }
            )
            cost_data.append(
                {
                    "timestamp": ts_str,
                    "value": round(avg_cst, 9) if avg_cst else 0,
                    "cost": round(avg_cst, 9) if avg_cst else 0,
                }
            )
            traffic_data.append(
                {
                    "timestamp": ts_str,
                    "traffic": count or 0,
                }
            )

        # Helper to extract values from dict or tuple rows
        def _get(r, key, idx, default=0):
            if isinstance(r, dict):
                return r.get(key, default)
            return r[idx] if len(r) > idx else default

        # Zero-fill missing buckets for each metric
        latency_data = self.format_time_series(
            rows=[(_get(r, "time_bucket", 0), _get(r, "avg_latency", 1)) for r in rows],
            columns=["time_bucket", "value", "latency"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value", "latency"],
        )
        tokens_data = self.format_time_series(
            rows=[
                (_get(r, "time_bucket", 0), _get(r, "total_tokens", 2)) for r in rows
            ],
            columns=["time_bucket", "value", "tokens"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value", "tokens"],
        )
        cost_data = self.format_time_series(
            rows=[(_get(r, "time_bucket", 0), _get(r, "avg_cost", 3)) for r in rows],
            columns=["time_bucket", "value", "cost"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value", "cost"],
        )
        traffic_data = self.format_time_series(
            rows=[
                (_get(r, "time_bucket", 0), _get(r, "traffic_count", 4)) for r in rows
            ],
            columns=["time_bucket", "traffic"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["traffic"],
        )

        # Additional metrics: prompt_tokens, completion_tokens, error_rate
        prompt_tokens_data = self.format_time_series(
            rows=[
                (_get(r, "time_bucket", 0), _get(r, "prompt_tokens", 5)) for r in rows
            ],
            columns=["time_bucket", "value"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value"],
        )
        completion_tokens_data = self.format_time_series(
            rows=[
                (_get(r, "time_bucket", 0), _get(r, "completion_tokens", 6))
                for r in rows
            ],
            columns=["time_bucket", "value"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value"],
        )
        error_rate_data = self.format_time_series(
            rows=[(_get(r, "time_bucket", 0), _get(r, "error_rate", 7)) for r in rows],
            columns=["time_bucket", "value"],
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value"],
        )

        return {
            "latency": latency_data,
            "tokens": tokens_data,
            "cost": cost_data,
            "traffic": traffic_data,
            "prompt_tokens": prompt_tokens_data,
            "completion_tokens": completion_tokens_data,
            "input_tokens": prompt_tokens_data,
            "output_tokens": completion_tokens_data,
            "total_tokens": tokens_data,
            "error_rate": error_rate_data,
        }

    # ------------------------------------------------------------------
    # Private query builders
    # ------------------------------------------------------------------

    def _build_agg_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query against the pre-aggregated ``spans_hourly_rollup`` table.

        Uses ``*Merge()`` aggregate combinators (``countMerge``,
        ``sumMerge``, ``quantilesTDigestMerge``) to reconstruct metrics
        from the ``AggregatingMergeTree`` state columns. See
        ``tracer/services/clickhouse/v2/schema/010_hourly_downsample.sql``
        for the rollup table definition.
        """
        bucket_fn = self.time_bucket_expr(self.interval)

        # quantilesTDigestMerge returns a Tuple; index [1] is the 0.5 (median).
        # The v2 rollup stores 3 quantiles (0.5, 0.95, 0.99) vs the legacy 4
        # (0.5, 0.9, 0.95, 0.99) — we still surface the median as avg_latency
        # to preserve the dashboard contract.
        query = f"""
        SELECT
            {bucket_fn}(hour) AS time_bucket,
            (quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q))[1]
                AS avg_latency,
            sumMerge(total_tokens_sum) AS total_tokens,
            sumMerge(cost_sum) / greatest(countMerge(n), 1)
                AS avg_cost,
            countMerge(n) AS traffic_count,
            sumMerge(prompt_tokens_sum) AS prompt_tokens,
            sumMerge(completion_tokens_sum) AS completion_tokens,
            countIfMerge(error_count) * 100.0 / greatest(countMerge(n), 1)
                AS error_rate
        FROM {self.AGG_TABLE}
        WHERE project_id = %(project_id)s
          AND hour >= %(start_date)s
          AND hour < %(end_date)s
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_raw_query(self, extra_where: str) -> tuple[str, dict[str, Any]]:
        """Build a query against the raw ``spans`` table with filters applied."""
        bucket_fn = self.time_bucket_expr(self.interval)

        query = f"""
        SELECT
            {bucket_fn}(start_time) AS time_bucket,
            avg(latency_ms) AS avg_latency,
            sum(total_tokens) AS total_tokens,
            avg(cost) AS avg_cost,
            count() AS traffic_count,
            sum(prompt_tokens) AS prompt_tokens,
            sum(completion_tokens) AS completion_tokens,
            countIf(status = 'ERROR') * 100.0 / greatest(count(), 1)
                AS error_rate
        FROM {self.RAW_TABLE}
        {self.project_where()}
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          AND {extra_where}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _exact_latest_scalar_source(
        self,
        *,
        row_predicates: tuple[str, ...],
        contribution_predicates: tuple[str, ...],
        scan_start_param: str,
        scan_end_param: str,
        candidate_trace_ids_param: str | None = None,
    ) -> str:
        """Collapse physical versions to one narrow current-row tuple.

        ``FINAL`` has to materialize every selected Map/JSON value while it
        resolves ReplacingMergeTree versions.  On the production high-volume
        project that exceeded 2 GiB before the downstream trace compaction ran.
        Instead, evaluate each requested predicate on its physical version and
        retain only booleans plus the scalar metric fields in one ``argMax``
        tuple. Grouping follows the table's exact sorting identity so
        ``optimize_aggregation_in_order`` can stream the collapse.
        """

        scalar_expressions = [
            "start_time",
            "toInt64(latency_ms)",
            "toInt64(total_tokens)",
            "cost",
            "toInt64(prompt_tokens)",
            "toInt64(completion_tokens)",
            "status",
            "toUInt8(is_deleted)",
        ]
        scalar_aliases = [
            "start_time",
            "latency_ms",
            "total_tokens",
            "cost",
            "prompt_tokens",
            "completion_tokens",
            "status",
            "is_deleted",
        ]
        for index, predicate in enumerate(row_predicates):
            scalar_expressions.append(f"toUInt8(ifNull(({predicate}), 0))")
            scalar_aliases.append(f"graph_row_match_{index}")
        for index, predicate in enumerate(contribution_predicates):
            scalar_expressions.append(f"toUInt8(ifNull(({predicate}), 0))")
            scalar_aliases.append(f"graph_contribution_match_{index}")

        projected_scalars = ",\n".join(
            f"                tupleElement(graph_latest_row, {index}) AS {alias}"
            for index, alias in enumerate(scalar_aliases, start=1)
        )
        latest_tuple = ",\n".join(
            f"                            {expression}"
            for expression in scalar_expressions
        )
        tombstone_index = scalar_aliases.index("is_deleted") + 1
        candidate_trace_fragment = (
            f"\n                  AND trace_id IN %({candidate_trace_ids_param})s"
            if candidate_trace_ids_param
            else ""
        )
        return f"""(
            SELECT
                trace_id,
{projected_scalars}
            FROM (
                SELECT
                    trace_id,
                    argMax(
                        tuple(
{latest_tuple}
                        ),
                        _version
                    ) AS graph_latest_row
                FROM {self.RAW_TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND start_time >= %({scan_start_param})s
                  AND start_time < %({scan_end_param})s{candidate_trace_fragment}
                GROUP BY
                    project_id,
                    observation_type,
                    service_name,
                    toStartOfHour(start_time),
                    trace_id,
                    id
            ) AS graph_physical_versions
            WHERE tupleElement(graph_latest_row, {tombstone_index}) = 0
        ) AS graph_latest_spans"""

    def _raw_scalar_source(
        self,
        *,
        row_predicates: tuple[str, ...],
        contribution_predicates: tuple[str, ...],
        scan_start_param: str,
        scan_end_param: str,
    ) -> str:
        """Project append-only physical span rows to the graph scalars.

        An optional compiler-proven positive witness may first restrict the
        outer read to candidate trace IDs. The outer read still evaluates every
        graph predicate and aggregates every span of each candidate trace, so
        the witness affects pruning only, never result semantics.
        """

        scalar_columns = [
            "trace_id",
            "start_time",
            "toInt64(latency_ms) AS latency_ms",
            "toInt64(total_tokens) AS total_tokens",
            "cost",
            "toInt64(prompt_tokens) AS prompt_tokens",
            "toInt64(completion_tokens) AS completion_tokens",
            "status",
        ]
        scalar_columns.extend(
            f"toUInt8(ifNull(({predicate}), 0)) AS graph_row_match_{index}"
            for index, predicate in enumerate(row_predicates)
        )
        scalar_columns.extend(
            f"toUInt8(ifNull(({predicate}), 0)) AS graph_contribution_match_{index}"
            for index, predicate in enumerate(contribution_predicates)
        )
        projected_columns = ",\n                ".join(scalar_columns)

        source = self.RAW_TABLE
        candidate_source = f"{self.RAW_TABLE} AS graph_seed_spans"
        replica_predicate = ""
        if self.raw_replica_shard_cluster:
            source = (
                f"cluster('{self.raw_replica_shard_cluster}', "
                f"currentDatabase(), {self.RAW_TABLE}) AS spans"
            )
            candidate_source = (
                f"cluster('{self.raw_replica_shard_cluster}', "
                f"currentDatabase(), {self.RAW_TABLE}) AS graph_seed_spans"
            )
            self.params["graph_replica_shard_count"] = self.raw_replica_shard_count
            replica_predicate = (
                "\n              AND modulo(toRelativeDayNum(start_time), "
                "%(graph_replica_shard_count)s) = shardNum() - 1"
            )

        candidate_trace_fragment = ""
        if self.raw_trace_candidate_predicate:
            if self.observe_type != "trace":
                raise ValueError("raw trace candidates require trace graph mode")
            duplicate_params = set(self.params).intersection(
                self.raw_trace_candidate_params
            )
            if duplicate_params:
                raise ValueError(
                    f"duplicate raw trace candidate params: {duplicate_params}"
                )
            self.params.update(self.raw_trace_candidate_params)
            candidate_trace_fragment = f"""
              AND trace_id GLOBAL IN (
                  SELECT trace_id
                  FROM {candidate_source}
                  PREWHERE project_id = toUUID(%(project_id)s)
                    AND start_time >= %({scan_start_param})s
                    AND start_time < %({scan_end_param})s{replica_predicate}
                  WHERE is_deleted = 0
                    AND ({self.raw_trace_candidate_predicate})
                  GROUP BY trace_id
              )"""

        return f"""(
            SELECT
                {projected_columns}
            FROM {source}
            PREWHERE {self.project_filter_sql()}
              AND start_time >= %({scan_start_param})s
              AND start_time < %({scan_end_param})s{replica_predicate}{candidate_trace_fragment}
            WHERE is_deleted = 0
        ) AS graph_raw_spans"""

    def _graph_scalar_source(
        self,
        *,
        row_predicates: tuple[str, ...],
        contribution_predicates: tuple[str, ...],
        scan_start_param: str,
        scan_end_param: str,
    ) -> str:
        if self.resolve_span_versions:
            return self._exact_latest_scalar_source(
                row_predicates=row_predicates,
                contribution_predicates=contribution_predicates,
                scan_start_param=scan_start_param,
                scan_end_param=scan_end_param,
            )
        return self._raw_scalar_source(
            row_predicates=row_predicates,
            contribution_predicates=contribution_predicates,
            scan_start_param=scan_start_param,
            scan_end_param=scan_end_param,
        )

    def build_exact_trace_contribution_batch(
        self,
        trace_ids: list[str] | tuple[str, ...],
    ) -> tuple[str, dict[str, Any]]:
        """Return exact additive bucket states for a finite matched-trace batch.

        Trace membership is intentionally absent here.  The asynchronous exact
        reader first exhausts the trace list's latest-state cursor and passes
        only fully classified identities to this method.  Constraining the raw
        latest-state collapse by those immutable identities prevents the
        tenant-wide per-trace hash state that can exceed ClickHouse memory.

        Every output column is additive across batches.  The caller must merge
        all batches before deriving averages or publishing a snapshot.
        """

        if not self.exact_snapshot or self.observe_type != "trace":
            raise ValueError(
                "exact trace contribution batches require an exact trace builder"
            )
        normalized_trace_ids = tuple(
            dict.fromkeys(str(trace_id) for trace_id in trace_ids if trace_id)
        )
        if not normalized_trace_ids:
            return "", {}
        if len(normalized_trace_ids) > 5_000:
            raise ValueError("exact trace contribution batch exceeds 5000 identities")
        if self.start_date is None or self.end_date is None:
            self.start_date, self.end_date = self.parse_time_range(self.filters)
        # CH25 replaces versions by ``toStartOfHour(start_time)``. A newer poll
        # may correct the producer timestamp across the exact request boundary
        # while remaining in that same physical identity. Scan both boundary
        # hours completely, collapse first, then apply the frozen output window
        # through ``contribution_condition`` below.
        trace_scan_start = self.start_date.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        trace_scan_end = self.end_date.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        if trace_scan_end < self.end_date:
            trace_scan_end += timedelta(hours=1)
        self.params.update(
            {
                "start_date": self.start_date,
                "end_date": self.end_date,
                "graph_trace_scan_start": trace_scan_start,
                "graph_trace_scan_end": trace_scan_end,
                "graph_candidate_trace_ids": normalized_trace_ids,
            }
        )

        from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
            compile_exact_graph_row_predicates,
        )

        exact_filter_plan = compile_exact_graph_row_predicates(
            self.filters,
            project_id=str(self.project_id),
            observe_type=self.observe_type,
            annotation_label_ids=self.annotation_label_ids,
        )
        self.params.update(exact_filter_plan.params)
        output_window = "start_time >= %(start_date)s AND start_time < %(end_date)s"
        contribution_terms = [
            output_window,
            *(
                f"graph_contribution_match_{index} = 1"
                for index in range(len(exact_filter_plan.contribution_predicates))
            ),
        ]
        contribution_condition = " AND ".join(
            f"({predicate})" for predicate in contribution_terms
        )
        latest_source = self._exact_latest_scalar_source(
            row_predicates=(),
            contribution_predicates=exact_filter_plan.contribution_predicates,
            scan_start_param="graph_trace_scan_start",
            scan_end_param="graph_trace_scan_end",
            candidate_trace_ids_param="graph_candidate_trace_ids",
        )
        bucket_fn = self.time_bucket_expr(self.interval)
        query = f"""
        SELECT
            {bucket_fn}(start_time) AS time_bucket,
            sumIf(toInt64(latency_ms), {contribution_condition}) AS latency_sum,
            sumIf(toInt64(total_tokens), {contribution_condition})
                AS total_tokens,
            sumIf(
                toDecimal128(toString(ifNull(cost, 0.0)), 18),
                {contribution_condition}
            ) AS cost_sum,
            countIf({contribution_condition}) AS traffic_count,
            sumIf(toInt64(prompt_tokens), {contribution_condition})
                AS prompt_tokens,
            sumIf(toInt64(completion_tokens), {contribution_condition})
                AS completion_tokens,
            countIf(
                ({contribution_condition})
                AND upper(status) IN ('ERROR', 'ERRORED', 'FAILED')
            ) AS error_count
        FROM {latest_source}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def build_exact_span_partition(
        self,
        *,
        partition_start: datetime,
        partition_end: datetime,
        exact_filter_plan: Any | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return additive exact span states for one storage-identity range.

        The physical scan bounds must be whole hours because the spans table's
        replacement key contains ``toStartOfHour(start_time)``. The direct
        writer can receive a corrected exact timestamp on a newer version, so
        an arbitrary event-time cut before ``argMax`` is not exact. Scan every
        version in each covered hour, collapse current state first, and only
        then apply the intersection with the requested output window. The
        caller merges states only after every required scan succeeds.
        """

        if not self.exact_snapshot or self.observe_type != "span":
            raise ValueError("exact span partitions require an exact span builder")
        if self.start_date is None or self.end_date is None:
            self.start_date, self.end_date = self.parse_time_range(self.filters)
        if not (
            partition_start < partition_end
            and partition_start.minute == 0
            and partition_start.second == 0
            and partition_start.microsecond == 0
            and partition_end.minute == 0
            and partition_end.second == 0
            and partition_end.microsecond == 0
        ):
            raise ValueError("exact span partition must use whole-hour scan bounds")
        contribution_start = max(partition_start, self.start_date)
        contribution_end = min(partition_end, self.end_date)
        if contribution_start >= contribution_end:
            raise ValueError("exact span partition must overlap the request window")

        if exact_filter_plan is None:
            from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
                compile_exact_graph_row_predicates,
            )

            exact_filter_plan = compile_exact_graph_row_predicates(
                self.filters,
                project_id=str(self.project_id),
                observe_type=self.observe_type,
                annotation_label_ids=self.annotation_label_ids,
            )
        self.params.update(exact_filter_plan.params)
        self.params.update(
            {
                "start_date": self.start_date,
                "end_date": self.end_date,
                "graph_partition_start": partition_start,
                "graph_partition_end": partition_end,
                "graph_contribution_start": contribution_start,
                "graph_contribution_end": contribution_end,
            }
        )

        contribution_terms = [
            (
                "start_time >= %(graph_contribution_start)s "
                "AND start_time < %(graph_contribution_end)s"
            ),
            *(
                f"graph_contribution_match_{index} = 1"
                for index in range(len(exact_filter_plan.contribution_predicates))
            ),
        ]
        contribution_condition = " AND ".join(
            f"({predicate})" for predicate in contribution_terms
        )
        row_filter = " AND ".join(
            (
                f"graph_row_match_{group[0][0]} = {1 if group[0][1] else 0}"
                if len(group) == 1
                else "("
                + " OR ".join(
                    f"graph_row_match_{index} = {1 if required else 0}"
                    for index, required in group
                )
                + ")"
            )
            for group in exact_filter_plan.match_condition_groups
        )
        filters = [contribution_condition]
        if row_filter:
            filters.append(row_filter)
        exact_row_filter = " AND ".join(f"({item})" for item in filters)

        latest_source = self._exact_latest_scalar_source(
            row_predicates=exact_filter_plan.predicates,
            contribution_predicates=exact_filter_plan.contribution_predicates,
            scan_start_param="graph_partition_start",
            scan_end_param="graph_partition_end",
        )
        bucket_fn = self.time_bucket_expr(self.interval)
        query = f"""
        SELECT
            {bucket_fn}(start_time) AS time_bucket,
            sum(toInt64(latency_ms)) AS latency_sum,
            sum(toInt64(total_tokens)) AS total_tokens,
            sum(cost) AS cost_sum,
            count() AS traffic_count,
            sum(toInt64(prompt_tokens)) AS prompt_tokens,
            sum(toInt64(completion_tokens)) AS completion_tokens,
            countIf(upper(status) IN ('ERROR', 'ERRORED', 'FAILED'))
                AS error_count
        FROM {latest_source}
        WHERE {exact_row_filter}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        # Repeated partition builds mutate ``self.params``. Return an isolated
        # snapshot so a queued statement can never inherit the next slice.
        return query, dict(self.params)

    def _build_exact_raw_query(
        self,
        row_predicates: tuple[str, ...],
        output_window_only: tuple[bool, ...],
        required_matches: tuple[bool, ...],
        match_condition_groups: tuple[tuple[tuple[int, bool], ...], ...],
        contribution_predicates: tuple[str, ...],
    ) -> tuple[str, dict[str, Any]]:
        """Aggregate the configured scalar source over the bounded window.

        The ordinary statement contains exactly one physical ``spans``
        reference. A caller may provide one separately cost-gated exhaustive
        raw witness, adding a candidate-ID scan before the unchanged outer
        aggregation. ClickHouse 25.3 expands a CTE independently at each use,
        so this is emitted as an explicit pruning subquery rather than a named
        shared-scan claim. Neither route uses ``FINAL`` or sampling.

        For a filtered trace graph the raw scan immediately collapses rows to
        ``(trace_id, output bucket)``. Attribute/Map/JSON columns are consumed
        by local ``max(predicate)`` aggregates and never cross that boundary.
        A second compact aggregation computes each trace's any-sibling flags
        and packs its exact additive bucket states; the outer query merges
        those states. This preserves exact averages through ``sum / count``
        without retaining raw rows in a window-function buffer (the shape that
        exceeded the production 2-GiB query memory limit).

        A span graph applies filters directly to contributing rows. Explicit
        ``PREWHERE`` contains only immutable identity/range predicates whose
        values are shared by every physical version; the winning tombstone and
        every mutable predicate are resolved inside/after ``argMax``.
        """

        assert self.start_date is not None and self.end_date is not None
        if not (
            len(row_predicates) == len(output_window_only) == len(required_matches)
        ):
            raise AssertionError("exact graph predicate scopes must align")
        if any(
            not group
            or any(index < 0 or index >= len(row_predicates) for index, _ in group)
            for group in match_condition_groups
        ):
            raise AssertionError("exact graph match groups must reference predicates")
        bucket_fn = self.time_bucket_expr(self.interval)
        output_window = "start_time >= %(start_date)s AND start_time < %(end_date)s"
        contribution_terms = [
            output_window,
            *(
                f"graph_contribution_match_{index} = 1"
                for index in range(len(contribution_predicates))
            ),
        ]
        contribution_condition = " AND ".join(
            f"({predicate})" for predicate in contribution_terms
        )

        if self.observe_type == "trace" and row_predicates:
            local_match_columns = ",\n".join(
                "                max(toUInt8(ifNull(("
                + f"graph_row_match_{index} = 1"
                + (
                    f") AND ({output_window}), 0))) "
                    if output_window_only[index]
                    else "), 0))) "
                )
                + f"AS graph_bucket_match_{index}"
                for index in range(len(row_predicates))
            )
            trace_match_columns = ",\n".join(
                f"                max(graph_bucket_match_{index}) "
                f"AS graph_match_{index}"
                for index in range(len(row_predicates))
            )
            match_having = "\n              AND ".join(
                (
                    f"graph_match_{group[0][0]} = {1 if group[0][1] else 0}"
                    if len(group) == 1
                    else "("
                    + " OR ".join(
                        f"graph_match_{index} = {1 if required else 0}"
                        for index, required in group
                    )
                    + ")"
                )
                for group in match_condition_groups
            )
            sentinel_bucket = (
                f"{bucket_fn}(toDateTime64('1970-01-01 00:00:00', 6, 'UTC'))"
            )
            self.params["graph_witness_start_date"] = self.start_date - timedelta(
                days=1
            )
            self.params["graph_witness_end_date"] = self.end_date + timedelta(days=1)
            latest_source = self._graph_scalar_source(
                row_predicates=row_predicates,
                contribution_predicates=contribution_predicates,
                scan_start_param="graph_witness_start_date",
                scan_end_param="graph_witness_end_date",
            )
            source = f"""(
            SELECT
                graph_output_bucket
            FROM (
                SELECT
                    trace_id,
{trace_match_columns},
                    groupArrayIf(
                        tuple(
                            graph_bucket,
                            graph_latency_sum,
                            graph_total_tokens_sum,
                            graph_cost_sum,
                            graph_row_count,
                            graph_prompt_tokens_sum,
                            graph_completion_tokens_sum,
                            graph_error_count
                        ),
                        graph_in_output_window = 1 AND graph_row_count > 0
                    ) AS graph_output_buckets
                FROM (
                    SELECT
                        trace_id,
                        if(
                            {output_window},
                            {bucket_fn}(start_time),
                            {sentinel_bucket}
                        ) AS graph_bucket,
                        toUInt8({output_window}) AS graph_in_output_window,
                        sumIf(toInt64(latency_ms), {contribution_condition})
                            AS graph_latency_sum,
                        sumIf(toInt64(total_tokens), {contribution_condition})
                            AS graph_total_tokens_sum,
                        sumIf(cost, {contribution_condition})
                            AS graph_cost_sum,
                        countIf({contribution_condition}) AS graph_row_count,
                        sumIf(toInt64(prompt_tokens), {contribution_condition})
                            AS graph_prompt_tokens_sum,
                        sumIf(toInt64(completion_tokens), {contribution_condition})
                            AS graph_completion_tokens_sum,
                        countIf(
                            ({contribution_condition})
                            AND upper(status) IN ('ERROR', 'ERRORED', 'FAILED')
                        ) AS graph_error_count,
{local_match_columns}
                    FROM {latest_source}
                    GROUP BY trace_id, graph_bucket, graph_in_output_window
                ) AS graph_trace_buckets
                GROUP BY trace_id
                HAVING {match_having}
            ) AS matched_traces
            ARRAY JOIN graph_output_buckets AS graph_output_bucket
        ) AS matched_trace_buckets"""
            query = f"""
        SELECT
            tupleElement(graph_output_bucket, 1) AS time_bucket,
            sum(tupleElement(graph_output_bucket, 2))
                / greatest(sum(tupleElement(graph_output_bucket, 5)), 1)
                AS avg_latency,
            sum(tupleElement(graph_output_bucket, 3)) AS total_tokens,
            sum(tupleElement(graph_output_bucket, 4))
                / greatest(sum(tupleElement(graph_output_bucket, 5)), 1)
                AS avg_cost,
            sum(tupleElement(graph_output_bucket, 5)) AS traffic_count,
            sum(tupleElement(graph_output_bucket, 6)) AS prompt_tokens,
            sum(tupleElement(graph_output_bucket, 7)) AS completion_tokens,
            sum(tupleElement(graph_output_bucket, 8)) * 100.0
                / greatest(sum(tupleElement(graph_output_bucket, 5)), 1)
                AS error_rate
        FROM {source}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
            return query, self.params

        row_filter = " AND ".join(
            (
                f"graph_row_match_{group[0][0]} = {1 if group[0][1] else 0}"
                if len(group) == 1
                else "("
                + " OR ".join(
                    f"graph_row_match_{index} = {1 if required else 0}"
                    for index, required in group
                )
                + ")"
            )
            for group in match_condition_groups
        )
        filters = [contribution_condition]
        if row_filter:
            filters.append(row_filter)
        exact_row_filter = " AND ".join(f"({item})" for item in filters)
        latest_source = self._graph_scalar_source(
            row_predicates=row_predicates,
            contribution_predicates=contribution_predicates,
            scan_start_param="start_date",
            scan_end_param="end_date",
        )
        query = f"""
        SELECT
            {bucket_fn}(start_time) AS time_bucket,
            avg(latency_ms) AS avg_latency,
            sum(total_tokens) AS total_tokens,
            avg(cost) AS avg_cost,
            count() AS traffic_count,
            sum(prompt_tokens) AS prompt_tokens,
            sum(completion_tokens) AS completion_tokens,
            countIf(upper(status) IN ('ERROR', 'ERRORED', 'FAILED'))
                * 100.0 / greatest(count(), 1) AS error_rate
        FROM {latest_source}
        WHERE {exact_row_filter}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

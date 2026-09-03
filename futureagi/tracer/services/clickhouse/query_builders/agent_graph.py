"""One-snapshot aggregate Agent Graph query for direct-write ClickHouse.

``edges`` are exact parent -> child topology transitions. ``path_edges`` stays
empty because span hierarchy does not prove chronological execution paths;
publishing the hierarchy twice under different labels would be misleading.

Both, together with node metrics, are produced by one ClickHouse statement and
one physical ``spans`` reference.  This matters on ClickHouse 25.3: named CTEs
are expanded at every use, so separate edge/node statements (or a CTE reused by
three UNION branches) can observe different ReplacingMergeTree part snapshots.
The query below collapses every physical identity with ``argMax(_version)``,
applies mutable filters only after that collapse, packs each accepted trace into
one compact array, and emits node/hierarchy/path events through one final
``arrayJoin``.
"""

from __future__ import annotations

from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)

AGENT_GRAPH_MAX_VISIBLE_NODES = 64
AGENT_GRAPH_OTHER_NODE_ID = "aggregate:__other_nodes__"
AGENT_GRAPH_OTHER_NODE_NAME = "__other_nodes__"
# The final SQL fold emits at most N nodes plus N^2 hierarchy transitions.
# Keep one extra sentinel row in the transport limit so an
# accidental regression fails closed before Python allocates an unbounded
# response.
AGENT_GRAPH_MAX_RESULT_ROWS = (
    AGENT_GRAPH_MAX_VISIBLE_NODES
    + AGENT_GRAPH_MAX_VISIBLE_NODES * AGENT_GRAPH_MAX_VISIBLE_NODES
)
AGENT_GRAPH_RESULT_ROW_SENTINEL = AGENT_GRAPH_MAX_RESULT_ROWS + 1
AGENT_GRAPH_MAX_RESULT_BYTES = 64 * 1024 * 1024


class AgentGraphQueryBuilder(BaseQueryBuilder):
    """Build one exact latest-state Agent Graph statement."""

    TABLE = "spans"
    VERSION_COLUMN = "_version"
    DELETED_COLUMN = "is_deleted"

    def __init__(
        self,
        project_id: str,
        filters: list[dict] | None = None,
        annotation_label_ids: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.filters = list(filters or [])
        self.annotation_label_ids = (
            None if annotation_label_ids is None else tuple(annotation_label_ids)
        )
        analyzed = self.analyze_bounded_datetime_filters(self.filters, strict=True)
        self.start_date = analyzed.start
        self.end_date = analyzed.end
        self.empty_window = analyzed.empty
        self.params.update(
            {
                "start_date": self.start_date,
                "end_date": self.end_date,
                "graph_visible_keep_count": AGENT_GRAPH_MAX_VISIBLE_NODES - 1,
                "graph_other_node_name": AGENT_GRAPH_OTHER_NODE_NAME,
            }
        )

    @staticmethod
    def _make_node_id(name: str, node_type: str) -> str:
        return f"{node_type}:{name}"

    @staticmethod
    def _latest_projection(
        row_predicates: tuple[str, ...],
        contribution_predicates: tuple[str, ...],
    ) -> tuple[str, str, int]:
        """Return argMax tuple expressions, aliases, and tombstone position."""

        expressions = [
            "id",
            "parent_span_id",
            "name",
            "observation_type",
            "start_time",
            "end_time",
            "toFloat64(latency_ms)",
            "toInt64(total_tokens)",
            "toFloat64(cost)",
            "status",
            "toUInt8(is_deleted)",
        ]
        aliases = [
            "id",
            "parent_span_id",
            "name",
            "observation_type",
            "start_time",
            "end_time",
            "latency_ms",
            "total_tokens",
            "cost",
            "status",
            "is_deleted",
        ]
        for index, predicate in enumerate(row_predicates):
            expressions.append(f"toUInt8(ifNull(({predicate}), 0))")
            aliases.append(f"graph_row_match_{index}")
        for index, predicate in enumerate(contribution_predicates):
            expressions.append(f"toUInt8(ifNull(({predicate}), 0))")
            aliases.append(f"graph_contribution_match_{index}")

        tuple_sql = ",\n".join(
            f"                        {item}" for item in expressions
        )
        projection_sql = ",\n".join(
            f"            tupleElement(graph_latest_row, {index}) AS {alias}"
            for index, alias in enumerate(aliases, start=1)
        )
        return tuple_sql, projection_sql, aliases.index("is_deleted") + 1

    @staticmethod
    def _span_tuple(prefix: str) -> str:
        """Return the scalar tuple used after the wide latest-state collapse."""

        return (
            f"tuple({prefix}id, {prefix}parent_span_id, {prefix}name, "
            f"{prefix}observation_type, toUnixTimestamp64Micro({prefix}start_time), "
            "greatest(toUnixTimestamp64Micro("
            f"{prefix}start_time), toUnixTimestamp64Micro(ifNull({prefix}end_time, "
            f"addMicroseconds({prefix}start_time, toInt64(greatest("
            f"toFloat64({prefix}latency_ms), 0) * 1000))))), "
            f"toFloat64({prefix}latency_ms), toInt64({prefix}total_tokens), "
            f"toFloat64({prefix}cost), {prefix}status)"
        )

    def build(self) -> tuple[str, dict[str, Any]]:
        """Return one exact node/hierarchy/path aggregation statement."""

        plan = compile_exact_graph_row_predicates(
            self.filters,
            project_id=str(self.project_id),
            # The historical Agent Graph endpoint filters traces, then graphs
            # every contributing child of each matched trace.
            observe_type="trace",
            annotation_label_ids=self.annotation_label_ids,
        )
        self.params.update(plan.params)

        tuple_sql, projection_sql, tombstone_index = self._latest_projection(
            plan.predicates,
            plan.contribution_predicates,
        )
        output_window = "start_time >= %(start_date)s AND start_time < %(end_date)s"
        contribution_terms = [
            output_window,
            *(
                f"graph_contribution_match_{index} = 1"
                for index in range(len(plan.contribution_predicates))
            ),
        ]
        contribution_condition = " AND ".join(
            f"({term})" for term in contribution_terms
        )
        match_columns = []
        match_having = []
        for index in range(len(plan.predicates)):
            match_condition = f"graph_row_match_{index} = 1"
            match_columns.append(
                "            max(toUInt8(ifNull(("
                f"{match_condition}), 0))) AS graph_match_{index}"
            )
        for group in plan.match_condition_groups:
            match_having.append(
                f"graph_match_{group[0][0]} = {1 if group[0][1] else 0}"
                if len(group) == 1
                else "("
                + " OR ".join(
                    f"graph_match_{index} = {1 if required else 0}"
                    for index, required in group
                )
                + ")"
            )

        # A trace datetime filter belongs to its canonical root. Attribute,
        # relation, Map, and JSON membership may be witnessed by any current
        # descendant at any timestamp. Keep the graph contribution array
        # request-window bounded, but never time-bound those membership flags.
        root_window_column = (
            "            max(toUInt8(ifNull(((parent_span_id IS NULL OR "
            "parent_span_id = '') AND ("
            f"{output_window})), 0))) AS graph_root_in_output_window"
        )
        trace_projection = ",\n" + ",\n".join([root_window_column, *match_columns])
        trace_having = [
            "length(graph_spans) > 0",
            "graph_root_in_output_window = 1",
            *match_having,
        ]
        if self.empty_window:
            trace_having.append("0 = 1")
        trace_having_sql = " AND ".join(f"({item})" for item in trace_having)

        # With no non-temporal filter, only output-window rows can contribute,
        # so retain the partition-prunable physical read. Once trace membership
        # has any additional predicate, the sole matching descendant may lie
        # arbitrarily far from its in-window root. The single-snapshot collapse
        # must then replay candidate membership across all child timestamps;
        # ``groupArrayIf`` below still prevents those remote witnesses from
        # becoming graph nodes or edges.
        physical_window = ""
        if not plan.predicates:
            physical_window = (
                "\n                  AND start_time >= %(start_date)s"
                "\n                  AND start_time < %(end_date)s"
            )

        span_tuple = self._span_tuple("")
        # Tuple indexes in ``graph_spans``:
        #   1 id, 2 parent id, 3 name, 4 type, 5 start-us, 6 end-us,
        #   7 latency, 8 tokens, 9 cost, 10 status.
        node_events = """arrayMap(
                    graph_span -> tuple(
                        'node',
                        tupleElement(graph_span, 3),
                        tupleElement(graph_span, 4),
                        '',
                        '',
                        tupleElement(graph_span, 7),
                        tupleElement(graph_span, 8),
                        tupleElement(graph_span, 9),
                        toUInt8(upper(tupleElement(graph_span, 10)) IN
                            ('ERROR', 'ERRORED', 'FAILED'))
                    ),
                    graph_spans
                )"""

        def recorded_edge_events() -> str:
            """Return exact recorded parent -> child edge events.

            A span's timestamps do not prove causality between siblings.  In
            particular, Agent Graph uses only explicit ``parent_span_id``
            topology. Agent Path is unavailable until producers record an
            authoritative execution-path relation.
            """

            return """arrayFlatten(arrayMap(
                    graph_sibling_set -> if(
                        tupleElement(graph_sibling_set, 2) > 0,
                        arrayMap(
                            graph_child -> tuple(
                                'hierarchy',
                                tupleElement(graph_id_sorted_spans[
                                    tupleElement(graph_sibling_set, 2)
                                ], 3),
                                tupleElement(graph_id_sorted_spans[
                                    tupleElement(graph_sibling_set, 2)
                                ], 4),
                                tupleElement(graph_child, 3),
                                tupleElement(graph_child, 4),
                                tupleElement(graph_child, 7),
                                tupleElement(graph_child, 8),
                                tupleElement(graph_child, 9),
                                toUInt8(upper(tupleElement(graph_child, 10)) IN
                                    ('ERROR', 'ERRORED', 'FAILED'))
                            ),
                            tupleElement(graph_sibling_set, 1)
                        ),
                        []
                    ),
                    graph_sibling_sets
                ))"""

        hierarchy_events = recorded_edge_events()

        query = f"""
        WITH graph_latest_spans AS (
            SELECT
                trace_id,
{projection_sql}
            FROM (
                SELECT
                    trace_id,
                    argMax(
                        tuple(
{tuple_sql}
                        ),
                        {self.VERSION_COLUMN}
                    ) AS graph_latest_row
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  {physical_window}
                GROUP BY
                    project_id,
                    observation_type,
                    service_name,
                    toStartOfHour(start_time),
                    trace_id,
                    id
            ) AS graph_physical_versions
            WHERE tupleElement(graph_latest_row, {tombstone_index}) = 0
        ),
        graph_traces AS (
            SELECT
                trace_id,
                groupArrayIf(
                    {span_tuple},
                    {contribution_condition}
                ) AS graph_spans
{trace_projection}
            FROM graph_latest_spans
            GROUP BY trace_id
            HAVING {trace_having_sql}
        ),
        graph_ordered_traces AS (
            SELECT
                trace_id,
                graph_spans,
                arraySort(
                    graph_span -> tuple(
                        tupleElement(graph_span, 1)
                    ),
                    graph_spans
                ) AS graph_id_sorted_spans,
                arraySort(
                    graph_span -> tuple(
                        tupleElement(graph_span, 2),
                        tupleElement(graph_span, 5),
                        tupleElement(graph_span, 6),
                        tupleElement(graph_span, 1)
                    ),
                    graph_spans
                ) AS graph_sibling_sorted_spans
            FROM graph_traces
        ),
        graph_indexed_traces AS (
            SELECT
                trace_id,
                graph_spans,
                graph_id_sorted_spans,
                arrayMap(
                    graph_span -> tupleElement(graph_span, 1),
                    graph_id_sorted_spans
                ) AS graph_sorted_span_ids,
                arraySplit(
                    (graph_span, graph_index) -> graph_index > 1
                        AND tupleElement(graph_span, 2) != tupleElement(
                            graph_sibling_sorted_spans[graph_index - 1], 2
                        ),
                    graph_sibling_sorted_spans,
                    arrayEnumerate(graph_sibling_sorted_spans)
                ) AS graph_sibling_groups
            FROM graph_ordered_traces
        ),
        graph_prepared_traces AS (
            SELECT
                trace_id,
                graph_spans,
                graph_id_sorted_spans,
                arrayMap(
                    graph_siblings -> tuple(
                        graph_siblings,
                        if(
                            length(graph_siblings) = 0
                                OR tupleElement(graph_siblings[1], 2) = '',
                            toUInt64(0),
                            indexOfAssumeSorted(
                                graph_sorted_span_ids,
                                tupleElement(graph_siblings[1], 2)
                            )
                        )
                    ),
                    graph_sibling_groups
                ) AS graph_sibling_sets
            FROM graph_indexed_traces
        ),
        graph_events AS (
            SELECT
                trace_id,
                arrayJoin(arrayConcat(
                    {node_events},
                    {hierarchy_events}
                )) AS graph_event
            FROM graph_prepared_traces
        ),
        graph_trace_events AS (
            SELECT
                trace_id,
                tupleElement(graph_event, 1) AS row_kind,
                tupleElement(graph_event, 2) AS source_node,
                tupleElement(graph_event, 3) AS source_type,
                tupleElement(graph_event, 4) AS target_node,
                tupleElement(graph_event, 5) AS target_type,
                count() AS trace_item_count,
                sum(tupleElement(graph_event, 6)) AS trace_latency_sum,
                sum(tupleElement(graph_event, 7)) AS trace_total_tokens,
                sum(tupleElement(graph_event, 8)) AS trace_total_cost,
                sum(tupleElement(graph_event, 9)) AS trace_error_count
            FROM graph_events
            GROUP BY
                trace_id,
                row_kind,
                source_node,
                source_type,
                target_node,
                target_type
        ),
        graph_aggregate_events AS (
            SELECT
                row_kind,
                source_node,
                source_type,
                target_node,
                target_type,
                sum(trace_item_count) AS item_count,
                sum(trace_latency_sum) AS latency_sum,
                sum(trace_total_tokens) AS total_tokens,
                sum(trace_total_cost) AS total_cost,
                sum(trace_error_count) AS error_count,
                count() AS trace_count
            FROM graph_trace_events
            GROUP BY
                row_kind,
                source_node,
                source_type,
                target_node,
                target_type
        ),
        graph_ranked_events AS (
            SELECT
                *,
                row_number() OVER (
                    ORDER BY
                        row_kind != 'node',
                        if(row_kind = 'node', item_count, 0) DESC,
                        if(row_kind = 'node', source_type, ''),
                        if(row_kind = 'node', source_node, '')
                ) AS graph_global_rank
            FROM graph_aggregate_events
        ),
        graph_fold_inputs AS (
            SELECT
                *,
                groupArrayIf(
                    tuple(source_type, source_node),
                    row_kind = 'node'
                        AND graph_global_rank <= %(graph_visible_keep_count)s
                ) OVER () AS graph_visible_nodes,
                countIf(row_kind = 'node') OVER () AS graph_total_nodes
            FROM graph_ranked_events
        ),
        graph_fold_flags AS (
            SELECT
                *,
                has(
                    graph_visible_nodes,
                    tuple(source_type, source_node)
                ) AS graph_source_visible,
                row_kind = 'node' OR has(
                    graph_visible_nodes,
                    tuple(target_type, target_node)
                ) AS graph_target_visible
            FROM graph_fold_inputs
        ),
        graph_mapped_events AS (
            SELECT
                row_kind,
                if(
                    graph_source_visible,
                    source_node,
                    %(graph_other_node_name)s
                ) AS mapped_source_node,
                if(
                    graph_source_visible,
                    source_type,
                    'aggregate'
                ) AS mapped_source_type,
                if(
                    graph_target_visible,
                    target_node,
                    %(graph_other_node_name)s
                ) AS mapped_target_node,
                if(
                    graph_target_visible,
                    target_type,
                    'aggregate'
                ) AS mapped_target_type,
                item_count AS mapped_item_count,
                latency_sum AS mapped_latency_sum,
                total_tokens AS mapped_total_tokens,
                total_cost AS mapped_total_cost,
                error_count AS mapped_error_count,
                trace_count AS mapped_trace_count,
                graph_source_visible,
                graph_target_visible,
                graph_total_nodes
            FROM graph_fold_flags
        )
        SELECT
            row_kind,
            mapped_source_node AS source_node,
            mapped_source_type AS source_type,
            mapped_target_node AS target_node,
            mapped_target_type AS target_type,
            sum(mapped_item_count) AS item_count,
            if(
                sum(mapped_item_count) = 0,
                0,
                sum(mapped_latency_sum) / sum(mapped_item_count)
            ) AS avg_latency_ms,
            sum(mapped_total_tokens) AS total_tokens,
            sum(mapped_total_cost) AS total_cost,
            sum(mapped_error_count) AS error_count,
            if(
                graph_source_visible AND graph_target_visible,
                toNullable(sum(mapped_trace_count)),
                NULL
            ) AS trace_count,
            toUInt8(graph_source_visible AND graph_target_visible)
                AS trace_count_exact,
            max(graph_total_nodes) AS graph_total_nodes,
            if(row_kind = 'node', count(), 0) AS aggregate_member_count,
            toUInt8(graph_source_visible) AS source_endpoint_exact,
            toUInt8(graph_target_visible) AS target_endpoint_exact
        FROM graph_mapped_events
        GROUP BY
            row_kind,
            mapped_source_node,
            mapped_source_type,
            mapped_target_node,
            mapped_target_type,
            graph_source_visible,
            graph_target_visible
        ORDER BY row_kind, item_count DESC, source_type, source_node,
                 target_type, target_node
        SETTINGS
            max_threads = 1,
            optimize_aggregation_in_order = 1,
            max_bytes_before_external_group_by = 33554432,
            max_bytes_before_external_sort = 33554432,
            max_result_rows = {AGENT_GRAPH_RESULT_ROW_SENTINEL},
            max_result_bytes = {AGENT_GRAPH_MAX_RESULT_BYTES},
            result_overflow_mode = 'throw'
        """
        return query, self.params

    # Kept as a hard failure so a future call site cannot silently reintroduce
    # the old independently-snapshotted second statement.
    def build_node_metrics(self) -> tuple[str, dict[str, Any]]:
        raise RuntimeError("agent graph nodes and edges must use build() together")

    def format_result(
        self,
        rows: list[Any],
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Split the single statement's tagged rows into wire graph payloads."""

        names = list(columns or [])

        def value(row: Any, key: str, index: int, default: Any = 0) -> Any:
            if isinstance(row, dict):
                return row.get(key, default)
            if names and key in names:
                position = names.index(key)
                return row[position] if len(row) > position else default
            return row[index] if len(row) > index else default

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        original_node_count = 0
        for row in rows or []:
            kind = str(value(row, "row_kind", 0, ""))
            source_name = str(value(row, "source_node", 1, ""))
            source_type = str(value(row, "source_type", 2, "unknown"))
            target_name = str(value(row, "target_node", 3, ""))
            target_type = str(value(row, "target_type", 4, "unknown"))
            count = int(value(row, "item_count", 5, 0) or 0)
            avg_latency = float(value(row, "avg_latency_ms", 6, 0) or 0)
            total_tokens = int(value(row, "total_tokens", 7, 0) or 0)
            total_cost = float(value(row, "total_cost", 8, 0) or 0)
            error_count = int(value(row, "error_count", 9, 0) or 0)
            raw_trace_count = value(row, "trace_count", 10, 0)
            trace_count = int(raw_trace_count) if raw_trace_count is not None else None
            trace_count_exact = bool(
                value(row, "trace_count_exact", 11, raw_trace_count is not None)
            )
            original_node_count = max(
                original_node_count,
                int(value(row, "graph_total_nodes", 12, 0) or 0),
            )
            member_count = int(value(row, "aggregate_member_count", 13, 1) or 0)
            source_endpoint_exact = bool(value(row, "source_endpoint_exact", 14, True))
            target_endpoint_exact = bool(value(row, "target_endpoint_exact", 15, True))

            source_id = (
                self._make_node_id(source_name, source_type)
                if source_endpoint_exact
                else AGENT_GRAPH_OTHER_NODE_ID
            )
            if kind == "node":
                is_aggregate = not source_endpoint_exact
                nodes.append(
                    {
                        "id": source_id,
                        "name": "Other nodes" if is_aggregate else source_name,
                        "type": "aggregate" if is_aggregate else source_type,
                        "span_count": count,
                        "avg_latency_ms": round(avg_latency, 2),
                        "total_tokens": total_tokens,
                        "total_cost": round(total_cost, 6),
                        "error_count": error_count,
                        "trace_count": trace_count,
                        **(
                            {
                                "trace_count_exact": trace_count_exact,
                                "is_aggregate": True,
                                "member_count": member_count,
                            }
                            if is_aggregate
                            else {}
                        ),
                    }
                )
                continue
            if kind != "hierarchy":
                continue
            target_id = (
                self._make_node_id(target_name, target_type)
                if target_endpoint_exact
                else AGENT_GRAPH_OTHER_NODE_ID
            )
            edge = {
                "source": source_id,
                "target": target_id,
                "transition_count": count,
                "avg_latency_ms": round(avg_latency, 2),
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 6),
                "error_count": error_count,
                "trace_count": trace_count,
                "is_self_loop": source_id == target_id,
                **(
                    {"trace_count_exact": trace_count_exact, "is_aggregate": True}
                    if not (source_endpoint_exact and target_endpoint_exact)
                    else {}
                ),
            }
            edges.append(edge)

        return self._bound_result(
            nodes,
            edges,
            [],
            original_node_count=original_node_count or len(nodes),
        )

    @staticmethod
    def _bound_result(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        path_edges: list[dict[str, Any]],
        *,
        original_node_count: int | None = None,
    ) -> dict[str, Any]:
        """Keep the wire graph UI-safe without sampling or inventing topology.

        Exact aggregation can legitimately discover an unbounded vocabulary of
        span names.  A browser cannot render an unbounded V x V graph, so when
        necessary we keep the highest-volume nodes deterministically and fold
        every omitted endpoint into one explicit ``Other nodes`` vertex.  Edge
        counts and additive metrics remain exact; no transition is discarded.

        Distinct trace counts cannot be unioned from already-aggregated rows, so
        the synthetic node/edges mark that one metric unavailable instead of
        publishing a false sum.
        """

        if len(nodes) <= AGENT_GRAPH_MAX_VISIBLE_NODES:
            aggregate_nodes = [node for node in nodes if node.get("is_aggregate")]
            graph_collapsed = bool(aggregate_nodes)
            visible_original_nodes = len(nodes) - len(aggregate_nodes)
            total_nodes = max(
                int(original_node_count or 0),
                visible_original_nodes,
            )
            omitted_node_count = sum(
                max(0, int(node.get("member_count") or 0)) for node in aggregate_nodes
            )
            if graph_collapsed and not omitted_node_count:
                omitted_node_count = max(0, total_nodes - visible_original_nodes)
            return {
                "nodes": nodes,
                "edges": edges,
                "path_edges": path_edges,
                "graph_collapsed": graph_collapsed,
                "graph_node_limit": AGENT_GRAPH_MAX_VISIBLE_NODES,
                "omitted_node_count": omitted_node_count,
            }

        keep_count = AGENT_GRAPH_MAX_VISIBLE_NODES - 1
        ordered = sorted(
            nodes,
            key=lambda node: (
                -int(node.get("span_count") or 0),
                str(node.get("id") or ""),
            ),
        )
        kept = ordered[:keep_count]
        omitted = ordered[keep_count:]
        kept_ids = {str(node["id"]) for node in kept}
        all_ids = {str(node.get("id") or "") for node in nodes}
        other_node_id = AGENT_GRAPH_OTHER_NODE_ID
        while other_node_id in all_ids:
            other_node_id += ":overflow"

        omitted_span_count = sum(int(node.get("span_count") or 0) for node in omitted)
        omitted_latency_total = sum(
            float(node.get("avg_latency_ms") or 0) * int(node.get("span_count") or 0)
            for node in omitted
        )
        other_node = {
            "id": other_node_id,
            "name": "Other nodes",
            "type": "aggregate",
            "span_count": omitted_span_count,
            "avg_latency_ms": round(
                omitted_latency_total / omitted_span_count if omitted_span_count else 0,
                2,
            ),
            "total_tokens": sum(int(node.get("total_tokens") or 0) for node in omitted),
            "total_cost": round(
                sum(float(node.get("total_cost") or 0) for node in omitted), 6
            ),
            "error_count": sum(int(node.get("error_count") or 0) for node in omitted),
            "trace_count": None,
            "trace_count_exact": False,
            "is_aggregate": True,
            "member_count": len(omitted),
        }

        def collapse_edge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            buckets: dict[tuple[str, str], dict[str, Any]] = {}
            for edge in rows:
                raw_source = str(edge.get("source") or "")
                raw_target = str(edge.get("target") or "")
                source = raw_source if raw_source in kept_ids else other_node_id
                target = raw_target if raw_target in kept_ids else other_node_id
                key = (source, target)
                count = int(edge.get("transition_count") or 0)
                bucket = buckets.setdefault(
                    key,
                    {
                        "source": source,
                        "target": target,
                        "transition_count": 0,
                        "_latency_total": 0.0,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                        "error_count": 0,
                        "trace_count": 0,
                        "_collapsed": False,
                    },
                )
                bucket["transition_count"] += count
                bucket["_latency_total"] += (
                    float(edge.get("avg_latency_ms") or 0) * count
                )
                bucket["total_tokens"] += int(edge.get("total_tokens") or 0)
                bucket["total_cost"] += float(edge.get("total_cost") or 0)
                bucket["error_count"] += int(edge.get("error_count") or 0)
                bucket["trace_count"] += int(edge.get("trace_count") or 0)
                bucket["_collapsed"] = bucket["_collapsed"] or (
                    source != raw_source or target != raw_target
                )

            collapsed: list[dict[str, Any]] = []
            for key in sorted(buckets):
                bucket = buckets[key]
                count = int(bucket["transition_count"])
                trace_count_exact = not bucket["_collapsed"]
                collapsed.append(
                    {
                        "source": bucket["source"],
                        "target": bucket["target"],
                        "transition_count": count,
                        "avg_latency_ms": round(
                            bucket["_latency_total"] / count if count else 0, 2
                        ),
                        "total_tokens": bucket["total_tokens"],
                        "total_cost": round(bucket["total_cost"], 6),
                        "error_count": bucket["error_count"],
                        "trace_count": (
                            bucket["trace_count"] if trace_count_exact else None
                        ),
                        "trace_count_exact": trace_count_exact,
                        "is_self_loop": bucket["source"] == bucket["target"],
                        "is_aggregate": bool(bucket["_collapsed"]),
                    }
                )
            return collapsed

        return {
            "nodes": [*kept, other_node],
            "edges": collapse_edge_rows(edges),
            "path_edges": collapse_edge_rows(path_edges),
            "graph_collapsed": True,
            "graph_node_limit": AGENT_GRAPH_MAX_VISIBLE_NODES,
            "omitted_node_count": len(omitted),
        }

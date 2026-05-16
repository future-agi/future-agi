"""
Agent Graph Query Builder for ClickHouse.

Computes the aggregate agent topology graph for a project by analyzing
parent-child span relationships across all traces in a time window.

Two queries:
1. **Edge query**: Self-join on ``spans`` to find all parent→child
   transitions, grouped by (source_name, source_type, target_name, target_type).
2. **Node metrics query**: Per-node aggregates (span_count, latency, tokens,
   cost, errors) for all observation types.

The result is a ``{nodes, edges}`` structure ready for React Flow rendering.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class AgentGraphQueryBuilder(BaseQueryBuilder):
    """Build aggregate agent graph queries from the ``spans`` table.

    Args:
        project_id: Project UUID string.
        filters: Frontend filter list (includes date range).
        max_nodes: Maximum number of distinct nodes to return.
        max_edges: Maximum number of edges to return.
    """

    TABLE = "spans"

    def __init__(
        self,
        project_id: str,
        filters: Optional[List[Dict]] = None,
        max_nodes: int = 100,
        max_edges: int = 200,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.filters = filters or []
        self.max_nodes = max_nodes
        self.max_edges = max_edges

        self.start_date, self.end_date = self.parse_time_range(self.filters)

        # Default to 30 days when no explicit date filter is provided.
        # The base class defaults to 3650 days (10 years), which causes
        # ClickHouse to scan all historical spans — triggering OOM on large
        # projects and the intermittent errors seen in TH-4422.
        has_date_filter = any(
            f.get("column_id") in ("created_at", "start_time")
            for f in self.filters
        )
        if not has_date_filter:
            self.start_date = self.end_date - timedelta(days=30)

        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

    def build(self) -> Tuple[str, Dict[str, Any]]:
        """Build the edge aggregation query (parent→child transitions).

        Returns:
            A ``(query_string, params)`` tuple. Each row represents an edge
            between two node types with aggregate metrics.
        """
        fb = ClickHouseFilterBuilder(table="child")
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        self.params["max_edges"] = self.max_edges

        # `created_at` is the partition/sort key (`PARTITION BY
        # toYYYYMM(created_at)`, `ORDER BY (project_id, toDate(created_at),
        # trace_id, id)`), so filtering on it lets CH skip partitions and
        # granules. `start_time` stays as the semantic bound so the user's
        # window is respected exactly; `created_at` is applied with a 1-day
        # buffer to tolerate replication lag / late backfills. Adding
        # `parent.project_id` is the bigger win — without it CH scans all
        # projects on the parent side of the self-join. Together this takes
        # the query from 5s+ timeout to ~0.6s on a 236k-span, 7-day window.
        query = f"""
        SELECT
            parent.name AS source_node,
            parent.observation_type AS source_type,
            child.name AS target_node,
            child.observation_type AS target_type,
            count() AS transition_count,
            avg(child.latency_ms) AS avg_latency_ms,
            sum(child.total_tokens) AS total_tokens,
            sum(child.cost) AS total_cost,
            countIf(child.status = 'ERROR') AS error_count,
            uniq(child.trace_id) AS trace_count
        FROM {self.TABLE} AS child
        INNER JOIN {self.TABLE} AS parent
            ON child.parent_span_id = parent.id
            AND child.trace_id = parent.trace_id
            AND parent.project_id = %(project_id)s
            AND parent._peerdb_is_deleted = 0
            AND parent.start_time >= %(start_date)s
            AND parent.start_time < %(end_date)s
        WHERE child.project_id = %(project_id)s
          AND parent.project_id = %(project_id)s
          AND child._peerdb_is_deleted = 0
          AND child.parent_span_id IS NOT NULL
          AND child.parent_span_id != ''
          AND child.created_at >= %(start_date)s - INTERVAL 1 DAY
          AND parent.created_at >= %(start_date)s - INTERVAL 2 DAY
          AND child.start_time >= %(start_date)s
          AND child.start_time < %(end_date)s
          {filter_fragment}
        GROUP BY source_node, source_type, target_node, target_type
        ORDER BY transition_count DESC
        LIMIT %(max_edges)s
        SETTINGS join_algorithm = 'parallel_hash', max_threads = 8
        """
        return query, self.params

    def build_node_metrics(self) -> Tuple[str, Dict[str, Any]]:
        """Build the node metrics aggregation query.

        Returns per-node aggregates across all spans (not just edges).
        This captures nodes that may not appear in edges (e.g., leaf nodes
        without children, or root nodes without parents).

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = {
            "project_id": self.project_id,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "max_nodes": self.max_nodes,
        }

        query = f"""
        SELECT
            name AS node_name,
            observation_type AS node_type,
            count() AS span_count,
            avg(latency_ms) AS avg_latency_ms,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            countIf(status = 'ERROR') AS error_count,
            uniq(trace_id) AS trace_count
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
        GROUP BY node_name, node_type
        ORDER BY span_count DESC
        LIMIT %(max_nodes)s
        """
        return query, params

    @staticmethod
    def _make_node_id(name: str, node_type: str) -> str:
        """Create a unique node identifier from type and name."""
        return f"{node_type}:{name}"

    def format_result(
        self,
        edge_rows: List,
        edge_columns: List[str],
        node_rows: List,
        node_columns: List[str],
    ) -> Dict[str, Any]:
        """Merge edge and node query results into graph response.

        Returns:
            ``{"nodes": [...], "edges": [...]}`` dict.
        """
        # Build node lookup from node metrics query
        node_map: Dict[str, Dict[str, Any]] = {}
        for row in node_rows:
            if isinstance(row, dict):
                name = row.get("node_name", "")
                ntype = row.get("node_type", "unknown")
                span_count = row.get("span_count", 0)
                avg_lat = row.get("avg_latency_ms", 0)
                tokens = row.get("total_tokens", 0)
                cost = row.get("total_cost", 0)
                errors = row.get("error_count", 0)
                traces = row.get("trace_count", 0)
            else:
                # Tuple row: (node_name, node_type, span_count, avg_latency,
                #              total_tokens, total_cost, error_count, trace_count)
                name = row[0] if len(row) > 0 else ""
                ntype = row[1] if len(row) > 1 else "unknown"
                span_count = row[2] if len(row) > 2 else 0
                avg_lat = row[3] if len(row) > 3 else 0
                tokens = row[4] if len(row) > 4 else 0
                cost = row[5] if len(row) > 5 else 0
                errors = row[6] if len(row) > 6 else 0
                traces = row[7] if len(row) > 7 else 0

            node_id = self._make_node_id(name, ntype)
            node_map[node_id] = {
                "id": node_id,
                "name": name,
                "type": ntype,
                "span_count": span_count,
                "avg_latency_ms": round(float(avg_lat), 2) if avg_lat else 0,
                "total_tokens": int(tokens) if tokens else 0,
                "total_cost": round(float(cost), 6) if cost else 0,
                "error_count": int(errors) if errors else 0,
                "trace_count": int(traces) if traces else 0,
            }

        # Build edges from edge query
        edges: List[Dict[str, Any]] = []
        for row in edge_rows:
            if isinstance(row, dict):
                src_name = row.get("source_node", "")
                src_type = row.get("source_type", "unknown")
                tgt_name = row.get("target_node", "")
                tgt_type = row.get("target_type", "unknown")
                trans = row.get("transition_count", 0)
                avg_lat = row.get("avg_latency_ms", 0)
                tokens = row.get("total_tokens", 0)
                cost = row.get("total_cost", 0)
                errors = row.get("error_count", 0)
                traces = row.get("trace_count", 0)
            else:
                src_name = row[0] if len(row) > 0 else ""
                src_type = row[1] if len(row) > 1 else "unknown"
                tgt_name = row[2] if len(row) > 2 else ""
                tgt_type = row[3] if len(row) > 3 else "unknown"
                trans = row[4] if len(row) > 4 else 0
                avg_lat = row[5] if len(row) > 5 else 0
                tokens = row[6] if len(row) > 6 else 0
                cost = row[7] if len(row) > 7 else 0
                errors = row[8] if len(row) > 8 else 0
                traces = row[9] if len(row) > 9 else 0

            source_id = self._make_node_id(src_name, src_type)
            target_id = self._make_node_id(tgt_name, tgt_type)

            # Ensure both nodes exist in node_map
            for nid, nname, ntype in [
                (source_id, src_name, src_type),
                (target_id, tgt_name, tgt_type),
            ]:
                if nid not in node_map:
                    node_map[nid] = {
                        "id": nid,
                        "name": nname,
                        "type": ntype,
                        "span_count": 0,
                        "avg_latency_ms": 0,
                        "total_tokens": 0,
                        "total_cost": 0,
                        "error_count": 0,
                        "trace_count": 0,
                    }

            is_self_loop = source_id == target_id
            edges.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "transition_count": int(trans),
                    "avg_latency_ms": round(float(avg_lat), 2) if avg_lat else 0,
                    "total_tokens": int(tokens) if tokens else 0,
                    "total_cost": round(float(cost), 6) if cost else 0,
                    "error_count": int(errors) if errors else 0,
                    "trace_count": int(traces) if traces else 0,
                    "is_self_loop": is_self_loop,
                }
            )

        return {
            "nodes": list(node_map.values()),
            "edges": edges,
        }

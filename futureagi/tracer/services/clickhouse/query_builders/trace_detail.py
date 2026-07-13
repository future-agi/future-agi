"""Trace-detail dispatch handler — V1 (PostgreSQL).

`GET /tracer/trace/{id}/` is routed through the v1↔v2 query dispatch
(`get_query_builder_class("TRACE_DETAIL")`) like the list queries. Under a V1
routing mode the dispatch returns THIS class, which serves the trace detail from
PostgreSQL exactly as the endpoint did before the CH migration (stability: an
existing deployment behaves identically until the operator flips TRACE_DETAIL to
V2). Under V2 the dispatch returns ``TraceDetailHandlerV2`` (ClickHouse).

The handler is constructed with the ``view`` so it can reuse the view's small,
already-tested helpers (the tenant queryset and the serializer); the summary and
agent graph come from the shared ``compute_trace_summary_and_graph`` below, which
the V2 (ClickHouse) handler also calls so the two paths cannot drift. The
trace-detail data assembly itself lives here. Both v1 and v2 return the identical
response dict ``{"trace", "observation_spans", "summary", "graph"}`` which the
view wraps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from rest_framework.request import Request

    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.views.trace import TraceView


class TraceDetail(TypedDict):
    """The response envelope both handlers return; the view wraps it verbatim."""

    trace: dict[str, Any]
    observation_spans: list[dict[str, Any]]
    summary: dict[str, Any]
    graph: dict[str, Any]


def compute_trace_summary_and_graph(
    spans_tree: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Single source of truth for the trace summary + agent graph.

    Walks the assembled span tree (a list of root entries, each
    ``{"observation_span": dict, "children": list}``) once and returns
    ``(summary, graph)``. Both the V1 (PostgreSQL) and V2 (ClickHouse) handlers
    call this so the two interchangeable paths cannot drift in the totals or the
    graph shape. Works with both PG and CH span dicts.
    """
    all_spans = []
    graph_nodes = []
    graph_edges = []

    def walk(entries, parent_id=None):
        for entry in entries:
            span = entry.get("observation_span") or entry.get("observationSpan") or {}
            span_id = span.get("id", "")
            all_spans.append({"span": span, "parent_id": parent_id})
            graph_nodes.append(
                {
                    "id": span_id,
                    "name": span.get("name", ""),
                    "type": span.get("observation_type", "unknown"),
                    "latency_ms": span.get("latency_ms") or span.get("latency") or 0,
                    "tokens": span.get("total_tokens") or 0,
                    "status": span.get("status"),
                }
            )
            if parent_id:
                graph_edges.append({"from": parent_id, "to": span_id})
            children = entry.get("children", [])
            if children:
                walk(children, parent_id=span_id)

    walk(spans_tree)

    total_tokens = 0
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0
    error_count = 0
    type_counts = {}
    root_latencies = []

    for item in all_spans:
        sp = item["span"]
        total_tokens += sp.get("total_tokens") or 0
        total_prompt += sp.get("prompt_tokens") or 0
        total_completion += sp.get("completion_tokens") or 0
        total_cost += sp.get("cost") or 0.0
        if sp.get("status") == "ERROR":
            error_count += 1
        t = sp.get("observation_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        if item["parent_id"] is None:
            root_latencies.append(sp.get("latency_ms") or sp.get("latency") or 0)

    summary = {
        "total_spans": len(all_spans),
        "total_duration_ms": max(root_latencies) if root_latencies else 0,
        "total_tokens": total_tokens,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cost": round(total_cost, 6),
        "error_count": error_count,
        "span_type_counts": type_counts,
    }
    graph = {"nodes": graph_nodes, "edges": graph_edges}
    return summary, graph


class TraceDetailHandler:
    """V1 / PostgreSQL trace-detail handler (the pre-migration behavior)."""

    def __init__(
        self,
        *,
        view: TraceView,
        request: Request,
        pk: str,
        analytics: AnalyticsQueryService | None = None,
    ) -> None:
        self.view = view
        self.request = request
        self.pk = pk
        self.analytics = analytics

    def fetch(self) -> TraceDetail:
        """Assemble the trace detail from PostgreSQL.

        Cross-store tenant gate = the org/workspace-scoped queryset; the span
        tree comes from PG via ``get_observation_spans``; summary/graph are
        computed from that tree.
        """
        from tracer.models.trace import Trace
        from tracer.views.observation_span import get_observation_spans

        view = self.view
        accessible_trace = view.get_queryset().filter(id=self.pk).first()
        if not accessible_trace:
            raise Trace.DoesNotExist

        trace_data = view.get_serializer(accessible_trace).data
        observation_spans_response = get_observation_spans(
            {
                "project_id": str(accessible_trace.project_id),
                "project_version_id": (
                    str(accessible_trace.project_version_id)
                    if accessible_trace.project_version_id
                    else None
                ),
                "trace_id": str(accessible_trace.id),
            }
        )
        summary, graph = compute_trace_summary_and_graph(observation_spans_response)
        return {
            "trace": trace_data,
            "observation_spans": observation_spans_response,
            "summary": summary,
            "graph": graph,
        }

"""
Tests for the shared trace-detail summary + graph compute.

These exercise the real ``compute_trace_summary_and_graph`` helper (the single
source of truth both the V1/PG and V2/CH handlers call) rather than a copy. The
flat ``{id: {observation_span, _parent_id}}`` fixtures are assembled into the
nested span tree the helper consumes via ``_tree`` (the same shape the handlers
build), so the totals/graph are validated against the production code path.
"""

from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)


def _tree(span_map):
    """Assemble a flat ``{id: {observation_span, _parent_id}}`` fixture into the
    nested span tree the shared helper consumes (mirrors the handler build).

    Orphans (``_parent_id`` not present in the map) become roots, matching the
    handler's ``root_spans + orphan_spans`` assembly. The span ``id`` is set from
    the map key so graph node/edge ids line up with the fixtures.
    """
    nodes = {
        sid: {
            "observation_span": {**entry["observation_span"], "id": sid},
            "children": [],
        }
        for sid, entry in span_map.items()
    }
    roots = []
    for sid, entry in span_map.items():
        parent = entry.get("_parent_id")
        if parent is not None and parent in nodes:
            nodes[parent]["children"].append(nodes[sid])
        else:
            roots.append(nodes[sid])
    return roots


def _summary(span_map):
    summary, _ = compute_trace_summary_and_graph(_tree(span_map))
    return summary


def _graph(span_map):
    _, graph = compute_trace_summary_and_graph(_tree(span_map))
    return graph


class TestTraceSummaryComputation:
    """Summary totals computed by the shared helper."""

    def test_empty_span_map(self):
        result = _summary({})
        assert result["total_spans"] == 0
        assert result["total_duration_ms"] == 0
        assert result["total_tokens"] == 0
        assert result["total_cost"] == 0
        assert result["error_count"] == 0

    def test_single_root_span(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "total_tokens": 500,
                    "prompt_tokens": 200,
                    "completion_tokens": 300,
                    "cost": 0.005,
                    "status": "OK",
                    "observation_type": "chain",
                    "latency_ms": 1234,
                },
                "_parent_id": None,
            }
        }
        result = _summary(span_map)
        assert result["total_spans"] == 1
        assert result["total_duration_ms"] == 1234
        assert result["total_tokens"] == 500
        assert result["total_prompt_tokens"] == 200
        assert result["total_completion_tokens"] == 300
        assert result["total_cost"] == 0.005
        assert result["error_count"] == 0
        assert result["span_type_counts"] == {"chain": 1}

    def test_multiple_spans_with_errors(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "total_tokens": 500,
                    "prompt_tokens": 200,
                    "completion_tokens": 300,
                    "cost": 0.005,
                    "status": "OK",
                    "observation_type": "chain",
                    "latency_ms": 2000,
                },
                "_parent_id": None,
            },
            "s2": {
                "observation_span": {
                    "total_tokens": 300,
                    "prompt_tokens": 100,
                    "completion_tokens": 200,
                    "cost": 0.003,
                    "status": "ERROR",
                    "observation_type": "llm",
                    "latency_ms": 500,
                },
                "_parent_id": "s1",
            },
            "s3": {
                "observation_span": {
                    "total_tokens": 100,
                    "prompt_tokens": 50,
                    "completion_tokens": 50,
                    "cost": 0.001,
                    "status": "OK",
                    "observation_type": "tool",
                    "latency_ms": 200,
                },
                "_parent_id": "s1",
            },
        }
        result = _summary(span_map)
        assert result["total_spans"] == 3
        assert result["total_duration_ms"] == 2000  # root span latency
        assert result["total_tokens"] == 900
        assert result["total_cost"] == 0.009
        assert result["error_count"] == 1
        assert result["span_type_counts"] == {"chain": 1, "llm": 1, "tool": 1}

    def test_none_values_treated_as_zero(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "total_tokens": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "cost": None,
                    "status": "OK",
                    "observation_type": "chain",
                    "latency_ms": None,
                },
                "_parent_id": None,
            }
        }
        result = _summary(span_map)
        assert result["total_tokens"] == 0
        assert result["total_cost"] == 0
        assert result["total_duration_ms"] == 0


class TestGraphDerivation:
    """Agent graph derived by the shared helper."""

    def test_empty_graph(self):
        result = _graph({})
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_single_node_no_edges(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "name": "root",
                    "observation_type": "chain",
                    "latency_ms": 100,
                    "total_tokens": 50,
                    "status": "OK",
                },
                "_parent_id": None,
            }
        }
        result = _graph(span_map)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["name"] == "root"
        assert result["edges"] == []

    def test_parent_child_edge(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "name": "root",
                    "observation_type": "chain",
                    "latency_ms": 100,
                    "total_tokens": 0,
                    "status": "OK",
                },
                "_parent_id": None,
            },
            "s2": {
                "observation_span": {
                    "name": "child",
                    "observation_type": "llm",
                    "latency_ms": 50,
                    "total_tokens": 100,
                    "status": "OK",
                },
                "_parent_id": "s1",
            },
        }
        result = _graph(span_map)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["edges"][0] == {"from": "s1", "to": "s2"}

    def test_orphan_span_no_edge(self):
        """Span with parent_id pointing to a non-existent span gets no edge."""
        span_map = {
            "s1": {
                "observation_span": {
                    "name": "orphan",
                    "observation_type": "tool",
                    "latency_ms": 10,
                    "total_tokens": 0,
                    "status": "OK",
                },
                "_parent_id": "nonexistent",
            },
        }
        result = _graph(span_map)
        assert len(result["nodes"]) == 1
        assert result["edges"] == []  # parent not in span set -> no edge

    def test_three_level_hierarchy(self):
        span_map = {
            "s1": {
                "observation_span": {
                    "name": "root",
                    "observation_type": "chain",
                    "latency_ms": 100,
                    "total_tokens": 0,
                    "status": "OK",
                },
                "_parent_id": None,
            },
            "s2": {
                "observation_span": {
                    "name": "mid",
                    "observation_type": "agent",
                    "latency_ms": 80,
                    "total_tokens": 0,
                    "status": "OK",
                },
                "_parent_id": "s1",
            },
            "s3": {
                "observation_span": {
                    "name": "leaf",
                    "observation_type": "llm",
                    "latency_ms": 50,
                    "total_tokens": 200,
                    "status": "OK",
                },
                "_parent_id": "s2",
            },
        }
        result = _graph(span_map)
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 2
        edge_pairs = [(e["from"], e["to"]) for e in result["edges"]]
        assert ("s1", "s2") in edge_pairs
        assert ("s2", "s3") in edge_pairs

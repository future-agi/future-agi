"""Shared trace-detail summaries distinguish unpriced work from free activity."""

import pytest

from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)

pytestmark = pytest.mark.unit

# Literal expected completeness, independent of the production predicate.
CASES = [
    ("tool", 0, {"fi.cost.kind": "unknown"}, False),
    ("chain", 0, {"fi.cost.kind": "unknown"}, False),
    ("agent", 0, {"fi.cost.kind": "unknown"}, False),
    ("llm", 0, {"fi.cost.kind": "unknown"}, True),
    ("tool", 1, {"fi.cost.kind": "unknown"}, True),
    ("chain", 1, {"fi.cost.kind": "unknown"}, True),
    ("agent", 1, {"fi.cost.kind": "unknown"}, True),
    ("chain", 0, {"fi.cost.kind": "unknown", "fi.usage.scope": "turn"}, True),
    ("tool", 0, {"fi.cost.kind": "unknown", "fi.usage.scope": "turn"}, True),
    ("llm", 0, {}, False),
    ("llm", 120, {"fi.cost.kind": "unknown", "fi.usage.contribution": "none"}, False),
    (
        "agent",
        0,
        {
            "fi.cost.kind": "unknown",
            "fi.usage.contribution": "none",
            "fi.usage.scope": "turn",
        },
        False,
    ),
]


@pytest.mark.parametrize("known_cost", [0.0, 1.25])
@pytest.mark.parametrize("entry_key", ["observation_span", "observationSpan"])
@pytest.mark.parametrize("kind,tokens,attributes,unknown", CASES)
def test_native_trace_summary_cost_completeness(
    known_cost, entry_key, kind, tokens, attributes, unknown
):
    tree = [
        {
            entry_key: {
                "id": "turn",
                "name": "agent.turn",
                "observation_type": "agent",
                "cost": 0.0,
                "total_tokens": 0,
                "span_attributes": {
                    "fi.cost.kind": "unknown",
                    "fi.usage.contribution": "none",
                },
            },
            "children": [
                {
                    entry_key: {
                        "id": "request",
                        "name": "chat",
                        "observation_type": "llm",
                        "cost": known_cost,
                        "total_tokens": 120,
                    },
                    "children": [],
                },
                {
                    entry_key: {
                        "id": "activity",
                        "name": "synthetic.activity",
                        "observation_type": kind,
                        "cost": 0.0,
                        "total_tokens": tokens,
                        "span_attributes": attributes,
                    },
                    "children": [],
                },
            ],
        }
    ]
    summary, graph = compute_trace_summary_and_graph(tree)
    if unknown:
        assert summary["total_cost"] is None
    else:
        assert summary["total_cost"] == known_cost
    assert summary["total_spans"] == 3
    assert [node["id"] for node in graph["nodes"]] == ["turn", "request", "activity"]
    assert graph["edges"] == [
        {"from": "turn", "to": "request"},
        {"from": "turn", "to": "activity"},
    ]


@pytest.mark.parametrize("kind", ["agent", "tool", "chain"])
@pytest.mark.parametrize(
    "components",
    [
        {"prompt_tokens": 100},
        {"completion_tokens": 20},
        {"prompt_tokens": 100, "completion_tokens": 20},
    ],
)
@pytest.mark.parametrize("total", [{}, {"total_tokens": 0}])
@pytest.mark.parametrize("contribution,expected", [("", None), ("none", 0.25)])
def test_native_summary_component_usage_without_total(
    kind, components, total, contribution, expected
):
    # Qualified non-LLM conversion can retain component usage without deriving
    # total_tokens. That is billable work, not an empty tool/hook interval.
    tree = [
        {
            "observation_span": {
                "id": "known",
                "name": "chat",
                "observation_type": "llm",
                "cost": 0.25,
                "total_tokens": 120,
            },
            "children": [
                {
                    "observation_span": {
                        "id": "component-work",
                        "name": "agent_call",
                        "observation_type": kind,
                        "cost": 0.0,
                        **components,
                        **total,
                        "span_attributes": {
                            "fi.cost.kind": "unknown",
                            "fi.usage.contribution": contribution,
                        },
                    },
                    "children": [],
                }
            ],
        }
    ]
    summary, graph = compute_trace_summary_and_graph(tree)
    assert summary["total_cost"] == expected
    assert summary["total_spans"] == 2
    assert summary["total_tokens"] == 120
    assert [node["id"] for node in graph["nodes"]] == ["known", "component-work"]

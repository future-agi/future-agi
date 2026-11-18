"""Literal usage oracles for legacy GenAI summaries and unchanged Pydantic runs."""

import pytest

from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.query_builders.trace_usage import (
    repeated_usage_summary_ids,
)


def row(id, parent="", kind="llm", tokens=193, cost=0.25, **changes):
    return {
        "id": id,
        "parent_span_id": parent,
        "name": "request",
        "observation_type": kind,
        "total_tokens": tokens,
        "prompt_tokens": tokens - 3 if tokens else 0,
        "completion_tokens": 3 if tokens else 0,
        "cost": cost,
        "span_attributes": {},
        **changes,
    }


def agent(**changes):
    return row(
        "agent",
        kind="agent",
        span_attributes={"gen_ai.operation.name": "invoke_agent"},
        **changes,
    )


@pytest.mark.parametrize("depth", [0, 1, 8, 40])
def test_genai_summary_uses_descendant_llms_not_only_children(depth):
    spans = [agent()]
    parent = "agent"
    for i in range(depth):
        spans.append(row(f"loop-{i}", parent, "chain", tokens=0, cost=0))
        parent = f"loop-{i}"
    spans.append(row("chat", parent))
    assert repeated_usage_summary_ids(spans) == {"agent"}


@pytest.mark.parametrize(
    "changes",
    [
        {"total_tokens": 194},
        {"prompt_tokens": 191},
        {"completion_tokens": 4},
        {"cost": 0.5},
        {"observation_type": "llm"},
        {"span_attributes": {}},
    ],
)
def test_unequal_or_non_summary_usage_is_additive(changes):
    summary = {**agent(), **changes}
    assert repeated_usage_summary_ids([summary, row("chat", "agent")]) == set()


def test_parent_only_and_unrelated_equal_usage_survive():
    assert repeated_usage_summary_ids([agent()]) == set()
    assert repeated_usage_summary_ids([agent(), row("unrelated")]) == set()
    assert repeated_usage_summary_ids([agent(), row("tool", "agent", "tool")]) == set()


def test_nested_summaries_do_not_double_count_descendant_usage():
    outer = {**agent(), "id": "outer"}
    inner = {**agent(), "parent_span_id": "outer"}
    assert repeated_usage_summary_ids([outer, inner, row("chat", "agent")]) == {
        "outer",
        "agent",
    }


def test_pydantic_immediate_children_semantics_remain_unchanged():
    parent = row("pydantic", kind="chain", name="pydantic_ai.agent.run")
    assert repeated_usage_summary_ids([parent, row("chat", "pydantic")]) == {"pydantic"}
    # A tokenless intervening chain did not qualify for the old Pydantic rule.
    assert (
        repeated_usage_summary_ids(
            [
                parent,
                row("loop", "pydantic", "chain", tokens=0, cost=0),
                row("chat", "loop"),
            ]
        )
        == set()
    )


def test_cycle_terminates_without_dropping_parent_only_usage():
    summary = {**agent(), "parent_span_id": "loop"}
    assert (
        repeated_usage_summary_ids(
            [
                summary,
                row("loop", "agent", "chain", tokens=0, cost=0),
            ]
        )
        == set()
    )


def test_detail_summary_preserves_tree_and_deduplicates_strands_shape():
    leaf = {"observation_span": row("chat", "loop"), "children": []}
    loop = {
        "observation_span": row("loop", "agent", "chain", tokens=0, cost=0),
        "children": [leaf],
    }
    summary, graph = compute_trace_summary_and_graph(
        [
            {"observation_span": agent(), "children": [loop]},
        ]
    )
    assert summary["total_tokens"] == 193
    assert summary["total_prompt_tokens"] == 190
    assert summary["total_completion_tokens"] == 3
    assert summary["total_cost"] == 0.25
    assert summary["total_spans"] == 3
    assert len(graph["nodes"]) == 3
    assert graph["edges"] == [
        {"from": "agent", "to": "loop"},
        {"from": "loop", "to": "chat"},
    ]


def test_distinct_physical_calls_with_same_logical_id_are_not_deduplicated():
    summary = {
        **agent(),
        "total_tokens": 386,
        "prompt_tokens": 380,
        "completion_tokens": 6,
        "cost": 0.5,
    }
    assert repeated_usage_summary_ids(
        [
            summary,
            row("chat", "agent"),
            row("chat", "agent"),
        ]
    ) == {"agent"}


def test_unequal_physical_wrapper_sharing_id_keeps_its_usage():
    assert (
        repeated_usage_summary_ids(
            [
                agent(),
                {**agent(), "total_tokens": 194},
                row("chat", "agent"),
            ]
        )
        == set()
    )


@pytest.mark.parametrize("kind", ["agent", "tool", "llm"])
def test_explicit_nonadditive_retry_cannot_veto_legacy_summary(kind):
    normalized = row(
        "agent",
        kind=kind,
        tokens=0,
        cost=0,
        span_attributes={
            "gen_ai.operation.name": "invoke_agent",
            "fi.usage.contribution": "none",
        },
    )
    spans = [agent(), normalized, row("chat", "agent")]
    assert repeated_usage_summary_ids(spans) == {"agent"}


def test_additive_llm_sharing_summary_id_is_not_suppressed():
    spans = [agent(), row("agent"), row("chat", "agent")]
    summary, _ = compute_trace_summary_and_graph(
        [
            {
                "observation_span": spans[0],
                "children": [
                    {"observation_span": spans[2], "children": []},
                ],
            },
            {"observation_span": spans[1], "children": []},
        ]
    )
    assert summary["total_tokens"] == 386


def test_eval_reader_attribute_shape_matches_detail_shape():
    summary = agent()
    summary["attrs_string"] = summary.pop("span_attributes")
    assert repeated_usage_summary_ids([summary, row("chat", "agent")]) == {"agent"}

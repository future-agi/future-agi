"""Unknown native billing must not be rendered as a verified zero charge."""

from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit


def test_trace_detail_unknown_billable_cost_survives_mixed_known_spans():
    tree = [
        {
            "observation_span": {
                "id": "root",
                "name": "agent.turn",
                "observation_type": "agent",
                "cost": 0,
                "span_attributes": {
                    "fi.cost.kind": "unknown",
                    "fi.usage.contribution": "none",
                },
            },
            "children": [
                {
                    "observation_span": {
                        "id": "known", "observation_type": "llm", "cost": 1.25,
                    },
                    "children": [],
                },
                {
                    "observation_span": {
                        "id": "unknown", "observation_type": "llm", "cost": 0,
                        "span_attributes": {"fi.cost.kind": "unknown"},
                    },
                    "children": [],
                },
            ],
        }
    ]
    summary, _ = compute_trace_summary_and_graph(tree)
    assert summary["total_cost"] is None


def test_trace_detail_nonadditive_unknown_does_not_poison_confirmed_cost():
    tree = [
        {
            "observation_span": {
                "id": "root", "observation_type": "agent", "cost": 0,
                "span_attributes": {
                    "fi.cost.kind": "unknown", "fi.usage.contribution": "none",
                },
            },
            "children": [
                {
                    "observation_span": {
                        "id": "known", "observation_type": "llm", "cost": 1.25,
                    },
                    "children": [],
                }
            ],
        }
    ]
    summary, _ = compute_trace_summary_and_graph(tree)
    assert summary["total_cost"] == 1.25


def test_session_list_preserves_nullable_unknown_cost():
    rows = [
        {
            "session_id": "session-1", "total_cost": None,
            "total_tokens": 120, "traces_count": 1,
        }
    ]
    assert SessionListQueryBuilder.format_sessions(rows, list(rows[0]))[0]["total_cost"] is None


@pytest.mark.parametrize(
    "cls,attrs_column", [
        (SessionListQueryBuilder, "span_attr_str"),
        (SessionListQueryBuilderV2, "attrs_string"),
    ],
)
def test_session_list_cost_is_nullable_in_all_query_shapes(cls, attrs_column):
    builder = cls(project_id=str(UUID(int=1)), filters=[], page_number=0, page_size=25)
    assert attrs_column in cls._SESSION_SCAN_COLS
    for sql in (
        cls._AGGREGATE_SELECT,
        builder.build_page_hydration_query([str(UUID(int=2))])[0],
        builder._build_aggregated_count_query()[0],
    ):
        assert "fi.cost.kind" in sql
        assert "fi.usage.contribution" in sql
        assert attrs_column in sql
    hydration = builder.build_page_hydration_query([str(UUID(int=2))])[0]
    assert "countIf(isNull(trace_cost)) > 0" in hydration
    assert "countIf(latest_cost_unknown) > 0" in hydration

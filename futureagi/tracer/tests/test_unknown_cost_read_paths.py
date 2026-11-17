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
                        "id": "known",
                        "observation_type": "llm",
                        "cost": 1.25,
                    },
                    "children": [],
                },
                {
                    "observation_span": {
                        "id": "unknown",
                        "observation_type": "llm",
                        "cost": 0,
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
                "id": "root",
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
                        "id": "known",
                        "observation_type": "llm",
                        "cost": 1.25,
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
            "session_id": "session-1",
            "total_cost": None,
            "total_tokens": 120,
            "traces_count": 1,
        }
    ]
    assert (
        SessionListQueryBuilder.format_sessions(rows, list(rows[0]))[0]["total_cost"]
        is None
    )


@pytest.mark.parametrize(
    "cls,attrs_column",
    [
        (SessionListQueryBuilder, "span_attr_str"),
        (SessionListQueryBuilderV2, "attrs_string"),
    ],
)
def test_session_list_cost_is_nullable_in_all_query_shapes(cls, attrs_column):
    builder = cls(
        project_id=str(UUID(int=1)),
        filters=[
            {
                "column_id": "total_cost",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "is_null",
                },
            }
        ],
        page_number=0,
        page_size=25,
    )
    assert attrs_column in cls._SESSION_SCAN_COLS
    hydration, _ = builder.build_page_hydration_query([str(UUID(int=2))])
    filtered_queries = (
        builder.build()[0],
        builder.build_id_query()[0],
        builder.build_count_query()[0],
        builder.build_candidate_page_query()[0],
        builder.build_candidate_count_query()[0],
        builder.build_filter_match_query([str(UUID(int=2))])[0],
    )
    for sql in (*filtered_queries, hydration):
        # Coverage is reduced on latest live members, then carried through the
        # session aggregate; the final projection alone contains no provenance.
        assert f"{attrs_column}['fi.cost.kind'] = 'unknown'" in sql
        assert f"{attrs_column}['fi.usage.contribution'] = 'none'" in sql
        assert "AS latest_usage_coverage" in sql
        assert "WHERE latest_is_deleted = 0" in sql
        assert "GROUP BY project_id, member_session_id, trace_id" in sql
        assert "NOT tupleElement(s.9, 1)" in sql
        assert "arrayExists(s -> tupleElement(s.9, 2), billable_usage_spans)" in sql
        assert "if(cost_unknown, CAST(NULL, 'Nullable(Float64)')" in sql
        assert "usage_metrics.1 AS usage_cost_unknown" in sql
        assert "usage_metrics.2 AS usage_cost" in sql
        assert "ARRAY JOIN trace_usage_metrics AS usage_metrics" in sql
        assert "countIf(isNull(usage_cost)) > 0" in sql
    for sql in filtered_queries:
        assert "HAVING total_cost IS NULL" in sql
    assert (
        "member_rollup.member_total_cost, root_page.root_total_cost) AS total_cost"
        in hydration
    )

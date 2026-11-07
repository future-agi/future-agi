"""Execute the shared list/session/user and graph proofs on ClickHouse 25.3."""

import pytest

from tracer.services.clickhouse.query_builders.graph_monetary_usage import (
    graph_monetary_rows_sql,
)
from tracer.services.clickhouse.query_builders.trace_usage import usage_aggregate_sql
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- shadow global DB mutator
    _ensure_test_score_tenant_column,  # noqa: F401 -- shadow global DB mutator
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import ch as shared_ch

pytestmark = pytest.mark.integration
ch = shared_ch

COLUMNS = (
    "id",
    "parent_span_id",
    "name",
    "observation_type",
    "cost",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "graph_cost_flags",
)


def source_rows(shape):
    parent = ("agent", "", "agent-run", "agent", 0.25, 193, 190, 3, 1)
    if shape.startswith("pydantic"):
        parent = (
            *parent[:2],
            "pydantic_ai.agent.run",
            *parent[3:-1],
            int(shape == "pydantic-genai-nested"),
        )
    if shape == "unequal":
        parent = (*parent[:5], 194, *parent[6:])
    rows = [parent]
    if shape != "parent-only":
        if shape in ("nested", "pydantic-genai-nested"):
            rows.append(("loop", "agent", "event_loop", "chain", 0.0, 0, 0, 0, 0))
        rows.append(
            (
                "chat",
                "loop" if shape in ("nested", "pydantic-genai-nested") else "agent",
                "request",
                "llm",
                0.25,
                193,
                190,
                3,
                0,
            )
        )
    params = {}
    selects = []
    fields = (*COLUMNS[:-1], "genai")
    for index, row in enumerate(rows):
        values = []
        for field, value in zip(fields, row, strict=True):
            params[f"{field}_{index}"] = value
            values.append(f"%({field}_{index})s AS {field}")
        selects.append(
            "SELECT 'project' AS project_id, 'trace' AS trace_id, " + ", ".join(values)
        )
    source = "(" + " UNION ALL ".join(selects) + ")"
    return (
        f"(SELECT *, tuple(0, 0, genai) AS coverage, "
        f"tuple(toUInt8(cost > 0), toUInt8(0), toUInt8(genai)) AS graph_cost_flags FROM {source})",
        params,
    )


@pytest.mark.parametrize(
    "shape,tokens,cost",
    [
        ("direct", 193, 0.25),
        ("nested", 193, 0.25),
        ("pydantic", 193, 0.25),
        ("pydantic-genai-nested", 386, 0.5),
        ("parent-only", 193, 0.25),
        ("unequal", 387, 0.5),
    ],
)
@pytest.mark.parametrize("reader", ["list-session", "user", "graph"])
def test_genai_summary_shared_readers(ch, shape, tokens, cost, reader):
    source, params = source_rows(shape)
    if reader == "graph":
        query = graph_monetary_rows_sql(source=source, columns=COLUMNS)
        result = ch.execute(f"SELECT sum(cost) AS cost FROM ({query})", params)
        assert result[0]["cost"] == pytest.approx(cost)
    elif reader == "user":
        query = usage_aggregate_sql(coverage="coverage", known_cost_fact="cost > 0")
        result = ch.execute(
            f"SELECT trace_metrics.1 AS cost, trace_metrics.4 AS tokens FROM (SELECT {query} FROM {source}) ARRAY JOIN trace_usage_metrics AS trace_metrics",
            params,
        )
        assert result == [{"cost": cost, "tokens": tokens}]
    else:
        query = usage_aggregate_sql(coverage="coverage")
        result = ch.execute(
            f"SELECT usage_cost AS cost, usage_total_tokens AS tokens FROM (SELECT {query} FROM {source})",
            params,
        )
        assert result == [{"cost": cost, "tokens": tokens}]


@pytest.mark.parametrize("width", [1, 1000])
def test_wide_agent_summary_does_not_expand_leaf_frontiers(ch, width):
    source = """(
        SELECT 'agent' AS id, '' AS parent_span_id, 'invoke_agent' AS name,
            'agent' AS observation_type, toFloat64(%(width)s) * 0.25 AS cost,
            toInt64(%(width)s) * 193 AS total_tokens,
            toInt64(%(width)s) * 190 AS prompt_tokens,
            toInt64(%(width)s) * 3 AS completion_tokens,
            tuple(0, 0, 1) AS coverage
        UNION ALL
        SELECT concat('call-', toString(number)), 'agent', 'chat', 'llm',
            toFloat64(0.25), toInt64(193), toInt64(190), toInt64(3), tuple(0, 0, 0)
        FROM numbers(%(width)s)
    )"""
    query = usage_aggregate_sql(coverage="coverage")
    assert ch.execute(
        f"SELECT usage_cost AS cost, usage_total_tokens AS tokens FROM (SELECT {query} FROM {source})",
        {"width": width},
    ) == [{"cost": width * 0.25, "tokens": width * 193}]

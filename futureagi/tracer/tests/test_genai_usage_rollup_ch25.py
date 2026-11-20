"""Execute the shared list/session/user and graph proofs on ClickHouse 25.3."""

import pytest

from tracer.services.clickhouse.query_builders.graph_monetary_usage import (
    graph_monetary_rows_sql,
)
from tracer.services.clickhouse.query_builders.time_series import graph_cost_flags_sql
from tracer.services.clickhouse.query_builders.trace_usage import (
    usage_aggregate_sql,
    usage_coverage_sql,
)
from tracer.tests._copilot_usage_capture import captured_usage_rows
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
    rows = [(*row, 0) for row in rows]
    if shape == "upgrade-retry":
        rows[0] = (*rows[0][:3], "unknown", *rows[0][4:])
        rows.append(("agent", "", "agent-run", "agent", 0.0, 0, 0, 0, 1, 1))
    elif shape == "additive-collision":
        rows.append(("agent", "", "agent-run", "unknown", 0.25, 194, 190, 3, 1, 0))
    elif shape == "independent-llm":
        rows.append(("agent", "", "request", "llm", 0.25, 193, 190, 3, 0, 0))
    elif shape == "explicit-nonadditive":
        rows[0] = (*rows[0][:-1], 1)
    params = {}
    selects = []
    fields = (*COLUMNS[:-1], "genai", "nonadditive")
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
        f"(SELECT *, tuple(nonadditive, 0, genai) AS coverage, "
        f"tuple(toUInt8(cost > 0 AND NOT nonadditive), toUInt8(0), "
        f"toUInt8(genai), toUInt8(nonadditive)) AS graph_cost_flags FROM {source})",
        params,
    )


def aggregate_usage(ch, source, params, reader):
    if reader == "graph":
        query = graph_monetary_rows_sql(source=source, columns=COLUMNS)
        projection = "sum(cost) AS cost, sum(total_tokens) AS tokens, sum(prompt_tokens) AS prompt, sum(completion_tokens) AS completion"
        return ch.execute(f"SELECT {projection} FROM ({query})", params)
    if reader in ("user", "materialized"):
        query = usage_aggregate_sql(
            coverage="coverage",
            known_cost_fact="cost > 0" if reader == "user" else None,
            materialized=reader == "materialized",
        )
        indices = (1, 4, 5, 6) if reader == "user" else (2, 3, 4, 5)
        projection = ", ".join(
            f"trace_metrics.{index} AS {name}"
            for index, name in zip(
                indices, ("cost", "tokens", "prompt", "completion"), strict=True
            )
        )
        return ch.execute(
            f"SELECT {projection} FROM (SELECT {query} FROM {source}) ARRAY JOIN trace_usage_metrics AS trace_metrics",
            params,
        )
    query = usage_aggregate_sql(coverage="coverage")
    return ch.execute(
        f"SELECT usage_cost AS cost, usage_total_tokens AS tokens, usage_prompt_tokens AS prompt, usage_completion_tokens AS completion FROM (SELECT {query} FROM {source})",
        params,
    )


@pytest.mark.parametrize(
    "shape,tokens,prompt,completion,cost",
    [
        ("direct", 193, 190, 3, 0.25),
        ("nested", 193, 190, 3, 0.25),
        ("pydantic", 193, 190, 3, 0.25),
        ("pydantic-genai-nested", 386, 380, 6, 0.5),
        ("parent-only", 193, 190, 3, 0.25),
        ("unequal", 387, 380, 6, 0.5),
        ("upgrade-retry", 193, 190, 3, 0.25),
        ("additive-collision", 580, 570, 9, 0.75),
        ("independent-llm", 386, 380, 6, 0.5),
        ("explicit-nonadditive", 193, 190, 3, 0.25),
    ],
)
@pytest.mark.parametrize("reader", ["list-session", "materialized", "user", "graph"])
def test_genai_summary_shared_readers(
    ch, shape, tokens, prompt, completion, cost, reader
):
    source, params = source_rows(shape)
    assert aggregate_usage(ch, source, params, reader) == [
        {"cost": cost, "tokens": tokens, "prompt": prompt, "completion": completion}
    ]


@pytest.mark.parametrize("reader", ["list-session", "materialized", "user", "graph"])
def test_capture_retry_across_normalization_upgrade_with_deployed_key(ch, reader):
    from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import _SPANS_DDL

    ch.execute(_SPANS_DDL)
    source = (
        f"(SELECT *, {usage_coverage_sql(attrs='attrs_string', include_summary=True)} AS coverage, "
        f"{graph_cost_flags_sql()} AS graph_cost_flags FROM spans FINAL WHERE is_deleted = 0)"
    )
    expected = [{"cost": 0.0, "tokens": 88073, "prompt": 87533, "completion": 540}]
    # The same immutable payload arrives first before, then after the collector
    # upgrade; an additional retry must also remain stable under FINAL.
    for version, normalized, count in [(1, False, 91), (2, True, 164), (3, True, 164)]:
        rows = captured_usage_rows(normalized=normalized)
        params = {}
        values = []
        columns = (
            *rows[0],
            "project_id",
            "trace_id",
            "start_time",
            "service_name",
            "_version",
        )
        for index, row in enumerate(rows):
            row = {
                **row,
                "project_id": "00000000-0000-0000-0000-000000000001",
                "trace_id": "82875303ebfe464f9f6a55fdbc718114",
                "start_time": "2026-11-06 12:00:00",
                "service_name": "copilot",
                "_version": version,
            }
            for column, value in row.items():
                params[f"{column}_{index}"] = value
            values.append(
                "(" + ", ".join(f"%({column}_{index})s" for column in columns) + ")"
            )
        ch.execute(
            f"INSERT INTO spans ({', '.join(columns)}) VALUES {', '.join(values)}",
            params,
        )
        assert ch.execute("SELECT count() AS count FROM spans FINAL") == [
            {"count": count}
        ]
        assert aggregate_usage(ch, source, {}, reader) == expected
        if reader == "graph":
            query = graph_monetary_rows_sql(source=source, columns=COLUMNS)
            assert ch.execute(f"SELECT count() AS count FROM ({query})") == [
                {"count": count}
            ]
        # The reader correction never rewrites persisted usage or hides rows.
        assert ch.execute("SELECT sum(total_tokens) AS tokens FROM spans FINAL") == [
            {"tokens": 176146}
        ]


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

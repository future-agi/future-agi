"""The ARRAY JOIN payload must survive the nested monetary proof projection."""

import pytest

from tracer.services.clickhouse.query_builders.graph_monetary_usage import (
    graph_monetary_rows_sql,
)
from tracer.tests import test_graph_native_cost_coverage as graph_fixtures

native_graph_db = graph_fixtures.native_graph_db
pytestmark = pytest.mark.integration

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


def _query(*, zero_child=False, parent_only=False, empty=False):
    amount = 0.0 if zero_child else 1.25
    rows = [
        (
            "parent",
            "",
            "pydantic_ai.agent.run",
            "agent",
            amount,
            120,
            100,
            20,
            int(not zero_child),
            int(zero_child),
        ),
        ("independent", "", "request", "llm", 1.25, 120, 100, 20, 1, 0),
    ]
    if not parent_only:
        rows.append(("child", "parent", "request", "llm", amount, 120, 100, 20, 1, 0))
    params = {}
    selects = []
    for index, row in enumerate(rows):
        fields = []
        for name, value in zip((*COLUMNS[:-1], "known", "unknown"), row, strict=True):
            params[f"{name}_{index}"] = value
            if name not in ("known", "unknown"):
                fields.append(f"%({name}_{index})s AS {name}")
        fields.append(
            f"tuple(toUInt8(%(known_{index})s), toUInt8(%(unknown_{index})s)) AS graph_cost_flags"
        )
        selects.append(
            "SELECT 'project' AS project_id, 'trace' AS trace_id, " + ", ".join(fields)
        )
    source = "(" + " UNION ALL ".join(selects) + ")"
    if empty:
        source = f"(SELECT * FROM {source} WHERE 0)"
    sql = graph_monetary_rows_sql(source=source, columns=COLUMNS)
    return f"SELECT id, cost, graph_cost_flags FROM ({sql}) ORDER BY id", params


@pytest.mark.parametrize(
    "options,expected",
    [
        (
            {},
            [
                ("child", 1.25, (1, 0)),
                ("independent", 1.25, (1, 0)),
                ("parent", 0.0, (0, 0)),
            ],
        ),
        (
            {"zero_child": True},
            [
                ("child", 0.0, (1, 0)),
                ("independent", 1.25, (1, 0)),
                ("parent", 0.0, (0, 0)),
            ],
        ),
        (
            {"parent_only": True},
            [("independent", 1.25, (1, 0)), ("parent", 1.25, (1, 0))],
        ),
        ({"empty": True}, []),
    ],
    ids=[
        "repeated-wrapper",
        "unknown-wrapper-known-zero-child",
        "parent-only",
        "empty",
    ],
)
def test_join_payload_is_visible_to_outer_projection(
    native_graph_db, options, expected
):
    sql, params = _query(**options)
    assert (
        native_graph_db.execute(
            sql, params, settings={"prefer_column_name_to_alias": 0}
        )
        == expected
    )

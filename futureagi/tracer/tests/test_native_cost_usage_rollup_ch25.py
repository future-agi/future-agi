"""Focused real-engine reducers; full stored-reader coverage uses the live overlay.

Reuse the existing CH25 fixture, never a mock SQL result. Literal expectations
below are independent of the production reducer and preserve ordinary usage.
"""

import pytest

from tracer.services.clickhouse.query_builders.trace_usage import (
    usage_aggregate_sql,
    usage_coverage_sql,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- shadow global DB mutator
    _ensure_test_score_tenant_column,  # noqa: F401 -- shadow global DB mutator
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    ch as shared_ch,
)

pytestmark = pytest.mark.integration
ch = shared_ch


def span(**changes):
    return {
        "parent_span_id": "",
        "name": "request",
        "observation_type": "llm",
        "cost": 0.25,
        "total_tokens": 12,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "kind": "provider_reported",
        "contribution": "",
        "scope": "",
        **changes,
    }


def activity(**changes):
    return span(
        **{
            "observation_type": "tool",
            "cost": 0.0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "kind": "unknown",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "spans,expected_cost,expected_tokens",
    [
        ([span()], 0.25, 12),
        ([span(cost=0.0)], 0.0, 12),
        ([span(kind="unknown", cost=0.0)], None, 12),
        ([span(), span(kind="unknown", cost=0.0)], None, 24),
        ([span(), activity()], 0.25, 12),
        ([span(), activity(observation_type="chain")], 0.25, 12),
        ([span(cost=0.0), activity()], 0.0, 12),
        ([span(), activity(observation_type="llm")], None, 12),
        ([span(), activity(scope="turn")], None, 12),
        ([span(), activity(prompt_tokens=5)], None, 12),
        ([span(), activity(completion_tokens=5)], None, 12),
        ([span(), activity(total_tokens=5)], None, 17),
        ([span(), span(kind="unknown", contribution="none", scope="turn")], 0.25, 12),
        ([span(kind="", cost=0.0)], 0.0, 12),
        ([span(), span()], 0.50, 24),
        (
            [
                span(name="pydantic_ai.agent.run", observation_type="chain"),
                span(parent_span_id="0"),
            ],
            0.25,
            12,
        ),
        (
            [
                span(name="pydantic_ai.agent.run", observation_type="chain"),
                activity(parent_span_id="0"),
            ],
            0.25,
            12,
        ),
    ],
    ids=[
        "known",
        "explicit-zero",
        "unknown",
        "mixed",
        "tokenless-tool",
        "tokenless-chain",
        "zero-with-activity",
        "tokenless-llm",
        "turn",
        "prompt-only",
        "completion-only",
        "total-only",
        "nonadditive-summary",
        "legacy-zero",
        "distinct-equal-calls",
        "pydantic-summary",
        "parent-only",
    ],
)
def test_native_coverage_uses_billable_work_not_activity(
    ch, spans, expected_cost, expected_tokens
):
    fields = tuple(span())
    params = {}
    selects = []
    for index, row in enumerate(spans):
        params[f"id_{index}"] = str(index)
        for field in fields:
            params[f"{field}_{index}"] = row[field]
        selects.append(
            "SELECT "
            + ", ".join(f"%({field}_{index})s AS {field}" for field in ("id", *fields))
        )
    source = " UNION ALL ".join(selects)
    coverage = usage_coverage_sql(attrs="attrs_string", prefix="input.")
    aggregate = usage_aggregate_sql(prefix="live.", coverage="live.coverage")
    rows = ch.execute(
        f"""SELECT usage_cost, usage_cost_unknown, usage_total_tokens FROM (
            SELECT {aggregate} FROM (
                SELECT *, map('fi.cost.kind', kind,
                    'fi.usage.contribution', contribution, 'fi.usage.scope', scope) AS attrs_string,
                    {coverage} AS coverage
                FROM ({source}) AS input
            ) AS live
        )""",
        params,
    )
    assert len(rows) == 1
    assert rows[0]["usage_total_tokens"] == expected_tokens
    assert bool(rows[0]["usage_cost_unknown"]) is (expected_cost is None)
    if expected_cost is None:
        assert rows[0]["usage_cost"] is None
    else:
        assert rows[0]["usage_cost"] == pytest.approx(expected_cost, abs=1e-12)


def test_unopted_caller_needs_no_provenance_column(ch):
    # Session-list callers that have not opted in still supply only eight fields.
    aggregate = usage_aggregate_sql(prefix="live.")
    rows = ch.execute(f"""SELECT usage_cost, usage_total_tokens FROM (
        SELECT {aggregate} FROM (
            SELECT 'request' AS id, '' AS parent_span_id, 'request' AS name,
                'llm' AS observation_type, 0.25 AS cost, 12 AS total_tokens,
                10 AS prompt_tokens, 2 AS completion_tokens
        ) AS live
    )""")
    assert rows == [{"usage_cost": 0.25, "usage_total_tokens": 12}]

"""Trace-list rollup fallback rules, without a ClickHouse.

The SQL itself is executed in test_openrouter_broadcast_list_rollups_ch25.py;
this pins the row-merge rules and which statements carry the rollup.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TRACE_ROLLUP_COLUMNS,
    TraceListQueryBuilderV2,
    apply_trace_rollup,
)

pytestmark = pytest.mark.unit

PROJECT = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 26, 0, 17, 2, tzinfo=UTC)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def _rollup(**changes):
    return {
        "trace_rollup_present": 1,
        "trace_rollup_cost": 3.054e-05,
        "trace_rollup_total_tokens": 61,
        "trace_rollup_prompt_tokens": 17,
        "trace_rollup_completion_tokens": 44,
        "first_llm_model": "openai/gpt-4o-mini",
        "first_llm_provider": "OpenAI",
        "first_llm_input": '[{"role":"user","content":"Summarise Hamlet."}]',
        "first_llm_output": '{"role":"assistant","content":"Revenge."}',
        **changes,
    }


def _root_row(**changes):
    return {
        "cost": 0.0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "model": "",
        "provider": "",
        "input": "",
        "output": "",
        **changes,
    }


def test_empty_root_takes_usage_content_and_model_from_the_trace():
    row = _root_row()
    apply_trace_rollup(row, _rollup())
    assert row == {
        "cost": 3.054e-05,
        "total_tokens": 61,
        "prompt_tokens": 17,
        "completion_tokens": 44,
        "model": "openai/gpt-4o-mini",
        "provider": "OpenAI",
        "input": '[{"role":"user","content":"Summarise Hamlet."}]',
        "output": '{"role":"assistant","content":"Revenge."}',
    }


@pytest.mark.parametrize(
    "root_content",
    [
        {"input": "root question", "output": ""},
        {"input": "", "output": "root answer"},
        {"input": "root question", "output": "root answer"},
    ],
)
def test_a_root_with_any_content_keeps_it(root_content):
    row = _root_row(model="gpt-4o", provider="openai", **root_content)
    apply_trace_rollup(row, _rollup())
    assert {key: row[key] for key in root_content} == root_content
    assert (row["model"], row["provider"]) == ("gpt-4o", "openai")
    # Usage is still the whole-trace total.
    assert row["total_tokens"] == 61


def test_a_row_without_a_rollup_is_untouched():
    for rollup in (
        {},
        _rollup(trace_rollup_present=0),
        _rollup(trace_rollup_present=None),
    ):
        row = _root_row(cost=0.5, total_tokens=7)
        apply_trace_rollup(row, rollup)
        assert row == _root_row(cost=0.5, total_tokens=7)


def test_null_rollup_values_never_blank_a_row():
    # join_use_nulls=1 turns an unmatched or empty column into NULL.
    row = _root_row(cost=0.25, total_tokens=8, model="m")
    apply_trace_rollup(
        row,
        dict.fromkeys(TRACE_ROLLUP_COLUMNS) | {"trace_rollup_present": 1},
    )
    assert row == _root_row(cost=0.25, total_tokens=8, model="m")


def test_privacy_mode_generation_leaves_content_empty():
    row = _root_row()
    apply_trace_rollup(row, _rollup(first_llm_input="", first_llm_output=""))
    assert (row["input"], row["output"]) == ("", "")
    assert row["total_tokens"] == 61


def _identity():
    start = NOW - timedelta(minutes=1)
    start_us = int(start.timestamp() * 1_000_000)
    return (
        PROJECT,
        "trace",
        "root",
        start_us,
        "chain",
        "openrouter",
        start_us // 3_600_000_000 * 3_600_000_000,
        1,
    )


def _builder():
    return TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [NOW - timedelta(days=7), NOW],
                },
            }
        ],
    )


def test_only_the_opted_in_content_replay_carries_the_rollup():
    plain, plain_params = _builder().build_content_query(
        ["trace"], root_identities=[_identity()]
    )
    rolled, params = _builder().build_content_query(
        ["trace"], root_identities=[_identity()], include_trace_rollup=True
    )
    assert "trace_rollup" not in plain
    assert "content_rollup_trace_identities" not in plain_params
    for column in TRACE_ROLLUP_COLUMNS:
        assert column in rolled
    assert params["content_rollup_trace_identities"] == ((PROJECT, "trace"),)
    # The span pass is bounded by the page identities and reads lengths, not
    # content; content is read once, by exact key, for LLM spans only.
    assert (
        "(toString(project_id), trace_id) IN %(content_rollup_trace_identities)s"
        in rolled
    )
    assert "input_length" in rolled and "output_length" in rolled
    assert rolled.count("argMax(tuple(input, output), _version)") == 1
    assert "AND observation_type = 'llm'" in rolled
    assert rolled.count(") AS trace_rollup_rows") == 1


def test_rollup_rides_on_content_replay_only():
    with pytest.raises(ValueError, match="content replay only"):
        _builder()._root_replay_query([_identity()], content=False, trace_rollup=True)

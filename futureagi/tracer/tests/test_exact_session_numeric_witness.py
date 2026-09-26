"""Numeric witness admission contracts; no private captures or database I/O."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    application_read_settings,
)
from tracer.tests import test_exact_session_scalar_witness as existing

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "operation,value,enabled",
    [
        ("greater_than", 1, True),
        ("greater_than", 0, True),
        ("greater_than_or_equal", 1, True),
        ("less_than", -1, True),
        ("less_than_or_equal", -1, True),
        ("between", [1, 2], True),
        ("between", [-2, -1], True),
        ("greater_than", -1, False),
        ("greater_than_or_equal", 0, False),
        ("less_than", 1, False),
        ("less_than_or_equal", 0, False),
        ("between", [-1, 1], False),
        ("not_between", [1, 2], False),
        ("not_equals", 1, False),
        ("is_null", 1, False),
        ("is_not_null", 1, False),
    ],
)
def test_numeric_witness_requires_compiler_proof_not_operator_alone(
    operation, value, enabled
):
    filters = existing._filters(
        existing._leaf(operation, value, "number", "agent.duration_s")
    )
    plan = graph._session_membership_plan(project_id=existing.PROJECT, filters=filters)
    assert bool(plan.scalar_witness_predicate) is enabled
    sql, params = existing._source(filters, enabled=True)
    baseline, baseline_params = existing._source(filters, enabled=False)
    assert ("session_scalar_witness_ids AS (" in sql) is enabled
    assert params == baseline_params
    assert sql.split("resolved_session_filter_spans AS (", 1)[1] == baseline.split(
        "resolved_session_filter_spans AS (", 1
    )[1]


def test_multiple_leaves_and_existing_typed_admission_are_preserved():
    numeric = existing._leaf("greater_than", 1, "number", "agent.duration_s")
    for filters, enabled in (
        (existing._filters(numeric, existing._leaf()), False),
        (existing._filters(existing._leaf()), True),
        (existing._filters(existing._leaf("equals", 0, "number")), False),
    ):
        plan = graph._session_membership_plan(project_id=existing.PROJECT, filters=filters)
        assert bool(plan.scalar_witness_predicate) is enabled


def test_numeric_witness_changes_only_identity_replay_scope(monkeypatch):
    # Portable synthetic equivalent of the retained private 532 shape check.
    # No private project IDs, captured values, frozen SQL hashes or /tmp imports.
    filters = existing._filters(
        existing._leaf("greater_than", 1, "number", "agent.duration_s")
    )
    current = graph._session_membership_plan
    captures = []

    def without_witness(**kwargs):
        return replace(current(**kwargs), scalar_witness_predicate=None)

    for plan in (without_witness, current):
        calls = []

        def record(sql, params, *, _calls=calls, **options):
            _calls.append((sql, dict(params), options))
            probe = sql.lstrip().startswith("SELECT 1 AS has_raw_witness")
            return SimpleNamespace(data=[{"has_raw_witness": 1}] if probe else [], columns=[])

        with monkeypatch.context() as patch:
            patch.setattr(graph, "_session_membership_plan", plan)
            result = graph.read_exact_session_system_graph(
                analytics=SimpleNamespace(execute_ch_query=record),
                project_id=existing.PROJECT,
                filters=filters,
                interval="day",
                metric_id="latency",
            )
        probes = [call for call in calls if call[0].lstrip().startswith("SELECT 1 AS has_raw_witness")]
        full = [call for call in calls if not call[0].lstrip().startswith("SELECT 1 AS has_raw_witness")]
        assert len(probes) == int(plan is current)
        assert len(full) == 1 and result["query_count"] == len(calls)
        if probes:
            assert calls[0] is probes[0] and calls[1] is full[0]
            assert probes[0][2]["settings"] == graph.EXACT_GRAPH_READ_SETTINGS
        captures.append(full[0])
    baseline, candidate = captures
    assert "session_scalar_witness_ids AS (" not in baseline[0]
    assert "session_scalar_witness_ids AS (" in candidate[0]
    assert baseline[1] == candidate[1]
    assert {key: type(value) for key, value in baseline[1].items()} == {
        key: type(value) for key, value in candidate[1].items()
    }
    assert baseline[0].split("latest_session_filter_spans AS (", 1)[0] == candidate[
        0
    ].split("session_scalar_witness_ids AS (", 1)[0]
    assert baseline[0].split("resolved_session_filter_spans AS (", 1)[1] == candidate[
        0
    ].split("resolved_session_filter_spans AS (", 1)[1]
    witness = candidate[0].split("session_scalar_witness_ids AS (", 1)[1].split(
        "latest_session_filter_spans AS (", 1
    )[0]
    assert all(token not in witness for token in ("LIMIT", "SAMPLE", "is_deleted", "trace_session_id"))
    assert "attrs_number[" in witness
    assert "physical_hour, trace_id, id" in candidate[0]
    settings = [application_read_settings(capture[2]["settings"]) for capture in captures]
    assert all(all(item[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS) for item in settings)
    assert settings[0]["max_memory_usage"] == settings[1]["max_memory_usage"] > 0
    assert settings[0]["max_threads"] == settings[1]["max_threads"] == 1


def test_existing_failure_and_complete_empty_contracts_remain():
    existing.test_programming_error_is_not_swallowed_or_retried()
    existing.test_failed_fallback_cannot_publish_empty_success()
    existing.test_exhausted_empty_witness_is_complete_not_a_budget_failure()

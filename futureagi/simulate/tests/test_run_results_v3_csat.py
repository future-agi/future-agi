"""V3 CSAT reads require an explicit score rather than a provider success value."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection

from simulate.models import CallExecution
from simulate.services.run_results_v3 import _csat_score, build_call_rows
from simulate.services.run_results_v3_queries import run_calls_queryset

CSAT_CASES = [
    ({"csat_score": 0}, 8, 0.0),
    ({"csat_score": 1}, 8, 1.0),
    ({"csat_score": 10}, 0, 10.0),
    ({"csat_score": "1"}, 8, 1.0),
    ({"csat_score": "8.5"}, 0, 8.5),
    ({}, 8, None),
    ({}, 1, None),
    ({}, 0, None),
    ({"csat_score": None}, 8, None),
    ({"csat_score": True}, 8, None),
    ({"csat_score": False}, 8, None),
    ({"csat_score": "unavailable"}, 8, None),
    ({"csat_score": "NaN"}, 8, None),
    ({"csat_score": "Infinity"}, 8, None),
    ({"csat_score": -1}, 8, None),
    ({"csat_score": 11}, 8, None),
    ({"csat_score": []}, 8, None),
    ({"csat_score": {}}, 8, None),
]


@pytest.mark.parametrize("metrics,provider_score,expected", CSAT_CASES)
def test_call_rows_csat_requires_explicit_score(metrics, provider_score, expected):
    call = SimpleNamespace(
        id="call-1",
        row_id=None,
        call_metadata={},
        provider_call_data={},
        test_execution=SimpleNamespace(agent_definition=None),
        scenario=SimpleNamespace(name="Scenario", metadata={}),
        conversation_metrics_data=metrics,
        overall_score=provider_score,
        avg_agent_latency_ms=None,
        avg_stop_time_after_interruption_ms=None,
        ai_interruption_count=None,
        eval_outputs={},
        status="completed",
        simulation_call_type="voice",
        started_at=None,
        completed_at=None,
        duration_seconds=1,
        customer_cost_cents=None,
        stt_cost_cents=None,
        llm_cost_cents=None,
        tts_cost_cents=None,
        storage_cost_cents=None,
        ended_reason=None,
        error_message=None,
    )
    with (
        patch(
            "simulate.services.run_results_v3.SimulateEvalConfig.objects.filter"
        ) as configs,
        patch("simulate.services.run_results_v3._harness_scenarios", return_value={}),
    ):
        configs.return_value.select_related.return_value = []
        rows, _ = build_call_rows(SimpleNamespace(), [call], [], set())
    assert rows[0]["csat"] == expected


@pytest.mark.parametrize("metrics", [None, [], "unavailable"])
def test_csat_helper_rejects_invalid_metrics_document(metrics):
    assert _csat_score(metrics) is None


@pytest.mark.django_db
@pytest.mark.parametrize("metrics,provider_score,expected", CSAT_CASES)
def test_sql_csat_requires_explicit_score(metrics, provider_score, expected):
    with patch(
        "simulate.services.run_results_v3_queries.SimulateEvalConfig.objects.filter"
    ) as configs:
        configs.return_value.select_related.return_value = []
        queryset = run_calls_queryset(SimpleNamespace(run_test=None), [])
    query = queryset.query
    sql, params = query.get_compiler(connection=connection).compile(
        query.annotations["result_csat"]
    )
    table = connection.ops.quote_name(CallExecution._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {sql} FROM (SELECT %s::jsonb AS conversation_metrics_data, "
            f"%s::double precision AS overall_score) {table}",
            [*params, json.dumps(metrics), provider_score],
        )
        actual = cursor.fetchone()[0]
    assert actual == expected


@pytest.mark.parametrize("csat", [None, 0.0, 1.0, 10.0])
def test_v3_detail_uses_explicit_csat_for_existing_score_fields(csat):
    from simulate.views.run_results_v3 import build_call_execution_detail

    row = dict.fromkeys(
        (
            "goal",
            "scenario_details",
            "ideal_outcome",
            "conversation_branch",
            "persona",
            "persona_details",
            "sub_goals",
            "outcome",
            "cost_breakdown_cents",
            "evaluations",
            "csat",
        )
    )
    row["csat"] = csat
    call = SimpleNamespace(test_execution=SimpleNamespace(), call_metadata={})
    with (
        patch(
            "simulate.views.run_results_v3.build_call_rows", return_value=([row], [])
        ),
        patch("simulate.views.run_results_v3.build_eval_configs_map", return_value={}),
        patch("simulate.views.run_results_v3.function_calls", return_value=[]),
        patch(
            "simulate.views.run_results_v3.CallExecutionDetailSerializer"
        ) as serializer,
    ):
        serializer.return_value.data = {"overall_score": 8, "csat_score": 8}
        detail = build_call_execution_detail(call)
    assert detail["overall_score"] == csat
    assert detail["csat_score"] == csat

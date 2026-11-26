import json

import pytest

from simulate.tests import test_execution_grouping

chat_execution = test_execution_grouping.execution_with_chat_metrics


def _status_bucket(client, execution):
    response = client.get(
        f"/simulate/test-executions/{execution.id}/",
        {"row_groups": json.dumps(["status"])},
    )
    assert response.status_code == 200, response.data
    assert response.data["count"] == 1
    (bucket,) = response.data["results"]
    assert bucket["count"] == 2
    return bucket


@pytest.mark.parametrize("metric", ["avg_latency_ms", "csat_score"])
def test_explicit_null_does_not_discard_bucket_or_other_metrics(
    auth_client, chat_execution, metric
):
    execution, (first, second) = chat_execution
    first.conversation_metrics_data[metric] = None
    second.conversation_metrics_data["csat_score"] = 4.25
    for call in (first, second):
        call.save(update_fields=["conversation_metrics_data"])

    bucket = _status_bucket(auth_client, execution)
    assert bucket["avg_latency_ms"] == (300 if metric == "avg_latency_ms" else 200)
    assert bucket["csat_score"] == (4.25 if metric == "csat_score" else 3.625)
    assert bucket["total_tokens"] == 30
    assert bucket["input_tokens"] == bucket["output_tokens"] == 15
    assert bucket["turn_count"] == 3
    assert bucket["avg_response_time"] == 200
    assert bucket["avg_overall_score"] == 4


@pytest.mark.parametrize(
    ("first_metrics", "second_metrics", "latency", "csat"),
    [
        ({"avg_latency_ms": None, "csat_score": None},) * 2 + (None, None),
        ({}, {"avg_latency_ms": 100, "csat_score": 4.25}, 100, 4.25),
        (None, {"avg_latency_ms": 100, "csat_score": 4.25}, 100, 4.25),
        (
            {"avg_latency_ms": 0, "csat_score": 0},
            {"avg_latency_ms": 100, "csat_score": 4.25},
            50,
            2.125,
        ),
    ],
    ids=["all-json-null", "missing-keys", "sql-null-object", "zeros-participate"],
)
def test_nullable_average_inputs(
    auth_client, chat_execution, first_metrics, second_metrics, latency, csat
):
    execution, calls = chat_execution
    for call, metrics in zip(calls, (first_metrics, second_metrics), strict=True):
        call.conversation_metrics_data = metrics
        call.save(update_fields=["conversation_metrics_data"])

    bucket = _status_bucket(auth_client, execution)
    assert bucket["avg_latency_ms"] == latency
    assert bucket["csat_score"] == csat

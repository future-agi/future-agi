import json
import uuid
from datetime import UTC, datetime

import pytest
from django.core.cache import cache

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate


def _make_eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"metric-contract-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        visible_ui=True,
    )


def _forbid_postgres_api_call_logs(monkeypatch):
    class ForbiddenManager:
        def filter(self, *_args, **_kwargs):
            raise AssertionError("Eval metrics must not query APICallLog")

    class ForbiddenAPICallLog:
        objects = ForbiddenManager()

    monkeypatch.setattr(
        "model_hub.views.separate_evals.APICallLog",
        ForbiddenAPICallLog,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_eval_metric_endpoint_uses_one_bounded_clickhouse_aggregate(
    auth_client,
    organization,
    workspace,
    monkeypatch,
    method,
):
    cache.clear()
    template = _make_eval_template(organization, workspace)
    _forbid_postgres_api_call_logs(monkeypatch)
    calls = []

    class ClickHouse:
        def execute_read(self, query, params, timeout_ms, settings):
            calls.append((query, params, timeout_ms, settings))
            return (
                [
                    (
                        3,
                        66.67,
                        [
                            (datetime(2026, 7, 29), 2, 50.0),
                            (datetime(2026, 7, 30), 1, 100.0),
                        ],
                    )
                ],
                [],
                12.0,
            )

    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: ClickHouse(),
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-29T00:00:00Z",
                    "2026-07-30T23:59:59Z",
                ],
            },
        }
    ]
    payload = {
        "eval_template_id": str(template.id),
        "filters": filters,
    }
    if method == "get":
        response = auth_client.get(
            "/model-hub/get-eval-metrics",
            {
                "eval_template_id": str(template.id),
                "filters": json.dumps(filters),
            },
        )
    else:
        response = auth_client.post(
            "/model-hub/get-eval-metrics",
            payload,
            format="json",
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["base_eval_template_id"] == str(template.id)
    assert result["api_call_count"]["api_call_count"] == 3
    assert result["average"]["average"] == 66.67
    assert result["query_complete"] is True
    assert result["query_status"] == "complete"
    assert [
        point["value"] for point in result["api_call_count"]["count_graph_data"]
    ] == [
        2,
        1,
    ]
    assert [point["value"] for point in result["average"]["avg_graph_data"]] == [
        50.0,
        100.0,
    ]

    assert len(calls) == 1
    query, params, timeout_ms, settings = calls[0]
    assert query.count("FROM usage_apicalllog FINAL") == 1
    assert timeout_ms == 750
    assert settings["max_threads"] == 2
    assert settings["max_rows_to_read"] == 2_000_000
    assert settings["max_bytes_to_read"] == 64 * 1024 * 1024
    assert settings["read_overflow_mode"] == "throw"
    assert params["organization_id"] == str(organization.id)
    assert params["workspace_id"] == str(workspace.id)
    assert params["start_date"] == datetime(2026, 7, 29, tzinfo=UTC)


@pytest.mark.django_db
def test_eval_metric_endpoint_fails_open_without_postgres_history_scan(
    auth_client,
    organization,
    workspace,
    monkeypatch,
):
    cache.clear()
    template = _make_eval_template(organization, workspace)
    _forbid_postgres_api_call_logs(monkeypatch)

    class ClickHouse:
        def execute_read(self, *_args, **_kwargs):
            raise TimeoutError("read budget exceeded")

    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: ClickHouse(),
    )
    response = auth_client.get(
        "/model-hub/get-eval-metrics",
        {"eval_template_id": str(template.id)},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["api_call_count"] == {
        "api_call_count": 0,
        "count_graph_data": [],
    }
    assert result["average"] == {"average": 0, "avg_graph_data": []}
    assert result["query_complete"] is False
    assert result["query_status"] == "degraded"
    assert result["query_error_code"] == "read_budget_exceeded"


@pytest.mark.django_db
def test_eval_metric_endpoint_serves_stale_cache_after_clickhouse_timeout(
    auth_client,
    organization,
    workspace,
    monkeypatch,
):
    from model_hub.views.separate_evals import (
        _eval_metric_cache_keys,
        _eval_metric_window,
    )

    cache.clear()
    template = _make_eval_template(organization, workspace)
    state = {"fail": False}

    class ClickHouse:
        def execute_read(self, *_args, **_kwargs):
            if state["fail"]:
                raise TimeoutError("read budget exceeded")
            return (
                [(1, 100.0, [(datetime(2026, 7, 30), 1, 100.0)])],
                [],
                5.0,
            )

    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: ClickHouse(),
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-30T00:00:00Z",
                    "2026-07-30T23:59:59Z",
                ],
            },
        }
    ]
    request_params = {
        "eval_template_id": str(template.id),
        "filters": json.dumps(filters),
    }
    first = auth_client.get("/model-hub/get-eval-metrics", request_params)
    assert first.status_code == 200

    start_date, end_date = _eval_metric_window(filters)
    fresh_key, _stale_key = _eval_metric_cache_keys(
        template,
        organization_id=organization.id,
        workspace=workspace,
        start_date=start_date,
        end_date=end_date,
        filters=filters,
    )
    cache.delete(fresh_key)
    state["fail"] = True

    second = auth_client.get("/model-hub/get-eval-metrics", request_params)
    assert second.status_code == 200
    assert (
        second.json()["result"]["api_call_count"]
        == first.json()["result"]["api_call_count"]
    )
    assert second.json()["result"]["average"] == first.json()["result"]["average"]
    assert second.json()["result"]["query_complete"] is False
    assert second.json()["result"]["query_status"] == "stale"


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_eval_metric_programming_error_is_sanitized_instead_of_degraded(
    auth_client,
    organization,
    workspace,
    monkeypatch,
    method,
):
    cache.clear()
    template = _make_eval_template(organization, workspace)
    _forbid_postgres_api_call_logs(monkeypatch)

    class ClickHouse:
        def execute_read(self, *_args, **_kwargs):
            raise RuntimeError("secret malformed eval metric SQL")

    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: ClickHouse(),
    )
    payload = {"eval_template_id": str(template.id)}
    if method == "get":
        response = auth_client.get("/model-hub/get-eval-metrics", payload)
    else:
        response = auth_client.post(
            "/model-hub/get-eval-metrics",
            payload,
            format="json",
        )

    assert response.status_code == 400
    body = json.dumps(response.json())
    assert "secret malformed eval metric SQL" not in body
    assert "Unable to load evaluation metrics" in body

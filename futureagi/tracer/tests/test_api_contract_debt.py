import json
from pathlib import Path

import pytest
from django.db import DatabaseError
from rest_framework import status

TRACER_GUARD_UUID = "00000000-0000-0000-0000-000000000001"


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def test_tracer_contract_debt_is_fully_burned_down():
    report = _debt_report()
    tracer_report = report["by_group"]["tracer"]

    assert tracer_report["mutation_endpoints_without_body_schema"] == 0
    assert tracer_report["operations_without_response_schema"] == 0
    assert tracer_report["operations_without_error_response_schema"] == 0
    assert tracer_report["broad_error_response_schemas"] == 0


def _tracer_saved_view_guard_cases():
    return [
        ("get", "/tracer/saved-views/", None),
        ("post", "/tracer/saved-views/", {}),
        ("post", "/tracer/saved-views/reorder/", {}),
        ("get", f"/tracer/saved-views/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/saved-views/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/saved-views/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/saved-views/{TRACER_GUARD_UUID}/", None),
        (
            "post",
            f"/tracer/saved-views/{TRACER_GUARD_UUID}/duplicate/",
            {},
        ),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_saved_view_guard_cases())
def test_tracer_saved_view_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_shared_link_guard_cases():
    return [
        ("get", "/tracer/shared-links/", None),
        ("post", "/tracer/shared-links/", {}),
        ("get", f"/tracer/shared-links/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/shared-links/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/shared-links/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/shared-links/{TRACER_GUARD_UUID}/", None),
        ("post", f"/tracer/shared-links/{TRACER_GUARD_UUID}/access/", {}),
        (
            "delete",
            f"/tracer/shared-links/{TRACER_GUARD_UUID}/access/{TRACER_GUARD_UUID}/",
            None,
        ),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_shared_link_guard_cases())
def test_tracer_shared_link_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_alert_guard_cases():
    return [
        ("get", "/tracer/user-alerts/", None),
        ("get", f"/tracer/user-alerts/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/user-alerts/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/user-alerts/{TRACER_GUARD_UUID}/", None),
        ("get", "/tracer/user-alert-logs/", None),
        ("post", "/tracer/user-alert-logs/", {}),
        ("get", "/tracer/user-alert-logs/all/", None),
        ("get", f"/tracer/user-alert-logs/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/user-alert-logs/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/user-alert-logs/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/user-alert-logs/{TRACER_GUARD_UUID}/", None),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_alert_guard_cases())
def test_tracer_alert_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_eval_task_guard_cases():
    return [
        ("get", "/tracer/eval-task/", None),
        ("get", f"/tracer/eval-task/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/eval-task/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/eval-task/{TRACER_GUARD_UUID}/", None),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_eval_task_guard_cases())
def test_tracer_eval_task_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_custom_eval_config_guard_cases():
    return [
        ("get", "/tracer/custom-eval-config/", None),
        ("post", "/tracer/custom-eval-config/", {}),
        ("post", "/tracer/custom-eval-config/check_exists/", {}),
        ("post", "/tracer/custom-eval-config/get_custom_eval_by_name/", {}),
        ("get", "/tracer/custom-eval-config/list_custom_eval_configs/", None),
        ("post", "/tracer/custom-eval-config/run_evaluation/", {}),
        ("get", f"/tracer/custom-eval-config/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/custom-eval-config/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/custom-eval-config/{TRACER_GUARD_UUID}/", {}),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_custom_eval_config_guard_cases())
def test_tracer_custom_eval_config_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_dataset_guard_cases():
    return [
        ("get", "/tracer/dataset/", None),
        ("post", "/tracer/dataset/", {}),
        ("post", "/tracer/dataset/add_to_existing_dataset/", {}),
        ("post", "/tracer/dataset/add_to_new_dataset/", {}),
        ("get", f"/tracer/dataset/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/dataset/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/dataset/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/dataset/{TRACER_GUARD_UUID}/", None),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_dataset_guard_cases())
def test_tracer_dataset_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_observability_provider_guard_cases():
    return [
        ("get", "/tracer/observability-provider/", None),
        ("post", "/tracer/observability-provider/", {}),
        ("post", "/tracer/observability-provider/verify_api_key/", {}),
        ("post", "/tracer/observability-provider/verify_assistant_id/", {}),
        ("get", f"/tracer/observability-provider/{TRACER_GUARD_UUID}/", None),
        ("put", f"/tracer/observability-provider/{TRACER_GUARD_UUID}/", {}),
        ("patch", f"/tracer/observability-provider/{TRACER_GUARD_UUID}/", {}),
        ("delete", f"/tracer/observability-provider/{TRACER_GUARD_UUID}/", None),
    ]


@pytest.mark.parametrize(
    "method,path,body", _tracer_observability_provider_guard_cases()
)
def test_tracer_observability_provider_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_replay_session_guard_cases():
    return [
        ("get", "/tracer/replay-session/", None),
        ("post", "/tracer/replay-session/", {}),
        ("get", "/tracer/replay-session/eval-configs/", None),
        ("get", f"/tracer/replay-session/{TRACER_GUARD_UUID}/", None),
        (
            "post",
            f"/tracer/replay-session/{TRACER_GUARD_UUID}/generate-scenario/",
            {},
        ),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_replay_session_guard_cases())
def test_tracer_replay_session_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


def _tracer_observe_helper_guard_cases():
    return [
        ("get", "/tracer/get-annotation-labels/", None),
        ("get", "/tracer/imagine-analysis/", None),
        ("post", "/tracer/imagine-analysis/", {}),
        ("get", "/tracer/users/", None),
        ("get", "/tracer/users/get_code_example/", None),
    ]


@pytest.mark.parametrize("method,path,body", _tracer_observe_helper_guard_cases())
def test_tracer_observe_helper_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json") if body is not None else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.django_db
def test_tracer_system_public_routes_keep_expected_boundary_semantics(api_client):
    health = api_client.get("/tracer/v1/health")
    assert health.status_code == status.HTTP_200_OK
    assert health["Content-Type"].startswith("application/json")
    assert json.loads(health.content) == {
        "status": "healthy",
        "service": "otlp-trace-receiver",
    }

    shared = api_client.get("/tracer/shared/fagi-api-journey-missing-token/")
    assert shared.status_code == status.HTTP_404_NOT_FOUND
    assert shared["Content-Type"].startswith("application/json")
    assert shared.data["code"] == "not_found"
    assert shared.data["detail"] == "Shared link not found"

    webhook = api_client.post(
        "/tracer/webhook/",
        data={
            "event": "call_analyzed",
            "interaction_type": "voice",
            "call": {"agent_id": "fagi-api-journey-missing-agent"},
        },
        format="json",
    )
    assert webhook.status_code == status.HTTP_400_BAD_REQUEST
    assert webhook["Content-Type"].startswith("application/json")
    assert webhook.data["code"] == "invalid"
    assert webhook.data["detail"] == "No matching agent definition found"


def test_shared_link_missing_token_db_error_stays_json(api_client, monkeypatch):
    from tracer.views import shared_link as shared_link_view

    def raise_database_error(*args, **kwargs):
        raise DatabaseError("database unavailable")

    monkeypatch.setattr(
        shared_link_view,
        "_get_shared_link_by_token",
        raise_database_error,
    )

    response = api_client.get("/tracer/shared/fagi-api-journey-missing-token/")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "service_unavailable"
    assert response.data["code"] == "service_unavailable"
    assert response.data["detail"] == "Shared link resolver is temporarily unavailable."


def test_tracer_webhook_agent_lookup_db_error_stays_json(api_client, monkeypatch):
    from tracer.views import observability_provider as provider_view

    def raise_database_error(*args, **kwargs):
        raise DatabaseError("database unavailable")

    monkeypatch.setattr(
        provider_view,
        "_matching_agent_definitions_for_webhook",
        raise_database_error,
    )

    response = api_client.post(
        "/tracer/webhook/",
        data={
            "event": "call_analyzed",
            "interaction_type": "voice",
            "call": {"agent_id": "fagi-api-journey-missing-agent"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "service_unavailable"
    assert response.data["code"] == "service_unavailable"
    assert response.data["detail"] == "Webhook agent lookup is temporarily unavailable."

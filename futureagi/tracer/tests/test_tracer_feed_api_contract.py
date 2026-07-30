import json
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from rest_framework.test import APIRequestFactory

from tracer.views.error_analysis import TraceErrorTaskUpdateRequestSerializer
from tracer.views.imagine_analysis import ImagineAnalysisQuerySerializer
from tracer.views.observability_provider import (
    WebhookHandlerView,
    WebhookRequestSerializer,
)


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_feed_endpoints_have_enveloped_response_contracts():
    expected = {
        ("GET", "/tracer/feed/issues/"): "FeedListApiResponse",
        ("GET", "/tracer/feed/issues/stats/"): "FeedStatsApiResponse",
        ("GET", "/tracer/feed/issues/{cluster_id}/"): "FeedDetailApiResponse",
        ("PATCH", "/tracer/feed/issues/{cluster_id}/"): "FeedDetailApiResponse",
        ("GET", "/tracer/feed/issues/{cluster_id}/overview/"): ("OverviewApiResponse"),
        ("GET", "/tracer/feed/issues/{cluster_id}/traces/"): ("TracesTabApiResponse"),
        ("GET", "/tracer/feed/issues/{cluster_id}/trends/"): ("TrendsTabApiResponse"),
        ("GET", "/tracer/feed/issues/{cluster_id}/sidebar/"): (
            "FeedSidebarApiResponse"
        ),
        ("GET", "/tracer/feed/issues/{cluster_id}/root-cause/"): (
            "DeepAnalysisApiResponse"
        ),
        ("POST", "/tracer/feed/issues/{cluster_id}/deep-analysis/"): (
            "DeepAnalysisDispatchApiResponse"
        ),
        ("GET", "/tracer/feed/integrations/linear/teams/"): ("LinearTeamsResponse"),
        ("POST", "/tracer/feed/issues/{cluster_id}/create-linear-issue/"): (
            "CreateLinearIssueResponse"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_feed_mutations_have_runtime_request_contracts():
    expected = {
        ("PATCH", "/tracer/feed/issues/{cluster_id}/"): "FeedUpdateBody",
        ("POST", "/tracer/feed/issues/{cluster_id}/deep-analysis/"): (
            "DeepAnalysisBody"
        ),
        ("POST", "/tracer/feed/issues/{cluster_id}/create-linear-issue/"): (
            "CreateLinearIssue"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_feed_contract_debt_stays_burned_down():
    report = _debt_report()
    covered_paths = {
        "/tracer/feed/issues/",
        "/tracer/feed/issues/stats/",
        "/tracer/feed/issues/{cluster_id}/",
        "/tracer/feed/issues/{cluster_id}/overview/",
        "/tracer/feed/issues/{cluster_id}/traces/",
        "/tracer/feed/issues/{cluster_id}/trends/",
        "/tracer/feed/issues/{cluster_id}/sidebar/",
        "/tracer/feed/issues/{cluster_id}/root-cause/",
        "/tracer/feed/issues/{cluster_id}/deep-analysis/",
        "/tracer/feed/integrations/linear/teams/",
        "/tracer/feed/issues/{cluster_id}/create-linear-issue/",
        "/tracer/imagine-analysis/",
        "/tracer/trace-error-task/{project_id}/",
        "/tracer/shared/{token}/",
        "/tracer/trace-error-analysis/{trace_id}/",
        "/tracer/v1/health",
        "/tracer/webhook/",
    }

    body_debt = {
        item["path"] for item in report["mutation_endpoints_without_body_schema"]
    }
    response_debt = {
        item["path"] for item in report["operations_without_response_schema"]
    }

    assert body_debt.isdisjoint(covered_paths)
    assert response_debt.isdisjoint(covered_paths)


def test_imagine_and_trace_error_task_have_runtime_contracts():
    expected_responses = {
        ("GET", "/tracer/imagine-analysis/"): "ImagineAnalysisResponse",
        ("POST", "/tracer/imagine-analysis/"): "ImagineAnalysisResponse",
        ("GET", "/tracer/trace-error-task/{project_id}/"): ("TraceErrorTaskResponse"),
        ("POST", "/tracer/trace-error-task/{project_id}/"): (
            "TraceErrorTaskUpdateResponse"
        ),
    }
    expected_bodies = {
        ("POST", "/tracer/imagine-analysis/"): "TriggerAnalysis",
        ("POST", "/tracer/trace-error-task/{project_id}/"): (
            "TraceErrorTaskUpdateRequest"
        ),
    }

    for (method, path), definition_name in expected_responses.items():
        assert _response_ref(_operation(path, method)) == definition_name

    for (method, path), definition_name in expected_bodies.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_protocol_and_public_tracer_endpoints_have_explicit_contracts():
    expected_responses = {
        ("GET", "/tracer/shared/{token}/"): "SharedLinkResolveResponse",
        ("GET", "/tracer/trace-error-analysis/{trace_id}/"): (
            "TraceErrorAnalysisResponse"
        ),
        ("GET", "/tracer/v1/health"): "OTLPHealthResponse",
        ("POST", "/tracer/webhook/"): "WebhookResponse",
    }
    expected_bodies = {
        ("POST", "/tracer/webhook/"): "WebhookRequest",
    }

    for (method, path), definition_name in expected_responses.items():
        assert _response_ref(_operation(path, method)) == definition_name

    for (method, path), definition_name in expected_bodies.items():
        operation = _operation(path, method)
        if definition_name == "object":
            body = next(
                parameter
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "body"
            )
            assert body["schema"]["type"] == definition_name
        else:
            assert _body_ref(operation) == definition_name


def test_runtime_contract_serializers_reject_drift_without_aliases():
    imagine = ImagineAnalysisQuerySerializer(
        data={"savedViewId": str(uuid.uuid4()), "traceId": "trace-1"}
    )
    assert not imagine.is_valid()
    assert "saved_view_id" in imagine.errors
    assert "trace_id" in imagine.errors

    trace_task = TraceErrorTaskUpdateRequestSerializer(data={"sampling_rate": 1.1})
    assert not trace_task.is_valid()
    assert "sampling_rate" in trace_task.errors

    webhook = WebhookRequestSerializer(
        data={
            "call": {
                "agent_id": "agent-1",
                "retell_extra_field": {"nested": True},
            },
        }
    )
    assert webhook.is_valid(), webhook.errors
    assert webhook.validated_data["call"]["retell_extra_field"]["nested"] is True


def test_webhook_signature_uses_original_retell_payload():
    payload = {
        "event": "call_analyzed",
        "interaction_type": "voice",
        "call": {
            "agent_id": "agent-1",
            "retell_extra_field": {"nested": True},
        },
    }
    agent_definition = Mock(id="agent-definition-1")
    agent_definition.latest_version.credentials.get_api_key.return_value = "retell-secret"
    queryset = Mock()
    queryset.iterator.return_value = [agent_definition]

    with (
        patch(
            "tracer.views.observability_provider.AgentDefinition.no_workspace_objects.select_related"
        ) as select_related,
        patch(
            "tracer.views.observability_provider.verify_retell_webhook"
        ) as verify_retell_webhook,
        patch(
            "tracer.views.observability_provider.normalize_and_store_logs"
        ) as normalize_and_store_logs,
    ):
        select_related.return_value.filter.return_value = queryset
        verify_retell_webhook.return_value = True

        request = APIRequestFactory().post(
            "/tracer/webhook/",
            payload,
            format="json",
            HTTP_X_RETELL_SIGNATURE="signature",
        )
        response = WebhookHandlerView.as_view()(request)

    assert response.status_code == 200
    signed_payload = verify_retell_webhook.call_args.args[0]
    assert json.loads(signed_payload) == payload
    assert verify_retell_webhook.call_args.kwargs["api_key"] == "retell-secret"
    assert verify_retell_webhook.call_args.kwargs["signature"] == "signature"
    normalize_and_store_logs.delay.assert_called_once()
    assert normalize_and_store_logs.delay.call_args.kwargs["body"] == payload


def test_webhook_invalid_signature_does_not_dispatch_logs():
    payload = {
        "event": "call_analyzed",
        "interaction_type": "voice",
        "call": {
            "agent_id": "agent-1",
        },
    }
    agent_definition = Mock(id="agent-definition-1")
    agent_definition.latest_version.credentials.get_api_key.return_value = "retell-secret"
    queryset = Mock()
    queryset.iterator.return_value = [agent_definition]

    with (
        patch(
            "tracer.views.observability_provider.AgentDefinition.no_workspace_objects.select_related"
        ) as select_related,
        patch(
            "tracer.views.observability_provider.verify_retell_webhook"
        ) as verify_retell_webhook,
        patch(
            "tracer.views.observability_provider.normalize_and_store_logs"
        ) as normalize_and_store_logs,
    ):
        select_related.return_value.filter.return_value = queryset
        verify_retell_webhook.return_value = False

        request = APIRequestFactory().post(
            "/tracer/webhook/",
            payload,
            format="json",
            HTTP_X_RETELL_SIGNATURE="bad-signature",
        )
        response = WebhookHandlerView.as_view()(request)

    assert response.status_code == 400
    assert response.data["result"] == "Invalid webhook signature"
    normalize_and_store_logs.delay.assert_not_called()

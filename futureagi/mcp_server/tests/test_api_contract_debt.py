import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import DatabaseError


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


def test_mcp_contract_debt_is_fully_burned_down():
    report = _debt_report()
    mcp_report = report["by_group"]["mcp"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["mcp"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["mcp"]
    ] == []
    assert mcp_report["operations_without_error_response_schema"] == 0
    assert mcp_report["broad_error_response_schemas"] == 0


def test_mcp_mutations_have_body_contracts():
    expected = {
        ("PUT", "/mcp/config/"): "MCPConnectionUpdate",
        ("PUT", "/mcp/config/tool-groups/"): "MCPToolGroupConfigUpdate",
        ("POST", "/mcp/internal/tool-call/"): "MCPToolCallRequest",
        ("POST", "/mcp/oauth/approve/"): "MCPOAuthApproveRequest",
        ("POST", "/mcp/oauth/consent/"): "MCPOAuthConsentRequest",
        ("POST", "/mcp/oauth/token/"): "MCPOAuthTokenRequest",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_mcp_endpoints_have_response_contracts():
    expected = {
        ("GET", "/mcp/health/"): "MCPHealthResponse",
        ("GET", "/mcp/config/"): "MCPConnectionResponse",
        ("PUT", "/mcp/config/"): "MCPConnectionResponse",
        ("GET", "/mcp/config/tool-groups/"): "MCPToolGroupsResponse",
        ("PUT", "/mcp/config/tool-groups/"): "MCPToolGroupsResponse",
        ("GET", "/mcp/analytics/summary/"): "MCPAnalyticsSummaryResponse",
        ("GET", "/mcp/analytics/timeline/"): "MCPAnalyticsTimelineResponse",
        ("GET", "/mcp/analytics/tools/"): "MCPAnalyticsToolsResponse",
        ("GET", "/mcp/internal/tools/"): "MCPToolListResponse",
        ("POST", "/mcp/internal/tool-call/"): "MCPToolCallResponse",
        ("GET", "/mcp/oauth/authorize/"): "MCPOAuthAuthorizeResponse",
        ("GET", "/mcp/oauth/approve-info/"): "MCPOAuthApproveInfoResponse",
        ("POST", "/mcp/oauth/approve/"): "MCPOAuthRedirectResponse",
        ("POST", "/mcp/oauth/consent/"): "MCPOAuthRedirectResponse",
        ("POST", "/mcp/oauth/token/"): "MCPOAuthTokenResponse",
        ("GET", "/mcp/sessions/"): "MCPSessionListResponse",
        ("DELETE", "/mcp/sessions/{session_id}/"): "MCPSessionRevokeResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_mcp_error_responses_have_contracts():
    expected = {
        ("GET", "/mcp/health/", "500"): "MCPErrorResponse",
        ("GET", "/mcp/config/tool-groups/", "403"): "MCPErrorResponse",
        ("DELETE", "/mcp/sessions/{session_id}/", "404"): "MCPErrorResponse",
    }

    for (method, path, status_code), definition_name in expected.items():
        assert _response_ref(_operation(path, method), status_code) == definition_name


def test_mcp_oauth_token_validation_uses_oauth_error_shape(api_client):
    response = api_client.post(
        "/mcp/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": "client",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "client_secret" in response.json()["error_description"]


def test_mcp_oauth_token_invalid_grant_type_uses_protocol_error(api_client):
    response = api_client.post(
        "/mcp/oauth/token/",
        {
            "grant_type": "password",
            "client_id": "client",
            "client_secret": "secret",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


@pytest.mark.django_db
def test_mcp_oauth_authorize_unknown_client_uses_json_error(api_client):
    response = api_client.get(
        "/mcp/oauth/authorize/",
        {
            "client_id": "missing-client",
            "redirect_uri": "https://example.com/callback",
            "response_type": "code",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"status": False, "error": "Unknown client_id"}


@pytest.mark.django_db
def test_mcp_oauth_token_unknown_client_uses_protocol_error(api_client):
    response = api_client.post(
        "/mcp/oauth/token/",
        {
            "grant_type": "authorization_code",
            "code": "missing-code",
            "client_id": "missing-client",
            "client_secret": "secret",
            "redirect_uri": "https://example.com/callback",
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}


def test_mcp_oauth_refresh_token_missing_token_uses_protocol_error(api_client):
    response = api_client.post(
        "/mcp/oauth/token/",
        {
            "grant_type": "refresh_token",
            "client_id": "client",
            "client_secret": "secret",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}


@pytest.mark.django_db
def test_mcp_oauth_refresh_token_unknown_client_uses_protocol_error(api_client):
    response = api_client.post(
        "/mcp/oauth/token/",
        {
            "grant_type": "refresh_token",
            "refresh_token": "missing-refresh-token",
            "client_id": "missing-client",
            "client_secret": "secret",
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}


def test_mcp_oauth_consent_requires_authentication(api_client):
    response = api_client.post(
        "/mcp/oauth/consent/",
        {
            "client_id": "missing-client",
            "redirect_uri": "https://example.com/callback",
            "approved": False,
        },
        format="json",
    )

    assert response.status_code in (401, 403)
    assert response.json()["status"] is False


def test_mcp_oauth_authorize_registry_failure_uses_json_503(api_client):
    with patch(
        "mcp_server.views.oauth.MCPOAuthClient.objects.get",
        side_effect=DatabaseError("missing oauth registry"),
    ):
        response = api_client.get(
            "/mcp/oauth/authorize/",
            {
                "client_id": "missing-client",
                "redirect_uri": "https://example.com/callback",
                "response_type": "code",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "status": False,
        "error": "OAuth client registry unavailable",
    }


def test_mcp_oauth_token_registry_failure_uses_protocol_503(api_client):
    with patch(
        "mcp_server.views.oauth.MCPOAuthClient.objects.get",
        side_effect=DatabaseError("missing oauth registry"),
    ):
        response = api_client.post(
            "/mcp/oauth/token/",
            {
                "grant_type": "authorization_code",
                "code": "missing-code",
                "client_id": "missing-client",
                "client_secret": "secret",
            },
            format="json",
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": "server_error",
        "error_description": "OAuth client registry unavailable",
    }

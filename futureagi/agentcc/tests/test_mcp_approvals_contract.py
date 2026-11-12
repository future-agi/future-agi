"""Approval contracts must describe the responses the console actually receives."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import include, path
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.app_settings import swagger_settings
from jsonschema import Draft4Validator, FormatChecker

from accounts.models.organization_membership import OrganizationMembership
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.test_mcp_approvals_bridge import record

ROOT = "/agentcc/mcp/approvals/"
OPERATIONS = [
    (ROOT, "get", "agentcc_mcp_approvals_list"),
    (ROOT + "{approval_id}/", "get", "agentcc_mcp_approvals_read"),
    (
        ROOT + "{approval_id}/decision/",
        "post",
        "agentcc_mcp_approvals_decision_create",
    ),
]


@pytest.fixture(scope="module", params=["generated", "published"])
def contract(request):
    if request.param == "published":
        root = Path(__file__).resolve().parents[3]
        return json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    generator = swagger_settings.DEFAULT_GENERATOR_CLASS(
        info=openapi.Info(title="MCP approval contract", default_version="v1"),
        patterns=[path("agentcc/", include("agentcc.urls_mcp_approvals"))],
    )
    schema = generator.get_schema(request=None, public=True).as_odict()
    prefix = schema.get("basePath", "").rstrip("/")
    schema["paths"] = {prefix + key: value for key, value in schema["paths"].items()}
    return schema


def _json_schema(value):
    """Adapt Swagger nullability and reject undocumented response properties.

    This is only a validation copy: explicit additionalProperties maps stay maps,
    and no expected response fields are invented outside the emitted contract.
    """
    if isinstance(value, list):
        return [_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _json_schema(item) for key, item in value.items()}
    if result.get("type") == "object" and "properties" in result:
        result.setdefault("additionalProperties", False)
    if result.pop("x-nullable", False):
        return {"anyOf": [result, {"type": "null"}]}
    return result


def validate(contract, schema, body):
    document = _json_schema(schema)
    document["definitions"] = _json_schema(contract.get("definitions", {}))
    Draft4Validator(document, format_checker=FormatChecker()).validate(body)


def validate_response(contract, route, method, response, status):
    assert response.status_code == status, response.data
    operation = contract["paths"][route][method]
    # A generic default error must not conceal message-only manual responses.
    assert str(status) in operation["responses"], (route, method, status)
    validate(contract, operation["responses"][str(status)]["schema"], response.json())


@pytest.fixture
def admin_client(auth_client, user, organization):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=8)
    return auth_client


@pytest.mark.parametrize("route,method,operation_id", OPERATIONS)
def test_approval_operations_are_published_with_stable_ids(
    contract, route, method, operation_id
):
    assert route in contract["paths"], f"Missing approval route: {route}"
    assert contract["paths"][route][method]["operationId"] == operation_id


@pytest.mark.django_db
@pytest.mark.parametrize("detail", [False, True], ids=["list", "detail"])
def test_authorized_get_body_matches_contract(contract, admin_client, organization, detail):
    row = record(organization)
    suffix = row["approval_id"] + "/" if detail else ""
    payload = row if detail else {"approvals": [row], "next_cursor": ""}
    with patch.object(GatewayClient, "_request", return_value=payload) as transport:
        response = admin_client.get(ROOT + suffix)
    assert response.status_code == 200, response.data
    transport.assert_called_once()
    assert response["Cache-Control"] == "no-store"
    route = ROOT + "{approval_id}/" if detail else ROOT
    validate_response(contract, route, "get", response, 200)
    result = response.json()["result"]
    assert (result if detail else result[0])["approval_id"] == row["approval_id"]


@pytest.mark.django_db
@pytest.mark.parametrize("decision,status", [("approve", "approved"), ("deny", "denied")])
def test_authorized_decision_request_and_body_match_contract(
    contract, admin_client, organization, user, decision, status
):
    row = record(
        organization,
        status=status,
        decided_by=str(user.pk),
        decided_at=timezone.now().isoformat(),
        note="Reviewed in the console",
    )
    body = {"decision": decision, "note": row["note"]}
    route = ROOT + "{approval_id}/decision/"
    with patch.object(GatewayClient, "_request", return_value=row) as transport:
        response = admin_client.post(
            ROOT + row["approval_id"] + "/decision/", body, format="json"
        )
    assert response.status_code == 200, response.data
    transport.assert_called_once_with(
        "POST",
        f"/-/mcp/approvals/{row['approval_id']}/decision",
        json_body={
            "org_id": str(organization.id),
            "decision": decision,
            "actor": str(user.pk),
            "note": row["note"],
        },
    )
    operation = contract["paths"][route]["post"]
    (parameter,) = [item for item in operation["parameters"] if item["in"] == "body"]
    validate(contract, parameter["schema"], body)
    validate_response(contract, route, "post", response, 200)
    assert response.json()["result"]["status"] == status


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"decision": "maybe"},
        {},
        {"decision": "approve", "actor": "another-person"},
        {"decision": "approve", "note": "x" * 1025},
    ],
    ids=["invalid-decision", "missing-decision", "unknown-field", "long-note"],
)
def test_invalid_decision_body_matches_error_contract(contract, admin_client, body):
    with patch.object(GatewayClient, "_request") as transport:
        response = admin_client.post(ROOT + "missing/decision/", body, format="json")
    assert response.status_code == 400, response.data
    transport.assert_not_called()
    validate_response(contract, ROOT + "{approval_id}/decision/", "post", response, 400)


@pytest.mark.django_db
@pytest.mark.parametrize("status", [404, 409, 503], ids=["unknown", "already-decided", "unavailable"])
@pytest.mark.parametrize("action", ["list", "detail", "decision"])
def test_gateway_error_bodies_match_contract(contract, admin_client, status, action):
    route, method, _ = OPERATIONS[["list", "detail", "decision"].index(action)]
    target = route.replace("{approval_id}", "unknown-approval")
    with patch.object(
        GatewayClient,
        "_request",
        side_effect=GatewayClientError("synthetic gateway failure", status_code=status),
    ) as transport:
        response = (
            admin_client.post(target, {"decision": "approve"}, format="json")
            if method == "post"
            else admin_client.get(target)
        )
    assert response.status_code == status, response.data
    transport.assert_called_once()
    assert set(response.json()) == {"message"}
    validate_response(contract, route, method, response, status)


@pytest.mark.django_db
def test_invalid_list_filter_matches_error_contract(contract, admin_client):
    with patch.object(GatewayClient, "_request") as transport:
        response = admin_client.get(ROOT, {"status": "not-a-status"})
    assert response.status_code == 400, response.data
    transport.assert_not_called()
    assert response.json() == {"message": "Invalid approval status."}
    validate_response(contract, ROOT, "get", response, 400)


@pytest.mark.django_db
@pytest.mark.parametrize("decision", [False, True], ids=["detail", "decision"])
def test_overlong_identifier_matches_not_found_contract(contract, admin_client, decision):
    route = ROOT + "{approval_id}/" + ("decision/" if decision else "")
    target = route.replace("{approval_id}", "a" * 4097)
    with patch.object(GatewayClient, "_request") as transport:
        response = (
            admin_client.post(target, {"decision": "approve"}, format="json")
            if decision
            else admin_client.get(target)
        )
    assert response.status_code == 404, response.data
    transport.assert_not_called()
    validate_response(contract, route, "post" if decision else "get", response, 404)

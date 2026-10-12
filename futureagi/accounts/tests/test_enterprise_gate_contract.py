"""The Community edition 402 is a declared, typed API contract (TH-8084 R5, AC-08).

Every mounted endpoint that can refuse an organization, workspace or member
with ``ENTERPRISE_FEATURE_REQUIRED`` declares a 402 response whose ``error`` is
string-or-object and whose ``enterprise_gate`` block is typed. Ordinary
accounts errors keep their existing ``AccountsErrorResponse`` schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from accounts.models.organization_invite import OrganizationInvite
from accounts.serializers.contracts import AccountsErrorResponseSerializer
from tfc.capabilities import edition
from tfc.capabilities.tests.edition_factories import make_member
from tfc.constants.levels import Level
from tfc.utils.api_contracts import _unknown_fields, _validate_serializer

GATED_OPERATIONS = [
    ("/accounts/signup/", "post"),
    ("/accounts/activate/{uidb64}/{token}/", "get"),
    ("/accounts/organizations/create/", "post"),
    ("/accounts/organizations/new/", "post"),
    ("/accounts/appsmith/users/", "post"),
    ("/accounts/workspaces/", "post"),
    ("/accounts/workspaces/{workspace_id}/members/", "post"),
    ("/accounts/workspace/invite/", "post"),
    ("/accounts/team/users/", "post"),
    ("/accounts/organization/invite/", "post"),
    ("/accounts/organization/invite/resend/", "post"),
    ("/accounts/organization/members/reactivate/", "post"),
]

INVITE_URL = "/accounts/organization/invite/"

# The 402 a fourth member gets. The frontend strict-validation test
# (frontend/src/utils/__tests__/axios.enterprise-gate.test.js) uses this body.
MESSAGE = (
    "Community includes up to 3 organization members. More members are an "
    "Enterprise feature. Contact sales@futureagi.com or activate a license in "
    "Settings > Plan & License."
)
FOURTH_MEMBER_402 = {
    "status": False,
    "type": "entitlement_error",
    "code": "ENTERPRISE_FEATURE_REQUIRED",
    "detail": MESSAGE,
    "message": MESSAGE,
    "error": {
        "code": "ENTERPRISE_FEATURE_REQUIRED",
        "message": MESSAGE,
        "detail": {"feature": "members"},
    },
    "result": MESSAGE,
    "details": {"feature": ["members"]},
    "attr": "feature",
    "upgrade_required": True,
    "enterprise_gate": {
        "feature": "members",
        "edition": "community",
        "limit": 3,
        "current": 3,
        "requested": 1,
        "license_state": "missing",
        "contact": "sales@futureagi.com",
        "activation_route": "/dashboard/settings/ee-licenses",
    },
}


def _swagger():
    root = Path(__file__).resolve().parents[3]
    with (root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _ref(response):
    return response["schema"]["$ref"].rsplit("/", 1)[-1]


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)
    monkeypatch.setattr(edition, "license_state", lambda: "missing")


def _jwt_client(email, password="testpassword123"):
    client = APIClient()
    login = client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    return client


def _invite(client, workspace, emails):
    return client.post(
        INVITE_URL,
        {
            "emails": emails,
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_VIEWER}
            ],
        },
        format="json",
    )


@pytest.mark.parametrize(("path", "method"), GATED_OPERATIONS)
def test_gate_endpoints_declare_the_typed_402(path, method):
    """AC-08: the 402 is declared; the ordinary 400 schema is unchanged."""
    responses = _swagger()["paths"][path][method]["responses"]
    assert _ref(responses["402"]) == "EnterpriseGateErrorResponse"
    if "400" in responses:
        assert _ref(responses["400"]) == "AccountsErrorResponse"


def test_enterprise_gate_definitions_are_typed():
    """AC-08: the gate block and the structured error are typed, and ``error``
    stays compatible with the string the other management errors return."""
    definitions = _swagger()["definitions"]
    error_response = definitions["EnterpriseGateErrorResponse"]["properties"]
    assert error_response["enterprise_gate"]["$ref"] == "#/definitions/EnterpriseGate"
    assert error_response["upgrade_required"]["type"] == "boolean"
    error = error_response["error"]
    assert error["x-string-or-object"] is True
    assert set(error["properties"]) == {"code", "message", "detail"}

    gate = definitions["EnterpriseGate"]
    assert set(gate["required"]) == set(FOURTH_MEMBER_402["enterprise_gate"])
    props = gate["properties"]
    assert props["edition"]["enum"] == ["community", "enterprise"]
    for count in ("limit", "current", "requested"):
        assert props[count]["type"] == "integer"
        assert props[count]["x-nullable"] is True
    assert "x-json-value" not in json.dumps(gate)


@pytest.mark.django_db
@pytest.mark.edition_rule
def test_real_fourth_member_402_matches_the_declared_contract(
    community, user, workspace
):
    """AC-08: a real fourth-member refusal through the mounted invite endpoint
    validates against the declared 402 serializer, with no undeclared field."""
    from tfc.capabilities.contracts import EnterpriseGateErrorResponseSerializer

    organization = user.organization
    make_member(organization)
    make_member(organization)
    client = _jwt_client(user.email)

    response = _invite(client, workspace, ["fourth@example.com"])

    assert response.status_code == 402, response.content
    body = response.json()
    assert body == FOURTH_MEMBER_402
    _serializer, errors, valid = _validate_serializer(
        EnterpriseGateErrorResponseSerializer, body, reject_unknown_fields=True
    )
    assert valid, errors
    assert not OrganizationInvite.objects.filter(
        target_email="fourth@example.com"
    ).exists()


@pytest.mark.django_db
@pytest.mark.edition_rule
def test_structured_gate_error_requires_code_message_and_feature_detail():
    """The structured 402 branch is typed, while the legacy branch stays a string."""
    from tfc.capabilities.contracts import EnterpriseGateErrorResponseSerializer

    body = dict(FOURTH_MEMBER_402)
    body["error"] = {"code": FOURTH_MEMBER_402["error"]["code"]}
    serializer = EnterpriseGateErrorResponseSerializer(data=body)

    assert not serializer.is_valid()
    assert set(serializer.errors["error"]) == {"message", "detail"}


def test_structured_gate_error_rejects_non_string_feature_detail():
    """A known structured detail field cannot silently become arbitrary JSON."""
    from tfc.capabilities.contracts import EnterpriseGateErrorResponseSerializer

    body = dict(FOURTH_MEMBER_402)
    body["error"] = {
        **FOURTH_MEMBER_402["error"],
        "detail": {"feature": {"unexpected": "object"}},
    }
    serializer = EnterpriseGateErrorResponseSerializer(data=body)

    assert not serializer.is_valid()
    assert "feature" in serializer.errors["error"]["detail"]


def test_ordinary_invite_error_keeps_the_accounts_error_contract(community, user):
    """AC-08: a plain 400 on the same endpoint is still a string error."""
    client = _jwt_client(user.email)
    response = client.post(INVITE_URL, {"emails": []}, format="json")

    assert response.status_code == 400, response.content
    body = response.json()
    assert isinstance(body["error"], str)
    serializer = AccountsErrorResponseSerializer(data=body)
    assert serializer.is_valid(), serializer.errors
    assert not _unknown_fields(body, serializer)

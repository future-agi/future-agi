"""Disabled Enterprise provisioning never admits or issues credentials."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization
from agentcc.models.mcp_service_provisioning import AgentccEEServiceCredential
from agentcc.services import mcp_service_provisioning as provisioning

pytestmark = pytest.mark.django_db

ROUTES = (
    ("post", "virtual-servers/ensure/"),
    ("delete", "virtual-servers/vs_00000000000000000000000000/"),
    ("post", "agent-keys/"),
    ("post", "agent-keys/key_000000000000000000000000/revoke/"),
    ("get", "credential/"),
    ("post", "credential/"),
    ("post", "credential/rotate/"),
    ("post", "credential/revoke/"),
    ("get", "workspaces/00000000-0000-0000-0000-000000000001/agent-key-policy/"),
    ("put", "workspaces/00000000-0000-0000-0000-000000000001/agent-key-policy/"),
    ("get", "workspaces/00000000-0000-0000-0000-000000000001/agent-keys/"),
    ("post", "agent-key-refusals/"),
)


def historical_credential():
    org = Organization.objects.create(name="disabled-provisioning")
    secret = uuid.uuid4().hex
    credential = AgentccEEServiceCredential.objects.create(
        organization=org,
        api_key="ee-service-" + uuid.uuid4().hex,
        secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
    )
    return credential, secret


@pytest.mark.parametrize("method,route", ROUTES)
def test_disabled_routes_have_one_non_enumerating_refusal(settings, method, route):
    settings.EE_SERVICE_PROVISIONING_ENABLED = False
    credential, secret = historical_credential()
    before = AgentccEEServiceCredential.objects.count()
    results = []
    for headers in (
        {},
        {"HTTP_X_API_KEY": credential.api_key, "HTTP_X_SECRET_KEY": secret},
    ):
        client = APIClient()
        response = getattr(client, method)(
            "/agentcc/mcp/service/" + route, {}, format="json", **headers
        )
        assert response.status_code == 503
        assert response.json()["code"] == "feature_unavailable"
        fixture = (
            Path(__file__).resolve().parents[3]
            / "agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/provision/error-unavailable.response.json"
        )
        assert response.json() == json.loads(fixture.read_text())["body"]
        assert (
            response.json()["result"]
            == "Enterprise credential provisioning is unavailable."
        )
        assert not {"api_key", "secret_key", "key"}.intersection(response.json())
        assert str(credential.organization_id) not in str(response.json())
        assert credential.api_key not in str(response.json())
        assert secret not in str(response.json())
        results.append(response.json())
    assert results[0] == results[1]
    assert AgentccEEServiceCredential.objects.count() == before
    credential.refresh_from_db()
    assert credential.last_used_at is None


def test_disabled_service_credentials_never_authenticate(settings):
    settings.EE_SERVICE_PROVISIONING_ENABLED = False
    credential, secret = historical_credential()
    assert provisioning.authenticate(credential.api_key, secret) is None
    credential.refresh_from_db()
    assert credential.last_used_at is None

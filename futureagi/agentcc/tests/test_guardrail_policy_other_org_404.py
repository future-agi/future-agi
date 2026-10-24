"""A write to another organization's guardrail policy answers 404, as a read
of it does.

Another organization's row is unchanged, its blob is never decrypted, and
nothing is pushed for either org.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models import Organization
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from integrations.services.credentials import CredentialManager

POLICIES = "/agentcc/guardrail-policies/"
# A test value, not a real credential.
ORG_B_SECRET = "org-b-guard-key-test-value"
ORG_B_CHECKS = [
    {
        "name": "lakera-guard",
        "enabled": True,
        "action": "block",
        "config": {"provider": "lakera", "api_key": "__encrypted__"},
    }
]


@pytest.fixture
def gateway():
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        yield client


@pytest.fixture
def decrypt_spy():
    real_decrypt = CredentialManager.decrypt
    with patch.object(CredentialManager, "decrypt", side_effect=real_decrypt) as spy:
        yield spy


@pytest.fixture
def org_b_policy(db):
    org_b = Organization.objects.create(name="Other Organization")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org_b, version=1, is_active=True
    )
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org_b,
        name="org-b-guard",
        checks=ORG_B_CHECKS,
        encrypted_check_configs=CredentialManager.encrypt(
            {"lakera-guard": {"api_key": ORG_B_SECRET}}
        ),
    )


# Every write to one policy by id, as org A sends it.
WRITES = {
    "patch": ("patch", "", {"is_active": False}),
    "put": ("put", "", {"name": "taken-over", "checks": []}),
    "delete": ("delete", "", None),
    "apply": ("post", "apply/", {"key_ids": []}),
}


def _send(client, how, policy):
    method, suffix, body = WRITES[how]
    url = f"{POLICIES}{policy.id}/{suffix}"
    if body is None:
        return getattr(client, method)(url)
    return getattr(client, method)(url, body, format="json")


@pytest.mark.integration
@pytest.mark.api
class TestAnotherOrgsPolicy:
    @pytest.mark.parametrize("how", sorted(WRITES))
    def test_a_write_answers_404_and_changes_nothing(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy, how
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        before = AgentccGuardrailPolicy.no_workspace_objects.values().get(
            id=org_b_policy.id
        )

        response = _send(auth_client, how, org_b_policy)

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.json()
        assert response.json()["result"] == "Guardrail policy not found"
        assert "AgentccGuardrailPolicy" not in str(response.json())
        after = AgentccGuardrailPolicy.no_workspace_objects.values().get(
            id=org_b_policy.id
        )
        assert after == before
        assert decrypt_spy.call_count == 0
        gateway.set_org_config.assert_not_called()

    def test_a_read_answers_404(self, auth_client, organization, org_b_policy, gateway):
        response = auth_client.get(f"{POLICIES}{org_b_policy.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.json()

    @pytest.mark.parametrize("how", sorted(WRITES))
    def test_an_unknown_id_answers_404(self, auth_client, organization, gateway, how):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        class _Unknown:
            id = "00000000-0000-4000-8000-000000000000"

        response = _send(auth_client, how, _Unknown)
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.json()
        gateway.set_org_config.assert_not_called()

    def test_the_own_orgs_policy_is_written(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        own = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="own-guard", checks=[]
        )
        response = auth_client.patch(
            f"{POLICIES}{own.id}/", {"description": "mine"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        own.refresh_from_db()
        assert own.description == "mine"

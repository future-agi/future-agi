"""R7 actual publication canaries; transport only is replaced, never the builder."""

import os
import secrets
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest
from accounts.models import Organization
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import config_push
from agentcc.tests.test_provider_public_projection_r7 import (
    AUTH_URL,
    KEY,
    SAFE,
    retained,
)
from integrations.services.credentials import CredentialManager
from rest_framework.test import APIClient

pytestmark = [pytest.mark.integration, pytest.mark.api]


def config(org):
    row = AgentccOrgConfig(organization=org, version=1, is_active=True)
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def bulk():
    # Runtime-only synthetic bearer, never a checked-in credential-shaped literal.
    token = secrets.token_hex(32)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", token):
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == 200
    assert response.json()["unbuilt_org_ids"] == []
    return response.json()["result"]


def captured_publications(org, cfg):
    transport = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=transport):
        assert config_push.push_org_config(str(org.pk), cfg) is True
        transport.set_org_config.assert_called_once()
        org_id, payload = transport.set_org_config.call_args.args
        assert org_id == str(org.pk)
        yield payload
        transport.reset_mock()
        config_push.push_all_org_configs()
        calls = transport.set_org_config.call_args_list
        selected = [call.args[1] for call in calls if call.args[0] == str(org.pk)]
        assert len(selected) == 1
        yield selected[0]
    yield bulk()[str(org.pk)]


def test_actual_push_and_bulk_exclude_url_auth_models(organization):
    retained(organization, base_url=AUTH_URL)
    cfg = config(organization)
    for payload in captured_publications(organization, cfg):
        provider = payload["providers"]["openai"]
        assert provider["models"] == SAFE
        # Push is private and must retain the provider's encrypted API key.
        assert provider["api_key"] == KEY


def test_two_tenant_publication_does_not_cross_org_predicate(organization):
    peer = Organization.objects.create(name="R7 publication peer")
    ours = retained(organization)
    ours.models_list = ["gpt-4o"]
    ours.save(update_fields=["models_list"])
    peer_key = "spruce-estuary-6924-lark"
    for name, models in (("openai", ["gpt-4o-mini"]), ("cohere", ["command-r"])):
        row = retained(peer, name=name)
        row.encrypted_credentials = CredentialManager.encrypt({"api_key": peer_key})
        row.models_list = models
        row.save(update_fields=["encrypted_credentials", "models_list"])
    ours_config = config(organization)
    config(peer)

    # Run this leaf a second time with PRR009_R7_REMOVE_PROVIDER_ORG=1 to apply
    # this one-predicate mutation.
    # No builder substitution and no assertion reversal: the mutant must fail.
    manager = AgentccProviderCredential.no_workspace_objects
    original_filter = manager.filter

    def omit_org(*args, **kwargs):
        kwargs.pop("organization_id", None)
        return original_filter(*args, **kwargs)

    mutation = (
        patch.object(manager, "filter", side_effect=omit_org)
        if os.environ.get("PRR009_R7_REMOVE_PROVIDER_ORG") == "1"
        else nullcontext()
    )
    with mutation:
        for payload in captured_publications(organization, ours_config):
            assert set(payload["providers"]) == {"openai"}
            provider = payload["providers"]["openai"]
            assert provider["api_key"] == KEY
            assert provider["models"] == ["gpt-4o"]
            assert peer_key not in repr(payload)
        peer_payload = bulk()[str(peer.pk)]["providers"]
        assert set(peer_payload) == {"openai", "cohere"}
        assert peer_payload["openai"]["api_key"] == peer_key
        assert peer_payload["openai"]["models"] == ["gpt-4o-mini"]
        assert peer_payload["cohere"]["models"] == ["command-r"]

"""Rotation validates JSON shape without hiding unexpected server faults."""

import logging

import pytest
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from integrations.services.credentials import CredentialManager
from tfc.tests.sentry_capture import sentry_capture

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def owned_credential(auth_client, monkeypatch):
    monkeypatch.setattr(
        AgentccProviderCredentialViewSet, "_push_config_to_gateway", lambda *a: True
    )
    response = auth_client.post(
        "/agentcc/provider-credentials/",
        {
            "provider_name": "synthetic-rotation",
            "credentials": {"api_key": "synthetic-old-value"},
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return AgentccProviderCredential.no_workspace_objects.get(
        provider_name="synthetic-rotation", deleted=False
    )


@pytest.mark.parametrize("raw_body", ["[]", "null", '"synthetic"', "42", "true"])
def test_rotate_rejects_non_object_json_without_server_error(
    raw_body, auth_client, owned_credential, caplog
):
    credential = owned_credential
    original_encrypted = bytes(credential.encrypted_credentials)
    original_rotation = credential.last_rotated_at
    auth_client.raise_request_exception = False
    caplog.clear()
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
        DjangoIntegration(),
    ) as transport:
        response = auth_client.post(
            f"/agentcc/provider-credentials/{credential.pk}/rotate/",
            data=raw_body,
            content_type="application/json",
        )
    credential.refresh_from_db()
    assert bytes(credential.encrypted_credentials) == original_encrypted
    assert credential.last_rotated_at == original_rotation
    assert response.status_code == 400, response.content
    assert isinstance(response.json(), dict)
    assert not [e for e in transport.events if e.get("level") in ("error", "fatal")]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_valid_rotation_still_updates_owned_credential(auth_client, owned_credential):
    response = auth_client.post(
        f"/agentcc/provider-credentials/{owned_credential.pk}/rotate/",
        {"credentials": {"api_key": "synthetic-new"}},
        format="json",
    )
    assert response.status_code == 200, response.content
    owned_credential.refresh_from_db()
    assert CredentialManager.decrypt(owned_credential.encrypted_credentials) == {
        "api_key": "synthetic-new"
    }
    assert owned_credential.last_rotated_at is not None

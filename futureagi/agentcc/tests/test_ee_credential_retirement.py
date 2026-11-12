"""Purpose bindings survive missed publication, replay records and retries."""

import hashlib
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for
from agentcc.models import AgentccAPIKey
from agentcc.models.ee_agent_key_mint import AgentccEEAgentKeyMintRecord
from agentcc.models.mcp_service_provisioning import (
    AgentccEEAgentKey,
    AgentccEEServiceCredential,
)
from agentcc.services import (
    auth_bridge,
    ee_credential_retirement,
    mcp_service_provisioning,
)
from agentcc.services.gateway_client import GatewayClientError

pytestmark = pytest.mark.django_db


@pytest.fixture
def history(settings):
    settings.EE_SERVICE_PROVISIONING_ENABLED = False
    org = Organization.objects.create(name="purpose-retirement")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    workspace = Workspace.no_workspace_objects.create(
        name="historical", organization=org, created_by=user
    )
    personal = personal_key_for(user, org)
    ordinary = AgentccAPIKey.no_workspace_objects.create(
        organization=org,
        user=user,
        name="explicit-personal",
        gateway_key_id="key_" + uuid.uuid4().hex[:24],
        key_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
    )
    credentials = []
    for state in ("active", "rotated"):
        secret = uuid.uuid4().hex
        credential = AgentccEEServiceCredential.objects.create(
            organization=org,
            api_key="ee-service-" + uuid.uuid4().hex,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
            status=state,
        )
        credentials.append((credential, secret))
    bindings = []
    for index, (owner, issuer) in enumerate(
        (
            (None, None),
            (user, None),
            (None, credentials[0][0]),
            (user, credentials[1][0]),
        )
    ):
        raw = "sk-agentcc-" + uuid.uuid4().hex
        key = AgentccAPIKey.no_workspace_objects.create(
            organization=org,
            workspace=workspace,
            user=owner,
            name=f"historical-{index}",
            gateway_key_id="key_" + uuid.uuid4().hex[:24],
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        )
        binding = AgentccEEAgentKey.objects.create(
            organization=org,
            workspace=workspace,
            api_key=key,
            minted_by=issuer,
            mint_idempotency_key=uuid.uuid4(),
        )
        bindings.append((binding, raw))
    record = AgentccEEAgentKeyMintRecord.objects.create(
        organization=org,
        workspace=workspace,
        service_credential=credentials[0][0],
        key_sha256=hashlib.sha256(b"synthetic-replay").hexdigest(),
        request_sha256=hashlib.sha256(b"synthetic-request").hexdigest(),
        agent_key=bindings[0][0],
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return org, personal, ordinary, credentials, bindings, record


@pytest.mark.parametrize("enabled", [False, True])
def test_live_bound_keys_are_loadable_only_when_enabled(history, settings, enabled):
    org, _, ordinary, _, bindings, _ = history
    settings.EE_SERVICE_PROVISIONING_ENABLED = enabled
    bound_ids = {binding.api_key_id for binding, _ in bindings}
    assert set(
        AgentccAPIKey._base_manager.filter(
            pk__in=bound_ids, status=AgentccAPIKey.ACTIVE, deleted=False
        ).values_list("pk", flat=True)
    ) == bound_ids
    for scope in (org, None):
        loadable = set(
            auth_bridge.gateway_loadable_keys(scope).values_list("pk", flat=True)
        )
        assert ordinary.pk in loadable
        assert loadable & bound_ids == (bound_ids if enabled else set())
    assert not AgentccAPIKey._base_manager.filter(
        pk__in=bound_ids, status=AgentccAPIKey.REVOKED
    ).exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_bulk_reload_omits_live_bound_keys_while_off(
    history, settings, monkeypatch, enabled
):
    import agentcc.permissions as permissions

    _, _, ordinary, _, bindings, _ = history
    settings.EE_SERVICE_PROVISIONING_ENABLED = enabled
    token = uuid.uuid4().hex
    monkeypatch.setattr(permissions, "AGENTCC_ADMIN_TOKEN", token)
    response = APIClient().get(
        "/agentcc/api-keys/bulk/", HTTP_AUTHORIZATION="Bearer " + token
    )
    assert response.status_code == 200
    sent = {row["id"] for row in response.json()["result"]}
    bound_ids = {binding.api_key.gateway_key_id for binding, _ in bindings}
    assert ordinary.gateway_key_id in sent
    assert sent & bound_ids == (bound_ids if enabled else set())
    for binding, raw in bindings:
        binding.api_key.refresh_from_db()
        assert binding.api_key.status == AgentccAPIKey.ACTIVE
        assert not binding.api_key.deleted
        assert raw not in response.content.decode()
        if not enabled:
            assert binding.api_key.key_hash not in response.content.decode()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("header", ["HTTP_AUTHORIZATION", "HTTP_X_API_KEY"])
def test_live_bound_key_off_refusal_is_generic(history, settings, enabled, header):
    org, personal, _, _, bindings, _ = history
    settings.EE_SERVICE_PROVISIONING_ENABLED = enabled
    for binding, raw in bindings:
        binding.api_key.refresh_from_db()
        assert binding.api_key.status == AgentccAPIKey.ACTIVE
        assert not binding.api_key.deleted
        presented = "Bearer " + raw if header == "HTTP_AUTHORIZATION" else raw
        response = APIClient().get("/api/public/health", **{header: presented})
        assert response.status_code == (403 if enabled else 401)
        body = response.json()
        assert body["code"] == (
            "key_scope_denied" if enabled else "authentication_failed"
        )
        assert raw not in str(body) and str(org.pk) not in str(body)
        if not enabled:
            assert response["WWW-Authenticate"] == "ApiKey"
    unknown = "sk-agentcc-" + uuid.uuid4().hex
    response = APIClient().get(
        "/api/public/health", HTTP_AUTHORIZATION="Bearer " + unknown
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"
    personal_response = APIClient().get(
        "/api/public/health",
        HTTP_X_API_KEY=personal.api_key,
        HTTP_X_SECRET_KEY=personal.secret_key,
    )
    assert personal_response.status_code == 200


def test_retirement_uses_bindings_and_retries_readback(history, monkeypatch):
    org, personal, ordinary, credentials, bindings, record = history
    gateway = Mock()
    gateway.revoke_key.side_effect = GatewayClientError("unavailable", status_code=503)
    factory = Mock(return_value=gateway)
    monkeypatch.setattr(ee_credential_retirement, "get_gateway_client", factory)
    first = ee_credential_retirement.retire_ee_credentials()
    assert first == {
        "service_credentials_retired": 2,
        "agent_keys_retired": 4,
        "gateway_confirmed": 0,
        "gateway_pending": 4,
    }
    factory.assert_called_once_with()
    assert gateway.revoke_key.call_count == 4
    gateway.get_key.assert_not_called()
    for credential, secret in credentials:
        credential.refresh_from_db()
        assert credential.status == "revoked" and credential.revoked_at is not None
        assert mcp_service_provisioning.authenticate(credential.api_key, secret) is None
    for binding, raw in bindings:
        binding.api_key.refresh_from_db()
        assert binding.api_key.status == "revoked"
        response = APIClient().get(
            "/api/public/health", HTTP_AUTHORIZATION="Bearer " + raw
        )
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_failed"
    record.refresh_from_db()
    assert record.replays == 0 and record.agent_key_id == bindings[0][0].pk
    ordinary.refresh_from_db()
    assert ordinary.status == "active"
    assert list(
        auth_bridge.gateway_loadable_keys(org).values_list("pk", flat=True)
    ) == [ordinary.pk]
    response = APIClient().get(
        "/api/public/health",
        HTTP_X_API_KEY=personal.api_key,
        HTTP_X_SECRET_KEY=personal.secret_key,
    )
    assert response.status_code == 200

    gateway.reset_mock()
    gateway.revoke_key.side_effect = None
    gateway.get_key.side_effect = lambda key_id: {
        "id": key_id,
        "status": "revoked",
        "metadata": {"org_id": str(org.pk)},
    }
    second = ee_credential_retirement.retire_ee_credentials()
    assert second == {
        "service_credentials_retired": 0,
        "agent_keys_retired": 0,
        "gateway_confirmed": 4,
        "gateway_pending": 0,
    }
    assert gateway.revoke_key.call_count == gateway.get_key.call_count == 4
    assert ee_credential_retirement.retire_ee_credentials() == second
    assert gateway.revoke_key.call_count == gateway.get_key.call_count == 8


@pytest.mark.parametrize(
    "readback",
    [
        None,
        {},
        {"status": "revoked"},
        {"id": "different", "status": "revoked"},
        {"metadata": []},
    ],
)
def test_malformed_readback_never_confirms_retirement(history, monkeypatch, readback):
    gateway = Mock()
    gateway.get_key.return_value = readback
    monkeypatch.setattr(ee_credential_retirement, "get_gateway_client", lambda: gateway)
    result = ee_credential_retirement.retire_ee_credentials()
    assert result["gateway_confirmed"] == 0 and result["gateway_pending"] == 4
    assert gateway.get_key.call_count == 4


def test_data_migration_retires_by_binding_without_deleting_replay_history(history):
    import importlib

    org, personal, ordinary, credentials, bindings, record = history
    module = importlib.import_module("agentcc.migrations.0058_retire_ee_credentials")
    editor = Mock(connection=connection)
    module.retire_credentials(apps, editor)
    module.retire_credentials(apps, editor)
    assert not AgentccEEServiceCredential.objects.exclude(status="revoked").exists()
    assert (
        AgentccAPIKey._base_manager.filter(
            ee_agent_key__isnull=False, status="revoked"
        ).count()
        == 4
    )
    ordinary.refresh_from_db()
    personal.refresh_from_db()
    assert ordinary.status == "active" and personal.enabled
    assert AgentccEEAgentKeyMintRecord.objects.filter(pk=record.pk).exists()

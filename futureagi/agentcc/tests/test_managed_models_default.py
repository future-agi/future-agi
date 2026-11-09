"""Future AGI models on by default for a new organization (one key that just
works): its first org-config version turns them on where the deployment
offers them, and its first key gives it that version. Existing organizations
keep their setting, and nothing turns on where the deployment does not offer
them (agentcc/services/managed_models_default.py)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.services import managed_models_default as default
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.services.org_config_writer import MERGE, write_config_version

PROBE = "agentcc.services.managed_models_default.get_gateway_client"
PUSH = "agentcc.services.config_push.push_org_config"
KEYS = "/agentcc/api-keys/"


@pytest.fixture
def auth_client(user, organization, workspace):
    # Same fixture person and role; explicit key issuance needs a real session.
    from agentcc.tests.test_personal_key_self_service import session_client

    return session_client(user, organization, workspace)


def _offering_gateway():
    client = MagicMock()
    client.get_org_credits.return_value = {"balance_usd": 0, "markup_percent": 5}
    return client


def _refusing_gateway():
    client = MagicMock()
    client.get_org_credits.side_effect = GatewayClientError(
        "404 managed_models_disabled"
    )
    return client


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()


def _first_write(org):
    config, _ = write_config_version(
        org, {"cache": {"enabled": True}}, mode=MERGE, push=lambda config: True
    )
    return config


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setenv(default.SETTING, "true")


@pytest.fixture
def old_org(db):
    org = Organization.objects.create(name=f"Old org {uuid.uuid4().hex[:6]}")
    Organization.objects.filter(pk=org.pk).update(
        created_at=datetime(2026, 1, 15, tzinfo=UTC)
    )
    org.refresh_from_db()
    return org


@pytest.mark.integration
class TestFirstVersionDefault:
    def test_new_org_gets_future_agi_models_on(self, declared, organization):
        with patch(PROBE, return_value=_offering_gateway()):
            config = _first_write(organization)

        assert config.managed_models == {"enabled": True}

    def test_off_where_the_gateway_does_not_offer_them(self, declared, organization):
        with patch(PROBE, return_value=_refusing_gateway()):
            config = _first_write(organization)

        assert config.managed_models == {}

    def test_off_unless_the_deployment_declares_them(self, monkeypatch, organization):
        monkeypatch.delenv(default.SETTING, raising=False)
        client = _offering_gateway()
        with patch(PROBE, return_value=client):
            config = _first_write(organization)

        assert config.managed_models == {}
        client.get_org_credits.assert_not_called()

    def test_existing_org_keeps_its_setting(self, declared, organization):
        """An org that already has a version keeps its managed_models, off
        included, on every later write."""
        with patch(PROBE, return_value=_refusing_gateway()):
            assert _first_write(organization).managed_models == {}
        with patch(PROBE, return_value=_offering_gateway()):
            config = _first_write(organization)

        assert config.version == 2
        assert config.managed_models == {}

    def test_org_created_before_the_change_starts_off(self, declared, old_org):
        client = _offering_gateway()
        with patch(PROBE, return_value=client):
            config = _first_write(old_org)

        assert config.managed_models == {}
        client.get_org_credits.assert_not_called()

    def test_the_first_write_can_still_turn_them_off(self, declared, organization):
        with patch(PROBE, return_value=_offering_gateway()):
            config, _ = write_config_version(
                organization,
                {"managed_models": {"enabled": False}},
                mode=MERGE,
                push=lambda config: True,
            )

        assert config.managed_models == {"enabled": False}


@pytest.fixture
def key_gateway():
    """The gateway client auth_bridge calls to create a key, mocked."""
    client = MagicMock()
    client.create_key.return_value = {
        "id": f"gw-{uuid.uuid4().hex[:8]}",
        "key": "pk-test-raw-key-value",  # a test value, not a credential
        "key_prefix": "pk-test",
    }
    with patch(
        "agentcc.services.auth_bridge.get_gateway_client", return_value=client
    ) as factory:
        yield client
        factory.assert_called_once()
        client.create_key.assert_called_once()


def _create_from_session(auth_client, session_auth_client, key_gateway, org, name):
    """Only an explicit browser-session mint may initialize model defaults."""
    before_keys = AgentccAPIKey.no_workspace_objects.count()
    before_configs = list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("pk")
        .values("pk", "version", "managed_models", "is_active")
    )
    response = auth_client.post(KEYS, {"name": name}, format="json")
    assert response.status_code == 403, response.content
    assert AgentccAPIKey.no_workspace_objects.count() == before_keys
    assert list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("pk")
        .values("pk", "version", "managed_models", "is_active")
    ) == before_configs
    key_gateway.create_key.assert_not_called()
    return session_auth_client.post(KEYS, {"name": name}, format="json")


@pytest.mark.integration
@pytest.mark.api
class TestFirstKey:
    def test_new_org_first_key_turns_future_agi_models_on(
        self, declared, auth_client, session_auth_client, user, key_gateway
    ):
        org = user.organization
        with (
            patch(PROBE, return_value=_offering_gateway()),
            patch(PUSH, return_value=True) as push,
        ):
            response = _create_from_session(
                auth_client, session_auth_client, key_gateway, org, "first-key"
            )

        assert response.status_code in (200, 201), response.content
        config = _active(org)
        assert config is not None
        assert config.managed_models == {"enabled": True}
        push.assert_called_once()

    def test_no_version_written_where_not_offered(
        self, declared, auth_client, session_auth_client, user, key_gateway
    ):
        with (
            patch(PROBE, return_value=_refusing_gateway()),
            patch(PUSH, return_value=True) as push,
        ):
            response = _create_from_session(
                auth_client, session_auth_client, key_gateway,
                user.organization, "first-key",
            )

        assert response.status_code in (200, 201), response.content
        assert _active(user.organization) is None
        push.assert_not_called()

    def test_an_org_with_a_version_is_left_alone(
        self, declared, auth_client, session_auth_client, user, key_gateway
    ):
        org = user.organization
        with patch(PROBE, return_value=_refusing_gateway()):
            write_config_version(
                org, {"managed_models": {"enabled": False}}, push=lambda config: True
            )
        with (
            patch(PROBE, return_value=_offering_gateway()),
            patch(PUSH, return_value=True),
        ):
            response = _create_from_session(
                auth_client, session_auth_client, key_gateway, org, "second-key"
            )

        assert response.status_code in (200, 201), response.content
        assert _active(org).managed_models == {"enabled": False}
        assert _active(org).version == 1


@pytest.mark.integration
class TestEveryFirstVersion:
    """An org's first version gets the default whichever action writes it:
    adding its own provider first (the provider-credentials view, or the
    gateway view's provider save, delete and reload, which push through
    _get_or_create_org_config) must not leave a new org's Future AGI models
    off, as the first key would then find a version and keep it."""

    def _provider_push(self, org):
        from agentcc.views.provider_credential import (
            AgentccProviderCredentialViewSet,
        )

        with patch("agentcc.views.provider_credential.push_org_config") as push:
            push.return_value = True
            AgentccProviderCredentialViewSet()._push_config_to_gateway(org)
        return _active(org)

    def _gateway_first(self, org):
        from agentcc.views.gateway import AgentccGatewayViewSet

        return AgentccGatewayViewSet()._get_or_create_org_config(org)

    def test_provider_first_turns_future_agi_models_on(self, declared, organization):
        with patch(PROBE, return_value=_offering_gateway()):
            config = self._provider_push(organization)

        assert (config.version, config.managed_models) == (1, {"enabled": True})

    def test_gateway_view_first_turns_future_agi_models_on(
        self, declared, organization
    ):
        with patch(PROBE, return_value=_offering_gateway()):
            config = self._gateway_first(organization)

        assert (config.version, config.managed_models) == (1, {"enabled": True})

    def test_provider_first_then_first_key_keeps_them_on(
        self, declared, auth_client, session_auth_client, user, key_gateway
    ):
        org = user.organization
        with (
            patch(PROBE, return_value=_offering_gateway()),
            patch(PUSH, return_value=True),
        ):
            self._provider_push(org)
            response = _create_from_session(
                auth_client, session_auth_client, key_gateway, org, "first-key"
            )

        assert response.status_code in (200, 201), response.content
        assert (_active(org).version, _active(org).managed_models) == (
            1,
            {"enabled": True},
        )

    def test_other_first_versions_stay_off_where_not_offered(
        self, declared, organization, old_org
    ):
        with patch(PROBE, return_value=_refusing_gateway()):
            assert self._provider_push(organization).managed_models == {}
        client = _offering_gateway()
        with patch(PROBE, return_value=client):
            assert self._gateway_first(old_org).managed_models == {}
        client.get_org_credits.assert_not_called()

    def test_other_first_versions_stay_off_unless_declared(
        self, monkeypatch, organization
    ):
        monkeypatch.delenv(default.SETTING, raising=False)
        client = _offering_gateway()
        with patch(PROBE, return_value=client):
            assert self._gateway_first(organization).managed_models == {}
        client.get_org_credits.assert_not_called()


def _gateway_answering(status, body):
    """The real gateway client, its HTTP answered as a gateway does: 404
    managed_models_disabled where its config marks no provider managed
    (agentcc-gateway/internal/server/managed.go managedRoute), 200 with the
    org's credits where one is."""
    seen = []

    def answer(request):
        seen.append(request.url.path)
        return httpx.Response(status, json=body)

    real = httpx.Client

    def client(**kwargs):
        return real(transport=httpx.MockTransport(answer), **kwargs)

    gateway = GatewayClient(base_url="http://gateway.test", admin_token="t" * 64)
    return gateway, client, seen


@pytest.mark.integration
class TestDefaultFollowsTheGatewaysConfig:
    """The default takes effect only where the gateway's config marks a
    provider managed. The gateway's side is pinned by
    agentcc-gateway/cmd/agentcc/onekey_shipped_configs_test.go: on every
    shipped config the credits route answers 404 managed_models_disabled, and
    it answers 200 once a provider is managed. Here the backend reads those
    two answers through its real client."""

    def _first_version(self, org, status, body):
        gateway, client, seen = _gateway_answering(status, body)
        with (
            patch(PROBE, return_value=gateway),
            patch("agentcc.services.gateway_client.httpx.Client", client),
        ):
            managed = default.initial_managed_models(org)
        return managed, seen

    def test_no_managed_provider_leaves_them_off(self, declared, organization):
        managed, seen = self._first_version(
            organization,
            404,
            {
                "error": {
                    "type": "not_found",
                    "code": "managed_models_disabled",
                    "message": "this gateway has no managed providers"
                    " (config.yaml providers with managed: true)",
                }
            },
        )

        assert managed == {}
        assert seen == [f"/-/orgs/{organization.id}/credits"]

    def test_a_managed_provider_turns_them_on(self, declared, organization):
        managed, seen = self._first_version(
            organization, 200, {"balance_usd": 0, "markup_percent": 5}
        )

        assert managed == {"enabled": True}
        assert seen == [f"/-/orgs/{organization.id}/credits"]

"""Postgres rows: all eight states and failed ON rollback.

Provider probe/push use the existing catalogue fixture transports; no egress.
"""

import itertools
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog as catalog
from agentcc.tests.test_connector_catalog import (  # noqa: F401
    _active,
    _add,
    _key,
    _seed,
    probe_budget,
)
from agentcc.tests.test_connector_catalog import (
    bundle as bundle_fixture,
)
from agentcc.tests.test_connector_catalog import (
    probe as probe_fixture,
)
from agentcc.tests.test_connector_catalog import (
    pushes as pushes_fixture,
)

bundle, probe, pushes = bundle_fixture, probe_fixture, pushes_fixture

pytestmark = pytest.mark.django_db
FLAGS = (
    "AGENTCC_MCP_CATALOG_ENABLED",
    "AGENTCC_MCP_CREDENTIAL_VAULT_ENABLED",
    "AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED",
)


def counts():
    return (
        AgentccOrgConfig._base_manager.count(),
        AgentccMCPCredential._base_manager.count(),
    )


@pytest.mark.parametrize("switches", list(itertools.product((False, True), repeat=3)))
def test_eight_state_atomic_connect(auth_client, user, bundle, probe, pushes, switches):
    org = user.organization
    _seed(
        org,
        {
            "enabled": True,
            "servers": {"static": {"url": "https://fixture.invalid/mcp"}},
        },
    )
    before = counts()
    before_mcp = _active(org).mcp
    with override_settings(**dict(zip(FLAGS, switches, strict=True))):
        response = auth_client.post(
            _add("acme-docs"), {"mode": "agent", **_key()}, format="json"
        )
    if all(switches):
        assert response.status_code == 201, response.status_code
        current = _active(org).mcp
        assert "acme-docs" in current["servers"]
        assert "acme-docs" in current["virtual_servers"]
        assert counts() == (before[0] + 1, before[1] + 1)
        assert len(pushes) == 1
    else:
        assert response.status_code == 404
        assert response.json()["code"].endswith("_disabled")
        assert counts() == before
        assert _active(org).mcp == before_mcp
        assert probe.calls == []
        assert pushes == []


def test_all_on_failure_after_vault_store_rolls_back_everything(
    user, bundle, probe, pushes
):
    org = user.organization
    _seed(org, {"enabled": True, "servers": {}})
    before = counts()
    before_mcp = _active(org).mcp
    actual = catalog.mcp_credentials.store

    def stored_then_fail(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("fixture rollback")

    with (
        override_settings(**dict.fromkeys(FLAGS, True)),
        patch.object(catalog.mcp_credentials, "store", side_effect=stored_then_fail),
        pytest.raises(RuntimeError, match="fixture rollback"),
    ):
        catalog.add_connector(
            org,
            "acme-docs",
            provider_id=None,
            mode="agent",
            url_variables={},
            actor=user,
            credential="fixture-not-a-real-key",
        )
    assert counts() == before
    assert _active(org).mcp == before_mcp
    assert pushes == []


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "none"},
        {"type": "bearer", "token": "synthetic-static-token"},
        {"type": "api_key", "header": "X-Static-Key", "key": "synthetic-static-key"},
    ],
)
def test_pre_existing_static_auth_all_off(auth_client, user, pushes, auth):
    with override_settings(**dict.fromkeys(FLAGS, False)):
        response = auth_client.post(
            "/agentcc/gateways/default/update-mcp-server/",
            {
                "server_id": "static",
                "config": {"url": "https://static.example.com/mcp", "auth": auth},
            },
            format="json",
        )
        assert response.status_code == 200
        saved = _active(user.organization).mcp["servers"]["static"]
        assert saved["url"] == "https://static.example.com/mcp"
        assert pushes
        if auth["type"] != "none":
            assert saved.get("credential", {}).get("credential_id")
            assert AgentccMCPCredential._base_manager.filter(
                organization=user.organization
            ).exists()
        response = auth_client.post(
            "/agentcc/gateways/default/remove-mcp-server/",
            {"server_id": "static"},
            format="json",
        )
        assert response.status_code == 200
        assert "static" not in _active(user.organization).mcp["servers"]


@pytest.mark.parametrize("spelling", ["virtual_servers", "virtualServers"])
@pytest.mark.parametrize("binding", ["key", "default"])
@pytest.mark.parametrize(
    "target_has_named", [False, True], ids=["rollback", "reactivate"]
)
def test_off_activation_preserves_restricted_active_config_without_writes_or_push(
    user, pushes, spelling, binding, target_has_named
):
    from agentcc.services.org_config_writer import (
        MCPConfigRejected,
        activate_config_version,
    )

    org = user.organization
    static = {
        "enabled": True,
        "servers": {"static": {"url": "https://fixture.invalid/mcp"}},
    }
    selection = {"id": "vs_restricted", "members": ["static"]}
    if binding == "key":
        selection["access"] = {"keys": ["restricted-key-id"]}
    else:
        selection["access"] = {"default_for_org": True}
    retained = {**static, spelling: {"restricted": selection}}
    target = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=False,
        mcp=retained if target_has_named else static,
    )
    active = AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=2, is_active=True, mcp=retained
    )
    versions = AgentccOrgConfig.no_workspace_objects.filter(organization=org).order_by(
        "pk"
    )
    before = list(versions.values())
    before_counts = counts()
    push = Mock(return_value=True)
    with override_settings(**dict.fromkeys(FLAGS, False)):
        with pytest.raises(MCPConfigRejected) as error:
            activate_config_version(target, push=push, user=user)
    assert error.value.code == "mcp_virtual_servers_disabled"
    assert list(versions.values()) == before
    assert counts() == before_counts
    assert _active(org).pk == active.pk
    push.assert_not_called()
    assert pushes == []

"""Integrator tests for M14's items at the W3b merge (X6-W3b)."""

import pytest

from model_hub.apps import (
    guarded_management_command,
    operator_startup_mutation_authorized,
)


@pytest.mark.unit
class TestMigrateSecretsCommandAllowed:
    """M14 IR-4: the startup guard let neither the migration nor its check run."""

    def test_check_form_runs_anywhere(self):
        argv = ["manage.py", "agentcc_mcp_migrate_secrets", "--check"]
        assert guarded_management_command(argv) is None

    @pytest.mark.parametrize(
        "argv",
        [
            # The bare form is a dry run; it stays an operator
            # command, as before.
            ["manage.py", "agentcc_mcp_migrate_secrets"],
            ["manage.py", "agentcc_mcp_migrate_secrets", "--check", "--org", "x"],
            ["manage.py", "agentcc_mcp_migrate_secrets", "--apply"],
        ],
    )
    def test_writing_or_open_forms_stay_guarded(self, argv):
        assert guarded_management_command(argv) == "agentcc_mcp_migrate_secrets"

    def test_migration_is_an_operator_one_shot(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
        monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
        argv = ["manage.py", "agentcc_mcp_migrate_secrets", "--apply"]
        assert operator_startup_mutation_authorized(argv) is True


def _version(organization, version, is_active, **fields):
    from agentcc.models.org_config import AgentccOrgConfig

    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=version, is_active=is_active, **fields
    )


def _versions(organization):
    from agentcc.models.org_config import AgentccOrgConfig

    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


@pytest.mark.integration
@pytest.mark.api
class TestActivationRefusesPlaintextMCPSecrets:
    """M14 IR-1: activation reruns the save guard's secret check.

    A version saved before M14 can still hold a plaintext MCP secret until
    agentcc_mcp_migrate_secrets runs; re-activating it must not put the
    secret back on the active config and into a push.
    """

    def test_activate_refuses_a_version_with_an_inline_mcp_secret(
        self, auth_client, organization
    ):
        from unittest.mock import patch

        from rest_framework import status

        legacy = _version(
            organization,
            1,
            False,
            mcp={
                "enabled": True,
                "servers": {
                    "github": {
                        "url": "https://mcp.example/mcp",
                        "auth": {"type": "bearer", "token": "pre-m14-plaintext"},
                    }
                },
            },
        )
        _version(organization, 2, True, mcp={"enabled": True})

        with patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
            return_value=True,
        ) as push:
            response = auth_client.post(f"/agentcc/org-configs/{legacy.id}/activate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert "secret_in_config" in str(response.json())
        assert _versions(organization) == [(1, False), (2, True)]
        push.assert_not_called()


@pytest.mark.unit
def test_url_refusal_points_at_the_vault_not_an_inline_secret():
    """M14 IR-14: the URL rule's hint named credential.secret, which M14's
    save guard now refuses (secret_in_config)."""
    from rest_framework import serializers

    from agentcc.serializers.org_config import _validate_mcp_server_endpoint

    with pytest.raises(serializers.ValidationError) as exc:
        _validate_mcp_server_endpoint(
            "mcp.servers.x", {"url": "https://user:pw@mcp.example/mcp"}, None
        )
    text = str(exc.value.detail)
    assert "/agentcc/mcp/credentials/" in text
    assert "credential.secret" not in text

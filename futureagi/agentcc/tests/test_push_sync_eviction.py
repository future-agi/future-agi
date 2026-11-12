"""Actual outer commits must not turn an incomplete eviction into a failed save."""

from unittest.mock import patch

import pytest
from django.db import transaction

from agentcc.models.org_config import AgentccOrgConfig as Config
from agentcc.services import mcp_shared_credential_lifecycle as lifecycle
from agentcc.services.org_config_writer import (
    GATEWAY_SYNC_FAILED,
    GATEWAY_SYNC_FAILED_MESSAGE,
    activate_config_version,
    is_gateway_synced,
    write_config_version,
)
from agentcc.tests import test_mcp_shared_credential_eviction as fixtures
from agentcc.tests.test_mcp_shared_credential_eviction import _create, _seed

gateway = fixtures.gateway
pushes = fixtures.pushes

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("via", ["writer", "activate", "endpoint"])
@pytest.mark.parametrize("outcome", ["raised", "false", "success"])
def test_committed_eviction_outcome(user, auth_client, pushes, gateway, via, outcome):
    org = user.organization
    old = _seed(org)
    row = _create(org, user).credential
    gateway.calls.clear()
    with (
        patch.object(
            lifecycle, "_retry_org_locked", return_value=outcome == "success"
        ) as retry,
        patch.object(lifecycle.logger, "exception") as logged,
    ):
        if outcome == "raised":
            retry.side_effect = RuntimeError("synthetic eviction failure")
        if via == "endpoint":
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"mcp": {"servers": {"github": None}}},
                format="json",
            )
            assert response.status_code == 200, response.json()
            result = response.json()["result"]
            assert result["gateway_synced"] is (outcome == "success")
            if outcome != "success":
                assert result["gateway_warning"] == GATEWAY_SYNC_FAILED_MESSAGE
        else:
            if via == "writer":
                _, warnings = write_config_version(
                    org, {"mcp": {"servers": {"github": None}}}, user=user
                )
            else:
                _, warnings = activate_config_version(old, user=user)
            assert is_gateway_synced(warnings) is (outcome == "success")
            if outcome != "success":
                assert {
                    "code": GATEWAY_SYNC_FAILED,
                    "operation": lifecycle.SHARED_CREDENTIAL_REVOCATION,
                    "message": lifecycle.SHARED_CREDENTIAL_REVOCATION_MESSAGE,
                } in warnings
        retry.assert_called_once_with(org.pk, ["github"])
        if outcome == "raised":
            logged.assert_called_once_with(
                "mcp_shared_eviction_after_commit_failed", org_id=str(org.pk)
            )
    row.refresh_from_db()
    assert row.status == "revoked"
    if outcome != "success":
        assert row.gateway_purge_pending
    active = Config.no_workspace_objects.get(organization=org, is_active=True)
    assert not active.mcp.get("servers", {}).get("github", {}).get("credential")


def test_outer_transaction_keeps_pending_warning_until_commit(user, pushes, gateway):
    org = user.organization
    _seed(org)
    _create(org, user)
    with patch.object(lifecycle, "_retry_org_locked", return_value=True) as retry:
        with transaction.atomic():
            _, warnings = write_config_version(
                org, {"mcp": {}}, mode="replace_sections", user=user
            )
            assert not is_gateway_synced(warnings)
            retry.assert_not_called()
        retry.assert_called_once()
        assert is_gateway_synced(warnings)


def test_eviction_success_does_not_erase_failed_push_warning(user, pushes, gateway):
    org = user.organization
    _seed(org)
    _create(org, user)
    with patch.object(lifecycle, "_retry_org_locked", return_value=True):
        _, warnings = write_config_version(
            org,
            {"mcp": {}},
            mode="replace_sections",
            user=user,
            push=lambda config: False,
        )
    assert not is_gateway_synced(warnings)
    assert warnings == [
        {"code": GATEWAY_SYNC_FAILED, "message": GATEWAY_SYNC_FAILED_MESSAGE}
    ]

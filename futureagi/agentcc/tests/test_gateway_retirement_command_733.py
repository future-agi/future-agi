"""F9 purpose selection and OFF-only precondition (never a batch drain gate)."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.management.base import CommandError

from agentcc.models import AgentccAPIKey
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.services import retirement_733
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.test_retirement_733 import make_key

pytestmark = pytest.mark.django_db


def test_f9_selects_purpose_even_with_person_and_preserves_other_ownerless(
    organization, user
):
    batch = make_key(organization, user, metadata={"purpose": "console-batch"})
    playground = make_key(organization, user, owner="playground")
    ee = make_key(organization, user, owner="omega-ee")
    adopted = make_key(organization, metadata={"source": "sync"})
    personal = make_key(organization, user)
    unrelated_ownerless = make_key(organization)
    selected = set(retirement_733.selected_keys().values_list("pk", flat=True))
    assert {batch.pk, playground.pk, ee.pk, adopted.pk} <= selected
    assert personal.pk not in selected
    assert unrelated_ownerless.pk not in selected


def test_f9_refuses_before_io_unless_real_off_switch_set(organization, user):
    make_key(organization, user, owner="playground")
    adapter = MagicMock()
    adapter.issuance_is_off.return_value = False
    with patch("agentcc.services.auth_bridge.get_gateway_client") as gateway:
        with pytest.raises(CommandError, match="OFF switch"):
            retirement_733.retire_selected_keys(adapter)
    gateway.assert_not_called()
    adapter.record.assert_not_called()


def test_f9_records_prior_state_and_requires_readback(organization, user):
    key = make_key(organization, user, metadata={"purpose": "console-batch"})
    adapter = MagicMock()
    adapter.issuance_is_off.return_value = True
    client = MagicMock()
    client.revoke_key.side_effect = GatewayClientError("unavailable", status_code=503)
    client.list_keys.return_value = {
        "data": [{"id": key.gateway_key_id, "status": "active"}]
    }
    with patch("agentcc.services.auth_bridge.get_gateway_client", return_value=client):
        report = retirement_733.retire_selected_keys(adapter)
    key.refresh_from_db()
    assert key.status == AgentccAPIKey.REVOKED
    assert report["pending"] == [key.gateway_key_id]
    client.revoke_key.assert_called_once_with(key.gateway_key_id)
    client.list_keys.assert_called_once()
    row = adapter.record.call_args.kwargs
    assert row["prior_state"] == {"status": "active", "expires_at": None}
    assert row["confirmed"] is False
    assert set(row["prior_state"]) == {"status", "expires_at"}
    # A retry confirms a missing key on the complete GET list, after negative sync.
    client.revoke_key.side_effect = None
    client.list_keys.return_value = {"data": []}
    with patch("agentcc.services.auth_bridge.get_gateway_client", return_value=client):
        report = retirement_733.retire_selected_keys(adapter)
    assert report["pending"] == []
    assert report["revoked"] == [key.gateway_key_id]


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: provisioning.issue_credential(None, None),
        lambda: provisioning.rotate_credential(None, None),
        lambda: provisioning.mint_agent_key(None, None, "unused"),
        lambda: provisioning.ensure(None, None),
        lambda: provisioning._provision_locked(None, None, None, None),
    ],
)
def test_cn22_off_refuses_before_mint_or_gateway_io(invoke):
    with patch("agentcc.services.auth_bridge.provision_key") as mint:
        with pytest.raises(provisioning.ProvisioningError) as exc:
            invoke()
    assert exc.value.status == 409
    assert exc.value.code == "feature_unavailable_733"
    mint.assert_not_called()

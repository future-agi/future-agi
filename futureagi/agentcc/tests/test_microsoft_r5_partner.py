"""Microsoft partner rows need proven public identity before secret custody."""
import json
from unittest.mock import Mock

import pytest

from agentcc.services.mcp_vendor_http import VendorUnavailable
from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.tests.test_microsoft_identity_exclusion import FILE_ID
from agentcc.tests.test_microsoft_secret_chokepoint import identities

pytestmark = pytest.mark.django_db
rig = gate.rig


def partner(rig, embedded_id, public_id, *, microsoft=True):
    org, user, config, _ = rig
    if not microsoft:
        config.mcp["servers"]["partner"].pop("catalog_ref", None)
        config.save(update_fields=["mcp"])
    credential = gate.Credential.objects.create(
        organization=org, server_id="partner", scope="shared", kind="oauth_client",
        origin="https://api.pipedream.com", oauth_client_id=public_id,
        encrypted_secret=gate.credential_manager.encrypt_token(json.dumps({
            "client_id": embedded_id, "client_secret": gate.SECRET,
        })),
    )
    binding = gate.Binding.objects.create(organization=org, server_id="partner",
        vendor="pipedream", credential=credential, project_id="proj_fixture",
        environment="development", app_slug=gate.OUTLOOK if microsoft else "gmail",
        enabled=True)
    row = gate.VendorConnection.objects.create(binding=binding,
        end_user_key=gate.grants.subject_key(org.pk, "member", str(user.pk)),
        source="member", user=user, status="active", vendor_account_ref="fixture-account")
    return binding, row


@pytest.mark.parametrize("embedded_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("public_id", ["", "fixture-inconsistent-public", gate.PLATFORM_ID])
@pytest.mark.parametrize("operation", ["custody", "grant", "revoke"])
def test_microsoft_partner_metadata_refused_before_decrypt(
    rig, identities, monkeypatch, embedded_id, public_id, operation,
):
    binding, row = partner(rig, embedded_id, public_id)
    _, _, _, fake = rig
    outbound = Mock(side_effect=AssertionError("partner outbound before refusal"))
    monkeypatch.setattr(gate.grants.Pipedream, "bearer", outbound)
    call = {"custody": lambda: gate.grants.vendor_client(binding),
            "grant": lambda: gate.grants._grant(binding, row),
            "revoke": lambda: gate.vendors._revoke_one(row)}[operation]
    with gate.no_effects(monkeypatch, fake):
        with pytest.raises((gate.oauth.ConnectRefused, VendorUnavailable)):
            call()
    outbound.assert_not_called()


def test_non_microsoft_legacy_partner_format_is_unchanged(rig, identities, monkeypatch):
    binding, _ = partner(rig, "fixture-partner-client", "", microsoft=False)
    decrypt = Mock(wraps=gate.grants.decrypt_token)
    monkeypatch.setattr(gate.grants, "decrypt_token", decrypt)
    api = gate.grants.vendor_client(binding)
    assert isinstance(api, gate.grants.Pipedream)
    assert decrypt.call_count == 1

"""Credential lifetime metadata survives the real push builder; no upstream I/O."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.services import config_push
from agentcc.services import mcp_credentials as vault
from agentcc.tests.test_mcp_credentials import _active, _seed, pushes  # noqa: F401


@pytest.mark.django_db
@pytest.mark.usefixtures("pushes")
@pytest.mark.parametrize("lifetime", [timedelta(hours=1), timedelta(seconds=-1)])
@pytest.mark.parametrize("spelling", ["credential_id", "credentialId", "credentialID"])
def test_credential_lifecycle_push_carries_current_expiry(user, lifetime, spelling):
    org = user.organization
    _seed(org)
    created = vault.create(
        org, "github", kind="bearer", value="synthetic-lifetime", actor=user
    ).credential
    expires = timezone.now() + lifetime
    created.token_expires_at = expires
    created.save(update_fields=["token_expires_at"])
    config = _active(org)
    reference = config.mcp["servers"]["github"]["credential"]
    reference[spelling] = reference.pop("credential_id")
    config.save(update_fields=["mcp"])
    with patch("agentcc.services.config_push.get_gateway_client"):
        payload = config_push._mcp_payload(org.pk, _active(org))
    credential = payload["servers"]["github"]["credential"]
    assert credential.get("expires_at") == expires.isoformat()
    if lifetime.total_seconds() < 0:
        assert credential["state"] == "expired"
        assert "secret" not in credential
    else:
        assert credential["state"] == "active"
        assert "secret" in credential
    assert "expires_at" not in _active(org).mcp["servers"]["github"]["credential"]


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["revoked", "expired", "needs_reauth"])
def test_credential_lifecycle_lapse_push_is_authoritative(
    user, request, state, gateway_unreachable
):
    sent = request.getfixturevalue("pushes")
    org = user.organization
    _seed(org)
    row = vault.create(
        org, "github", kind="bearer", value="synthetic-lifetime", actor=user
    ).credential
    pushed_before = len(sent)
    vault.mark_lapsed(row, state, user)
    # The fixture intercepts get_gateway_client().set_org_config, before HTTP.
    # Require a new push for this lapse, not the earlier creation payload.
    assert len(sent) == pushed_before + 1
    ref = sent[-1][1]["mcp"]["servers"]["github"]["credential"]
    assert ref["credential_id"] == str(row.pk)
    assert ref["state"] == state
    assert "secret" not in ref
    assert gateway_unreachable, "this path asks the gateway, which is not running"

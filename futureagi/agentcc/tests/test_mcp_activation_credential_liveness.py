"""W4: a rollback refuses unusable references instead of silently dropping auth."""

import copy
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault
from agentcc.tests import test_r1_mcp_origin_change as origin_fixtures
from agentcc.tests.test_r1_mcp_origin_change import (
    DOCS_ORIGIN,
    MOVED,
    SAME_ORIGIN,
    SECRET,
    URL,
    _activate,
    _activation_refused,
    _active,
    _console,
    _pushed_after,
    _saved_with_secret,
)

pytestmark = [pytest.mark.integration, pytest.mark.api]
pushes = origin_fixtures.pushes


@pytest.mark.parametrize("status", ["active", "revoked", "expired", "needs_reauth"])
def test_already_active_target_validates_references(auth_client, user, pushes, status):
    org = user.organization
    reference = _saved_with_secret(auth_client, org, pushes)
    active = _active(org)
    Credential.objects.filter(pk=reference["credential_id"]).update(status=status)
    pushed_before = len(pushes)

    response = _activate(auth_client, active)

    if status == "active":
        assert response.status_code == 200, response.json()
        assert len(pushes) == pushed_before
    else:
        _activation_refused(response, org, pushes, pushed_before, active)


@pytest.mark.parametrize("spelling", ["credential_id", "credentialId"])
@pytest.mark.parametrize(
    "unusable",
    [
        "revoked",
        "expired",
        "needs_reauth",
        "missing",
        "malformed",
        "other_server",
        "unbound",
        "origin",
        "expiry",
        "reference_state",
    ],
)
def test_unusable_reference_refuses_activation(
    auth_client, user, pushes, spelling, unusable
):
    org = user.organization
    reference = _saved_with_secret(auth_client, org, pushes)
    older = _active(org)
    assert _console(auth_client, {"url": SAME_ORIGIN["path"]}).status_code == 200
    active = _active(org)
    row = Credential.objects.get(pk=reference["credential_id"])
    mcp = copy.deepcopy(older.mcp)
    ref = mcp["servers"]["docs"]["credential"]
    ref[spelling] = ref.pop("credential_id")
    if unusable in ("revoked", "expired", "needs_reauth"):
        Credential.objects.filter(pk=row.pk).update(status=unusable)
    elif unusable == "missing":
        ref[spelling] = str(uuid.uuid4())
    elif unusable == "malformed":
        ref[spelling] = "not-a-credential-id"
    elif unusable == "other_server":
        Credential.objects.filter(pk=row.pk).update(server_id="wiki")
    elif unusable == "unbound":
        Credential.objects.filter(pk=row.pk).update(origin="")
    elif unusable == "origin":
        Credential.objects.filter(pk=row.pk).update(
            origin=vault.origin_of(MOVED["host"])
        )
    elif unusable == "expiry":
        Credential.objects.filter(pk=row.pk).update(
            token_expires_at=timezone.now() - timedelta(seconds=1)
        )
    else:
        ref["state"] = "revoked"
    AgentccOrgConfig.no_workspace_objects.filter(pk=older.pk).update(mcp=mcp)
    pushed_before = len(pushes)

    response = _activate(auth_client, older)

    _activation_refused(response, org, pushes, pushed_before, active)
    assert _pushed_after(pushes, pushed_before) == []


@pytest.mark.parametrize("status", ["active", "revoked"])
@pytest.mark.parametrize(
    "origin", [DOCS_ORIGIN, "", "https://collector.example.net:443"]
)
def test_shared_oauth_reference_is_checked_without_decrypting(
    auth_client, user, pushes, status, origin
):
    org = user.organization
    reference = _saved_with_secret(auth_client, org, pushes)
    older = _active(org)
    assert _console(auth_client, {"url": SAME_ORIGIN["path"]}).status_code == 200
    active = _active(org)
    Credential.objects.filter(pk=reference["credential_id"]).update(
        kind="oauth", status=status, origin=origin
    )
    mcp = copy.deepcopy(older.mcp)
    mcp["servers"]["docs"]["credential"]["kind"] = "oauth"
    AgentccOrgConfig.no_workspace_objects.filter(pk=older.pk).update(mcp=mcp)
    pushed_before = len(pushes)

    response = _activate(auth_client, older)

    if status == "active" and origin == DOCS_ORIGIN:
        assert response.status_code == 200, response.json()
        # OAuth tokens are fetched on use, never pushed inline.
        assert _pushed_after(pushes, pushed_before) == [(URL, None)]
    else:
        _activation_refused(response, org, pushes, pushed_before, active)


def test_removed_same_origin_reference_still_requires_reconnect(
    auth_client, user, pushes
):
    org = user.organization
    reference = _saved_with_secret(auth_client, org, pushes)
    older = _active(org)
    empty = AgentccOrgConfig.no_workspace_objects.get(organization=org, version=1)
    assert _activate(auth_client, empty).status_code == 200
    row = Credential.objects.get(pk=reference["credential_id"])
    assert (row.status, row.origin) == ("revoked", DOCS_ORIGIN)
    pushed_before = len(pushes)

    response = _activate(auth_client, older)

    _activation_refused(response, org, pushes, pushed_before, empty)
    assert _pushed_after(pushes, pushed_before) == []


@pytest.mark.parametrize("managed_spelling", ["managed_tools", "managedTools"])
def test_managed_reference_must_still_be_active(
    auth_client, user, pushes, managed_spelling
):
    org = user.organization
    _saved_with_secret(auth_client, org, pushes)
    managed = vault.create(
        org,
        "fagi.web",
        kind="api_key",
        value=SECRET,
        header_name="x-api-key",
        actor=user,
    )
    older = _active(org)
    assert _console(auth_client, {"url": SAME_ORIGIN["path"]}).status_code == 200
    active = _active(org)
    Credential.objects.filter(pk=managed.credential.pk).update(status="revoked")
    mcp = copy.deepcopy(older.mcp)
    mcp[managed_spelling] = mcp.pop("managed_tools")
    AgentccOrgConfig.no_workspace_objects.filter(pk=older.pk).update(mcp=mcp)
    pushed_before = len(pushes)

    response = _activate(auth_client, older)

    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "mcp_credential_origin_changed"
    assert "fagi.web" in json.dumps(response.json())
    assert "reconnect" in json.dumps(response.json())
    assert _active(org).pk == active.pk
    assert len(pushes) == pushed_before

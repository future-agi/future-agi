"""Every config mutation retires removed shared-static grants after commit."""

import copy
from unittest.mock import patch

import pytest
from django.db import transaction
from rest_framework.test import APIClient

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault
from agentcc.services.org_config_writer import (
    activate_config_version,
    write_config_version,
    write_config_version_with,
)
from agentcc.tests import test_mcp_shared_credential_eviction as fixtures
from agentcc.tests.test_mcp_shared_credential_eviction import SECRET, _create, _seed

# Re-export the seam fixtures without redeclaring their implementations.
gateway = fixtures.gateway
pushes = fixtures.pushes


def _remove(config):
    config.mcp["servers"].pop("github")


def _change(org, user, path, client):
    if path == "reference":
        write_config_version_with(
            org, lambda cfg: cfg.mcp["servers"]["github"].pop("credential"), user=user
        )
    elif path == "server":
        write_config_version_with(org, _remove, user=user)
    elif path == "merge":
        write_config_version(org, {"mcp": {"servers": {"github": None}}}, user=user)
    elif path == "replace":
        write_config_version(org, {"mcp": {}}, mode="replace_sections", user=user)
    elif path in ("activate", "rollback"):
        if path == "rollback":
            # The real older version, before the vault added its reference.
            target = AgentccOrgConfig.no_workspace_objects.get(
                organization=org, version=1
            )
        else:
            target = AgentccOrgConfig.no_workspace_objects.create(
                organization=org, version=3, is_active=False, mcp={}
            )
        activate_config_version(target)
    else:
        action, body = (
            ("remove-mcp-server", {"server_id": "github"})
            if path == "delete"
            else ("update-config", {"mcp": {"servers": {"github": None}}})
        )
        response = client.post(
            f"/agentcc/gateways/default/{action}/", body, format="json"
        )
        assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        "server",
        "reference",
        "merge",
        "replace",
        "activate",
        "rollback",
        "update",
        "delete",
    ],
)
@pytest.mark.parametrize("confirmed", [False, True])
def test_removal_records_then_evicts_after_commit(
    user,
    auth_client,
    pushes,
    gateway,
    django_capture_on_commit_callbacks,
    path,
    confirmed,
):
    org = user.organization
    _seed(org)
    row = _create(org, user).credential
    personal = vault.create(
        org, "github", kind="bearer", value=SECRET, scope="user", user=user, actor=user
    ).credential
    gateway.calls.clear()
    gateway.up = confirmed
    with django_capture_on_commit_callbacks(execute=True):
        _change(org, user, path, auth_client)
        assert gateway.calls == [], "gateway eviction ran before config commit"
        row.refresh_from_db()
        assert row.gateway_purge_pending, "removal did not record a durable obligation"
        assert row.status == "revoked"
    assert (str(org.pk), "github", None, row.grant_revision) in gateway.calls, (
        "generic removal made no revisioned gateway eviction"
    )
    row.refresh_from_db()
    assert row.gateway_purge_pending is not confirmed
    personal.refresh_from_db()
    if path != "delete":  # Explicit server deletion already revokes all accounts.
        assert personal.status == "active"
    # Bulk sync is read-only: it carries the committed removal and cannot
    # resurrect the grant or clear an unconfirmed obligation.
    client = APIClient()
    admin = "synthetic-w4-admin-not-a-real-credential"
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin}")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", admin):
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == 200
    mcp = response.json()["result"][str(org.pk)].get("mcp", {})
    assert "secret" not in mcp.get("servers", {}).get("github", {}).get(
        "credential", {}
    )
    row.refresh_from_db()
    assert row.gateway_purge_pending is not confirmed


@pytest.mark.integration
def test_refused_generic_write_rolls_back_obligation_and_status(
    user, pushes, gateway, django_capture_on_commit_callbacks
):
    org = user.organization
    _seed(org)
    row = _create(org, user).credential
    gateway.calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                write_config_version_with(org, _remove, user=user)
                assert Credential.objects.get(pk=row.pk).gateway_purge_pending
                raise RuntimeError("refused")
    row.refresh_from_db()
    assert row.status == "active" and not row.gateway_purge_pending
    assert gateway.calls == []


@pytest.mark.integration
def test_unchanged_reference_and_unrelated_slots_survive(
    user, pushes, gateway, django_capture_on_commit_callbacks
):
    from accounts.models import Organization

    org = user.organization
    _seed(org)
    row = _create(org, user).credential
    other_org = Organization.objects.create(name="w4 unrelated org")
    _seed(other_org)
    other = _create(other_org, None).credential
    managed = vault.create(
        org, "fagi.web", kind="bearer", value=SECRET, actor=user
    ).credential
    gateway.calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        write_config_version(org, {"cache": {"enabled": False}}, user=user)
    row.refresh_from_db()
    assert row.status == "active" and gateway.calls == []
    with django_capture_on_commit_callbacks(execute=True):
        write_config_version_with(org, _remove, user=user)
    assert gateway.calls == [(str(org.pk), "github", None, row.grant_revision)]
    for kept in (other, managed):
        kept.refresh_from_db()
        assert kept.status == "active" and not kept.gateway_purge_pending


@pytest.mark.integration
def test_rollback_removing_managed_reference_evicts_it(
    user, pushes, gateway, django_capture_on_commit_callbacks
):
    org = user.organization
    old = _seed(org)
    row = vault.create(
        org, "fagi.web", kind="bearer", value=SECRET, actor=user
    ).credential
    gateway.calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        activate_config_version(old)
    assert gateway.calls == [(str(org.pk), "fagi.web", None, row.grant_revision)]
    row.refresh_from_db()
    assert row.status == "revoked" and not row.gateway_purge_pending


@pytest.mark.integration
def test_removal_of_many_slots_calls_each_after_commit(
    user, pushes, gateway, django_capture_on_commit_callbacks
):
    org = user.organization
    config = _seed(org)
    entry = copy.deepcopy(config.mcp["servers"]["github"])
    config.mcp["servers"] = {f"slot-{i}": entry for i in range(5)}
    config.save(update_fields=["mcp"])
    rows = [
        vault.create(
            org, f"slot-{i}", kind="bearer", value=SECRET, actor=user
        ).credential
        for i in range(5)
    ]
    gateway.calls.clear()
    gateway.up = False
    with django_capture_on_commit_callbacks(execute=True):
        write_config_version(org, {"mcp": {}}, mode="replace_sections", user=user)
        assert gateway.calls == []
    assert set(gateway.calls) == {
        (str(org.pk), row.server_id, None, row.grant_revision) for row in rows
    }
    assert (
        Credential.objects.filter(
            pk__in=[row.pk for row in rows], gateway_purge_pending=True
        ).count()
        == 5
    )

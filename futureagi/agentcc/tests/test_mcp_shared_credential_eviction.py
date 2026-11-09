"""Shared static credentials are fenced across gateway replicas (W4).

A shared bearer/API-key credential is pushed with its org-and-server slot's
grant revision, and revoking, lapsing or replacing it calls the gateway
eviction a personal credential uses, naming the revision the slot is revoked
through. These tests fake the gateway admin API at its seam
(``gateway_admin.mcp_credentials.purge``) and the push at the gateway client.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault

SECRET = "test-w4-shared-not-a-real-token"
SECRET_2 = "test-w4-rotated-not-a-real-token"
GITHUB_URL = "https://mcp.github.example.com/mcp"


@pytest.fixture
def pushes():
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


class Gateway:
    """The gateway's POST /-/mcp/credentials/purge: records each call."""

    def __init__(self):
        self.calls = []
        self.up = True

    def purge(self, org_id, server_id, user_id=None, *, revision=None):
        self.calls.append((org_id, server_id, user_id, revision))
        return self.up


@pytest.fixture
def gateway():
    fake = Gateway()
    with patch(
        "agentcc.services.gateway_admin.mcp_credentials.purge", side_effect=fake.purge
    ):
        yield fake


def _seed(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={"servers": {"github": {"url": GITHUB_URL, "transport": "http"}}},
    )


def _create(org, user, value=SECRET):
    return vault.create(org, "github", kind="bearer", value=value, actor=user)


def _pushed(pushes):
    return pushes[-1][1]["mcp"]["servers"]["github"]["credential"]


def _revisions(gateway, org):
    return [call[3] for call in gateway.calls if call[0] == str(org.pk) and call[3]]


@pytest.mark.integration
class TestSharedStaticEviction:
    def test_push_carries_the_vault_grant_revision(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        assert row.grant_revision >= 1
        assert _pushed(pushes)["revision"] == row.grant_revision
        # Push-only: the stored config never holds it.
        stored = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True
        ).mcp["servers"]["github"]["credential"]
        assert "revision" not in stored

    def test_shared_static_revoke_calls_the_gateway_eviction(
        self, user, pushes, gateway
    ):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        gateway.calls.clear()
        vault.revoke(row, user)
        shared = [c for c in gateway.calls if c[:3] == (str(org.pk), "github", None)]
        assert shared, "a shared static revoke made no gateway eviction"
        assert all(isinstance(c[3], int) and c[3] >= 1 for c in shared)

    def test_shared_static_revoke_evicts_its_revision(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        change = vault.revoke(row, user)
        assert change.gateway_synced is True
        # The same seam as a personal eviction, naming no person but the
        # revision every replica must stop and never admit again.
        assert gateway.calls[-1] == (str(org.pk), "github", None, row.grant_revision)
        row.refresh_from_db()
        assert row.status == "revoked"
        assert row.gateway_purge_pending is False

    @pytest.mark.parametrize("state", ["expired", "needs_reauth"])
    def test_a_lapse_evicts_too(self, user, pushes, gateway, state):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        vault.mark_lapsed(row, state, user)
        assert _revisions(gateway, org) == [row.grant_revision]

    def test_a_new_grant_outranks_every_revoked_one(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        first = _create(org, user).credential
        vault.revoke(first, user)
        second = _create(org, user, SECRET_2).credential
        assert second.grant_revision > first.grant_revision
        assert _pushed(pushes)["revision"] == second.grant_revision
        assert max(_revisions(gateway, org)) < second.grant_revision

    def test_rotation_revokes_only_the_replaced_revision(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        before = row.grant_revision
        change = vault.rotate(row, SECRET_2, user)
        row.refresh_from_db()
        assert change.gateway_synced is True
        assert row.grant_revision > before
        assert _pushed(pushes)["revision"] == row.grant_revision
        through = _revisions(gateway, org)[-1]
        assert before <= through < row.grant_revision

    def test_a_failed_eviction_is_kept_and_retried(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        gateway.up = False
        change = vault.revoke(row, user)
        row.refresh_from_db()
        assert change.gateway_synced is False
        assert row.gateway_purge_pending is True
        # The org's next shared credential change retries it before it
        # reports the gateway in sync.
        gateway.up = True
        vault.create(org, "fagi.web", kind="bearer", value=SECRET_2, actor=user)
        vault.revoke(vault.active_shared(org.pk, "fagi.web"), user)
        row.refresh_from_db()
        assert row.gateway_purge_pending is False
        assert ("github", row.grant_revision) in [
            (call[1], call[3]) for call in gateway.calls if call[3]
        ]

    def test_a_removed_server_is_evicted_once_its_write_commits(
        self, user, pushes, gateway, django_capture_on_commit_callbacks
    ):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        gateway.calls.clear()
        with django_capture_on_commit_callbacks(execute=True):
            with transaction.atomic():
                vault.revoke_server_credentials(org, "github")
                # Nothing is revoked at the gateway before the write commits.
                assert gateway.calls == []
                assert AgentccMCPCredential.objects.get(pk=row.pk).gateway_purge_pending
        assert gateway.calls == [(str(org.pk), "github", None, row.grant_revision)]
        row.refresh_from_db()
        assert row.gateway_purge_pending is False

    def test_a_refused_write_revokes_nothing(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        row = _create(org, user).credential
        gateway.calls.clear()
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                vault.revoke_server_credentials(org, "github")
                raise RuntimeError("the config write was refused")
        assert gateway.calls == []
        row.refresh_from_db()
        assert row.status == "active"
        assert row.gateway_purge_pending is False

    def test_a_personal_eviction_names_no_revision(self, user, pushes, gateway):
        org = user.organization
        _seed(org)
        change = vault.create(
            org,
            "github",
            kind="bearer",
            value=SECRET,
            scope="user",
            user=user,
            actor=user,
        )
        vault.revoke(change.credential, user)
        assert gateway.calls[-1] == (str(org.pk), "github", str(user.pk), None)

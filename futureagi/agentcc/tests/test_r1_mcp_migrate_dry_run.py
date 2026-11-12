"""Bare ``agentcc_mcp_migrate_secrets`` is a dry run that
reports what ``--apply`` would do and writes nothing; ``--apply`` migrates;
``--check`` exits non-zero while anything is unmigrated (a plaintext MCP secret
in any config version, or an access default ``--apply`` would set) and zero
when clean. Config pushes are recorded from ``get_gateway_client``."""

import copy
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig

COMMAND = "agentcc_mcp_migrate_secrets"
TOKEN = "test-r1-dryrun-not-a-real-token"
URL = "https://mcp.example.com/mcp"


@pytest.fixture
def pushes():
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _state(org):
    """Everything the migration could write for org: its config versions (as
    stored, timestamps included) and its credential rows."""
    versions = [
        (
            row.pk,
            row.version,
            row.is_active,
            row.deleted,
            json.dumps(row.mcp),
            row.updated_at,
        )
        for row in AgentccOrgConfig._base_manager.filter(organization=org).order_by(
            "version"
        )
    ]
    credentials = list(
        AgentccMCPCredential.objects.filter(organization=org).values_list(
            "pk", "status", "version"
        )
    )
    return versions, credentials


@pytest.fixture
def legacy_org(user):
    """An org saved before the vault: a plaintext secret in the active version."""
    org = user.organization
    AgentccOrgConfig._base_manager.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={
            "servers": {
                "github": {
                    "url": URL,
                    "transport": "http",
                    "auth": {"type": "bearer", "token": TOKEN},
                }
            }
        },
    )
    return org


@pytest.mark.integration
class TestMigrateSecretsDryRunByDefault:
    def test_bare_run_reports_and_writes_nothing(self, legacy_org, pushes):
        before = _state(legacy_org)

        out, err = _run()

        assert _state(legacy_org) == before
        assert pushes == []
        assert "dry run" in out.lower(), out
        assert f"org {legacy_org.pk}:" in out, out
        assert "--apply" in out, out
        assert TOKEN not in out + err

    def test_apply_migrates_and_check_tracks_it(self, legacy_org, pushes):
        with pytest.raises(CommandError):
            _run("--check")

        _run("--apply")

        versions, credentials = _state(legacy_org)
        assert TOKEN not in json.dumps([v[4] for v in versions])
        assert [status for _, status, _ in credentials] == ["active"]
        assert len(pushes) == 1
        out, _ = _run("--check")
        assert "No plaintext MCP secrets in org configs." in out
        # Nothing left: a dry run now has nothing to report for the org.
        dry, _ = _run()
        assert f"org {legacy_org.pk}:" not in dry, dry

    def test_check_counts_a_pending_access_default(self, user, pushes):
        """An org that had servers before the vault and states no access
        default is unmigrated (--apply sets mcp.access.default=allow) even
        with no secret anywhere."""
        org = user.organization
        AgentccOrgConfig._base_manager.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={"servers": {"docs": {"url": URL}}},
        )
        cutoff = timezone.now()
        AgentccOrgConfig._base_manager.filter(organization=org).update(
            created_at=cutoff - timedelta(days=30)
        )

        with pytest.raises(CommandError) as raised:
            _run("--check", "--servers-before", cutoff.isoformat())
        assert "access" in str(raised.value), raised.value

        _run("--apply", "--servers-before", cutoff.isoformat())
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True
        )
        assert active.mcp["access"] == {"default": "allow"}
        out, _ = _run("--check", "--servers-before", cutoff.isoformat())
        assert "No plaintext MCP secrets in org configs." in out

    def test_apply_and_check_are_exclusive(self, legacy_org, pushes):
        before = _state(legacy_org)
        with pytest.raises(CommandError):
            _run("--apply", "--check")
        assert _state(legacy_org) == before

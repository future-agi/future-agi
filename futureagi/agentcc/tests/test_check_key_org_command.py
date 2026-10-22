"""manage.py agentcc_check_key_org reports API keys whose metadata names another
org or a reserved entry, and --fix binds each key to its own org."""

from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import Organization
from agentcc.models import AgentccAPIKey


def _run(*args):
    out = StringIO()
    call_command("agentcc_check_key_org", *args, stdout=out)
    return out.getvalue()


@pytest.mark.integration
class TestCheckKeyOrgCommand:
    @pytest.fixture
    def other_org(self, db):
        return Organization.objects.create(name="Check Key Org Other")

    @pytest.fixture
    def rebound(self, organization, workspace, other_org):
        return AgentccAPIKey.objects.create(
            gateway_key_id="key_rebound",
            name="rebound",
            organization=organization,
            workspace=workspace,
            metadata={"org_id": str(other_org.id), "env": "prod"},
        )

    @pytest.fixture
    def escalated(self, organization, workspace):
        return AgentccAPIKey.objects.create(
            gateway_key_id="key_escalated",
            name="escalated",
            organization=organization,
            workspace=workspace,
            metadata={
                "org_id": str(organization.id),
                "type": "internal",
                "access_groups": "internal",
                "team": "platform-admins",
            },
        )

    @pytest.fixture
    def clean(self, organization, workspace):
        return AgentccAPIKey.objects.create(
            gateway_key_id="key_clean",
            name="clean",
            organization=organization,
            workspace=workspace,
            metadata={"org_id": str(organization.id), "service": "infra"},
        )

    def test_dry_run_reports_and_changes_nothing(
        self, rebound, escalated, clean, other_org
    ):
        output = _run()

        assert "key_rebound" in output
        assert f"org_id {other_org.id}" in output
        assert "key_escalated" in output
        assert "access_groups, team, type" in output
        assert "key_clean" not in output
        assert "2 key(s) need repair" in output
        assert "--fix" in output
        for key in (rebound, escalated, clean):
            before = dict(key.metadata)
            key.refresh_from_db()
            assert key.metadata == before

    def test_fix_binds_keys_to_their_own_org(
        self, rebound, escalated, clean, organization
    ):
        output = _run("--fix")

        assert "Repaired 2 key(s)" in output
        rebound.refresh_from_db()
        assert rebound.metadata == {"org_id": str(organization.id), "env": "prod"}
        escalated.refresh_from_db()
        assert escalated.metadata == {"org_id": str(organization.id)}
        clean.refresh_from_db()
        assert clean.metadata == {"org_id": str(organization.id), "service": "infra"}
        assert _run().strip().endswith("No API key needs repair.")

    def test_reports_a_key_whose_creator_is_outside_its_org(
        self, organization, workspace, user, other_org
    ):
        # A key whose row and metadata both name an org its creator is not in:
        # the command reports the key by its creator and leaves its org as stored.
        moved = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="key_moved",
            name="moved",
            organization=other_org,
            user=user,
            metadata={"org_id": str(other_org.id)},
        )

        output = _run("--fix")

        assert "key_moved" in output
        assert "is outside the key's org" in output
        moved.refresh_from_db()
        assert moved.organization_id == other_org.id

    def test_reports_and_fixes_every_reserved_name(self, organization, workspace):
        # Names the gateway reserves for the key's own type and identity.
        spoofed = AgentccAPIKey.objects.create(
            gateway_key_id="key_spoofed_identity",
            name="spoofed",
            organization=organization,
            workspace=workspace,
            metadata={
                "org_id": str(organization.id),
                "id": "key_someone_else",
                "name": "someone-else",
                "owner": "someone",
                "type": "byok",
                "env": "prod",
            },
        )

        output = _run()

        assert "key_spoofed_identity" in output
        assert "reserved metadata id, name, owner, type" in output
        assert "1 key(s) need repair" in output
        spoofed.refresh_from_db()
        assert spoofed.metadata["name"] == "someone-else"

        assert "Repaired 1 key(s)" in _run("--fix")
        spoofed.refresh_from_db()
        assert spoofed.metadata == {"org_id": str(organization.id), "env": "prod"}

    def test_scopes_to_one_org(self, rebound, other_org):
        assert "key_rebound" not in _run("--org", str(other_org.id))
        assert "key_rebound" in _run("--org", str(rebound.organization_id))

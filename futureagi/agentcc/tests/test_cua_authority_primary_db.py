"""Real-primary H03 controls. Run only on the isolated Postgres test venue.

transaction=True is essential: the service refuses inherited transactions so
it cannot reuse a snapshot opened before the introspection request.
"""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.api_key import AgentccAPIKey
from agentcc.models.cap_activation import AgentccCapActivation, AgentccCapPolicyRevision
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import cua_authority, cua_authority_primary
from agentcc.tests.cua_authority_fixtures import ACTIVATION, expected, requirements, snapshot

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def authority_rows(organization, user, workspace):
    key = AgentccAPIKey._base_manager.create(
        organization=organization, user=user, workspace=workspace,
        gateway_key_id="h03-key", name="H03 synthetic personal key", status="active",
    )
    config = AgentccOrgConfig._base_manager.create(
        organization=organization, budgets=snapshot()["budgets"], routing={},
    )
    revision = AgentccCapPolicyRevision.objects.create(organization=organization, revision=12)
    activation = AgentccCapActivation.objects.create(
        organization=organization, level="key_id:h03-key", activated_at=ACTIVATION,
    )
    who = {"kind": "personal_key", "key_id": key.gateway_key_id,
           "org_id": str(organization.pk), "user_id": str(user.pk),
           "workspace_id": str(workspace.pk)}
    exp = expected()
    exp["policy_relation"]["org_id"] = str(organization.pk)
    return key, config, revision, activation, who, exp


def check(rows):
    return cua_authority.check_personal_authority(rows[4], rows[5], requirements())


def test_primary_positive_reads_real_rows(authority_rows):
    assert connection.vendor == "postgresql", "H03 proof requires Postgres, not SQLite"
    result = check(authority_rows)
    assert result["result"] == "allow"
    assert result["admission_comparison"]["result"] == "match"
    assert result["observed_policy"]["policy_revision"] == 12


@pytest.mark.parametrize("changed", [
    "key_revoked", "key_pending", "key_deleted", "key_expired", "user_inactive",
    "org_membership_inactive", "org_membership_deleted",
    "workspace_inactive", "workspace_deleted", "workspace_membership_inactive",
    "workspace_membership_deleted", "service_key",
])
def test_live_membership_revocation_controls(authority_rows, changed):
    key, _, _, _, who, _ = authority_rows
    if changed.startswith("key_"):
        fields = {"key_revoked": {"status": "revoked"}, "key_pending": {"status": "pending"},
                  "key_deleted": {"deleted": True},
                  "key_expired": {"expires_at": timezone.now() - timedelta(seconds=1)}}[changed]
        AgentccAPIKey._base_manager.filter(pk=key.pk).update(**fields)
    elif changed == "user_inactive":
        User._base_manager.filter(pk=who["user_id"]).update(is_active=False)
    elif changed.startswith("org_membership_"):
        OrganizationMembership._base_manager.filter(
            user_id=who["user_id"], organization_id=who["org_id"],
        ).update(**({"deleted": True} if changed.endswith("deleted") else {"is_active": False}))
    elif changed.startswith("workspace_membership_"):
        WorkspaceMembership._base_manager.filter(
            user_id=who["user_id"], workspace_id=who["workspace_id"],
        ).update(**({"deleted": True} if changed.endswith("deleted") else {"is_active": False}))
    elif changed.startswith("workspace_"):
        Workspace._base_manager.filter(pk=who["workspace_id"]).update(
            **({"deleted": True} if changed.endswith("deleted") else {"is_active": False}))
    else:
        AgentccEEAgentKey.objects.create(
            api_key=key, organization_id=who["org_id"], workspace_id=who["workspace_id"],
        )
    assert check(authority_rows) == {"result": "deny"}


@pytest.mark.parametrize("field", ["org_id", "user_id", "workspace_id"])
def test_cross_subject_denied(authority_rows, field):
    who = {**authority_rows[4], field: "00000000-0000-4000-8000-000000000009"}
    assert cua_authority.check_personal_authority(who, authority_rows[5], requirements()) == {"result": "deny"}


def test_unpinned_key_requires_concrete_live_workspace(authority_rows):
    key, _, _, _, who, _ = authority_rows
    # Use a separate unpinned row; never mutate protected ownership on a key.
    unpinned = AgentccAPIKey._base_manager.create(
        organization_id=who["org_id"], user_id=who["user_id"], workspace=None,
        gateway_key_id="h03-unpinned", name="Synthetic unpinned", status="active",
    )
    # Workspace autoassignment, if enabled in this venue, must not fake this case.
    unpinned.refresh_from_db()
    assert unpinned.workspace_id is None
    request = {**who, "key_id": unpinned.gateway_key_id}
    evidence = deepcopy(authority_rows[5])
    evidence["policy_relation"].update(key_id=unpinned.gateway_key_id, key_daily=None)
    evidence["applicability"]["key_daily"] = "not_applicable"
    evidence["activations"]["key_daily"] = {"state": "not_applicable"}
    assert cua_authority.check_personal_authority(request, evidence, requirements())["result"] == "allow"
    WorkspaceMembership._base_manager.filter(user_id=who["user_id"], workspace_id=who["workspace_id"]).update(is_active=False)
    assert cua_authority.check_personal_authority(request, evidence, requirements()) == {"result": "deny"}


def test_durable_vs_active_revision_mismatch_refuses(authority_rows):
    AgentccCapPolicyRevision.objects.filter(pk=authority_rows[2].pk).update(revision=13)
    assert check(authority_rows) == {"result": "unavailable"}


def test_activation_storage_vs_config_mismatch_refuses(authority_rows):
    AgentccCapActivation.objects.filter(pk=authority_rows[3].pk).update(activated_at=ACTIVATION + timedelta(seconds=1))
    assert check(authority_rows) == {"result": "unavailable"}


def test_missing_active_config_refuses(authority_rows):
    AgentccOrgConfig._base_manager.filter(pk=authority_rows[1].pk).update(is_active=False)
    assert check(authority_rows) == {"result": "unavailable"}


def test_existing_transaction_cannot_supply_old_snapshot(authority_rows):
    with transaction.atomic():
        assert check(authority_rows) == {"result": "unavailable"}


def test_revocation_between_checks_is_not_cached(authority_rows):
    assert check(authority_rows)["result"] == "allow"
    AgentccAPIKey._base_manager.filter(pk=authority_rows[0].pk).update(status="revoked")
    assert check(authority_rows) == {"result": "deny"}


def test_primary_queries_use_fresh_repeatable_read_snapshot(authority_rows):
    with connection.execute_wrapper(_record_sql := SQLRecorder()):
        assert check(authority_rows)["result"] == "allow"
    statements = _record_sql.statements
    isolation = next(i for i, sql in enumerate(statements) if "REPEATABLE READ" in sql)
    first_data = next(i for i, sql in enumerate(statements) if "agentcc_api_key" in sql.lower())
    assert isolation < first_data
    assert "READ ONLY" in statements[isolation]
    assert any("pg_is_in_recovery" in sql for sql in statements)
    assert not any(word in " ".join(statements).upper() for word in ("INSERT INTO", "UPDATE ", "DELETE FROM"))


class SQLRecorder:
    def __init__(self):
        self.statements = []

    def __call__(self, execute, sql, params, many, context):
        self.statements.append(sql)  # No params, no records, no credentials.
        return execute(sql, params, many, context)

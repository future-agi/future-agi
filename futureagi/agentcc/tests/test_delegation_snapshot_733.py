"""The delegation feed enumerates membership, never the positive key list."""

import json
import secrets
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.db import OperationalError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import AgentccAPIKey
from agentcc.serializers.contracts import APIKeyBulkResponseSerializer
from agentcc.tests.test_retirement_733 import bulk

pytestmark = pytest.mark.django_db
READER = "agentcc.services.delegation_snapshot.revoked_batch_grants"


@pytest.fixture
def delegation_bulk_contract():
    path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts/delegation/v1/bulk_snapshot.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def member(org, user, workspace="-"):
    return {
        "org_id": str(org.pk),
        "user_id": str(user.pk),
        "workspace_id": str(workspace),
    }


def read_snapshot():
    response = bulk()
    assert response.status_code == 200, response.data
    serializer = APIKeyBulkResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
    return response.json()["delegation"]


def test_733_bulk_serializer_round_trips_shared_contract(delegation_bulk_contract):
    serializer = APIKeyBulkResponseSerializer(data=delegation_bulk_contract)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == delegation_bulk_contract


@pytest.mark.parametrize("field", ["org_id", "user_id", "workspace_id"])
def test_733_bulk_serializer_requires_contract_member_tags(
    delegation_bulk_contract, field
):
    row = delegation_bulk_contract["delegation"]["members"][0]
    row[field.removesuffix("_id")] = row.pop(field)
    serializer = APIKeyBulkResponseSerializer(data=delegation_bulk_contract)
    assert not serializer.is_valid()
    assert field in serializer.errors["delegation"]["members"][0]


@pytest.mark.parametrize("case", ["zero_key", "removed_member", "revoked_grant"])
def test_733_bulk_delegation_matches_shared_contract(
    organization, user, workspace, delegation_bulk_contract, case
):
    # Adapt only identities and lifecycle state, not the shared fixture's tags.
    # The reader is stubbed until L1b is composed; its real DB gate is below.
    expected = delegation_bulk_contract["delegation"]
    org_row, workspace_row = expected["members"]
    identities = {
        org_row["org_id"]: str(organization.pk),
        org_row["user_id"]: str(user.pk),
        workspace_row["workspace_id"]: str(workspace.pk),
    }
    expected["members"] = [
        {key: identities.get(value, value) for key, value in row.items()}
        for row in expected["members"]
    ]
    now = parse_datetime(expected["fetched_at"])
    expected["fetched_at"] = now.isoformat()
    if case != "revoked_grant":
        expected["revoked_grants"] = []

    with (
        patch(READER, return_value=expected["revoked_grants"]),
        patch("agentcc.services.delegation_snapshot.timezone.now", return_value=now),
    ):
        assert read_snapshot() == expected
        if case == "removed_member":
            OrganizationMembership._base_manager.filter(
                user=user, organization=organization
            ).update(is_active=False)
            expected["members"] = []
            assert read_snapshot() == expected

    assert not AgentccAPIKey._base_manager.exists()
    assert not OrgApiKey._base_manager.filter(user=user).exists()


def test_733_delegation_includes_zero_key_member_and_live_workspaces(
    organization, user, workspace
):
    with patch(READER, return_value=[]):
        before = timezone.now()
        snapshot = read_snapshot()
    assert snapshot["complete"] is True
    assert before <= parse_datetime(snapshot["fetched_at"]) <= timezone.now()
    assert member(organization, user) in snapshot["members"]
    assert member(organization, user, workspace.pk) in snapshot["members"]
    assert snapshot["revoked_grants"] == []
    assert not AgentccAPIKey._base_manager.exists()
    assert not OrgApiKey._base_manager.filter(user=user).exists()


def test_733_snapshot_is_all_orgs_not_current_workspace(organization, user, workspace):
    other_org = Organization.objects.create(name="Another delegation org")
    other_user = User.objects.create(
        email="delegation-" + uuid.uuid4().hex + "@example.invalid",
        organization=other_org,
    )
    OrganizationMembership._base_manager.get_or_create(
        organization=other_org,
        user=other_user,
        defaults={"is_active": True},
    )
    other_workspace = Workspace._base_manager.create(
        name="other", organization=other_org, created_by=other_user
    )
    WorkspaceMembership._base_manager.create(workspace=other_workspace, user=other_user)
    with patch(READER, return_value=[]):
        snapshot = read_snapshot()
    assert member(organization, user) in snapshot["members"]
    assert member(other_org, other_user) in snapshot["members"]
    assert member(other_org, other_user, other_workspace.pk) in snapshot["members"]
    assert member(organization, other_user, workspace.pk) not in snapshot["members"]


@pytest.mark.parametrize(
    "removed", ["inactive_membership", "deleted_membership", "inactive_user"]
)
def test_733_removed_person_disappears_even_without_a_key(
    organization, user, workspace, removed
):
    with patch(READER, return_value=[]):
        assert member(organization, user) in read_snapshot()["members"]
        if removed == "inactive_user":
            User._base_manager.filter(pk=user.pk).update(is_active=False)
        else:
            OrganizationMembership._base_manager.filter(
                user=user, organization=organization
            ).update(
                **(
                    {"is_active": False}
                    if removed == "inactive_membership"
                    else {"deleted": True}
                )
            )
        snapshot = read_snapshot()
    assert snapshot["complete"] is True
    assert not any(row["user_id"] == str(user.pk) for row in snapshot["members"])
    assert not AgentccAPIKey._base_manager.exists()


@pytest.mark.parametrize(
    "removed",
    [
        "membership_inactive",
        "membership_deleted",
        "workspace_inactive",
        "workspace_deleted",
    ],
)
def test_733_workspace_removal_keeps_only_org_membership(
    organization, user, workspace, removed
):
    model = WorkspaceMembership if removed.startswith("membership") else Workspace
    rows = model._base_manager.filter(
        **(
            {"workspace": workspace}
            if model is WorkspaceMembership
            else {"pk": workspace.pk}
        )
    )
    rows.update(
        **({"deleted": True} if removed.endswith("deleted") else {"is_active": False})
    )
    with patch(READER, return_value=[]):
        snapshot = read_snapshot()
    assert member(organization, user) in snapshot["members"]
    assert member(organization, user, workspace.pk) not in snapshot["members"]


def test_733_cross_org_workspace_row_does_not_grant_access(organization, user):
    other_org = Organization.objects.create(name="Not a member")
    other_workspace = Workspace._base_manager.create(
        name="foreign", organization=other_org, created_by=user
    )
    WorkspaceMembership._base_manager.create(workspace=other_workspace, user=user)
    with patch(READER, return_value=[]):
        snapshot = read_snapshot()
    assert member(other_org, user, other_workspace.pk) not in snapshot["members"]


def test_733_missing_registry_cannot_claim_complete_empty_revocations(
    organization, user
):
    with patch(
        "agentcc.services.delegation_snapshot.apps.get_model", side_effect=LookupError
    ):
        snapshot = read_snapshot()
    assert snapshot["complete"] is False
    assert snapshot["members"] == []
    assert snapshot["revoked_grants"] == []


def test_733_registry_read_failure_never_returns_a_complete_snapshot(
    organization, user
):
    with patch(READER, side_effect=OperationalError("test registry unavailable")):
        response = bulk()
    assert response.status_code == 500
    assert "delegation" not in response.data


def test_733_revoked_grants_reader_uses_l1b_fields_and_primary():
    from agentcc.services.delegation_snapshot import revoked_batch_grants

    model = MagicMock()
    query = model._base_manager.using.return_value
    now = timezone.now()
    jti = secrets.token_hex(16)
    query.filter.return_value.order_by.return_value.values_list.return_value = [jti]
    with patch(
        "agentcc.services.delegation_snapshot.apps.get_model", return_value=model
    ) as lookup:
        assert revoked_batch_grants(now) == [jti]
    lookup.assert_called_once_with("accounts", "ServiceGrant")
    model._base_manager.using.assert_called_once_with("default")
    query.filter.assert_called_once_with(
        kind="batch",
        aud="fa-eval-grant",
        revoked_at__isnull=False,
        exp__gte=int(now.timestamp()),
    )


def test_733_membership_snapshot_bypasses_replica_routing(
    organization, user, workspace
):
    from agentcc.services.delegation_snapshot import delegation_snapshot

    # If any membership query is left to a read-replica router this fails, not
    # merely on an assertion of a decorator or a mocked queryset's field names.
    with (
        patch(READER, return_value=[]),
        patch(
            "django.db.router.db_for_read", side_effect=AssertionError("unbound read")
        ),
    ):
        snapshot = delegation_snapshot()
    assert member(organization, user) in snapshot["members"]
    assert member(organization, user, workspace.pk) in snapshot["members"]


def test_733_real_l1b_registry_only_unexpired_revoked_batch_grants(organization, user):
    # This is a gate on the real code, not a substitute model.
    try:
        model = apps.get_model("accounts", "ServiceGrant")
    except LookupError:
        pytest.skip("OWED: compose L1b accounts.ServiceGrant and migration 0032")
    now = timezone.now()
    expiry = int(now.timestamp())
    expected = []
    for kind, aud, revoked, exp in [
        ("batch", "fa-eval-grant", True, expiry + 60),
        ("batch", "fa-eval-grant", True, expiry),
        ("batch", "fa-eval-grant", True, expiry - 1),
        ("batch", "fa-eval-grant", False, expiry + 60),
        ("batch", "collector-ingest", True, expiry + 60),
        ("sandbox", "collector-ingest", True, expiry + 60),
    ]:
        jti = secrets.token_hex(16)
        model._base_manager.create(
            jti=jti,
            kind=kind,
            kid="k1",
            aud=aud,
            org=organization.pk,
            workspace=None,
            user=user.pk,
            via="person",
            sub="batch:" + str(uuid.uuid4()),
            scope="eval",
            iat=expiry - 60,
            exp=exp,
            revoked_at=now if revoked else None,
        )
        if kind == "batch" and aud == "fa-eval-grant" and revoked and exp >= expiry:
            expected.append(jti)
    with patch("agentcc.services.delegation_snapshot.timezone.now", return_value=now):
        snapshot = read_snapshot()
    assert snapshot["complete"] is True
    assert snapshot["revoked_grants"] == sorted(expected)
    assert member(organization, user) in snapshot["members"]

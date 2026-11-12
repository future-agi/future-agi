"""Independent-connection races and retained-identity lifecycle regressions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, transaction

from accounts.api_key_policy import admit_api_key
from accounts.authentication import decode_token
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import test_offboarding_cross_org as support
from accounts.tests.test_offboarding_cross_org import (
    ENDPOINTS,
    assert_removed,
    assert_success,
    credentials,
    remove_member,
    set_actor_level,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.tenant_scope import acts_in_organization

# Share fixture definitions without duplicating their setup in this module.
local_only = support.local_only
target = support.target
other_org_data = support.other_org_data


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("endpoint", ENDPOINTS + ["member_remove", "deactivate"])
@pytest.mark.parametrize("change", ["create", "reactivate", "remove"])
def test_foreign_membership_commit_during_offboarding_keeps_identity_usable(
    auth_client,
    target,
    organization,
    other_org_data,
    change,
    endpoint,
):
    if connection.vendor != "postgresql":
        pytest.skip("Requires independent PostgreSQL transactions")
    from accounts.services.personal_key_lifecycle import revoke_personal_keys

    membership, ws, _, memory, dataset = other_org_data
    if change == "create":
        OrganizationMembership._base_manager.filter(pk=membership.pk).delete()
    elif change == "reactivate":
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False
        )
    paused, resume = Event(), Event()

    def scoped_revoke(user_id, organization_id=None, **kwargs):
        assert organization_id == organization.pk  # No global credential revocation.
        paused.set()
        assert resume.wait(10), "Concurrent membership transaction did not complete"
        return revoke_personal_keys(user_id, organization_id, **kwargs)

    def offboard():
        close_old_connections()
        try:
            return remove_member(auth_client, endpoint, target.pk)
        finally:
            close_old_connections()

    with patch(
        "accounts.services.offboarding.revoke_personal_keys", side_effect=scoped_revoke
    ):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(offboard)
            try:
                assert paused.wait(10), "Offboarding did not reach scoped revocation"
                with transaction.atomic():
                    if change == "create":
                        membership = OrganizationMembership.no_workspace_objects.create(
                            user=target, organization=ws.organization
                        )
                    else:
                        OrganizationMembership._base_manager.filter(
                            pk=membership.pk
                        ).update(is_active=change == "reactivate")
                    if change != "remove":
                        WorkspaceMembership._base_manager.update_or_create(
                            user=target,
                            workspace=ws,
                            defaults={
                                "organization_membership": membership,
                                "is_active": True,
                                "deleted": False,
                            },
                        )
                        # Commit real O2 grants/credentials before releasing O1.
                        key = OrgApiKey._base_manager.create(
                            user=target, organization=ws.organization, workspace=ws
                        )
                        tokens = credentials(target, ws.organization)
            finally:
                resume.set()
            assert_success(result.result(timeout=10), endpoint, target.pk)
    assert_removed(target, organization)
    for row in (ws, memory, dataset):
        row.refresh_from_db()
        assert not row.deleted
    if change != "remove":
        assert acts_in_organization(target, ws.organization)
        key.refresh_from_db()
        assert key.enabled and admit_api_key(key).pk == key.pk
        assert decode_token(tokens[0][1])[0].is_active
        for token, _ in tokens:
            token.refresh_from_db()
            assert token.is_active


@pytest.mark.django_db
def test_retained_former_owner_does_not_defeat_last_owner_guard(
    auth_client,
    user,
    organization,
    target,
):
    set_actor_level(user, organization, Level.ADMIN)
    target.organization_role = OrganizationRoles.OWNER
    target.save(update_fields=["organization_role"])
    OrganizationMembership._base_manager.filter(
        user=target, organization=organization
    ).update(role=OrganizationRoles.OWNER, level=Level.OWNER)
    former_owner = User.objects.create_user(
        email="former-owner@futureagi.com",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership._base_manager.create(
        user=former_owner,
        organization=organization,
        is_active=False,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
    )
    response = remove_member(auth_client, "legacy", target.pk)
    assert response.status_code == 400, response.data
    assert "Cannot delete the last owner" in str(response.data)
    assert acts_in_organization(target, organization)

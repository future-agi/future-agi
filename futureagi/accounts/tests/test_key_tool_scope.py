"""Key tools use membership in the selected organization."""

import pytest

from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.key_scope_helpers import ROLES, make_key
from ai_tools.base import ToolContext
from ai_tools.tools.users.create_api_key import CreateApiKeyInput, CreateApiKeyTool
from ai_tools.tools.users.list_api_keys import ListApiKeysInput, ListApiKeysTool
from ai_tools.tools.users.revoke_api_key import RevokeApiKeyInput, RevokeApiKeyTool
from tfc.constants.levels import Level

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.mark.parametrize("level", ROLES)
@pytest.mark.parametrize("action", ["create", "list", "revoke"])
def test_key_tools_use_current_org_membership(key_scope, level, action):
    home, current = key_scope
    user = home.users[Level.OWNER]
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=current.org, role="Owner", level=level
    )
    context = ToolContext(
        user=user, organization=current.org, workspace=current.workspace
    )
    if action == "create":
        result = CreateApiKeyTool().execute(
            CreateApiKeyInput(key_type="system"), context
        )
        assert result.is_error is (level < Level.ADMIN)
        keys = OrgApiKey.no_workspace_objects.filter(
            organization=current.org, type="system"
        )
        assert keys.exists() is (level >= Level.ADMIN)
    else:
        key = make_key(current.org, key_type="system")
        if action == "list":
            result = ListApiKeysTool().execute(
                ListApiKeysInput(key_type="system"), context
            )
            ids = {row["id"] for row in result.data["api_keys"]}
            assert (str(key.pk) in ids) is (level >= Level.ADMIN)
        else:
            result = RevokeApiKeyTool().execute(
                RevokeApiKeyInput(key_id=key.pk), context
            )
            assert result.is_error is (level < Level.ADMIN)
            key.refresh_from_db()
            assert key.enabled is (level < Level.ADMIN)


@pytest.mark.parametrize("state", ["inactive", "deleted", "missing"])
@pytest.mark.parametrize("action", ["create", "list", "revoke"])
def test_key_tools_do_not_fall_back_to_legacy_owner(key_scope, state, action):
    scope = key_scope[0]
    user = scope.users[Level.OWNER]
    membership = user.get_membership(scope.org)
    if state == "missing":
        membership.delete()
    else:
        OrganizationMembership.no_workspace_objects.filter(pk=membership.pk).update(
            **({"is_active": False} if state == "inactive" else {"deleted": True})
        )
    context = ToolContext(user=user, organization=scope.org, workspace=scope.workspace)
    if action == "create":
        result = CreateApiKeyTool().execute(
            CreateApiKeyInput(key_type="system"), context
        )
        assert result.is_error
        assert not OrgApiKey.no_workspace_objects.filter(
            organization=scope.org
        ).exists()
    else:
        key = make_key(scope.org, key_type="system")
        if action == "list":
            result = ListApiKeysTool().execute(ListApiKeysInput(), context)
            assert result.data["api_keys"] == []
        else:
            result = RevokeApiKeyTool().execute(
                RevokeApiKeyInput(key_id=key.pk), context
            )
            assert result.is_error
            key.refresh_from_db()
            assert key.enabled


def test_current_admin_can_create_even_when_legacy_role_is_member(key_scope):
    home, current = key_scope
    user = home.users[Level.MEMBER]
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=current.org, role="Member", level=Level.ADMIN
    )
    context = ToolContext(
        user=user, organization=current.org, workspace=current.workspace
    )
    result = CreateApiKeyTool().execute(CreateApiKeyInput(key_type="system"), context)
    assert not result.is_error
    assert (
        OrgApiKey.no_workspace_objects.get(pk=result.data["key_id"]).organization_id
        == current.org.pk
    )


def test_tool_cannot_revoke_foreign_or_ingest_key(key_scope):
    own, foreign = key_scope
    context = ToolContext(
        user=own.users[Level.OWNER], organization=own.org, workspace=own.workspace
    )
    for key in (
        make_key(foreign.org, key_type="system"),
        make_key(own.org, key_type="ingest"),
    ):
        result = RevokeApiKeyTool().execute(RevokeApiKeyInput(key_id=key.pk), context)
        assert result.is_error
        key.refresh_from_db()
        assert key.enabled

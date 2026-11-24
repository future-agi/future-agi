"""Every caller of the role service must authorize the workspace being granted."""

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.member_role_service import (
    MemberRoleUpdateError,
    update_member_role,
)
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def state():
    clear_workspace_context()
    org = Organization.objects.create(name="Synthetic grant tenant")
    foreign = Organization.objects.create(name="Other synthetic tenant")
    actor = User.objects.create_user(email="grant-actor@example.test", organization=org)
    target = User.objects.create_user(
        email="grant-target@example.test", organization=org
    )
    am = OrganizationMembership.no_workspace_objects.create(
        user=actor,
        organization=org,
        level=Level.MEMBER,
        role="Member",
        is_active=True,
    )
    tm = OrganizationMembership.no_workspace_objects.create(
        user=target,
        organization=org,
        level=Level.VIEWER,
        role="Viewer",
        is_active=True,
    )
    a, b, other = [
        Workspace.no_workspace_objects.create(
            name=name,
            organization=tenant,
            created_by=actor,
            is_active=True,
        )
        for name, tenant in [("A", org), ("B", org), ("Foreign", foreign)]
    ]
    aw = WorkspaceMembership.no_workspace_objects.create(
        user=actor,
        workspace=a,
        organization_membership=am,
        role="workspace_admin",
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
    )
    set_workspace_context(workspace=a, organization=org)
    yield org, actor, target, am, tm, a, b, other, aw
    clear_workspace_context()


def mutate(state, ws, path, level=Level.WORKSPACE_ADMIN):
    org, actor, target, *_ = state
    kwargs = (
        {"ws_level": level, "workspace_id": ws.pk}
        if path == "direct"
        else {
            "org_level": Level.VIEWER,
            "workspace_access": [{"workspace_id": ws.pk, "level": level}],
            "workspace_access_provided": True,
        }
    )
    return update_member_role(
        organization=org, actor=actor, target_user_id=target.pk, **kwargs
    )


def snapshot(state):
    _, _, target, _, tm, *_ = state
    tm.refresh_from_db()
    return tm.level, list(
        WorkspaceMembership._base_manager.filter(user=target).order_by("pk").values()
    )


@pytest.mark.parametrize("path", ["direct", "access"])
@pytest.mark.parametrize(
    "scope",
    [
        "sibling",
        "foreign",
        "disabled",
        "revoked_org",
        "revoked_ws",
        "deleted_org",
        "peer_ws_admin",
    ],
)
def test_denied_grants_are_atomic(state, path, scope):
    org, actor, target, am, tm, a, b, foreign, aw = state
    ws = a
    if scope == "sibling":
        ws = b
    elif scope == "foreign":
        ws = foreign
    elif scope == "disabled":
        Workspace.no_workspace_objects.filter(pk=a.pk).update(is_active=False)
    elif scope == "revoked_org":
        OrganizationMembership.no_workspace_objects.filter(pk=am.pk).update(
            is_active=False
        )
    elif scope == "deleted_org":
        OrganizationMembership.no_workspace_objects.filter(pk=am.pk).update(
            deleted=True
        )
    elif scope == "revoked_ws":
        WorkspaceMembership.no_workspace_objects.filter(pk=aw.pk).update(
            is_active=False
        )
    else:
        WorkspaceMembership.no_workspace_objects.create(
            workspace=a,
            user=target,
            organization_membership=tm,
            role="workspace_admin",
            level=Level.WORKSPACE_ADMIN,
            is_active=True,
        )
    before = snapshot(state)
    with pytest.raises(MemberRoleUpdateError):
        # Use an otherwise allowed grant so each scope gate remains exercised.
        mutate(state, ws, path, Level.WORKSPACE_MEMBER)
    assert snapshot(state) == before


@pytest.mark.parametrize("path", ["direct", "access"])
@pytest.mark.parametrize(
    "target_level", [None, Level.WORKSPACE_VIEWER, Level.WORKSPACE_MEMBER]
)
@pytest.mark.parametrize("legacy_null", [False, True])
def test_workspace_admin_cannot_grant_own_level(state, path, target_level, legacy_null):
    _, _, target, _, tm, own, _, _, aw = state
    if legacy_null:
        aw.organization_membership = None
        aw.level = None
        aw.save(update_fields=["organization_membership", "level"])
    if target_level is not None:
        WorkspaceMembership.no_workspace_objects.create(
            user=target,
            workspace=own,
            organization_membership=tm,
            role=Level.to_ws_role(target_level),
            level=target_level,
            is_active=True,
        )
    before = snapshot(state)
    with pytest.raises(MemberRoleUpdateError) as exc:
        mutate(state, own, path, Level.WORKSPACE_ADMIN)
    assert exc.value.code == "WS_ROLE_ASSIGN_FORBIDDEN"
    assert exc.value.status_code == 403
    assert snapshot(state) == before


@pytest.mark.parametrize("path", ["direct", "access"])
@pytest.mark.parametrize("level", [Level.WORKSPACE_VIEWER, Level.WORKSPACE_MEMBER])
@pytest.mark.parametrize("legacy_null", [False, True])
def test_authorized_workspace_admin_can_grant_owned_workspace(
    state, path, level, legacy_null
):
    if legacy_null:
        aw = state[8]
        aw.organization_membership = None
        aw.level = None
        aw.save(update_fields=["organization_membership", "level"])
    mutate(state, state[5], path, level)
    row = WorkspaceMembership.no_workspace_objects.get(
        user=state[2], workspace=state[5]
    )
    assert row.level == level and row.is_active


@pytest.mark.parametrize("path", ["direct", "access"])
def test_workspace_admin_can_downgrade_lower_member(state, path):
    _, _, target, _, tm, own, *_ = state
    row = WorkspaceMembership.no_workspace_objects.create(
        user=target,
        workspace=own,
        organization_membership=tm,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    mutate(state, own, path, Level.WORKSPACE_VIEWER)
    row.refresh_from_db()
    assert row.level == Level.WORKSPACE_VIEWER
    assert row.role == "workspace_viewer"
    assert row.is_active and not row.deleted


@pytest.mark.parametrize("path", ["direct", "access"])
@pytest.mark.parametrize("actor_level", [Level.ADMIN, Level.OWNER])
def test_org_admin_or_owner_can_grant_sibling_workspace(state, path, actor_level):
    am = state[3]
    am.level = actor_level
    am.role = Level.to_org_string(actor_level)
    am.save(update_fields=["level", "role"])
    mutate(state, state[6], path)
    row = WorkspaceMembership.no_workspace_objects.get(
        user=state[2], workspace=state[6]
    )
    assert row.level == Level.WORKSPACE_ADMIN


@pytest.mark.parametrize("path", ["direct", "access"])
@pytest.mark.parametrize("level", [True, 15, 99, "8"])
def test_invalid_workspace_levels_are_rejected(state, path, level):
    before = snapshot(state)
    with pytest.raises(MemberRoleUpdateError):
        mutate(state, state[5], path, level)
    assert snapshot(state) == before

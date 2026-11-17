"""A personal API key with no workspace works with MCP where its person can work.

Each call checks the key's own person and organization live, then uses the
organization's default workspace when that person can open it, and otherwise
the person's own landing workspace. With no such workspace the call is refused.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.api_key_policy import APIKeyRetired
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from mcp_server import mcp_app
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db


def _member(organization, role, level):
    """A person whose only link to ``organization`` is one active membership."""
    person = User.objects.create_user(
        email=f"personal-key-{uuid4().hex}@example.test",
        password="test-password",
        name=str(role),
        organization=organization,
        organization_role=role,
        config={},
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person,
        organization=organization,
        role=role,
        level=level,
        is_active=True,
    )
    return person, membership


@pytest.fixture
def world(db):
    """One organization with an Owner, a Member, its default and one more workspace."""
    clear_workspace_context()
    organization = Organization.objects.create(name="Personal key organization")
    owner, owner_membership = _member(
        organization, OrganizationRoles.OWNER, Level.OWNER
    )
    member, member_membership = _member(
        organization, OrganizationRoles.MEMBER, Level.MEMBER
    )
    default = Workspace.objects.create(
        name="Default",
        organization=organization,
        created_by=owner,
        is_default=True,
    )
    other = Workspace.objects.create(
        name="Other", organization=organization, created_by=owner
    )
    # The Member belongs to the other workspace only, not to the default one.
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=other,
        organization_membership=member_membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    return SimpleNamespace(
        organization=organization,
        owner=owner,
        owner_membership=owner_membership,
        member=member,
        member_membership=member_membership,
        default=default,
        other=other,
    )


def _personal_key(person, organization):
    key = OrgApiKey.no_workspace_objects.create(
        user=person, organization=organization, type="user", name="No workspace"
    )
    assert key.workspace_id is None
    return key


def _authenticate(key):
    """One call's authentication; the scope it binds is cleared afterwards."""
    mcp_app._clear_context()
    try:
        context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        if context is not None:
            assert mcp_app._current_context() is context
            assert get_current_workspace().pk == context.workspace.pk
        return context
    finally:
        mcp_app._clear_context()


def _assert_refused(key, monkeypatch):
    """The usual refusal: nothing is bound and one value-free warning is logged."""
    captured_logger = Mock()
    bind_context = Mock(wraps=mcp_app._bind_context)
    monkeypatch.setattr(mcp_app, "logger", captured_logger)
    monkeypatch.setattr(mcp_app, "_bind_context", bind_context)
    mcp_app._clear_context()
    try:
        assert (
            mcp_app._authenticate_and_set_context(key.api_key, key.secret_key) is None
        )
        bind_context.assert_not_called()
        assert mcp_app._current_context() is None
        assert get_current_user() is None
        assert get_current_organization() is None
        assert get_current_workspace() is None
        captured_logger.warning.assert_called_once_with("mcp_auth_failed")
        assert len(captured_logger.mock_calls) == 1
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("saved_landing", [False, True])
def test_owner_key_uses_the_default_workspace(world, saved_landing):
    """An Owner's key lands on the default workspace of the key's organization."""
    if saved_landing:
        # The Owner saved the other workspace as their landing; the default wins.
        User.objects.filter(pk=world.owner.pk).update(
            config={"currentWorkspaceId": str(world.other.pk)}
        )
    key = _personal_key(world.owner, world.organization)
    context = _authenticate(key)
    assert context is not None
    assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
        world.owner.pk,
        world.organization.pk,
        world.default.pk,
    )
    assert context.api_key.pk == key.pk
    # Landing is a read: the Owner gets no workspace membership from it.
    assert not WorkspaceMembership._base_manager.filter(user=world.owner).exists()


def test_member_without_default_access_uses_their_landing_workspace(world):
    """A Member who cannot open the default lands on their own landing workspace."""
    # Saving the default as a choice does not open it for them.
    default_id = str(world.default.pk)
    User.objects.filter(pk=world.member.pk).update(
        config={
            "orgWorkspaceMap": {str(world.organization.pk): default_id},
            "currentWorkspaceId": default_id,
            "defaultWorkspaceId": default_id,
        }
    )
    assert not world.member.can_access_workspace(world.default)
    key = _personal_key(world.member, world.organization)
    context = _authenticate(key)
    assert context is not None
    assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
        world.member.pk,
        world.organization.pk,
        world.other.pk,
    )
    assert context.api_key.pk == key.pk


def test_person_with_no_workspace_to_open_is_refused(world, monkeypatch):
    """A Member who can open no workspace in the key's organization is refused."""
    WorkspaceMembership.no_workspace_objects.filter(user=world.member).update(
        is_active=False
    )
    assert not world.member.can_access_workspace(world.default)
    assert not world.member.can_access_workspace(world.other)
    _assert_refused(_personal_key(world.member, world.organization), monkeypatch)


@pytest.mark.parametrize(
    "state", ["inactive_person", "deactivated_membership", "removed_membership"]
)
def test_inactive_person_or_ended_membership_is_refused(world, monkeypatch, state):
    """The key's person must be active and an active member on every call."""
    key = _personal_key(world.owner, world.organization)
    # The same key works first, so the refusal below comes from the change.
    assert _authenticate(key) is not None
    memberships = OrganizationMembership._base_manager.filter(
        pk=world.owner_membership.pk
    )
    # Changed in place, so the live check on the call refuses, not key cleanup.
    if state == "inactive_person":
        User._base_manager.filter(pk=world.owner.pk).update(is_active=False)
    elif state == "deactivated_membership":
        memberships.update(is_active=False)
    else:
        ended_at = timezone.now()
        memberships.update(
            is_active=False, deleted=True, deleted_at=ended_at, removed_at=ended_at
        )
    stored = OrgApiKey._base_manager.filter(pk=key.pk)
    assert stored.filter(enabled=True, deleted=False).exists()
    _assert_refused(key, monkeypatch)


@pytest.mark.parametrize("ambient", [False, True])
def test_current_choice_of_another_organization_does_not_move_the_key(world, ambient):
    """The key stays in its own organization whatever the app has selected."""
    second = Organization.objects.create(name="Second organization")
    OrganizationMembership.no_workspace_objects.create(
        user=world.owner,
        organization=second,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    second_default = Workspace.objects.create(
        name="Second default",
        organization=second,
        created_by=world.owner,
        is_default=True,
    )
    second_id, second_default_id = str(second.pk), str(second_default.pk)
    User.objects.filter(pk=world.owner.pk).update(
        config={
            "currentOrganizationId": second_id,
            "orgWorkspaceMap": {second_id: second_default_id},
            "currentWorkspaceId": second_default_id,
            "defaultWorkspaceId": second_default_id,
        }
    )
    key = _personal_key(world.owner, world.organization)
    mcp_app._clear_context()
    try:
        if ambient:
            set_workspace_context(
                user=world.owner, organization=second, workspace=second_default
            )
        context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            world.owner.pk,
            world.organization.pk,
            world.default.pk,
        )
        assert get_current_organization().pk == world.organization.pk
        assert get_current_workspace().pk == world.default.pk
    finally:
        mcp_app._clear_context()


def test_access_is_checked_on_every_call(world, monkeypatch):
    """Access removed between two calls decides the very next call."""
    default_access = WorkspaceMembership.no_workspace_objects.create(
        user=world.member,
        workspace=world.default,
        organization_membership=world.member_membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    key = _personal_key(world.member, world.organization)
    first = _authenticate(key)
    assert first is not None
    assert (first.organization.pk, first.workspace.pk) == (
        world.organization.pk,
        world.default.pk,
    )

    # Access to the default ends: the next call uses the person's landing.
    WorkspaceMembership.no_workspace_objects.filter(pk=default_access.pk).update(
        is_active=False
    )
    second = _authenticate(key)
    assert second is not None
    assert (second.organization.pk, second.workspace.pk) == (
        world.organization.pk,
        world.other.pk,
    )

    # No workspace is left to open: the next call is refused.
    WorkspaceMembership.no_workspace_objects.filter(user=world.member).update(
        is_active=False
    )
    _assert_refused(key, monkeypatch)


@pytest.mark.parametrize("retired_type", ["mcp", "system"])
def test_retired_key_types_keep_the_retirement_answer(world, monkeypatch, retired_type):
    """Stored keys of the retired types get the retirement answer, never a scope."""
    key = OrgApiKey.no_workspace_objects.create(
        user=world.owner,
        organization=world.organization,
        type=retired_type,
        name="Retired type",
    )
    assert key.workspace_id is None
    bind_context = Mock(wraps=mcp_app._bind_context)
    monkeypatch.setattr(mcp_app, "_bind_context", bind_context)
    mcp_app._clear_context()
    try:
        with pytest.raises(APIKeyRetired) as retired:
            mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        assert retired.value.get_codes() == "api_key_retired"
        # Without its secret the same key gets the usual refusal instead.
        assert mcp_app._authenticate_and_set_context(key.api_key, uuid4().hex) is None
        bind_context.assert_not_called()
        assert mcp_app._current_context() is None
        assert get_current_workspace() is None
    finally:
        mcp_app._clear_context()

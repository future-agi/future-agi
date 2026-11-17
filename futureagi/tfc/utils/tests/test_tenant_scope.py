import uuid
from types import SimpleNamespace

import pytest
from django.db.models import Q
from django.db.models.query import EmptyQuerySet
from rest_framework import serializers

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.tenant_scope import (
    acts_in_organization,
    align_legacy_organization,
    caller_organization,
    caller_workspace,
    has_membership_rows,
    scope_to_caller,
    set_related_queryset,
    user_organization,
    workspace_scope_q,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


class _Serializer(serializers.Serializer):
    one = serializers.PrimaryKeyRelatedField(
        queryset=Project.no_workspace_objects.none()
    )
    many = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.no_workspace_objects.none(), many=True
    )


@pytest.fixture(autouse=True)
def _no_bound_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.mark.unit
def test_set_related_queryset_reaches_the_child_of_a_many_field():
    serializer = _Serializer()
    single = Project.no_workspace_objects.filter(name="single")
    several = CustomEvalConfig.no_workspace_objects.filter(name="several")

    set_related_queryset(serializer.fields["one"], single)
    set_related_queryset(serializer.fields["many"], several)

    assert serializer.fields["one"].queryset is single
    assert serializer.fields["many"].child_relation.queryset is several


@pytest.mark.unit
def test_scope_to_caller_without_an_organization_is_empty():
    request = SimpleNamespace(organization=None, workspace=None, user=None)

    scoped = scope_to_caller(Project.no_workspace_objects.all(), request)

    assert isinstance(scoped, EmptyQuerySet)


@pytest.mark.unit
def test_caller_falls_back_to_the_bound_context():
    organization = SimpleNamespace(id=1)
    workspace = SimpleNamespace(id=2, is_default=False, organization_id=1)
    set_workspace_context(workspace=workspace, organization=organization)

    assert caller_organization(None) is organization
    assert caller_workspace(None) is workspace
    # A request authentication has not seen, with no user.
    request = SimpleNamespace(workspace=None, user=None)
    assert caller_organization(request) is organization
    assert caller_workspace(request) is workspace


@pytest.mark.unit
def test_caller_organization_keeps_what_authentication_resolved_none_included():
    former = SimpleNamespace(id=1)
    set_workspace_context(organization=SimpleNamespace(id=9))
    user = SimpleNamespace(is_authenticated=True, organization=former)
    request = SimpleNamespace(organization=None, workspace=None, user=user)

    assert caller_organization(request) is None


# An unsaved ``User`` already has a pk (``User.id`` is a UUID with a default),
# so "pk is None" cannot tell a stand-in from a stored account. These tests
# take no ``db`` fixture: pytest-django raises on any query, so each one also
# proves that a stand-in costs no database access.


@pytest.mark.unit
def test_an_unsaved_account_is_a_stand_in_with_no_organization():
    stand_in = User(email="stand-in@example.invalid")
    assert stand_in.pk is not None and stand_in._state.adding

    assert user_organization(stand_in) is None


@pytest.mark.unit
def test_an_unsaved_account_offers_only_its_organization_attribute():
    organization = Organization(name="Unsaved stand-in organization")
    stand_in = User(email="stand-in@example.invalid", organization=organization)

    assert user_organization(stand_in) is organization


@pytest.mark.unit
def test_a_force_authenticated_stand_in_resolves_without_the_database():
    # No ``organization`` attribute: a request authentication has not seen.
    request = SimpleNamespace(
        user=User(email="stand-in@example.invalid"), workspace=None
    )

    assert caller_organization(request) is None
    assert isinstance(
        scope_to_caller(Project.no_workspace_objects.all(), request), EmptyQuerySet
    )


@pytest.mark.unit
def test_align_legacy_organization_leaves_a_stand_in_alone():
    legacy = Organization(name="Unsaved legacy organization")
    stand_in = User(email="stand-in@example.invalid", organization=legacy)

    align_legacy_organization(stand_in, Organization(name="Resolved elsewhere"))

    assert stand_in.organization is legacy


def _account(organization, label):
    return User.objects.create_user(
        email=f"scope-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="scopepass123",
        name=f"Scope {label}",
        organization=organization,
        organization_role="Member",
    )


def _membership(user, organization, *, is_active):
    return OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role="Member",
        level=Level.MEMBER,
        is_active=is_active,
    )


@pytest.mark.integration
def test_user_organization_ignores_the_legacy_key_of_a_former_member(db):
    former = Organization.objects.create(name="Scope former")
    user = _account(former, "former")
    _membership(user, former, is_active=False)

    assert user_organization(user) is None
    request = SimpleNamespace(workspace=None, user=user)
    assert caller_organization(request) is None


def _removed_membership(user, organization):
    """A membership row removed as a cancelled invitation removes it."""
    membership = _membership(user, organization, is_active=False)
    OrganizationMembership._base_manager.filter(pk=membership.pk).update(deleted=True)
    return membership


@pytest.mark.integration
def test_membership_rows_of_every_state_count(db):
    organization = Organization.objects.create(name="Rows removed")
    other = Organization.objects.create(name="Rows other")
    user = _account(organization, "rows")
    assert has_membership_rows(user) is False

    _removed_membership(user, organization)

    assert has_membership_rows(user) is True
    assert has_membership_rows(user, organization) is True
    assert has_membership_rows(user, other.id) is False


@pytest.mark.integration
def test_a_removed_membership_grants_no_legacy_organization(db):
    former = Organization.objects.create(name="Removed former")
    user = _account(former, "removed")
    _removed_membership(user, former)

    assert user_organization(user) is None
    assert acts_in_organization(user, former) is False
    assert user.get_membership_level(former) is None
    assert user.get_organization_role(former) is None


@pytest.mark.integration
def test_user_organization_moves_to_the_active_membership(db):
    former = Organization.objects.create(name="Scope former")
    current = Organization.objects.create(name="Scope current")
    user = _account(former, "moved")
    _membership(user, former, is_active=False)
    _membership(user, current, is_active=True)

    assert user_organization(user) == current


@pytest.mark.integration
def test_user_organization_keeps_the_legacy_key_without_memberships(db):
    legacy = Organization.objects.create(name="Scope legacy")
    user = _account(legacy, "legacy")

    assert user_organization(user) == legacy


@pytest.mark.integration
def test_user_organization_keeps_the_legacy_key_of_an_active_member(db):
    home = Organization.objects.create(name="Scope home")
    other = Organization.objects.create(name="Scope other")
    user = _account(home, "member")
    _membership(user, other, is_active=True)
    _membership(user, home, is_active=True)

    assert user_organization(user) == home


@pytest.mark.unit
def test_caller_prefers_the_request():
    set_workspace_context(
        workspace=SimpleNamespace(id=9), organization=SimpleNamespace(id=9)
    )
    organization = SimpleNamespace(id=1)
    workspace = SimpleNamespace(id=2)
    request = SimpleNamespace(organization=organization, workspace=workspace)

    assert caller_organization(request) is organization
    assert caller_workspace(request) is workspace


class _Workspace:
    # Hashable by identity, as Q() comparison requires.
    def __init__(self, is_default):
        self.is_default = is_default
        self.organization_id = 7


@pytest.mark.unit
def test_workspace_scope_q_follows_the_manager_rule():
    default = _Workspace(is_default=True)
    other = _Workspace(is_default=False)

    assert workspace_scope_q(None) == Q()
    assert workspace_scope_q(other, "dataset__workspace") == Q(dataset__workspace=other)
    assert workspace_scope_q(default, "dataset__workspace") == (
        Q(dataset__workspace=default)
        | Q(dataset__workspace__is_default=True, dataset__workspace__organization_id=7)
        | Q(dataset__workspace__isnull=True)
    )


@pytest.mark.integration
def test_acts_in_organization_requires_an_active_membership(db):
    home = Organization.objects.create(name="Acts home")
    former = Organization.objects.create(name="Acts former")
    user = _account(former, "acts")
    _membership(user, former, is_active=False)
    _membership(user, home, is_active=True)

    assert acts_in_organization(user, home) is True
    assert acts_in_organization(user, home.id) is True
    assert acts_in_organization(user, former) is False
    assert acts_in_organization(user, None) is False
    assert acts_in_organization(None, home) is False


@pytest.mark.integration
def test_acts_in_organization_keeps_an_account_older_than_memberships(db):
    legacy = Organization.objects.create(name="Acts legacy")
    other = Organization.objects.create(name="Acts other")
    user = _account(legacy, "acts-legacy")

    assert acts_in_organization(user, legacy) is True
    assert acts_in_organization(user, other) is False


@pytest.mark.integration
def test_align_legacy_organization_clears_an_inactive_legacy_organization(db):
    former = Organization.objects.create(name="Align former")
    user = _account(former, "align-none")
    _membership(user, former, is_active=False)

    align_legacy_organization(user, None)

    assert user.organization is None
    assert user.organization_id is None
    # In memory only: the stored record is left as it was.
    assert User.objects.get(pk=user.pk).organization_id == former.id


@pytest.mark.integration
def test_align_legacy_organization_follows_the_resolved_organization(db):
    former = Organization.objects.create(name="Align former")
    current = Organization.objects.create(name="Align current")
    user = _account(former, "align-moved")
    _membership(user, former, is_active=False)
    _membership(user, current, is_active=True)

    align_legacy_organization(user, current)

    assert user.organization == current
    assert User.objects.get(pk=user.pk).organization_id == former.id


@pytest.mark.integration
def test_align_legacy_organization_keeps_an_active_or_legacy_organization(db):
    home = Organization.objects.create(name="Align home")
    other = Organization.objects.create(name="Align other")
    member = _account(home, "align-member")
    _membership(member, home, is_active=True)
    _membership(member, other, is_active=True)
    legacy = _account(home, "align-legacy")

    align_legacy_organization(member, other)
    align_legacy_organization(legacy, None)

    assert member.organization == home
    assert legacy.organization == home


def _resolved_request(view_cls=None):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.resolver_match = (
        SimpleNamespace(func=SimpleNamespace(cls=view_cls)) if view_cls else None
    )
    return request


@pytest.mark.integration
def test_authentication_aligns_the_request_user_with_the_resolved_organization(
    db,
):
    from accounts.authentication import APIKeyAuthentication

    former = Organization.objects.create(name="Auth former")
    user = _account(former, "auth-none")
    _membership(user, former, is_active=False)
    request = _resolved_request()

    APIKeyAuthentication()._set_workspace_context(request, user)

    assert request.organization is None
    assert user.organization is None
    assert User.objects.get(pk=user.pk).organization_id == former.id


@pytest.mark.unit
def test_no_organization_is_refused_unless_the_view_allows_it():
    from rest_framework.exceptions import PermissionDenied
    from rest_framework.views import APIView

    from accounts.authentication import (
        _require_an_organization,
        allows_no_organization,
    )

    class _Plain(APIView):
        pass

    @allows_no_organization
    class _Setup(APIView):
        pass

    for request in (_resolved_request(), _resolved_request(_Plain)):
        request.organization = None
        with pytest.raises(PermissionDenied) as refused:
            _require_an_organization(request)
        assert refused.value.get_codes() == "no_active_organization"

    allowed = _resolved_request(_Setup)
    allowed.organization = None
    _require_an_organization(allowed)

    resolved = _resolved_request(_Plain)
    resolved.organization = SimpleNamespace(id=uuid.uuid4())
    _require_an_organization(resolved)

"""N02/N11: accessible but unjoined workspaces never become implicit grants."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import PermissionDenied

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import org_member
from accounts.tests.test_wsl_merge_auth import request_for, snapshot
from model_hub.models.develop_dataset import Dataset
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
    workspace_context,
    workspace_scope_is_empty,
)
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
    request_workspace_q,
)

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.mark.parametrize("bound", [False, True])
def test_active_unjoined_default_is_not_provisioned_during_authentication(
    landing, bound
):
    # No prior workspace row: a provisioning mutant must reach the assertion,
    # not fail on the unique constraint for a deactivated membership.
    user, _ = org_member(
        landing.org, "never-joined@example.test", OrganizationRoles.MEMBER, Level.MEMBER
    )
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()
    assert landing.default.is_active
    assert not user.can_access_workspace(landing.default)
    before = snapshot()
    request = request_for(landing.org, bound=bound)
    if bound:
        with pytest.raises(PermissionDenied) as refused:
            APIKeyAuthentication()._set_workspace_context(request, user)
        assert refused.value.get_codes() == "workspace_required"
    else:
        APIKeyAuthentication()._set_workspace_context(request, user)
    assert request.workspace is None
    assert workspace_scope_is_empty()
    assert snapshot() == before


@pytest.mark.parametrize("actor", ["owner", "admin", "member", "unjoined", "anonymous"])
@pytest.mark.parametrize(
    "mixin", [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg]
)
def test_membership_union_controls_are_confined_without_an_empty_sentinel(
    landing, actor, mixin
):
    foreign = Organization.objects.create(name="Union foreign organization")
    rows = [
        Dataset.objects.create(name=name, organization=org, workspace=workspace)
        for name, org, workspace in (
            ("named", landing.org, landing.other),
            ("default", landing.org, landing.default),
            ("legacy", landing.org, None),
            ("foreign", foreign, None),
        )
    ]
    if actor == "admin":
        user, _ = org_member(
            landing.org,
            "union-admin@example.test",
            OrganizationRoles.ADMIN,
            Level.ADMIN,
        )
    elif actor == "anonymous":
        user = AnonymousUser()
    else:
        user = landing.owner if actor == "owner" else landing.user
    if actor == "member":
        WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).update(
            is_active=False
        )
    elif actor == "unjoined":
        WorkspaceMembership._base_manager.filter(user=user).update(is_active=False)
    queryset = Dataset.no_workspace_objects.filter(pk__in=[row.pk for row in rows])
    assert queryset.count() == 4
    request = SimpleNamespace(user=user, organization=landing.org, workspace=None)
    view = mixin()
    view.queryset, view.request, view.organization_lookup = (
        queryset,
        request,
        "organization",
    )
    expected = {
        "owner": [0, 1, 2],
        "admin": [0, 1, 2],
        "member": [1, 2],
        "unjoined": [],
        "anonymous": [],
    }[actor]
    assert not workspace_scope_is_empty()
    assert set(
        queryset.filter(
            request_workspace_q(request, "workspace", Dataset), organization=landing.org
        ).values_list("pk", flat=True)
    ) == {rows[index].pk for index in expected}
    assert set(view.get_queryset().values_list("pk", flat=True)) == {
        rows[index].pk for index in expected
    }
    with workspace_context(None, landing.org, user):
        set_workspace_context(require_workspace=True)
        assert workspace_scope_is_empty()
        assert not queryset.filter(
            request_workspace_q(request, "workspace", Dataset)
        ).exists()
        assert not view.get_queryset().exists()

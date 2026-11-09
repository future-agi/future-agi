"""Prebuilt querysets must retain the authenticated empty-workspace boundary."""

from types import SimpleNamespace

import pytest

from accounts.authentication import APIKeyAuthentication
from accounts.tests.test_workspace_landing_boundary import authenticate_data_request
from accounts.tests.test_workspace_landing_boundary import (
    boundary as _boundary_fixture,
)
from accounts.tests.test_workspace_landing_boundary import landing as _landing_fixture
from agentcc.models.session import AgentccSession
from agentcc.views.session import AgentccSessionViewSet
from model_hub.models.develop_dataset import Row
from model_hub.models.kb import KnowledgeBase
from model_hub.views.kb import KnowledgeBaseViewSet
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
)

boundary = _boundary_fixture
landing = _landing_fixture


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
@pytest.mark.parametrize(
    "mixin", [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg]
)
@pytest.mark.parametrize("manager", ["objects", "all_objects", "no_workspace_objects"])
@pytest.mark.parametrize("kind", ["dataset", "row"])
def test_prebuilt_view_queryset_remains_empty(boundary, mixin, manager, kind):
    instance = boundary.hidden
    if kind == "row":
        instance = Row.objects.create(dataset=boundary.hidden, order=0)
    # ModelViewSet.queryset is usually constructed at import, before auth.
    queryset = getattr(type(instance), manager).filter(pk=instance.pk)
    assert queryset.exists()
    view = mixin()
    view.queryset = queryset
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    request.user = boundary.user
    view.request = request
    assert request.workspace is None
    scoped = view.get_queryset()
    assert not scoped.exists()
    assert scoped.update(deleted=True) == 0
    assert scoped.delete()[0] == 0
    instance.refresh_from_db()
    assert instance.deleted is False


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
@pytest.mark.parametrize("resource", ["session", "knowledge_base"])
def test_real_auth_prebuilt_list_is_empty(boundary, resource):
    if resource == "session":
        model = AgentccSession
        values = {"session_id": "private-session", "name": "Private session"}
        view = AgentccSessionViewSet
    else:
        model = KnowledgeBase
        values = {"name": "Private knowledge base", "chunk_size": 256}
        view = KnowledgeBaseViewSet
    model.no_workspace_objects.create(
        organization=boundary.landing.org,
        workspace=boundary.landing.other,
        **values,
    )
    # These real views use class-level querysets created with no request scope.
    response = view.as_view({"get": "list"})(authenticate_data_request(boundary))
    assert response.status_code == 200, response.data
    payload = response.data.get("result", response.data)
    rows = payload.get("results", payload) if isinstance(payload, dict) else payload
    assert rows == [], response.data
    assert not boundary.user.can_access_workspace(boundary.landing.other)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize(
    "mixin", [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg]
)
def test_prebuilt_access_metadata_is_still_readable(boundary, mixin):
    from accounts.models.organization_membership import OrganizationMembership

    view = mixin()
    view.queryset = OrganizationMembership.no_workspace_objects.filter(
        user=boundary.user
    )
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    view.request = SimpleNamespace(
        user=boundary.user, workspace=None, organization=boundary.landing.org
    )
    assert view.get_queryset().exists()

"""The shared viewset mixins keep every organization check of both lineages.

FA dev integration (SC1): ``tfc/utils/base_viewset.py`` is the
integration (7C) mixin with three checks folded in. Each test below pins
one of them; ``tfc/tests/test_viewset_mixin_org_scope.py`` and
``agentcc/tests/test_orgless_user_isolation.py`` pin the integration checks.

- The organization authentication set on a request, ``None`` included, is
  final. Only a request authentication has not seen (no ``organization``
  attribute) falls back to the user's active membership.
- A viewset that names its own organization path still holds every row to the
  request's organization through the model's own organization foreign key.
- In a default workspace, a row reached through a project with no workspace
  is admitted only for a project of the workspace's organization, also by the
  workspace filter on its own.
"""

import uuid
from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
    active_request_organization,
    request_workspace_q,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project

PASSWORD = "viewset-union-scope-pw-123"

MIXINS = pytest.mark.parametrize(
    "mixin",
    [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg],
    ids=["BaseModelViewSetMixin", "BaseModelViewSetMixinWithUserOrg"],
)

# A request authentication has not seen carries no ``organization`` attribute.
_ABSENT = object()


def _tenant(name):
    """An org, an owner with an active membership, and its default workspace."""
    org = Organization.objects.create(name=name)
    user = User.objects.create_user(
        email=f"union-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name=name + " owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=name + " ws",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_ADMIN,
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return SimpleNamespace(org=org, user=user, workspace=workspace)


def _request(tenant, organization=_ABSENT, workspace=None):
    request = SimpleNamespace(user=tenant.user, workspace=workspace)
    if organization is not _ABSENT:
        request.organization = organization
    return request


def _rows(mixin, queryset, request, **attrs):
    view = type("_Probe", (mixin,), {"queryset": queryset, **attrs})()
    view.request = request
    return {row.id for row in view.get_queryset()}


def _dataset(tenant, name):
    return Dataset.no_workspace_objects.create(
        name=name,
        organization=tenant.org,
        workspace=tenant.workspace,
        user=tenant.user,
    )


def _config_of_a_project_without_a_workspace(tenant, name):
    """An eval config, a model scoped by its project only (no workspace and
    no organization of its own), of a project with no workspace."""
    template = EvalTemplate.no_workspace_objects.create(
        name=name + "-template",
        organization=tenant.org,
        workspace=tenant.workspace,
        config={"type": "pass_fail"},
    )
    project = Project.no_workspace_objects.create(
        name=name + "-project",
        organization=tenant.org,
        workspace=None,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    return CustomEvalConfig.no_workspace_objects.create(
        name=name, project=project, eval_template=template
    )


@pytest.mark.integration
@MIXINS
class TestAuthenticationsOrganizationIsFinal:
    def test_an_explicit_none_organization_is_final(self, db, mixin):
        mine = _tenant("Org A")
        own = _dataset(mine, "own-dataset")
        # The account is still an active owner of Org A, but authentication
        # resolved no organization for this request: nothing may widen that.
        request = _request(mine, organization=None, workspace=mine.workspace)

        assert active_request_organization(request) is None
        assert own.id not in _rows(mixin, Dataset.no_workspace_objects.all(), request)

    def test_a_request_authentication_has_not_seen_falls_back_to_the_membership(
        self, db, mixin
    ):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _dataset(mine, "own-dataset")
        other = _dataset(theirs, "victim-dataset")
        request = _request(mine, workspace=mine.workspace)

        assert active_request_organization(request) == mine.org
        rows = _rows(mixin, Dataset.no_workspace_objects.all(), request)

        assert own.id in rows
        assert other.id not in rows


@pytest.mark.integration
@MIXINS
class TestEveryRequiredRelation:
    def test_rows_are_held_to_the_org_through_every_required_relation(self, db, mixin):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _dataset(mine, "own-dataset")
        stray = _dataset(theirs, "stray-dataset")
        # An inconsistent row: Org B's dataset filed in Org A's workspace.
        # update() writes it without the model's save().
        Dataset.no_workspace_objects.filter(id=stray.id).update(
            workspace=mine.workspace
        )
        request = _request(mine, organization=mine.org, workspace=mine.workspace)

        rows = _rows(
            mixin,
            Dataset.no_workspace_objects.all(),
            request,
            organization_lookup="workspace__organization",
        )

        assert own.id in rows
        assert stray.id not in rows


@pytest.mark.integration
class TestProjectWithoutAWorkspace:
    @MIXINS
    def test_project_only_rows_follow_the_workspace_org_in_the_mixin(self, db, mixin):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _config_of_a_project_without_a_workspace(mine, "own-config")
        other = _config_of_a_project_without_a_workspace(theirs, "victim-config")
        request = _request(mine, organization=mine.org, workspace=mine.workspace)

        rows = _rows(mixin, CustomEvalConfig.no_workspace_objects.all(), request)

        assert own.id in rows
        assert other.id not in rows

    def test_project_only_null_workspace_rows_follow_the_workspace_org(self, db):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _config_of_a_project_without_a_workspace(mine, "own-config")
        other = _config_of_a_project_without_a_workspace(theirs, "victim-config")
        request = _request(mine, organization=mine.org, workspace=mine.workspace)

        # The workspace filter on its own, before any organization filter.
        rows = set(
            CustomEvalConfig.no_workspace_objects.filter(
                request_workspace_q(request, "project__workspace", CustomEvalConfig)
            ).values_list("id", flat=True)
        )

        assert own.id in rows
        assert other.id not in rows

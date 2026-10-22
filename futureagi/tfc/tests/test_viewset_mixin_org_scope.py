"""The shared viewset mixins constrain every row to the request's organization.

Every row a shared viewset mixin serves belongs to the request's organization,
whether the model is scoped by organization, workspace or project; a model
with none of these serves no rows. Through the mixins, a request with no
workspace sees only the workspaces the user can access
(``TestNoWorkspaceFollowsTheUsersWorkspaceAccess``).

The SAML path over HTTP is in ``saml2_auth/tests/test_scoped_session_without_workspace.py``.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.urls import get_resolver
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.blocklist import AgentccBlocklist
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
    organization_lookup_for,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project

PASSWORD = "viewset-org-scope-pw-123"

MIXINS = pytest.mark.parametrize(
    "mixin",
    [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg],
    ids=["BaseModelViewSetMixin", "BaseModelViewSetMixinWithUserOrg"],
)


def _tenant(name, *, role=OrganizationRoles.OWNER, level=Level.OWNER):
    """An org, a member with a password, and its default workspace."""
    org = Organization.objects.create(name=name)
    user = User.objects.create_user(
        email=f"scope-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name=name + " member",
        organization=org,
        organization_role=role,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={"role": role, "level": level, "is_active": True},
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
            "level": level,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return SimpleNamespace(org=org, user=user, workspace=workspace)


def _dashboard(tenant, name):
    return Dashboard.no_workspace_objects.create(
        name=name,
        workspace=tenant.workspace,
        created_by=tenant.user,
        updated_by=tenant.user,
    )


def _eval_config(tenant, name, *, project_workspace):
    template = EvalTemplate.no_workspace_objects.create(
        name=name + "-template",
        organization=tenant.org,
        workspace=tenant.workspace,
        config={"type": "pass_fail"},
    )
    project = Project.no_workspace_objects.create(
        name=name + "-project",
        organization=tenant.org,
        workspace=project_workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    return CustomEvalConfig.no_workspace_objects.create(
        name=name, project=project, eval_template=template
    )


def _experiment(tenant, name):
    dataset = Dataset.no_workspace_objects.create(
        name=name + "-dataset",
        organization=tenant.org,
        workspace=tenant.workspace,
        user=tenant.user,
    )
    return ExperimentsTable.no_workspace_objects.create(
        name=name, dataset=dataset, user=tenant.user
    )


def _rows(mixin, queryset, tenant, workspace, **attrs):
    view_class = type("_Probe", (mixin,), {"queryset": queryset, **attrs})
    view = view_class()
    view.request = SimpleNamespace(
        organization=tenant.org, workspace=workspace, user=tenant.user
    )
    return {row.id for row in view.get_queryset()}


@pytest.mark.integration
@MIXINS
class TestOrganizationWithoutAWorkspace:
    """The request has an organization and no workspace: a SAML-scoped member
    with no workspace access."""

    def test_workspace_scoped_rows_stay_in_the_requests_org(self, db, mixin):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _dashboard(mine, "own-dashboard")
        other = _dashboard(theirs, "victim-dashboard")

        rows = _rows(mixin, Dashboard.no_workspace_objects.all(), mine, None)

        assert other.id not in rows
        assert own.id in rows

    def test_project_scoped_rows_stay_in_the_requests_org(self, db, mixin):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _eval_config(mine, "own-config", project_workspace=mine.workspace)
        other = _eval_config(
            theirs, "victim-config", project_workspace=theirs.workspace
        )

        rows = _rows(mixin, CustomEvalConfig.no_workspace_objects.all(), mine, None)

        assert other.id not in rows
        assert own.id in rows


@pytest.mark.integration
@MIXINS
class TestDefaultWorkspace:
    def test_another_orgs_project_without_a_workspace_is_not_admitted(self, db, mixin):
        """In a default workspace the filter admits rows whose project has no
        workspace; each of those belongs to the request's org."""
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own_unassigned = _eval_config(mine, "own-config", project_workspace=None)
        other_unassigned = _eval_config(theirs, "victim-config", project_workspace=None)

        rows = _rows(
            mixin, CustomEvalConfig.no_workspace_objects.all(), mine, mine.workspace
        )

        assert other_unassigned.id not in rows
        assert own_unassigned.id in rows

    def test_a_named_workspace_still_narrows_within_the_org(self, db, mixin):
        mine = _tenant("Org A")
        named = Workspace.objects.create(
            name="Named",
            organization=mine.org,
            is_default=False,
            is_active=True,
            created_by=mine.user,
        )
        in_default = _dashboard(mine, "default-ws-dashboard")
        in_named = Dashboard.no_workspace_objects.create(
            name="named-ws-dashboard",
            workspace=named,
            created_by=mine.user,
            updated_by=mine.user,
        )

        rows = _rows(mixin, Dashboard.no_workspace_objects.all(), mine, named)

        assert rows == {in_named.id}
        assert in_default.id not in rows


@pytest.mark.integration
@MIXINS
class TestModelsWithoutADirectOrganization:
    def test_a_viewsets_organization_lookup_scopes_the_rows(self, db, mixin):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        own = _experiment(mine, "own-experiment")
        other = _experiment(theirs, "victim-experiment")

        rows = _rows(
            mixin,
            ExperimentsTable.no_workspace_objects.all(),
            mine,
            mine.workspace,
            organization_lookup="dataset__organization",
        )

        assert rows == {own.id}
        assert other.id not in rows

    def test_a_model_with_no_path_to_an_organization_serves_nothing(self, db, mixin):
        """No organization, workspace or project field and no
        organization_lookup: the mixin cannot tell whose a row is, so it
        serves none."""
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        DashboardWidget.objects.create(dashboard=_dashboard(mine, "own"))
        DashboardWidget.objects.create(dashboard=_dashboard(theirs, "victim"))

        rows = _rows(mixin, DashboardWidget.objects.all(), mine, mine.workspace)

        assert rows == set()


def _member(tenant, *, role=OrganizationRoles.MEMBER, level=Level.MEMBER, of=()):
    """A user of ``tenant``'s org with ``role``, an active member of the
    workspaces ``of`` and of no other."""
    user = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name="org member",
        organization=tenant.org,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=tenant.org, role=role, level=level, is_active=True
    )
    for workspace in of:
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
    return user


def _rows_as(mixin, queryset, organization, user):
    """The rows a request with ``organization``, ``user`` and no workspace gets."""
    view_class = type("_Probe", (mixin,), {"queryset": queryset})
    view = view_class()
    view.request = SimpleNamespace(organization=organization, workspace=None, user=user)
    return {row.id for row in view.get_queryset()}


@pytest.fixture
def org_with_a_restricted_workspace(db):
    """Org A with its default workspace, a non-default one, and in each a
    dashboard and an eval config; plus an eval config on a project with no
    workspace."""
    tenant = _tenant("Org A")
    restricted = Workspace.objects.create(
        name="Restricted",
        organization=tenant.org,
        is_default=False,
        is_active=True,
        created_by=tenant.user,
    )
    in_restricted = SimpleNamespace(
        org=tenant.org, user=tenant.user, workspace=restricted
    )
    return SimpleNamespace(
        tenant=tenant,
        restricted=restricted,
        default_dashboard=_dashboard(tenant, "default-ws-dashboard"),
        restricted_dashboard=_dashboard(in_restricted, "restricted-dashboard"),
        default_config=_eval_config(
            tenant, "default-config", project_workspace=tenant.workspace
        ),
        restricted_config=_eval_config(
            tenant, "restricted-config", project_workspace=restricted
        ),
        unassigned_config=_eval_config(
            tenant, "unassigned-config", project_workspace=None
        ),
    )


@pytest.mark.integration
@MIXINS
class TestNoWorkspaceFollowsTheUsersWorkspaceAccess:
    """The request has an organization and no workspace (as for a SAML-scoped
    session that resolves none). Rows scoped by workspace or project are the
    ones the user gets in the workspaces they can access, none when they can
    access none; org-level rows stay the org's."""

    def test_a_member_with_no_workspace_access_gets_no_workspace_scoped_rows(
        self, mixin, org_with_a_restricted_workspace
    ):
        org = org_with_a_restricted_workspace
        member = _member(org.tenant)

        dashboards = _rows_as(
            mixin, Dashboard.no_workspace_objects.all(), org.tenant.org, member
        )
        configs = _rows_as(
            mixin, CustomEvalConfig.no_workspace_objects.all(), org.tenant.org, member
        )

        assert dashboards == set()
        assert configs == set()

    def test_a_member_gets_the_workspaces_they_belong_to(
        self, mixin, org_with_a_restricted_workspace
    ):
        org = org_with_a_restricted_workspace
        member = _member(org.tenant, of=[org.restricted])

        dashboards = _rows_as(
            mixin, Dashboard.no_workspace_objects.all(), org.tenant.org, member
        )
        configs = _rows_as(
            mixin, CustomEvalConfig.no_workspace_objects.all(), org.tenant.org, member
        )

        assert dashboards == {org.restricted_dashboard.id}
        assert configs == {org.restricted_config.id}

    def test_a_member_of_the_default_workspace_also_gets_rows_without_one(
        self, mixin, org_with_a_restricted_workspace
    ):
        """The default workspace admits rows with no workspace; so does the
        union over the workspaces a member of it can access."""
        org = org_with_a_restricted_workspace
        member = _member(org.tenant, of=[org.tenant.workspace])

        configs = _rows_as(
            mixin, CustomEvalConfig.no_workspace_objects.all(), org.tenant.org, member
        )

        assert configs == {org.default_config.id, org.unassigned_config.id}

    @pytest.mark.parametrize(
        "role,level",
        [
            (OrganizationRoles.OWNER, Level.OWNER),
            (OrganizationRoles.ADMIN, Level.ADMIN),
        ],
        ids=["owner", "admin"],
    )
    def test_an_owner_or_admin_without_a_workspace_is_not_narrowed(
        self, mixin, org_with_a_restricted_workspace, role, level
    ):
        """They can access every workspace of the org, so they keep every
        row, those with no workspace included (org-owned rows such as
        projects and gateway rows carry a NULL workspace by design)."""
        org = org_with_a_restricted_workspace
        user = _member(org.tenant, role=role, level=level)
        unassigned_project = Project.no_workspace_objects.create(
            name="unassigned-project",
            organization=org.tenant.org,
            workspace=None,
            model_type="GenerativeLLM",
            trace_type="observe",
        )

        configs = _rows_as(
            mixin, CustomEvalConfig.no_workspace_objects.all(), org.tenant.org, user
        )
        projects = _rows_as(
            mixin, Project.no_workspace_objects.all(), org.tenant.org, user
        )

        assert configs == {
            org.default_config.id,
            org.restricted_config.id,
            org.unassigned_config.id,
        }
        assert unassigned_project.id in projects

    def test_rows_of_a_model_with_no_workspace_are_not_narrowed(
        self, mixin, org_with_a_restricted_workspace
    ):
        """The rule applies to rows scoped by workspace or project. An
        org-level row (a gateway blocklist) has no workspace and stays the
        org's, still never another org's."""
        org = org_with_a_restricted_workspace
        theirs = _tenant("Org B")
        own = AgentccBlocklist.no_workspace_objects.create(
            organization=org.tenant.org, name="own-blocklist"
        )
        AgentccBlocklist.no_workspace_objects.create(
            organization=theirs.org, name="victim-blocklist"
        )
        member = _member(org.tenant)

        rows = _rows_as(
            mixin, AgentccBlocklist.no_workspace_objects.all(), org.tenant.org, member
        )

        assert rows == {own.id}

    def test_a_request_with_no_user_gets_no_workspace_scoped_rows(
        self, mixin, org_with_a_restricted_workspace
    ):
        org = org_with_a_restricted_workspace

        rows = _rows_as(
            mixin, Dashboard.no_workspace_objects.all(), org.tenant.org, None
        )

        assert rows == set()


def _mixin_viewsets():
    assert get_resolver().url_patterns  # imports every routed view module
    seen, stack = set(), [BaseModelViewSetMixin]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return seen


def _model_of(view_class):
    queryset = getattr(view_class, "queryset", None)
    if queryset is not None:
        return queryset.model
    meta = getattr(getattr(view_class, "serializer_class", None), "Meta", None)
    return getattr(meta, "model", None)


# Viewsets whose own get_queryset applies an organization or workspace
# filter; the organization-path check below covers the mixin's queryset.
BUILDS_ITS_OWN_QUERYSET = {
    "integrations.views.sync_log.SyncLogViewSet",
    "model_hub.views.experiments.ExperimentsTableDetailView",
    "model_hub.views.prompt_template.PromptHistoryExecutionViewSet",
    "tracer.views.dashboard.DashboardWidgetViewSet",
}


@pytest.mark.integration
def test_every_mixin_viewset_can_reach_its_rows_organization(db):
    """A viewset added on the mixins whose model has no organization,
    workspace or project field would serve nothing (fail closed). This names
    it, so it gets an ``organization_lookup``, and checks each lookup is a
    real ORM path."""
    missing, invalid, checked = [], [], 0
    for view_class in _mixin_viewsets():
        name = f"{view_class.__module__}.{view_class.__qualname__}"
        model = _model_of(view_class)
        if model is None or name in BUILDS_ITS_OWN_QUERYSET:
            continue
        # Probe classes that test modules (this one included) build on the
        # mixins; any test module imported in the same worker can add some.
        # tfc/tests has no __init__.py, so its modules import as "test_...".
        # (simulate.models.test_execution and the like are real modules.)
        parts = view_class.__module__.split(".")
        if (
            "tests" in parts
            or parts[-1] == "conftest"
            or (len(parts) == 1 and parts[0].startswith("test_"))
        ):
            continue
        lookup = organization_lookup_for(model, view_class.organization_lookup)
        if lookup is None:
            missing.append(f"{name} ({model._meta.label})")
            continue
        try:
            str(model._base_manager.filter(**{lookup: uuid.uuid4()}).query)
        except Exception as exc:  # noqa: BLE001 - report every bad path
            invalid.append(f"{name}: {lookup}: {exc}")
        checked += 1

    assert not missing, f"no path to an organization: {missing}"
    assert not invalid, f"organization_lookup is not a valid path: {invalid}"
    assert checked >= 40, f"only {checked} mixin viewsets found"


def _token_client(user):
    client = APIClient()
    resp = client.post(
        "/accounts/token/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


@pytest.mark.integration
@pytest.mark.api
class TestOrgMemberOverHTTP:
    """An ordinary signed-in member, in their org's default workspace."""

    def test_experiment_list_shows_only_the_members_org(self, db):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        _experiment(mine, "own-experiment")
        other = _experiment(theirs, "victim-experiment")
        client = _token_client(mine.user)

        listed = client.get("/model-hub/experiments/data/")
        by_dataset = client.get(
            f"/model-hub/experiments/data/?dataset_id={other.dataset_id}"
        )

        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert "own-experiment" in listed.content.decode()
        assert "victim-experiment" not in listed.content.decode(), (
            "another org's experiment in the list"
        )
        assert "victim-experiment" not in by_dataset.content.decode(), (
            "another org's experiment in the dataset-filtered list"
        )

    def test_experiment_list_keeps_to_the_requests_workspace(self, db):
        """Experiments have no workspace of their own: the list narrows by
        the dataset's workspace, as the other experiment views do."""
        mine = _tenant("Org A")
        named = Workspace.objects.create(
            name="Named",
            organization=mine.org,
            is_default=False,
            is_active=True,
            created_by=mine.user,
        )
        _experiment(mine, "default-ws-experiment")
        elsewhere = SimpleNamespace(org=mine.org, user=mine.user, workspace=named)
        _experiment(elsewhere, "named-ws-experiment")
        client = _token_client(mine.user)

        resp = client.get(
            "/model-hub/experiments/data/", HTTP_X_WORKSPACE_ID=str(named.id)
        )

        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert "named-ws-experiment" in resp.content.decode()
        assert "default-ws-experiment" not in resp.content.decode()

    def test_eval_configs_of_another_orgs_unassigned_project_are_not_listed(self, db):
        mine, theirs = _tenant("Org A"), _tenant("Org B")
        _eval_config(mine, "own-config", project_workspace=mine.workspace)
        _eval_config(theirs, "victim-config", project_workspace=None)
        client = _token_client(mine.user)

        resp = client.get("/tracer/custom-eval-config/")

        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert "own-config" in resp.content.decode()
        assert "victim-config" not in resp.content.decode(), (
            "another org's eval config in the list"
        )

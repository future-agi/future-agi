"""N11-N13: prebuilt querysets retain both landing and dual-owner confinement."""

from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
    workspace_context,
    workspace_scope_is_empty,
)
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
    _no_workspace_q,
    request_workspace_q,
    required_organization_lookup_for,
    workspace_lookup_for,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project

landing = _landing_fixture
MIXINS = pytest.mark.parametrize(
    "mixin", [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg]
)
MANAGERS = pytest.mark.parametrize(
    "manager", ["objects", "all_objects", "no_workspace_objects"]
)


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


def view_for(mixin, queryset, request, lookup):
    view = mixin()
    view.queryset = queryset
    view.request = request
    view.organization_lookup = lookup
    return view


@MIXINS
@MANAGERS
@pytest.mark.parametrize("kind", ["dataset", "row", "cell"])
def test_prebuilt_fk_owned_rows_cannot_bypass_empty_scope(
    landing, mixin, manager, kind
):
    dataset = Dataset.objects.create(
        name="Prebuilt target", organization=landing.org, workspace=landing.other
    )
    row = Row.objects.create(dataset=dataset, order=0)
    column = Column.objects.create(
        name="Input",
        dataset=dataset,
        data_type=Column._meta.get_field("data_type").choices[0][0],
        source=Column._meta.get_field("source").choices[0][0],
    )
    cell = Cell.objects.create(
        dataset=dataset, row=row, column=column, value="unchanged"
    )
    instance, lookup = {
        "dataset": (dataset, "organization"),
        "row": (row, "dataset__organization"),
        "cell": (cell, "row__dataset__organization"),
    }[kind]
    queryset = getattr(type(instance), manager).filter(pk=instance.pk)
    assert queryset.exists()
    assert row.dataset_id == column.dataset_id == cell.dataset_id == dataset.pk
    assert cell.row_id == row.pk and cell.column_id == column.pk
    assert workspace_lookup_for(type(instance)) == (
        "workspace" if kind == "dataset" else None
    )
    before = list(type(instance)._base_manager.filter(pk=instance.pk).values())
    request = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=None
    )
    view = view_for(mixin, queryset, request, lookup)
    with workspace_context(None, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert request.organization == landing.org
        assert request.workspace is None
        assert workspace_scope_is_empty()
        scoped = view.get_queryset()
        assert not scoped.exists()
        assert scoped.update(deleted=True) == 0
        assert scoped.delete()[0] == 0
        assert (
            type(instance)._base_manager.filter(pk=instance.pk, deleted=False).exists()
        )
        assert OrganizationMembership.no_workspace_objects.filter(
            user=landing.user, organization=landing.org
        ).exists()
        assert (
            list(type(instance)._base_manager.filter(pk=instance.pk).values()) == before
        )
    # Reuse the exact pre-auth queryset: do not hide the mutant behind a fresh manager.
    assert not workspace_scope_is_empty()
    assert view.get_queryset().filter(pk=instance.pk).exists()


@MIXINS
@MANAGERS
@pytest.mark.parametrize("is_default", [False, True])
@pytest.mark.parametrize("case", ["direct", "project", "template"])
def test_nonempty_dual_owned_rows_require_both_organizations(
    landing, mixin, manager, is_default, case
):
    workspace = landing.default if is_default else landing.other
    foreign = Organization.objects.create(name="Foreign owner")
    if case == "direct":
        control = Dataset.objects.create(
            name="Allowed", organization=landing.org, workspace=workspace
        )
        victim = Dataset.objects.create(name="Inconsistent", organization=foreign)
        Dataset._base_manager.filter(pk=victim.pk).update(workspace=workspace)
        victim.refresh_from_db()
        assert victim.organization_id == foreign.pk
        assert victim.workspace_id == workspace.pk
        assert control.organization_id == landing.org.pk
        model, lookup = Dataset, "workspace__organization"
        assert required_organization_lookup_for(model) == "organization"
    else:
        template = EvalTemplate.objects.create(
            name="Allowed template",
            organization=landing.org,
            workspace=workspace,
            config={"type": "pass_fail"},
        )
        other_template = EvalTemplate.objects.create(
            name="Foreign template", organization=foreign, config={"type": "pass_fail"}
        )
        project = Project.objects.create(
            name="Allowed project",
            organization=landing.org,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        other_project = Project.objects.create(
            name="Foreign project",
            organization=foreign,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        Project._base_manager.filter(pk=other_project.pk).update(workspace=workspace)
        other_project.refresh_from_db()
        assert other_project.organization_id == foreign.pk
        assert other_project.workspace_id == workspace.pk
        control = CustomEvalConfig.objects.create(
            name="Allowed", project=project, eval_template=template
        )
        victim = CustomEvalConfig.objects.create(
            name="Inconsistent",
            project=other_project if case == "project" else project,
            eval_template=template if case == "project" else other_template,
        )
        victim.refresh_from_db()
        assert victim.project_id == (
            other_project.pk if case == "project" else project.pk
        )
        assert victim.eval_template_id == (
            template.pk if case == "project" else other_template.pk
        )
        assert template.organization_id == project.organization_id == landing.org.pk
        assert other_template.organization_id == foreign.pk
        assert (
            control.project_id == project.pk and control.eval_template_id == template.pk
        )
        model, lookup = CustomEvalConfig, "eval_template__organization"
        assert "organization" not in {f.name for f in model._meta.fields}
        assert "workspace" not in {f.name for f in model._meta.fields}
        assert workspace_lookup_for(model) == "project__workspace"
        assert required_organization_lookup_for(model) == "project__organization"
    queryset = getattr(model, manager).filter(pk__in=[control.pk, victim.pk])
    assert set(queryset.values_list("pk", flat=True)) == {control.pk, victim.pk}
    request = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=workspace
    )
    assert landing.user.can_access_workspace(workspace)
    with workspace_context(workspace, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert not workspace_scope_is_empty()
        required = required_organization_lookup_for(model)
        assert queryset.filter(
            request_workspace_q(request, workspace_lookup_for(model), model),
            pk=control.pk,
            **{lookup: landing.org, required: landing.org},
        ).exists()
        if case == "template":
            assert queryset.filter(pk=victim.pk, **{required: landing.org}).exists()
            assert not queryset.filter(pk=victim.pk, **{lookup: landing.org}).exists()
        if case == "project":
            # M13-DUPLICATE has no other reason to reject this victim.
            assert queryset.filter(pk=victim.pk, **{lookup: landing.org}).exists()
            assert queryset.filter(
                request_workspace_q(request, "project__workspace", model), pk=victim.pk
            ).exists()
            assert not queryset.filter(
                pk=victim.pk, project__organization=landing.org
            ).exists()
        visible = set(
            view_for(mixin, queryset, request, lookup)
            .get_queryset()
            .values_list("pk", flat=True)
        )
        assert visible == {control.pk}
        assert victim.pk not in visible


@MIXINS
@pytest.mark.parametrize("actor", ["owner", "user"])
def test_empty_sentinel_overrides_owner_and_membership_union(landing, mixin, actor):
    user = getattr(landing, actor)
    own = Dataset.objects.create(
        name="Accessible otherwise", organization=landing.org, workspace=landing.default
    )
    queryset = Dataset.no_workspace_objects.filter(pk=own.pk)
    request = SimpleNamespace(user=user, organization=landing.org, workspace=None)
    view = view_for(mixin, queryset, request, "organization")
    assert view.get_queryset().exists()
    with workspace_context(None, landing.org, user):
        set_workspace_context(require_workspace=True)
        assert not queryset.filter(
            request_workspace_q(request, "workspace", Dataset)
        ).exists()
        assert not view.get_queryset().exists()
    request.auth_scope = SimpleNamespace(org_id=landing.org.pk)
    assert not queryset.filter(
        request_workspace_q(request, "workspace", Dataset)
    ).exists()
    assert not view.get_queryset().exists()


@MIXINS
@MANAGERS
def test_explicit_none_org_and_absent_org_remain_distinct(landing, mixin, manager):
    row = Dataset.objects.create(
        name="Explicit org control", organization=landing.org, workspace=landing.default
    )
    queryset = getattr(Dataset, manager).filter(pk=row.pk)
    request = SimpleNamespace(
        user=landing.user, organization=None, workspace=landing.default
    )
    view = view_for(mixin, queryset, request, "organization")
    with workspace_context(landing.default, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert not workspace_scope_is_empty()
        assert not view.get_queryset().exists()
        del request.organization
        assert list(view.get_queryset()) == [row]


def test_default_null_project_predicate_keeps_project_organization(landing):
    foreign = Organization.objects.create(name="Foreign NULL project owner")
    template = EvalTemplate.objects.create(
        name="Caller template",
        organization=landing.org,
        workspace=landing.default,
        config={"type": "pass_fail"},
    )
    rows = []
    for org in (landing.org, foreign):
        project = Project.objects.create(
            name="NULL project",
            organization=org,
            workspace=None,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        rows.append(
            CustomEvalConfig.objects.create(
                name="NULL project config",
                project=project,
                eval_template=template,
            )
        )
    queryset = CustomEvalConfig.no_workspace_objects.filter(
        pk__in=[row.pk for row in rows]
    )
    assert queryset.count() == 2
    request = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=landing.default
    )
    with workspace_context(landing.default, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert not workspace_scope_is_empty()
        # Direct helper witness: the mixin's required-owner filter must not
        # hide deletion of this independent NULL-project ownership clause.
        assert list(
            queryset.filter(
                _no_workspace_q("project__workspace", landing.default, CustomEvalConfig)
            )
        ) == [rows[0]]
        assert list(
            queryset.filter(
                request_workspace_q(request, "project__workspace", CustomEvalConfig)
            )
        ) == [rows[0]]

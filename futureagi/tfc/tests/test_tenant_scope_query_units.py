"""Unit layer under the production-path tenant conformance tests
(``test_tenant_scope_production_path.py``).

For a caller signed in to tenant B's workspace:

- the experiment list's queryset carries B's organization and workspace in
  its SQL, the organization on the scoped model's own column;
- every related serializer field the tenant scoping covers resolves ids from
  a queryset whose SQL carries B's organization and workspace, accepts B's
  own id, and rejects A's id exactly as it rejects an id that does not exist.

For a caller whom authentication resolved to no organization (a member
removed from every organization, whose account still names the former one),
each serializer that scopes its related fields by organization resolves no
row at all, and the monitor view resolves no organization.
"""

import uuid

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.ai_model import AIModel
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.serializers.develop_annotations import AnnotationsLabelsSerializer
from model_hub.views.experiments import ExperimentListAPIView
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer
from tracer.serializers.eval_task import EvalTaskSerializer
from tracer.serializers.monitor import UserAlertMonitorSerializer
from tracer.serializers.observation_span import ObservationSpanSerializer
from tracer.serializers.project_version import ProjectVersionSerializer
from tracer.serializers.trace import TraceSerializer
from tracer.serializers.trace_session import TraceSessionSerializer
from tracer.views.monitor import UserAlertMonitorView


def _rows(tenant, tag):
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        name=f"{tag} project",
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"{tag.lower()}-unit-template",
        organization=tenant.organization,
        workspace=tenant.workspace,
        owner=OwnerChoices.USER.value,
        config={"type": "pass_fail", "criteria": "Is the output correct?"},
    )
    return {
        "project": project,
        "template": template,
        "kb_file": KnowledgeBaseFile.no_workspace_objects.create(
            name=f"{tag} kb file",
            organization=tenant.organization,
            workspace=tenant.workspace,
        ),
        "config": CustomEvalConfig.no_workspace_objects.create(
            name=f"{tag} config",
            project=project,
            eval_template=template,
            config={},
            mapping={"input": "input", "output": "output"},
            filters={},
        ),
    }


@pytest.fixture
def tenants(db):
    a, b = make_tenant("A"), make_tenant("B")
    data = {"a": a, "b": b, "a_rows": _rows(a, "A"), "b_rows": _rows(b, "B")}
    clear_workspace_context()
    return data


def _request(tenant, path="/"):
    """A request as authentication leaves it for a caller in ``tenant``."""
    django_request = APIRequestFactory().get(path)
    force_authenticate(django_request, user=tenant.user)
    django_request.organization = tenant.organization
    django_request.workspace = tenant.workspace
    return Request(django_request)


def _request_without_organization(tenant):
    """A request as authentication leaves it for a caller it resolved to no
    organization. The account's own organization field still names
    ``tenant``'s organization, as a removed member's does."""
    django_request = APIRequestFactory().get("/")
    force_authenticate(django_request, user=tenant.user)
    django_request.organization = None
    django_request.workspace = None
    return Request(django_request)


def _hex(value):
    return str(value).replace("-", "")


def _assert_sql_scoped_to(queryset, tenant, organization_model):
    """The SQL compares ``organization_model``'s own organization column with
    the tenant's organization, and filters on the tenant's workspace.

    The organization check names the table: a default workspace's rule alone
    also compares an organization column (the workspace's), which does not
    limit rows that have no workspace to the tenant's organization.
    """
    sql, params = queryset.query.sql_with_params()
    assert " WHERE " in sql, sql
    column = f'"{organization_model._meta.db_table}"."organization_id" = %s'
    head, found, _ = sql.partition(column)
    assert found and " WHERE " in head, (column, sql)
    assert _hex(params[head.count("%s")]) == tenant.organization.id.hex, (
        column,
        sql,
        params,
    )
    where = sql.split(" WHERE ", 1)[1]
    assert '"workspace_id"' in where, sql
    assert tenant.workspace.id.hex in {_hex(value) for value in params}, (sql, params)


def test_sql_check_requires_the_organization_filter_itself(tenants):
    """Planted case: a queryset with the default workspace's rule but no
    organization filter of its own fails the check (and does list another
    organization's row that has no workspace)."""
    tenant_scope = pytest.importorskip("tfc.utils.tenant_scope")
    a_project = tenants["a_rows"]["project"]
    clear_workspace_context()
    Project.no_workspace_objects.filter(id=a_project.id).update(workspace=None)
    workspace_only = Project.no_workspace_objects.filter(
        tenant_scope.workspace_scope_q(tenants["b"].workspace)
    )

    assert tenants["b"].workspace.is_default
    assert a_project.id in set(workspace_only.values_list("id", flat=True))
    with pytest.raises(AssertionError):
        _assert_sql_scoped_to(workspace_only, tenants["b"], Project)
    _assert_sql_scoped_to(
        tenant_scope.scope_to_caller(
            Project.no_workspace_objects.all(), _request(tenants["b"])
        ),
        tenants["b"],
        Project,
    )


def test_experiment_list_queryset_sql_carries_caller_organization_and_workspace(
    tenants,
):
    request = _request(tenants["b"], "/model-hub/experiments/data/")
    view = ExperimentListAPIView()
    view.setup(request._request)
    view.request = request
    view.format_kwarg = None

    _assert_sql_scoped_to(view.get_queryset(), tenants["b"], Dataset)


# name -> (serializer, field, row key)
FIELDS = {
    "custom-eval-config-project": (CustomEvalConfigSerializer, "project", "project"),
    "custom-eval-config-eval-template": (
        CustomEvalConfigSerializer,
        "eval_template",
        "template",
    ),
    "custom-eval-config-kb-file": (CustomEvalConfigSerializer, "kb_id", "kb_file"),
    "eval-task-project": (EvalTaskSerializer, "project", "project"),
    "eval-task-eval-config": (EvalTaskSerializer, "evals", "config"),
    "annotation-label-project": (AnnotationsLabelsSerializer, "project", "project"),
    "monitor-project": (UserAlertMonitorSerializer, "project", "project"),
}

# name -> the model whose own organization column the field's queryset filters
# (an eval config belongs to its organization through its project).
ORGANIZATION_MODELS = {
    "custom-eval-config-project": Project,
    "custom-eval-config-eval-template": EvalTemplate,
    "custom-eval-config-kb-file": KnowledgeBaseFile,
    "eval-task-project": Project,
    "eval-task-eval-config": Project,
    "annotation-label-project": Project,
    "monitor-project": Project,
}


def _relation(name, tenant):
    """The relation that resolves the field's ids (a many field's child)."""
    serializer_class, field_name, _ = FIELDS[name]
    serializer = serializer_class(context={"request": _request(tenant)})
    field = serializer.fields[field_name]
    return getattr(field, "child_relation", field)


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_serializer_field_queryset_sql_carries_caller_organization_and_workspace(
    tenants, name
):
    _assert_sql_scoped_to(
        _relation(name, tenants["b"]).get_queryset(),
        tenants["b"],
        ORGANIZATION_MODELS[name],
    )


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_serializer_field_validates_against_caller_scope(tenants, name):
    key = FIELDS[name][2]
    own, foreign = tenants["b_rows"][key], tenants["a_rows"][key]
    relation = _relation(name, tenants["b"])

    assert relation.to_internal_value(str(own.id)).id == own.id

    with pytest.raises(ValidationError) as foreign_error:
        relation.to_internal_value(str(foreign.id))
    with pytest.raises(ValidationError) as unknown_error:
        relation.to_internal_value(str(uuid.uuid4()))

    assert foreign_error.value.get_codes() == ["does_not_exist"]
    assert unknown_error.value.get_codes() == ["does_not_exist"]


# name -> (serializer, the related fields it scopes by the caller's organization)
NO_ORGANIZATION_FIELDS = {
    "trace": (TraceSerializer, ("project", "project_version", "session")),
    "observation-span": (
        ObservationSpanSerializer,
        ("project", "trace", "project_version"),
    ),
    "project-version": (ProjectVersionSerializer, ("project",)),
    "trace-session": (TraceSessionSerializer, ("project",)),
    "monitor": (UserAlertMonitorSerializer, ("project",)),
}


@pytest.mark.parametrize("name", sorted(NO_ORGANIZATION_FIELDS))
def test_serializer_without_caller_organization_resolves_no_row(tenants, name):
    serializer_class, field_names = NO_ORGANIZATION_FIELDS[name]
    serializer = serializer_class(
        context={"request": _request_without_organization(tenants["b"])}
    )

    for field_name in field_names:
        field = serializer.fields[field_name]
        queryset = getattr(field, "child_relation", field).get_queryset()
        # .none(): the query is empty before it reaches the database.
        assert queryset.query.is_empty(), (name, field_name)
        assert not queryset.exists(), (name, field_name)

    relation = serializer.fields["project"]
    with pytest.raises(ValidationError) as own_error:
        relation.to_internal_value(str(tenants["b_rows"]["project"].id))
    assert own_error.value.get_codes() == ["does_not_exist"]


def _monitor_view(request):
    view = UserAlertMonitorView()
    view.request = request
    view.format_kwarg = None
    view.kwargs = {}
    return view


def test_monitor_view_resolves_the_organization_authentication_resolved(tenants):
    own = _monitor_view(_request(tenants["b"]))
    without = _monitor_view(_request_without_organization(tenants["b"]))
    # An unauthenticated request (drf-yasg's view during schema generation).
    anonymous = _monitor_view(Request(APIRequestFactory().get("/")))

    assert own._current_organization() == tenants["b"].organization
    assert without._current_organization() is None
    assert not without._visible_observe_projects().exists()
    assert anonymous._current_organization() is None

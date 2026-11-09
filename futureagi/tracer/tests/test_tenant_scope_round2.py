"""Cloud-only regressions: real ORM authorization, no provider calls.

Compose this tests-only commit onto the round-1 head to replay RED. Socket
isolation and same-org A/B ordinary-member fixtures come from round 1.
"""

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ObjectDoesNotExist

from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from tfc.middleware.workspace_context import set_workspace_context
from tfc.utils.tests import test_tenant_scope_fail_closed as scope_fixtures
from tfc.utils.tests.test_tenant_scope_fail_closed import request_for
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

isolated_scope = scope_fixtures.isolated_scope
tenants = scope_fixtures.tenants
pytestmark = pytest.mark.django_db
STATES = ("own", "other", "missing", "revoked", "inactive")
SOURCES = ("span", "trace", "session", "dataset_row", "cell")


@pytest.fixture
def resources(tenants):
    rows = {}
    for label in ("a", "b"):
        project = tenants.rows[label]["eval_configs"].project
        workspace = tenants.workspaces[label]
        template = EvalTemplate.no_workspace_objects.create(
            name=f"round2-{label}",
            organization=tenants.org,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
        )
        group = EvalGroup.no_workspace_objects.create(
            name=f"round2-{label}", organization=tenants.org, workspace=workspace
        )
        session = TraceSession.no_workspace_objects.create(project=project)
        trace = Trace.no_workspace_objects.create(project=project, session=session)
        span = ObservationSpan.no_workspace_objects.create(
            id=f"round2-{label}",
            project=project,
            trace=trace,
            observation_type="llm",
            name=label,
        )
        dataset = Dataset.no_workspace_objects.create(
            name=f"round2-{label}", organization=tenants.org, workspace=workspace
        )
        column = Column.no_workspace_objects.create(
            name="text", dataset=dataset, data_type="text", source="OTHERS"
        )
        row = Row.no_workspace_objects.create(dataset=dataset, order=1)
        cell = Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=column, value="test"
        )
        config = tenants.rows[label]["eval_configs"]
        version = ProjectVersion.no_workspace_objects.create(
            project=project,
            name=label,
            version="v1",
            eval_tags=[{"custom_eval_config_id": str(config.pk), "value": "llm"}],
        )
        rows[label] = {
            "template": template,
            "group": group,
            "span": span,
            "trace": trace,
            "session": session,
            "dataset_row": row,
            "cell": cell,
            "version": version,
            "config": config,
        }
    return rows


def caller(tenants, state):
    request = request_for(tenants, None if state == "missing" else "a")
    if state == "revoked":
        WorkspaceMembership.no_workspace_objects.filter(
            user=tenants.user, workspace=tenants.workspaces["a"]
        ).update(is_active=False)
    if state == "inactive":
        Workspace.no_workspace_objects.filter(pk=tenants.workspaces["a"].pk).update(
            is_active=False
        )
    # Bind only the actor: managers must not mask a consumer's missing filter.
    set_workspace_context(user=tenants.user)
    return request


def target(resources, state, kind):
    return resources["b" if state == "other" else "a"][kind]


@pytest.mark.parametrize("state", STATES)
def test_search_requires_live_workspace(tenants, state):
    from ai_tools.tools.context.search import _in_caller_scope

    request = caller(tenants, state)
    project = tenants.rows["b" if state == "other" else "a"]["eval_configs"].project
    rows = _in_caller_scope(Project.no_workspace_objects.all(), request)
    assert rows.filter(pk=project.pk).exists() is (state == "own")


@pytest.mark.parametrize(
    "consumer", ("picker", "relation", "simulate_template", "simulate_group")
)
@pytest.mark.parametrize("state", STATES)
def test_template_and_group_consumers_require_live_workspace(
    tenants, resources, consumer, state
):
    from simulate.services.eval_config_edit import (
        unknown_eval_group_ids,
        visible_eval_template_query,
    )
    from tracer.serializers.custom_eval_config import caller_eval_templates
    from tracer.serializers.relation_scope import eval_template_queryset

    request = caller(tenants, state)
    selected = target(
        resources, state, "group" if consumer == "simulate_group" else "template"
    )
    if consumer == "simulate_group":
        permitted = not unknown_eval_group_ids(
            [selected.pk], tenants.org, request.workspace
        )
    else:
        if consumer == "picker":
            rows = caller_eval_templates(request)
        elif consumer == "relation":
            rows = eval_template_queryset(request)
        else:
            rows = EvalTemplate.no_workspace_objects.filter(
                visible_eval_template_query(tenants.org, request.workspace)
            )
        permitted = rows.filter(pk=selected.pk).exists()
    assert permitted is (state == "own")


@pytest.mark.parametrize("state", ("own", "missing", "revoked"))
def test_explicit_global_templates_and_sample_groups_remain_visible(tenants, state):
    from simulate.services.eval_config_edit import (
        unknown_eval_group_ids,
        visible_eval_template_query,
    )
    from tracer.serializers.custom_eval_config import caller_eval_templates
    from tracer.serializers.relation_scope import eval_template_queryset

    request = caller(tenants, state)
    template = EvalTemplate.no_workspace_objects.create(
        name="round2-system", owner=OwnerChoices.SYSTEM.value, organization=None
    )
    sample = EvalGroup.no_workspace_objects.create(name="round2-sample", is_sample=True)
    assert caller_eval_templates(request).filter(pk=template.pk).exists()
    assert eval_template_queryset(request).filter(pk=template.pk).exists()
    assert EvalTemplate.no_workspace_objects.filter(
        visible_eval_template_query(tenants.org, request.workspace), pk=template.pk
    ).exists()
    assert not unknown_eval_group_ids([sample.pk], tenants.org, request.workspace)


@pytest.fixture
def no_ch(monkeypatch):
    import tracer.services.clickhouse.v2 as ch
    import tracer.services.clickhouse.v2.trace_session_dict_reader as sessions

    reader = MagicMock()
    reader.scope_by_ids.return_value = {}
    reader.list_by_project.return_value = []
    factory = MagicMock()
    factory.return_value.__enter__.return_value = reader
    monkeypatch.setattr(ch, "get_reader", factory)
    resolver = MagicMock(return_value={})
    monkeypatch.setattr(sessions, "resolve_session_fields", resolver)
    return SimpleNamespace(reader=reader, factory=factory, resolver=resolver)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("state", STATES)
def test_evaluation_source_requires_live_workspace(
    tenants, resources, no_ch, source, state
):
    from ai_tools.tools.evaluations.evaluate_with_agent import _source_in_scope

    request = caller(tenants, state)
    selected = target(resources, state, source)
    assert _source_in_scope(
        source, str(selected.pk), tenants.org, request.workspace
    ) is (state == "own")


@pytest.mark.parametrize("source", ("span", "session"))
@pytest.mark.parametrize("state", STATES)
def test_clickhouse_project_allowlist_requires_live_workspace(
    tenants, no_ch, source, state
):
    from ai_tools.tools.evaluations.evaluate_with_agent import _ch_source_in_scope

    request = caller(tenants, state)
    project = tenants.rows["b" if state == "other" else "a"]["eval_configs"].project
    seen = []

    def scoped(ids, *, project_ids):
        seen.append(set(project_ids))
        return {"ch-only": {}} if project.pk in project_ids else {}

    no_ch.reader.scope_by_ids.side_effect = scoped
    no_ch.resolver.side_effect = scoped
    assert _ch_source_in_scope(source, "ch-only", tenants.org, request.workspace) is (
        state == "own"
    )
    if state in ("missing", "revoked", "inactive"):
        assert seen == []
        no_ch.factory.assert_not_called()
    else:
        assert seen == [{tenants.rows["a"]["eval_configs"].project_id}]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("state", STATES)
def test_orchestrator_inspection_requires_live_workspace(
    tenants, resources, source, state
):
    from ee.agenthub.eval_orchestrator.orchestrator import EvalOrchestrator

    request = caller(tenants, state)
    selected = target(resources, state, source)
    # Bypass construction of provider clients; execute the real inspection/ORM.
    orchestrator = object.__new__(EvalOrchestrator)
    orchestrator.eval_config = SimpleNamespace(
        organization_id=str(tenants.org.pk),
        workspace=request.workspace,
        user=tenants.user,
    )
    orchestrator._org_id = str(tenants.org.pk)
    orchestrator._detected_media = []
    method = getattr(orchestrator, f"_inspect_{source}")
    args = (str(selected.pk),) if source == "cell" else (str(selected.pk), "overview")
    if state == "own":
        assert method(*args)
    else:
        with pytest.raises(ObjectDoesNotExist):
            method(*args)


def config_view(request):
    from tracer.views.custom_eval_config import CustomEvalConfigView

    view = CustomEvalConfigView()
    view.request = request
    view.kwargs = {}
    view.format_kwarg = None
    return view


@pytest.mark.parametrize("state", STATES)
def test_list_configs_never_exposes_other_workspace(
    tenants, resources, monkeypatch, state
):
    import tracer.views.custom_eval_config as module

    request = caller(tenants, state)
    selected = target(resources, state, "config")
    request.query_params = {"project_id": str(selected.project_id)}
    monkeypatch.setattr(module, "DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST", "default")
    view = config_view(request)
    response = inspect.unwrap(module.CustomEvalConfigView.list_custom_eval_configs)(
        view, request
    )
    assert response.status_code == 200, response.data
    ids = {str(row["id"]) for row in response.data["result"]}
    assert ids == ({str(selected.pk)} if state == "own" else set())


@pytest.mark.parametrize("path", ("action", "viewset"))
@pytest.mark.parametrize("state", ("own", "missing"))
def test_unfiltered_config_lists_require_workspace(tenants, monkeypatch, path, state):
    import tracer.views.custom_eval_config as module

    if state == "missing":
        WorkspaceMembership.no_workspace_objects.filter(user=tenants.user).update(
            is_active=False
        )
    request = caller(tenants, state)
    assert tenants.user.can_access_organization(tenants.org)
    request.query_params = {}
    monkeypatch.setattr(module, "DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST", "default")
    view = config_view(request)
    if path == "action":
        response = inspect.unwrap(module.CustomEvalConfigView.list_custom_eval_configs)(
            view, request
        )
        assert response.status_code == 200, response.data
        rows = response.data["result"]
    else:
        view.action = "list"
        # Inspect every row; a page boundary must not conceal a leaked B row.
        view.pagination_class = None
        response = view.list(request)
        assert response.status_code == 200, response.data
        rows = response.data
    ids = {str(row["id"]) for row in rows}
    expected = {str(tenants.rows["a"]["eval_configs"].pk)} if state == "own" else set()
    assert ids == expected
    assert str(tenants.rows["b"]["eval_configs"].pk) not in ids


@pytest.mark.parametrize("field", ("config", "version"))
@pytest.mark.parametrize("state", ("other", "missing", "revoked", "inactive"))
def test_run_configs_and_versions_match_unknown_404(
    tenants, resources, no_ch, monkeypatch, field, state
):
    import tracer.views.custom_eval_config as module

    request = caller(tenants, state)
    own = resources["a"]
    selected = target(resources, state, field)
    key = "custom_eval_config_id" if field == "config" else "project_version_id"
    request.data = {
        "custom_eval_config_id": str(own["config"].pk),
        "project_version_id": str(target(resources, state, "version").pk),
        key: str(selected.pk),
    }
    if field == "version":
        # A valid tag must not mask missing authorization of B's version.
        ProjectVersion.no_workspace_objects.filter(pk=selected.pk).update(
            eval_tags=[{"custom_eval_config_id": str(own["config"].pk), "value": "llm"}]
        )
    # Without the fix these are runnable pairs, not incidental tag failures.
    no_ch.reader.list_by_project.return_value = [
        SimpleNamespace(id=target(resources, state, "span").pk)
    ]
    monkeypatch.setattr(module, "close_old_connections", lambda: None)
    enqueue = MagicMock()
    monkeypatch.setattr(module.evaluate_observation_span, "delay", enqueue)
    view = config_view(request)
    response = view.run_evaluation(request)
    unknown_id = str(uuid.uuid4())
    request.data[key] = unknown_id
    unknown = view.run_evaluation(request)
    assert response.status_code == unknown.status_code == 404
    assert str(response.data).replace(str(selected.pk), "<id>") == str(
        unknown.data
    ).replace(unknown_id, "<id>")
    no_ch.factory.assert_not_called()
    enqueue.assert_not_called()


def test_run_own_config_and_version_still_enqueue(
    tenants, resources, no_ch, monkeypatch
):
    import tracer.views.custom_eval_config as module

    request = caller(tenants, "own")
    own = resources["a"]
    request.data = {
        "custom_eval_config_id": str(own["config"].pk),
        "project_version_id": str(own["version"].pk),
    }
    monkeypatch.setattr(module, "close_old_connections", lambda: None)
    enqueue = MagicMock()
    monkeypatch.setattr(module.evaluate_observation_span, "delay", enqueue)
    no_ch.reader.list_by_project.return_value = [SimpleNamespace(id=own["span"].pk)]
    response = config_view(request).run_evaluation(request)
    assert response.status_code == 200, response.data
    enqueue.assert_called_once_with(own["span"].pk, own["config"].pk)
    assert no_ch.reader.list_by_project.call_args.args == (
        str(own["config"].project_id),
    )

"""Related ids follow the request's organization and workspace on every instance."""

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers

from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

pytestmark = pytest.mark.django_db


@pytest.fixture
def scopes(settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    a, b = make_tenant("A"), make_tenant("B")
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        organization=a.organization,
        name="Second workspace",
        is_default=False,
        is_active=True,
        created_by=a.user,
    )
    a2 = SimpleNamespace(organization=a.organization, workspace=workspace, user=a.user)
    return a, a2, b


def request_for(scope):
    return SimpleNamespace(
        organization=scope.organization, workspace=scope.workspace, user=scope.user
    )


def resources(scope):
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        name=f"Project {uuid.uuid4()}",
        trace_type="observe",
        model_type="GenerativeLLM",
    )
    template = EvalTemplate.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        name=f"template-{uuid.uuid4()}",
        owner=OwnerChoices.USER.value,
        config={"required_keys": []},
    )
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        name="Reference",
    )
    config = CustomEvalConfig.no_workspace_objects.create(
        project=project,
        eval_template=template,
        name="Config",
        mapping={},
        config={},
    )
    trace = Trace.no_workspace_objects.create(project=project, name="Trace")
    span = ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Span",
        observation_type="llm",
    )
    version = ProjectVersion.no_workspace_objects.create(project=project, name="v1")
    session = TraceSession.no_workspace_objects.create(project=project, name="Session")
    task = EvalTask.no_workspace_objects.create(project=project, name="Task")
    prompt = PromptTemplate.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        created_by=scope.user,
        name="Prompt",
    )
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        prompt_config_snapshot=[],
    )
    label = AnnotationsLabels.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        name=f"Label {uuid.uuid4()}",
        type="thumbs_up_down",
    )
    annotations = Annotations.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        name="Annotations",
    )
    return dict(
        annotations=annotations,
        span=span,
        project=project,
        eval_template=template,
        kb_id=kb,
        custom_eval_config=config,
        evals=config,
        trace=trace,
        observation_span=span,
        project_version=version,
        session=session,
        eval_task=task,
        prompt_version=prompt_version,
        annotation_label=label,
    )


CASES = [
    ("custom_eval_config.CustomEvalConfigSerializer", "project"),
    ("custom_eval_config.CustomEvalConfigSerializer", "eval_template"),
    ("custom_eval_config.CustomEvalConfigSerializer", "kb_id"),
    ("eval_task.EvalTaskSerializer", "project"),
    ("eval_task.EvalTaskSerializer", "evals"),
    ("eval_task.EvalTaskLoggerSerializer", "eval_task"),
    ("observation_span.ObservationSpanSerializer", "project"),
    ("observation_span.ObservationSpanSerializer", "trace"),
    ("observation_span.ObservationSpanSerializer", "project_version"),
    ("observation_span.ObservationSpanSerializer", "custom_eval_config"),
    ("observation_span.ObservationSpanSerializer", "prompt_version"),
    ("trace.TraceSerializer", "project"),
    ("trace.TraceSerializer", "project_version"),
    ("trace.TraceSerializer", "session"),
    ("trace_session.TraceSessionSerializer", "project"),
    ("project_version.ProjectVersionSerializer", "project"),
    ("project_version.ProjectVersionSerializer", "annotations"),
    ("annotation.SpanNotesSerializer", "span"),
    ("monitor.UserAlertMonitorSerializer", "project"),
    ("annotation.TraceAnnotationSerializer", "trace"),
    ("annotation.TraceAnnotationSerializer", "annotation_label"),
    ("annotation.TraceAnnotationSerializer", "observation_span"),
]


def serializer_type(path):
    module, name = path.split(".")
    return getattr(importlib.import_module(f"tracer.serializers.{module}"), name)


@pytest.mark.parametrize("path,field_name", CASES)
@pytest.mark.parametrize("other_index", [1, 2], ids=["workspace", "organization"])
def test_relation_denial_and_own_scope_control(scopes, path, field_name, other_index):
    own, other = resources(scopes[0]), resources(scopes[other_index])
    cls = serializer_type(path)
    # Instantiate twice with different requests; class-import state must not bind either.
    for scope, allowed, refused in (
        (scopes[0], own, other),
        (scopes[other_index], other, own),
    ):
        clear_workspace_context()
        field = cls(context={"request": request_for(scope)}).fields[field_name]

        def value(obj):
            identifier = str(obj.pk)
            return [identifier] if field_name == "evals" else identifier

        with CaptureQueriesContext(connection) as queries:
            result = field.run_validation(value(allowed[field_name]))
        assert result == (
            [allowed[field_name]] if field_name == "evals" else allowed[field_name]
        )
        assert len(queries) <= 2
        with pytest.raises(serializers.ValidationError):
            field.run_validation(value(refused[field_name]))


@pytest.mark.parametrize("path,field_name", CASES)
def test_relations_require_request_scope(scopes, path, field_name):
    own = resources(scopes[0])
    clear_workspace_context()
    cls = serializer_type(path)
    for context in (
        {},
        {
            "request": SimpleNamespace(
                organization=None, workspace=None, user=scopes[0].user
            )
        },
    ):
        field = cls(context=context).fields[field_name]
        identifier = str(own[field_name].pk)
        with pytest.raises(serializers.ValidationError):
            field.run_validation([identifier] if field_name == "evals" else identifier)


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("field_name", ["project", "kb_id", "eval_template"])
@pytest.mark.parametrize("other_index", [1, 2], ids=["workspace", "organization"])
def test_config_create_and_update_resolve_relations(scopes, field_name, other_index, method):
    a = scopes[0]
    own, other = resources(a), resources(scopes[other_index])
    payload = dict(
        name="Request config",
        project=str(own["project"].pk),
        eval_template=str(own["eval_template"].pk),
        kb_id=str(own["kb_id"].pk),
        mapping={},
        config={},
    )
    clear_workspace_context()
    created = a.client.post("/tracer/custom-eval-config/", payload, format="json")
    assert created.status_code == 200, created.content
    config = CustomEvalConfig.no_workspace_objects.get(
        name="Request config", project=own["project"]
    )
    payload[field_name] = str(other[field_name].pk)
    payload["name"] = f"Other request {uuid.uuid4()}"
    before = CustomEvalConfig.no_workspace_objects.count()
    clear_workspace_context()
    if method == "post":
        denied = a.client.post("/tracer/custom-eval-config/", payload, format="json")
    else:
        denied = a.client.patch(
            f"/tracer/custom-eval-config/{config.pk}/",
            {field_name: str(other[field_name].pk)},
            format="json",
        )
    assert denied.status_code in (400, 403, 404), denied.content
    config.refresh_from_db()
    assert getattr(config, field_name).pk == own[field_name].pk
    assert CustomEvalConfig.no_workspace_objects.count() == before


@pytest.mark.parametrize("other_index", [1, 2])
def test_orchestrator_uses_connected_scoped_kb(scopes, other_index):
    from ee.agenthub.eval_orchestrator.orchestrator import EvalOrchestrator

    a = scopes[0]
    own, other = resources(a), resources(scopes[other_index])
    fake = SimpleNamespace(
        eval_config=SimpleNamespace(
            kb_id=str(own["kb_id"].pk),
            organization_id=str(a.organization.pk),
            workspace=a.workspace,
        ),
        _resources_used=[],
    )
    clear_workspace_context()
    with patch("model_hub.utils.kb_indexer.KBIndexer") as indexer:
        indexer.return_value.search.return_value = []
        with CaptureQueriesContext(connection) as queries:
            result = EvalOrchestrator._tool_search_kb(
                fake, "query", kb_id=str(other["kb_id"].pk), top_k=10000
            )
        assert result.get("error") is None
        assert indexer.return_value.search.call_args.kwargs["kb_id"] == str(
            own["kb_id"].pk
        )
        assert 1 <= indexer.return_value.search.call_args.kwargs["top_k"] <= 50
        assert 1 <= len(queries) <= 2
        indexer.reset_mock()
        for invalid in (str(other["kb_id"].pk), str(uuid.uuid4()), None, "invalid"):
            fake.eval_config.kb_id = invalid
            result = EvalOrchestrator._tool_search_kb(
                fake, "query", kb_id=str(own["kb_id"].pk)
            )
            assert result.get("error")
        indexer.return_value.search.assert_not_called()


def test_resource_summary_is_scoped_and_bounded(scopes):
    from ee.agenthub.eval_orchestrator.utils import gather_available_resources

    a = scopes[0]
    # Use the public call shape shared with older implementations; a foreign
    # organization must be refused even without an explicit workspace argument.
    all_resources = [resources(scopes[0]), resources(scopes[2])]
    for index, rows in enumerate(all_resources):
        clear_workspace_context()
        with CaptureQueriesContext(connection) as queries:
            result = gather_available_resources(
                str(a.organization.pk),
                kb_id=str(rows["kb_id"].pk),
            )
        expected = [str(rows["kb_id"].pk)] if index == 0 else []
        assert [item["id"] for item in result["knowledge_bases"]] == expected
        kb_queries = [
            q["sql"] for q in queries if KnowledgeBaseFile._meta.db_table in q["sql"]
        ]
        assert len(kb_queries) == 1
        assert "LIMIT" in kb_queries[0].upper()

"""Consumer call-shape regressions over real feedback readers and vector SQL.

Reuse the isolated ClickHouse fixture from test_feedback_rag_workspace_scope.
Only inference, provider construction, billing and unrelated result persistence
are stubbed; runner mapping, engine preparation and feedback SQL stay real.
"""

import uuid
from copy import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_django.django_compat import is_django_unittest
from pytest_django.fixtures import validate_django_db

import model_hub.tasks  # noqa: F401 - break the tracer eval/tasks import cycle
from model_hub.tests.test_feedback_rag_workspace_scope import feedback_store  # noqa: F401
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# EmbeddingManager.close() closes Django connections; use committed fixtures,
# not an enclosing atomic transaction that would be broken by the real reader.
pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def _django_db_helper(request, django_db_setup, django_db_blocker):
    """pytest-django 4.11.1 lifecycle with isolated transactional flush only.

    Keep the django_db markers: the plugin discovers database aliases from them
    before this per-test fixture runs. Re-export this fixture in consuming modules
    rather than changing the plugin or granting erase authority to test bodies.
    """
    if is_django_unittest(request):
        yield
        return

    marker = request.node.get_closest_marker("django_db")
    if marker:
        (
            transactional,
            reset_sequences,
            databases,
            serialized_rollback,
            available_apps,
        ) = validate_django_db(marker)
    else:
        (
            transactional,
            reset_sequences,
            databases,
            serialized_rollback,
            available_apps,
        ) = False, False, None, False, None

    transactional = (
        transactional
        or reset_sequences
        or (
            "transactional_db" in request.fixturenames
            or "live_server" in request.fixturenames
        )
    )
    reset_sequences = reset_sequences or (
        "django_db_reset_sequences" in request.fixturenames
    )
    serialized_rollback = serialized_rollback or (
        "django_db_serialized_rollback" in request.fixturenames
    )

    with django_db_blocker.unblock():
        import django.test

        if transactional:
            from accounts.tests.test_hard_erase_db import IsolatedEraseTestCase

            test_case_class = IsolatedEraseTestCase
        else:
            test_case_class = django.test.TestCase

        _reset_sequences = reset_sequences
        _serialized_rollback = serialized_rollback
        _databases = databases
        _available_apps = available_apps

        class PytestDjangoTestCase(test_case_class):
            reset_sequences = _reset_sequences
            serialized_rollback = _serialized_rollback
            if _databases is not None:
                databases = _databases
            if _available_apps is not None:
                available_apps = _available_apps

            # Match the plugin's ordinary TestCase rollback path, including its
            # omission of class-level atomics and connection-closing teardown.
            if not transactional:

                @classmethod
                def setUpClass(cls):
                    super(django.test.TestCase, cls).setUpClass()

                @classmethod
                def tearDownClass(cls):
                    super(django.test.TestCase, cls).tearDownClass()

        PytestDjangoTestCase.setUpClass()
        test_case = PytestDjangoTestCase(methodName="__init__")
        test_case._pre_setup()
        yield
        test_case._post_teardown()
        PytestDjangoTestCase.tearDownClass()
        PytestDjangoTestCase.doClassCleanups()


@pytest.fixture
def consumer_scope(
    feedback_store,  # noqa: F811
    organization,
    workspace,
    urlconf_loaded_without_workspace_context,  # noqa: F811
):
    from accounts.models.workspace import Workspace
    from model_hub.models.ai_model import AIModel
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    scope = feedback_store
    scope.organization = organization
    scope.workspace = workspace
    scope.organization_id = str(organization.id)
    scope.workspace_id = str(workspace.id)
    scope.foreign_workspace = Workspace.objects.create(
        organization=organization,
        name=f"foreign-{uuid.uuid4().hex}",
        is_active=True,
        created_by_id=workspace.created_by_id,
    )
    scope.foreign_workspace_id = str(scope.foreign_workspace.id)
    scope.template = EvalTemplate.all_objects.create(
        id=scope.eval_id,
        name=f"feedback-consumer-{uuid.uuid4().hex}",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["output"],
            "output": "score",
        },
    )
    scope.project = Project.all_objects.create(
        name=f"feedback-project-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    scope.trace = Trace.all_objects.create(
        project=scope.project, input="question", output="current case"
    )
    # A hostile default-workspace ambient context must not widen explicit scope.
    ambient_workspace = copy(scope.foreign_workspace)
    ambient_workspace.is_default = True
    set_workspace_context(workspace=ambient_workspace, organization=organization)
    try:
        yield scope
    finally:
        clear_workspace_context()


class _Recorder:
    def __init__(self):
        self.calls = []
        self.cost = {}
        self.token_usage = {}

    def run(self, **params):
        self.calls.append(params)
        return SimpleNamespace(
            eval_results=[
                {
                    "data": {"output": "current case"},
                    "failure": False,
                    "reason": "evaluated",
                    "runtime": 0.01,
                    "model": "test-model",
                    "metrics": [{"id": "score", "value": 1.0}],
                    "metadata": {},
                }
            ]
        )


@pytest.fixture
def standalone_recorder(monkeypatch):
    from ee.usage.services import metering
    from model_hub.services.ground_truth_service import GroundTruthService
    from model_hub.views.eval_runner import EvaluationRunner
    from model_hub.views.utils import evals
    from tracer.utils import external_eval

    recorder = _Recorder()
    monkeypatch.setattr(
        EvaluationRunner, "_create_eval_instance", lambda self, **kwargs: recorder
    )
    monkeypatch.setattr(
        external_eval, "_log_and_deduct_cost_for_external_eval", lambda *args: None
    )
    monkeypatch.setattr(external_eval, "_send_eval_result", lambda *args: None)
    monkeypatch.setattr(evals, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(
        metering, "check_usage", lambda *args: SimpleNamespace(allowed=True)
    )
    monkeypatch.setattr(
        GroundTruthService, "inject_context", lambda params, *args, **kwargs: params
    )
    monkeypatch.setattr(
        GroundTruthService, "resolve_preview_examples", lambda **kwargs: []
    )
    return recorder


def _standalone_call(consumer, scope, recorder, *, workspace, template=None):
    from model_hub.views.utils.evals import run_eval_func
    from tracer.utils.external_eval import _execute_evaluation_on_external_platform

    template = template or scope.template
    before = len(recorder.calls)
    if consumer == "external":
        config = SimpleNamespace(
            id=uuid.uuid4(),
            platform="test-platform",
            eval_template=template,
            organization=scope.organization,
            workspace=workspace,
            config={},
            model="turing_large",
            mapping={"output": "current case"},
            save=Mock(),
        )
        _execute_evaluation_on_external_platform(config)
    else:
        result = run_eval_func(
            {},
            {"output": "current case"},
            template,
            scope.organization,
            workspace=workspace,
            resolved_version=SimpleNamespace(id=uuid.uuid4(), version_number=1),
        )
        assert result.get("output") == 1.0, "SETUP_AQ-XWS-FBC: preview evaluated"
    assert len(recorder.calls) == before + 1, "SETUP_AQ-XWS-FBC: evaluator reached"
    return recorder.calls[-1]["few_shots"]


@pytest.mark.parametrize("consumer", ["external", "preview"])
def test_standalone_production_call_shape_is_workspace_bound(
    consumer_scope, standalone_recorder, consumer
):
    scope = consumer_scope
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    scope.insert(
        "other-org",
        workspace_id=scope.workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    scope.insert(
        "other-org-null",
        workspace_id=None,
        organization_id=scope.foreign_organization_id,
    )
    result = _standalone_call(
        consumer, scope, standalone_recorder, workspace=scope.workspace
    )
    # Same-organization feedback saved with no workspace before the release
    # cutoff is admitted.
    assert (
        "local-comment" in str(result)
        and "legacy-null-comment" in str(result)
        and all(
            marker not in str(result)
            for marker in ("foreign-", "other-org-", "after-cutoff-null-")
        )
    ), (
        "standalone evaluator-type callers retrieve admitted workspace "
        "feedback and same-org pre-cutoff no-workspace feedback only"
    )

    foreign_only = copy(scope.template)
    foreign_only.id = uuid.uuid4()
    scope.insert(
        "foreign-only",
        workspace_id=scope.foreign_workspace_id,
        eval_id=str(foreign_only.id),
    )
    absent = copy(scope.template)
    absent.id = uuid.uuid4()
    foreign = _standalone_call(
        consumer,
        scope,
        standalone_recorder,
        workspace=scope.workspace,
        template=foreign_only,
    )
    nonexistent = _standalone_call(
        consumer,
        scope,
        standalone_recorder,
        workspace=scope.workspace,
        template=absent,
    )
    unbound = _standalone_call(consumer, scope, standalone_recorder, workspace=None)
    assert foreign == nonexistent == unbound == [], (
        "SETUP_AQ-XWS-FBC: foreign nonexistent and unbound feedback share empty shape"
    )


@pytest.mark.parametrize("target", ["trace", "session"])
def test_null_project_workspace_evaluates_without_default_feedback(
    consumer_scope, monkeypatch, target
):
    from evaluations.engine import runner as engine_runner
    from tracer.models.project import Project
    from tracer.models.trace_session import TraceSession
    from tracer.utils import eval as trace_eval

    scope = consumer_scope
    scope.insert("default", workspace_id=scope.workspace_id)
    assert scope.workspace.is_default, "SETUP_AQ-XWS-FBC: matching default workspace"
    recorder = _Recorder()
    provider_scopes = []

    def create_instance(**kwargs):
        provider_scopes.append(kwargs["workspace_id"])
        return recorder, None

    monkeypatch.setattr(engine_runner, "create_eval_instance", create_instance)
    monkeypatch.setattr(trace_eval, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(trace_eval, "_emit_eval_billing", lambda **kwargs: None)
    monkeypatch.setattr(trace_eval, "_stamp_eval_version", lambda *args: None)
    monkeypatch.setattr(
        trace_eval, "_inject_ground_truth", lambda params, *args, **kwargs: params
    )
    monkeypatch.setattr(trace_eval, "_collect_run_warnings", lambda *args, **kwargs: [])
    logs = []
    monkeypatch.setattr(trace_eval, "_persist_eval_logger", logs.append)
    config = SimpleNamespace(
        id=uuid.uuid4(),
        name="consumer",
        eval_template=scope.template,
        config={"run_config": {"data_injection": {}}},
        model="turing_large",
        kb_id=None,
    )
    session = TraceSession.all_objects.create(project=scope.project)

    def evaluate():
        if target == "trace":
            trace_eval._execute_evaluation_for_trace(
                trace=scope.trace,
                anchor_span=SimpleNamespace(id=uuid.uuid4()),
                custom_eval_config=config,
                eval_task_id=None,
                run_params={"output": "current case"},
            )
        else:
            trace_eval._execute_evaluation_for_session(
                trace_session=session,
                custom_eval_config=config,
                eval_task_id=None,
                run_params={"output": "current case"},
            )

    evaluate()
    assert recorder.calls and "default-comment" in str(recorder.calls[-1]), (
        "SETUP_AQ-XWS-FBC: bound trace or session retrieves default workspace feedback"
    )
    Project.all_objects.filter(id=scope.project.id).update(workspace=None)
    scope.project.refresh_from_db()
    scope.trace.project = scope.project
    session.project = scope.project
    assert scope.project.workspace_id is None, (
        "SETUP_AQ-XWS-FBC: persisted NULL binding"
    )
    evaluate()
    assert len(recorder.calls) == 2 and not logs[-1].get("error"), (
        "SETUP_AQ-XWS-FBC: NULL-workspace project still evaluates"
    )
    assert recorder.calls[-1]["few_shots"] == [], (
        "AQ-XWS-FBC: NULL-project trace and session runs must not retrieve default workspace feedback"
    )
    assert provider_scopes == [scope.workspace_id, scope.workspace_id], (
        "SETUP_AQ-XWS-FBC: provider default-workspace fallback remains unchanged"
    )


def _orchestrator(scope, *, workspace, template_id=None, source_id=None):
    from ee.agenthub.eval_orchestrator.orchestrator import EvalConfig, EvalOrchestrator

    # The existing orchestrator tests use __new__ to avoid provider setup.
    # No feedback method, input resolver, ORM manager or SQL reader is patched.
    orchestrator = EvalOrchestrator.__new__(EvalOrchestrator)
    orchestrator.eval_config = EvalConfig(
        criteria="Evaluate output",
        input_scope="trace",
        source_id=str(source_id or scope.trace.id),
        eval_template_id=str(template_id or scope.template.id),
        organization_id=scope.organization_id,
        workspace=workspace,
    )
    orchestrator.scout_brief = {"available_resources": {"feedback_count": 1}}
    orchestrator._resources_used = []
    return orchestrator


def test_orchestrator_resource_discovery_uses_admitted_workspace(consumer_scope):
    from ee.agenthub.eval_orchestrator.utils import gather_available_resources

    scope = consumer_scope
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)

    def resources(eval_id, workspace, organization_id=scope.organization_id):
        return gather_available_resources(
            organization_id=organization_id,
            eval_template_id=str(eval_id),
            workspace=workspace,
        )

    local = resources(scope.eval_id, scope.workspace)
    orchestrator = _orchestrator(scope, workspace=scope.workspace)
    orchestrator.scout_brief = {"available_resources": local}
    tools = orchestrator._build_tool_list()
    # Counts the local row and the same-org pre-cutoff no-workspace row.
    assert local["feedback_count"] == 2 and any(
        tool["function"]["name"] == "get_feedback_examples" for tool in tools
    ), "orchestrator discovery counts admitted feedback"

    foreign_id = uuid.uuid4()
    scope.insert(
        "foreign-only", workspace_id=scope.foreign_workspace_id, eval_id=str(foreign_id)
    )
    foreign = resources(foreign_id, scope.workspace)["feedback_count"]
    nonexistent = resources(uuid.uuid4(), scope.workspace)["feedback_count"]
    unbound = resources(scope.eval_id, None)["feedback_count"]
    mismatched = resources(
        scope.eval_id, scope.workspace, scope.foreign_organization_id
    )["feedback_count"]
    assert foreign == nonexistent == unbound == mismatched == 0, (
        "SETUP_AQ-XWS-FBC: discovery foreign nonexistent unbound and mismatched shape"
    )


def test_orchestrator_feedback_tool_uses_admitted_workspace(consumer_scope):
    from model_hub.models.evals_metric import EvalTemplate

    scope = consumer_scope
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    orchestrator = _orchestrator(scope, workspace=scope.workspace)
    result = orchestrator._tool_get_feedback(str(uuid.uuid4()))
    # Same-organization feedback saved with no workspace before the cutoff is read.
    assert (
        result["count"] > 0
        and "local-comment" in str(result)
        and "legacy-null-comment" in str(result)
        and all(
            marker not in str(result)
            for marker in ("foreign-", "after-cutoff-null-")
        )
    ), "orchestrator tool retrieves admitted and pre-cutoff feedback only"

    foreign_template = EvalTemplate.all_objects.create(
        name=f"foreign-feedback-{uuid.uuid4().hex}",
        config=dict(scope.template.config),
    )
    scope.insert(
        "foreign-only",
        workspace_id=scope.foreign_workspace_id,
        eval_id=str(foreign_template.id),
    )
    foreign = _orchestrator(
        scope, workspace=scope.workspace, template_id=foreign_template.id
    )._tool_get_feedback(str(scope.eval_id))
    nonexistent = _orchestrator(
        scope, workspace=scope.workspace, template_id=uuid.uuid4()
    )._tool_get_feedback(str(scope.eval_id))
    unbound = _orchestrator(scope, workspace=None)._tool_get_feedback(
        str(scope.eval_id)
    )
    missing_org = _orchestrator(scope, workspace=scope.workspace)
    missing_org.eval_config.organization_id = None
    missing_org_result = missing_org._tool_get_feedback(str(scope.eval_id))
    mismatched = _orchestrator(scope, workspace=scope.workspace)
    mismatched.eval_config.organization_id = scope.foreign_organization_id
    mismatched_result = mismatched._tool_get_feedback(str(scope.eval_id))
    foreign_source = copy(scope.trace)
    foreign_source.pk = uuid.uuid4()
    foreign_project = copy(scope.project)
    foreign_project.pk = uuid.uuid4()
    foreign_project.name = f"foreign-project-{uuid.uuid4().hex}"
    foreign_project.workspace = scope.foreign_workspace
    foreign_project.save(force_insert=True)
    foreign_source.project = foreign_project
    foreign_source.save(force_insert=True)
    foreign_record = _orchestrator(
        scope, workspace=scope.workspace, source_id=foreign_source.id
    )._tool_get_feedback(str(scope.eval_id))
    nonexistent_record = _orchestrator(
        scope, workspace=scope.workspace, source_id=uuid.uuid4()
    )._tool_get_feedback(str(scope.eval_id))
    assert (
        foreign
        == nonexistent
        == unbound
        == missing_org_result
        == mismatched_result
        == foreign_record
        == nonexistent_record
    ), "SETUP_AQ-XWS-FBC: feedback tool foreign nonexistent and unbound shape"
    assert foreign == {"fewshots": "", "count": 0}, (
        "SETUP_AQ-XWS-FBC: feedback tool canonical empty result"
    )


def test_dataset_missing_metric_remains_fail_closed(consumer_scope):
    from model_hub.views.eval_runner import EvaluationRunner

    scope = consumer_scope
    scope.insert("local", workspace_id=scope.workspace_id)
    runner = EvaluationRunner(
        user_eval_metric_id=str(uuid.uuid4()),
        format_output=True,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    runner.eval_template = scope.template
    assert runner.get_few_shot_examples(["current case"], ["output"]) == [], (
        "SETUP_AQ-XWS-FBC: missing persisted dataset metric still refuses"
    )


@pytest.mark.parametrize("source_scope", ["cell", "dataset_row"])
def test_orchestrator_rechecks_dataset_source_in_worker(consumer_scope, source_scope):
    from django.core.exceptions import ObjectDoesNotExist

    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from model_hub.models.evals_metric import UserEvalMetric

    scope = consumer_scope
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name="feedback-source",
        organization=scope.organization,
        workspace=scope.workspace,
    )
    column = Column.no_workspace_objects.create(
        name="output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    cell = Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="current case"
    )
    UserEvalMetric.no_workspace_objects.create(
        name="feedback-source",
        dataset=dataset,
        template=scope.template,
        organization=scope.organization,
        workspace=scope.workspace,
        config={"mapping": {"output": str(column.id)}},
    )
    orchestrator = _orchestrator(
        scope,
        workspace=scope.workspace,
        source_id=cell.id if source_scope == "cell" else row.id,
    )
    orchestrator.eval_config.input_scope = source_scope
    assert orchestrator._resolve_input_mapping() == (["current case"], ["output"]), (
        "SETUP_AQ-XWS-FBC: persisted dataset source resolves in the worker"
    )
    # Simulate the source moving after admission. The next worker read must
    # re-check the persisted binding rather than trusting the earlier one.
    Dataset.no_workspace_objects.filter(id=dataset.id).update(
        workspace=scope.foreign_workspace
    )
    foreign = orchestrator._resolve_input_mapping()
    orchestrator.eval_config.source_id = str(uuid.uuid4())
    try:
        nonexistent = orchestrator._resolve_input_mapping()
    except ObjectDoesNotExist:
        nonexistent = "raised-not-found"
    assert foreign == nonexistent == ([], []), (
        "AQ-XWS-FBC: worker source recheck must refuse foreign and nonexistent records identically"
    )


def test_orchestrator_unbound_context_cannot_use_stale_feedback_count(consumer_scope):
    scope = consumer_scope
    orchestrator = _orchestrator(scope, workspace=None)
    assert not any(
        tool["function"]["name"] == "get_feedback_examples"
        for tool in orchestrator._build_tool_list()
    ), (
        "AQ-XWS-FBC: unbound orchestration must not advertise feedback from stale discovery"
    )
    assert orchestrator._tool_get_feedback(str(scope.eval_id)) == {
        "fewshots": "",
        "count": 0,
    }, "SETUP_AQ-XWS-FBC: legacy unbound tool call has canonical empty shape"

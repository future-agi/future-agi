"""Feedback reruns use the evaluator's real signature and stay tenant-scoped."""

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate, Feedback
from tfc.utils.tests.two_tenants import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.utils import eval as eval_module
from tracer.views import observation_span as span_view

URL = "/tracer/observation-span/submit_feedback_action_type/"
pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def tenants():
    owner = make_tenant("rerun-owner")
    other = make_tenant("rerun-other")
    yield owner, other
    owner.client.stop_workspace_injection()
    other.client.stop_workspace_injection()


@pytest.fixture
def evaluators(monkeypatch):
    # Autospec the underlying functions, not the decorator's (*args, **kwargs)
    # placeholder: a permissive mock would hide the four-vs-three argument bug.
    experiment = create_autospec(
        eval_module.evaluate_observation_span._original_func, return_value=True
    )
    observe = create_autospec(
        eval_module.evaluate_observation_span_observe._original_func,
        return_value=True,
    )
    monkeypatch.setattr(span_view, "evaluate_observation_span", experiment)
    monkeypatch.setattr(span_view, "evaluate_observation_span_observe", observe)
    monkeypatch.setattr(span_view, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(span_view, "track_mixpanel_event", Mock())
    return experiment, observe


def _records(tenant, versioned, workspace=None):
    workspace = workspace or tenant.workspace
    project = Project.no_workspace_objects.create(
        name="Feedback rerun",
        organization=tenant.organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment" if versioned else "observe",
    )
    version = (
        ProjectVersion.objects.create(project=project, name="Run", version="v1")
        if versioned
        else None
    )
    trace = Trace.objects.create(project=project, project_version=version)
    span = ObservationSpan.objects.create(
        id=f"rerun-{uuid.uuid4().hex[:16]}",
        project=project,
        project_version=version,
        trace=trace,
        name="Feedback span",
        observation_type="llm",
        start_time=timezone.now(),
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"feedback-{uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "Is the output correct?"},
    )
    config = CustomEvalConfig.objects.create(
        name="Feedback eval", project=project, eval_template=template, config={}
    )
    feedback = Feedback.objects.create(
        source="experiment" if versioned else "observe",
        source_id=span.id,
        value="true",
        user=tenant.user,
        eval_template=template,
        custom_eval_config_id=config.id,
        organization=tenant.organization,
        workspace=workspace,
    )
    log = EvalLogger.objects.create(
        observation_span=span,
        trace=trace,
        custom_eval_config=config,
        eval_task_id=str(uuid.uuid4()),
        output_bool=False,
    )
    return SimpleNamespace(span=span, config=config, feedback=feedback, log=log)


def _body(records):
    return {
        "observation_span_id": records.span.id,
        "custom_eval_config_id": str(records.config.id),
        "feedback_id": str(records.feedback.id),
        "action_type": "recalculate",
    }


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
def test_feedback_rerun_passes_evaluator_parameters(tenants, evaluators, versioned):
    owner, _ = tenants
    records = _records(owner, versioned)
    response = owner.client.post(URL, _body(records), format="json")

    assert response.status_code == 200, response.content
    assert response.json()["result"] == {
        "message": "Action type submitted successfully"
    }
    called, unused = evaluators if versioned else evaluators[::-1]
    called.assert_called_once()
    unused.assert_not_called()
    function = (
        eval_module.evaluate_observation_span
        if versioned
        else eval_module.evaluate_observation_span_observe
    )._original_func
    arguments = (
        inspect.signature(function)
        .bind(*called.call_args.args, **called.call_args.kwargs)
        .arguments
    )
    expected = {
        "observation_span_id": records.span.id,
        "custom_eval_config_id": str(records.config.id),
        "feedback_id": records.feedback.id,
    }
    if not versioned:
        expected["eval_task_id"] = records.log.eval_task_id
    assert arguments == expected
    records.feedback.refresh_from_db()
    assert records.feedback.action_type == "recalculate"
    records.log.refresh_from_db()
    assert records.log.deleted is True
    assert records.log.deleted_at is not None


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
def test_feedback_rerun_rejects_another_users_feedback(tenants, evaluators, versioned):
    owner, other = tenants
    records = _records(owner, versioned)
    response = other.client.post(URL, _body(records), format="json")

    assert response.status_code == 400, response.content
    for evaluator in evaluators:
        evaluator.assert_not_called()
    records.feedback.refresh_from_db()
    assert records.feedback.action_type is None
    records.log.refresh_from_db()
    assert records.log.deleted is False
    assert records.log.deleted_at is None


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
@pytest.mark.parametrize("boundary", ["organization", "workspace"])
@pytest.mark.parametrize("reference", ["span", "config"])
def test_feedback_rerun_rejects_out_of_scope_references(
    tenants, evaluators, versioned, boundary, reference
):
    owner, other = tenants
    local = _records(owner, versioned)
    if boundary == "workspace":
        workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=owner.organization,
            is_default=False,
            created_by=owner.user,
        )
        foreign = _records(owner, versioned, workspace)
    else:
        foreign = _records(other, versioned)
    body = _body(local)
    if reference == "span":
        # Keep the feedback owned by the caller and matched to the requested
        # span, so the request reaches the span's scope check, not the user check.
        local.feedback.source_id = foreign.span.id
        local.feedback.save(update_fields=["source_id"])
        body["observation_span_id"] = foreign.span.id
        body["custom_eval_config_id"] = str(foreign.config.id)
    else:
        body["custom_eval_config_id"] = str(foreign.config.id)
        # An existing result for this combination ensures a missing scope
        # predicate cannot pass the test merely because no eval result exists.
        EvalLogger.objects.create(
            observation_span=local.span,
            trace=local.span.trace,
            custom_eval_config=foreign.config,
            output_bool=False,
        )
    before = list(EvalLogger.objects.values_list("id", "deleted", "deleted_at"))

    response = owner.client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    for evaluator in evaluators:
        evaluator.assert_not_called()
    assert list(EvalLogger.objects.values_list("id", "deleted", "deleted_at")) == before

"""Separate feedback tenancy from the provider/billing workspace.

Use the existing real ClickHouse fixture and production engine/feedback reader.
Only inference, provider creation, billing and unrelated persistence are stubbed.
Defect assertions also work on the pre-field dataclass: assigning the proposed
attribute after construction lets the old runner fail on feedback, not TypeError.
"""

import uuid
from types import SimpleNamespace

import pytest

from model_hub.tests.test_feedback_rag_consumer_scope import (
    _django_db_helper,  # noqa: F401 - module-local transactional teardown override
    _Recorder,
    consumer_scope,  # noqa: F401
)
from model_hub.tests.test_feedback_rag_workspace_scope import feedback_store  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def engine_execution(monkeypatch):
    from evaluations.engine import runner as engine_runner
    from tfc.constants.api_calls import APICallStatusChoices
    from tracer.services.eval_tasks import entries
    from tracer.utils import eval as trace_eval

    execution = SimpleNamespace(
        recorder=_Recorder(), providers=[], legacy_billing=[], billing=[], logs=[]
    )

    def create_instance(**kwargs):
        execution.providers.append(kwargs["workspace_id"])
        return execution.recorder, None

    def log_cost(**kwargs):
        execution.legacy_billing.append(str(kwargs["workspace"].id))
        return SimpleNamespace(
            config="{}",
            status=APICallStatusChoices.PROCESSING.value,
            log_id=uuid.uuid4(),
            save=lambda: None,
        )

    monkeypatch.setattr(engine_runner, "create_eval_instance", create_instance)
    monkeypatch.setattr(trace_eval, "log_and_deduct_cost_for_api_request", log_cost)
    monkeypatch.setattr(
        trace_eval,
        "_emit_eval_billing",
        lambda **kwargs: execution.billing.append(kwargs),
    )
    monkeypatch.setattr(trace_eval, "_stamp_eval_version", lambda *args: None)
    monkeypatch.setattr(
        trace_eval, "_inject_ground_truth", lambda params, *args, **kwargs: params
    )
    monkeypatch.setattr(trace_eval, "_collect_run_warnings", lambda *args, **kwargs: [])
    monkeypatch.setattr(trace_eval, "_persist_eval_logger", execution.logs.append)
    # Route span result persistence through the same stub, not the legacy PG join.
    monkeypatch.setattr(entries, "in_engine_write_mode", lambda: True)
    return execution


@pytest.fixture
def project_run(consumer_scope, engine_execution):  # noqa: F811
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.trace_session import TraceSession
    from tracer.utils import eval as trace_eval

    scope = consumer_scope
    config = CustomEvalConfig.no_workspace_objects.create(
        project=scope.project,
        name=f"feedback-engine-{uuid.uuid4().hex}",
        eval_template=scope.template,
        config={"run_config": {"data_injection": {}}},
        model="turing_large",
    )
    session = TraceSession.all_objects.create(project=scope.project)
    span = SimpleNamespace(id=uuid.uuid4(), trace=scope.trace, project=scope.project)

    def evaluate(target):
        scope.trace.project = scope.project
        session.project = scope.project
        span.project = scope.project
        before = len(engine_execution.recorder.calls)
        if target == "trace":
            trace_eval._execute_evaluation_for_trace(
                trace=scope.trace,
                anchor_span=span,
                custom_eval_config=config,
                eval_task_id=None,
                run_params={"output": "current case"},
            )
        elif target == "session":
            trace_eval._execute_evaluation_for_session(
                trace_session=session,
                custom_eval_config=config,
                eval_task_id=None,
                run_params={"output": "current case"},
            )
        else:
            trace_eval._execute_evaluation(
                observation_span_id=span.id,
                custom_eval_config_id=config.id,
                eval_task_id=None,
                type="observe",
                run_params={"output": "current case"},
                observation_span=span,
                project_id=scope.project.id,
            )
        assert len(engine_execution.recorder.calls) == before + 1, (
            "SETUP_AQ-XWS-FBD: production caller reached the evaluator"
        )
        assert engine_execution.logs and not engine_execution.logs[-1].get("error"), (
            "SETUP_AQ-XWS-FBD: evaluation completed without an error result"
        )
        return engine_execution.recorder.calls[-1]["few_shots"]

    return evaluate


def _bind_project(scope, workspace):
    from tracer.models.project import Project

    updated = Project.no_workspace_objects.filter(
        id=scope.project.id, organization_id=scope.organization.id
    ).update(workspace=workspace)
    assert updated == 1, "SETUP_AQ-XWS-FBD: persisted project was rebound"
    scope.project.refresh_from_db()
    assert scope.project.workspace_id == (workspace.id if workspace else None), (
        "SETUP_AQ-XWS-FBD: worker uses the persisted project binding"
    )


@pytest.mark.parametrize("target", ["trace", "session"])
def test_null_project_feedback_is_empty_but_provider_and_billing_keep_default(
    consumer_scope,  # noqa: F811
    engine_execution,
    project_run,
    target,  # noqa: F811
):
    scope = consumer_scope
    scope.insert("default", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    assert scope.workspace.is_default, "SETUP_AQ-XWS-FBD: seeded default workspace"
    bound = project_run(target)
    assert "default-comment" in str(bound) and "foreign-comment" not in str(bound), (
        "SETUP_AQ-XWS-FBD: bound project has matching feedback"
    )
    _bind_project(scope, None)
    unbound = project_run(target)
    assert engine_execution.providers == [scope.workspace_id, scope.workspace_id], (
        "SETUP_AQ-XWS-FBD: provider still receives the default workspace"
    )
    assert engine_execution.legacy_billing == [
        scope.workspace_id,
        scope.workspace_id,
    ], "SETUP_AQ-XWS-FBD: cost log still receives the default workspace"
    assert [entry["ws_id"] for entry in engine_execution.billing] == [
        scope.workspace_id,
        scope.workspace_id,
    ], "SETUP_AQ-XWS-FBD: usage billing still receives the default workspace"
    assert unbound == [], (
        "AQ-XWS-FBD: NULL-project trace and session must evaluate with zero feedback examples"
    )


@pytest.mark.parametrize("target", ["trace", "session"])
def test_nondefault_project_keeps_only_its_own_feedback(
    consumer_scope,  # noqa: F811
    engine_execution,
    project_run,
    target,  # noqa: F811
):
    scope = consumer_scope
    scope.insert("default", workspace_id=scope.workspace_id)
    scope.insert("project", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    scope.insert(
        "other-org",
        workspace_id=scope.foreign_workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    scope.insert(
        "other-org-null",
        workspace_id=None,
        organization_id=scope.foreign_organization_id,
    )
    _bind_project(scope, scope.foreign_workspace)
    examples = project_run(target)
    # Same-organization feedback saved with no workspace before the release
    # cutoff is admitted.
    assert (
        "project-comment" in str(examples)
        and "legacy-null-comment" in str(examples)
        and all(
            marker not in str(examples)
            for marker in ("default-", "other-org-", "after-cutoff-null-")
        )
    ), (
        "bound trace and session keep their workspace's feedback and "
        "same-org pre-cutoff no-workspace feedback, nothing else"
    )
    assert engine_execution.providers == [scope.foreign_workspace_id], (
        "SETUP_AQ-XWS-FBD: bound provider workspace remains unchanged"
    )


def _engine_call(scope, execution, *, feedback_workspace_id, organization_id=None):
    from evaluations.engine import EvalRequest, run_eval

    request = EvalRequest(
        eval_template=scope.template,
        inputs={"output": "current case"},
        organization_id=organization_id or scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    # The pre-fix dataclass accepts attributes, but its runner ignores this one.
    # Avoid a constructor TypeError masking the behavioral RED assertion.
    request.feedback_workspace_id = feedback_workspace_id
    before = len(execution.recorder.calls)
    result = run_eval(request)
    assert result.value == 1.0 and len(execution.recorder.calls) == before + 1, (
        "SETUP_AQ-XWS-FBD: explicit feedback scope still evaluates successfully"
    )
    assert execution.providers[-1] == scope.workspace_id, (
        "SETUP_AQ-XWS-FBD: feedback override does not rebind the provider"
    )
    return execution.recorder.calls[-1]["few_shots"]


def test_explicit_feedback_workspace_overrides_only_feedback(
    consumer_scope,  # noqa: F811
    engine_execution,  # noqa: F811
):
    scope = consumer_scope
    scope.insert("provider", workspace_id=scope.workspace_id)
    scope.insert("feedback", workspace_id=scope.foreign_workspace_id)
    local = _engine_call(
        scope, engine_execution, feedback_workspace_id=scope.workspace_id
    )
    assert "provider-comment" in str(local), (
        "SETUP_AQ-XWS-FBD: provider-workspace feedback is available"
    )
    selected = _engine_call(
        scope, engine_execution, feedback_workspace_id=scope.foreign_workspace_id
    )
    assert "feedback-comment" in str(selected) and "provider-comment" not in str(
        selected
    ), "AQ-XWS-FBD: explicit feedback scope must override only the feedback workspace"


def test_explicit_null_foreign_and_nonexistent_scopes_have_identical_empty_shape(
    consumer_scope,  # noqa: F811
    engine_execution,  # noqa: F811
):
    from evaluations.engine.params import prepare_run_params

    scope = consumer_scope
    foreign_workspace_id = str(uuid.uuid4())
    scope.insert("provider", workspace_id=scope.workspace_id)
    scope.insert(
        "foreign-org",
        workspace_id=foreign_workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    visible = prepare_run_params(
        {"output": "current case"},
        scope.template,
        organization_id=scope.foreign_organization_id,
        workspace_id=foreign_workspace_id,
    )["few_shots"]
    assert "foreign-org-comment" in str(visible), (
        "SETUP_AQ-XWS-FBD: foreign feedback exists and is visible in its own scope"
    )
    results = [
        _engine_call(scope, engine_execution, feedback_workspace_id=workspace_id)
        for workspace_id in (None, foreign_workspace_id, str(uuid.uuid4()))
    ]
    assert results == [[], [], []], (
        "AQ-XWS-FBD: explicit NULL foreign and nonexistent feedback scopes must share the empty shape"
    )


def test_untouched_span_uses_sentinel_workspace_feedback(
    consumer_scope,  # noqa: F811
    engine_execution,
    project_run,  # noqa: F811
):
    scope = consumer_scope
    scope.insert("span", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    result = project_run("span")
    assert "span-comment" in str(result) and "foreign-comment" not in str(result), (
        "SETUP_AQ-XWS-FBD: untouched span caller retains same-workspace feedback"
    )
    assert engine_execution.providers == [scope.workspace_id], (
        "SETUP_AQ-XWS-FBD: untouched span provider scope remains unchanged"
    )


def test_untouched_standalone_uses_sentinel_workspace_feedback(
    consumer_scope,  # noqa: F811
    engine_execution,
    monkeypatch,  # noqa: F811
):
    from ee.usage.services.config import BillingConfig
    from model_hub.services.ground_truth_service import GroundTruthService
    from sdk.utils import evaluations as sdk_evaluations

    scope = consumer_scope
    scope.insert("standalone", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    monkeypatch.setattr(
        GroundTruthService, "inject_context", lambda params, *args, **kwargs: params
    )
    monkeypatch.setattr(
        sdk_evaluations,
        "_log_and_deduct_cost_for_standalone_eval",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(BillingConfig, "get", lambda: None)
    result = sdk_evaluations._run_eval(
        scope.template,
        {"output": "current case"},
        "turing_large",
        SimpleNamespace(id=uuid.uuid4(), organization=scope.organization),
        scope.workspace,
    )
    assert result["value"] == 1.0 and len(engine_execution.recorder.calls) == 1, (
        "SETUP_AQ-XWS-FBD: untouched standalone caller still evaluates"
    )
    examples = engine_execution.recorder.calls[-1]["few_shots"]
    assert "standalone-comment" in str(examples) and "foreign-comment" not in str(
        examples
    ), "SETUP_AQ-XWS-FBD: untouched standalone caller retains same-workspace feedback"
    assert engine_execution.providers == [scope.workspace_id], (
        "SETUP_AQ-XWS-FBD: untouched standalone provider scope remains unchanged"
    )

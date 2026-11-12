"""Synchronous SDK evaluations must persist their explicit workspace binding.

Reuse the root organization/user/workspace fixtures and the PG-only span pattern
from tracer/tests/test_inline_eval_target_scope.py. Only the external evaluation
engine is stubbed; creation, admission, ORM lookups and worker writes are real.
"""

import uuid
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from sdk.utils import evaluations as sdk_evaluations
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_workspace,
    set_workspace_context,
)
from tracer.models.custom_eval_config import (
    CustomEvalConfig,
    InlineEval,
    InLineEvalStatus,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.utils.inline_evals import process_in_line_evals, trigger_inline_eval

pytestmark = pytest.mark.django_db

REJECTION_REASON = "inline_eval_target_not_in_scope"
ENGINE_RESULT = {
    "value": "Passed",
    "reason": "AQ W2b completed evaluation",
    "runtime": 0.01,
    "output": "pass_fail",
    "failure": None,
}


def _span(organization, workspace):
    project = Project.objects.create(
        name=f"AQ W2b project {uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
    )
    trace = Trace.objects.create(project=project, name="AQ W2b trace")
    return ObservationSpan.objects.create(
        id=f"aq-w2b-{uuid.uuid4().hex}",
        project=project,
        trace=trace,
        name="AQ W2b span",
        observation_type="llm",
        status="OK",
    )


@pytest.fixture
def sync_scope(organization, workspace, user, monkeypatch):
    clear_workspace_context()
    request_workspace = Workspace.objects.create(
        name="AQ W2b request workspace", organization=organization, created_by=user
    )
    peer_workspace = Workspace.objects.create(
        name="AQ W2b peer workspace", organization=organization, created_by=user
    )
    template = EvalTemplate.objects.create(
        name=f"AQ W2b template {uuid.uuid4().hex}",
        organization=organization,
        workspace=request_workspace,
        config={"type": "pass_fail", "criteria": "AQ W2b criteria"},
    )
    own = _span(organization, request_workspace)
    targets = {
        "peer": _span(organization, peer_workspace),
        "default": _span(organization, workspace),
        "null": _span(organization, None),
    }
    assert workspace.is_default and not request_workspace.is_default, (
        "SETUP_AQ-XWS-W2B: request workspace must differ from the default workspace"
    )
    assert all(
        span.project.organization_id == organization.id
        and span.project.workspace_id != request_workspace.id
        for span in targets.values()
    ), "SETUP_AQ-XWS-W2B: foreign targets must share the org but not the workspace"
    assert targets["null"].project.workspace_id is None, (
        "SETUP_AQ-XWS-W2B: legacy target project must have no workspace"
    )
    assert not InlineEval.objects.exists(), (
        "SETUP_AQ-XWS-W2B: inline evaluation table must start empty"
    )
    assert not CustomEvalConfig.objects.exists() and not EvalLogger.objects.exists(), (
        "SETUP_AQ-XWS-W2B: result and configuration tables must start empty"
    )
    assert get_current_workspace() is None and get_current_organization() is None, (
        "SETUP_AQ-XWS-W2B: worker must not inherit request context"
    )
    monkeypatch.setattr(
        sdk_evaluations, "_run_eval", lambda *args, **kwargs: dict(ENGINE_RESULT)
    )
    yield SimpleNamespace(
        organization=organization,
        workspace=request_workspace,
        default_workspace=workspace,
        peer_workspace=peer_workspace,
        user=user,
        template=template,
        own=own,
        targets=targets,
    )
    clear_workspace_context()


def _run_sync(scope, span_id, *, batched=False, workspace_bound=True, trace_eval=True):
    kwargs = {
        "eval_template": scope.template,
        "inputs": {"input": ["first", "second"] if batched else "first"},
        "model": None,
        "user": scope.user,
        "workspace": scope.workspace if workspace_bound else None,
        "eval_config": {},
        "error_localizer_enabled": False,
        "trace_eval": trace_eval,
        "custom_eval_name": "aq-w2b-inline",
        "span_id": span_id,
    }
    if batched:
        return sdk_evaluations._run_batched_standalone_eval(**kwargs)
    return [sdk_evaluations._run_standalone_eval(**kwargs)]


def _stored_evaluations(results):
    return list(
        Evaluation.no_workspace_objects.filter(
            id__in=[result["eval_id"] for result in results]
        )
    )


def _process_pending():
    # Avoid only the Temporal transport, which closes pytest's DB transaction.
    process_in_line_evals._original_func()


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("trace_eval", [False, True])
@pytest.mark.parametrize("ambient_default", [False, True])
def test_sync_persists_request_workspace(
    sync_scope, batched, trace_eval, ambient_default
):
    if ambient_default:
        set_workspace_context(
            organization=sync_scope.organization,
            workspace=sync_scope.default_workspace,
            user=sync_scope.user,
        )
    results = _run_sync(
        sync_scope, sync_scope.own.id, batched=batched, trace_eval=trace_eval
    )
    clear_workspace_context()
    evaluations = _stored_evaluations(results)
    assert len(evaluations) == (2 if batched else 1), (
        "SETUP_AQ-XWS-W2B: every completed sync result must have a stored evaluation"
    )
    assert all(
        (evaluation.organization_id, evaluation.workspace_id)
        == (sync_scope.organization.id, sync_scope.workspace.id)
        for evaluation in evaluations
    ), "AQ-XWS-W2B: sync evaluation must persist the explicit request workspace"


@pytest.mark.parametrize("target", ["peer", "default", "null"])
@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("effect", ["record", "reason"])
def test_sync_rejects_other_workspace_admission(sync_scope, target, batched, effect):
    span = sync_scope.targets[target]
    assert ObservationSpan.objects.filter(id=span.id).exists(), (
        "SETUP_AQ-XWS-W2B: refused same-organization span must exist"
    )
    with capture_logs() as logs:
        _run_sync(sync_scope, span.id, batched=batched)
    if effect == "record":
        assert not InlineEval.objects.exists(), (
            "AQ-XWS-W2B: sync admission must not queue a different-workspace target"
        )
    else:
        assert sum(log.get("reason") == REJECTION_REASON for log in logs) == (
            2 if batched else 1
        ), "AQ-XWS-W2B: sync admission must emit the fixed unavailable-target reason"


@pytest.mark.parametrize("target", ["peer", "default", "null"])
@pytest.mark.parametrize("effect", ["logger", "config"])
def test_sync_foreign_target_cannot_reach_worker_writes(sync_scope, target, effect):
    _run_sync(sync_scope, sync_scope.targets[target].id)
    _process_pending()
    if effect == "logger":
        assert not EvalLogger.objects.exists(), (
            "AQ-XWS-W2B: sync foreign target must not receive an EvalLogger"
        )
    else:
        assert not CustomEvalConfig.objects.exists(), (
            "AQ-XWS-W2B: sync foreign target must not receive a CustomEvalConfig"
        )


@pytest.mark.parametrize("target", ["peer", "default", "null"])
def test_sync_foreign_and_missing_have_identical_refusal_shape(sync_scope, target):
    missing_id = f"missing-{uuid.uuid4().hex}"
    assert not ObservationSpan.objects.filter(id=missing_id).exists(), (
        "SETUP_AQ-XWS-W2B: missing span must not exist"
    )
    shapes = []
    for span_id in (sync_scope.targets[target].id, missing_id):
        with capture_logs() as logs:
            result = _run_sync(sync_scope, span_id)[0]
        shapes.append(
            (
                {key: value for key, value in result.items() if key != "eval_id"},
                InlineEval.objects.filter(evaluation_id=result["eval_id"]).count(),
                [log["reason"] for log in logs if "reason" in log],
            )
        )
    assert shapes[0] == shapes[1] and shapes[0][1:] == (0, [REJECTION_REASON]), (
        "AQ-XWS-W2B: foreign and missing spans must have the same refusal shape"
    )


@pytest.mark.parametrize("effect", ["logger", "status"])
def test_sync_worker_rechecks_persisted_workspace_after_target_moves(
    sync_scope, effect
):
    result = _run_sync(sync_scope, sync_scope.own.id)[0]
    inline_eval = InlineEval.objects.get(evaluation_id=result["eval_id"])
    assert inline_eval.status == InLineEvalStatus.PENDING, (
        "SETUP_AQ-XWS-W2B: own target must be admitted before its project moves"
    )
    changed = Project.objects.filter(id=sync_scope.own.project_id).update(
        workspace=sync_scope.peer_workspace
    )
    assert changed == 1, "SETUP_AQ-XWS-W2B: exactly the admitted project must move"
    _process_pending()
    inline_eval.refresh_from_db()
    if effect == "logger":
        assert not EvalLogger.objects.exists(), (
            "AQ-XWS-W2B: worker must not write after the target leaves its workspace"
        )
    else:
        assert inline_eval.status == InLineEvalStatus.FAILED, (
            "AQ-XWS-W2B: worker must fail a target outside the persisted workspace"
        )


@pytest.mark.parametrize("batched", [False, True])
def test_sync_without_workspace_does_not_admit_or_replay_inline_eval(
    sync_scope, batched
):
    results = _run_sync(
        sync_scope, sync_scope.own.id, batched=batched, workspace_bound=False
    )
    for evaluation in _stored_evaluations(results):
        # Retry the real admission function: absence of binding must remain safe.
        trigger_inline_eval(evaluation)
    assert not InlineEval.objects.exists(), (
        "AQ-XWS-W2B: missing workspace must not fall back to organization-only admission"
    )


def test_legacy_create_call_without_workspace_cannot_persist_inline_intent(sync_scope):
    evaluation = sdk_evaluations._create_evaluation(
        eval_template=sync_scope.template,
        inputs={"input": "first"},
        user=sync_scope.user,
        eval_config={},
        result=dict(ENGINE_RESULT),
        error_localizer_enabled=False,
        trace_eval=True,
        custom_eval_name="aq-w2b-inline",
        span_id=sync_scope.own.id,
    )
    evaluation.refresh_from_db()
    assert evaluation.trace_data is None, (
        "AQ-XWS-W2B: unbound creation must not persist replayable inline intent"
    )


@pytest.mark.parametrize("batched", [False, True])
def test_same_workspace_sync_completes_with_unchanged_result(sync_scope, batched):
    """Positive control: real admission and worker must still produce results."""
    results = _run_sync(sync_scope, sync_scope.own.id, batched=batched)
    expected_count = 2 if batched else 1
    inline_evals = list(InlineEval.objects.all())
    assert len(inline_evals) == expected_count, (
        "AQ-XWS-W2B: same-workspace sync must admit every result"
    )
    assert all(
        (item.organization_id, item.workspace_id)
        == (sync_scope.organization.id, sync_scope.workspace.id)
        for item in inline_evals
    ), "AQ-XWS-W2B: inline jobs must retain the sync request workspace"
    _process_pending()
    for result in results:
        assert result == {
            "name": sync_scope.template.name,
            "reason": ENGINE_RESULT["reason"],
            "runtime": ENGINE_RESULT["runtime"],
            "output": ENGINE_RESULT["value"],
            "output_type": ENGINE_RESULT["output"],
            "eval_id": result["eval_id"],
        }, "AQ-XWS-W2B: sync evaluation response must retain its existing contract"
    assert all(
        item.status == InLineEvalStatus.COMPLETED for item in InlineEval.objects.all()
    ), "AQ-XWS-W2B: same-workspace worker must complete admitted jobs"
    loggers = list(EvalLogger.objects.all())
    assert len(loggers) == expected_count and all(
        item.observation_span_id == sync_scope.own.id
        and item.trace_id == sync_scope.own.trace_id
        and item.custom_eval_config.project_id == sync_scope.own.project_id
        and item.output_bool is True
        for item in loggers
    ), "AQ-XWS-W2B: same-workspace worker must retain target and evaluation output"


def test_unbound_non_inline_sync_retains_standalone_result(sync_scope):
    """Positive control: refusing inline writes must not break standalone output."""
    result = _run_sync(sync_scope, None, workspace_bound=False, trace_eval=False)[0]
    assert result["output"] == ENGINE_RESULT["value"], (
        "AQ-XWS-W2B: workspace-less standalone evaluation must retain its result"
    )
    assert not InlineEval.objects.exists(), (
        "AQ-XWS-W2B: non-inline evaluation must not queue an inline job"
    )

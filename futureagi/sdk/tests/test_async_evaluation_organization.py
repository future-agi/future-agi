"""Async execution must retain the personal key's tenant.

Keep real key authentication, evaluation persistence, worker reload and _run_eval.
Stub only external execution/metering boundaries and Temporal transport. These
hosted tests need the normal Django/Postgres harness; they make no provider calls.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for
from evaluations import engine
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.services.ground_truth_service import GroundTruthService
from sdk.utils import evaluations as sdk_evaluations
from sdk.utils.helpers import _get_api_call_type
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal import evaluations as evaluation_workflows
from tfc.temporal.evaluations import activities
from tracer.utils import inline_evals

pytestmark = pytest.mark.django_db


@pytest.fixture
def async_scope(organization, user, workspace, monkeypatch):
    clear_workspace_context()
    selected_org = Organization.objects.create(name="Async evaluation organization B")
    selected_workspace = Workspace.objects.create(
        name="Async evaluation workspace B",
        organization=selected_org,
        created_by=user,
    )
    key = personal_key_for(
        user, selected_org, workspace=selected_workspace, name="Async tenant regression"
    )
    template = EvalTemplate.objects.create(
        name=f"async-tenant-{uuid.uuid4().hex}",
        organization=None,
        workspace=None,
        owner="system",
        config={
            "eval_type_id": "TestEval",
            "required_keys": ["input", "context"],
            "output": "score",
        },
    )
    start_workflow = Mock()
    monkeypatch.setattr(
        evaluation_workflows, "start_evaluation_workflow", start_workflow
    )
    # Connection cleanup is transport plumbing; keep pytest's DB transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(inline_evals, "trigger_inline_eval", Mock())
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.post(
        "/sdk/api/v1/new-eval/",
        {
            "eval_name": template.name,
            "inputs": {
                "input": "What is Python?",
                "context": "Python is a programming language.",
            },
            "model": "turing_large",
            "is_async": True,
        },
        format="json",
    )
    assert response.status_code == 200
    evaluation_id = response.data["result"][0]["evaluations"][0]["eval_id"]
    start_workflow.assert_called_once_with(evaluation_id=str(evaluation_id))
    clear_workspace_context()
    evaluation = Evaluation.no_workspace_objects.select_related("user").get(
        pk=evaluation_id
    )
    assert evaluation.organization_id == selected_org.id
    assert evaluation.workspace_id == selected_workspace.id
    assert evaluation.user.organization_id == organization.id
    assert set(
        OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    ) == {organization.id, selected_org.id}
    yield SimpleNamespace(
        evaluation=evaluation,
        user=user,
        legacy_org=organization,
        legacy_workspace=workspace,
        organization=selected_org,
        workspace=selected_workspace,
    )
    clear_workspace_context()


@pytest.fixture
def effects(monkeypatch):
    from ee.usage.services import emitter, metering
    from ee.usage.services.config import BillingConfig

    quota = Mock(return_value=SimpleNamespace(allowed=True, reason=None))
    cost_log = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
        config="{}",
        log_id=uuid.uuid4(),
        save=Mock(),
    )
    log = Mock(return_value=cost_log)
    ground_truth = Mock()
    execute = Mock(
        return_value=engine.EvalResult(
            value=0.9,
            data={},
            reason="Relevant context",
            failure=None,
            runtime=0.01,
            model_used="turing_large",
            metrics=[],
            metadata={},
            output_type="score",
            cost={"total_cost": 0.02},
            token_usage={},
        )
    )
    emit = Mock()
    billing = SimpleNamespace(
        get_eval_per_run_fee=lambda: 0.01,
        calculate_ai_credits=lambda cost: cost * 100,
    )
    monkeypatch.setattr(metering, "check_usage", quota)
    monkeypatch.setattr(sdk_evaluations, "log_and_deduct_cost_for_api_request", log)
    monkeypatch.setattr(GroundTruthService, "inject_context", ground_truth)
    monkeypatch.setattr(engine, "run_eval", execute)
    monkeypatch.setattr(BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(emitter, "emit", emit)
    return SimpleNamespace(
        quota=quota, log=log, ground_truth=ground_truth, execute=execute, emit=emit
    )


def _run(scope):
    clear_workspace_context()
    result = activities._run_single_evaluation_sync(str(scope.evaluation.pk))
    scope.evaluation.refresh_from_db()
    scope.user.refresh_from_db()
    return result


def _assert_selected_tenant(scope, effects, *, workspace):
    organization_id = scope.organization.id
    workspace_id = workspace.id if workspace else None
    effects.quota.assert_called_once_with(
        str(organization_id), _get_api_call_type("turing_large")
    )
    effects.log.assert_called_once()
    assert effects.log.call_args.kwargs["organization"].id == organization_id
    assert effects.log.call_args.kwargs["workspace"] == workspace
    effects.ground_truth.assert_called_once()
    assert effects.ground_truth.call_args.kwargs == {
        "organization_id": organization_id,
        "workspace_id": workspace_id,
    }
    effects.execute.assert_called_once()
    engine_request = effects.execute.call_args.args[0]
    assert engine_request.organization_id == str(organization_id)
    assert engine_request.workspace_id == (str(workspace_id) if workspace_id else None)
    effects.emit.assert_called_once()
    event = effects.emit.call_args.args[0]
    assert event.org_id == str(organization_id)
    assert event.amount == pytest.approx(3.0)
    assert scope.evaluation.organization_id == organization_id
    assert scope.evaluation.workspace_id == workspace_id
    assert scope.evaluation.status == StatusChoices.COMPLETED
    assert scope.evaluation.value == "0.9"


@pytest.mark.parametrize("legacy_matches", [False, True], ids=["legacy-A", "legacy-B"])
@pytest.mark.parametrize(
    "workspace_bound", [True, False], ids=["workspace-B", "no-workspace"]
)
def test_async_personal_key_tenant_owns_all_execution_effects(
    async_scope, effects, legacy_matches, workspace_bound
):
    scope = async_scope
    persisted_org = scope.organization if legacy_matches else scope.legacy_org
    type(scope.user)._base_manager.filter(pk=scope.user.pk).update(
        organization=persisted_org
    )
    selected_workspace = scope.workspace if workspace_bound else None
    Evaluation.no_workspace_objects.filter(pk=scope.evaluation.pk).update(
        workspace=selected_workspace
    )
    result = _run(scope)
    assert result["status"] == StatusChoices.COMPLETED
    _assert_selected_tenant(scope, effects, workspace=selected_workspace)
    # Binding is local to the worker, not a change to the person's legacy tenant.
    assert scope.user.organization_id == persisted_org.id


def test_legacy_organization_quota_cannot_block_async_evaluation(async_scope, effects):
    scope = async_scope
    effects.quota.side_effect = lambda org_id, call_type: SimpleNamespace(
        allowed=org_id != str(scope.legacy_org.id), reason="Legacy organization paused"
    )
    result = _run(scope)
    assert result["status"] == StatusChoices.COMPLETED
    _assert_selected_tenant(scope, effects, workspace=scope.workspace)
    assert scope.user.organization_id == scope.legacy_org.id


def test_evaluation_organization_quota_is_enforced(async_scope, effects):
    from ee.usage.schemas.events import CheckResult

    scope = async_scope
    # A refusal reads the check's dimension, usage and limit, so the stub returns
    # the same result type the real usage check returns.
    effects.quota.side_effect = lambda org_id, call_type: CheckResult(
        allowed=org_id != str(scope.organization.id),
        reason="Evaluation quota exhausted",
    )
    result = _run(scope)
    assert result["status"] == "FAILED"
    assert scope.evaluation.status == StatusChoices.FAILED
    assert scope.evaluation.error_message == "Evaluation quota exhausted"
    effects.quota.assert_called_once_with(
        str(scope.organization.id), _get_api_call_type("turing_large")
    )
    effects.log.assert_not_called()
    effects.execute.assert_not_called()
    effects.emit.assert_not_called()
    assert scope.user.organization_id == scope.legacy_org.id


def test_mismatched_workspace_is_rejected_before_execution(async_scope, effects):
    scope = async_scope
    Evaluation.no_workspace_objects.filter(pk=scope.evaluation.pk).update(
        workspace=scope.legacy_workspace
    )
    result = _run(scope)
    assert result["status"] == "FAILED"
    assert scope.evaluation.status == StatusChoices.FAILED
    for effect in vars(effects).values():
        effect.assert_not_called()
    assert scope.evaluation.organization_id == scope.organization.id
    assert scope.evaluation.workspace_id == scope.legacy_workspace.id
    assert scope.user.organization_id == scope.legacy_org.id

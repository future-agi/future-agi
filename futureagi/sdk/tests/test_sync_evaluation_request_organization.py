"""Bind synchronous inline evaluations to the request-authorized organization.

Real bearer access sessions preserve an active legacy organization A while
selecting B with X-Organization-Id. Personal API keys instead align the in-memory
user to B and are positive controls, not evidence of the A/B mismatch. Reuse the
root account fixtures, real-auth helpers and W2b PG span / worker helpers. Only
external evaluation execution and analytics are stubbed; creation, authentication,
tenant admission, persistence and worker writes remain real.
"""

import uuid
from inspect import signature
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for, session_client_for
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from sdk.tests.test_sync_evaluation_workspace_binding import (
    ENGINE_RESULT,
    REJECTION_REASON,
    _process_pending,
    _span,
)
from sdk.utils import evaluations as sdk_evaluations
from sdk.views import evaluations as evaluation_views
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
from tracer.utils.inline_evals import trigger_inline_eval

pytestmark = pytest.mark.django_db

EVAL_URL = "/sdk/api/v1/new-eval/"
INLINE_NAME = "aq-w2d-inline"


def _client(user, organization, workspace):
    key = personal_key_for(
        user, organization, workspace=workspace, name="AQ W2d test key"
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.fixture
def request_scope(request, organization, workspace, user, monkeypatch):
    clear_workspace_context()
    single_org = getattr(request, "param", "multi") == "single"
    request_org = (
        organization
        if single_org
        else Organization.objects.create(name="AQ W2d active organization B")
    )
    request_workspace = Workspace.objects.create(
        name="AQ W2d explicit workspace", organization=request_org, created_by=user
    )
    # The key helper provisions the active B memberships used by both transports.
    # session_client_for issues a live AuthToken and supplies real bearer headers;
    # it never force-authenticates or injects request organization/workspace.
    api_key_client = _client(user, request_org, request_workspace)
    client = session_client_for(user, request_org, request_workspace)
    legacy_client = _client(user, organization, workspace)
    template = EvalTemplate.objects.create(
        name=f"AQ W2d system template {uuid.uuid4().hex}",
        organization=None,
        workspace=None,
        owner="system",
        config={
            "eval_type_id": "TestEval",
            "required_keys": ["input"],
            "output": "pass_fail",
        },
    )
    own = _span(request_org, request_workspace)
    peer_workspace = Workspace.objects.create(
        name="AQ W2d peer workspace", organization=request_org, created_by=user
    )
    foreign = _span(organization, workspace)
    peer = _span(request_org, peer_workspace)
    memberships = set(
        OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    )
    assert memberships == {organization.id, request_org.id}, (
        "SETUP_AQ-XWS-W2F: account must have exactly the expected active memberships"
    )
    assert user.organization_id == organization.id, (
        "SETUP_AQ-XWS-W2F: legacy organization must remain A"
    )
    assert template.organization_id is None and template.workspace_id is None, (
        "SETUP_AQ-XWS-W2F: system template must be globally available"
    )
    assert not request_workspace.is_default, (
        "SETUP_AQ-XWS-W2F: request workspace must not be the default workspace"
    )
    assert not InlineEval.objects.exists() and not EvalLogger.objects.exists(), (
        "SETUP_AQ-XWS-W2F: inline jobs and results must start empty"
    )
    assert not CustomEvalConfig.objects.exists(), (
        "SETUP_AQ-XWS-W2F: inline configurations must start empty"
    )
    monkeypatch.setattr(
        sdk_evaluations, "_run_eval", lambda *args, **kwargs: dict(ENGINE_RESULT)
    )
    monkeypatch.setattr(
        evaluation_views, "track_mixpanel_event", lambda *args, **kwargs: None
    )
    yield SimpleNamespace(
        user=user,
        legacy_org=organization,
        legacy_workspace=workspace,
        organization=request_org,
        workspace=request_workspace,
        peer_workspace=peer_workspace,
        template=template,
        own=own,
        targets={"legacy_org": foreign, "peer_workspace": peer},
        client=client,
        api_key_client=api_key_client,
        legacy_client=legacy_client,
    )
    clear_workspace_context()


def _post(scope, span_id, *, batched=False, api_key=False):
    client = scope.api_key_client if api_key else scope.client
    response = client.post(
        EVAL_URL,
        {
            "eval_name": scope.template.name,
            "inputs": {"input": ["first", "second"] if batched else "first"},
            "trace_eval": True,
            "span_id": span_id,
            "custom_eval_name": INLINE_NAME,
        },
        format="json",
    )
    assert response.status_code == 200, (
        "SETUP_AQ-XWS-W2F: authenticated synchronous request must complete"
    )
    authenticated = response.renderer_context["request"]
    assert (
        authenticated.organization.id == scope.organization.id
        and authenticated.workspace.id == scope.workspace.id
    ), (
        "SETUP_AQ-XWS-W2F: real authentication must select organization B and workspace WB"
    )
    if api_key:
        assert (
            authenticated.org_api_key.organization_id == scope.organization.id
            and authenticated.user.organization_id == scope.organization.id
        ), "SETUP_AQ-XWS-W2F: real API-key authentication must align the user to B"
    else:
        assert (
            authenticated.credential_provenance == "interactive_session"
            and authenticated.auth_scope is None
            and getattr(authenticated, "org_api_key", None) is None
            and authenticated.user.organization_id == scope.legacy_org.id
        ), (
            "SETUP_AQ-XWS-W2F: a real unscoped bearer session must retain active legacy A"
        )
    results = response.data["result"][0]["evaluations"]
    assert len(results) == (2 if batched else 1), (
        "SETUP_AQ-XWS-W2F: synchronous response must contain every standalone result"
    )
    clear_workspace_context()
    return results


def _evaluations(results):
    evaluations = list(
        Evaluation.no_workspace_objects.filter(
            id__in=[result["eval_id"] for result in results]
        )
    )
    assert len(evaluations) == len(results), (
        "SETUP_AQ-XWS-W2F: every returned result must have a persisted evaluation"
    )
    return evaluations


def _create(scope, *, organization=None, workspace=None):
    kwargs = {
        "eval_template": scope.template,
        "inputs": {"input": "first"},
        "user": scope.user,
        "eval_config": {},
        "result": dict(ENGINE_RESULT),
        "error_localizer_enabled": False,
        "trace_eval": True,
        "custom_eval_name": INLINE_NAME,
        "span_id": scope.own.id,
        "workspace": workspace,
    }
    # At the RED pin the additive keyword does not exist. Exercise its real
    # creator with the old signature so failures identify wrong persisted scope
    # or replayable intent, not an unexpected-keyword TypeError. On the fix the
    # supplied organization is passed explicitly; no guarded function is mocked.
    if "organization" in signature(sdk_evaluations._create_evaluation).parameters:
        kwargs["organization"] = organization
    return sdk_evaluations._create_evaluation(**kwargs)


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("effect", ["evaluation", "inline", "worker"])
def test_active_request_org_owns_sync_evaluation_and_inline_result(
    request_scope, batched, effect
):
    scope = request_scope
    results = _post(scope, scope.own.id, batched=batched)
    evaluations = _evaluations(results)
    expected = (scope.organization.id, scope.workspace.id)
    if effect == "evaluation":
        assert all(
            (item.organization_id, item.workspace_id) == expected
            for item in evaluations
        ), (
            "AQ-XWS-W2F: "
            "AQ-XWS-W2D: synchronous evaluations must persist the authorized organization B"
        )
    elif effect == "inline":
        jobs = list(InlineEval.objects.all())
        assert len(jobs) == len(results) and all(
            (job.organization_id, job.workspace_id) == expected
            and job.span_id == scope.own.id
            for job in jobs
        ), (
            "AQ-XWS-W2F: "
            "AQ-XWS-W2D: synchronous results must queue inline jobs bound to B"
        )
    else:
        _process_pending()
        loggers = list(EvalLogger.objects.all())
        assert len(loggers) == len(results) and all(
            item.observation_span_id == scope.own.id
            and item.trace_id == scope.own.trace_id
            and item.custom_eval_config.project_id == scope.own.project_id
            and item.output_bool is True
            for item in loggers
        ), (
            "AQ-XWS-W2F: "
            "AQ-XWS-W2D: the real worker must write the expected B-span results"
        )


@pytest.mark.parametrize("batched", [False, True])
def test_api_key_positive_control_binds_its_aligned_organization(
    request_scope, batched
):
    scope = request_scope
    results = _post(scope, scope.own.id, batched=batched, api_key=True)
    expected = (scope.organization.id, scope.workspace.id)
    assert all(
        (item.organization_id, item.workspace_id) == expected
        for item in _evaluations(results)
    ), "AQ-XWS-W2F: API-key results must persist the key-authorized B binding"
    jobs = list(InlineEval.objects.all())
    assert len(jobs) == len(results) and all(
        (job.organization_id, job.workspace_id) == expected
        and job.span_id == scope.own.id
        for job in jobs
    ), "AQ-XWS-W2F: API-key inline jobs must persist the key-authorized B binding"
    _process_pending()
    loggers = list(EvalLogger.objects.all())
    assert len(loggers) == len(results) and all(
        item.observation_span_id == scope.own.id
        and item.trace_id == scope.own.trace_id
        and item.custom_eval_config.project_id == scope.own.project_id
        and item.output_bool is True
        for item in loggers
    ), "AQ-XWS-W2F: the real worker must retain API-key B-span results"
    assert all(
        job.status == InLineEvalStatus.COMPLETED for job in InlineEval.objects.all()
    ), "AQ-XWS-W2F: API-key inline jobs must complete"


@pytest.mark.parametrize("ambient_legacy", [False, True])
def test_creator_uses_explicit_organization_not_ambient_context(
    request_scope, ambient_legacy
):
    scope = request_scope
    if ambient_legacy:
        set_workspace_context(
            organization=scope.legacy_org,
            workspace=scope.legacy_workspace,
            user=scope.user,
        )
    evaluation = _create(
        scope, organization=scope.organization, workspace=scope.workspace
    )
    clear_workspace_context()
    evaluation.refresh_from_db()
    assert (evaluation.organization_id, evaluation.workspace_id) == (
        scope.organization.id,
        scope.workspace.id,
    ), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: explicit creator organization must win without ambient fallback"
    )


def test_mismatched_organization_workspace_has_no_replayable_intent(request_scope):
    scope = request_scope
    assert scope.workspace.organization_id != scope.legacy_org.id, (
        "SETUP_AQ-XWS-W2F: mismatched workspace must belong to B, not passed organization A"
    )
    evaluation = _create(
        scope, organization=scope.legacy_org, workspace=scope.workspace
    )
    evaluation.refresh_from_db()
    assert evaluation.trace_data is None, (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: inconsistent organization and workspace must not persist inline intent"
    )
    trigger_inline_eval(evaluation)
    _process_pending()
    assert (
        evaluation.value == ENGINE_RESULT["value"] and not InlineEval.objects.exists()
    ), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: inconsistent binding must retain only the standalone result"
    )
    assert not EvalLogger.objects.exists() and not CustomEvalConfig.objects.exists(), (
        "AQ-XWS-W2F: inconsistent creator binding must leave all worker writes empty"
    )


@pytest.mark.parametrize("explicit_organization", [False, True])
def test_workspace_less_creator_keeps_standalone_only(
    request_scope, explicit_organization
):
    scope = request_scope
    evaluation = _create(
        scope, organization=scope.organization if explicit_organization else None
    )
    trigger_inline_eval(evaluation)
    _process_pending()
    assert (
        evaluation.trace_data is None and evaluation.value == ENGINE_RESULT["value"]
    ), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: workspace-less legacy and explicit callers must remain standalone"
    )
    assert not InlineEval.objects.exists() and not EvalLogger.objects.exists(), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: workspace-less evaluations must never admit or replay inline writes"
    )
    assert not CustomEvalConfig.objects.exists(), (
        "AQ-XWS-W2F: workspace-less evaluations must not create inline configurations"
    )


@pytest.mark.parametrize("request_scope", ["single"], indirect=True)
@pytest.mark.parametrize("batched", [False, True])
def test_single_organization_positive_control(request_scope, batched):
    scope = request_scope
    results = _post(scope, scope.own.id, batched=batched)
    evaluations = _evaluations(results)
    assert all(
        (item.organization_id, item.workspace_id)
        == (scope.organization.id, scope.workspace.id)
        for item in evaluations
    ), "AQ-XWS-W2F: AQ-XWS-W2D: single-organization persistence must remain unchanged"
    _process_pending()
    jobs = list(InlineEval.objects.all())
    assert len(jobs) == len(results) and all(
        job.status == InLineEvalStatus.COMPLETED for job in jobs
    ), "AQ-XWS-W2F: AQ-XWS-W2D: single-organization inline jobs must still complete"
    assert EvalLogger.objects.filter(
        observation_span=scope.own, output_bool=True
    ).count() == len(results), (
        "AQ-XWS-W2F: AQ-XWS-W2D: single-organization worker must retain its output"
    )
    assert all(
        result
        == {
            "name": scope.template.name,
            "reason": ENGINE_RESULT["reason"],
            "runtime": ENGINE_RESULT["runtime"],
            "output": ENGINE_RESULT["value"],
            "output_type": ENGINE_RESULT["output"],
            "eval_id": result["eval_id"],
        }
        for result in results
    ), "AQ-XWS-W2F: AQ-XWS-W2D: synchronous response contract must remain unchanged"


@pytest.mark.parametrize("target", ["legacy_org", "peer_workspace"])
@pytest.mark.parametrize("batched", [False, True])
def test_foreign_and_nonexistent_span_have_identical_refusal(
    request_scope, target, batched
):
    scope = request_scope
    foreign = scope.targets[target]
    missing = f"aq-w2d-missing-{uuid.uuid4().hex}"
    assert ObservationSpan.objects.filter(id=foreign.id).exists(), (
        "SETUP_AQ-XWS-W2F: foreign span must exist"
    )
    assert not ObservationSpan.objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-W2F: missing span must not exist"
    )
    shapes = []
    for span_id in (foreign.id, missing):
        with capture_logs() as logs:
            results = _post(scope, span_id, batched=batched)
        shapes.append(
            (
                [
                    {k: v for k, v in result.items() if k != "eval_id"}
                    for result in results
                ],
                InlineEval.objects.count(),
                [log["reason"] for log in logs if "reason" in log],
            )
        )
    assert shapes[0] == shapes[1] and shapes[0][1:] == (
        0,
        [REJECTION_REASON] * (2 if batched else 1),
    ), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: foreign and nonexistent spans must have identical refusal shapes"
    )
    _process_pending()
    assert not EvalLogger.objects.exists() and not CustomEvalConfig.objects.exists(), (
        "AQ-XWS-W2F: AQ-XWS-W2D: refused targets must not produce worker writes"
    )


def test_request_org_can_retrieve_its_standalone_result(request_scope):
    scope = request_scope
    result = _post(scope, scope.own.id)[0]
    response = scope.client.get(EVAL_URL, {"eval_id": str(result["eval_id"])})
    clear_workspace_context()
    assert response.status_code == 200, (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: authorized organization B must retrieve its synchronous result"
    )
    assert response.data["result"]["result"]["value"] == ENGINE_RESULT["value"], (
        "AQ-XWS-W2F: AQ-XWS-W2D: standalone detail must retain the completed result"
    )


def test_other_org_and_nonexistent_evaluation_have_identical_detail_refusal(
    request_scope,
):
    scope = request_scope
    result = _post(scope, scope.own.id)[0]
    shapes = []
    for evaluation_id in (str(result["eval_id"]), str(uuid.uuid4())):
        response = scope.legacy_client.get(EVAL_URL, {"eval_id": evaluation_id})
        shapes.append((response.status_code, response.data))
        clear_workspace_context()
    assert shapes[0] == shapes[1] and shapes[0][0] == 400, (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: other-organization and nonexistent evaluations must look identical"
    )


def test_worker_rechecks_request_binding_after_project_moves(request_scope):
    scope = request_scope
    _post(scope, scope.own.id)
    jobs = list(InlineEval.objects.all())
    assert len(jobs) == 1, (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: request-organization job must be admitted before worker recheck"
    )
    Project.objects.filter(id=scope.own.project_id).update(
        workspace=scope.peer_workspace
    )
    clear_workspace_context()
    assert get_current_organization() is None and get_current_workspace() is None, (
        "SETUP_AQ-XWS-W2F: worker must run without request context"
    )
    _process_pending()
    jobs[0].refresh_from_db()
    assert (
        jobs[0].status == InLineEvalStatus.FAILED and not EvalLogger.objects.exists()
    ), (
        "AQ-XWS-W2F: "
        "AQ-XWS-W2D: worker must refuse targets moved outside the persisted B workspace"
    )
    assert not CustomEvalConfig.objects.exists(), (
        "AQ-XWS-W2F: moved targets must not create worker configurations"
    )

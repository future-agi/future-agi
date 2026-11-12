"""Inline-eval admission and background writes must use the persisted tenant.

Use the tracer project/template fixtures, but create PG spans directly: this
worker reads PG, and the shared observation_span fixture also seeds ClickHouse.
No ORM writes or scope checks are mocked. Clear request context explicitly to
exercise the scheduled worker's unscoped-manager environment.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evaluation import Evaluation, StatusChoices
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_workspace,
)
from tracer.models.custom_eval_config import (
    CustomEvalConfig,
    InlineEval,
    InLineEvalStatus,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.utils.inline_evals import (
    _target_spans,
    process_in_line_evals,
    trigger_inline_eval,
)

pytestmark = pytest.mark.django_db

REJECTION_REASON = "inline_eval_target_not_in_scope"


def _process_pending():
    # Run the real worker body. The Temporal transport wrapper closes DB
    # connections, which would close pytest-django's enclosing transaction.
    process_in_line_evals._original_func()


def _span(project, trace=None):
    trace = trace or Trace.objects.create(project=project, name="AQ W2 trace")
    return ObservationSpan.objects.create(
        id=f"aq-w2-{uuid.uuid4().hex}",
        project=project,
        trace=trace,
        name="AQ W2 span",
        observation_type="llm",
        status="OK",
    )


@pytest.fixture
def scope_setup(organization, workspace, user, project, eval_template):
    clear_workspace_context()
    other_org = Organization.objects.create(name="AQ W2 other organization")
    other_workspace = Workspace.objects.create(
        name="AQ W2 other organization workspace",
        organization=other_org,
        created_by=user,
    )
    peer_workspace = Workspace.objects.create(
        name="AQ W2 peer workspace", organization=organization, created_by=user
    )
    other_project = Project.objects.create(
        name="AQ W2 other organization project",
        organization=other_org,
        workspace=other_workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    peer_project = Project.objects.create(
        name="AQ W2 peer workspace project",
        organization=organization,
        workspace=peer_workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    own = _span(project)
    other = _span(other_project)
    peer = _span(peer_project)
    foreign_trace = _span(project, trace=other.trace)
    assert other_project.organization_id != organization.id, (
        "SETUP_AQ-XWS-W2: foreign project must belong to another organization"
    )
    assert peer_project.organization_id == organization.id, (
        "SETUP_AQ-XWS-W2: peer project must belong to the caller organization"
    )
    assert peer_project.workspace_id != workspace.id, (
        "SETUP_AQ-XWS-W2: peer project must belong to another workspace"
    )
    assert foreign_trace.trace.project_id != foreign_trace.project_id, (
        "SETUP_AQ-XWS-W2: inconsistent span must reference a foreign trace"
    )
    assert not CustomEvalConfig.objects.exists(), (
        "SETUP_AQ-XWS-W2: no pre-existing custom eval configs"
    )
    assert not EvalLogger.objects.exists(), (
        "SETUP_AQ-XWS-W2: no pre-existing eval loggers"
    )
    assert get_current_workspace() is None and get_current_organization() is None, (
        "SETUP_AQ-XWS-W2: background processing must have no request scope"
    )
    yield SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        template=eval_template,
        own=own,
        targets={
            "organization": other,
            "workspace": peer,
            "foreign_trace": foreign_trace,
        },
    )
    clear_workspace_context()


def _evaluation(scope, span_id, *, workspace_bound=True):
    evaluation = Evaluation.objects.create(
        organization=scope.organization,
        workspace=scope.workspace if workspace_bound else None,
        user=scope.user,
        eval_template=scope.template,
        status=StatusChoices.COMPLETED,
        error_localizer_enabled=False,
        value="Passed",
        reason="AQ W2 completed evaluation",
        trace_data={
            "span_id": span_id,
            "custom_eval_name": f"aq-w2-{uuid.uuid4().hex}",
        },
    )
    assert evaluation.status == StatusChoices.COMPLETED, (
        "SETUP_AQ-XWS-W2: evaluation must be completed"
    )
    assert not evaluation.error_localizer_enabled, (
        "SETUP_AQ-XWS-W2: no localizer may block the worker"
    )
    return evaluation


def _pending(evaluation):
    inline_eval = InlineEval.objects.create(
        organization=evaluation.organization,
        workspace=evaluation.workspace,
        evaluation=evaluation,
        span_id=evaluation.trace_data["span_id"],
        custom_eval_name=evaluation.trace_data["custom_eval_name"],
    )
    assert inline_eval.status == InLineEvalStatus.PENDING, (
        "SETUP_AQ-XWS-W2: directly inserted inline eval must be pending"
    )
    return inline_eval


@pytest.mark.parametrize("target", ["organization", "workspace", "foreign_trace"])
@pytest.mark.parametrize("effect", ["logger", "config", "status", "reason"])
def test_worker_rejects_directly_inserted_foreign_target(scope_setup, target, effect):
    """Separate observations ensure each base defect has its own RED message."""
    span = scope_setup.targets[target]
    assert ObservationSpan.objects.filter(id=span.id).exists(), (
        "SETUP_AQ-XWS-W2: foreign span must exist and be visible without context"
    )
    inline_eval = _pending(_evaluation(scope_setup, span.id))

    with capture_logs() as logs:
        _process_pending()

    inline_eval.refresh_from_db()
    if effect == "logger":
        assert not EvalLogger.objects.exists(), (
            "AQ-XWS-W2: worker must not create an EvalLogger for a foreign target"
        )
    elif effect == "config":
        assert not CustomEvalConfig.objects.exists(), (
            "AQ-XWS-W2: worker must not create a CustomEvalConfig for a foreign target"
        )
    elif effect == "status":
        assert inline_eval.status in (
            InLineEvalStatus.FAILED,
            InLineEvalStatus.SKIPPED,
        ), "AQ-XWS-W2: worker must terminate a foreign target as failed or skipped"
    else:
        assert any(log.get("reason") == REJECTION_REASON for log in logs), (
            "AQ-XWS-W2: worker must log the fixed target-scope rejection reason"
        )


@pytest.mark.parametrize(
    "target", ["organization", "workspace", "foreign_trace", "missing"]
)
@pytest.mark.parametrize("effect", ["record", "reason"])
def test_admission_rejects_foreign_or_missing_span(scope_setup, target, effect):
    span_id = (
        f"missing-{uuid.uuid4().hex}"
        if target == "missing"
        else scope_setup.targets[target].id
    )
    evaluation = _evaluation(scope_setup, span_id)
    with capture_logs() as logs:
        trigger_inline_eval(evaluation)

    if effect == "record":
        assert not InlineEval.objects.filter(evaluation=evaluation).exists(), (
            "AQ-XWS-W2: admission must not persist an out-of-scope inline eval"
        )
    else:
        assert any(log.get("reason") == REJECTION_REASON for log in logs), (
            "AQ-XWS-W2: admission must log the fixed target-scope rejection reason"
        )


def test_worker_terminates_missing_target(scope_setup):
    span_id = f"missing-{uuid.uuid4().hex}"
    assert not ObservationSpan.objects.filter(id=span_id).exists(), (
        "SETUP_AQ-XWS-W2: missing target must not exist"
    )
    inline_eval = _pending(_evaluation(scope_setup, span_id))
    _process_pending()
    inline_eval.refresh_from_db()
    assert inline_eval.status in (InLineEvalStatus.FAILED, InLineEvalStatus.SKIPPED), (
        "AQ-XWS-W2: worker must terminate a missing target instead of leaving it pending"
    )


@pytest.mark.parametrize("effect", ["logger", "config", "status", "reason", "replay"])
def test_mixed_scope_batch_does_not_share_target_authority(scope_setup, effect):
    span = scope_setup.targets["workspace"]
    rejected = _pending(_evaluation(scope_setup, span.id))
    evaluation = _evaluation(scope_setup, span.id, workspace_bound=False)
    unbound = _pending(evaluation)
    assert rejected.span_id == unbound.span_id, (
        "SETUP_AQ-XWS-W2C: mixed-scope jobs must reference the same span"
    )
    assert rejected.workspace_id is not None and unbound.workspace_id is None, (
        "SETUP_AQ-XWS-W2C: neither a foreign workspace nor NULL grants target authority"
    )
    before_evaluation = (evaluation.status, evaluation.value, evaluation.reason)
    missing_id = f"missing-{uuid.uuid4().hex}"
    assert not ObservationSpan.objects.filter(pk=missing_id).exists(), (
        "SETUP_AQ-XWS-W2C: the paired unbound worker target must not exist"
    )
    absent = _pending(_evaluation(scope_setup, missing_id, workspace_bound=False))

    with CaptureQueriesContext(connection) as queries, capture_logs() as logs:
        _process_pending()

    unbound.refresh_from_db()
    if effect == "logger":
        names = set(
            EvalLogger.objects.filter(observation_span=span).values_list(
                "custom_eval_config__name", flat=True
            )
        )
        assert names == set(), (
            "AQ-XWS-W2C: unbound mixed-scope jobs must not create an EvalLogger"
        )
    elif effect == "config":
        assert not CustomEvalConfig.objects.exists(), (
            "AQ-XWS-W2C: unbound mixed-scope jobs must not create a CustomEvalConfig"
        )
    elif effect == "status":
        assert unbound.status == InLineEvalStatus.FAILED, (
            "AQ-XWS-W2C: a pending NULL-workspace job must end FAILED"
        )
    elif effect == "reason":
        reasons = [
            [
                (entry.get("event"), entry.get("reason"))
                for entry in logs
                if entry.get("inline_eval_id") == str(job.pk)
            ]
            for job in (unbound, absent)
        ]
        assert (
            reasons[0]
            == reasons[1]
            == [("Skipping inline eval with unavailable target", REJECTION_REASON)]
        ), (
            "AQ-XWS-W2C: existing and absent unbound worker targets must have identical refusals"
        )
    else:
        # A scheduled retry and an explicitly requeued legacy job both fail closed.
        _process_pending()
        InlineEval.objects.filter(pk=unbound.pk).update(status=InLineEvalStatus.PENDING)
        with CaptureQueriesContext(connection) as replay_queries:
            _process_pending()
        unbound.refresh_from_db()
        assert unbound.status == InLineEvalStatus.FAILED, (
            "AQ-XWS-W2C: replaying a NULL-workspace job must still end FAILED"
        )
        for model in (CustomEvalConfig, EvalLogger):
            assert not any(
                f'{verb} "{model._meta.db_table}"' in query["sql"]
                for query in replay_queries
                for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
            ), "AQ-XWS-W2C: replaying an unbound job must attempt no result writes"

    rejected.refresh_from_db()
    assert rejected.status in (InLineEvalStatus.FAILED, InLineEvalStatus.SKIPPED), (
        "AQ-XWS-W2: mixed-scope worker must terminate the unauthorized job"
    )
    absent.refresh_from_db()
    assert unbound.status == absent.status == InLineEvalStatus.FAILED, (
        "AQ-XWS-W2C: existing and absent NULL-workspace jobs must both end FAILED"
    )
    for model in (CustomEvalConfig, EvalLogger):
        assert not model.objects.exists(), (
            "AQ-XWS-W2C: unbound worker jobs must leave zero result rows"
        )
        assert not any(
            f'{verb} "{model._meta.db_table}"' in query["sql"]
            for query in queries
            for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
        ), "AQ-XWS-W2C: unbound worker jobs must attempt no result writes"
    evaluation.refresh_from_db()
    assert (
        evaluation.status,
        evaluation.value,
        evaluation.reason,
    ) == before_evaluation, (
        "AQ-XWS-W2C: refusing an inline job must retain its standalone evaluation"
    )


def test_invalid_target_does_not_wait_for_error_localizer(scope_setup):
    valid_evaluation = _evaluation(scope_setup, scope_setup.own.id)
    invalid_evaluation = _evaluation(
        scope_setup, scope_setup.targets["organization"].id
    )
    Evaluation.objects.filter(
        id__in=[valid_evaluation.id, invalid_evaluation.id]
    ).update(error_localizer_enabled=True)
    valid = _pending(valid_evaluation)
    invalid = _pending(invalid_evaluation)

    _process_pending()

    invalid.refresh_from_db()
    assert invalid.status in (InLineEvalStatus.FAILED, InLineEvalStatus.SKIPPED), (
        "AQ-XWS-W2: invalid target must fail even while its error localizer is pending"
    )
    valid.refresh_from_db()
    assert valid.status == InLineEvalStatus.PENDING, (
        "AQ-XWS-W2: valid target must continue waiting for its error localizer"
    )
    assert not EvalLogger.objects.exists(), (
        "AQ-XWS-W2: waiting or rejected jobs must not create result rows"
    )
    assert not CustomEvalConfig.objects.exists(), (
        "AQ-XWS-W2: waiting or rejected jobs must not create config rows"
    )


@pytest.mark.parametrize("target", ["local", "workspace"])
@pytest.mark.parametrize("effect", ["record", "reason", "replay"])
def test_unbound_admission_refuses_existing_and_absent_targets(
    scope_setup, target, effect
):
    span = scope_setup.own if target == "local" else scope_setup.targets[target]
    missing_id = f"missing-{uuid.uuid4().hex}"
    assert ObservationSpan.objects.filter(pk=span.pk).exists(), (
        "SETUP_AQ-XWS-W2C: the unbound existing target must exist"
    )
    assert not ObservationSpan.objects.filter(pk=missing_id).exists(), (
        "SETUP_AQ-XWS-W2C: the paired absent target must not exist"
    )
    observed = []
    for span_id in (span.pk, missing_id):
        evaluation = _evaluation(scope_setup, span_id, workspace_bound=False)
        assert evaluation.workspace_id is None, (
            "SETUP_AQ-XWS-W2C: the stored evaluation must have no workspace binding"
        )
        before_evaluation = (evaluation.status, evaluation.value, evaluation.reason)
        with CaptureQueriesContext(connection) as queries, capture_logs() as logs:
            trigger_inline_eval(evaluation)
        reasons = [
            (entry.get("event"), entry.get("reason"))
            for entry in logs
            if entry.get("evaluation_id") == str(evaluation.pk)
        ]
        if effect == "record":
            assert not InlineEval.objects.filter(evaluation=evaluation).exists(), (
                "AQ-XWS-W2C: admission must not persist a NULL-workspace inline intent"
            )
        elif effect == "reason":
            assert reasons == [
                (
                    "Skipping inline eval creation with unavailable target",
                    REJECTION_REASON,
                )
            ], "AQ-XWS-W2C: unbound admission must use the fixed refusal reason"
        else:
            with capture_logs() as replay_logs:
                trigger_inline_eval(evaluation)
            assert not InlineEval.objects.filter(evaluation=evaluation).exists(), (
                "AQ-XWS-W2C: replaying stored unbound intent must not create an InlineEval"
            )
            assert [
                (entry.get("event"), entry.get("reason"))
                for entry in replay_logs
                if entry.get("evaluation_id") == str(evaluation.pk)
            ] == reasons, (
                "AQ-XWS-W2C: unbound admission replay must keep its refusal shape"
            )
        observed.append(reasons)
        for model in (InlineEval, CustomEvalConfig, EvalLogger):
            assert not model.objects.exists(), (
                "AQ-XWS-W2C: unbound admission must leave zero inline or result rows"
            )
            assert not any(
                f'{verb} "{model._meta.db_table}"' in query["sql"]
                for query in queries
                for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
            ), "AQ-XWS-W2C: unbound admission must attempt no inline or result writes"
        evaluation.refresh_from_db()
        assert (
            evaluation.status,
            evaluation.value,
            evaluation.reason,
        ) == before_evaluation, (
            "AQ-XWS-W2C: refused unbound intent must retain its standalone result"
        )
    assert (
        observed[0]
        == observed[1]
        == [("Skipping inline eval creation with unavailable target", REJECTION_REASON)]
    ), "AQ-XWS-W2C: existing and absent unbound targets must have identical refusals"


@pytest.mark.parametrize("missing_binding", ["organization", "workspace", "both"])
def test_target_selector_requires_both_bindings(scope_setup, missing_binding):
    organization_id = (
        None
        if missing_binding in ("organization", "both")
        else scope_setup.organization.pk
    )
    workspace_id = (
        None if missing_binding in ("workspace", "both") else scope_setup.workspace.pk
    )
    assert not _target_spans(organization_id, workspace_id).exists(), (
        "AQ-XWS-W2C: a missing organization or workspace must resolve no targets"
    )


def test_same_scope_admission_and_worker_create_logger(scope_setup):
    # Keep the fully bound positive control; NULL is refusal, never wildcard scope.
    span = scope_setup.own
    evaluation = _evaluation(scope_setup, span.id)
    trigger_inline_eval(evaluation)
    inline_evals = list(InlineEval.objects.filter(evaluation=evaluation))
    assert len(inline_evals) == 1, (
        "AQ-XWS-W2: same-scope admission must create exactly one inline eval"
    )
    inline_eval = inline_evals[0]
    assert (inline_eval.organization_id, inline_eval.workspace_id) == (
        evaluation.organization_id,
        evaluation.workspace_id,
    ), "AQ-XWS-W2: admission must persist the evaluation tenant binding"

    _process_pending()

    inline_eval.refresh_from_db()
    assert inline_eval.status == InLineEvalStatus.COMPLETED, (
        "AQ-XWS-W2: same-scope inline eval must complete"
    )
    loggers = list(EvalLogger.objects.filter(observation_span=span))
    assert len(loggers) == 1, (
        "AQ-XWS-W2: same-scope worker must create exactly one EvalLogger"
    )
    result = loggers[0]
    assert result.trace_id == span.trace_id, (
        "AQ-XWS-W2: same-scope logger must retain the validated trace"
    )
    assert result.custom_eval_config.project_id == span.project_id, (
        "AQ-XWS-W2: same-scope config must belong to the validated project"
    )
    assert result.output_bool is True, (
        "AQ-XWS-W2: same-scope logger must preserve evaluation results"
    )

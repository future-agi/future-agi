"""Ground-truth visibility on the simple-eval Observe path.

Composite children reach ``GroundTruthService.inject_context`` through
``run_eval_func``; the simple-eval path does not, so a template with Ground
Truth switched on runs uncalibrated on spans, traces and sessions. These tests
pin the run-level warning that makes that state visible, pin that it keys off
the injected blocks rather than the config, and pin that the run still succeeds.
"""

from __future__ import annotations

import pytest

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle, as in test_eval_task_runtime.py.
import model_hub.tasks  # noqa: F401
from model_hub.models.evals_metric import EvalGroundTruth
from model_hub.utils.eval_input_validation import PARTIAL_INPUT_WARNING_TYPE
from tracer.models.observation_span import EvalLogger
from tracer.utils.eval import GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE as GT_WARNING_TYPE

RUN_PARAMS = {"input": "hello", "output": "world"}


def _make_ground_truth(
    eval_template,
    organization,
    workspace,
    *,
    enabled=True,
    embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
):
    return EvalGroundTruth.objects.create(
        eval_template=eval_template,
        name="labelled answers",
        columns=["question", "answer"],
        data=[{"question": "hello", "answer": "world"}],
        row_count=1,
        variable_mapping={"input": "question"},
        role_mapping={"output": "answer"},
        embedding_status=embedding_status,
        embedded_row_count=1,
        organization=organization,
        workspace=workspace,
        is_active=True,
        enabled=enabled,
    )


def _warnings_on(eval_log):
    return (eval_log.output_metadata or {}).get("warnings") or []


def _gt_warnings(eval_log):
    return [w for w in _warnings_on(eval_log) if w.get("type") == GT_WARNING_TYPE]


def _latest_log(custom_eval_config):
    return (
        EvalLogger.objects.filter(custom_eval_config=custom_eval_config)
        .order_by("-created_at")
        .first()
    )


def _run_span_eval(observation_span, custom_eval_config, eval_task_id=None):
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    _execute_evaluation(
        observation_span_id=observation_span.id,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=eval_task_id,
        type=OBSERVE,
        run_params=dict(RUN_PARAMS),
    )


@pytest.mark.django_db
def test_span_eval_surfaces_ground_truth_not_applied(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    _make_ground_truth(eval_template, organization, workspace)

    _run_span_eval(observation_span, custom_eval_config)

    eval_log = _latest_log(custom_eval_config)
    assert eval_log is not None
    warnings = _gt_warnings(eval_log)
    assert len(warnings) == 1
    # The row carries the type only; the copy lives in the view's table so it
    # is not written to every EvalLogger and APICallLog row.
    assert warnings[0] == {"type": GT_WARNING_TYPE}


@pytest.mark.django_db
def test_span_eval_still_succeeds_with_ground_truth_enabled(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    """Fail-open is the contract: the warning must not turn the run into an error."""
    _make_ground_truth(eval_template, organization, workspace)

    _run_span_eval(observation_span, custom_eval_config)

    eval_log = _latest_log(custom_eval_config)
    assert eval_log.error is False
    assert eval_log.output_str != "ERROR"
    assert _gt_warnings(eval_log)


@pytest.mark.django_db
def test_trace_eval_surfaces_ground_truth_not_applied(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    from tracer.utils.eval import _execute_evaluation_for_trace

    _make_ground_truth(eval_template, organization, workspace)

    _execute_evaluation_for_trace(
        trace=trace,
        anchor_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )

    eval_log = _latest_log(custom_eval_config)
    assert eval_log is not None
    assert len(_gt_warnings(eval_log)) == 1
    assert eval_log.error is False


@pytest.mark.django_db
def test_session_eval_surfaces_ground_truth_not_applied(
    organization,
    workspace,
    observe_project,
    trace_session,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    from tracer.utils.eval import _execute_evaluation_for_session

    _make_ground_truth(eval_template, organization, workspace)

    _execute_evaluation_for_session(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )

    eval_log = _latest_log(custom_eval_config)
    assert eval_log is not None
    assert len(_gt_warnings(eval_log)) == 1
    assert eval_log.error is False


@pytest.mark.django_db
def test_no_warning_when_no_ground_truth_configured(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    _run_span_eval(observation_span, custom_eval_config)

    assert _warnings_on(_latest_log(custom_eval_config)) == []


@pytest.mark.django_db
def test_no_warning_when_ground_truth_is_disabled(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    _make_ground_truth(eval_template, organization, workspace, enabled=False)

    _run_span_eval(observation_span, custom_eval_config)

    assert _gt_warnings(_latest_log(custom_eval_config)) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        EvalGroundTruth.EmbeddingStatus.PENDING,
        EvalGroundTruth.EmbeddingStatus.PROCESSING,
        EvalGroundTruth.EmbeddingStatus.FAILED,
    ],
)
def test_no_warning_until_the_ground_truth_rows_are_embedded(
    status,
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    """An unembedded row is not injected on any path, so Observe is not the fault."""
    _make_ground_truth(
        eval_template, organization, workspace, embedding_status=status
    )

    _run_span_eval(observation_span, custom_eval_config)

    assert _gt_warnings(_latest_log(custom_eval_config)) == []


@pytest.mark.django_db
def test_no_warning_once_the_run_carries_ground_truth_blocks(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """Keys off the injected blocks, so wiring the path retires the warning."""
    _make_ground_truth(eval_template, organization, workspace)
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda _template, values, **_kwargs: (
            None,
            {**values, "ground_truth_blocks": [{"question": "hello"}]},
        ),
    )

    _run_span_eval(observation_span, custom_eval_config)

    assert _gt_warnings(_latest_log(custom_eval_config)) == []


@pytest.mark.django_db
def test_default_workspace_ground_truth_warns_on_the_span_path(
    organization,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    """A project with no workspace resolves to the default one, as trace and session do."""
    from accounts.models.workspace import Workspace

    default_ws = Workspace.objects.filter(
        organization=organization, is_default=True, is_active=True
    ).first()
    project.workspace = None
    project.save(update_fields=["workspace"])
    _make_ground_truth(eval_template, organization, default_ws)

    _run_span_eval(observation_span, custom_eval_config)

    assert len(_gt_warnings(_latest_log(custom_eval_config))) == 1


@pytest.mark.django_db
def test_workspaceless_project_keeps_a_null_workspace_on_the_eval_request(
    organization,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """The GT lookup resolves the default workspace; the eval request must not.

    ``ws_id`` also picks the provider API key in ``run_eval`` and the billing
    attribution in ``_emit_eval_billing``, so it stays the project's own.
    """
    import evaluations.engine as engine
    from accounts.models.workspace import Workspace

    default_ws = Workspace.objects.filter(
        organization=organization, is_default=True, is_active=True
    ).first()
    project.workspace = None
    project.save(update_fields=["workspace"])
    _make_ground_truth(eval_template, organization, default_ws)

    seen = {}
    stubbed = engine.run_eval

    def _capture(request):
        seen["workspace_id"] = request.workspace_id
        return stubbed(request)

    monkeypatch.setattr(engine, "run_eval", _capture)

    _run_span_eval(observation_span, custom_eval_config)

    assert seen["workspace_id"] is None
    # ...and the warning still fires, because its lookup resolved the default.
    assert len(_gt_warnings(_latest_log(custom_eval_config))) == 1


@pytest.mark.django_db
def test_no_warning_for_another_tenants_ground_truth(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="other org")
    _make_ground_truth(eval_template, other_org, None)

    _run_span_eval(observation_span, custom_eval_config)

    assert _gt_warnings(_latest_log(custom_eval_config)) == []


@pytest.mark.django_db
def test_eval_runs_when_ground_truth_lookup_raises(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """A broken GT lookup must never block the eval. Fail open, no warning."""
    from model_hub.services.ground_truth_service import GroundTruthService

    _make_ground_truth(eval_template, organization, workspace)

    def _boom(**_kwargs):
        raise RuntimeError("gt_lookup_blew_up")

    monkeypatch.setattr(
        GroundTruthService, "is_enabled_for_template", staticmethod(_boom)
    )

    _run_span_eval(observation_span, custom_eval_config)

    eval_log = _latest_log(custom_eval_config)
    assert eval_log.error is False
    assert _gt_warnings(eval_log) == []


@pytest.mark.django_db
def test_partial_input_and_ground_truth_warnings_coexist(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """Adding the GT warning must not displace the partial-input one."""
    partial = {
        "type": PARTIAL_INPUT_WARNING_TYPE,
        "empty_keys": ["output"],
        "filled_keys": ["input"],
    }

    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda _template, values, **_kwargs: (partial, values),
    )
    _make_ground_truth(eval_template, organization, workspace)

    _run_span_eval(observation_span, custom_eval_config)

    types = [w.get("type") for w in _warnings_on(_latest_log(custom_eval_config))]
    assert types == [PARTIAL_INPUT_WARNING_TYPE, GT_WARNING_TYPE]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_task_logs_endpoint_reports_the_ground_truth_warning_group(
    auth_client,
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """The signal has to reach the surface a user actually opens."""
    _make_ground_truth(eval_template, organization, workspace)

    _run_span_eval(observation_span, custom_eval_config, eval_task_id=eval_task.id)

    response = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/",
        {"eval_task_id": str(eval_task.id)},
    )
    assert response.status_code == 200

    payload = response.json()
    result = payload.get("result", payload)
    assert result["warnings_count"] == 1

    groups = [g for g in result["warning_groups"] if g["type"] == GT_WARNING_TYPE]
    assert len(groups) == 1
    assert groups[0]["count"] == 1
    # The row persists the type only; the copy comes from the view's table.
    assert "expected_value" in groups[0]["message"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_task_logs_endpoint_counts_rows_while_groups_count_warnings(
    auth_client,
    organization,
    workspace,
    project,
    trace,
    observation_span,
    eval_template,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """One row, two warnings: the header is runs, the groups are occurrences."""
    partial = {
        "type": PARTIAL_INPUT_WARNING_TYPE,
        "empty_keys": ["output"],
        "filled_keys": ["input"],
    }
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda _template, values, **_kwargs: (partial, values),
    )
    _make_ground_truth(eval_template, organization, workspace)

    _run_span_eval(observation_span, custom_eval_config, eval_task_id=eval_task.id)

    response = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/",
        {"eval_task_id": str(eval_task.id)},
    )
    assert response.status_code == 200
    result = response.json().get("result", response.json())

    assert result["warnings_count"] == 1
    counts = {g["type"]: g["count"] for g in result["warning_groups"]}
    assert counts[PARTIAL_INPUT_WARNING_TYPE] == 1
    assert counts[GT_WARNING_TYPE] == 1
    assert sum(counts.values()) == 2

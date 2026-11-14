"""Follow-up work on eval results against a lagging PostgreSQL replica.

Every test opts the nine TH-3119 models in to the real read router and uses
``tfc.tests.lagging_replica``: a second PostgreSQL connection frozen in a
REPEATABLE READ snapshot. Rows written after the freeze are on the primary
only, as on a streaming standby that has not applied them yet.

Covered consumers: the error localizer's Observe write-back (C13), eval-result
clustering (C29), inline SDK evals (C56), the experiment eval scheduler and
the runner it dispatches (C61), the trace error-analysis task API (D05) and
feedback re-evaluation (D20).

Only external boundaries are replaced: the localizer model call and its
billing hooks, embedding/serving and the ClickHouse centroid store, the
evaluator engine and cost log, and workflow dispatch. ORM routing, transactions
and the stored rows are real.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import FeedbackSourceChoices, OwnerChoices, StatusType
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import EvalTemplate, Feedback
from model_hub.models.evaluation import Evaluation, StatusChoices
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.routers import force_primary
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.custom_eval_config import (
    CustomEvalConfig,
    InlineEval,
    InLineEvalStatus,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_error_analysis_task import (
    TraceErrorAnalysisTask,
    TraceErrorTaskStatus,
)
from tracer.services.eval_tasks.entries import soft_delete_eval_results

pytestmark = pytest.mark.django_db(transaction=True)

_EMBEDDING = [0.1, 0.2, 0.3]


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


def _primary_logger(pk):
    return EvalLogger.all_objects.using("default").get(pk=pk)


# -- C13: error localizer write-back onto an Observe eval result -------------


@pytest.fixture
def localizer_template(organization, workspace):
    return EvalTemplate.objects.create(
        name="localizer-" + uuid.uuid4().hex[:6],
        description="error localizer gate",
        owner=OwnerChoices.USER.value,
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        template_type="single",
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
        choice_scores={},
        config={"rule_prompt": "is the answer grounded?"},
        choices=[],
        model="turing_large",
    )


_ANALYSIS = {"input": [{"segment": "Hello", "reason": "ungrounded"}]}


@pytest.fixture
def localizer(monkeypatch):
    """Replace the localizer model call and its billing hooks."""
    import ee.usage.services.emitter as emitter
    import ee.usage.services.metering as metering
    import model_hub.tasks.user_evaluation as user_evaluation

    class _Localizer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def localize_errors(self):
            return SimpleNamespace(
                analysis=_ANALYSIS, selected_key="input", skip_reason=None
            )

    refunds = []
    row = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value, save=lambda **kwargs: None
    )
    monkeypatch.setattr(user_evaluation, "ErrorLocalizer", _Localizer)
    monkeypatch.setattr(
        user_evaluation, "log_and_deduct_cost_for_api_request", lambda **kw: row
    )
    monkeypatch.setattr(user_evaluation, "refund_cost_for_api_call", refunds.append)
    monkeypatch.setattr(
        metering,
        "check_usage",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=None),
    )
    monkeypatch.setattr(emitter, "emit", lambda event: None)
    return SimpleNamespace(refunds=refunds)


def _observe_logger(trace, span, config, metadata):
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=config,
        output_bool=False,
        eval_explanation="the answer ignores the context",
        output_metadata=metadata,
    )


def _localize(template, eval_logger):
    from model_hub.tasks.user_evaluation import process_single_error_localization

    task = ErrorLocalizerTask.objects.create(
        eval_template=template,
        source=ErrorLocalizerSource.OBSERVE,
        source_id=eval_logger.id,
        input_data={"input": "Hello"},
        input_keys=["input"],
        input_types={"input": "text"},
        eval_result="Failed",
        eval_explanation="the answer ignores the context",
        rule_prompt="is the answer grounded?",
        organization=template.organization,
        workspace=template.workspace,
        status=ErrorLocalizerStatus.PENDING,
    )
    process_single_error_localization._original_func(str(task.id))
    task.refresh_from_db()
    return task


def test_c13_localizer_keeps_metadata_the_result_wrote_after_freeze(
    lagging_replica,  # noqa: F811
    localizer,
    localizer_template,
    trace,
    observation_span,
    custom_eval_config,
):
    eval_logger = _observe_logger(
        trace, observation_span, custom_eval_config, {"tokens": 3}
    )
    lagging_replica.freeze()
    # The eval result is re-scored on the primary moments before the
    # localizer runs: the replica still has the old metadata.
    EvalLogger.objects.filter(pk=eval_logger.pk).update(
        output_metadata={"tokens": 3, "partial_input_warning": "output was empty"}
    )

    task = _localize(localizer_template, eval_logger)

    assert task.status == ErrorLocalizerStatus.COMPLETED, task.error_message
    metadata = _primary_logger(eval_logger.pk).output_metadata
    assert metadata["partial_input_warning"] == "output was empty"
    assert metadata["error_analysis"] == _ANALYSIS
    assert metadata["selected_input_key"] == "input"
    assert localizer.refunds == []


def test_c13_localizer_writes_back_onto_result_created_after_freeze(
    lagging_replica,  # noqa: F811
    localizer,
    localizer_template,
    trace,
    observation_span,
    custom_eval_config,
):
    lagging_replica.freeze()
    eval_logger = _observe_logger(
        trace, observation_span, custom_eval_config, {"tokens": 3}
    )
    assert not EvalLogger.objects.filter(pk=eval_logger.pk).exists()

    task = _localize(localizer_template, eval_logger)

    assert task.status == ErrorLocalizerStatus.COMPLETED, task.error_message
    metadata = _primary_logger(eval_logger.pk).output_metadata
    assert metadata["tokens"] == 3
    assert metadata["error_analysis"] == _ANALYSIS
    assert localizer.refunds == []


def test_c13_localizer_writes_back_onto_result_of_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    localizer,
    localizer_template,
    project,
    eval_template,
    trace,
    observation_span,
):
    """A new eval (config) whose first result fails: saving the analysis
    validates the result's config, which the replica does not have yet."""
    lagging_replica.freeze()
    config = CustomEvalConfig.objects.create(
        name="localizer-fresh-" + uuid.uuid4().hex[:6],
        project=project,
        eval_template=eval_template,
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    # The eval run that wrote the result runs on the primary.
    with force_primary():
        eval_logger = _observe_logger(trace, observation_span, config, {"tokens": 3})
    assert not CustomEvalConfig.objects.filter(pk=config.pk).exists()

    task = _localize(localizer_template, eval_logger)

    assert task.status == ErrorLocalizerStatus.COMPLETED, task.error_message
    metadata = _primary_logger(eval_logger.pk).output_metadata
    assert metadata["tokens"] == 3
    assert metadata["error_analysis"] == _ANALYSIS
    assert localizer.refunds == []


# -- C29: eval-result clustering drain ----------------------------------------


@pytest.fixture
def clustering(monkeypatch):
    """Embedding/serving and the ClickHouse centroid store, nothing else.

    The centroid read returns no row, so every fetched result takes the
    create path; a result fetched twice joins its own cluster again.
    """
    import tracer.queries.eval_clustering as queries
    import tracer.utils.eval_clustering as service

    store = MagicMock()
    store.return_value.execute_read.return_value = []
    monkeypatch.setattr(service, "serving_available", lambda: True)
    monkeypatch.setattr(
        service, "embed_texts", lambda texts: [list(_EMBEDDING) for _ in texts]
    )
    monkeypatch.setattr(service, "distill_eval_failure_phrases", lambda results: None)
    monkeypatch.setattr(queries, "ClickHouseVectorDB", store)
    monkeypatch.setattr(queries, "ensure_centroid_table", MagicMock())
    monkeypatch.setattr(
        "tracer.ee_boundary.generate_eval_cluster_meta", lambda *a, **k: None
    )


def _failing_eval(trace, span, config):
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=config,
        target_type="span",
        output_bool=False,
        eval_explanation="assistant ignored the retrieved context",
        eval_task_id="et-" + uuid.uuid4().hex[:8],
    )


def _memberships(eval_logger):
    return ErrorClusterTraces.objects.filter(
        eval_logger_id=eval_logger.id, deleted=False
    )


def test_c29_second_drain_batch_sees_membership_written_after_freeze(
    lagging_replica,  # noqa: F811
    clustering,
    project,
    trace,
    observation_span,
    custom_eval_config,
):
    from tracer.utils.eval_clustering import cluster_eval_results

    failing = _failing_eval(trace, observation_span, custom_eval_config)
    lagging_replica.freeze()

    first = cluster_eval_results(str(project.id))
    second = cluster_eval_results(str(project.id))

    assert (first.fetched, first.new_clusters) == (1, 1)
    assert second.fetched == 0
    assert _memberships(failing).count() == 1
    cluster = TraceErrorGroup.objects.get(project_id=project.id)
    assert (cluster.error_count, cluster.total_events) == (1, 1)


def test_c29_drain_task_clusters_each_failure_once(
    lagging_replica,  # noqa: F811
    clustering,
    project,
    trace,
    observation_span,
    custom_eval_config,
):
    from tracer.tasks.eval_clustering import cluster_eval_results_task

    failing = _failing_eval(trace, observation_span, custom_eval_config)
    lagging_replica.freeze()

    summary = cluster_eval_results_task._original_func(str(project.id))

    assert summary == {"clustered": 1, "new_clusters": 1, "assigned": 0}
    assert _memberships(failing).count() == 1
    assert TraceErrorGroup.objects.get(project_id=project.id).error_count == 1


# -- C56: inline SDK evals on a span ingested moments before ------------------


def test_c56_inline_eval_completes_for_span_ingested_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    trace,
    user,
    eval_template,
):
    from tracer.utils.inline_evals import process_in_line_evals, trigger_inline_eval

    lagging_replica.freeze()
    span = ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        name="sdk span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
    )
    evaluation = Evaluation.objects.create(
        user=user,
        organization=project.organization,
        workspace=project.workspace,
        eval_template=eval_template,
        status=StatusChoices.COMPLETED,
        value="Passed",
        reason="grounded",
        output_bool=True,
        trace_data={"span_id": span.id, "custom_eval_name": "sdk-groundedness"},
    )
    trigger_inline_eval(evaluation)  # the SDK evaluation handoff

    # The scheduled worker runs without a request's workspace context.
    clear_workspace_context()
    process_in_line_evals._original_func()

    inline_eval = InlineEval.objects.get(evaluation=evaluation)
    assert inline_eval.status == InLineEvalStatus.COMPLETED
    results = EvalLogger.objects.using("default").filter(observation_span_id=span.id)
    assert results.count() == 1
    result = results.get()
    config = CustomEvalConfig.objects.using("default").get(
        pk=result.custom_eval_config_id
    )
    assert (config.name, config.project_id) == ("sdk-groundedness", project.id)
    assert (result.trace_id, result.output_bool) == (trace.id, True)


# -- C61: the experiment eval scheduler -----------------------------------------


@pytest.fixture
def scheduled_span(project, trace, project_version):
    project_version.eval_tags = [{"custom_eval_name": "experiment-check"}]
    project_version.save(update_fields=["eval_tags"])
    return ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex[:16],
        project=project,
        project_version=project_version,
        trace=trace,
        name="experiment span",
        observation_type="llm",
        eval_status=StatusType.NOT_STARTED.value,
        start_time=timezone.now() - timedelta(seconds=2),
    )


@pytest.fixture
def dispatched(monkeypatch):
    import tracer.utils.span as span_module

    calls = []
    monkeypatch.setattr(
        span_module,
        "eval_observation_span_runner",
        SimpleNamespace(delay=lambda span_id, tags: calls.append(span_id)),
    )
    return calls


def test_c61_scheduler_dispatches_span_once_across_polls(
    lagging_replica,  # noqa: F811
    scheduled_span,
    dispatched,
):
    from tracer.utils.span import run_evals_on_spans

    lagging_replica.freeze()
    run_evals_on_spans._original_func()
    run_evals_on_spans._original_func()

    assert dispatched == [scheduled_span.id]
    span = ObservationSpan.objects.using("default").get(pk=scheduled_span.pk)
    assert span.eval_status == StatusType.RUNNING.value


def test_c61_second_poll_keeps_what_the_runner_wrote(
    lagging_replica,  # noqa: F811
    scheduled_span,
    dispatched,
):
    from tracer.utils.span import run_evals_on_spans

    lagging_replica.freeze()
    run_evals_on_spans._original_func()
    # The dispatched runner finishes on the primary before the next poll.
    ObservationSpan.objects.filter(pk=scheduled_span.pk).update(
        eval_status=StatusType.COMPLETED.value, tags=["scored"]
    )
    run_evals_on_spans._original_func()

    span = ObservationSpan.objects.using("default").get(pk=scheduled_span.pk)
    assert (span.eval_status, span.tags) == (StatusType.COMPLETED.value, ["scored"])
    assert dispatched == [scheduled_span.id]


@pytest.fixture
def runner_inline(monkeypatch):
    """Run each span the scheduler dispatches in place, as its worker would."""
    from tracer.utils.eval import eval_observation_span_runner

    # The scheduler imported this same activity object, so it sees the patch.
    monkeypatch.setattr(
        eval_observation_span_runner,
        "delay",
        eval_observation_span_runner._original_func,
    )


@pytest.mark.parametrize("read_source", ["postgres", "clickhouse"])
def test_c61_runner_scores_experiment_span_ingested_after_freeze(
    lagging_replica,  # noqa: F811
    settings,
    ch_seed,
    project,
    eval_template,
    runner_inline,
    stub_run_eval,
    stub_cost_log,
    read_source,
):
    from tracer.utils.otel import get_or_create_project_version
    from tracer.utils.span import run_evals_on_spans

    settings.EVAL_SPAN_READ_SOURCE = read_source
    lagging_replica.freeze()
    # A new experiment run: ingestion creates its eval config and version,
    # then its trace and span, all after the replica's snapshot.
    eval_name = "experiment-" + uuid.uuid4().hex[:6]
    version = get_or_create_project_version(
        project_id=project.id,
        project_version_name="run after freeze",
        project_version_id=None,
        eval_tags=[
            {
                "custom_eval_name": eval_name,
                "eval_name": eval_template.name,
                "mapping": {"input": "input", "output": "output"},
                "type": "OBSERVATION_SPAN_TYPE",
                "value": "llm",
            }
        ],
        metadata={},
        project_type="experiment",
    )
    config = CustomEvalConfig.objects.using("default").get(
        project=project, name=eval_name
    )
    trace = Trace.objects.create(
        project=project, project_version=version, name="experiment trace"
    )
    span = ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex[:16],
        project=project,
        project_version=version,
        trace=trace,
        name="experiment span",
        observation_type="llm",
        eval_status=StatusType.NOT_STARTED.value,
        start_time=timezone.now() - timedelta(seconds=2),
        input={"question": "What is the capital of France?"},
        output={"answer": "Paris"},
    )
    if read_source == "clickhouse":
        ch_seed(span)
    # The replica has none of it yet.
    assert not CustomEvalConfig.objects.filter(pk=config.pk).exists()
    assert not ObservationSpan.objects.filter(pk=span.pk).exists()

    # The scheduled worker runs without a request's workspace context.
    clear_workspace_context()
    run_evals_on_spans._original_func()

    result = EvalLogger.objects.using("default").get(observation_span_id=span.id)
    assert (result.custom_eval_config_id, result.trace_id, result.error) == (
        config.id,
        trace.id,
        False,
    )
    assert result.eval_explanation == "stubbed pass"
    assert len(stub_run_eval.requests) == 1
    if read_source == "postgres":
        # A span read from ClickHouse does not write its status back to
        # Postgres (see eval_loader), so only this mode records it.
        stored = ObservationSpan.objects.using("default").get(pk=span.pk)
        assert stored.eval_status == StatusType.COMPLETED.value


def test_c61_runner_raises_the_load_error_for_a_missing_span():
    from tracer.utils.eval import eval_observation_span_runner

    tags = [{"type": "OBSERVATION_SPAN_TYPE", "value": "llm"}]
    # Raised, not swallowed, so the activity retries a failed span read.
    with pytest.raises(ObservationSpan.DoesNotExist):
        eval_observation_span_runner._original_func("span_missing", tags)


# -- D05: the trace error-analysis task API -----------------------------------


def _task_url(project):
    return f"/tracer/trace-error-task/{project.id}/"


def _tasks(project):
    return TraceErrorAnalysisTask.all_objects.using("default").filter(project=project)


def test_d05_second_get_after_freeze_returns_the_created_task(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    user,
):
    lagging_replica.freeze()
    first = auth_client.get(_task_url(project))
    second = auth_client.get(_task_url(project))

    assert first.status_code == 200, first.data
    assert second.status_code == 200, second.data
    assert first.data["result"]["created"] is True
    assert second.data["result"]["created"] is False
    assert _tasks(project).count() == 1

    foreign_org = Organization.objects.create(name="Foreign organization")
    foreign = Project.objects.create(
        name="Foreign project",
        organization=foreign_org,
        workspace=Workspace.objects.create(
            name="Foreign workspace",
            organization=foreign_org,
            created_by=user,
            is_active=True,
        ),
        model_type=project.model_type,
        trace_type="observe",
    )
    denied = auth_client.get(_task_url(foreign))
    assert denied.status_code == 404, denied.data
    assert not _tasks(foreign).exists()


def test_d05_post_after_get_updates_the_created_task(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    lagging_replica.freeze()
    created = auth_client.get(_task_url(project))
    updated = auth_client.post(
        _task_url(project), {"sampling_rate": 0.3}, format="json"
    )

    assert created.status_code == 200, created.data
    assert updated.status_code == 200, updated.data
    assert (updated.data["result"]["old_rate"], updated.data["result"]["new_rate"]) == (
        0.01,
        0.3,
    )
    assert _tasks(project).get().sampling_rate == 0.3


def test_d05_restored_task_keeps_configuration_set_after_restore(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    retired = TraceErrorAnalysisTask.objects.create(
        project=project, sampling_rate=0.42, status=TraceErrorTaskStatus.PAUSED
    )
    retired.delete()
    lagging_replica.freeze()

    restored = auth_client.get(_task_url(project))
    configured = auth_client.post(
        _task_url(project),
        {"sampling_rate": 0.3, "status": TraceErrorTaskStatus.PAUSED},
        format="json",
    )
    reread = auth_client.get(_task_url(project))

    assert restored.status_code == 200, restored.data
    assert restored.data["result"]["created"] is True
    assert configured.status_code == 200, configured.data
    assert reread.status_code == 200, reread.data
    result = reread.data["result"]
    assert (result["created"], result["sampling_rate"], result["status"]) == (
        False,
        0.3,
        TraceErrorTaskStatus.PAUSED,
    )
    task = _tasks(project).get()
    assert (task.deleted, task.sampling_rate) == (False, 0.3)


# -- D20: feedback re-evaluation of a task-less eval result -------------------


def _feedback(user, span, config):
    return Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(span.id),
        value="Failed",
        eval_template=config.eval_template,
        user=user,
        custom_eval_config_id=config.id,
        organization=span.project.organization,
        workspace=span.project.workspace,
    )


def test_d20_reevaluation_runs_after_result_retired_after_freeze(
    lagging_replica,  # noqa: F811
    monkeypatch,
    observation_span,
    custom_eval_config,
    trace,
):
    import tracer.utils.eval as eval_module

    old = _observe_logger(trace, observation_span, custom_eval_config, {})
    lagging_replica.freeze()
    soft_delete_eval_results(EvalLogger.objects.filter(pk=old.pk))
    runs = []
    monkeypatch.setattr(
        eval_module,
        "_execute_evaluation",
        lambda **kwargs: runs.append(kwargs) or "persisted",
    )

    status = eval_module.evaluate_observation_span_observe._original_func(
        str(observation_span.id), str(custom_eval_config.id), None, "feedback-1"
    )

    assert status is True
    assert [(r["eval_task_id"], r["feedback_id"]) for r in runs] == [
        (None, "feedback-1")
    ]


def test_d20_feedback_recalculate_replaces_task_less_result(
    lagging_replica,  # noqa: F811
    monkeypatch,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    trace,
    stub_run_eval,
    stub_cost_log,
):
    monkeypatch.setattr(
        "tracer.views.observation_span.track_mixpanel_event", lambda *a, **k: None
    )
    old = _observe_logger(trace, observation_span, custom_eval_config, {})
    feedback = _feedback(user, observation_span, custom_eval_config)
    lagging_replica.freeze()

    response = auth_client.post(
        "/tracer/observation-span/submit_feedback_action_type/",
        {
            "observation_span_id": observation_span.id,
            "action_type": "recalculate",
            "custom_eval_config_id": str(custom_eval_config.id),
            "feedback_id": str(feedback.id),
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert _primary_logger(old.pk).deleted is True
    live = EvalLogger.objects.using("default").filter(
        observation_span=observation_span, custom_eval_config=custom_eval_config
    )
    assert live.count() == 1
    replacement = live.get()
    assert replacement.pk != old.pk
    assert replacement.eval_explanation == "stubbed pass"
    assert len(stub_run_eval.requests) == 1

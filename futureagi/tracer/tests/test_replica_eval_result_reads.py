"""Readers of eval results against a lagging PostgreSQL replica.

Every test opts the nine TH-3119 models in to the real read router and uses
``tfc.tests.lagging_replica``: a second PostgreSQL connection frozen in a
REPEATABLE READ snapshot. Rows written after the freeze are on the primary
only, as on a streaming standby that has not applied them yet.

Covered readers: the eval task logs panel (D23), feedback on an Observe eval
result (D27) and its recalculate action (D28), and the copy of eval results
into a dataset (D16). The UI shows eval results from ClickHouse, which can be
ahead of the replica, so each of these acts on a result the user has seen.

Only external boundaries are replaced: the feedback embedding store, the
evaluator engine and cost log, analytics, and the ClickHouse span export.
ORM routing, transactions and the stored rows are real.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    FeedbackSourceChoices,
    ModelTypes,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.evals_metric import Feedback
from tfc.routers import force_primary
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.tests.eval_task_factories import make_fresh_span, make_row

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


def _result(trace, span, config, **fields):
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=config,
        output_bool=False,
        eval_explanation="the answer ignores the context",
        **fields,
    )


# -- D23: eval task logs pair the task status with final tallies -------------


def test_d23_completed_logs_carry_the_final_tallies(
    lagging_replica,  # noqa: F811
    auth_client,
    eval_task,
    observation_span,
    custom_eval_config,
):
    rows = [
        make_row(
            span=make_fresh_span(observation_span),
            cfg=custom_eval_config,
            task=eval_task,
            status=EvalEntryStatus.RUNNING,
        )
        for _ in range(3)
    ]
    lagging_replica.freeze()
    # The drain settles every entry and finalizes the task on the primary;
    # the replica still has all three running.
    EvalLogger.objects.filter(pk=rows[0].pk).update(
        status=EvalEntryStatus.COMPLETED, output_bool=True
    )
    EvalLogger.objects.filter(pk=rows[1].pk).update(
        status=EvalEntryStatus.COMPLETED,
        output_bool=True,
        output_metadata={
            "warnings": [{"type": "partial_input", "empty_keys": ["context"]}]
        },
    )
    EvalLogger.objects.filter(pk=rows[2].pk).update(
        status=EvalEntryStatus.ERRORED,
        error=True,
        error_message="Error during evaluation: judge timed out",
    )
    EvalTask.objects.filter(pk=eval_task.pk).update(status=EvalTaskStatus.COMPLETED)
    assert EvalLogger.objects.filter(
        eval_task_id=str(eval_task.id), status=EvalEntryStatus.RUNNING
    ).count() == len(rows)

    response = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/",
        {"eval_task_id": str(eval_task.id)},
    )

    assert response.status_code == 200, response.data
    logs = response.data["result"]
    assert logs["status"] == EvalTaskStatus.COMPLETED
    assert (
        logs["total_count"],
        logs["success_count"],
        logs["errors_count"],
        logs["warnings_count"],
    ) == (3, 2, 1, 1)
    assert [(g["normalized"], g["count"]) for g in logs["error_groups"]] == [
        ("judge timed out", 1)
    ]
    assert [(g["type"], g["count"]) for g in logs["warning_groups"]] == [
        ("partial_input", 1)
    ]


def test_d23_logs_of_another_workspace_task_stay_hidden(
    lagging_replica,  # noqa: F811
    auth_client,
    user,
    eval_task,
    observation_span,
    custom_eval_config,
):
    from tracer.tests.test_eval_task import make_other_workspace_eval_task

    other = make_other_workspace_eval_task(eval_task.project, user, custom_eval_config)
    lagging_replica.freeze()
    make_row(span=observation_span, cfg=custom_eval_config, task=other)

    response = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/",
        {"eval_task_id": str(other.id)},
    )

    assert response.status_code == 400
    assert "total_count" not in json.dumps(response.data, default=str)


# -- D27 / D28: feedback on an Observe eval result ----------------------------


class _NoEmbeddings:
    """The feedback few-shot store (a vector database), not under test."""

    stored = []

    def data_formatter(self, **kwargs):
        self.stored.append(kwargs)

    def close(self):
        pass


@pytest.fixture
def no_embeddings(monkeypatch):
    _NoEmbeddings.stored = []
    monkeypatch.setattr("tracer.views.observation_span.EmbeddingManager", _NoEmbeddings)
    monkeypatch.setattr(
        "tracer.views.observation_span.track_mixpanel_event", lambda *a, **k: None
    )
    return _NoEmbeddings


def _submit_feedback(client, span, config):
    return client.post(
        "/tracer/observation-span/submit_feedback/",
        {
            "observation_span_id": span.id,
            "custom_eval_config_id": str(config.id),
            "feedback_value": "Passed",
            "feedback_explanation": "the context does answer it",
        },
        format="json",
    )


def test_d27_feedback_on_result_created_after_freeze(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    user,
    trace,
    observation_span,
    custom_eval_config,
):
    lagging_replica.freeze()
    _result(trace, observation_span, custom_eval_config)

    response = _submit_feedback(auth_client, observation_span, custom_eval_config)

    assert response.status_code == 200, response.data
    feedback = Feedback.objects.using("default").get(
        pk=response.data["result"]["feedback_id"]
    )
    assert (feedback.source, feedback.source_id, feedback.value, feedback.user_id) == (
        FeedbackSourceChoices.OBSERVE.value,
        observation_span.id,
        "Passed",
        user.id,
    )
    assert feedback.custom_eval_config_id == custom_eval_config.id
    assert len(no_embeddings.stored) == 1


def test_d27_feedback_refused_once_result_is_retired_after_freeze(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    trace,
    observation_span,
    custom_eval_config,
):
    from tracer.services.eval_tasks.entries import soft_delete_eval_results

    old = _result(trace, observation_span, custom_eval_config)
    lagging_replica.freeze()
    soft_delete_eval_results(EvalLogger.objects.filter(pk=old.pk))

    response = _submit_feedback(auth_client, observation_span, custom_eval_config)

    assert response.status_code == 400
    assert (
        not Feedback.objects.using("default")
        .filter(source_id=observation_span.id)
        .exists()
    )
    assert no_embeddings.stored == []


def test_d28_recalculate_result_created_after_freeze(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    user,
    trace,
    observation_span,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    feedback = Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(observation_span.id),
        value="Passed",
        eval_template=custom_eval_config.eval_template,
        user=user,
        custom_eval_config_id=custom_eval_config.id,
        organization=observation_span.project.organization,
        workspace=observation_span.project.workspace,
    )
    lagging_replica.freeze()
    fresh = _result(trace, observation_span, custom_eval_config)
    assert not EvalLogger.objects.filter(pk=fresh.pk).exists()

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
    assert EvalLogger.all_objects.using("default").get(pk=fresh.pk).deleted is True
    live = EvalLogger.objects.using("default").filter(
        observation_span=observation_span, custom_eval_config=custom_eval_config
    )
    assert live.count() == 1
    assert live.get().pk != fresh.pk
    assert len(stub_run_eval.requests) == 1
    assert Feedback.objects.using("default").get(pk=feedback.pk).action_type == (
        "recalculate"
    )


# -- R5: feedback and result details on a span or config the replica lacks ---


def _after_freeze_span_and_config(project, eval_template):
    """A trace, its span and an eval config written after the freeze: on the
    primary only, as when the result is shown from ClickHouse before the
    replica has applied them."""
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace

    trace = Trace.objects.create(project=project, name="fresh trace")
    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="fresh span",
        observation_type="llm",
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi there"}}]},
    )
    config = CustomEvalConfig.objects.create(
        name=f"fresh-{uuid.uuid4().hex[:8]}",
        project=project,
        eval_template=eval_template,
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    assert not ObservationSpan.objects.filter(pk=span.pk).exists()
    assert not Trace.objects.filter(pk=trace.pk).exists()
    assert not CustomEvalConfig.objects.filter(pk=config.pk).exists()
    return trace, span, config


def test_r5_feedback_on_span_trace_and_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    user,
    project,
    eval_template,
):
    """Feedback reads the span, its config and its trace: all of them may be
    newer than the replica, not only the result."""
    lagging_replica.freeze()
    trace, span, config = _after_freeze_span_and_config(project, eval_template)
    # The eval run that wrote the result ran on the primary.
    with force_primary():
        _result(trace, span, config)

    response = _submit_feedback(auth_client, span, config)

    assert response.status_code == 200, response.data
    feedback = Feedback.objects.using("default").get(
        pk=response.data["result"]["feedback_id"]
    )
    assert (feedback.source_id, feedback.custom_eval_config_id) == (
        span.id,
        config.id,
    )
    assert len(no_embeddings.stored) == 1


def test_r5_recalculate_on_span_and_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    user,
    project,
    eval_template,
    stub_run_eval,
    stub_cost_log,
):
    lagging_replica.freeze()
    trace, span, config = _after_freeze_span_and_config(project, eval_template)
    with force_primary():
        fresh = _result(trace, span, config)
    feedback = Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(span.id),
        value="Passed",
        eval_template=eval_template,
        user=user,
        custom_eval_config_id=config.id,
        organization=project.organization,
        workspace=project.workspace,
    )

    response = auth_client.post(
        "/tracer/observation-span/submit_feedback_action_type/",
        {
            "observation_span_id": span.id,
            "action_type": "recalculate",
            "custom_eval_config_id": str(config.id),
            "feedback_id": str(feedback.id),
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert EvalLogger.all_objects.using("default").get(pk=fresh.pk).deleted is True
    live = EvalLogger.objects.using("default").filter(
        observation_span=span, custom_eval_config=config
    )
    assert live.count() == 1
    assert len(stub_run_eval.requests) == 1


def test_r5_result_details_for_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    monkeypatch,
):
    """Opening a result's details checks its config on the primary before the
    ClickHouse read: no 'No eval logger found' 400 for a fresh config."""
    from tracer.views import observation_span as span_views

    asked = []
    monkeypatch.setattr(span_views, "V2AnalyticsQueryService", lambda: None)
    monkeypatch.setattr(
        span_views.ObservationSpanView,
        "_get_evaluation_details_clickhouse",
        lambda self, span_id, config_id, project_id, analytics: (
            asked.append((span_id, config_id, project_id))
            or self._gm.success_response({"ok": True})
        ),
    )
    lagging_replica.freeze()
    _trace, span, config = _after_freeze_span_and_config(project, eval_template)

    response = auth_client.get(
        "/tracer/observation-span/get_evaluation_details/",
        {"observation_span_id": span.id, "custom_eval_config_id": str(config.id)},
    )

    assert response.status_code == 200, response.content
    assert asked == [(span.id, str(config.id), str(project.id))]


# -- Follow-up: recalculate on an experiment span, and a failed recompute ------


def _recalculate(client, span, config, feedback):
    return client.post(
        "/tracer/observation-span/submit_feedback_action_type/",
        {
            "observation_span_id": span.id,
            "action_type": "recalculate",
            "custom_eval_config_id": str(config.id),
            "feedback_id": str(feedback.id),
        },
        format="json",
    )


def _feedback(user, span, config):
    return Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(span.id),
        value="Passed",
        eval_template=config.eval_template,
        user=user,
        custom_eval_config_id=config.id,
        organization=span.project.organization,
        workspace=span.project.workspace,
    )


def test_recalculate_on_experiment_span_saves_a_new_result(
    lagging_replica,  # noqa: F811
    settings,
    no_embeddings,
    auth_client,
    user,
    trace,
    project_version,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    """An experiment span (project_version set) recalculates through the
    experiment activity: the old result is retired and a new one saved, even
    when both are newer than the replica."""
    from tracer.models.observation_span import ObservationSpan

    settings.EVAL_SPAN_READ_SOURCE = "postgres"
    lagging_replica.freeze()
    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=trace.project,
        trace=trace,
        project_version=project_version,
        name="experiment span",
        observation_type="llm",
        input={"question": "What is the capital of France?"},
        output={"answer": "Paris"},
    )
    with force_primary():
        old = _result(trace, span, custom_eval_config)
    feedback = _feedback(user, span, custom_eval_config)

    response = _recalculate(auth_client, span, custom_eval_config, feedback)

    assert response.status_code == 200, response.data
    assert EvalLogger.all_objects.using("default").get(pk=old.pk).deleted is True
    live = EvalLogger.objects.using("default").filter(
        observation_span=span, custom_eval_config=custom_eval_config
    )
    assert live.count() == 1
    assert live.get().pk != old.pk
    assert len(stub_run_eval.requests) == 1


def test_failed_recalculate_keeps_the_previous_result(
    lagging_replica,  # noqa: F811
    no_embeddings,
    auth_client,
    user,
    trace,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    """A recompute that saves nothing must not leave the span without its
    result: the retirement rolls back and the request says so."""
    from tracer.views import observation_span as span_views

    old = _result(trace, observation_span, custom_eval_config)
    feedback = _feedback(user, observation_span, custom_eval_config)
    lagging_replica.freeze()

    def failing_recompute(*args, **kwargs):
        return False

    monkeypatch.setattr(
        span_views, "evaluate_observation_span_observe", failing_recompute
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert EvalLogger.objects.using("default").get(pk=old.pk).deleted is False


# -- D16: eval results copied into a dataset ----------------------------------


@pytest.fixture
def ch_export(monkeypatch):
    """Serve span rows for the requested ids instead of reading ClickHouse."""
    import tracer.services.clickhouse.v2 as ch_v2
    import tracer.tasks.dataset as dataset_tasks

    monkeypatch.setattr(ch_v2, "get_reader", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        dataset_tasks,
        "_export_span_rows",
        lambda reader, span_ids, fields, project_id=None, org_id=None: {
            str(span_id): {"id": str(span_id)} for span_id in span_ids
        },
    )
    return dataset_tasks


def test_d16_dataset_copy_keeps_result_created_after_freeze(
    lagging_replica,  # noqa: F811
    ch_export,
    organization,
    workspace,
    user,
    trace,
    observation_span,
    custom_eval_config,
):
    dataset = Dataset.objects.create(
        id=uuid.uuid4(),
        name="from spans " + uuid.uuid4().hex[:6],
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        source=DatasetSourceChoices.OBSERVE.value,
        user=user,
    )
    column = Column.objects.create(
        id=uuid.uuid4(),
        name="eval_metrics",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    lagging_replica.freeze()
    result = _result(
        trace,
        observation_span,
        custom_eval_config,
        eval_type_id="groundedness",
        results_explanation={"reason": "ungrounded"},
    )

    created = ch_export.process_spans_chunk_task._original_func(
        [observation_span.id],
        str(dataset.id),
        [
            {
                "column_id": str(column.id),
                "span_field": "eval_metrics",
                "column_name": "eval_metrics",
            }
        ],
        str(observation_span.project_id),
        str(organization.id),
    )

    assert created == {"rows_created": 1, "cells_created": 1}
    cell = Cell.objects.using("default").get(dataset=dataset, column=column)
    assert json.loads(cell.value) == {
        "groundedness": {
            "score": result.output_bool,
            "explanation": {"reason": "ungrounded"},
            "error": False,
            "error_message": None,
        }
    }

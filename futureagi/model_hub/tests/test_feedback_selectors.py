"""Selector-level tests for feedback edit-context resolution."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from model_hub.models.choices import (
    DataTypeChoices,
    FeedbackSourceChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    Feedback,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentsTable
from model_hub.selectors.feedback import resolve_feedback_edit_contexts


def _make_template(user, workspace):
    return EvalTemplate.objects.create(
        name=f"selector-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
    )


def _make_metric(user, workspace, template, dataset=None):
    return UserEvalMetric.objects.create(
        name=f"metric-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        user=user,
        template=template,
        dataset=dataset or _make_dataset(user, workspace, marker="metric"),
        config={"mapping": {}},
        status=StatusType.COMPLETED.value,
    )


def _make_dataset(user, workspace, marker):
    return Dataset.objects.create(
        name=f"selector-{marker}-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )


def _make_column(dataset):
    return Column.objects.create(
        name=f"col-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        status=StatusType.COMPLETED.value,
    )


@pytest.mark.django_db
def test_empty_input_does_zero_queries(user, workspace):
    with CaptureQueriesContext(connection) as ctx:
        result = resolve_feedback_edit_contexts([])
    assert result == {}
    assert len(ctx.captured_queries) == 0


@pytest.mark.django_db
def test_non_experiment_sources_skip_extra_queries(user, workspace):
    template = _make_template(user, workspace)
    metric = _make_metric(user, workspace, template)
    playground = Feedback.objects.create(
        organization=user.organization,
        workspace=workspace,
        user=user,
        source=FeedbackSourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(uuid.uuid4()),
        eval_template=template,
        user_eval_metric=metric,
        value="passed",
    )
    dataset_fb = Feedback.objects.create(
        organization=user.organization,
        workspace=workspace,
        user=user,
        source=FeedbackSourceChoices.DATASET.value,
        source_id=str(uuid.uuid4()),
        eval_template=template,
        user_eval_metric=metric,
        value="failed",
    )

    with CaptureQueriesContext(connection) as ctx:
        result = resolve_feedback_edit_contexts([playground, dataset_fb])

    assert len(ctx.captured_queries) == 0
    assert result[playground.id]["experiment_id"] == ""
    assert result[dataset_fb.id]["experiment_id"] == ""
    assert result[dataset_fb.id]["user_eval_metric_id"] == str(metric.id)


@pytest.mark.django_db
def test_experiment_sources_use_two_batched_queries(user, workspace):
    template = _make_template(user, workspace)
    metric = _make_metric(user, workspace, template)

    def _experiment_feedback():
        snapshot_dataset = _make_dataset(user, workspace, marker="snap")
        column = _make_column(snapshot_dataset)
        experiment = ExperimentsTable.objects.create(
            name=f"exp-{uuid.uuid4().hex[:8]}",
            user=user,
            dataset=snapshot_dataset,
            snapshot_dataset=snapshot_dataset,
        )
        return experiment, Feedback.objects.create(
            organization=user.organization,
            workspace=workspace,
            user=user,
            source=FeedbackSourceChoices.EXPERIMENT.value,
            source_id=str(column.id),
            eval_template=template,
            user_eval_metric=metric,
            value="passed",
        )

    exp_one, fb_one = _experiment_feedback()
    exp_two, fb_two = _experiment_feedback()
    exp_three, fb_three = _experiment_feedback()

    with CaptureQueriesContext(connection) as ctx:
        result = resolve_feedback_edit_contexts([fb_one, fb_two, fb_three])

    # Constant cost regardless of feedback count: 1 Column lookup + 1 ExperimentsTable lookup.
    assert len(ctx.captured_queries) == 2
    assert result[fb_one.id]["experiment_id"] == str(exp_one.id)
    assert result[fb_two.id]["experiment_id"] == str(exp_two.id)
    assert result[fb_three.id]["experiment_id"] == str(exp_three.id)


@pytest.mark.django_db
def test_custom_eval_config_id_surfaces_for_observe(user, workspace):
    template = _make_template(user, workspace)
    metric = _make_metric(user, workspace, template)
    custom_eval_config_id = uuid.uuid4()
    observe = Feedback.objects.create(
        organization=user.organization,
        workspace=workspace,
        user=user,
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(uuid.uuid4()),
        eval_template=template,
        user_eval_metric=metric,
        value="passed",
        custom_eval_config_id=custom_eval_config_id,
    )

    result = resolve_feedback_edit_contexts([observe])

    assert result[observe.id]["custom_eval_config_id"] == str(custom_eval_config_id)
    assert result[observe.id]["experiment_id"] == ""
    assert result[observe.id]["user_eval_metric_id"] == str(metric.id)

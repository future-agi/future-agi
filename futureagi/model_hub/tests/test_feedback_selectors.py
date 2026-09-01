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
    EvalTemplateVersion,
    Feedback,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentsTable
from model_hub.selectors.feedback import (
    resolve_feedback_edit_contexts,
    resolve_feedback_template_data,
)


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


def _template(user, workspace, **fields) -> EvalTemplate:
    defaults = dict(
        name=f"tpl-{uuid.uuid4().hex[:8]}",
        description="",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
    )
    defaults.update(fields)
    return EvalTemplate.objects.create(**defaults)


def _metric_for(user, workspace, template, config=None) -> UserEvalMetric:
    return UserEvalMetric.objects.create(
        name=f"metric-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        user=user,
        template=template,
        dataset=_make_dataset(user, workspace, marker="tpldata"),
        config=config if config is not None else {"mapping": {}},
        status=StatusType.COMPLETED.value,
    )


@pytest.mark.django_db
class TestResolveFeedbackTemplateData:
    def test_pass_fail_returns_hardcoded_choices(self, user, workspace):
        tpl = _template(user, workspace, config={"output": "Pass/Fail"})
        metric = _metric_for(user, workspace, tpl)

        data = resolve_feedback_template_data(metric, tpl)

        assert data["output_type"] == "Pass/Fail"
        assert data["choices"] == ["Passed", "Failed"]
        assert data["eval_name"] == tpl.name
        assert data["user_eval_name"] == metric.name

    def test_choice_scores_surfaces_when_populated(self, user, workspace):
        tpl = _template(
            user,
            workspace,
            config={"output": "score"},
            choice_scores={"Yes": 1.0, "No": 0.0},
        )
        metric = _metric_for(user, workspace, tpl)

        data = resolve_feedback_template_data(metric, tpl)

        assert data["choice_scores"] == {"Yes": 1.0, "No": 0.0}

    def test_choice_scores_is_null_when_absent(self, user, workspace):
        tpl = _template(user, workspace, config={"output": "score"})
        metric = _metric_for(user, workspace, tpl)

        data = resolve_feedback_template_data(metric, tpl)

        assert data["choice_scores"] is None

    def test_metric_choices_override_wins_over_template(self, user, workspace):
        tpl = _template(
            user,
            workspace,
            config={"output": "choices"},
            choices=["Alpha", "Beta"],
        )
        metric = _metric_for(
            user,
            workspace,
            tpl,
            config={"config": {"choices": ["X", "Y", "Z"], "multi_choice": True}},
        )

        data = resolve_feedback_template_data(metric, tpl)

        assert data["choices"] == ["X", "Y", "Z"]
        assert data["multi_choice"] is False

    def test_multi_choice_sourced_from_template_when_metric_asserts_true(
        self, user, workspace
    ):
        """Metric-side ``multi_choice`` override is ignored; the
        template's canonical multi_choice field is the single source of
        truth."""
        tpl = _template(
            user,
            workspace,
            config={"output": "choices"},
            choices=["neutral", "joy", "sadness"],
            multi_choice=False,
        )
        metric = _metric_for(
            user, workspace, tpl, config={"config": {"multi_choice": True}}
        )

        data = resolve_feedback_template_data(metric, tpl)

        assert data["multi_choice"] is False
        assert data["choices"] == ["neutral", "joy", "sadness"]

    def test_multi_choice_from_template_ignores_metric_false(
        self, user, workspace
    ):
        """Template says multi_choice=True; metric override is ignored."""
        tpl = _template(
            user,
            workspace,
            config={"output": "choices"},
            choices=["A", "B"],
            multi_choice=True,
        )
        metric = _metric_for(
            user, workspace, tpl, config={"config": {"multi_choice": False}}
        )

        data = resolve_feedback_template_data(metric, tpl)

        assert data["multi_choice"] is True

    def test_multi_choice_from_pinned_version_beats_template_field(
        self, user, workspace
    ):
        """When the metric resolves to a template version whose snapshot
        captured a different multi_choice, honor the version. Templates
        with multiple versions can toggle multi_choice per version; the
        template's direct field only reflects the latest edit."""
        tpl = _template(
            user,
            workspace,
            config={"output": "choices"},
            choices=["A", "B"],
            multi_choice=False,
        )
        # v1 captured multi_choice=True in its snapshot; not the default.
        version = EvalTemplateVersion.objects.create(
            eval_template=tpl,
            version_number=1,
            config_snapshot={
                "multi_choice": True,
                "choices": ["A", "B"],
                "output": "choices",
            },
            is_default=False,
            organization=user.organization,
            workspace=workspace,
        )
        metric = _metric_for(user, workspace, tpl)
        metric.pinned_version = version
        metric.save(update_fields=["pinned_version"])

        data = resolve_feedback_template_data(metric, tpl)

        assert data["multi_choice"] is True

    def test_choices_type_with_no_choices_anywhere_returns_empty(
        self, user, workspace
    ):
        tpl = _template(user, workspace, config={"output": "choices"})
        metric = _metric_for(user, workspace, tpl)

        data = resolve_feedback_template_data(metric, tpl)

        assert data["output_type"] == "choices"
        assert data["choices"] == []
        assert data["multi_choice"] is False

"""Supplemental integration tests for judge vs human agreement edge cases.

These tests cover important scenarios identified during code review:
- Mixed compatible/incompatible labels
- Skipped and deleted eval rows filtering
- LLM-generated scores exclusion from human majority
- Malformed Score.value handling
"""

import pytest

from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    QueueItemSourceType,
    ScoreSource,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.score import Score
from model_hub.utils.annotation_queue_helpers import (
    _calculate_judge_human_agreement,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_project(organization, workspace):
    return Project.objects.create(
        name="test-project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _make_trace(project):
    return Trace.objects.create(project=project)


def _make_span(project, trace):
    return ObservationSpan.objects.create(
        project=project,
        trace=trace,
        observation_type="generation",
    )


def _make_eval_template(organization, workspace, output_type_normalized="pass_fail"):
    return EvalTemplate.objects.create(
        name="Test Template",
        organization=organization,
        workspace=workspace,
        output_type_normalized=output_type_normalized,
    )


def _make_custom_eval_config(project, template):
    return CustomEvalConfig.objects.create(
        name="Test Config",
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _make_queue(organization, workspace, project, custom_eval_config=None):
    return AnnotationQueue.objects.create(
        name="Test Queue",
        organization=organization,
        workspace=workspace,
        project=project,
        custom_eval_config=custom_eval_config,
        status=AnnotationQueueStatusChoices.COMPLETED.value,
    )


def _make_queue_item(queue, span, organization):
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span=span,
        organization=organization,
    )


def _make_label(organization, workspace, label_type="categorical"):
    return AnnotationsLabels.objects.create(
        name=f"Test Label {label_type}",
        organization=organization,
        workspace=workspace,
        type=label_type,
    )


def _make_eval_row(observation_span, trace, custom_eval_config, **kwargs):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=observation_span,
        trace=trace,
        custom_eval_config=custom_eval_config,
        status=EvalEntryStatus.COMPLETED,
        error=False,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Supplemental integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
class TestJudgeHumanAgreementSupplemental:
    """Additional edge-case tests for judge vs human agreement."""

    def test_mixed_compatible_and_incompatible_labels(self, organization, workspace):
        """Queue with both compatible and incompatible labels.

        Overall agreement should only count the compatible label's
        comparison, not the incompatible one. The incompatible label
        should report comparable=False.
        """
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)

        # pass_fail evaluator
        template = _make_eval_template(organization, workspace, "pass_fail")
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)

        # Compatible label (categorical)
        label_compatible = _make_label(organization, workspace, "categorical")
        queue.queue_labels.create(label=label_compatible)

        # Incompatible label (star rating, 1-5)
        label_incompatible = _make_label(organization, workspace, "star")
        queue.queue_labels.create(label=label_incompatible)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human says "pass" on compatible label
        Score.objects.create(
            queue_item=item,
            label=label_compatible,
            organization=organization,
            value={"selected": ["pass"]},
            score_source=ScoreSource.HUMAN.value,
        )

        # Human says "4" on incompatible label (star rating)
        Score.objects.create(
            queue_item=item,
            label=label_incompatible,
            organization=organization,
            value={"rating": 4.0},
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # Overall agreement: 1/1 = 100% (only compatible label counted)
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1

        # Compatible label: 100% agreement
        compatible_label_result = result["labels"][str(label_compatible.id)]
        assert compatible_label_result["comparable"] is True
        assert compatible_label_result["judge_human_agreement"] == 1.0
        assert compatible_label_result["total_comparisons"] == 1

        # Incompatible label: marked as not comparable
        incompatible_label_result = result["labels"][str(label_incompatible.id)]
        assert incompatible_label_result["comparable"] is False
        assert incompatible_label_result["judge_human_agreement"] is None
        assert incompatible_label_result["total_comparisons"] == 0

    def test_skipped_eval_rows_excluded(self, organization, workspace):
        """Eval rows with skipped_reason should not be included in agreement."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Skipped eval row (has skipped_reason)
        EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=span,
            trace=trace,
            custom_eval_config=cfg,
            status=EvalEntryStatus.COMPLETED,
            error=False,
            output_bool=False,  # Would disagree if counted
            skipped_reason="Rate limit exceeded",
        )

        # Human says "pass"
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # No comparison because the eval was skipped
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

    def test_deleted_eval_rows_excluded(self, organization, workspace):
        """Soft-deleted eval rows should not be included."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Deleted eval row
        eval_row = _make_eval_row(span, trace, cfg, output_bool=False)
        eval_row.deleted = True
        eval_row.save()

        # Human says "pass"
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # No comparison because the eval was deleted
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

    def test_llm_scores_excluded_from_human_majority(self, organization, workspace):
        """Only score_source=HUMAN should count in human majority.

        LLM-generated scores should be excluded from the majority calculation.
        """
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Two human annotators say "pass"
        for _ in range(2):
            Score.objects.create(
                queue_item=item,
                label=label,
                organization=organization,
                value={"selected": ["pass"]},
                score_source=ScoreSource.HUMAN.value,
            )

        # One LLM score says "fail" (should be excluded)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
            score_source=ScoreSource.LLM.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # Human majority is "pass" (2 votes), LLM "fail" not counted
        # Judge "pass" agrees with human majority
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1

    def test_score_value_empty_dict_not_comparable(self, organization, workspace):
        """Score.value={} should degrade gracefully to not comparable."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human score with empty dict value
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={},  # Empty dict
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # Empty dict cannot be unwrapped, so no comparison
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

    def test_score_value_none_not_comparable(self, organization, workspace):
        """Score.value=None should degrade gracefully."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human score with None value
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value=None,
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # None value cannot be compared
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

    def test_score_value_empty_selected_list_not_comparable(
        self, organization, workspace
    ):
        """Score.value={"selected": []} should not be comparable."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human score with empty selected list (no choice made)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": []},
            score_source=ScoreSource.HUMAN.value,
        )

        result = _calculate_judge_human_agreement(queue)

        # Empty selection means no choice was made
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

"""Integration tests for ``_calculate_judge_human_agreement``.

These tests hit a real PostgreSQL database — no mocks, no stubs.
They verify that the ORM queries (Subquery, joins, FK traversal) produce
correct results end-to-end.
"""

import pytest
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.choices import (
    AnnotationTypeChoices,
    QueueItemSourceType,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.score import Score
from model_hub.utils.annotation_queue_helpers import (
    _calculate_judge_human_agreement,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

# ---------------------------------------------------------------------------
# helpers — create everything directly, no fixtures needed
# ---------------------------------------------------------------------------


def _make_project(organization, workspace, **kwargs):
    return Project.objects.create(
        name="integration-project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        **kwargs,
    )


def _make_trace(project, **kwargs):
    return Trace.objects.create(
        project=project,
        name="integration-trace",
        input={},
        output={},
        **kwargs,
    )


def _make_span(project, trace, **kwargs):
    import uuid

    span_id = f"span_{uuid.uuid4().hex[:16]}"
    return ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace,
        name="integration-span",
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
        **kwargs,
    )


def _make_eval_template(organization, workspace, **kwargs):
    defaults = {
        "name": "integration-eval-template",
        "output_type_normalized": "pass_fail",
        "config": {"type": "pass_fail"},
    }
    defaults.update(kwargs)
    return EvalTemplate.objects.create(
        organization=organization,
        workspace=workspace,
        **defaults,
    )


def _make_custom_eval_config(project, eval_template, **kwargs):
    defaults = {
        "name": "integration-eval-config",
        "config": {},
        "mapping": {},
        "filters": {},
    }
    defaults.update(kwargs)
    return CustomEvalConfig.objects.create(
        project=project,
        eval_template=eval_template,
        **defaults,
    )


def _make_queue(organization, workspace, project, custom_eval_config=None, **kwargs):
    return AnnotationQueue.objects.create(
        name="integration-queue",
        organization=organization,
        workspace=workspace,
        project=project,
        custom_eval_config=custom_eval_config,
        **kwargs,
    )


def _make_queue_item(queue, observation_span, organization, **kwargs):
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span=observation_span,
        organization=organization,
        **kwargs,
    )


def _make_label(organization, workspace, **kwargs):
    defaults = {
        "name": "integration-label",
        "type": AnnotationTypeChoices.CATEGORICAL.value,
        "settings": {},
    }
    defaults.update(kwargs)
    return AnnotationsLabels.objects.create(
        organization=organization,
        workspace=workspace,
        **defaults,
    )


def _make_eval_row(observation_span, trace, custom_eval_config, **kwargs):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=observation_span,
        trace=trace,
        custom_eval_config=custom_eval_config,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
class TestJudgeHumanAgreementIntegration:
    """End-to-end tests with a real database."""

    def test_queue_without_evaluator_returns_none(self, organization, workspace):
        """A queue with no linked CustomEvalConfig → None."""
        project = _make_project(organization, workspace)
        queue = _make_queue(organization, workspace, project)
        assert _calculate_judge_human_agreement(queue) is None

    def test_queue_with_null_output_type_returns_none(self, organization, workspace):
        """output_type_normalized is None → early return None."""
        project = _make_project(organization, workspace)
        template = _make_eval_template(
            organization, workspace, output_type_normalized=None
        )
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        assert _calculate_judge_human_agreement(queue) is None

    def test_no_span_sourced_items_returns_none(self, organization, workspace):
        """Queue exists & evaluator linked, but no observation_span items → None."""
        project = _make_project(organization, workspace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        assert _calculate_judge_human_agreement(queue) is None

    def test_basic_agreement_single_item_single_label(self, organization, workspace):
        """One item, one label, judge agrees → 1.0 agreement."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass"
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human says "pass"
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result is not None
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1
        assert result["evaluator_name"] == "integration-eval-config"
        assert str(label.id) in result["labels"]
        assert result["labels"][str(label.id)]["judge_human_agreement"] == 1.0

    def test_judge_disagrees_with_human(self, organization, workspace):
        """Judge says pass, human says fail → 0.0 agreement."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_bool=True)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["overall_agreement"] == 0.0
        assert result["labels"][str(label.id)]["judge_human_agreement"] == 0.0

    def test_human_majority_wins(self, organization, workspace):
        """Two annotators agree with judge, one disagrees → 1.0 agreement."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge: pass
        _make_eval_row(span, trace, cfg, output_bool=True)

        # 2 annotators say "pass", 1 says "fail" — majority is "pass"
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["overall_agreement"] == 1.0  # majority agrees

    def test_human_tie_skips_comparison(self, organization, workspace):
        """Two annotators tie (one pass, one fail) → item skipped,
        overall is None."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_bool=True)

        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
        )

        result = _calculate_judge_human_agreement(queue)
        # Tie → no comparison possible → overall None, label agreement None
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0
        assert result["labels"][str(label.id)]["judge_human_agreement"] is None
        assert result["labels"][str(label.id)]["total_comparisons"] == 0

    def test_skips_error_eval_rows(self, organization, workspace):
        """EvalLogger rows with error=True are excluded from agreement."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Error row — should be ignored.
        _make_eval_row(span, trace, cfg, output_bool=False, error=True)
        # Clean row — should be used instead.
        _make_eval_row(span, trace, cfg, output_bool=True, error=False)

        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        # If the error row were used, agreement would be 0.0 (fail vs pass).
        assert result["overall_agreement"] == 1.0

    def test_latest_eval_row_used_per_span(self, organization, workspace):
        """When a span has multiple EvalLogger rows for the same config,
        only the latest non-error one contributes."""
        from datetime import timedelta

        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Older row — says "fail"
        older = _make_eval_row(span, trace, cfg, output_bool=False)
        # Newer row — says "pass"
        newer = _make_eval_row(span, trace, cfg, output_bool=True)

        # Set created_at so we can control ordering.
        EvalLogger.objects.filter(id=older.id).update(
            created_at=timezone.now() - timedelta(hours=1),
        )
        EvalLogger.objects.filter(id=newer.id).update(
            created_at=timezone.now(),
        )

        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        # Latest row says "pass" → agrees with human "pass" → 1.0
        assert result["overall_agreement"] == 1.0

    def test_percentage_output_type(self, organization, workspace):
        """Judge uses percentage output type — agreement matches."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(
            organization,
            workspace,
            output_type_normalized="percentage",
        )
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        # numeric label is comparable with a percentage judge output.
        label = _make_label(
            organization, workspace, type=AnnotationTypeChoices.NUMERIC.value
        )

        _make_eval_row(span, trace, cfg, output_float=0.876)

        # Human stores a numeric dict {"value": 0.876}; it must unwrap and
        # round to "0.88" to match the judge.
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"value": 0.876},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["overall_agreement"] == 1.0

    def test_deterministic_output_type(self, organization, workspace):
        """Judge uses deterministic output type — string comparison."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(
            organization,
            workspace,
            output_type_normalized="deterministic",
        )
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_str="toxic")

        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["toxic"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["overall_agreement"] == 1.0

    def test_evaluator_name_fallback_to_template_name(self, organization, workspace):
        """When custom_eval_config.name is None, fall back to
        eval_template.name."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(
            organization,
            workspace,
            name="Template-Name-Fallback",
        )
        cfg = _make_custom_eval_config(
            project,
            template,
            name=None,  # config name is None
        )
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_bool=True)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["evaluator_name"] == "Template-Name-Fallback"

    def test_config_name_none_falls_back_to_template_name(
        self, organization, workspace
    ):
        """When custom_eval_config.name is None but eval_template.name is
        truthy, the evaluator name resolves to template.name.

        The ``or str(config_id)`` fallback is unreachable via integration
        tests (EvalTemplate.name can't be blank).  That path is covered by
        ``test_uses_config_id_as_final_evaluator_name_fallback`` in the
        unit-test suite."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(
            organization,
            workspace,
            name="Template-Name",
        )
        cfg = _make_custom_eval_config(
            project,
            template,
            name=None,
        )
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_bool=True)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        # config.name is None → falls back to template.name
        assert result["evaluator_name"] == "Template-Name"

    def test_select_related_avoids_extra_queries(
        self,
        organization,
        workspace,
        django_assert_max_num_queries,
    ):
        """The viewset uses select_related('custom_eval_config__eval_template')
        so the function call should stay within a tight query budget."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        # Re-fetch with select_related to simulate the viewset queryset.
        queue = AnnotationQueue.objects.select_related(
            "custom_eval_config__eval_template"
        ).get(id=queue.id)

        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)
        _make_eval_row(span, trace, cfg, output_bool=True)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        # The function does 3 queries: EvalLogger Subquery, EvalLogger values,
        # and Score values. The eager-loaded FK chain should add 0 extras.
        with django_assert_max_num_queries(5):
            result = _calculate_judge_human_agreement(queue)

        assert result["overall_agreement"] == 1.0

    def test_multiple_items_same_span(self, organization, workspace):
        """Two queue items that reference the same span each compare
        independently against the single judge output for that span."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)

        # Two items share the same span.
        item_a = _make_queue_item(queue, span, organization)
        item_b = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge says "pass" (once, for the single span).
        _make_eval_row(span, trace, cfg, output_bool=True)

        # Human annotator agrees on item_a ("pass"), disagrees on item_b ("fail").
        Score.objects.create(
            queue_item=item_a,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )
        Score.objects.create(
            queue_item=item_b,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
        )

        result = _calculate_judge_human_agreement(queue)
        # 1 agree + 1 disagree = 0.5
        assert result["overall_agreement"] == 0.5
        assert result["total_comparisons"] == 2

    def test_all_eval_rows_normalize_to_none(self, organization, workspace):
        """Judge evals exist but every output value is None (e.g. pass_fail
        with output_bool=None).  No overlap means overall is None."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        # Judge output_bool is None — normalize_eval_output returns None.
        _make_eval_row(span, trace, cfg, output_bool=None)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

    def test_three_annotators_majority_wins(self, organization, workspace):
        """Three annotators: two "pass", one "fail" → majority is "pass",
        which agrees with the judge."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        label = _make_label(organization, workspace)

        _make_eval_row(span, trace, cfg, output_bool=True)

        # Three annotators.
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
            annotator_id="a1",
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
            annotator_id="a2",
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["fail"]},
            annotator_id="a3",
        )

        result = _calculate_judge_human_agreement(queue)
        # Judge "pass", human majority "pass" → agree.
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1

    def test_trace_sourced_item_is_compared(self, organization, workspace):
        """A queue item sourced from a TRACE (not a span) is still compared
        against the judge eval anchored to that trace."""
        from tracer.models.observation_span import EvalTargetType

        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)

        # Build a trace-sourced item directly (bypass the span helper).
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=trace,
            organization=organization,
        )
        label = _make_label(organization, workspace)

        # EvalLogger anchored to the trace (target_type='trace').
        EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=None,
            trace=trace,
            custom_eval_config=cfg,
            output_bool=True,
            status="completed",
        )
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"selected": ["pass"]},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result is not None
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1

    def test_incompatible_label_type_is_not_comparable(self, organization, workspace):
        """A pass_fail evaluator against a star (1-5) label is not comparable,
        so the label reports comparable=False and no misleading 0%."""
        project = _make_project(organization, workspace)
        trace = _make_trace(project)
        span = _make_span(project, trace)
        template = _make_eval_template(organization, workspace)
        cfg = _make_custom_eval_config(project, template)
        queue = _make_queue(organization, workspace, project, custom_eval_config=cfg)
        item = _make_queue_item(queue, span, organization)
        # star label — incompatible with pass_fail judge output
        label = _make_label(
            organization, workspace, type=AnnotationTypeChoices.STAR.value
        )

        _make_eval_row(span, trace, cfg, output_bool=True)
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            value={"rating": 5},
        )

        result = _calculate_judge_human_agreement(queue)
        assert result is not None
        lbl = result["labels"][str(label.id)]
        assert lbl["comparable"] is False
        assert lbl["judge_human_agreement"] is None
        assert lbl["total_comparisons"] == 0
        # overall only counts comparable labels
        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0

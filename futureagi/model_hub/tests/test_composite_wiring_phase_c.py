"""
Tests for Phase 7 wiring — Phase C (experiment + trace integration).

Covers:
- `CompositeEvaluationRunner.build_column_config` classmethod shape
- Experiment runner branching: composite metrics bypass `EvaluationRunner`
  for column creation and use the composite-aware path instead
- Trace span composite short-circuit in `_execute_evaluation`
"""

from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import (
    DatasetSourceChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
from model_hub.tasks.composite_runner import CompositeEvaluationRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def child_leaf(db, organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="leaf-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        output_type_normalized="percentage",
    )


@pytest.fixture
def aggregated_composite(db, organization, workspace, child_leaf):
    parent = EvalTemplate.no_workspace_objects.create(
        name="agg-composite",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="weighted_avg",
        config={},
    )
    CompositeEvalChild.objects.create(
        parent=parent, child=child_leaf, order=0, weight=1.0
    )
    return parent


@pytest.fixture
def independent_composite(db, organization, workspace, child_leaf):
    parent = EvalTemplate.no_workspace_objects.create(
        name="indep-composite",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=False,
        aggregation_function="weighted_avg",
        config={},
    )
    CompositeEvalChild.objects.create(
        parent=parent, child=child_leaf, order=0, weight=1.0
    )
    return parent


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="phase-c-dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


def _make_metric(template, dataset, organization, workspace):
    return UserEvalMetric.objects.create(
        name=f"phase-c-{template.name}-metric",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        config={},
    )


# ---------------------------------------------------------------------------
# build_column_config
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildColumnConfig:
    def test_aggregated_metric_uses_float_column(
        self, aggregated_composite, dataset, organization, workspace
    ):
        metric = _make_metric(aggregated_composite, dataset, organization, workspace)
        cfg = CompositeEvaluationRunner.build_column_config(
            user_eval_metric=metric,
            dataset=dataset,
        )
        assert cfg["data_type"] == "float"
        assert cfg["source"] == SourceChoices.EVALUATION.value
        assert cfg["source_id"] == str(metric.id)
        assert cfg["dataset"] is dataset
        assert cfg["name"] == metric.name

    def test_independent_metric_uses_text_column(
        self, independent_composite, dataset, organization, workspace
    ):
        metric = _make_metric(independent_composite, dataset, organization, workspace)
        cfg = CompositeEvaluationRunner.build_column_config(
            user_eval_metric=metric,
            dataset=dataset,
        )
        assert cfg["data_type"] == "text"

    def test_experiment_mode_encodes_source_id(
        self, aggregated_composite, dataset, organization, workspace
    ):
        metric = _make_metric(aggregated_composite, dataset, organization, workspace)

        experiment_dataset = MagicMock()
        experiment_dataset.id = "edt-123"
        input_column = MagicMock()
        input_column.id = "col-456"
        input_column.name = "model-output"

        cfg = CompositeEvaluationRunner.build_column_config(
            user_eval_metric=metric,
            dataset=dataset,
            experiment_dataset=experiment_dataset,
            column=input_column,
        )
        assert cfg["source"] == SourceChoices.EXPERIMENT_EVALUATION.value
        assert cfg["source_id"] == f"edt-123-col-456-sourceid-{metric.id}"
        assert cfg["name"] == f"{metric.name}-model-output"
        assert cfg["data_type"] == "float"


# ---------------------------------------------------------------------------
# Experiment runner branching
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExperimentRunEvaluationsBranching:
    def test_composite_metric_dispatches_composite_runner(
        self, aggregated_composite, dataset, organization, workspace
    ):
        """`run_evaluations` should instantiate `CompositeEvaluationRunner`
        (not `EvaluationRunner`) when the bound template is composite."""
        from model_hub.views.experiment_runner import ExperimentRunner

        metric = _make_metric(aggregated_composite, dataset, organization, workspace)

        experiment_dataset = MagicMock()
        experiment_dataset.id = "edt-xyz"
        experiment_dataset.columns = MagicMock()

        input_column = MagicMock()
        input_column.id = "col-abc"
        input_column.name = "model-output"

        fake_experiment = MagicMock()
        fake_experiment.id = "exp-1"

        runner_instance = ExperimentRunner.__new__(ExperimentRunner)
        runner_instance.experiment = fake_experiment
        runner_instance.dataset = dataset
        runner_instance.cancel_event = None

        with (
            patch(
                "model_hub.tasks.composite_runner.CompositeEvaluationRunner.run_prompt"
            ) as mock_run,
            patch(
                "model_hub.views.experiment_runner.EvaluationRunner"
            ) as mock_eval_runner,
        ):
            runner_instance.run_evaluations(
                column=input_column,
                eval_template_id=metric.id,
                experiment_dataset=experiment_dataset,
            )

        mock_run.assert_called_once()
        # The single-eval EvaluationRunner must NOT be used for composites.
        # CompositeEvaluationRunner.run_prompt owns column creation now —
        # the previous explicit `_get_or_create_eval_column` pre-creation
        # in `run_evaluations` was removed because it double-created the
        # result column.
        mock_eval_runner.assert_not_called()

    def test_single_metric_still_uses_eval_runner(
        self, dataset, organization, workspace
    ):
        """Regression guard: single (non-composite) templates keep the
        existing `EvaluationRunner` path."""
        from model_hub.views.experiment_runner import ExperimentRunner

        single_template = EvalTemplate.no_workspace_objects.create(
            name="phase-c-single",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            template_type="single",
            config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        )
        metric = _make_metric(single_template, dataset, organization, workspace)

        experiment_dataset = MagicMock()
        experiment_dataset.id = "edt-1"
        input_column = MagicMock()
        input_column.id = "col-1"
        input_column.name = "out"

        runner_instance = ExperimentRunner.__new__(ExperimentRunner)
        runner_instance.experiment = MagicMock(id="exp-1")
        runner_instance.dataset = dataset
        runner_instance.cancel_event = None

        with (
            patch(
                "model_hub.views.experiment_runner.EvaluationRunner"
            ) as mock_eval_runner,
            patch(
                "model_hub.tasks.composite_runner.CompositeEvaluationRunner.run_prompt"
            ) as mock_composite_run,
        ):
            fake_runner = MagicMock()
            mock_eval_runner.return_value = fake_runner
            runner_instance.run_evaluations(
                column=input_column,
                eval_template_id=metric.id,
                experiment_dataset=experiment_dataset,
            )

        mock_eval_runner.assert_called_once()
        fake_runner.run_prompt.assert_called_once()
        mock_composite_run.assert_not_called()


# ---------------------------------------------------------------------------
# Trace span composite short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTraceSpanCompositeBranch:
    def test_composite_template_short_circuits_into_helper(self, aggregated_composite):
        """`_execute_evaluation` should detect composite templates and
        delegate to `_execute_composite_on_span` without touching
        `run_eval`."""
        from tracer.utils import eval as tracer_eval

        with (
            patch.object(
                tracer_eval, "_execute_composite_on_span", return_value={"value": 0.5}
            ) as mock_composite,
            patch.object(tracer_eval.CustomEvalConfig.objects, "get") as mock_cfg_get,
            patch.object(
                tracer_eval.ObservationSpan.objects, "select_related"
            ) as mock_span_select,
        ):
            fake_span = MagicMock()
            fake_span.project.organization.id = "org-1"
            fake_span.project.workspace = None
            mock_span_select.return_value.get.return_value = fake_span

            fake_config = MagicMock()
            fake_config.eval_template = aggregated_composite
            fake_config.eval_template.template_type = "composite"
            mock_cfg_get.return_value = fake_config

            result = tracer_eval._execute_evaluation(
                observation_span_id="span-1",
                custom_eval_config_id="cfg-1",
                eval_task_id="task-1",
                type="observe",
                run_params={"input": "hello"},
            )

        mock_composite.assert_called_once()
        assert result == {"value": 0.5}


@pytest.mark.django_db
class TestCompositeOnSpanContextForwarding:
    """`_execute_composite_on_span` forwards canonical span/trace/session contexts (TH-5261)."""

    def test_span_composite_always_forwards_canonical_span_context(
        self, aggregated_composite
    ):
        from tracer.utils import eval as tracer_eval

        recorded: dict = {}

        def _capture(**kwargs):
            recorded.update(kwargs)

            class _Outcome:
                aggregate_score = 0.7
                aggregate_pass = True
                summary = "ok"
                child_results = []
                error_localizer_results = None

            return _Outcome()

        fake_span = MagicMock()
        fake_span.id = "span-abc"
        fake_span.project.organization = MagicMock()
        fake_span.project.workspace = MagicMock()
        fake_span.trace = None  # trace/session contexts absent cleanly
        # build_span_context uses getattr with defaults — sufficient for capture.
        fake_span.name = "user.message"
        fake_span.observation_type = "span"
        fake_span.status = "OK"
        fake_span.status_message = None
        fake_span.model = "turing_large"
        fake_span.latency_ms = 12
        fake_span.total_tokens = 100
        fake_span.cost = 0

        fake_cfg = MagicMock()
        fake_cfg.eval_template = aggregated_composite
        fake_cfg.eval_template.template_type = "composite"
        fake_cfg.config = {}
        fake_cfg.model = "turing_large"

        with (
            patch.object(
                tracer_eval.ObservationSpan.objects, "select_related"
            ) as mock_span_select,
            patch.object(tracer_eval.CustomEvalConfig.objects, "get") as mock_cfg_get,
            patch(
                "model_hub.utils.composite_execution.execute_composite_children_sync",
                side_effect=_capture,
            ),
        ):
            mock_span_select.return_value.get.return_value = fake_span
            mock_cfg_get.return_value = fake_cfg
            tracer_eval._execute_composite_on_span(
                observation_span_id="span-abc",
                custom_eval_config_id="cfg-1",
                eval_task_id="task-1",
                run_params={"input": "hello"},
            )

        assert recorded.get("source") == "tracer_composite"
        ctx = recorded.get("span_context")
        assert ctx is not None, "span_context must always be forwarded"
        assert ctx.get("id") == "span-abc"
        assert ctx.get("name") == "user.message"
        assert "trace_context" not in recorded
        assert "session_context" not in recorded

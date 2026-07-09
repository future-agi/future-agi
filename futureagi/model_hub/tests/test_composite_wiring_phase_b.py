"""
Tests for Phase 7 wiring — Phase B (runner + async task dispatch).

Covers:
- `execute_composite_children_sync` helper aggregation + weight overrides
- `CompositeEvaluationRunner` row → cell + evaluation-row pipeline
- `process_eval_batch_async_task` branching on `template_type`
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.utils.composite_execution import (
    execute_composite_children_sync,
    resolve_child_weights,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def child_a(db, organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="child-a",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )


@pytest.fixture
def child_b(db, organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="child-b",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )


@pytest.fixture
def composite_parent(db, organization, workspace, child_a, child_b):
    parent = EvalTemplate.no_workspace_objects.create(
        name="composite-parent",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="weighted_avg",
        config={},
    )
    CompositeEvalChild.objects.create(parent=parent, child=child_a, order=0, weight=1.0)
    CompositeEvalChild.objects.create(parent=parent, child=child_b, order=1, weight=3.0)
    return parent


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="phase-b-dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


@pytest.fixture
def input_column(db, dataset):
    col = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save(update_fields=["column_order"])
    return col


@pytest.fixture
def row(db, dataset, input_column):
    r = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=r,
        value="hello world",
        status=CellStatus.PASS.value,
    )
    return r


@pytest.fixture
def composite_metric(
    db, organization, workspace, composite_parent, dataset, input_column, user
):
    return UserEvalMetric.objects.create(
        name="phase-b-composite-metric",
        organization=organization,
        workspace=workspace,
        template=composite_parent,
        dataset=dataset,
        user=user,
        config={"mapping": {"input": str(input_column.id)}},
    )


# ---------------------------------------------------------------------------
# resolve_child_weights
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveChildWeights:
    def test_falls_back_to_template_weights(self, composite_parent):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent).order_by("order")
        )
        resolved = resolve_child_weights(links, None)
        assert resolved[str(links[0].child_id)] == 1.0
        assert resolved[str(links[1].child_id)] == 3.0

    def test_applies_binding_overrides(self, composite_parent):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent).order_by("order")
        )
        overrides = {str(links[0].child_id): 5.0}
        resolved = resolve_child_weights(links, overrides)
        assert resolved[str(links[0].child_id)] == 5.0
        # Other child still falls back to template value.
        assert resolved[str(links[1].child_id)] == 3.0

    def test_empty_overrides_match_none(self, composite_parent):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent).order_by("order")
        )
        assert resolve_child_weights(links, {}) == resolve_child_weights(links, None)


# ---------------------------------------------------------------------------
# execute_composite_children_sync
# ---------------------------------------------------------------------------


def _fake_run_eval_func(_config, _mapping, template, *_args, **_kwargs):
    """Return a canned result keyed by child template name.

    Used to sidestep the eval engine, LLM calls, and usage billing in
    Phase B unit tests. Return shape matches `run_eval_func`.
    """
    canned = {
        "child-a": {"output": 0.2, "reason": "child-a reason", "output_type": "score"},
        "child-b": {"output": 0.8, "reason": "child-b reason", "output_type": "score"},
    }
    payload = canned.get(template.name, {"output": 0.0, "reason": ""})
    return {**payload, "model": "turing_large", "metadata": {}, "log_id": None}


@pytest.mark.django_db
class TestExecuteCompositeChildrenSync:
    def test_weighted_average_aggregation(self, composite_parent, organization):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent)
            .select_related("child")
            .order_by("order")
        )

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_fake_run_eval_func,
        ):
            outcome = execute_composite_children_sync(
                parent=composite_parent,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
            )

        # weighted_avg of (0.2, w=1), (0.8, w=3) = (0.2 + 2.4) / 4 = 0.65
        assert outcome.aggregate_score == pytest.approx(0.65, abs=1e-6)
        assert outcome.aggregate_pass is True
        assert len(outcome.child_results) == 2
        assert [cr.status for cr in outcome.child_results] == ["completed", "completed"]

    def test_weight_overrides_applied(self, composite_parent, organization):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent)
            .select_related("child")
            .order_by("order")
        )
        overrides = {
            str(links[0].child_id): 3.0,  # was 1.0
            str(links[1].child_id): 1.0,  # was 3.0
        }

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_fake_run_eval_func,
        ):
            outcome = execute_composite_children_sync(
                parent=composite_parent,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
                weight_overrides=overrides,
            )

        # Inverted weights: (0.2, w=3), (0.8, w=1) = (0.6 + 0.8) / 4 = 0.35
        assert outcome.aggregate_score == pytest.approx(0.35, abs=1e-6)

    def test_child_config_params_are_merged_per_child(
        self, composite_parent, organization
    ):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent)
            .select_related("child")
            .order_by("order")
        )
        links[0].config = {"params": {"min_words": 5}}
        links[0].save(update_fields=["config"])

        seen_configs = {}

        def _capture_config(config, mapping, template, *args, **kwargs):
            seen_configs[template.name] = config
            return _fake_run_eval_func(config, mapping, template, *args, **kwargs)

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_capture_config,
        ):
            execute_composite_children_sync(
                parent=composite_parent,
                child_links=links,
                mapping={"input": "hello"},
                config={"params": {"max_words": 20}},
                org=organization,
            )

        assert seen_configs["child-a"]["params"] == {
            "max_words": 20,
            "min_words": 5,
        }
        assert seen_configs["child-b"]["params"] == {"max_words": 20}

    def test_aggregation_disabled_returns_none(self, composite_parent, organization):
        composite_parent.aggregation_enabled = False
        composite_parent.save(update_fields=["aggregation_enabled"])

        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent)
            .select_related("child")
            .order_by("order")
        )

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_fake_run_eval_func,
        ):
            outcome = execute_composite_children_sync(
                parent=composite_parent,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
            )

        assert outcome.aggregate_score is None
        assert outcome.aggregate_pass is None
        assert outcome.summary is None
        assert len(outcome.child_results) == 2

    def test_failing_child_is_captured_not_raised(self, composite_parent, organization):
        links = list(
            CompositeEvalChild.objects.filter(parent=composite_parent)
            .select_related("child")
            .order_by("order")
        )

        def _raising(_cfg, _map, template, *_a, **_k):
            if template.name == "child-a":
                raise RuntimeError("simulated child failure")
            return _fake_run_eval_func(_cfg, _map, template)

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_raising,
        ):
            outcome = execute_composite_children_sync(
                parent=composite_parent,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
            )

        statuses = [cr.status for cr in outcome.child_results]
        assert statuses == ["failed", "completed"]
        # Aggregate should still compute using the one completed child.
        assert outcome.aggregate_score == pytest.approx(0.8, abs=1e-6)

    def test_composite_child_billing_uses_token_pricing_when_cost_is_zero(
        self, composite_parent, organization, workspace, monkeypatch
    ):
        from ee.usage.services.config import BillingConfig
        from model_hub.views.utils.evals import run_eval_func
        from tfc.constants.api_calls import APICallStatusChoices

        child = CompositeEvalChild.objects.filter(parent=composite_parent).first().child
        child.model = "turing_large"
        child.save(update_fields=["model"])
        captured = []

        class FakeEvalInstance:
            cost = {"total_cost": 0}
            token_usage = {
                "prompt_tokens": 1341,
                "completion_tokens": 150,
                "total_tokens": 1491,
            }

            def run(self, **_kwargs):
                return SimpleNamespace(
                    eval_results=[
                        {
                            "data": {"input": "hello"},
                            "failure": None,
                            "reason": "ok",
                            "runtime": 0.01,
                            "model": "turing_large",
                            "metrics": None,
                            "metadata": {},
                        }
                    ]
                )

        log_row = SimpleNamespace(
            log_id="log-1",
            config=json.dumps({}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

        monkeypatch.setattr(
            "tfc.billing.boundary._EeBilling.log_and_deduct",
            lambda self, **_kwargs: log_row,
        )
        monkeypatch.setattr(
            "ee.usage.services.metering.check_usage",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
        )
        monkeypatch.setattr(
            "ee.usage.services.emitter.emit",
            lambda event: captured.append(event),
        )
        monkeypatch.setattr(
            "model_hub.views.utils.evals.EvaluationRunner._create_eval_instance",
            lambda *_args, **_kwargs: FakeEvalInstance(),
        )
        monkeypatch.setattr(
            "model_hub.views.utils.evals.EvaluationRunner.map_fields",
            lambda *_args, **_kwargs: {"input": "hello"},
        )
        monkeypatch.setattr(
            "model_hub.views.utils.evals.EvaluationRunner.format_output",
            lambda *_args, **_kwargs: 0.7,
        )

        output = run_eval_func(
            {"config": {}, "params": {}},
            {"input": "hello"},
            child,
            organization,
            model=None,
            workspace=workspace,
            source="composite_eval",
        )

        assert output["output"] == 0.7
        assert captured
        event = captured[0]
        assert event.properties["raw_cost_usd"] == "0.012001"
        assert event.properties["llm_cost_usd"] == "0.011501"
        assert event.properties["reported_llm_cost_usd"] == "0"
        assert event.properties["llm_cost_source"] == "token_pricing"
        assert event.properties["pricing_source"] == "available_models"
        assert event.amount == pytest.approx(
            BillingConfig.get().calculate_ai_credits(0.012001)
        )


# ---------------------------------------------------------------------------
# CompositeEvaluationRunner
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompositeEvaluationRunner:
    def test_run_prompt_writes_cell_and_evaluation_rows(
        self, composite_metric, row, organization, workspace
    ):
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_fake_run_eval_func,
        ):
            runner = CompositeEvaluationRunner(
                user_eval_metric_id=composite_metric.id,
            )
            runner.run_prompt(row_ids=[row.id])

        # One result column created for the composite metric.
        result_columns = Column.objects.filter(
            source=SourceChoices.EVALUATION.value,
            source_id=str(composite_metric.id),
            deleted=False,
        )
        assert result_columns.count() == 1
        result_column = result_columns.first()
        assert result_column.data_type == "float"

        # One aggregate cell in the result column for this row.
        cells = Cell.objects.filter(column=result_column, row=row, deleted=False)
        assert cells.count() == 1
        aggregate_cell = cells.first()
        assert aggregate_cell.status == CellStatus.PASS.value
        assert float(aggregate_cell.value) == pytest.approx(0.65, abs=1e-6)

        # Parent Evaluation row + 2 child Evaluation rows linked via FK.
        parent_rows = Evaluation.objects.filter(
            eval_template=composite_metric.template, parent_evaluation__isnull=True
        )
        assert parent_rows.count() == 1
        parent_eval = parent_rows.first()
        assert parent_eval.status == StatusChoices.COMPLETED
        assert float(parent_eval.value) == pytest.approx(0.65, abs=1e-6)

        children = Evaluation.objects.filter(parent_evaluation=parent_eval)
        assert children.count() == 2
        assert {c.eval_template.name for c in children} == {"child-a", "child-b"}


# ---------------------------------------------------------------------------
# process_eval_batch_async_task dispatch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAsyncTaskDispatch:
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_composite_template_routes_to_composite_runner(
        self, _mock_close, composite_metric, row
    ):
        # The task is wrapped by `@temporal_activity`, which calls
        # `close_old_connections` before and after invoking the real
        # function. That closes pytest-django's per-test connection.
        # Invoke the original function directly to skip the wrapper, and
        # also patch the in-module `close_old_connections` so the task's
        # own line-424 call is a no-op.
        from model_hub.tasks.user_evaluation import process_eval_batch_async_task

        raw_task = process_eval_batch_async_task._original_func

        with patch(
            "model_hub.tasks.composite_runner.CompositeEvaluationRunner.run_prompt"
        ) as mock_run:
            raw_task(
                None,  # column_id
                [str(row.id)],
                {
                    "user_eval_metric_id": str(composite_metric.id),
                    "source": "dataset",
                    "source_id": str(composite_metric.template.id),
                },
            )

        mock_run.assert_called_once()
        (_, kwargs) = mock_run.call_args
        assert kwargs.get("row_ids") == [str(row.id)]

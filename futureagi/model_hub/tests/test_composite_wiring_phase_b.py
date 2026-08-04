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

    def test_choices_children_score_via_shared_helper(
        self, db, organization, workspace
    ):
        """Deterministic-typed children route the picked label to its choice_scores value."""
        child_a = EvalTemplate.no_workspace_objects.create(
            name="choices-child-a",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "choices", "eval_type_id": "CustomPromptEvaluator"},
            output_type_normalized="deterministic",
            choices=["Sad", "Happy", "Neutral"],
            choice_scores={"Sad": 1.0, "Happy": 0.5, "Neutral": 0.0},
            pass_threshold=0.5,
            multi_choice=True,
        )
        child_b = EvalTemplate.no_workspace_objects.create(
            name="choices-child-b",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "choices", "eval_type_id": "AgentEvaluator"},
            output_type_normalized="deterministic",
            choices=["Happy", "Sad", "Neutral"],
            choice_scores={"Sad": 0.5, "Happy": 0.5, "Neutral": 0.5},
            pass_threshold=0.5,
        )
        parent = EvalTemplate.no_workspace_objects.create(
            name="choices-composite",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            template_type="composite",
            aggregation_enabled=True,
            aggregation_function="avg",
            pass_threshold=0.5,
            config={},
        )
        CompositeEvalChild.objects.create(parent=parent, child=child_a, order=0, weight=1.0)
        CompositeEvalChild.objects.create(parent=parent, child=child_b, order=1, weight=1.0)

        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )

        def _choices_fake_run_eval_func(_cfg, _mapping, template, *_a, **_k):
            # `run_eval_func` returns `output` as the formatted verdict dict from
            # `format_eval_value`: `{"score": float, "choice": str}` for choices.
            canned = {
                "choices-child-a": {"score": 1.0, "choice": "Sad"},
                "choices-child-b": {"score": 0.5, "choice": "Sad"},
            }
            payload = canned.get(template.name)
            return {
                "output": payload,
                "reason": f"{template.name} labelled Sad",
                "output_type": "choices",
                "model": "turing_large",
                "metadata": {},
                "log_id": None,
            }

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_choices_fake_run_eval_func,
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "I am very sad today."},
                config={},
                org=organization,
            )

        by_name = {cr.child_name: cr for cr in outcome.child_results}
        assert by_name["choices-child-a"].score == pytest.approx(1.0)
        assert by_name["choices-child-b"].score == pytest.approx(0.5)
        # Simple average, both weights 1.0 → (1.0 + 0.5) / 2 = 0.75
        assert outcome.aggregate_score == pytest.approx(0.75)
        assert outcome.aggregate_pass is True

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


def _make_percentage_child(
    organization, workspace, name, pass_threshold=0.5
):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "CustomPromptEvaluator"},
        output_type_normalized="percentage",
        pass_threshold=pass_threshold,
    )


def _make_composite(
    organization,
    workspace,
    name,
    aggregation_function,
    pass_threshold=0.5,
    aggregation_enabled=True,
):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=aggregation_enabled,
        aggregation_function=aggregation_function,
        pass_threshold=pass_threshold,
        config={},
    )


def _fake_response(output, output_type="score"):
    """Build a run_eval_func-shaped response dict for the given output value."""
    return {
        "output": output,
        "reason": "canned",
        "output_type": output_type,
        "model": "turing_large",
        "metadata": {},
        "log_id": None,
    }


def _canned_by_name(name_to_output, output_type="score"):
    """Return a side_effect that maps template.name to a canned response dict."""
    def _inner(_cfg, _mapping, template, *_a, **_k):
        return _fake_response(name_to_output.get(template.name, 0.0), output_type)
    return _inner


@pytest.mark.django_db
class TestCompositeAggregationFunctions:
    """One test per aggregation function against a real 3-child composite."""

    def _build(self, organization, workspace, function, weights):
        children = [
            _make_percentage_child(organization, workspace, f"child-{k}")
            for k in ("a", "b", "c")
        ]
        parent = _make_composite(organization, workspace, f"comp-{function}", function)
        for i, (child, w) in enumerate(zip(children, weights)):
            CompositeEvalChild.objects.create(
                parent=parent, child=child, order=i, weight=w
            )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        return parent, links, children

    def test_weighted_avg_honours_link_weights(self, db, organization, workspace):
        parent, links, _ = self._build(
            organization, workspace, "weighted_avg", weights=[1.0, 2.0, 3.0]
        )
        canned = {"child-a": 0.1, "child-b": 0.5, "child-c": 0.9}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        # (0.1*1 + 0.5*2 + 0.9*3) / (1+2+3) = 3.8 / 6 = 0.6333...
        assert outcome.aggregate_score == pytest.approx(0.6333, abs=1e-3)
        assert outcome.aggregate_pass is True

    def test_avg_ignores_weights(self, db, organization, workspace):
        parent, links, _ = self._build(
            organization, workspace, "avg", weights=[1.0, 2.0, 3.0]
        )
        canned = {"child-a": 0.1, "child-b": 0.5, "child-c": 0.9}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        # Simple mean = (0.1 + 0.5 + 0.9) / 3 = 0.5, weights ignored.
        assert outcome.aggregate_score == pytest.approx(0.5)

    def test_min_returns_worst_child_score(self, db, organization, workspace):
        parent, links, _ = self._build(
            organization, workspace, "min", weights=[1.0, 1.0, 1.0]
        )
        canned = {"child-a": 0.9, "child-b": 0.2, "child-c": 0.7}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(0.2)
        # Below default parent threshold of 0.5 → fail.
        assert outcome.aggregate_pass is False

    def test_max_returns_best_child_score(self, db, organization, workspace):
        parent, links, _ = self._build(
            organization, workspace, "max", weights=[1.0, 1.0, 1.0]
        )
        canned = {"child-a": 0.2, "child-b": 0.9, "child-c": 0.5}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(0.9)
        assert outcome.aggregate_pass is True

    def test_pass_rate_uses_per_child_thresholds(self, db, organization, workspace):
        """pass_rate gates each child on its own threshold, returns pass fraction."""
        child_a = _make_percentage_child(
            organization, workspace, "pr-child-a", pass_threshold=0.5
        )
        child_b = _make_percentage_child(
            organization, workspace, "pr-child-b", pass_threshold=0.5
        )
        child_c = _make_percentage_child(
            organization, workspace, "pr-child-c", pass_threshold=0.9
        )
        parent = _make_composite(organization, workspace, "pr-comp", "pass_rate")
        for i, child in enumerate([child_a, child_b, child_c]):
            CompositeEvalChild.objects.create(
                parent=parent, child=child, order=i, weight=1.0
            )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        # child_a passes (0.6 >= 0.5), child_b fails (0.4 < 0.5),
        # child_c fails (0.85 < 0.9).
        canned = {"pr-child-a": 0.6, "pr-child-b": 0.4, "pr-child-c": 0.85}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        # 1 out of 3 passed against their own threshold.
        assert outcome.aggregate_score == pytest.approx(1.0 / 3.0, abs=1e-6)
        assert outcome.aggregate_pass is False  # 0.333 < parent threshold 0.5


@pytest.mark.django_db
class TestCompositeChildThresholdPrecedence:
    """One test per source in link.config -> pinned_version -> live -> 0.5."""

    def _setup_pass_rate_composite(
        self,
        organization,
        workspace,
        child_pass_threshold=0.5,
        link_config=None,
        pinned_version_threshold=None,
    ):
        child = _make_percentage_child(
            organization,
            workspace,
            "prec-child",
            pass_threshold=child_pass_threshold,
        )
        parent = _make_composite(
            organization, workspace, "prec-comp", "pass_rate"
        )
        pinned_version = None
        if pinned_version_threshold is not None:
            from model_hub.models.evals_metric import EvalTemplateVersion

            pinned_version = EvalTemplateVersion.all_objects.create(
                eval_template=child,
                version_number=1,
                config_snapshot=child.config,
                model=child.model or "",
                pass_threshold=pinned_version_threshold,
                output_type_normalized="percentage",
                is_default=False,
            )
        CompositeEvalChild.objects.create(
            parent=parent,
            child=child,
            order=0,
            weight=1.0,
            config=link_config,
            pinned_version=pinned_version,
        )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        return parent, links

    def test_link_config_pass_threshold_wins_over_all_others(
        self, db, organization, workspace
    ):
        # link.config=0.9 should win; pinned_version=0.6 and child=0.3 ignored.
        parent, links = self._setup_pass_rate_composite(
            organization,
            workspace,
            child_pass_threshold=0.3,
            link_config={"pass_threshold": 0.9},
            pinned_version_threshold=0.6,
        )
        # Child scores 0.8: fails 0.9 gate (link.config), passes 0.6 & 0.3.
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"prec-child": 0.8}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        # 0 of 1 passed → pass_rate = 0.0
        assert outcome.aggregate_score == pytest.approx(0.0)

    def test_pinned_version_threshold_used_when_link_config_absent(
        self, db, organization, workspace
    ):
        # pinned_version=0.7 wins over child=0.3.
        parent, links = self._setup_pass_rate_composite(
            organization,
            workspace,
            child_pass_threshold=0.3,
            link_config=None,
            pinned_version_threshold=0.7,
        )
        # Child scores 0.6: fails 0.7 gate (pinned), passes 0.3.
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"prec-child": 0.6}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(0.0)

    def test_live_template_threshold_used_when_link_and_version_absent(
        self, db, organization, workspace
    ):
        parent, links = self._setup_pass_rate_composite(
            organization,
            workspace,
            child_pass_threshold=0.4,
            link_config=None,
            pinned_version_threshold=None,
        )
        # Child scores 0.45: passes 0.4 gate.
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"prec-child": 0.45}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(1.0)

    def test_default_0_5_when_all_sources_are_missing(
        self, db, organization, workspace
    ):
        # child.pass_threshold=None (missing on model). Should default to 0.5.
        child = _make_percentage_child(organization, workspace, "def-child")
        child.pass_threshold = None
        child.save(update_fields=["pass_threshold"])
        parent = _make_composite(organization, workspace, "def-comp", "pass_rate")
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, weight=1.0
        )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        # Score 0.6 passes 0.5 default.
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"def-child": 0.6}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(1.0)


@pytest.mark.django_db
class TestCompositeOutputTypesEndToEnd:
    """Score routing across the four child output-type axes."""

    def test_pass_fail_children_score_correctly(
        self, db, organization, workspace
    ):
        child_a = EvalTemplate.no_workspace_objects.create(
            name="pf-a",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
            output_type_normalized="pass_fail",
            pass_threshold=0.5,
        )
        child_b = EvalTemplate.no_workspace_objects.create(
            name="pf-b",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
            output_type_normalized="pass_fail",
            pass_threshold=0.5,
        )
        parent = _make_composite(organization, workspace, "pf-comp", "avg")
        CompositeEvalChild.objects.create(parent=parent, child=child_a, order=0, weight=1.0)
        CompositeEvalChild.objects.create(parent=parent, child=child_b, order=1, weight=1.0)
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )

        def _fake(_cfg, _map, template, *_a, **_k):
            return _fake_response(
                "Passed" if template.name == "pf-a" else "Failed",
                output_type="Pass/Fail",
            )

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_fake,
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        by_name = {cr.child_name: cr for cr in outcome.child_results}
        assert by_name["pf-a"].score == pytest.approx(1.0)
        assert by_name["pf-b"].score == pytest.approx(0.0)
        assert outcome.aggregate_score == pytest.approx(0.5)

    def test_percentage_children_score_correctly(
        self, db, organization, workspace
    ):
        parent, links, _ = TestCompositeAggregationFunctions()._build(
            organization, workspace, "avg", weights=[1.0, 1.0, 1.0]
        )
        canned = {"child-a": 0.2, "child-b": 0.6, "child-c": 1.0}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name(canned),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        by_name = {cr.child_name: cr for cr in outcome.child_results}
        assert by_name["child-a"].score == pytest.approx(0.2)
        assert by_name["child-b"].score == pytest.approx(0.6)
        assert by_name["child-c"].score == pytest.approx(1.0)
        assert outcome.aggregate_score == pytest.approx(0.6)

    def test_code_children_score_correctly(
        self, db, organization, workspace
    ):
        # Code eval with output_type_normalized="percentage": the runtime
        # emits a numeric score directly via the CustomCodeEval evaluator.
        child = EvalTemplate.no_workspace_objects.create(
            name="code-child",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            eval_type="code",
            config={
                "output": "score",
                "eval_type_id": "CustomCodeEval",
                "code": "def evaluate(**kwargs): return 0.7",
                "language": "python",
            },
            output_type_normalized="percentage",
            pass_threshold=0.5,
        )
        parent = _make_composite(organization, workspace, "code-comp", "avg")
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, weight=1.0
        )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"code-child": 0.7}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.child_results[0].score == pytest.approx(0.7)
        assert outcome.aggregate_score == pytest.approx(0.7)
        assert outcome.aggregate_pass is True


@pytest.mark.django_db
class TestCompositeAggregationEdgeCases:
    def test_all_children_fail_aggregate_is_none(
        self, db, organization, workspace
    ):
        parent, links, _ = TestCompositeAggregationFunctions()._build(
            organization, workspace, "avg", weights=[1.0, 1.0, 1.0]
        )

        def _all_raise(*_a, **_k):
            raise RuntimeError("boom")

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_all_raise,
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score is None
        assert outcome.aggregate_pass is None
        assert all(cr.status == "failed" for cr in outcome.child_results)

    def test_pass_rate_denominator_excludes_failed_children(
        self, db, organization, workspace
    ):
        """Documented behaviour: pass_rate denominator = number of scored children,
        not total children. If children fail, pass_rate can inflate."""
        parent, links, children = TestCompositeAggregationFunctions()._build(
            organization, workspace, "pass_rate", weights=[1.0, 1.0, 1.0]
        )

        def _mixed(_cfg, _map, template, *_a, **_k):
            if template.name == "child-a":
                raise RuntimeError("simulated failure")
            return _fake_response(0.8)  # both remaining pass 0.5 gate

        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_mixed,
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        # 2 completed children, both pass → 2/2 = 1.0. Failed child excluded.
        assert outcome.aggregate_score == pytest.approx(1.0)

    def test_aggregate_pass_true_when_score_meets_threshold(
        self, db, organization, workspace
    ):
        parent = _make_composite(
            organization, workspace, "at-comp", "avg", pass_threshold=0.7
        )
        child = _make_percentage_child(organization, workspace, "at-child")
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, weight=1.0
        )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"at-child": 0.75}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(0.75)
        assert outcome.aggregate_pass is True

    def test_aggregate_pass_false_when_score_below_threshold(
        self, db, organization, workspace
    ):
        parent = _make_composite(
            organization, workspace, "bt-comp", "avg", pass_threshold=0.7
        )
        child = _make_percentage_child(organization, workspace, "bt-child")
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, weight=1.0
        )
        links = list(
            CompositeEvalChild.objects.filter(parent=parent)
            .select_related("child")
            .order_by("order")
        )
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_canned_by_name({"bt-child": 0.65}),
        ):
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "x"},
                config={},
                org=organization,
            )
        assert outcome.aggregate_score == pytest.approx(0.65)
        assert outcome.aggregate_pass is False


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

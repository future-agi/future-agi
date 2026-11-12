"""Pin excluded optimizer scoring to the pre-Choices-union (460e0f3db6) contract.

The exporters, shell reconstruction, run_eval_func, EvaluationRunner formatter,
and final optimizer scorer are real. Only the raw judge and unrelated services
are mocked. In particular, do not mock run_eval_func with an already scored
output: that would hide the override leak this suite is meant to catch.

Cloud baseline/red/green execution is required; local execution is not evidence.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("no_external_lookups")]

LABELS = ("Good", "Neutral", "Bad")
TEMPLATE_SCORES = {"Good": 1.0, "Neutral": 0.5, "Bad": 0.0}
OVERRIDE = {"Good": 0.25}
TEMPLATE_ID = "00000000-0000-0000-0000-000000000001"
CONFIG_ID = "00000000-0000-0000-0000-000000000002"
ROW_ID = "00000000-0000-0000-0000-000000000003"


def _template(choice_scores, *, legacy_choices=False):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate(
        id=TEMPLATE_ID,
        name="optimizer_choices_contract",
        description="Choices optimizer regression fixture",
        criteria="Return one label.",
        model="test-model",
        choices=list(LABELS),
        choice_scores=deepcopy(choice_scores),
        output_type_normalized="deterministic",
        config={
            "eval_type_id": "LlmEvaluator",
            "output": "choices",
            "required_keys": ["output"],
            # Modern templates store choices in the column above. The legacy
            # nested shape exercises the optimizer's early skip separately.
            "config": {"choices": list(LABELS)} if legacy_choices else {},
        },
    )


def _reconstruct(execution_data):
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent

    optimizer = FixYourAgent()
    with patch.object(optimizer, "optimize") as optimize:
        optimizer.optimize_from_execution(execution_data=execution_data)
    optimize.assert_called_once()
    configs = optimize.call_args.kwargs["eval_configs"]
    assert len(configs) == 1
    return configs[0]


def _agent_config(binding_config, *, legacy_choices=False):
    from simulate.models import SimulateEvalConfig
    from simulate.utils.agent_optimiser import _build_evaluation_data

    saved = SimulateEvalConfig(
        id=CONFIG_ID,
        name="optimizer_choices_contract",
        eval_template=_template(TEMPLATE_SCORES, legacy_choices=legacy_choices),
        model="test-model",
        mapping={"output": "reply"},
        config=deepcopy(binding_config),
    )
    call = SimpleNamespace(
        call_metadata={"row_data": {"reply": "a reply"}},
        eval_outputs={},
        recording_url=None,
        stereo_recording_url=None,
        call_summary="",
        duration_seconds=0,
    )
    with patch(
        "simulate.utils.agent_optimiser._get_transcript_text", return_value="transcript"
    ):
        exported = _build_evaluation_data(saved, call)
    # This omission is pre-existing; fixing it would change the excluded path.
    assert "choice_scores" not in exported
    rebuilt = _reconstruct(
        {
            "agent_definition_prompt": {"description": "Reply helpfully."},
            "call_executions": [{"evaluations": [exported], "transcripts": []}],
        }
    )
    assert rebuilt.eval_template.choice_scores is None
    assert rebuilt.config == binding_config
    return rebuilt


def _dataset_config(binding_config, template_scores):
    from model_hub.models.develop_dataset import Cell, Column, Row
    from tfc.temporal.dataset_optimization.activities import (
        _prepare_dataset_execution_data,
    )

    template = _template(template_scores)
    metric = SimpleNamespace(
        id=CONFIG_ID,
        name=template.name,
        template=template,
        model=template.model,
        config={"mapping": {"output": "output"}, **deepcopy(binding_config)},
    )
    column = SimpleNamespace(id="output-column", name="reply")
    columns = MagicMock()
    columns.__iter__.return_value = iter([column])
    columns.values_list.return_value = [column.name]
    with (
        patch.object(Row.objects, "filter") as rows,
        patch.object(Column.objects, "filter", return_value=columns),
        patch.object(Cell.objects, "filter") as cells,
    ):
        rows.return_value.order_by.return_value = [SimpleNamespace(id=ROW_ID)]
        cells.return_value.select_related.return_value = [
            SimpleNamespace(column=column, value="a reply")
        ]
        execution_data = _prepare_dataset_execution_data(
            column, SimpleNamespace(id="dataset"), [metric], "Reply helpfully."
        )
    rebuilt = _reconstruct(execution_data)
    assert rebuilt.eval_template.choice_scores == template_scores
    # Metric-level overrides are dropped; config.config becomes the binding.
    assert rebuilt.config == binding_config.get("config", {})
    return rebuilt, template


@pytest.fixture
def raw_judge(request):
    """Keep runner configuration/formatting real and return raw labels only."""
    request.getfixturevalue("no_external_lookups")
    judge = MagicMock()
    with (
        patch("evaluations.engine.registry.get_eval_class", return_value=MagicMock()),
        patch(
            "evaluations.engine.instance.create_eval_instance",
            return_value=(judge, None),
        ),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.map_fields",
            return_value={"output": "a reply"},
        ),
        patch(
            "model_hub.models.evals_metric.EvalTemplateVersion.objects.get_default",
            return_value=None,
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.inject_context"
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.resolve_preview_examples",
            return_value=None,
        ),
        patch("model_hub.views.utils.evals.log_and_deduct_cost_for_api_request", None),
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True),
        ),
    ):
        yield judge


def _evaluator(kind):
    if kind == "agent":
        from ee.agenthub.fix_your_agent.fix_your_agent import SimulationEvaluator

        evaluator = SimulationEvaluator.__new__(SimulationEvaluator)
        evaluator.eval_lookup = {}
        evaluator._eval_template_cache = {}
    else:
        from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator

        evaluator = DirectEvaluator.__new__(DirectEvaluator)
        evaluator.execution_model = "test-model"
    evaluator.organization = SimpleNamespace(id="00000000-0000-0000-0000-000000000004")
    evaluator.workspace = None
    evaluator.eval_source = "fix_your_agent"
    return evaluator


def _score_all(kind, config, judge):
    evaluator = _evaluator(kind)
    results = {}
    for label in LABELS:
        judge.run.reset_mock()
        judge.run.return_value = SimpleNamespace(
            eval_results=[{"data": label, "reason": "raw mocked label"}]
        )
        if kind == "agent":
            score, reason = evaluator._run_eval_with_config(
                config, "a transcript", "a prompt", {}
            )
        else:
            score, reason = evaluator._run_eval_with_config(
                config, {}, "a reply", "a prompt"
            )
        # Refuse a false green from a swallowed error, skip, or direct fallback.
        assert reason == "raw mocked label"
        judge.run.assert_called_once_with(output="a reply")
        results[label] = score
    return results


@pytest.mark.parametrize(
    "binding_config",
    [{}, {"choice_scores": OVERRIDE}],
    ids=["no-override", "partial-override"],
)
def test_agent_optimizer_keeps_pre_union_scores(binding_config, raw_judge):
    config = _agent_config(binding_config)
    before = deepcopy(config.config)
    scores = _score_all("agent", config, raw_judge)
    assert config.config == before
    # 460e0f3db6 formats raw labels and the shell scorer defaults each to 0.5.
    assert scores == dict.fromkeys(LABELS, 0.5)


@pytest.mark.parametrize(
    "binding_config",
    [{}, {"choice_scores": OVERRIDE}],
    ids=["no-override", "partial-override"],
)
def test_agent_legacy_choices_still_skip(binding_config, raw_judge):
    config = _agent_config(binding_config, legacy_choices=True)
    result = _evaluator("agent")._run_eval_with_config(
        config, "transcript", "prompt", {}
    )
    assert result == (0.5, "skipped_choices")
    raw_judge.run.assert_not_called()


@pytest.mark.parametrize(
    "binding_config",
    [{}, {"choice_scores": OVERRIDE}, {"config": {"choice_scores": OVERRIDE}}],
    ids=["no-override", "metric-top-level", "binding-top-level"],
)
@pytest.mark.parametrize(
    "template_scores", [None, TEMPLATE_SCORES], ids=["unmapped", "mapped"]
)
def test_dataset_optimizer_keeps_pre_union_scores(
    binding_config, template_scores, raw_judge
):
    from model_hub.models.evals_metric import EvalTemplate

    config, saved_template = _dataset_config(binding_config, template_scores)
    before = deepcopy(config.config)
    # DirectEvaluator resolves the saved template; no ORM or provider is used.
    with patch.object(EvalTemplate.objects, "get", return_value=saved_template):
        scores = _score_all("dataset", config, raw_judge)
    assert config.config == before
    assert scores == (
        TEMPLATE_SCORES if template_scores else dict.fromkeys(LABELS, 0.5)
    )

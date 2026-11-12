"""W10-JEV-USAGE-PRICING-L02: Test must use the edited Jev definition."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("ee.evals.llm.jev_evaluator.evaluator")

from ee.jev.mapping import validate_jev_mapping  # noqa: E402
from evaluations.engine.instance import create_eval_instance  # noqa: E402
from model_hub.views.eval_runner import EvaluationRunner  # noqa: E402
from model_hub.views.utils.evals import run_eval_func  # noqa: E402

pytestmark = pytest.mark.requires_ee


class _DefinitionCaptured(BaseException):
    """Stop the playground before transport, formatting, or usage writes."""


def _definitions(output):
    live_config = {
        "eval_type_id": "CustomPromptEvaluator",
        "model": "jev-latest",
        "rule_prompt": "NEW {{input}}",
        "system_prompt": "NEW system",
        "required_keys": ["input"],
        "output": output,
    }
    saved_config = {
        **live_config,
        "rule_prompt": "OLD {{input}}",
        "system_prompt": "OLD system",
    }
    live_scores = None
    saved_scores = None
    if output == "choices":
        live_scores = {"Bad": 1, "Good": 0}
        saved_scores = {"Bad": 0, "Good": 1}
        live_config["jev_mapping"] = {
            "choice": {"descriptions": {"Bad": "NEW bad", "Good": "NEW good"}}
        }
        saved_config["jev_mapping"] = {
            "choice": {"descriptions": {"Bad": "OLD bad", "Good": "OLD good"}}
        }
    elif output == "score":
        live_config["jev_mapping"] = {
            "score": {"levels": ["NEW poor", "NEW fair", "NEW best"]}
        }
        saved_config["jev_mapping"] = {
            "score": {"levels": ["OLD bad", "OLD good"]}
        }
    else:
        live_config["jev_mapping"] = {
            "pass": {"criteria_true": "NEW pass", "criteria_false": "NEW fail"}
        }
        saved_config["jev_mapping"] = {
            "pass": {"criteria_true": "OLD pass", "criteria_false": "OLD fail"}
        }
    template = SimpleNamespace(
        id="preview-template",
        eval_type="llm",
        config=live_config,
        choice_scores=live_scores,
        choices=list(live_scores or {}),
        multi_choice=False,
        pass_threshold=0.25,
        organization=SimpleNamespace(id="preview-org"),
        workspace=None,
    )
    saved = SimpleNamespace(
        model="jev-latest",
        config_snapshot=saved_config,
        prompt_messages=[
            {"role": "system", "content": "OLD system"},
            {"role": "user", "name": "eval_prompt", "content": "OLD {{input}}"},
        ],
        choice_scores=saved_scores,
        pass_threshold=0.75,
    )
    return template, saved


def _capture_definition(template, saved, invoke):
    before = deepcopy((vars(template), vars(saved) if saved else None))
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=saved),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("accounts.models.organization.Organization.objects.filter") as orgs,
        patch("evaluations.engine.instance._get_typesafe_api_key", return_value=None),
        patch(
            "ee.evals.llm.jev_evaluator.evaluator.JevEvaluator",
            side_effect=_DefinitionCaptured,
        ) as constructor,
    ):
        orgs.return_value.first.return_value = template.organization
        with pytest.raises(_DefinitionCaptured):
            invoke()
    assert (vars(template), vars(saved) if saved else None) == before
    return constructor.call_args.kwargs


def _assert_definition(config, template, saved=None):
    definition = saved.config_snapshot if saved else template.config
    scores = saved.choice_scores if saved else template.choice_scores
    assert config["rule_prompt"] == definition["rule_prompt"]
    assert config["system_prompt"] == definition["system_prompt"]
    assert config["output_type"] == definition["output"]
    assert config["choice_scores"] == scores
    assert config["pass_threshold"] == (
        saved.pass_threshold if saved else template.pass_threshold
    )
    assert config["jev_mapping"] == validate_jev_mapping(
        template,
        output=definition["output"],
        choice_scores=scores,
        multi_choice=False,
        config=definition,
    ).to_dict()


@pytest.mark.parametrize("source", ["eval_playground", "eval_playground_test"])
@pytest.mark.parametrize("output", ["choices", "score", "Pass/Fail"])
def test_playground_uses_current_jev_definition_without_saving_version(source, output):
    template, saved = _definitions(output)
    config = _capture_definition(
        template,
        saved,
        lambda: run_eval_func(
            {"config": {}},
            {"input": "example"},
            template,
            template.organization,
            model="jev-latest",
            source=source,
        ),
    )
    _assert_definition(config, template)


@pytest.mark.parametrize("has_saved_version", [False, True])
def test_preview_output_change_does_not_restore_saved_rubric(has_saved_version):
    template, saved = _definitions("score")
    template.config["output"] = "Pass/Fail"
    # Removing a mapping is an edit too: the old rubric must not leak back in.
    template.config.pop("jev_mapping")
    config = _capture_definition(
        template,
        saved if has_saved_version else None,
        lambda: run_eval_func(
            {"config": {}},
            {"input": "example"},
            template,
            template.organization,
            model="jev-latest",
            source="eval_playground",
        ),
    )
    _assert_definition(config, template)


@pytest.mark.parametrize("version_number", [None, 1])
@pytest.mark.parametrize("output", ["choices", "score", "Pass/Fail"])
def test_production_jev_runs_keep_selected_saved_definition(version_number, output):
    template, saved = _definitions(output)
    config = _capture_definition(
        template,
        saved,
        lambda: create_eval_instance(
            Mock(), template, model="jev-latest", version_number=version_number
        ),
    )
    _assert_definition(config, template, saved)


@pytest.mark.parametrize(
    ("source", "version_number", "metric_id"),
    [
        ("dataset", None, None),
        ("eval_playground", 1, None),
        ("eval_playground", None, "bound-metric"),
    ],
    ids=["non-preview", "explicit-version", "metric-binding"],
)
def test_runner_preview_does_not_replace_saved_execution(source, version_number, metric_id):
    template, saved = _definitions("choices")
    runner = EvaluationRunner(
        user_eval_metric_id=metric_id,
        format_output=True,
        source=source,
        version_number=version_number,
    )
    runner.eval_template = template
    config = _capture_definition(
        template,
        saved,
        lambda: runner._create_eval_instance(
            config={}, eval_class=Mock(), model="jev-latest"
        ),
    )
    _assert_definition(config, template, saved)

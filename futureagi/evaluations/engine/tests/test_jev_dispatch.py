"""R-04..R-06, R-14, R-18: effective model dispatch before BYOK resolution."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.mapping import JevMappingError
from evaluations.engine.instance import create_eval_instance
from evaluations.engine.registry import get_eval_class

pytestmark = pytest.mark.requires_ee


def template(model="gpt-4o", **config):
    return SimpleNamespace(
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": model,
            "rule_prompt": "Judge {{input}}",
            "output": "Pass/Fail",
            "required_keys": ["input"],
            **config,
        },
        organization=SimpleNamespace(id="trusted-org"),
        workspace=None,
        choice_scores=None,
        choices=[],
        pass_threshold=0.5,
        multi_choice=False,
    )


def version(model="jev-latest", **snapshot):
    return SimpleNamespace(
        model=model,
        config_snapshot=snapshot,
        prompt_messages=[],
        pass_threshold=0.7,
        choice_scores=None,
    )


@pytest.mark.parametrize("eval_type", ["CustomPromptEvaluator", "AgentEvaluator"])
def test_r05_c22_version_jev_over_byok_no_key_lookup(eval_type):
    tpl = template(eval_type_id=eval_type, agent_mode="quick")
    before = deepcopy(tpl.config)
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=version()),
        patch(
            "evaluations.engine.instance._get_api_key",
            side_effect=AssertionError("BYOK must not run"),
        ),
        patch("tfc.capabilities.service.check_or_raise") as capability,
    ):
        instance, _ = create_eval_instance(Mock(), tpl, model=None)
    assert isinstance(instance, JevEvaluator)
    assert instance._model == "jev-latest"
    assert instance.provider == "typesafe"
    assert instance.api_key is None
    assert instance._pass_threshold == 0.7
    capability.assert_called_once_with("jev_models", org_id="trusted-org")
    assert tpl.config == before


def test_r04_c22_default_argument_does_not_hide_jev_version():
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=version()),
        patch("tfc.capabilities.service.check_or_raise"),
        patch(
            "evaluations.engine.instance._get_api_key",
            side_effect=AssertionError("BYOK"),
        ),
    ):
        instance, _ = create_eval_instance(Mock(), template())
    assert isinstance(instance, JevEvaluator)


def test_r04_version_mapping_snapshot_and_runtime_precedence():
    tpl = template(output="score", jev_mapping={"score": {"levels": ["old", "new"]}})
    resolved = version(
        output="score", jev_mapping={"score": {"levels": ["bad", "ok", "good"]}}
    )
    runtime = {
        "run_config": {
            "model": "jev-1.13.0",
            "pass_threshold": 0,
            "reverse_output": True,
        }
    }
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=resolved),
        patch("tfc.capabilities.service.check_or_raise"),
    ):
        instance, _ = create_eval_instance(
            Mock(), tpl, model="jev-1.13.0", runtime_config=runtime
        )
    assert instance._model == "jev-1.13.0"
    assert instance._pass_threshold == 0
    assert instance._reverse_output is True
    assert instance.mapping.score["levels"] == ["bad", "ok", "good"]


def test_r04_c20_binding_numeric_requires_levels():
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        with pytest.raises(JevMappingError, match="JEV_SCORE_LEVELS_REQUIRED"):
            create_eval_instance(
                Mock(),
                template(output="score"),
                model="jev-latest",
                runtime_config={"run_config": {"model": "jev-latest"}},
            )
    client.assert_not_called()


def test_r14_capability_denial_before_constructor_or_transport():
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "tfc.capabilities.service.check_or_raise",
            side_effect=PermissionError("denied"),
        ),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        with pytest.raises(PermissionError):
            create_eval_instance(Mock(), template("jev-latest"), model=None)
    client.assert_not_called()


def test_r06_unknown_jev_rejected_before_key_lookup():
    """An unsupported jev-* ID fails closed on the direct engine path: typed
    JEV_MODEL_UNKNOWN, and neither the BYOK key lookup nor the Jev client runs.
    Covers template, version and run_config sources (review finding 1)."""
    from ee.jev.mapping import JevMappingError

    cases = [
        {"template_model": "jev-2.0.0", "version_model": None, "model": None},
        {"template_model": "turing_large", "version_model": "jev-preview", "model": None},
        {
            "template_model": "turing_large",
            "version_model": None,
            "model": None,
            "runtime_config": {"run_config": {"model": "JEV-9"}},
        },
    ]
    for case in cases:
        with (
            patch(
                "evaluations.engine.instance.resolve_version",
                return_value=version(case["version_model"])
                if case["version_model"]
                else None,
            ),
            patch("evaluations.engine.instance._get_api_key") as key_lookup,
            patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
        ):
            with pytest.raises(JevMappingError, match="JEV_MODEL_UNKNOWN"):
                create_eval_instance(
                    Mock(),
                    template(case["template_model"]),
                    model=case["model"],
                    runtime_config=case.get("runtime_config"),
                )
        key_lookup.assert_not_called()
        client.assert_not_called()


def test_r18_c21_turing_binding_over_jev_version_uses_turing():
    cls = Mock()
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=version()),
        patch("tfc.capabilities.service.check_or_raise") as capability,
    ):
        create_eval_instance(
            cls,
            template("jev-latest"),
            model="turing_small",
            runtime_config={"run_config": {"model": "turing_small"}},
        )
    assert cls.call_args.kwargs["model"] == "turing_small"
    assert cls.call_args.kwargs["provider"] == "turing"
    capability.assert_not_called()


@pytest.mark.parametrize(
    "model", ["turing_large", "turing_small", "turing_flash", "gpt-4o"]
)
def test_r18_existing_provider_path(model):
    cls = Mock()
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "evaluations.engine.instance._get_api_key", return_value="fixture-key"
        ) as key,
        patch(
            "model_hub.utils.llm_providers.get_provider_for_model",
            return_value="openai",
        ),
    ):
        create_eval_instance(cls, template(model), model=model)
    assert cls.call_args.kwargs["model"] == model
    if model.startswith("turing_"):
        assert cls.call_args.kwargs["provider"] == "turing"
        key.assert_not_called()
    else:
        assert cls.call_args.kwargs["api_key"] == "fixture-key"
        key.assert_called_once()


def test_r05_registry_has_jev_without_template_migration():
    assert get_eval_class("JevEvaluator") is JevEvaluator


def test_r04_explicit_model_equal_to_template_still_beats_jev_version():
    cls = Mock()
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=version()),
        patch("evaluations.engine.instance._get_api_key", return_value="fixture-key"),
        patch(
            "model_hub.utils.llm_providers.get_provider_for_model",
            return_value="openai",
        ),
        patch("tfc.capabilities.service.check_or_raise") as capability,
    ):
        create_eval_instance(cls, template("gpt-4o"), model="gpt-4o")
    assert cls.call_args.kwargs["model"] == "gpt-4o"
    assert cls.call_args.kwargs["provider"] == "openai"
    capability.assert_not_called()


def test_r04_runtime_choice_scores_reach_formatted_result():
    from evaluations.engine.runner import EvalRequest, run_eval

    tpl = template("jev-latest", output="choices")
    tpl.name = "jev-test"
    tpl.choice_scores = {"Yes": 1, "No": 0}
    tpl.choices = ["Yes", "No"]
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch("tfc.capabilities.service.check_or_raise"),
        patch(
            "ee.jev.client.JevSystemOneClient.evaluate",
            return_value={
                "model": "jev-1.13.0",
                "answers": {
                    "q1": {
                        "type": "choice",
                        "choice": "Yes",
                        "probabilities": {"Yes": 0.9, "No": 0.1},
                        "confidence": 0.9,
                    }
                },
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        ),
    ):
        result = run_eval(
            EvalRequest(
                eval_template=tpl,
                inputs={"input": "text"},
                skip_params_preparation=True,
                runtime_config={
                    "run_config": {"choice_scores": {"Yes": 0.25, "No": 0.75}}
                },
            )
        )
    assert result.value == {"score": 0.25, "choice": "Yes"}
    assert result.failure is True
    assert tpl.choice_scores == {"Yes": 1, "No": 0}


def test_r14_engine_checks_capability_before_loading_ee_evaluator():
    import builtins

    original_import = builtins.__import__

    def without_jev(name, *args, **kwargs):
        if name == "ee.evals.llm.jev_evaluator.evaluator":
            raise ImportError("EE code unavailable")
        return original_import(name, *args, **kwargs)

    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "tfc.capabilities.service.check_or_raise",
            side_effect=PermissionError("denied"),
        ),
        patch("builtins.__import__", side_effect=without_jev),
    ):
        with pytest.raises(PermissionError):
            create_eval_instance(Mock(), template("jev-latest"), model=None)

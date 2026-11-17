"""A provider-prefixed model id is treated as its base model: metering logs it
under its base model's call type, and the FutureAGI model config a run uses
is its base model's, so the two agree. Plain ids, and the model config
lookup's prefix fallback, are unchanged."""

from types import SimpleNamespace

import pytest

from tfc.constants.api_calls import APICallTypeChoices


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("x/turing_small", APICallTypeChoices.TURING_SMALL_EVALUATOR.value),
        ("vertex_ai/turing_flash", APICallTypeChoices.TURING_FLASH_EVALUATOR.value),
        ("x/protect_flash", APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value),
    ],
)
def test_a_prefixed_turing_id_meters_as_its_base_model(model, expected):
    from sdk.utils.helpers import _get_api_call_type

    assert _get_api_call_type(model) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("turing_small", APICallTypeChoices.TURING_SMALL_EVALUATOR.value),
        ("turing_flash", APICallTypeChoices.TURING_FLASH_EVALUATOR.value),
        ("protect", APICallTypeChoices.PROTECT_EVALUATOR.value),
        ("gpt-4o", APICallTypeChoices.TURING_LARGE_EVALUATOR.value),
        ("openai/gpt-4o", APICallTypeChoices.TURING_LARGE_EVALUATOR.value),
    ],
)
def test_plain_and_non_turing_ids_meter_as_before(model, expected):
    from sdk.utils.helpers import _get_api_call_type

    assert _get_api_call_type(model) == expected


def test_a_prefixed_turing_id_runs_as_its_base_model():
    from evaluations.engine.instance import _get_futureagi_model_config

    assert _get_futureagi_model_config("x/turing_small") == ("turing_small", "turing")
    assert _get_futureagi_model_config("turing_small") == ("turing_small", "turing")
    assert _get_futureagi_model_config("openai/gpt-4o") == ("turing_large", "turing")


def test_the_runner_model_config_agrees():
    from model_hub.views.eval_runner import EvaluationRunner

    lookup = EvaluationRunner._get_futureagi_model_config
    runner = SimpleNamespace()
    assert lookup(runner, model="x/turing_small") == ("turing_small", "turing")
    assert lookup(runner, model="turing_small") == ("turing_small", "turing")
    assert lookup(runner, model="openai/gpt-4o") == ("turing_large", "turing")


def test_model_config_prefix_fallback_is_unchanged():
    from agentic_eval.core.utils.model_config import ModelConfigs

    assert ModelConfigs.get_config("x/turing_small") is ModelConfigs.TURING_SMALL
    assert (
        ModelConfigs.get_config("gemini-2.5-pro") is ModelConfigs.VERTEX_GEMINI_2_5_PRO
    )
    # The fallback stays case-sensitive.
    assert ModelConfigs.get_config("x/TURING_SMALL") is None

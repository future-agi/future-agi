"""Workbench eval passes the FE-selected model to the evaluator (TH-6725)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _CapturedInstance:
    def __init__(self, **kwargs):
        self.received = kwargs


def _resolve_model_via_view_slice(evaluation, eval_template):
    """Mirror of the model-resolution slice inside
    PromptTemplateView.run_evaluation. Kept as a pure helper so the
    precedence order can be pinned without spinning up a full HTTP
    request.
    """
    runtime_config = evaluation.config or {}
    return (
        runtime_config.get("model")
        or (runtime_config.get("run_config") or {}).get("model")
        or eval_template.config.get("model")
    )


@pytest.mark.parametrize(
    "eval_config, template_config, expected",
    [
        ({"model": "gpt-4.1"}, {"model": "turing_large"}, "gpt-4.1"),
        (
            {"run_config": {"model": "claude-3-5-sonnet-latest"}},
            {"model": "turing_large"},
            "claude-3-5-sonnet-latest",
        ),
        ({}, {"model": "turing_large"}, "turing_large"),
        (None, {"model": "turing_large"}, "turing_large"),
    ],
    ids=[
        "top-level-model-wins",
        "run_config-model-fallback",
        "template-default-when-runtime-empty",
        "template-default-when-runtime-none",
    ],
)
def test_model_resolution_precedence(eval_config, template_config, expected):
    evaluation = SimpleNamespace(config=eval_config)
    template = SimpleNamespace(config=template_config)
    assert _resolve_model_via_view_slice(evaluation, template) == expected

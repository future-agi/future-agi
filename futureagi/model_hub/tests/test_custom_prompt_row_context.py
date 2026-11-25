"""The playground's JSON row context must reach the live judge as data."""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from evaluations.engine.registry import get_eval_class
from model_hub.models.evals_metric import EvalTemplate
from model_hub.serializers.eval_runner import EvalPlayGroundSerializer
from model_hub.utils.eval_input_validation import validate_eval_inputs


@pytest.fixture
def judge(monkeypatch):
    # Keep real construction, rendering and run(); replace only provider I/O.
    monkeypatch.setattr(LLM, "_init_client", lambda *_args: None)
    evaluator_class = get_eval_class("CustomPromptEvaluator")
    assert evaluator_class is CustomPromptEvaluator
    evaluator = evaluator_class(
        rule_prompt="Evaluate the supplied data.",
        model="gpt-4.1",
        provider="openai",
        output_type="Pass/Fail",
    )
    monkeypatch.setattr(
        evaluator,
        "call_llm",
        Mock(return_value='{"result": "Pass", "explanation": "Data evaluated."}'),
    )
    return evaluator


def _run_row_context(judge, row_context, **mapped_inputs):
    serializer = EvalPlayGroundSerializer(
        data={"template_id": str(uuid4()), "row_context": row_context}
    )
    assert serializer.is_valid(), serializer.errors
    template = EvalTemplate(config={"required_keys": list(mapped_inputs)})
    warning, run_kwargs = validate_eval_inputs(
        template,
        {
            **mapped_inputs,
            "required_keys": list(mapped_inputs),
            "row_context": serializer.validated_data["row_context"],
        },
    )
    assert warning is None
    result = judge.run(**run_kwargs)
    assert result.eval_results[0]["data"]["result"] == "Pass"
    judge.call_llm.assert_called_once()
    return judge.call_llm.call_args.kwargs["prompt"][-1]["content"]


@pytest.mark.parametrize(
    ("row_context", "expected"),
    [
        (
            [{"input": "hello"}, {"output": "world"}],
            "1. **input:** hello\n2. **output:** world",
        ),
        (["hello", 42, True], "1. hello\n2. 42\n3. True"),
        ({"input": "hello", "output": "world"}, "**input:** hello\n**output:** world"),
        ("plain context", "plain context"),
    ],
    ids=["object-list", "scalar-list", "object", "string"],
)
def test_playground_row_context_reaches_live_judge(judge, row_context, expected):
    user_message = _run_row_context(judge, row_context)
    assert f"\n\n## Data\n{expected}\n\nPlease return" in user_message


def test_empty_row_context_does_not_add_data_section(judge):
    user_message = _run_row_context(judge, [])
    assert "## Data" not in user_message


def test_mapped_inputs_do_not_also_inject_row_context(judge):
    user_message = _run_row_context(judge, ["unmapped row data"], input="hello")
    assert "<input>hello</input>" in user_message
    assert "## Data" not in user_message
    assert "unmapped row data" not in user_message


def test_large_list_row_context_is_windowed(judge):
    user_message = _run_row_context(judge, ["x" * 210_000 + "END_SENTINEL"])
    assert "## Data\n1. " in user_message
    assert "[truncated," in user_message
    assert "END_SENTINEL" not in user_message
    assert len(user_message) < 200_000

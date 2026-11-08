from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from evaluations.engine.params import prepare_run_params


@pytest.fixture(params=[False, True], ids=["customer-model", "turing"])
def judge(request, monkeypatch):
    monkeypatch.setattr(LLM, "__init__", lambda self, **kwargs: None)
    evaluator = CustomPromptEvaluator(
        rule_prompt="Evaluate {{output}}", model="test-model", provider="openai"
    )
    evaluator.temperature = 0
    evaluator.max_tokens = 256
    evaluator.token_usage = {
        "completion_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
    }
    evaluator.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
    evaluator._system_message()
    evaluator._is_turing = request.param
    provider_call = Mock(return_value='{"result": "Pass", "explanation": "ok"}')
    if request.param:
        turing_module = pytest.importorskip("ee.turing.client")
        client = SimpleNamespace(
            chat_completion=provider_call,
            token_usage=evaluator.token_usage.copy(),
            cost=evaluator.cost.copy(),
        )
        monkeypatch.setattr(turing_module, "TuringClient", lambda: client)
    else:
        evaluator.call_llm = provider_call
    return evaluator, provider_call


def evaluate_messages(judge, **kwargs):
    evaluator, provider_call = judge
    evaluator._evaluate(required_keys=["output"], output="current case", **kwargs)
    return provider_call.call_args.kwargs[
        "messages" if evaluator._is_turing else "prompt"
    ]


def test_retrieved_feedback_reaches_judge(judge, monkeypatch):
    feedback = [{"type": "text", "text": "Human correction: mark this as failed."}]
    retrieval = Mock(return_value=feedback)
    monkeypatch.setattr("evaluations.engine.params._get_few_shot_examples", retrieval)
    template = SimpleNamespace(
        config={"eval_type_id": "CustomPromptEvaluator", "required_keys": ["output"]},
        criteria="Evaluate {{output}}",
        name="feedback judge",
    )
    params = prepare_run_params(
        inputs={"output": "current case"},
        eval_template=template,
        is_futureagi=False,
        organization_id="owning-organization",
        workspace_id="owning-workspace",
    )
    evaluator, provider_call = judge
    evaluator._evaluate(**params)
    messages = provider_call.call_args.kwargs[
        "messages" if evaluator._is_turing else "prompt"
    ]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][0] == feedback[0]
    assert "current case" in messages[-1]["content"][1]["text"]
    retrieval.assert_called_once()
    assert retrieval.call_args.kwargs["organization_id"] == "owning-organization"


@pytest.mark.parametrize("few_shots", [None, [], ""])
def test_empty_feedback_preserves_plain_text_request(judge, few_shots):
    baseline = evaluate_messages(judge)
    assert evaluate_messages(judge, few_shots=few_shots) == baseline
    assert isinstance(baseline[-1]["content"], str)


def test_text_feedback_is_normalized_to_content_block(judge):
    messages = evaluate_messages(judge, few_shots="Human correction")
    assert messages[-1]["content"][0] == {
        "type": "text",
        "text": "Human correction",
    }


def test_feedback_preserves_ground_truth_media_and_static_examples(judge, monkeypatch):
    evaluator, _ = judge
    evaluator.system_prompt = "Custom judge system prompt"
    evaluator._few_shot_examples = [
        {"input": "static input", "output": "static answer"}
    ]
    evaluator._messages = [{"role": "user", "content": "Editor {{output}}"}]
    feedback = [
        {"type": "text", "text": "Correction {{output}} stays literal"},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.test/feedback.png"},
        },
    ]
    ground_truth = [{"type": "text", "text": "Ground truth example"}]
    media = [
        {"type": "image_url", "image_url": {"url": "https://example.test/current.png"}}
    ]
    original = deepcopy((feedback, ground_truth, media, evaluator._few_shot_examples))
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator.detect_and_build_media_blocks",
        lambda **kwargs: (media, {"output": "image"}),
    )
    messages = evaluate_messages(
        judge, few_shots=feedback, ground_truth_blocks=ground_truth
    )
    assert messages[0]["content"].startswith(evaluator.system_prompt)
    assert messages[1:4] == [
        {"role": "user", "content": "static input"},
        {"role": "assistant", "content": "static answer"},
        {"role": "user", "content": "Editor current case"},
    ]
    content = messages[-1]["content"]
    assert content[:3] == ground_truth + feedback
    assert "Evaluate current case" in content[3]["text"]
    assert content[4:] == media
    assert (feedback, ground_truth, media, evaluator._few_shot_examples) == original


def test_feedback_is_replaced_and_not_retained_between_runs(judge):
    original = [{"type": "text", "text": "old correction"}]
    replacement = [{"type": "text", "text": "new correction"}]
    first = evaluate_messages(judge, few_shots=original)
    second = evaluate_messages(judge, few_shots=replacement)
    deleted = evaluate_messages(judge, few_shots=[])
    assert first[-1]["content"][0] == original[0]
    assert second[-1]["content"][0] == replacement[0]
    assert "old correction" not in str(second)
    assert "correction" not in str(deleted)
    assert isinstance(deleted[-1]["content"], str)
    assert len(original) == len(replacement) == 1


@pytest.mark.parametrize("eval_type_id", ["CustomPromptEvaluator", "AgentEvaluator"])
@pytest.mark.parametrize("dataset_bound", [False, True], ids=["standalone", "dataset"])
def test_legacy_runner_retrieves_with_correct_input_keys(
    monkeypatch, eval_type_id, dataset_bound
):
    from model_hub.views.eval_runner import EvaluationRunner

    feedback = [{"type": "text", "text": "Stored correction"}]
    manager = Mock()
    manager.process_examples.return_value = feedback
    monkeypatch.setattr("model_hub.views.eval_runner.EmbeddingManager", lambda: manager)
    template = SimpleNamespace(
        id="eval-template",
        organization=None,
        config={"eval_type_id": eval_type_id, "required_keys": ["output"]},
    )
    runner = EvaluationRunner(
        user_eval_metric_id=None,
        format_output=True,
        organization_id="owning-organization",
        workspace_id="owning-workspace",
    )
    if dataset_bound:
        runner.user_eval_metric = SimpleNamespace(
            template=template,
            config={"mapping": {"output": "dataset-column-id"}},
        )
    params = runner.map_fields(
        required_field=["output"], mapping=["current case"], eval_template=template
    )
    input_keys = ["dataset-column-id" if dataset_bound else "output"]
    manager.retrieve_avg_rag_based_examples.assert_called_once_with(
        eval_id=template.id,
        inputs=["current case"],
        input_cols=input_keys,
        organization_id="owning-organization",
        workspace_id=None,
    )
    assert manager.process_examples.call_args.kwargs["inputs"] == input_keys
    assert params["few_shots"] == feedback
    assert params["output"] == "current case"
    manager.close.assert_called_once()


@pytest.mark.parametrize(
    "eval_type_id,is_futureagi",
    [
        ("CustomPromptEvaluator", False),
        ("AgentEvaluator", False),
        ("DeterministicEvaluator", True),
    ],
)
def test_shared_params_retrieve_for_feedback_aware_evaluators(
    monkeypatch, eval_type_id, is_futureagi
):
    feedback = [{"type": "text", "text": "Stored correction"}]
    retrieval = Mock(return_value=feedback)
    monkeypatch.setattr("evaluations.engine.params._get_few_shot_examples", retrieval)
    template = SimpleNamespace(
        config={"eval_type_id": eval_type_id, "required_keys": ["output"]},
        criteria="Evaluate {{output}}",
        name="feedback judge",
    )
    inputs = {"output": "current case"}
    params = prepare_run_params(
        inputs,
        template,
        is_futureagi=is_futureagi,
        organization_id="owning-organization",
    )
    assert params["few_shots"] == feedback
    assert inputs == {"output": "current case"}
    assert params["required_keys"] == ["output"]
    assert ("criteria" in params) is is_futureagi
    assert ("eval_name" in params) is is_futureagi
    retrieval.assert_called_once()


def test_shared_params_do_not_inject_feedback_into_code_evaluators(monkeypatch):
    retrieval = Mock()
    monkeypatch.setattr("evaluations.engine.params._get_few_shot_examples", retrieval)
    template = SimpleNamespace(config={"eval_type_id": "CustomCodeEval"})
    assert prepare_run_params({"output": "case"}, template) == {"output": "case"}
    retrieval.assert_not_called()


def test_shared_params_skip_feedback_retrieval_without_organization(monkeypatch):
    manager = Mock()
    monkeypatch.setattr(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager", manager
    )
    template = SimpleNamespace(config={"eval_type_id": "CustomPromptEvaluator"})
    params = prepare_run_params({"output": "case"}, template)
    assert params["few_shots"] == []
    manager.assert_not_called()


def test_shared_params_logs_retrieval_failure_and_continues(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager",
        Mock(side_effect=RuntimeError("embedding service unavailable")),
    )
    monkeypatch.setattr("evaluations.engine.params.logger", logger)
    template = SimpleNamespace(
        id="template-id",
        config={"eval_type_id": "CustomPromptEvaluator"},
    )

    params = prepare_run_params(
        {"output": "case"}, template, organization_id="organization-id"
    )

    assert params["few_shots"] == []
    logger.warning.assert_called_once()

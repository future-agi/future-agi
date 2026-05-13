"""Tests for the Agent Playground evaluation runner."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent_playground.services import evaluation as evaluation_service
from agent_playground.services.engine.runners import evaluation
from evaluations.engine import runner as eval_runner


@dataclass
class FakeEvalResult:
    value: object
    data: dict | None = None
    reason: str | None = None
    failure: str | None = None
    runtime: float | None = None
    model_used: str | None = None
    metrics: list | None = None
    metadata: dict | None = None
    output_type: str = "score"
    duration: float | None = 0.01


@pytest.mark.unit
def test_evaluation_runner_returns_summary_and_passthrough(monkeypatch):
    template = SimpleNamespace(
        id="template-1",
        name="accuracy",
        config={"required_keys": ["output"], "optional_keys": ["context"]},
    )
    captured = {}

    monkeypatch.setattr(
        evaluation_service, "_load_eval_template", lambda spec: template
    )

    def fake_run_eval(request):
        captured["inputs"] = request.inputs
        captured["organization_id"] = request.organization_id
        return FakeEvalResult(value=0.84, reason="good")

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)

    result = evaluation.EvaluationRunner().run(
        {
            "evaluators": [
                {
                    "templateId": "template-1",
                    "name": "accuracy",
                    "config": {"mapping": {"output": "input"}},
                }
            ],
            "threshold": 0.8,
            "fail_action": "continue",
        },
        {"input": "answer", "context": "source doc"},
        {"organization_id": "org-1", "workspace_id": "ws-1"},
    )

    assert captured["inputs"] == {"output": "answer", "context": "source doc"}
    assert captured["organization_id"] == "org-1"
    assert result["evaluation_result"]["passed"] is True
    assert result["evaluation_result"]["score"] == 0.84
    assert result["passthrough"] == "answer"
    assert result["fallback"] is None


@pytest.mark.unit
def test_evaluation_runner_routes_failed_result_to_fallback(monkeypatch):
    template = SimpleNamespace(id="template-1", name="toxicity", config={})
    monkeypatch.setattr(
        evaluation_service, "_load_eval_template", lambda spec: template
    )
    monkeypatch.setattr(
        eval_runner,
        "run_eval",
        lambda request: FakeEvalResult(value=False, reason="unsafe"),
    )

    result = evaluation.EvaluationRunner().run(
        {
            "evaluators": [{"templateId": "template-1"}],
            "threshold": 0.5,
            "fail_action": "route_fallback",
        },
        {"input": "candidate"},
        {},
    )

    assert result["evaluation_result"]["passed"] is False
    assert result["passthrough"] is None
    assert result["fallback"] == "candidate"


@pytest.mark.unit
def test_evaluation_runner_stop_action_fails_node(monkeypatch):
    template = SimpleNamespace(id="template-1", name="faithfulness", config={})
    monkeypatch.setattr(
        evaluation_service, "_load_eval_template", lambda spec: template
    )
    monkeypatch.setattr(
        eval_runner,
        "run_eval",
        lambda request: FakeEvalResult(value=0.2, reason="mismatch"),
    )

    with pytest.raises(ValueError, match="Evaluation failed"):
        evaluation.EvaluationRunner().run(
            {
                "evaluators": [{"templateId": "template-1"}],
                "threshold": 0.5,
                "fail_action": "stop",
            },
            {"input": "candidate"},
            {},
        )


@pytest.mark.unit
def test_agent_evaluation_batch_resolves_mappings_per_evaluator(monkeypatch):
    observed_inputs = []

    monkeypatch.setattr(
        evaluation_service,
        "_execution_output_payloads",
        lambda graph_execution: {
            "node-a.response": "retrieval-grounded answer",
            "node-b.response": "final answer",
        },
    )

    def fake_run_evaluator(spec, inputs, execution_context, threshold):
        observed_inputs.append((spec["name"], inputs))
        return {
            "name": spec["name"],
            "score": 0.9,
            "passed": True,
            "threshold": threshold,
        }

    monkeypatch.setattr(evaluation_service, "_run_evaluator", fake_run_evaluator)

    result = evaluation_service.run_agent_evaluation_batch(
        evaluators=[
            {
                "name": "faithfulness",
                "mapping": {"output": "node-a.response"},
            },
            {
                "name": "answer-quality",
                "mapping": {"output": "node-b.response"},
            },
        ],
        graph_execution=object(),
        execution_context={},
        threshold=0.5,
    )

    assert observed_inputs == [
        ("faithfulness", {"output": "retrieval-grounded answer"}),
        ("answer-quality", {"output": "final answer"}),
    ]
    assert result["passed"] is True
    assert result["score"] == 0.9


@pytest.mark.unit
def test_missing_evaluation_template_raises_contract_error(monkeypatch):
    class FakeEvalTemplate:
        class DoesNotExist(Exception):
            pass

        class no_workspace_objects:
            @staticmethod
            def get(**kwargs):
                raise FakeEvalTemplate.DoesNotExist

    monkeypatch.setattr(
        "model_hub.models.evals_metric.EvalTemplate",
        FakeEvalTemplate,
    )

    with pytest.raises(ValueError, match="Evaluation template not found: missing"):
        evaluation_service._load_eval_template({"templateId": "missing"})

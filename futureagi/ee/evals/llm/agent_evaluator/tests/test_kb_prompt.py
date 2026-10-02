from ee.evals.llm.agent_evaluator.evaluator import _build_eval_system_prompt


class DummyEvaluator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_system_prompt_includes_multiple_kb_ids():
    prompt = _build_eval_system_prompt(
        output_type="Pass/Fail",
        knowledge_base_ids=["kb-one", "kb-two"],
    )

    assert "search_knowledge_base" in prompt
    assert "kb-one" in prompt
    assert "kb-two" in prompt


def test_system_prompt_omits_kb_instruction_when_no_kb_ids():
    prompt = _build_eval_system_prompt(output_type="Pass/Fail")

    assert "search_knowledge_base" not in prompt


def test_playground_kb_id_reaches_agent_evaluator_config():
    from types import SimpleNamespace

    from evaluations.engine.instance import create_eval_instance

    template = SimpleNamespace(
        config={
            "eval_type_id": "AgentEvaluator",
            "rule_prompt": "Check KB",
            "output": "Pass/Fail",
            "agent_mode": "agent",
        },
        choice_scores={},
        choices=[],
        pass_threshold=0.5,
        organization=None,
    )

    instance, _ = create_eval_instance(
        eval_class=DummyEvaluator,
        eval_template=template,
        config={},
        kb_id="playground-kb-id",
    )

    assert instance.kwargs["knowledge_base_id"] == "playground-kb-id"


def test_dataset_runner_passes_user_eval_metric_kb_id_to_eval_instance():
    from types import SimpleNamespace

    import pytest
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.user_eval_metric = SimpleNamespace(
        model="turing_flash", kb_id="dataset-kb-id"
    )
    runner.eval_template = None
    runner._prepare_mapping_data = lambda row, mappings: ([], [])

    def capture_create_eval_instance(**kwargs):
        assert kwargs["kb_id"] == "dataset-kb-id"
        raise RuntimeError("stop after evaluator creation")

    runner._create_eval_instance = capture_create_eval_instance

    with pytest.raises(RuntimeError, match="stop after evaluator creation"):
        runner._run_evaluation(
            row=SimpleNamespace(dataset_id="dataset-id"), mappings={}, config={}
        )


def test_sdk_run_eval_passes_kb_id_to_unified_engine(monkeypatch):
    from types import SimpleNamespace

    from sdk.utils import evaluations

    captured = {}

    def fake_log(*args, **kwargs):
        captured["logged_kb_id"] = kwargs.get("kb_id")
        return SimpleNamespace(config="{}", status="processing", save=lambda: None)

    def fake_run_eval(request):
        captured["request_kb_id"] = request.kb_id
        return SimpleNamespace(
            data={},
            failure=False,
            reason="ok",
            runtime=0,
            model_used="turing_flash",
            metrics=[],
            metadata={},
            output_type="Pass/Fail",
            value="Pass",
            cost={},
        )

    monkeypatch.setattr(
        evaluations, "_log_and_deduct_cost_for_standalone_eval", fake_log
    )
    monkeypatch.setattr("evaluations.engine.run_eval", fake_run_eval)

    template = SimpleNamespace(
        id="template-id",
        name="Template",
        config={"eval_type_id": "AgentEvaluator", "output": "Pass/Fail"},
    )
    user = SimpleNamespace(id="user-id", organization=SimpleNamespace(id="org-id"))
    workspace = SimpleNamespace(id="workspace-id")

    evaluations._run_eval(
        eval_template=template,
        inputs={},
        model="turing_flash",
        user=user,
        workspace=workspace,
        eval_config={"kb_id": "sdk-kb-id"},
    )

    assert captured["logged_kb_id"] == "sdk-kb-id"
    assert captured["request_kb_id"] == "sdk-kb-id"

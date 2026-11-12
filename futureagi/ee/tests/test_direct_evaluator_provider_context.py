"""Optimization inference must use workspace provider configuration.

Exercise the real RunPrompt/LLMHandler path with a database-resolution stand-in
and a mocked provider transport. No live credentials or provider calls are used.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import litellm
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def provider_path(monkeypatch):
    from agentic_eval.core_evals.run_prompt import litellm_response
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
        llm_handler,
    )
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
        ParametersValidator,
    )
    from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    organization = SimpleNamespace(id="fixture-organization")
    workspace = SimpleNamespace(id="fixture-workspace")
    saved_config = {"value": "workspace-fixture-only"}

    def lookup(*, organization_id, workspace_id, provider):
        # Represents a key stored only in this workspace's database record.
        assert organization_id == organization.id
        assert workspace_id == workspace.id
        assert provider == "openai"
        return deepcopy(saved_config["value"])

    manager = Mock()
    manager.get_provider.return_value = "openai"
    manager.get_api_key.side_effect = lookup
    manager_factory = Mock(return_value=manager)
    monkeypatch.setattr(litellm_response, "LiteLLMModelManager", manager_factory)
    monkeypatch.setattr(litellm_response, "USE_NEW_RUNPROMPT_HANDLERS", True)
    monkeypatch.setattr(
        litellm_response.ModelHandlerFactory, "create_handler", llm_handler.LLMHandler
    )
    monkeypatch.setattr(ParametersValidator, "validate_all", Mock(return_value={}))

    def format_response(*, response, **kwargs):
        output = response.choices[0].message.content
        return output, {"data": {"response": output}, "metadata": {}}

    monkeypatch.setattr(
        llm_handler.ResponseFormatter, "format_llm_response", format_response
    )

    async def complete(**kwargs):
        if kwargs.get("api_key") != "workspace-fixture-only":
            raise litellm.AuthenticationError(
                message="Workspace provider authentication refused",
                llm_provider="openai",
                model=kwargs["model"],
            )
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="workspace answer", tool_calls=None)
                )
            ],
        )

    completion = AsyncMock(side_effect=complete)
    # Intercept both the defective direct call and the shared provider path.
    monkeypatch.setattr(litellm, "acompletion", completion)
    monkeypatch.setattr(llm_handler, "guarded_acompletion", completion)
    evaluator = DirectEvaluator(
        user_eval_configs=[SimpleNamespace(name="nonempty")],
        execution_model="gpt-4o",
        organization=organization,
        workspace=workspace,
    )
    score = Mock(side_effect=lambda config, inputs, output, prompt: (float(bool(output)), "nonempty"))
    monkeypatch.setattr(evaluator, "_run_eval_with_config", score)
    return SimpleNamespace(
        evaluator=evaluator,
        completion=completion,
        score=score,
        organization=organization,
        workspace=workspace,
        manager=manager,
        manager_factory=manager_factory,
        saved_config=saved_config,
    )


@pytest.mark.parametrize("endpoint_config", [False, True], ids=["saved-key", "saved-endpoint"])
def test_baseline_and_candidate_use_workspace_provider_config(provider_path, endpoint_config):
    path = provider_path
    if endpoint_config:
        path.saved_config["value"] = {
            "key": "workspace-fixture-only",
            "api_base": "https://workspace-provider.invalid/v1",
        }
    inputs = [
        {"agent_prompt": "Baseline {{question}}", "input": {"question": "hello"}},
        {"agent_prompt": "Candidate {{question}}", "input": {"question": "hello"}},
    ]

    results = path.evaluator.evaluate(inputs)

    assert [result.metadata["output"] for result in results] == ["workspace answer"] * 2
    assert [result.score for result in results] == [1.0, 1.0]
    assert path.manager_factory.call_count == 2
    for call in path.manager_factory.call_args_list:
        assert call.args == ("gpt-4o",)
        assert call.kwargs == {"organization_id": path.organization.id}
    assert path.manager.get_api_key.call_count == 2
    for call in path.manager.get_api_key.call_args_list:
        assert call.kwargs == {
            "organization_id": path.organization.id,
            "workspace_id": path.workspace.id,
            "provider": "openai",
        }
    assert path.completion.await_count == 2
    assert {
        call.kwargs["messages"][0]["content"]
        for call in path.completion.await_args_list
    } == {"Baseline hello", "Candidate hello"}
    for call in path.completion.await_args_list:
        payload = call.kwargs
        assert payload["api_key"] == "workspace-fixture-only"
        assert payload["temperature"] == 0.1
        assert payload["seed"] == 47
        if endpoint_config:
            assert payload["api_base"] == "https://workspace-provider.invalid/v1"
            assert payload["model"] == "openai/gpt-4o"
        else:
            assert payload["model"] == "gpt-4o"
    assert path.score.call_count == 2


@pytest.mark.parametrize("failure", ["authentication", "lookup", "organization"])
def test_inference_failure_is_not_scored(provider_path, failure):
    path = provider_path
    if failure == "authentication":
        path.completion.side_effect = litellm.AuthenticationError(
            message="Workspace provider authentication refused",
            llm_provider="openai",
            model="gpt-4o",
        )
    elif failure == "lookup":
        path.manager.get_api_key.side_effect = ValueError("No workspace provider configured")
    else:
        path.evaluator.organization = None

    with pytest.raises(RuntimeError, match="inference failed"):
        path.evaluator.evaluate([{"agent_prompt": "Answer the question", "input": {}}])

    path.score.assert_not_called()
    if failure != "authentication":
        path.completion.assert_not_awaited()


def test_failed_baseline_stops_optimization_without_persisting_scores(provider_path, monkeypatch):
    from ee.agenthub.fix_your_agent import direct_evaluator, fix_your_agent

    path = provider_path
    path.completion.side_effect = litellm.AuthenticationError(
        message="Workspace provider authentication refused",
        llm_provider="openai",
        model="gpt-4o",
    )
    monkeypatch.setattr(direct_evaluator, "DirectEvaluator", Mock(return_value=path.evaluator))
    monkeypatch.setattr(fix_your_agent, "LiteLLMGenerator", Mock())
    optimizer = Mock()
    optimizer.optimize.side_effect = AssertionError("A failed baseline must stop the run")
    monkeypatch.setattr(fix_your_agent, "RandomSearchOptimizer", Mock(return_value=optimizer))
    monkeypatch.setattr(fix_your_agent, "update_agent_optimiser_run_step", Mock())
    persist_trial = Mock()

    with pytest.raises(RuntimeError, match="inference failed"):
        fix_your_agent.FixYourAgent().optimize(
            initial_agent_prompt="Answer the question",
            scenarios=[{"call_execution_id": "row-1", "input": {}}],
            eval_configs=path.evaluator.user_eval_configs,
            optimizer_type="random_search",
            optimization_model="gpt-4.1",
            api_key="teacher-fixture-only",
            use_direct_evaluation=True,
            execution_model="gpt-4o",
            organization=path.organization,
            workspace=path.workspace,
            on_trial_callback=persist_trial,
        )

    path.score.assert_not_called()
    optimizer.optimize.assert_not_called()
    persist_trial.assert_not_called()

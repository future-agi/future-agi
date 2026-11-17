"""Unit tests for the managed-AI transport fallback in ``AgentEvaluator``.

When an agent eval runs on the *default* client and that client is wired to
the managed Falcon/Turing gateway, the evaluator must route the eval's own
model through its native provider whenever the managed gateway can't be
reached — otherwise the activation client fails with ACTIVATION_FAILED.

``_managed_ai_available()`` answers "is the gateway reachable in this
deployment" via the capability service; ``_provider_for_user_model()`` maps
the user-selected model to a native provider for the fallback path. Both are
static and side-effect free (no DB, no LLM calls) — the capability service is
patched at its module seam.

The client the evaluator picks is also the model the agent runs on. The
evaluator exposes it as ``ran_on_model`` for its callers' stored records, while
its result keeps naming the configured model.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ee.evals.llm.agent_evaluator.evaluator import (
    AgentEvaluator,
    ManagedGatewayRequiredError,
)
from ee.falcon_ai.llm_client import FalconLLMClient
from tfc.licensing.types import DeploymentLocation


# ── _provider_for_user_model() ───────────────────────────────────────────
@pytest.mark.parametrize(
    "model,expected",
    [
        # OpenAI family
        ("gpt-4o", "openai"),
        ("gpt-4.1-mini", "openai"),
        ("GPT-4O", "openai"),  # case-insensitive
        ("o1-preview", "openai"),
        ("o3-mini", "openai"),
        ("o4", "openai"),
        ("chatgpt-4o-latest", "openai"),
        ("openai/gpt-4o", "openai"),  # provider-prefixed
        # Anthropic (direct) family
        ("claude-3-5-sonnet", "anthropic"),
        ("anthropic/claude-3-opus", "anthropic"),
        # Bedrock: full inference-profile ARN, bare cross-region profile ids
        # (us./eu./global.), and the explicit bedrock/ prefix. SigV4 auth →
        # works with zero user setup, so these must never fall through to None.
        (
            "bedrock/arn:aws:bedrock:us-east-1:000000000000:inference-profile/"
            "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
            "bedrock",
        ),
        ("us.anthropic.claude-sonnet-4-6", "bedrock"),
        ("eu.anthropic.claude-sonnet-4-6", "bedrock"),
        ("global.anthropic.claude-sonnet-4-5-20250929", "bedrock"),
        ("bedrock/some-managed-profile", "bedrock"),
        # Vertex / Gemini family
        ("vertex_ai/gemini-1.5-pro", "vertex_ai"),  # explicit vertex prefix
        ("gemini-1.5-flash", "vertex_ai"),  # bare gemini family
        # Genuinely unknown → None (caller keeps its existing default)
        ("mistral-large", None),
        ("", None),
        (None, None),
    ],
)
def test_provider_for_user_model(model, expected):
    assert AgentEvaluator._provider_for_user_model(model) == expected


@pytest.mark.parametrize(
    "model",
    [
        "turing_large",
        "turing_large_xl",
        "turing_small",
        "turing_flash",
        "protect",
        "protect_flash",
        "protect_toxicity",
    ],
)
def test_provider_for_user_model_raises_for_managed_only_models(model):
    # Turing/Protect have no direct provider: returning None would strand the
    # eval on the dead managed path. It must raise a clear, actionable error.
    with pytest.raises(ManagedGatewayRequiredError, match="managed"):
        AgentEvaluator._provider_for_user_model(model)


# ── _managed_ai_available() ──────────────────────────────────────────────
def test_managed_ai_available_true_on_cloud_without_calling_check():
    """Cloud always has the managed gateway; per-org Turing entitlement is
    enforced at model selection, so we must not consult check() here."""
    check = MagicMock()
    with (
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=DeploymentLocation.CLOUD,
        ),
        patch("tfc.capabilities.service.check", check),
    ):
        assert AgentEvaluator._managed_ai_available() is True
    check.assert_not_called()


def test_managed_ai_available_true_when_self_hosted_license_includes_falcon():
    with (
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=DeploymentLocation.SELF_HOSTED,
        ),
        patch(
            "tfc.capabilities.service.check",
            return_value=SimpleNamespace(allowed=True),
        ) as check,
    ):
        assert AgentEvaluator._managed_ai_available() is True
    check.assert_called_once_with("falcon_ai")


def test_managed_ai_available_false_when_self_hosted_denies_falcon():
    """A self-hosted install whose license omits (or has expired for) managed
    compute must route direct — the previous DeploymentMode.is_ee() check
    returned True here and would have hit a gateway that rejects it."""
    with (
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=DeploymentLocation.SELF_HOSTED,
        ),
        patch(
            "tfc.capabilities.service.check",
            return_value=SimpleNamespace(allowed=False),
        ) as check,
    ):
        assert AgentEvaluator._managed_ai_available() is False
    check.assert_called_once_with("falcon_ai")


# ── The model the agent's client ran on ──────────────────────────────────
_VERDICT = '{"result": "Pass", "explanation": "The answer is grounded."}'
_MULTIMODAL_MODEL = "stub-multimodal-model"


def _run_agent_choosing_its_client(evaluator, llm_override, location):
    """Run ``_run_agent`` with the evaluator choosing and building its client.

    The loop starts on the default client ``AgentLoop.__init__`` builds. Only
    the loop's run, the tool registry, the tool context and the deployment
    location are stubbed. Returns the agent result and the client it ran on.
    """
    agent = MagicMock()
    agent.llm_client = FalconLLMClient()
    agent.run = AsyncMock(
        return_value={
            "content": _VERDICT,
            "tool_calls": [],
            "input_tokens": 120,
            "output_tokens": 30,
        }
    )
    with (
        patch("ee.falcon_ai.agent.AgentLoop", return_value=agent),
        patch("ai_tools.registry.registry"),
        patch.object(evaluator, "_build_tool_context", return_value=MagicMock()),
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=location,
        ),
        patch(
            "tfc.capabilities.service.check",
            return_value=SimpleNamespace(allowed=False),
        ),
    ):
        result = evaluator._run_agent(
            "Judge whether the answer is grounded.", llm_override=llm_override
        )
    return result, agent.llm_client


@pytest.mark.parametrize(
    "model,media,location,ran_on",
    [
        ("gpt-4o", None, DeploymentLocation.CLOUD, "falcon_ai"),
        ("turing_small", None, DeploymentLocation.CLOUD, "turing_small"),
        ("turing_small", "pdf", DeploymentLocation.CLOUD, "turing_large_xl"),
        ("gpt-4o", "audio", DeploymentLocation.CLOUD, _MULTIMODAL_MODEL),
        ("gpt-4o", None, DeploymentLocation.SELF_HOSTED, "gpt-4o"),
    ],
    ids=[
        "managed-default-in-cloud",
        "turing-alias",
        "turing-with-pdf",
        "other-model-with-audio",
        "self-hosted-direct",
    ],
)
def test_the_evaluator_exposes_the_model_its_client_ran_on(
    model, media, location, ran_on, monkeypatch
):
    """In cloud a configured gpt-4o runs on the managed model falcon_ai and a
    Turing alias on itself. Audio or PDF moves a Turing model to
    turing_large_xl and any other model to the multimodal setting's model. A
    self-hosted install without managed AI runs the configured model."""
    monkeypatch.setenv("FALCON_MULTIMODAL_PROVIDER", "openai")
    monkeypatch.setenv("FALCON_MULTIMODAL_MODEL", _MULTIMODAL_MODEL)
    evaluator = AgentEvaluator(
        rule_prompt="Is the answer grounded?", model=model, output_type="Pass/Fail"
    )
    evaluator._current_eval_id = "eval-1"
    # The client override _evaluate hands to _run_agent for this input.
    llm_override = (
        evaluator._build_llm_override(evaluator._model_cfg)
        if evaluator._is_turing
        else None
    )
    if media:
        llm_override = evaluator._resolve_multimodal_override(media)

    agent_result, client = _run_agent_choosing_its_client(
        evaluator, llm_override, location
    )
    eval_result = evaluator._build_result(agent_result, runtime_ms=5)

    assert client.model == ran_on
    assert getattr(evaluator, "ran_on_model", None) == ran_on
    # The result and its metadata name the configured model, as before.
    assert eval_result["model"] == model
    metadata = json.loads(eval_result["metadata"])
    assert set(metadata) == {
        "usage",
        "cost",
        "response_time",
        "explanation",
        "agent_metadata",
    }
    assert metadata["agent_metadata"]["model_used"] == model
    assert set(metadata["agent_metadata"]) == {
        "model_used",
        "mode",
        "iterations",
        "tool_calls_count",
        "tools_used",
    }
    assert "ran_on_model" not in json.dumps(eval_result)
    if ran_on != model:
        assert ran_on not in json.dumps(eval_result)


def test_a_protect_run_after_an_agent_run_exposes_no_model_its_client_ran_on():
    """An evaluator that ran an agent evaluation can run again. A Protect
    evaluation makes a single gateway call and builds no agent client, so after
    it the evaluator exposes no model rather than the earlier run's model."""
    evaluator = AgentEvaluator(
        rule_prompt="Is the answer grounded?", model="gpt-4o", output_type="Pass/Fail"
    )
    evaluator._current_eval_id = "eval-1"
    _run_agent_choosing_its_client(evaluator, None, DeploymentLocation.CLOUD)
    assert evaluator.ran_on_model == "falcon_ai"

    gateway = MagicMock()
    gateway._try_gateway_completion.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="<label>Passed</label>"
                    "<explanation>The text is polite.</explanation>"
                )
            )
        ]
    )
    with patch("agentic_eval.core.llm.llm.LLM", return_value=gateway):
        batch = evaluator.run(
            call_type="protect", eval_name="toxicity", input="Have a nice day."
        )

    assert batch.eval_results[0]["output"] == "Passed"
    assert evaluator.ran_on_model is None

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
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ee.evals.llm.agent_evaluator.evaluator import (
    AgentEvaluator,
    ManagedGatewayRequiredError,
)
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

"""Managed-route mode of the generic LLM wrapper (census R3-10) and the
call sites that build one: the CustomPromptEvaluator judge (R3-07) and the
AI eval writer, smart filters and criteria helper (R3-16).

RED on base 651ab5bb: there is no managed-route mode. LLM has no
``model_route`` argument, a gateway failure returns None and the call
continues on litellm with direct provider or deployment credentials
(agentic_eval/core/llm/llm.py:790-816, 2278-2285), and auxiliary callers
carry no organization. RED on the prior OK-A bytes: an LLM bound one
admitted route at construction and reused its admission and request id for
every call; a managed LLM still built provider SDK
clients from the deployment's keys (OKA-C4); the judge was admitted for the
template's model before version overrides (OKA-C3); the criteria helper
carried no organization (OKA-C6). ``TestNoRouteUnchanged`` pins today's
fallback for calls without a route and passes on base too.

``TestEvalCallSites`` and ``TestAuxiliaryCallSites`` exercise compose hunks
CH-08, CH-09, CH-25, CH-26 (evaluations/engine/instance.py), CH-10 (the
evaluator), CH-12 (eval_runner.py), CH-13..CH-15 (AI eval writer) and CH-21,
CH-22, CH-32 (tasks/agent.py); they are RED until those hunks are applied.
OK-COMPOSE r2: a managed judge is bound only for a caller that bills its
evaluation once (``managed_judge_billing=MANAGED_JUDGE_SINGLE_METER``); every
other caller gets the visible cut refusal (RED on the r1 bytes, which have no
such argument). The ``mr`` fixture pins a deployment without the legacy
combined billing, so the standalone judge meter is in play.

Doubles: the gateway port (MockGatewayManagedRouting), the gateway client
transport (FakeGatewayClient), the ClickHouse usage log, the template
version lookup and the evaluator class being built. Organizations,
workspaces and provider credentials are real rows; litellm and the OpenAI
SDK are tripwires.
"""

import asyncio
import importlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs

# Imported here, before any tripwire is set, so nothing their import does is
# taken for a managed call.
from evaluations.engine import instance as eval_instance
from model_hub.models.api_key import ApiKey
from model_hub.services.ai_eval_writer_service import generate_eval_prompt
from model_hub.tasks import agent as agent_tasks
from model_hub.tests.test_model_route_support import (
    ANTHROPIC_CHAT_MODEL,
    BYOK_FIXTURE_VALUE,
    MESSAGES,
    OPENAI_CHAT_MODEL,
    PRIVATE_UPSTREAM_TEXT,
    FakeGatewayClient,
    GatewayStatusError,
    chat_response,
    eligible_port,
    gateway_refusal,
    provider_tripwires,
    sent_request_ids,
)
from model_hub.views.eval_runner import EvaluationRunner

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def mr(monkeypatch):
    module = importlib.import_module("model_hub.services.model_route")
    from decimal import Decimal
    from model_hub.services import credit_ledger_port as ledger
    ledger.reset_meter_safety()
    # No legacy combined billing debits these judge and helper calls; the
    # legacy-billing cut is covered by test_one_meter_compose_r2.py.
    monkeypatch.setattr(ledger, "legacy_billing_installed", lambda: False)
    with module.managed_routing_port(None), ledger.use_credit_ledger(
        ledger.MockCreditLedger(default_balance=Decimal("100"))
    ):
        yield module
    ledger.reset_meter_safety()


@pytest.fixture(autouse=True)
def _no_clickhouse_usage_log():
    with patch("agentic_eval.core.llm.llm.log_to_clickhouse"):
        yield


@pytest.fixture
def direct():
    """Every direct (non-gateway) completion path of the wrapper."""
    with (
        patch("agentic_eval.core.llm.llm.litellm") as litellm_double,
        patch("agentic_eval.core.llm.llm.guarded_completion") as guarded,
        patch("agentic_eval.core.llm.llm.time.sleep"),
    ):
        yield SimpleNamespace(litellm=litellm_double, guarded=guarded)


@pytest.fixture
def tripped(monkeypatch):
    return provider_tripwires(monkeypatch)


def _binding(
    mr,
    organization,
    workspace,
    *,
    client=None,
    async_client=None,
    model=OPENAI_CHAT_MODEL,
    operation=None,
    **kwargs,
):
    port = eligible_port(
        mr,
        organization,
        {model},
        client=client or FakeGatewayClient(),
        async_client=async_client or FakeGatewayClient(is_async=True),
        **kwargs,
    )
    with mr.managed_routing_port(port):
        binding = mr.resolve_model_route(
            organization=organization,
            workspace=workspace,
            model_name=model,
            operation=operation or mr.OP_CUSTOM_PROMPT_EVAL,
        )
    return port, binding


def _managed_llm(binding, **kwargs):
    """A managed LLM, built without patching _init_client (OKA-C4)."""
    return LLM(
        provider="openai",
        model_name=binding.model_name,
        temperature=0.0,
        max_tokens=64,
        model_route=binding,
        **kwargs,
    )


def _plain_llm(**kwargs):
    with patch("agentic_eval.core.llm.llm.LLM._init_client"):
        return LLM(
            provider="openai",
            model_name=OPENAI_CHAT_MODEL,
            temperature=0.0,
            max_tokens=64,
            **kwargs,
        )


def _identity(organization, workspace, request_id):
    return {
        "X-Gateway-Route": "mock-route-1",
        "X-Org-Id": str(organization.id),
        "X-Workspace-Id": str(workspace.id),
        "x-agentcc-request-id": request_id,
    }


class TestManagedRouteCompletion:
    def test_goes_through_the_gateway_with_identity(
        self, mr, organization, workspace, direct, tripped
    ):
        client = FakeGatewayClient(response=chat_response("managed ok"))
        port, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        assert llm._get_completion_content(messages=MESSAGES) == "managed ok"

        direct.litellm.completion.assert_not_called()
        direct.guarded.assert_not_called()
        assert len(client.calls) == 1
        sent = client.calls[0]["kwargs"]
        assert sent["model"] == OPENAI_CHAT_MODEL
        assert "api_key" not in sent
        assert sent["extra_headers"] == _identity(
            organization, workspace, port.authorize_calls[0]["request_id"]
        )
        assert llm.token_usage["total_tokens"] == 3
        assert llm.org_id == str(organization.id)
        assert llm.workspace_id == str(workspace.id)
        assert tripped == []

    def test_gateway_failure_is_final(self, mr, organization, workspace, direct):
        client = FakeGatewayClient(error=RuntimeError(PRIVATE_UPSTREAM_TEXT))
        _, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        with patch.object(LLM, "_handle_final_fallback") as final_fallback:
            with pytest.raises(mr.ManagedRouteCallFailed) as failed:
                llm._get_completion_content(messages=MESSAGES)

        assert PRIVATE_UPSTREAM_TEXT not in str(failed.value)
        assert len(client.calls) == 1
        final_fallback.assert_not_called()
        direct.litellm.completion.assert_not_called()
        direct.guarded.assert_not_called()

    def test_credit_refusal_from_the_gateway_is_typed(
        self, mr, organization, workspace, direct
    ):
        client = FakeGatewayClient(
            error=gateway_refusal(402, "managed_credits_exhausted")
        )
        _, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        with pytest.raises(mr.InsufficientCreditError) as refused:
            llm._get_completion_content(messages=MESSAGES)

        assert refused.value.code == "managed_credits_exhausted"
        assert refused.value.retryable is False
        assert PRIVATE_UPSTREAM_TEXT not in str(refused.value)
        direct.litellm.completion.assert_not_called()

    def test_model_override_is_refused_before_admission(
        self, mr, organization, workspace, direct
    ):
        client = FakeGatewayClient()
        port, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        with pytest.raises(mr.ManagedRouteDenied) as refused:
            llm._get_completion_content(messages=MESSAGES, model="gpt-4o")

        assert refused.value.code == "MANAGED_ROUTE_MODEL_MISMATCH"
        assert client.calls == []
        assert port.authorize_calls == []
        direct.litellm.completion.assert_not_called()


class TestPerCallAdmission:
    """One admission and one x-agentcc-request-id per
    completion; admitted at dispatch, never at construction."""

    def test_two_tool_completions_are_two_admissions(
        self, mr, organization, workspace, direct, tripped
    ):
        client = FakeGatewayClient(response=chat_response("tools ok"))
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        with mr.managed_routing_port(port):
            llm = mr.platform_llm(
                organization=organization,
                workspace=workspace,
                operation=mr.OP_SMART_FILTER,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
                temperature=0.0,
                max_tokens=10,
            )
        assert port.authorize_calls == []

        llm._get_completion_with_tools(MESSAGES, [], timeout_ms=5000)
        llm._get_completion_with_tools(MESSAGES, [], timeout_ms=5000)

        admitted = [call["request_id"] for call in port.authorize_calls]
        assert len(admitted) == 2
        assert len(set(admitted)) == 2
        assert sent_request_ids(client) == admitted
        assert {call["operation"] for call in port.authorize_calls} == {
            mr.OP_SMART_FILTER
        }
        assert tripped == []

    def test_two_judge_calls_are_two_admissions(
        self, mr, organization, workspace, direct
    ):
        client = FakeGatewayClient(response=chat_response("ok"))
        port, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        llm.call_llm(prompt=MESSAGES, provider="openai")
        llm.call_llm(prompt=MESSAGES, provider="openai")

        admitted = [call["request_id"] for call in port.authorize_calls]
        assert len(set(admitted)) == 2
        assert sent_request_ids(client) == admitted

    def test_async_calls_are_two_admissions(self, mr, organization, workspace, direct):
        async_client = FakeGatewayClient(response=chat_response("a"), is_async=True)
        port, binding = _binding(mr, organization, workspace, async_client=async_client)
        llm = _managed_llm(binding)

        async def run():
            await llm._get_completion_content_async(messages=MESSAGES)
            await llm._get_completion_content_async(messages=MESSAGES)

        asyncio.run(run())

        admitted = [call["request_id"] for call in port.authorize_calls]
        assert len(set(admitted)) == 2
        assert sent_request_ids(async_client) == admitted


class TestManagedRouteCallLlm:
    def test_success_forwards_response_format(
        self, mr, organization, workspace, direct
    ):
        client = FakeGatewayClient(response=chat_response('{"ok": true}'))
        port, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        content = llm.call_llm(
            prompt=MESSAGES,
            provider="openai",
            response_format={"type": "json_object"},
        )

        assert content == '{"ok": true}'
        sent = client.calls[0]["kwargs"]
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["extra_headers"] == _identity(
            organization, workspace, port.authorize_calls[0]["request_id"]
        )
        direct.litellm.completion.assert_not_called()
        direct.guarded.assert_not_called()

    def test_failure_has_no_fallback(self, mr, organization, workspace, direct):
        client = FakeGatewayClient(error=GatewayStatusError(503, {}))
        _, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        with pytest.raises(mr.ManagedRouteCallFailed) as failed:
            llm.call_llm(prompt=MESSAGES, provider="openai")

        # The unknown call retains its hold for reconciliation and the failure is
        # never offered as an automatic retry.
        assert failed.value.dispatched is None
        assert failed.value.retryable is False
        assert failed.value.retry_after is None
        assert "provider's usage record" in str(failed.value)
        assert str(failed.value) == failed.value.response_body()["message"]
        assert failed.value.__context__ is None
        assert failed.value.__cause__ is None
        assert len(client.calls) == 1
        direct.litellm.completion.assert_not_called()
        direct.guarded.assert_not_called()


class TestManagedRouteToolsAndAsync:
    def test_bounded_tools_completion_uses_the_gateway_only(
        self, mr, organization, workspace, direct
    ):
        client = FakeGatewayClient(response=chat_response("tools ok"))
        port, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        response = llm._get_completion_with_tools(MESSAGES, [], timeout_ms=5000)

        assert response.choices[0].message.content == "tools ok"
        assert client.calls[0]["kwargs"]["extra_headers"] == _identity(
            organization, workspace, port.authorize_calls[0]["request_id"]
        )
        # The caller's wall bounds the one gateway call; no SDK retries.
        bound = client.calls[0]["options"][-1]
        assert bound["max_retries"] == 0
        assert 0 < bound["timeout"] <= 5
        direct.litellm.completion.assert_not_called()

    def test_tools_failure_is_final(self, mr, organization, workspace, direct):
        client = FakeGatewayClient(error=RuntimeError("down"))
        _, binding = _binding(mr, organization, workspace, client=client)
        llm = _managed_llm(binding)

        with pytest.raises(mr.ManagedRouteCallFailed):
            llm._get_completion_with_tools(MESSAGES, [], timeout_ms=5000)

        assert len(client.calls) == 1
        direct.litellm.completion.assert_not_called()

    def test_async_success_and_identity(self, mr, organization, workspace, direct):
        async_client = FakeGatewayClient(
            response=chat_response("async ok"), is_async=True
        )
        port, binding = _binding(mr, organization, workspace, async_client=async_client)
        llm = _managed_llm(binding)

        content = asyncio.run(llm._get_completion_content_async(messages=MESSAGES))

        assert content == "async ok"
        assert async_client.calls[0]["kwargs"]["extra_headers"] == _identity(
            organization, workspace, port.authorize_calls[0]["request_id"]
        )
        direct.litellm.acompletion.assert_not_called()

    def test_async_failure_is_final(self, mr, organization, workspace, direct):
        async_client = FakeGatewayClient(error=RuntimeError("down"), is_async=True)
        _, binding = _binding(mr, organization, workspace, async_client=async_client)
        llm = _managed_llm(binding)

        with pytest.raises(mr.ManagedRouteCallFailed):
            asyncio.run(llm._get_completion_content_async(messages=MESSAGES))

        assert len(async_client.calls) == 1
        direct.litellm.acompletion.assert_not_called()


class TestManagedRouteBinding:
    def test_a_provider_key_with_a_managed_route_is_refused(
        self, mr, organization, workspace
    ):
        _, binding = _binding(mr, organization, workspace)
        with pytest.raises(ValueError):
            _managed_llm(binding, api_key=BYOK_FIXTURE_VALUE)

    def test_a_route_of_another_organization_is_refused(
        self, mr, organization, workspace
    ):
        _, binding = _binding(mr, organization, workspace)
        with pytest.raises(ValueError):
            _managed_llm(binding, org_id="another-organization")

    def test_an_admitted_route_is_refused(self, mr, organization, workspace):
        port, binding = _binding(mr, organization, workspace)
        route = mr.admit_managed_request(binding)
        with pytest.raises(ValueError):
            _managed_llm(route)


class TestManagedLlmBuildsNoProviderClient:
    """OKA-C4: a managed LLM takes its client from the gateway port per call;
    it never builds a provider SDK client or reads deployment keys."""

    @pytest.mark.parametrize("provider", ["openai", "vertex_ai", "xai", "openrouter"])
    def test_constructs_without_provider_clients(
        self, mr, organization, workspace, monkeypatch, tripped, provider
    ):
        for name in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
            monkeypatch.delenv(name, raising=False)
        _, binding = _binding(mr, organization, workspace)

        llm = LLM(provider=provider, model_name=OPENAI_CHAT_MODEL, model_route=binding)

        assert llm.llm_client is None
        assert llm.llm_client_async is None
        assert llm._gateway_client is None
        assert llm._async_gateway_client is None
        assert tripped == []


class TestPlatformLlm:
    def test_not_eligible_keeps_the_transport_and_adds_identity(
        self, mr, organization, workspace
    ):
        with patch("agentic_eval.core.llm.llm.LLM._init_client"):
            llm = mr.platform_llm(
                organization=organization,
                workspace=workspace,
                operation=mr.OP_AI_EVAL_WRITER,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
                temperature=0.3,
                max_tokens=10,
            )
        assert llm.model_route is None
        assert llm.org_id == str(organization.id)
        assert llm.workspace_id == str(workspace.id)
        assert llm.api_key is None

    def test_eligible_is_managed_mode_with_nothing_admitted(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            llm = mr.platform_llm(
                organization=organization,
                workspace=workspace,
                operation=mr.OP_SMART_FILTER,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
                temperature=0.0,
                max_tokens=10,
            )
        assert mr.is_managed_route_binding(llm.model_route)
        assert llm.model_route.operation == mr.OP_SMART_FILTER
        assert llm.org_id == str(organization.id)
        assert port.authorize_calls == []

    def test_eligibility_failure_refuses_instead_of_falling_back(
        self, mr, organization, workspace
    ):
        port = eligible_port(
            mr,
            organization,
            {OPENAI_CHAT_MODEL},
            eligibility_error=RuntimeError("gateway down"),
        )
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ManagedRouteDenied):
                mr.platform_llm(
                    organization=organization,
                    workspace=workspace,
                    operation=mr.OP_CRITERIA_HELPER,
                    provider="openai",
                    model_name=OPENAI_CHAT_MODEL,
                )

    def test_no_organization_is_the_legacy_constructor(self, mr):
        with patch("agentic_eval.core.llm.llm.LLM._init_client"):
            llm = mr.platform_llm(
                organization=None,
                operation=mr.OP_CRITERIA_HELPER,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
            )
        assert llm.org_id is None
        assert llm.model_route is None


class _RecordingEvaluator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _judge_template(organization, workspace, model):
    return SimpleNamespace(
        id="one-key-judge",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": model,
            "rule_prompt": "Is the answer polite?",
            "output": "Pass/Fail",
        },
        choice_scores=None,
        choices=None,
        multi_choice=False,
        pass_threshold=0.5,
        organization=organization,
        workspace=workspace,
        owner="user",
    )


def _build_judge(
    organization,
    workspace,
    *,
    template_model,
    version_model,
    config=None,
    managed_judge_billing=None,
):
    resolved = SimpleNamespace(
        model=version_model, prompt_messages=[], pass_threshold=None
    )
    with patch.object(eval_instance, "resolve_version", return_value=resolved):
        evaluator, _ = eval_instance.create_eval_instance(
            eval_class=_RecordingEvaluator,
            eval_template=_judge_template(organization, workspace, template_model),
            config=dict(config or {}),
            model=None,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            managed_judge_billing=managed_judge_billing,
        )
    return evaluator.kwargs


def _store_byok(organization, workspace, user, provider):
    return ApiKey.no_workspace_objects.create(
        provider=provider,
        organization=organization,
        workspace=workspace,
        key=BYOK_FIXTURE_VALUE,
        user=user,
    )


class TestEvalCallSites:
    """R3-07: CustomPromptEvaluator judge (CH-08, CH-09, CH-25, CH-26
    instance.py; CH-12 eval_runner.py). RED until those hunks are applied."""

    def test_judge_is_bound_to_the_model_after_version_overrides(
        self, mr, organization, workspace
    ):
        port = eligible_port(
            mr, organization, {ANTHROPIC_CHAT_MODEL, OPENAI_CHAT_MODEL}
        )
        with mr.managed_routing_port(port):
            kwargs = _build_judge(
                organization,
                workspace,
                template_model=ANTHROPIC_CHAT_MODEL,
                version_model=OPENAI_CHAT_MODEL,
                managed_judge_billing=eval_instance.MANAGED_JUDGE_SINGLE_METER,
            )
        assert kwargs["model"] == OPENAI_CHAT_MODEL
        assert kwargs["api_key"] is None
        binding = kwargs["model_route"]
        assert mr.is_managed_route_binding(binding)
        assert binding.model_name == OPENAI_CHAT_MODEL
        assert binding.operation == mr.OP_CUSTOM_PROMPT_EVAL
        assert binding.organization_id == str(organization.id)
        # A binding is not an admission: the judge admits each call.
        assert port.authorize_calls == []

    def test_a_caller_without_single_meter_billing_gets_the_cut_line(
        self, mr, organization, workspace
    ):
        from model_hub.services import credit_ledger_port as ledger

        port = eligible_port(
            mr, organization, {ANTHROPIC_CHAT_MODEL, OPENAI_CHAT_MODEL}
        )
        with mr.managed_routing_port(port):
            with pytest.raises(ledger.ManagedDispatchUnavailableError) as refused:
                _build_judge(
                    organization,
                    workspace,
                    template_model=ANTHROPIC_CHAT_MODEL,
                    version_model=OPENAI_CHAT_MODEL,
                )
        assert str(refused.value) == ledger.MANAGED_JUDGE_CUT_MESSAGE
        assert port.authorize_calls == []

    def test_a_stored_key_for_the_final_model_wins(
        self, mr, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(
            mr, organization, {ANTHROPIC_CHAT_MODEL, OPENAI_CHAT_MODEL}
        )
        with mr.managed_routing_port(port):
            kwargs = _build_judge(
                organization,
                workspace,
                template_model=ANTHROPIC_CHAT_MODEL,
                version_model=OPENAI_CHAT_MODEL,
            )
        assert kwargs["model"] == OPENAI_CHAT_MODEL
        assert kwargs["api_key"] == BYOK_FIXTURE_VALUE
        assert "model_route" not in kwargs
        assert port.authorize_calls == []

    def test_byok_judge_drops_a_stale_route(self, mr, organization, workspace, user):
        _store_byok(organization, workspace, user, "anthropic")
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            kwargs = _build_judge(
                organization,
                workspace,
                template_model=ANTHROPIC_CHAT_MODEL,
                version_model=None,
                config={"model_route": "stale-route-from-a-reused-config"},
            )
        assert kwargs["api_key"] == BYOK_FIXTURE_VALUE
        assert "model_route" not in kwargs
        assert port.eligibility_calls == []

    def test_eval_runner_config_prep_admits_nothing(self, mr, organization, workspace):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            api_key = EvaluationRunner._get_api_key(
                None, ANTHROPIC_CHAT_MODEL, organization.id, workspace.id
            )
        assert api_key is None
        assert port.authorize_calls == []


class TestAuxiliaryCallSites:
    """R3-16: criteria helper (CH-21, CH-22, CH-32 tasks/agent.py) and AI
    eval writer (CH-13..CH-15). RED until those hunks are applied."""

    @pytest.fixture(autouse=True)
    def deterministic_managed_models(self, monkeypatch):
        # ModelConfigs is frozen and imported already; environment mutation is too late.
        for name in ("CLAUDE_4_5_SONNET_BEDROCK_ARN", "HAIKU_4_5_BEDROCK_ARN"):
            monkeypatch.setattr(ModelConfigs, name, replace(
                getattr(ModelConfigs, name), model_name=ANTHROPIC_CHAT_MODEL
            ))

    def _create_criteria(self, mr, port, organization, workspace):
        metric = SimpleNamespace(
            text_prompt="one-key criteria prompt",
            model=SimpleNamespace(
                organization_id=organization.id, workspace_id=workspace.id
            ),
            save=lambda: None,
        )
        llm_class = MagicMock(name="LLM")
        with (
            mr.managed_routing_port(port),
            patch.object(agent_tasks, "LLM", llm_class),
            patch.object(
                agent_tasks,
                "get_qualitative_eval_parameter_prompt_v2",
                side_effect=RuntimeError("stop after the LLM is built"),
            ),
        ):
            with pytest.raises(Exception):  # noqa: B017 - the helper re-raises generically
                agent_tasks.create_criteria(metric)
        return llm_class.call_args.kwargs

    def test_criteria_helper_runs_managed_for_the_metrics_organization(
        self, mr, organization, workspace
    ):
        cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        port = eligible_port(mr, organization, {cfg.model_name})
        kwargs = self._create_criteria(mr, port, organization, workspace)
        binding = kwargs["model_route"]
        assert binding.organization_id == str(organization.id)
        assert binding.workspace_id == str(workspace.id)
        assert binding.operation == mr.OP_CRITERIA_HELPER
        assert port.authorize_calls == []

    def test_criteria_helper_not_eligible_still_carries_identity(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, set())
        kwargs = self._create_criteria(mr, port, organization, workspace)
        assert kwargs["org_id"] == str(organization.id)
        assert kwargs["workspace_id"] == str(workspace.id)
        assert "model_route" not in kwargs

    def test_ai_eval_writer_runs_on_the_gateway_with_identity(
        self, mr, organization, workspace, tripped
    ):
        haiku = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
        client = FakeGatewayClient(
            response=chat_response("Judge whether the answer is polite.")
        )
        port = eligible_port(mr, organization, {haiku.model_name}, client=client)
        with mr.managed_routing_port(port):
            result = generate_eval_prompt(
                description="polite answers",
                output_format="prompt",
                organization=organization,
                workspace=workspace,
            )

        assert result == {"prompt": "Judge whether the answer is polite."}
        sent = client.calls[0]["kwargs"]
        assert sent["model"] == haiku.model_name
        assert sent["extra_headers"]["X-Org-Id"] == str(organization.id)
        assert sent["extra_headers"]["X-Workspace-Id"] == str(workspace.id)
        assert port.authorize_calls[0]["operation"] == mr.OP_AI_EVAL_WRITER
        assert tripped == []


class TestNoRouteUnchanged:
    """Regression: without a route the wrapper keeps today's gateway-then-
    litellm behaviour (GREEN on base)."""

    @pytest.fixture(autouse=True)
    def licensed(self, settings):
        settings.EE_LICENSE_KEY = "fixture-license"

    def test_gateway_failure_still_falls_back_to_litellm(self, direct):
        direct.litellm.completion.return_value = chat_response("direct ok")
        llm = _plain_llm()
        llm._gateway_client = FakeGatewayClient(error=RuntimeError("down"))

        assert llm._get_completion_content(messages=MESSAGES) == "direct ok"
        direct.litellm.completion.assert_called_once()

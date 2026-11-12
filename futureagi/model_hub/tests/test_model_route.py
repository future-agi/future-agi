"""One-key routing adapter: the managed branch of
LiteLLMModelManager.get_api_key, per-call managed dispatch, the real
refusal contract (ONEKEY-REAL-REFUSAL-CONTRACT-r1) and the dataset Run
Prompt call sites.

RED on base 651ab5bb: ``model_hub.services.model_route`` does not exist and
get_api_key has no managed branch, so an organization with only a Future AGI
key gets "API key not configured" for every model (census R3-01). RED on the
prior OK-A bytes: a managed credential was admitted when it was resolved and
handed to litellm as a client, which litellm can drop and then read the
platform's own provider key; one admission and one request id
covered every call (OKA-C1); refusals used the assumed INSUFFICIENT_CREDIT
shape instead of the real gateway codes. ``TestByokUnchanged`` and the
Run Prompt BYOK twin pin today's BYOK behaviour and pass on base too.

``TestRunPromptManagedCallSites`` exercises the compose hunks (CH-01..CH-07,
CH-23/CH-24 litellm_response.py, CH-27..CH-29 model_endpoints.py, CH-30/CH-31
llm_handler.py); it is RED until they are applied.

Doubles: only the gateway port (MockGatewayManagedRouting), the gateway
client transport (FakeGatewayClient) and the WebSocket manager. Provider
credentials are real ApiKey rows; organizations and workspaces are real
rows; litellm and the OpenAI SDK are tripwires, never doubles.
"""

import asyncio
import importlib
import json
import sys
import threading
import types
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

# Imported here, before any tripwire is set, so nothing their import does is
# taken for a managed call.
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
    LLMHandler,
)
from model_hub.models.api_key import ApiKey
from model_hub.tests.test_model_route_support import (
    ANTHROPIC_CHAT_MODEL,
    BYOK_FIXTURE_VALUE,
    LEGACY_REFUSAL,
    MESSAGES,
    OPENAI_CHAT_MODEL,
    PLATFORM_KEY_SENTINEL,
    PRIVATE_UPSTREAM_TEXT,
    TTS_MODEL,
    FakeGatewayClient,
    GatewayStatusError,
    RecordingWsManager,
    chat_response,
    eligible_port,
    exception_chain,
    gateway_refusal,
    provider_tripwires,
    sent_request_ids,
    stream_chunk,
)
from model_hub.utils.model_endpoints import guarded_image_generation

pytestmark = pytest.mark.django_db(transaction=True)

REQUEST = {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}


@pytest.fixture
def mr():
    """The adapter; absent on base (ModuleNotFoundError makes every test that
    uses it RED). The default port is restored around each test."""
    module = importlib.import_module("model_hub.services.model_route")
    from decimal import Decimal
    from model_hub.services import credit_ledger_port as ledger
    ledger.reset_meter_safety()
    with module.managed_routing_port(None), ledger.use_credit_ledger(
        ledger.MockCreditLedger(default_balance=Decimal("100"))
    ):
        yield module
    ledger.reset_meter_safety()


@pytest.fixture
def tripped(monkeypatch):
    return provider_tripwires(monkeypatch)


@pytest.fixture
def other_workspace(user):
    other_org = Organization.objects.create(name="One-key other organization")
    return Workspace.objects.create(
        name="One-key other workspace",
        organization=other_org,
        is_default=True,
        created_by=user,
    )


def _store_byok(organization, workspace, user, provider):
    return ApiKey.no_workspace_objects.create(
        provider=provider,
        organization=organization,
        workspace=workspace,
        key=BYOK_FIXTURE_VALUE,
        user=user,
    )


def _manager(model, organization):
    return LiteLLMModelManager(model, organization_id=organization.id)


def _managed_binding(mr, port, organization, workspace, model=ANTHROPIC_CHAT_MODEL):
    with mr.managed_routing_port(port):
        return _manager(model, organization).get_api_key(
            organization_id=organization.id,
            workspace_id=workspace.id,
            provider=LiteLLMModelManager(model).get_provider(model),
            allow_managed=True,
        )


def _binding(mr, port, organization, workspace, model=OPENAI_CHAT_MODEL):
    with mr.managed_routing_port(port):
        return mr.bind_managed_route(
            organization_id=organization.id,
            workspace_id=workspace.id,
            model_name=model,
            provider="openai",
            operation=mr.OP_RUN_PROMPT,
        )


def _chain_texts(error):
    return [str(item) for item in exception_chain(error)]


class TestByokUnchanged:
    """Regression: today's BYOK resolution, byte for byte (GREEN on base)."""

    def test_stored_workspace_key_is_returned_as_stored(
        self, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        value = _manager(OPENAI_CHAT_MODEL, organization).get_api_key(
            organization_id=organization.id,
            workspace_id=workspace.id,
            provider="openai",
        )
        assert value == BYOK_FIXTURE_VALUE

    def test_missing_key_keeps_the_legacy_refusal(self, organization, workspace):
        with pytest.raises(ValueError) as refused:
            _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                organization_id=organization.id,
                workspace_id=workspace.id,
                provider="anthropic",
            )
        assert str(refused.value) == LEGACY_REFUSAL.format(provider="anthropic")


class TestGetApiKeyManagedBranch:
    def test_default_call_never_consults_the_port(self, mr, organization, workspace):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(ValueError) as refused:
                _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=workspace.id,
                    provider="anthropic",
                )
        assert str(refused.value) == LEGACY_REFUSAL.format(provider="anthropic")
        assert port.eligibility_calls == []
        assert port.authorize_calls == []

    def test_allow_managed_returns_an_unadmitted_binding(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            binding = _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                organization_id=organization.id,
                workspace_id=workspace.id,
                provider="anthropic",
                allow_managed=True,
                operation=mr.OP_DYNAMIC_COLUMN,
            )
        assert mr.is_managed_route_binding(binding)
        assert binding.mode == mr.MODE_MANAGED
        assert binding.payer == mr.PAYER_FUTURE_AGI_CREDITS
        assert binding.credential is None
        assert binding.gateway_route is None
        assert binding.provider == "anthropic"
        assert binding.model_name == ANTHROPIC_CHAT_MODEL
        assert binding.operation == mr.OP_DYNAMIC_COLUMN
        assert binding.organization_id == str(organization.id)
        assert binding.workspace_id == str(workspace.id)
        assert len(port.eligibility_calls) == 1
        # Nothing is admitted until a call is dispatched.
        assert port.authorize_calls == []

    def test_binding_is_never_a_provider_key(self, mr, organization, workspace):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        binding = _managed_binding(mr, port, organization, workspace)
        assert not isinstance(binding, (str, bytes, dict))
        assert bool(binding) is True
        # Anything that would put it in a header or a URL fails before a send.
        with pytest.raises(TypeError):
            str(binding)
        with pytest.raises(TypeError):
            f"Bearer {binding}"  # noqa: B018
        assert "Mock" not in repr(binding)
        assert ANTHROPIC_CHAT_MODEL in repr(binding)

    def test_stored_key_wins_and_the_port_is_untouched(
        self, mr, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "anthropic")
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            value = _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                organization_id=organization.id,
                workspace_id=workspace.id,
                provider="anthropic",
                allow_managed=True,
            )
        assert value == BYOK_FIXTURE_VALUE
        assert port.eligibility_calls == []
        assert port.authorize_calls == []

    def test_not_eligible_keeps_the_legacy_refusal(self, mr, organization, workspace):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteUnavailable) as refused:
                _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=workspace.id,
                    provider="anthropic",
                    allow_managed=True,
                )
        assert isinstance(refused.value, ValueError)
        assert str(refused.value) == LEGACY_REFUSAL.format(provider="anthropic")
        assert port.authorize_calls == []

    def test_default_port_admits_nothing(self, mr, organization, workspace):
        with pytest.raises(mr.ModelRouteUnavailable) as refused:
            _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                organization_id=organization.id,
                workspace_id=workspace.id,
                provider="anthropic",
                allow_managed=True,
            )
        assert str(refused.value) == LEGACY_REFUSAL.format(provider="anthropic")

    def test_without_organization_refused_before_the_port(self, mr, organization):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteUnavailable):
                LiteLLMModelManager(ANTHROPIC_CHAT_MODEL).get_api_key(
                    organization_id=None,
                    provider="anthropic",
                    allow_managed=True,
                )
        assert port.eligibility_calls == []
        assert port.authorize_calls == []

    def test_non_chat_model_is_never_managed(self, mr, organization, workspace):
        port = eligible_port(mr, organization, {TTS_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteUnavailable) as refused:
                _manager(TTS_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=workspace.id,
                    allow_managed=True,
                )
        assert str(refused.value) == LEGACY_REFUSAL.format(provider="lmnt")
        assert port.eligibility_calls == []

    def test_other_organizations_eligibility_does_not_apply(
        self, mr, organization, workspace, other_workspace
    ):
        port = eligible_port(mr, other_workspace.organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteUnavailable):
                _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=workspace.id,
                    provider="anthropic",
                    allow_managed=True,
                )
        assert port.authorize_calls == []

    def test_workspace_of_another_organization_is_refused_first(
        self, mr, organization, other_workspace
    ):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteScopeError):
                _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=other_workspace.id,
                    provider="anthropic",
                    allow_managed=True,
                )
        assert port.eligibility_calls == []
        assert port.authorize_calls == []

    def test_eligibility_failure_is_a_typed_refusal(self, mr, organization, workspace):
        port = eligible_port(
            mr,
            organization,
            {ANTHROPIC_CHAT_MODEL},
            eligibility_error=RuntimeError("gateway internals"),
        )
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ManagedRouteDenied) as refused:
                _manager(ANTHROPIC_CHAT_MODEL, organization).get_api_key(
                    organization_id=organization.id,
                    workspace_id=workspace.id,
                    provider="anthropic",
                    allow_managed=True,
                )
        assert refused.value.retryable is True
        assert "gateway internals" not in str(refused.value)
        assert port.authorize_calls == []


class TestManagedDispatch:
    """One completion = one admission, one request id, one gateway send
    (never more); nothing is admitted unless the send is certain;
    the access check runs before every send; and the
    request carries no provider credential."""

    def test_each_call_is_one_admission_with_its_own_request_id(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        binding = _binding(mr, port, organization, workspace)

        mr.managed_chat_completion(binding, dict(REQUEST))
        mr.managed_chat_completion(binding, dict(REQUEST))

        admitted = [call["request_id"] for call in port.authorize_calls]
        assert len(admitted) == 2
        assert len(set(admitted)) == 2
        # Each create() carries its admission's id in its own request
        # headers, so the SDK's retries of that create() share it.
        assert sent_request_ids(client) == admitted
        assert len(client.calls) == 2

    def test_identity_headers_are_per_request_and_win_over_the_port(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(
            mr,
            organization,
            {OPENAI_CHAT_MODEL},
            client=client,
            route_headers={
                "X-Org-Id": "spoofed",
                "x-agentcc-request-id": "spoofed",
                "X-Gateway-Hint": "hint",
            },
        )
        binding = _binding(mr, port, organization, workspace)

        mr.managed_chat_completion(binding, dict(REQUEST))

        assert client.calls[0]["kwargs"]["extra_headers"] == {
            "X-Gateway-Route": "mock-route-1",
            "X-Gateway-Hint": "hint",
            "X-Org-Id": str(organization.id),
            "X-Workspace-Id": str(workspace.id),
            "x-agentcc-request-id": port.authorize_calls[0]["request_id"],
        }
        assert client.calls[0]["default_headers"] == {}

    def test_request_carries_no_provider_credential(self, mr, organization, workspace):
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        binding = _binding(mr, port, organization, workspace)
        litellm_payload = {
            "model": OPENAI_CHAT_MODEL,
            "messages": MESSAGES,
            "temperature": 0.2,
            "max_tokens": 50,
            "tools": [],
            "custom_llm_provider": "openai",
            "api_key": binding,
            "api_base": "https://elsewhere.example/v1",
            "drop_params": True,
            "vertex_credentials": "{}",
            "extra_headers": {"X-Org-Id": "spoofed"},
            "thinking": {"type": "enabled", "budget_tokens": 32},
        }

        assert mr.managed_binding_in(litellm_payload) is binding
        mr.managed_chat_completion(binding, litellm_payload)

        sent = client.calls[0]["kwargs"]
        assert set(sent) == {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "extra_body",
            "extra_headers",
        }
        assert sent["extra_body"] == {
            "thinking": {"type": "enabled", "budget_tokens": 32}
        }
        assert sent["extra_headers"]["X-Org-Id"] == str(organization.id)

    def test_a_binding_anywhere_but_api_key_is_refused(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        binding = _binding(mr, port, organization, workspace)
        with pytest.raises(mr.ManagedRouteDenied) as refused:
            mr.managed_binding_in({**REQUEST, "client": binding})
        assert refused.value.code == "MANAGED_ROUTE_MISPLACED"
        assert mr.managed_binding_in({**REQUEST, "api_key": "a-provider-key"}) is None

    def test_model_mismatch_is_refused_before_admission(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        binding = _binding(mr, port, organization, workspace)
        with pytest.raises(mr.ManagedRouteDenied) as refused:
            mr.managed_chat_completion(binding, {**REQUEST, "model": "gpt-4o"})
        assert refused.value.code == "MANAGED_ROUTE_MODEL_MISMATCH"
        assert port.authorize_calls == []
        assert client.calls == []

    def test_unavailable_gateway_client_is_refused_before_admission(
        self, mr, organization, workspace
    ):
        port = mr.MockGatewayManagedRouting(
            eligible={str(organization.id): {OPENAI_CHAT_MODEL}}
        )
        binding = _binding(mr, port, organization, workspace)
        with pytest.raises(mr.ManagedRouteDenied) as refused:
            mr.managed_chat_completion(binding, dict(REQUEST))
        assert refused.value.code == "MANAGED_GATEWAY_UNAVAILABLE"
        assert port.authorize_calls == []

    def test_access_check_runs_before_admission_and_send(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        binding = _binding(mr, port, organization, workspace)
        mr.managed_chat_completion(binding, dict(REQUEST))
        assert port.events.index("before_send") < port.events.index("authorize")

        denied = eligible_port(
            mr,
            organization,
            {OPENAI_CHAT_MODEL},
            client=client,
            before_send_error=mr.ManagedRouteDenied("NO_ENTERPRISE_LICENSE"),
        )
        binding = _binding(mr, denied, organization, workspace)
        with pytest.raises(mr.ManagedRouteDenied) as refused:
            mr.managed_chat_completion(binding, dict(REQUEST))
        assert refused.value.code == "NO_ENTERPRISE_LICENSE"
        assert denied.authorize_calls == []
        assert len(client.calls) == 1

    def test_admission_is_released_when_nothing_is_sent(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(
            mr,
            organization,
            {OPENAI_CHAT_MODEL},
            client=client,
            headers_error=RuntimeError("header material unavailable"),
        )
        binding = _binding(mr, port, organization, workspace)
        with pytest.raises(mr.ManagedRouteDenied) as refused:
            mr.managed_chat_completion(binding, dict(REQUEST))
        assert "header material" not in str(refused.value)
        assert len(port.authorize_calls) == 1
        assert [route.route_id for route in port.release_calls] == ["mock-route-1"]
        assert client.calls == []

    def test_gateway_client_routing_checks_access_before_each_send(
        self, mr, monkeypatch
    ):
        checks = []

        class LicenseDenied(Exception):
            pass

        def require_gateway_access():
            checks.append("checked")
            raise LicenseDenied("NO_ENTERPRISE_LICENSE")

        fake = types.ModuleType("ee.usage.services.gateway_llm_client")
        fake.require_gateway_access = require_gateway_access
        monkeypatch.setitem(sys.modules, "ee.usage.services.gateway_llm_client", fake)
        with pytest.raises(LicenseDenied):
            mr.GatewayClientRouting().before_send()
        assert checks == ["checked"]

    def test_async_dispatch_admits_off_the_event_loop(
        self, mr, organization, workspace
    ):
        async_client = FakeGatewayClient(is_async=True)
        port = eligible_port(
            mr, organization, {OPENAI_CHAT_MODEL}, async_client=async_client
        )
        binding = _binding(mr, port, organization, workspace)

        async def run():
            loop_thread = threading.get_ident()
            first = await mr.managed_chat_completion_async(binding, dict(REQUEST))
            second = await mr.managed_chat_completion_async(binding, dict(REQUEST))
            return loop_thread, first, second

        loop_thread, first, second = asyncio.run(run())
        assert first.choices[0].message.content == "managed ok"
        assert port.authorize_threads
        assert loop_thread not in port.authorize_threads
        assert len(set(sent_request_ids(async_client))) == 2

    def test_stream_failure_is_a_typed_refusal_without_context(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient(
            stream_chunks=[_r12_priced_stream_chunk("a")],
            stream_error=gateway_refusal(
                429, "managed_spend_velocity", headers={"Retry-After": "7"}
            ),
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        binding = _binding(mr, port, organization, workspace)
        stream = mr.managed_chat_completion(binding, {**REQUEST, "stream": True})
        chunks = []
        with pytest.raises(mr.ManagedRouteRefused) as refused:
            for chunk in stream:
                chunks.append(chunk)
        assert len(chunks) == 1
        assert refused.value.code == "managed_spend_velocity"
        # Claimed and charged once (a chunk had arrived): terminal, never
        # offered as an automatic retry.
        assert refused.value.dispatched is True
        assert refused.value.retryable is False
        assert refused.value.retry_after is None
        assert refused.value.__cause__ is None
        assert refused.value.__context__ is None
        from model_hub.services import credit_ledger_port as cl
        assert [row[0] for row in cl.get_credit_ledger().calls] == ["hold", "settle"]
        assert "credits were charged" in refused.value.message
        assert str(refused.value) == refused.value.response_body()["message"]

    def test_async_stream_failure_is_typed(self, mr, organization, workspace):
        async_client = FakeGatewayClient(
            is_async=True,
            stream_chunks=[_r12_priced_stream_chunk("a")],
            stream_error=gateway_refusal(402, "managed_credits_exhausted"),
        )
        port = eligible_port(
            mr, organization, {OPENAI_CHAT_MODEL}, async_client=async_client
        )
        binding = _binding(mr, port, organization, workspace)

        async def run():
            stream = await mr.managed_chat_completion_async(
                binding, {**REQUEST, "stream": True}
            )
            async for _ in stream:
                pass

        with pytest.raises(mr.InsufficientCreditError) as refused:
            asyncio.run(run())
        assert refused.value.code == "managed_credits_exhausted"
        assert PRIVATE_UPSTREAM_TEXT not in " ".join(_chain_texts(refused.value))


# The real refusal contract: (status, error.code, class, retryable).
REAL_REFUSALS = [
    (402, "managed_credits_exhausted", "InsufficientCreditError", False),
    (402, "managed_credits_insufficient", "InsufficientCreditError", False),
    (402, "agent_budget_exhausted", "InsufficientCreditError", False),
    (429, "budget_exceeded", "ManagedRouteRefused", False),
    (429, "org_budget_exceeded", "ManagedRouteRefused", False),
    (429, "managed_spend_velocity", "ManagedRouteRefused", False),
    (429, "rate_limit_exceeded", "ManagedRouteRefused", False),
    (503, "managed_billing_unavailable", "ManagedRouteRefused", False),
    (403, "permission_denied", "ManagedRouteRefused", False),
]


class TestRealRefusalContract:
    @pytest.mark.parametrize("status,code,class_name,retryable", REAL_REFUSALS)
    def test_each_code_is_a_typed_refusal(
        self, mr, status, code, class_name, retryable
    ):
        error = mr.managed_call_error(gateway_refusal(status, code))
        assert type(error) is getattr(mr, class_name)
        assert isinstance(error, mr.ModelRouteError)
        assert error.code == code
        assert error.http_status == status
        assert error.retryable is retryable
        assert str(error) == error.message
        assert PRIVATE_UPSTREAM_TEXT not in error.message
        assert error.__cause__ is None and error.__context__ is None
        body = error.response_body()
        assert body["code"] == code
        assert PRIVATE_UPSTREAM_TEXT not in json.dumps(body, default=str)

    def test_every_contract_code_is_covered(self, mr):
        assert len(mr.REFUSAL_CONTRACT) == 49
        assert {code for _, code, _, _ in REAL_REFUSALS} <= {code for _, code in mr.REFUSAL_CONTRACT}

    def test_credits_insufficient_retries_only_for_in_flight_holds(self, mr):
        in_flight = mr.managed_call_error(
            gateway_refusal(
                402,
                "managed_credits_insufficient",
                metadata={
                    "limit_source": "agentcc_in_flight",
                    "remedy_hint": "retry",
                    "required_usd": "0.40",
                    "available_usd": "0.10",
                    "scope": "org",
                },
                headers={"Retry-After": "2"},
            )
        )
        assert in_flight.retryable is True
        assert in_flight.retry_after == 2
        assert in_flight.required == Decimal("0.40")
        assert in_flight.available == Decimal("0.10")
        assert in_flight.details["limit_source"] == "agentcc_in_flight"

        credits = mr.managed_call_error(
            gateway_refusal(
                402,
                "managed_credits_insufficient",
                metadata={"limit_source": "agentcc_credits", "remedy_hint": "retry"},
                headers={"Retry-After": "2"},
            )
        )
        assert credits.retryable is False
        assert credits.retry_after is None

    def test_keyed_on_code_not_type(self, mr):
        budget = mr.managed_call_error(
            gateway_refusal(429, "org_budget_exceeded", error_type="rate_limit_error")
        )
        assert budget.retryable is False
        velocity = mr.managed_call_error(
            gateway_refusal(
                429,
                "managed_spend_velocity",
                error_type="payment_required",
                metadata={"limit_source": "agentcc_spend_guard", "remedy_hint": "retry"},
                headers={"retry-after": "5"},
            )
        )
        assert velocity.retryable is True
        assert velocity.retry_after == 5
        typed_only = mr.managed_call_error(
            GatewayStatusError(429, {"type": "rate_limit_error"})
        )
        assert typed_only.retryable is False
        assert typed_only.code == "MANAGED_ROUTE_CALL_FAILED"
        assert typed_only.category == "generic"

    def test_unknown_codes_fail_closed_by_status(self, mr):
        credit = mr.managed_call_error(gateway_refusal(402, "a_new_code"))
        assert isinstance(credit, mr.TerminalManagedRouteCallFailed)
        assert credit.code == "MANAGED_ROUTE_CALL_FAILED"
        assert credit.retryable is False
        denied = mr.managed_call_error(gateway_refusal(403, "a_new_code"))
        assert type(denied) is mr.TerminalManagedRouteCallFailed
        assert denied.retryable is False
        server = mr.managed_call_error(GatewayStatusError(500, {}))
        client = mr.managed_call_error(GatewayStatusError(400, {}))
        assert isinstance(server, mr.ManagedRouteCallFailed)
        assert server.retryable is False
        assert client.retryable is False

    def test_transport_failures(self, mr):
        timeout = mr.managed_call_error(TimeoutError("read timed out at 10.0.0.7"))
        assert isinstance(timeout, mr.TerminalManagedRouteCallFailed)
        assert timeout.retryable is False
        transport = mr.managed_call_error(ConnectionError("socket detail"))
        assert type(transport) is mr.TerminalManagedRouteCallFailed
        assert transport.retryable is False
        for error in (timeout, transport):
            assert "10.0.0.7" not in str(error)
            assert "socket detail" not in str(error)

    def test_billing_url_comes_from_settings_never_the_gateway(self, mr, settings):
        settings.MANAGED_CREDITS_BILLING_URL = "/settings/billing"
        error = mr.managed_call_error(
            GatewayStatusError(
                402,
                {
                    "code": "managed_credits_exhausted",
                    "billing_url": "https://elsewhere.example/pay",
                },
            )
        )
        body = error.response_body()
        assert body["billing_url"] == "/settings/billing"
        assert body["remedy"] == "add_credits"
        assert "elsewhere" not in json.dumps(body, default=str)

    def test_admission_refusal_from_the_port_is_typed(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient()
        port = eligible_port(
            mr,
            organization,
            {OPENAI_CHAT_MODEL},
            client=client,
            insufficient={str(organization.id): ("0.50", "0.10")},
        )
        binding = _binding(mr, port, organization, workspace)
        with pytest.raises(mr.InsufficientCreditError) as refused:
            mr.managed_chat_completion(binding, dict(REQUEST))
        assert refused.value.code == "managed_credits_exhausted"
        assert refused.value.required == Decimal("0.50")
        assert client.calls == []


class TestResolveModelRoute:
    def test_byok_route(self, mr, organization, workspace, user):
        _store_byok(organization, workspace, user, "openai")
        route = mr.resolve_model_route(
            organization=organization,
            workspace=workspace,
            model_name=OPENAI_CHAT_MODEL,
            operation=mr.OP_CUSTOM_PROMPT_EVAL,
        )
        assert route.mode == mr.MODE_BYOK
        assert route.payer == mr.PAYER_OWN_PROVIDER_KEY
        assert route.provider == "openai"
        assert route.credential == BYOK_FIXTURE_VALUE
        assert route.gateway_route is None
        assert BYOK_FIXTURE_VALUE not in repr(route)

    def test_managed_route_is_a_binding_and_admits_nothing(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            route = mr.resolve_model_route(
                organization=organization,
                workspace=workspace,
                model_name=OPENAI_CHAT_MODEL,
                operation=mr.OP_CUSTOM_PROMPT_EVAL,
            )
        assert mr.is_managed_route_binding(route)
        assert route.mode == mr.MODE_MANAGED
        assert route.payer == mr.PAYER_FUTURE_AGI_CREDITS
        assert route.credential is None
        assert route.organization_id == str(organization.id)
        assert route.operation == mr.OP_CUSTOM_PROMPT_EVAL
        assert port.authorize_calls == []

    def test_admitted_route_material_is_never_printed(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        binding = _binding(mr, port, organization, workspace)
        route = mr.admit_managed_request(binding)
        assert route.gateway_route is not None
        assert route.request_id == port.authorize_calls[0]["request_id"]
        for text in (repr(route), repr(binding)):
            assert "mock-route" not in text
            assert "X-Gateway-Route" not in text

    def test_workspace_instance_of_another_organization_is_refused(
        self, mr, organization, other_workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ModelRouteScopeError):
                mr.resolve_model_route(
                    organization=organization,
                    workspace=other_workspace,
                    model_name=OPENAI_CHAT_MODEL,
                    operation=mr.OP_CUSTOM_PROMPT_EVAL,
                )
        assert port.eligibility_calls == []

    def test_eligibility_read_admits_nothing(
        self, mr, organization, workspace, other_workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            assert mr.is_managed_eligible(
                organization_id=organization.id,
                workspace_id=workspace.id,
                model_name=OPENAI_CHAT_MODEL,
            )
            assert not mr.is_managed_eligible(
                organization_id=organization.id,
                workspace_id=workspace.id,
                model_name=TTS_MODEL,
                model_mode="tts",
            )
            with pytest.raises(mr.ModelRouteScopeError):
                mr.is_managed_eligible(
                    organization_id=organization.id,
                    workspace_id=other_workspace.id,
                    model_name=OPENAI_CHAT_MODEL,
                )
        assert port.authorize_calls == []

    def test_unknown_operation_is_refused(self, mr, organization, workspace):
        with pytest.raises(ValueError):
            mr.resolve_model_route(
                organization=organization,
                workspace=workspace,
                model_name=OPENAI_CHAT_MODEL,
                operation="anything",
            )


class TestPortRegistry:
    def test_set_port_rejects_an_object_that_is_not_a_port(self, mr):
        with pytest.raises(TypeError):
            mr.set_managed_routing_port(object())

    def test_a_port_without_release_or_access_check_is_rejected(self, mr):
        class AdmitOnlyPort:
            def eligible_models(self, *, organization_id, workspace_id, model_names):
                return frozenset()

            def authorize(self, **kwargs):
                return object()

            def request_headers(self, gateway_route):
                return {}

            def client(self, *, is_async):
                return None

        with pytest.raises(TypeError):
            mr.set_managed_routing_port(AdmitOnlyPort())

    def test_registered_port_is_restored(self, mr, organization):
        port = eligible_port(mr, organization, set())
        with mr.managed_routing_port(port):
            assert mr.get_managed_routing_port() is port
        assert isinstance(
            mr.get_managed_routing_port(), mr.UnavailableGatewayManagedRouting
        )


def _r12_priced_stream_chunk(content, model=OPENAI_CHAT_MODEL):
    from model_hub.tests.test_model_route_support import GatewayUsage

    chunk = stream_chunk(content, model=model)
    chunk.usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    return chunk


def _run_prompt(organization, workspace, model=ANTHROPIC_CHAT_MODEL, **overrides):
    params = {
        "model": model,
        "messages": list(MESSAGES),
        "organization_id": organization.id,
        "output_format": "string",
        "temperature": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "max_tokens": None,
        "top_p": None,
        "response_format": None,
        "tool_choice": None,
        "tools": None,
        "workspace_id": workspace.id,
    }
    params.update(overrides)
    return RunPrompt(**params)


def _llm_handler(binding, organization, workspace):
    return LLMHandler(
        ModelHandlerContext(
            model=ANTHROPIC_CHAT_MODEL,
            messages=list(MESSAGES),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            output_format="string",
            provider="anthropic",
            api_key=binding,
            template_id="one-key-template",
            version="v1",
            result_index=0,
            num_results=1,
        )
    )


class TestRunPromptManagedCallSites:
    """Dataset Run Prompt (R3-02; dynamic columns R3-03 and experiment rows
    R3-05 build the same RunPrompt). RED until compose hunks CH-01..CH-07,
    CH-23, CH-24 (litellm_response.py), CH-27..CH-29 (model_endpoints.py)
    and CH-30, CH-31 (llm_handler.py) are applied."""

    def test_goes_to_the_gateway_only(self, mr, organization, workspace, tripped):
        client = FakeGatewayClient(response=chat_response("managed ok"))
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        with mr.managed_routing_port(port):
            response, value_info = _run_prompt(
                organization, workspace
            ).litellm_response()

        assert response == "managed ok"
        assert value_info["data"]["response"] == "managed ok"
        assert tripped == []
        assert len(client.calls) == 1
        sent = client.calls[0]["kwargs"]
        assert sent["model"] == ANTHROPIC_CHAT_MODEL
        for credential_field in ("api_key", "api_base", "base_url", "client"):
            assert credential_field not in sent
        assert sent["extra_headers"]["X-Org-Id"] == str(organization.id)
        assert sent["extra_headers"]["X-Workspace-Id"] == str(workspace.id)
        assert len(port.authorize_calls) == 1
        assert (
            sent["extra_headers"]["x-agentcc-request-id"]
            == port.authorize_calls[0]["request_id"]
        )
        assert port.authorize_calls[0]["operation"] == mr.OP_RUN_PROMPT
        assert PLATFORM_KEY_SENTINEL not in repr(client.calls)

    def test_byok_twin_is_unchanged(
        self, mr, organization, workspace, user, monkeypatch
    ):
        """Composed-tree BYOK twin; the standalone base twin is below."""
        import litellm

        _store_byok(organization, workspace, user, "anthropic")
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        seen = []

        def completion(**kwargs):
            seen.append(kwargs)
            return chat_response("byok ok", model=ANTHROPIC_CHAT_MODEL)

        monkeypatch.setattr(litellm, "completion", completion)
        with mr.managed_routing_port(port):
            response, _ = _run_prompt(organization, workspace).litellm_response()

        assert response == "byok ok"
        assert seen[0]["api_key"] == BYOK_FIXTURE_VALUE
        assert seen[0]["custom_llm_provider"] == "anthropic"
        assert client.calls == []
        assert port.eligibility_calls == []
        assert port.authorize_calls == []

    def test_credit_refusal_survives_litellm_try_except(
        self, mr, organization, workspace, tripped
    ):
        client = FakeGatewayClient(
            error=gateway_refusal(402, "managed_credits_exhausted")
        )
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        with mr.managed_routing_port(port):
            with pytest.raises(mr.InsufficientCreditError) as refused:
                _run_prompt(organization, workspace).litellm_response()

        assert refused.value.code == "managed_credits_exhausted"
        assert refused.value.http_status == 402
        assert refused.value.retryable is False
        assert PRIVATE_UPSTREAM_TEXT not in " ".join(_chain_texts(refused.value))
        assert len(client.calls) == 1
        assert tripped == []

    @pytest.mark.parametrize(
        "status,body,class_name,code",
        [
            (
                429,
                {"code": "budget_exceeded"},
                "ManagedRouteRefused",
                "budget_exceeded",
            ),
            (
                503,
                {"code": "managed_billing_unavailable"},
                "TerminalManagedRouteCallFailed",
                "MANAGED_OUTCOME_PENDING_RECONCILE",
            ),
            (
                500,
                {"message": "x"},
                "TerminalManagedRouteCallFailed",
                "MANAGED_OUTCOME_PENDING_RECONCILE",
            ),
        ],
    )
    def test_other_failures_are_typed_and_sanitized(
        self, mr, organization, workspace, tripped, status, body, class_name, code
    ):
        client = FakeGatewayClient(error=GatewayStatusError(status, body))
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        with mr.managed_routing_port(port):
            with pytest.raises(getattr(mr, class_name)) as refused:
                _run_prompt(organization, workspace).litellm_response()
        assert refused.value.code == code
        assert PRIVATE_UPSTREAM_TEXT not in " ".join(_chain_texts(refused.value))
        assert refused.value.retryable is False and refused.value.retry_after is None
        assert len(client.calls) == len(port.authorize_calls) == 1
        if code == "MANAGED_OUTCOME_PENDING_RECONCILE":
            from model_hub.services import credit_ledger_port as cl

            assert isinstance(refused.value, ValueError)
            assert refused.value.charged is None and refused.value.dispatched is None
            ledger = cl.get_credit_ledger()
            assert [entry[0] for entry in ledger.calls] == ["hold"]
            assert len(ledger.holds) == 1
            held = next(iter(ledger.holds.values()))
            assert held.state == "held" and held.pending_reconcile is True
            assert held.debits == 0 and held.settled_amount is None
            assert ledger.held_of(organization) == held.amount
        assert tripped == []

    def test_audio_output_is_refused_before_admission(
        self, mr, organization, workspace, tripped
    ):
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ManagedRouteUnsupported) as refused:
                _run_prompt(
                    organization, workspace, output_format="audio"
                ).litellm_response()
        assert refused.value.code == "MANAGED_ROUTE_UNSUPPORTED_OUTPUT"
        assert port.authorize_calls == []
        assert client.calls == []
        assert tripped == []

    def test_streaming_goes_to_the_gateway_only(
        self, mr, organization, workspace, tripped, monkeypatch
    ):
        client = FakeGatewayClient(
            stream_chunks=[_r12_priced_stream_chunk("Hel", ANTHROPIC_CHAT_MODEL), _r12_priced_stream_chunk("lo", ANTHROPIC_CHAT_MODEL)]
        )
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        handler = _llm_handler(
            _managed_binding(mr, port, organization, workspace), organization, workspace
        )
        ws = RecordingWsManager()
        monkeypatch.setattr(handler, "_get_ws_manager", lambda: ws)

        result = handler.execute_sync(streaming=True)

        assert result.response == "Hello"
        assert client.calls[0]["kwargs"]["stream"] is True
        assert len(port.authorize_calls) == 1
        assert ws.sent("send_error_message") == []
        assert tripped == []

    def test_streaming_failure_sends_the_typed_message(
        self, mr, organization, workspace, tripped, monkeypatch
    ):
        client = FakeGatewayClient(
            stream_chunks=[_r12_priced_stream_chunk("Hel", ANTHROPIC_CHAT_MODEL)],
            stream_error=gateway_refusal(402, "managed_credits_exhausted"),
        )
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        handler = _llm_handler(
            _managed_binding(mr, port, organization, workspace), organization, workspace
        )
        ws = RecordingWsManager()
        monkeypatch.setattr(handler, "_get_ws_manager", lambda: ws)

        with pytest.raises(Exception) as failed:
            handler.execute_sync(streaming=True)

        refusal = next(
            error
            for error in exception_chain(failed.value)
            if isinstance(error, mr.InsufficientCreditError)
        )
        assert refusal.code == "managed_credits_exhausted"
        assert [sent["error"] for sent in ws.sent("send_error_message")] == [
            refusal.message
        ]
        assert PRIVATE_UPSTREAM_TEXT not in repr(ws.messages)
        assert PRIVATE_UPSTREAM_TEXT not in " ".join(_chain_texts(failed.value))
        assert tripped == []

    def test_async_handler_goes_to_the_gateway_only(
        self, mr, organization, workspace, tripped
    ):
        async_client = FakeGatewayClient(
            response=chat_response("async managed"), is_async=True
        )
        port = eligible_port(
            mr, organization, {ANTHROPIC_CHAT_MODEL}, async_client=async_client
        )
        handler = _llm_handler(
            _managed_binding(mr, port, organization, workspace), organization, workspace
        )

        result = asyncio.run(handler.execute_async(streaming=False))

        assert result.response == "async managed"
        assert len(async_client.calls) == 1
        assert sent_request_ids(async_client) == [port.authorize_calls[0]["request_id"]]
        assert tripped == []

    def test_image_generation_never_takes_a_binding(
        self, mr, organization, workspace, tripped
    ):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        binding = _managed_binding(mr, port, organization, workspace)
        with pytest.raises(mr.ManagedRouteUnsupported):
            guarded_image_generation(model="dall-e-3", prompt="x", api_key=binding)
        assert port.authorize_calls == []
        assert tripped == []


class TestComposeAsyncWorkbench:
    @pytest.mark.parametrize("streaming", [False, True])
    def test_async_workbench_success_has_one_admission(self, mr, organization, workspace, tripped, monkeypatch, streaming):
        from decimal import Decimal
        from model_hub.services import credit_ledger_port as cl
        from unittest.mock import AsyncMock
        from model_hub.tests.test_model_route_support import GatewayUsage

        chunk = stream_chunk("async ok", model=ANTHROPIC_CHAT_MODEL)
        chunk.usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        client = FakeGatewayClient(response=chat_response("async ok", model=ANTHROPIC_CHAT_MODEL),
                                   stream_chunks=[chunk], is_async=True)
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, async_client=client)
        ws = AsyncMock()
        ws.is_streaming_stopped.return_value = False
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            meter = cl.call_meter(
                organization=organization, workspace=workspace, model_name=ANTHROPIC_CHAT_MODEL,
                purpose=cl.PURPOSE_PROMPT_PLAYGROUND, source="prompt_playground", source_id="compose",
            )
            result, call = asyncio.run(cl.arun_metered(
                meter, lambda: _run_prompt(organization, workspace, ws_manager=ws).litellm_response_async(
                    streaming=streaming, type="run_prompt", template_id="compose", version="v1", index=0, max_index=1,
                ), messages=MESSAGES, observe=cl.observe_value_info,
            ))
            response, _ = result
            assert call.output_seen is True
        assert response == "async ok"
        assert len(port.authorize_calls) == 1
        assert len(client.calls) == 1
        assert [x[0] for x in ledger.calls].count("settle") == 1
        assert call.gateway_request_id == "fixture-gateway-response"
        assert call.actual_amount > 0 and call.cost_source.startswith("provider_usage")
        if streaming:
            ws.send_started_message.assert_awaited_once()
            ws.send_completed_message.assert_awaited_once()
            ws.send_error_message.assert_not_awaited()
        assert tripped == []

    @pytest.mark.parametrize("streaming", [False, True])
    def test_async_workbench_without_outer_meter_keeps_cut(self, mr, organization, workspace, streaming):
        from model_hub.services import credit_ledger_port as cl
        client = FakeGatewayClient(is_async=True)
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, async_client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            with pytest.raises(cl.ManagedDispatchUnavailableError, match="cannot run this prompt here yet") as caught:
                asyncio.run(_run_prompt(organization, workspace).litellm_response_async(
                    streaming=streaming, type="run_prompt", template_id="compose", version="v1", index=0, max_index=1,
                ))
        assert type(caught.value) is cl.ManagedDispatchUnavailableError
        assert str(caught.value) == cl.MANAGED_PROMPT_CUT_MESSAGE
        assert caught.value.retryable is False
        assert client.calls == []
        assert port.authorize_calls == []
        assert ledger.calls == []

    def test_async_workbench_refusal_is_typed(self, mr, organization, workspace, tripped):
        from decimal import Decimal
        from model_hub.services import credit_ledger_port as cl
        client = FakeGatewayClient(error=gateway_refusal(402, "managed_credits_exhausted"), is_async=True)
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, async_client=client)
        with cl.use_credit_ledger(cl.MockCreditLedger(default_balance=Decimal("1"))), mr.managed_routing_port(port):
            with pytest.raises(mr.InsufficientCreditError) as caught:
                asyncio.run(_run_prompt(organization, workspace).litellm_response_async(type="run_prompt"))
        assert caught.value.code == "managed_credits_exhausted"
        assert PRIVATE_UPSTREAM_TEXT not in " ".join(_chain_texts(caught.value))
        assert len(port.authorize_calls) == 1
        assert tripped == []

    def test_async_workbench_audio_has_no_admission(self, mr, organization, workspace, tripped):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            with pytest.raises(mr.ManagedRouteUnsupported):
                asyncio.run(_run_prompt(organization, workspace, output_format="audio").litellm_response_async(type="run_prompt"))
        assert port.authorize_calls == []
        assert tripped == []

    def test_byok_run_prompt_base_twin(self, organization, workspace, user, monkeypatch):
        import litellm
        _store_byok(organization, workspace, user, "anthropic")
        sent = []
        def complete(**kwargs):
            sent.append(kwargs)
            return chat_response("byok ok", model=ANTHROPIC_CHAT_MODEL)
        monkeypatch.setattr(litellm, "completion", complete)
        response, _ = _run_prompt(organization, workspace).litellm_response()
        assert response == "byok ok"
        assert sent[0]["api_key"] == BYOK_FIXTURE_VALUE
        assert sent[0]["custom_llm_provider"] == "anthropic"


class TestComposeOneMeterTransport:
    def test_outer_meter_and_actual_transport_share_one_debit(self, mr, organization, workspace, tripped):
        from decimal import Decimal
        from model_hub.services import credit_ledger_port as cl
        client = FakeGatewayClient(response=chat_response("one debit"))
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            meter = cl.call_meter(organization=organization, workspace=workspace,
                model_name=ANTHROPIC_CHAT_MODEL, purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
                source="dataset_run_prompt", source_id="compose")
            with meter.metered(amount_estimate="0.1") as call:
                response, info = _run_prompt(organization, workspace).litellm_response()
                call.observe(cost="0.04")
            assert meter.emit_usage_event(call, event_type="dataset_run_prompt") is None
        assert response == "one debit"
        assert [x[0] for x in ledger.calls] == ["hold", "settle"]
        assert ledger.balance_of(organization) == Decimal("0.96")
        assert call.dispatch_count == 1
        assert len(port.authorize_calls) == len(client.calls) == 1
        assert sent_request_ids(client) == [call.request_id]
        assert port.authorize_calls[0]["request_id"] == call.request_id
        assert tripped == []


@pytest.mark.parametrize("headers", [None, {}, {"X-AgentCC-Request-ID": "explicit-response"}])
def test_r12_gateway_double_defaults_do_not_overwrite_explicit_missing_headers(headers):
    from model_hub.tests.test_model_route_support import GatewayUsage

    client = FakeGatewayClient(response_headers=headers)
    clone = client.with_options(default_headers={"test": "value"})
    expected = {"x-agentcc-request-id": "fixture-gateway-response"} if headers is None else headers
    assert client.response_headers == clone.response_headers == expected
    usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    assert usage.prompt_tokens == 20
    assert dict(usage) == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    # Missing usage remains an explicit fixture option; never invent a price.
    assert stream_chunk("unpriced").usage is None


# OK-COMPOSE-r13: these controls are authored before the retry fix. The BYOK
# controls are RED on the composed r12 source; managed terminality is retained.
def _r13_transient_error(kind):
    if kind == "builtin_timeout":
        return TimeoutError("provider timed out")
    if kind in ("requests_timeout", "requests_read_timeout", "requests_connect_timeout"):
        import requests

        timeout_types = {
            "requests_timeout": requests.exceptions.Timeout,
            "requests_read_timeout": requests.exceptions.ReadTimeout,
            "requests_connect_timeout": requests.exceptions.ConnectTimeout,
        }
        return timeout_types[kind]("provider timed out")
    return GatewayStatusError(429, {"code": "rate_limit_exceeded"})


class TestR13RunPromptRetryBoundary:
    @pytest.mark.parametrize("credential_kind", ["string", "config", "missing"])
    @pytest.mark.parametrize(
        "failure_kind",
        ["builtin_timeout", "requests_timeout", "requests_read_timeout", "requests_connect_timeout", "provider_429"],
    )
    def test_byok_transient_failure_retries(self, monkeypatch, credential_kind, failure_kind):
        from agentic_eval.core_evals.run_prompt import litellm_response as response_module

        runner = response_module.RunPrompt.__new__(response_module.RunPrompt)
        if credential_kind == "string":
            runner.api_key = BYOK_FIXTURE_VALUE
        elif credential_kind == "config":
            runner.api_key = {"api_key": BYOK_FIXTURE_VALUE}
        error = _r13_transient_error(failure_kind)
        send = MagicMock(side_effect=[error, "provider recovered"])
        sleeps = []
        monkeypatch.setattr(response_module.time, "sleep", sleeps.append)
        with patch.object(response_module, "managed_route_failure", wraps=response_module.managed_route_failure) as normalize:
            result = runner._retry_on_timeout(send, max_retries=3, initial_delay=2, max_delay=3)
        assert result == "provider recovered"
        assert send.call_count == 2
        assert sleeps == [2]
        normalize.assert_not_called()

    @pytest.mark.parametrize(
        "failure_kind",
        ["builtin_timeout", "requests_timeout", "requests_read_timeout", "requests_connect_timeout", "provider_429"],
    )
    def test_managed_transient_failure_is_terminal(self, mr, organization, workspace, monkeypatch, failure_kind):
        from agentic_eval.core_evals.run_prompt import litellm_response as response_module

        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        binding = _binding(mr, port, organization, workspace)
        runner = response_module.RunPrompt.__new__(response_module.RunPrompt)
        runner.api_key = binding
        error = _r13_transient_error(failure_kind)
        send = MagicMock(side_effect=[error, "must not retry"])
        sleeps = []
        monkeypatch.setattr(response_module.time, "sleep", sleeps.append)
        with patch.object(response_module, "managed_route_failure", wraps=response_module.managed_route_failure) as normalize:
            with pytest.raises(type(error)) as caught:
                runner._retry_on_timeout(send, max_retries=3, initial_delay=2, max_delay=3)
        assert caught.value is error
        assert send.call_count == 1
        assert sleeps == []
        normalize.assert_called_once_with(error)

    def test_byok_exhaustion_preserves_bounded_backoff(self, monkeypatch):
        from agentic_eval.core_evals.run_prompt import litellm_response as response_module

        runner = response_module.RunPrompt.__new__(response_module.RunPrompt)
        runner.api_key = BYOK_FIXTURE_VALUE
        error = TimeoutError("provider timed out")
        send = MagicMock(side_effect=error)
        sleeps = []
        monkeypatch.setattr(response_module.time, "sleep", sleeps.append)
        with pytest.raises(TimeoutError) as caught:
            runner._retry_on_timeout(send, max_retries=3, initial_delay=2, max_delay=3)
        assert caught.value is error
        assert send.call_count == 3
        assert sleeps == [2, 3]

    def test_typed_terminal_refusal_without_binding_still_stops(self, monkeypatch):
        from agentic_eval.core_evals.run_prompt import litellm_response as response_module
        from model_hub.services import credit_ledger_port as ledger

        runner = response_module.RunPrompt.__new__(response_module.RunPrompt)
        error = ledger.CreditSettlementError()
        send = MagicMock(side_effect=[error, "must not retry"])
        sleeps = []
        monkeypatch.setattr(response_module.time, "sleep", sleeps.append)
        with patch.object(response_module, "managed_route_failure", wraps=response_module.managed_route_failure) as normalize:
            with pytest.raises(ledger.CreditSettlementError) as caught:
                runner._retry_on_timeout(send)
        assert caught.value is error
        assert send.call_count == 1
        assert sleeps == []
        normalize.assert_not_called()

    @pytest.mark.parametrize("mode,method", [("stt", "_transcription_response"), ("image_generation", "_image_generation_response")])
    @pytest.mark.parametrize("failure_kind", ["builtin_timeout", "provider_429"])
    def test_byok_media_entry_point_retries(self, mr, organization, workspace, user, monkeypatch, mode, method, failure_kind):
        from agentic_eval.core_evals.run_prompt import litellm_response as response_module

        _store_byok(organization, workspace, user, "openai")
        runner = _run_prompt(organization, workspace, model=OPENAI_CHAT_MODEL)
        monkeypatch.setattr(response_module, "get_model_mode", lambda model: mode)
        monkeypatch.setattr(response_module.OtherServicesManager, "get_transcription_handler", lambda self, provider: None)
        expected = ("provider recovered", {"metadata": {}})
        send = MagicMock(side_effect=[_r13_transient_error(failure_kind), expected])
        monkeypatch.setattr(runner, method, send)
        sleeps = []
        monkeypatch.setattr(response_module.time, "sleep", sleeps.append)
        with patch.object(response_module, "managed_route_failure", wraps=response_module.managed_route_failure) as normalize:
            result = runner._litellm_response_old()
        assert result == expected
        assert runner.api_key == BYOK_FIXTURE_VALUE
        assert send.call_count == 2
        assert sleeps == [1]
        normalize.assert_not_called()


class TestR14RequestsTimeoutClassification:
    @pytest.mark.parametrize(
        "failure_kind", ["requests_timeout", "requests_read_timeout", "requests_connect_timeout"]
    )
    @pytest.mark.parametrize("wrapped", [False, True])
    def test_requests_timeout_family_is_a_terminal_typed_refusal(self, mr, failure_kind, wrapped):
        error = _r13_transient_error(failure_kind)
        if wrapped:
            wrapper = Exception("local wrapper")
            wrapper.__cause__ = error
            error = wrapper

        refusal = mr.managed_route_failure(error)

        assert type(refusal) is mr.TerminalManagedRouteCallFailed
        assert refusal.code == "MANAGED_ROUTE_CALL_FAILED"
        assert refusal.retryable is False
        assert refusal.is_managed_refusal is True
        assert refusal.__cause__ is None
        assert refusal.__context__ is None
        assert "provider timed out" not in str(refusal)
        assert "local wrapper" not in str(refusal)


class TestR13RealSettlementFailureDisplay:
    @pytest.mark.parametrize("async_mode", [False, True])
    @pytest.mark.parametrize("normalized", [False, True])
    def test_original_refusal_survives_real_settle_failure(self, mr, organization, workspace, monkeypatch, async_mode, normalized):
        """RED until the two hash-bound C04 owner compose hunks are applied.

        Use the real open/claim/fail/succeed flow and MockCreditLedger's failed
        settle I/O, never assign status or replace session.fail with a no-op.
        """
        from decimal import Decimal
        from model_hub.services import credit_ledger_port as ledger

        port = ledger.MockCreditLedger(default_balance=Decimal("10"))
        port.fail_next_settles = ledger.SETTLE_ATTEMPTS
        envelope = ledger.build_envelope(
            organization=organization, workspace=workspace,
            purpose=ledger.PURPOSE_DATASET_RUN_PROMPT, source="dataset_run_prompt",
            source_id="r13-real-settle-failure", model=OPENAI_CHAT_MODEL,
        )
        meter = ledger.CallMeter(organization=organization, workspace=workspace,
                                 route=ledger.PayerRoute.managed(), envelope=envelope)
        raw = gateway_refusal(
            503, "managed_billing_unavailable", error_type="server_error",
            metadata={"reason": "redis_unavailable"}, headers={"retry-after": "600"},
        )
        original = mr.managed_call_error(raw) if normalized else raw
        selected = []
        real_succeed = ledger._HoldSession.succeed

        def observe_selected_outcome(session):
            try:
                return real_succeed(session)
            except ledger.CreditSettlementError as outcome:
                selected.append(outcome)
                raise

        # Observation only: every real settlement operation still executes.
        monkeypatch.setattr(ledger._HoldSession, "succeed", observe_selected_outcome)
        calls = []

        def partial_output(call):
            calls.append(call)
            assert ledger.require_managed_hold(model_name=OPENAI_CHAT_MODEL) is call
            ledger.note_gateway_response(call, SimpleNamespace(headers={
                "x-agentcc-request-id": "r13-priced-partial-response",
            }))
            call.observe(cost=Decimal("0.02"))
            raise original

        async def run_async():
            async with meter.ametered(amount_estimate="0.1") as call:
                partial_output(call)

        with ledger.use_credit_ledger(port):
            with pytest.raises(ledger.CreditSettlementError) as caught:
                if async_mode:
                    asyncio.run(run_async())
                else:
                    with meter.metered(amount_estimate="0.1") as call:
                        partial_output(call)

        assert selected == [caught.value]
        assert caught.value is selected[0]
        assert type(caught.value) is ledger.CreditSettlementError
        assert isinstance(caught.value.__cause__, ConnectionError)
        assert len(calls) == 1
        call = calls[0]
        assert call.status == "settle_failed"
        assert call.dispatch_count == 1
        assert call.actual_amount is None
        assert port.holds[call.hold_id].state == "held"
        assert port.holds[call.hold_id].settled_amount is None
        assert port.holds[call.hold_id].debits == 0
        assert port.balance_of(organization) == Decimal("10")
        assert [entry[0] for entry in port.calls] == ["hold"] + ["settle"] * ledger.SETTLE_ATTEMPTS
        assert ledger.current_managed_call() is None
        assert caught.value.code == "CREDIT_SETTLEMENT_FAILED"
        assert caught.value.http_status == 502
        assert caught.value.charged is None
        assert caught.value.retryable is False
        projected = mr.managed_route_failure(caught.value)
        for body in [caught.value.as_response_body(), projected.response_body()]:
            assert body["code"] == "CREDIT_SETTLEMENT_FAILED"
            assert body["message"] == caught.value.message
            assert body["retryable"] is False
            assert body["retry_after"] is None
            assert body["original_refusal"]["code"] == "managed_billing_unavailable"
            assert body["original_refusal"]["status"] == 503
            assert body["original_refusal"]["metadata"] == {"reason": "redis_unavailable"}
            assert body["original_refusal"]["display_wait_seconds"] == 600
            assert body["original_refusal"]["retryable"] is False
            assert body["original_refusal"]["retry_after"] is None
            assert PRIVATE_UPSTREAM_TEXT not in str(body)

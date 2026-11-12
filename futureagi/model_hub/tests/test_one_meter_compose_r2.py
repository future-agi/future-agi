"""OK-COMPOSE r2: one Future AGI key charges exactly once per managed call.

RED on the OK-COMPOSE r1 bytes (static reasoning; RED-TESTS.json):

- ``TestManagedJudgeIsOneDebit``: r1 EvaluationRunner emits
  ``calculate_ai_credits(llm_cost + per_run_fee)`` after the standalone
  transport meter already settled the same judge call, and marks no settled
  request id.
- ``TestManagedJudgeCut``: r1 ``create_eval_instance`` hands a managed judge to
  every caller, including surfaces that bill the evaluation again, and has no
  ``managed_judge_billing`` argument.
- ``TestPromptWorkbenchCut``: r1 RunPrompt dispatches a managed binding for
  the prompt workbench's synchronous run (a ``template_id`` and no outer
  meter) through the standalone transport meter; prompt_template.py then
  debits the same call through legacy billing.
- ``TestChargedBillingFailureCopy``: r1 settles a claimed call that failed
  with ``managed_billing_unavailable`` and then tells the user it was
  not run.
- ``TestBoundedAmounts``: r1 ``_to_decimal`` evaluates ``abs()`` before the
  exponent bound, so ``1E1000000`` raises
  decimal.Overflow.

Doubles: MockCreditLedger (ledger), MockGatewayManagedRouting with
FakeGatewayClient (gateway; no network), and a capturing double of the EE
cost-based usage emitter. Organizations, workspaces and stored provider
credentials are real rows. No security, tenancy or billing check is mocked
away: the tests run the real meter, the real tenancy binding and the real
EvaluationRunner billing block.
"""

import asyncio
import enum
import importlib
import sys
import types
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic_eval.core.llm.llm import LLM

# Imported here, before any tripwire is set, so nothing their import does is
# taken for a managed call.
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from evaluations.engine import instance as eval_instance
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import CellStatus
from model_hub.services import credit_ledger_port as cl
from model_hub.tests.test_model_route_support import (
    ANTHROPIC_CHAT_MODEL,
    BYOK_FIXTURE_VALUE,
    MESSAGES,
    OPENAI_CHAT_MODEL,
    PRIVATE_UPSTREAM_TEXT,
    FakeGatewayClient as _BaseFakeGatewayClient,
    GatewayStatusError,
    GatewayUsage,
    chat_response,
    eligible_port,
    gateway_refusal,
    provider_tripwires,
    sent_request_ids,
    stream_chunk,
)
from model_hub.views import eval_runner
from tfc.utils.error_codes import get_error_message, get_specific_error_message, get_usage_error_code

pytestmark = pytest.mark.django_db(transaction=True)

PER_RUN_FEE = 0.25
AFTER_DISPATCH_KEY = "MANAGED_BILLING_UNAVAILABLE_AFTER_DISPATCH"
GATEWAY_RESPONSE_ID = "response-r10"


class FakeGatewayClient(_BaseFakeGatewayClient):
    """The shared gateway double plus what the real gateway sends on every
    response: an x-agentcc-request-id RESPONSE header (r10: the only reconcile
    key; a response without it is R11, never a provisional debit). A test that
    supplies its own value (any header case) keeps it; ``keyed=False`` models a
    response that never carried one."""

    def __init__(self, *, keyed=True, response_headers=None, **kwargs):
        headers = dict(response_headers or {})
        if keyed and not any(isinstance(key, str) and key.lower() == "x-agentcc-request-id" for key in headers):
            headers["x-agentcc-request-id"] = GATEWAY_RESPONSE_ID
        super().__init__(response_headers=headers, **kwargs)


@pytest.fixture
def mr(monkeypatch):
    """The model-route module with no port installed, a fresh meter safety
    state, and a deployment without the legacy combined billing (a test that
    needs it installed says so)."""
    module = importlib.import_module("model_hub.services.model_route")
    cl.reset_meter_safety()
    monkeypatch.setattr(cl, "legacy_billing_installed", lambda: False)
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", None)
    with module.managed_routing_port(None):
        yield module
    cl.reset_meter_safety()


@pytest.fixture(autouse=True)
def _no_clickhouse_usage_log():
    with patch("agentic_eval.core.llm.llm.log_to_clickhouse"):
        yield


@pytest.fixture
def tripped(monkeypatch):
    return provider_tripwires(monkeypatch)


class _UsageEvent:
    def __init__(self, org_id, event_type, amount=0, properties=None, **kwargs):
        self.org_id = org_id
        self.event_type = event_type
        self.amount = amount
        self.properties = dict(properties or {})


class _BillingEventType(enum.Enum):
    CODE_EVALUATOR = "code_evaluator"


class _BillingConfig:
    @classmethod
    def get(cls):
        return cls()

    def get_eval_per_run_fee(self):
        return PER_RUN_FEE

    def calculate_ai_credits(self, cost_usd):
        return float(cost_usd or 0) * 100.0


@pytest.fixture
def billing(monkeypatch):
    """A double of the EE cost-based usage emitter: every UsageEvent that
    EvaluationRunner emits is captured, with a non-zero per-run fee."""
    events = []

    def install(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    install("ee.usage.schemas.events", UsageEvent=_UsageEvent)
    install("ee.usage.schemas.event_types", BillingEventType=_BillingEventType)
    install("ee.usage.services.config", BillingConfig=_BillingConfig)
    install("ee.usage.services.emitter", emit=events.append)
    install(
        "ee.usage.utils.event_properties",
        token_usage_properties=lambda usage: {
            "total_tokens": (usage or {}).get("total_tokens", 0)
        },
    )
    return events


def _ops(ledger):
    return [entry[0] for entry in ledger.calls]


def _row(workspace):
    return SimpleNamespace(id=uuid.uuid4(), dataset=SimpleNamespace(workspace=workspace))


def _runner(organization, workspace, run_evaluation):
    """An EvaluationRunner around one row: the real _process_eval_result and
    its billing block; the dataset plumbing around them is stubbed."""
    runner = eval_runner.EvaluationRunner.__new__(eval_runner.EvaluationRunner)
    runner.replace_column_id = None
    template = SimpleNamespace(
        id="one-key-judge-template", eval_type="llm", error_localizer_enabled=False
    )
    runner.user_eval_metric = SimpleNamespace(
        id="one-key-metric",
        name="judge",
        model=OPENAI_CHAT_MODEL,
        config={},
        organization=organization,
        template=template,
        error_localizer=False,
    )
    runner.user_eval_metric_id = "one-key-metric"
    runner.eval_template = template
    runner.organization_id = organization.id
    runner.workspace_id = workspace.id
    runner.source = "dataset_evaluation"
    runner.source_id = "one-key-dataset"
    runner.source_configs = {}
    runner.experiment_dataset = None
    runner.optimize = None
    runner.futureagi_eval = False
    runner.legacy_billing_seen = []

    def handle_api_call(row, mappings, config):
        # What the real _handle_api_call sees: the legacy combined billing.
        runner.legacy_billing_seen.append(
            eval_runner.log_and_deduct_cost_for_api_request
        )
        return None

    runner.update_config_list_values = lambda config, row=None: {}
    runner._handle_api_call = handle_api_call
    runner._run_evaluation = run_evaluation
    runner._format_response = lambda eval_result, partial_input_warning=None: {
        "output": eval_result,
        "reason": "The answer is polite.",
    }
    runner.format_output = lambda response, row=None: response["output"]
    return runner


def _judge_binding(mr, organization, workspace, operation=None):
    return mr.resolve_model_route(
        organization=organization,
        workspace=workspace,
        model_name=OPENAI_CHAT_MODEL,
        operation=operation or mr.OP_CUSTOM_PROMPT_EVAL,
    )


def _managed_judge_run(binding, judges):
    """A _run_evaluation whose judge makes one managed call through the real
    LLM wrapper, model route and standalone transport meter."""

    def run_evaluation(row, mappings, config):
        judge = LLM(
            provider="openai",
            model_name=binding.model_name,
            temperature=0.0,
            max_tokens=64,
            model_route=binding,
        )
        judge.call_llm(prompt=MESSAGES, provider="openai")
        judges.append(judge)
        return "Passed", None, None, None, judge, None

    return run_evaluation


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


class _RecordingEvaluator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _build_judge(organization, workspace, *, managed_judge_billing=None):
    resolved = SimpleNamespace(model=None, prompt_messages=[], pass_threshold=None)
    with patch.object(eval_instance, "resolve_version", return_value=resolved):
        evaluator, _ = eval_instance.create_eval_instance(
            eval_class=_RecordingEvaluator,
            eval_template=_judge_template(organization, workspace, OPENAI_CHAT_MODEL),
            config={},
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


def _run_prompt(organization, workspace, model=ANTHROPIC_CHAT_MODEL):
    return RunPrompt(
        model=model,
        messages=list(MESSAGES),
        organization_id=organization.id,
        output_format="string",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        tools=None,
        workspace_id=workspace.id,
    )


def _workbench_run(run_prompt):
    # prompt_template.py's synchronous run: (streaming, template id, version,
    # index, max index, run type).
    return run_prompt.litellm_response(False, "one-key-template", None, 0, 1, "run_prompt")


class TestManagedJudgeIsOneDebit:
    """P1: one managed judge call -> exactly one LLM-cost debit."""

    def test_one_managed_judge_call_is_one_llm_cost_debit(
        self, mr, organization, workspace, billing, tripped
    ):
        client = FakeGatewayClient(response=chat_response('{"result": "Passed"}'))
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        judges = []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace)
            runner = _runner(organization, workspace, _managed_judge_run(binding, judges))
            response, status, value = runner._process_eval_result(_row(workspace), {})

        assert status == CellStatus.PASS.value
        assert value == "Passed"
        # The one LLM-cost debit: the settle of the judge call's own hold.
        assert _ops(ledger) == ["hold", "settle"]
        assert [hold.debits for hold in ledger.holds.values()] == [1]
        # No legacy debit ran and no evaluator UsageEvent bills the provider
        # cost again (the contract defines no separate per-run fee).
        assert runner.legacy_billing_seen == [None]
        assert billing == []
        # The evaluator usage carries the settled request id.
        settled = response["credit_settlement"]
        assert [entry["request_id"] for entry in settled] == [
            port.authorize_calls[0]["request_id"]
        ]
        assert sent_request_ids(client) == [settled[0]["request_id"]]
        assert settled[0]["status"] == "settled"
        assert settled[0]["payer"] == cl.PAYER_CREDITS
        assert judges[0].token_usage["total_tokens"] == 3
        assert tripped == []

    def test_a_separate_per_run_fee_is_the_only_evaluator_amount(
        self, mr, organization, workspace, billing, monkeypatch
    ):
        monkeypatch.setattr(eval_runner, "MANAGED_JUDGE_PER_RUN_FEE_SEPARATE", True)
        client = FakeGatewayClient(response=chat_response('{"result": "Passed"}'))
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace)
            runner = _runner(organization, workspace, _managed_judge_run(binding, []))
            runner._process_eval_result(_row(workspace), {})

        assert _ops(ledger) == ["hold", "settle"]
        assert len(billing) == 1
        event = billing[0]
        assert event.amount == pytest.approx(PER_RUN_FEE * 100.0)
        assert event.properties["raw_cost_usd"] == str(PER_RUN_FEE)
        assert event.properties["billed_component"] == "eval_per_run_fee"
        assert event.properties["payer"] == cl.PAYER_CREDITS
        assert event.properties["dsx_settled_request_ids"] == (
            port.authorize_calls[0]["request_id"]
        )
        assert "total_tokens" not in event.properties

    def test_byok_judge_keeps_its_cost_based_event(
        self, mr, organization, workspace, billing
    ):
        byok_judge = SimpleNamespace(
            model_route=None,
            cost={"total_cost": 0.01, "prompt_cost": 0.006, "completion_cost": 0.004},
            token_usage={"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1},
        )
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            runner = _runner(
                organization,
                workspace,
                lambda row, mappings, config: ("Passed", None, None, None, byok_judge, None),
            )
            response, status, _ = runner._process_eval_result(_row(workspace), {})

        assert status == CellStatus.PASS.value
        assert ledger.calls == []
        assert len(billing) == 1
        event = billing[0]
        assert event.amount == pytest.approx((0.01 + PER_RUN_FEE) * 100.0)
        assert event.properties["raw_cost_usd"] == str(0.01 + PER_RUN_FEE)
        assert event.properties["total_tokens"] == 3
        assert "credit_settlement" not in response


class TestManagedJudgeCut:
    """A managed judge exists only where its evaluation is billed once."""

    def test_runner_opts_in_only_without_the_legacy_debit(
        self, mr, organization, workspace, monkeypatch
    ):
        runner = _runner(organization, workspace, None)
        assert runner._managed_judge_billing() == eval_instance.MANAGED_JUDGE_SINGLE_METER
        monkeypatch.setattr(
            eval_runner, "log_and_deduct_cost_for_api_request", lambda *a, **k: None
        )
        assert runner._managed_judge_billing() is None

    def test_the_single_meter_opt_in_binds_the_managed_judge(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            kwargs = _build_judge(
                organization,
                workspace,
                managed_judge_billing=eval_instance.MANAGED_JUDGE_SINGLE_METER,
            )
        assert mr.is_managed_route_binding(kwargs["model_route"])
        assert kwargs["api_key"] is None
        assert port.authorize_calls == []

    def test_a_caller_without_single_meter_billing_is_refused_before_dispatch(
        self, mr, organization, workspace
    ):
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            with pytest.raises(cl.ManagedDispatchUnavailableError) as refused:
                _build_judge(organization, workspace)

        assert str(refused.value) == cl.MANAGED_JUDGE_CUT_MESSAGE
        assert get_specific_error_message(refused.value) == cl.MANAGED_JUDGE_CUT_MESSAGE
        assert cl.is_terminal_refusal(refused.value)
        assert port.authorize_calls == []
        assert ledger.calls == []

    def test_legacy_debit_installed_cuts_the_runner_judge(
        self, mr, organization, workspace, monkeypatch
    ):
        monkeypatch.setattr(
            eval_runner, "log_and_deduct_cost_for_api_request", lambda *a, **k: None
        )
        runner = _runner(organization, workspace, None)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            with pytest.raises(cl.ManagedDispatchUnavailableError):
                _build_judge(
                    organization,
                    workspace,
                    managed_judge_billing=runner._managed_judge_billing(),
                )
        assert port.authorize_calls == []
        assert ledger.calls == []

    def test_a_stored_key_is_never_cut(self, mr, organization, workspace, user):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            kwargs = _build_judge(organization, workspace)
        assert kwargs["api_key"] == BYOK_FIXTURE_VALUE
        assert "model_route" not in kwargs
        assert port.authorize_calls == []

    def test_standalone_judge_dispatch_is_refused_while_legacy_billing_is_installed(
        self, mr, organization, workspace, monkeypatch
    ):
        monkeypatch.setattr(cl, "legacy_billing_installed", lambda: True)
        client = FakeGatewayClient()
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace)
            with pytest.raises(cl.ManagedDispatchUnavailableError) as refused:
                mr.managed_chat_completion(
                    binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}
                )
        assert str(refused.value) == cl.MANAGED_JUDGE_CUT_MESSAGE
        assert client.calls == []
        assert port.authorize_calls == []
        assert ledger.calls == []

    def test_helpers_keep_their_single_transport_meter(
        self, mr, organization, workspace, monkeypatch
    ):
        monkeypatch.setattr(cl, "legacy_billing_installed", lambda: True)
        client = FakeGatewayClient(response=chat_response("helper ok"))
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(
                mr, organization, workspace, operation=mr.OP_SMART_FILTER
            )
            mr.managed_chat_completion(
                binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}
            )
        assert _ops(ledger) == ["hold", "settle"]
        assert len(client.calls) == 1


class TestPromptWorkbenchCut:
    """The prompt workbench's synchronous run debits legacy billing after the
    call; a managed binding there is refused unless an outer meter holds it."""

    def test_managed_template_run_without_an_outer_meter_is_refused(
        self, mr, organization, workspace, tripped
    ):
        client = FakeGatewayClient(response=chat_response("managed ok"))
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            with pytest.raises(cl.ManagedDispatchUnavailableError) as refused:
                _workbench_run(_run_prompt(organization, workspace))

        assert type(refused.value) is cl.ManagedDispatchUnavailableError
        assert str(refused.value) == cl.MANAGED_PROMPT_CUT_MESSAGE
        assert get_specific_error_message(refused.value, True) == (
            cl.MANAGED_PROMPT_CUT_MESSAGE
        )
        assert client.calls == []
        assert port.authorize_calls == []
        assert ledger.calls == []
        assert tripped == []

    def test_template_run_under_an_outer_meter_is_one_debit(
        self, mr, organization, workspace, tripped
    ):
        gateway_response = chat_response("one debit", model=ANTHROPIC_CHAT_MODEL)
        gateway_response.usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        client = FakeGatewayClient(
            response=gateway_response,
            response_headers={"x-agentcc-request-id": GATEWAY_RESPONSE_ID},
        )
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            meter = cl.call_meter(
                organization=organization,
                workspace=workspace,
                model_name=ANTHROPIC_CHAT_MODEL,
                purpose=cl.PURPOSE_PROMPT_PLAYGROUND,
                source="prompt_playground",
                source_id="one-key-template",
            )
            with meter.metered(amount_estimate="0.1") as call:
                response, value_info = _workbench_run(_run_prompt(organization, workspace))
                # An outer meter owns usage observation, as in observe_value_info.
                call.observe(value_info=value_info)

        assert response == "one debit"
        assert _ops(ledger) == ["hold", "settle"]
        assert call.dispatch_count == 1
        assert sent_request_ids(client) == [call.request_id]
        assert tripped == []
        hold = next(iter(ledger.holds.values()))
        assert hold.debits == 1
        assert Decimal("0") < hold.settled_amount < Decimal("0.1")
        assert hold.settled_amount == cl.cost_from_value_info(value_info)
        row = ledger.get_hold(hold.hold_id)
        assert row["settlement_source"] == "provider_usage"
        assert row["gateway_request_id"] == GATEWAY_RESPONSE_ID

    def test_byok_template_run_is_unchanged(
        self, mr, organization, workspace, user, monkeypatch
    ):
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
            response, _ = _workbench_run(_run_prompt(organization, workspace))

        assert response == "byok ok"
        assert seen[0]["api_key"] == BYOK_FIXTURE_VALUE
        assert client.calls == []
        assert port.authorize_calls == []


def _org():
    return SimpleNamespace(id=uuid.uuid4())


def _ws(org):
    return SimpleNamespace(id=uuid.uuid4(), organization_id=org.id)


def _meter(org, ws):
    return cl.CallMeter(
        organization=org,
        workspace=ws,
        route=cl.PayerRoute.managed(provider="openai"),
        envelope=cl.build_envelope(
            organization=org,
            workspace=ws,
            purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW,
            source="run_prompt_preview",
            source_id="preview-1",
            model="gpt-4o",
            row_id="row-1",
        ),
    )


def _raise_at_gateway_boundary(error):
    cl.note_gateway_failure(cl.current_managed_call(), error)
    raise error


class TestChargedBillingFailureCopy:
    """A charged post-dispatch billing failure never says 'not run'."""

    def test_claimed_billing_failure_says_it_may_have_run(self, mr):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
                with _meter(org, _ws(org)).metered(amount_estimate="0.1"):
                    cl.require_managed_hold(model_name="gpt-4o")
                    _raise_at_gateway_boundary(gateway_refusal(
                        503, "managed_billing_unavailable", error_type="server_error"
                    ))

        # The unknown outcome keeps the reservation open without charging.
        assert _ops(ledger) == ["hold"]
        assert ledger.balance_of(org) == Decimal("1")
        error = caught.value
        assert error.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert error.dispatched is None
        assert error.retryable is False
        assert error.message == get_error_message("MANAGED_OUTCOME_PENDING_RECONCILE")
        assert "not run" not in error.message
        assert PRIVATE_UPSTREAM_TEXT not in error.message
        assert get_specific_error_message(error) == error.message
        assert get_specific_error_message(error, True) == error.message
        assert cl.refusal_cell_info(error)["reason"] == error.message
        assert cl.refusal_message(error) == error.message

    @pytest.mark.parametrize(
        "status, code, copy_key",
        [
            (429, "managed_spend_velocity", "MANAGED_SPEND_VELOCITY"),
            (429, "rate_limit_exceeded", "RATE_LIMIT_REACHED"),
        ],
    )
    def test_claimed_backoff_refusal_is_released_uncharged_and_terminal(
        self, mr, status, code, copy_key
    ):
        """A refused call is never charged; it is still
        never retried automatically."""
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedRefusedAfterDispatchError) as caught:
                with _meter(org, _ws(org)).metered(amount_estimate="0.1"):
                    cl.require_managed_hold(model_name="gpt-4o")
                    _raise_at_gateway_boundary(gateway_refusal(
                        status, code, error_type="rate_limit_error",
                        headers={"Retry-After": "5"},
                    ))

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1")
        error = caught.value
        assert error.code == code
        assert error.charged is False
        assert error.dispatched is False
        assert error.retryable is False
        assert error.retry_after is None
        assert error.message == get_error_message(copy_key)
        assert "charged" not in error.message
        assert PRIVATE_UPSTREAM_TEXT not in error.message
        assert cl.refusal_message(error) == error.message

    def test_unclaimed_billing_failure_keeps_the_not_run_copy(self, mr):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        ledger.refuse_next_holds = [cl.refusal_for_code("managed_billing_unavailable")]
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditLedgerUnavailableError) as caught:
                with _meter(org, _ws(org)).metered(amount_estimate="0.1"):
                    pytest.fail("dispatched without a hold")

        assert type(caught.value) is cl.CreditLedgerUnavailableError
        assert caught.value.dispatched is False
        assert caught.value.retryable is True
        assert get_specific_error_message(caught.value) == get_error_message(
            "MANAGED_BILLING_UNAVAILABLE"
        )
        assert _ops(ledger) == ["hold"]

    def test_the_standalone_transport_marks_a_charged_failure(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient(
            error=gateway_refusal(
                503, "managed_billing_unavailable", error_type="server_error"
            )
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(
                mr, organization, workspace, operation=mr.OP_SMART_FILTER
            )
            with pytest.raises(mr.ManagedRouteCallFailed) as refused:
                mr.managed_chat_completion(
                    binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}
                )

        assert _ops(ledger) == ["hold"]
        assert refused.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert refused.value.dispatched is None
        # Charged once, so never offered as an automatic retry.
        assert refused.value.retryable is False
        assert refused.value.retry_after is None
        # The evaluator re-raises a judge failure as ValueError(str(e)) from e.
        try:
            raise ValueError(str(refused.value)) from refused.value
        except ValueError as wrapped:
            message = get_specific_error_message(wrapped)
        assert message == get_error_message("MANAGED_OUTCOME_PENDING_RECONCILE")
        assert "not run" not in message


class TestRefusedTransportCall:
    """The standalone transport's claimed
    gateway refusal is released (not charged) and is not retryable."""

    def test_the_standalone_transport_releases_a_refused_call(
        self, mr, organization, workspace
    ):
        client = FakeGatewayClient(
            error=gateway_refusal(
                429, "managed_spend_velocity", error_type="rate_limit_error",
                headers={"Retry-After": "7"},
            )
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(
                mr, organization, workspace, operation=mr.OP_SMART_FILTER
            )
            with pytest.raises(mr.ManagedRouteRefused) as refused:
                mr.managed_chat_completion(
                    binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}
                )

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("1")
        assert len(client.calls) == 1
        assert refused.value.code == "managed_spend_velocity"
        assert getattr(refused.value, "dispatched", False) is False
        assert refused.value.retryable is False
        assert refused.value.retry_after is None
        assert get_specific_error_message(refused.value) == get_error_message(
            "MANAGED_SPEND_VELOCITY"
        )


class TestBoundedAmounts:
    """COR-02: the exponent is bounded before any context-sensitive arithmetic."""

    @pytest.mark.parametrize(
        "value",
        [
            "1E1000000",
            "-1E1000000",
            "1E-1000000",
            Decimal("1E1000000"),
            "1E999999",
            "1E13",
            "NaN",
            "Infinity",
        ],
    )
    def test_out_of_range_amounts_are_dropped(self, value):
        assert cl._to_decimal(value) is None

    def test_in_range_amounts_are_kept(self):
        assert cl._to_decimal("0.04") == Decimal("0.04")
        assert cl._to_decimal(0) == Decimal("0")
        assert cl._to_decimal("999999999") == Decimal("999999999")
        assert cl._to_decimal("1E9") is None

    def test_a_refusal_with_a_huge_amount_still_renders(self):
        refusal = cl.InsufficientCreditError(
            metadata={"required_usd": "1E1000000", "available_usd": "0.01"}
        )
        body = refusal.as_response_body()
        assert body["required"] is None
        assert body["metadata"]["required_usd"] is None
        assert body["available"] == "0.01"
        assert get_specific_error_message(refusal) == get_error_message(
            "MANAGED_CREDITS_INSUFFICIENT"
        )


# Expected billing is explicit in every case, not computed with the production predicate.
MONEY_FAILURES = [
    (402, 'managed_credits_exhausted', None, False),
    (402, 'managed_credits_insufficient', None, False),
    (402, 'agent_budget_exhausted', None, False),
    (429, 'budget_exceeded', None, False),
    (429, 'org_budget_exceeded', None, False),
    (429, 'managed_spend_velocity', None, False),
    (429, 'rate_limit_exceeded', None, False),
    (403, 'permission_denied', None, False),
    (403, 'future_agi_credits_off', None, False),
    (401, 'invalid_api_key', None, False),
    (402, 'agent_call_cap_exceeded', None, False),
    (402, 'call_cap_unenforceable', None, False),
    (402, 'agent_run_stopped', None, False),
    (503, 'budget_state_unavailable', None, False),
    (402, 'managed_spend_paused', None, False),
    (402, 'payment_required', None, False),
    (429, 'budget_throttled', None, False),
    (503, 'key_list_not_loaded', None, False),
    (403, 'model_not_permitted', None, False),
    (403, 'managed_router_unbounded', None, False),
    (400, 'unsupported_field_for_managed', None, False),
    (400, 'unsupported_routing_field', None, False),
    (403, 'key_scope_denied', None, False),
    (402, 'agent_key_budget_exhausted', None, False),
    (429, 'token_rate_limit_exceeded', None, False),
    (429, 'usage_paused', None, False),
    (429, 'quota_exceeded', None, False),
    (400, 'usage_unavailable', None, False),
    (503, 'provider_busy', None, False),
    (409, 'idempotency_in_progress', None, False),
    (422, 'idempotency_key_reused', None, False),
    (404, 'managed_models_disabled', None, False),
    (400, 'deferred_run_unavailable', None, False),
    (404, 'model_not_found', None, False),
    (410, 'model_expired', None, False),
    (404, 'no_provider_matches', None, False),
    (400, 'provider_preference_unavailable', None, False),
    (400, 'model_ambiguous', None, False),
    (400, 'no_auto_candidate', None, False),
    (400, 'omega_default_unroutable', None, False),
    (400, 'route_hint_unsupported', None, False),
    (400, 'routing_field_not_yet_supported', None, False),
    (400, 'invalid_provider_preference', None, False),
    (400, 'invalid_routing_field', None, False),
    (400, 'extra_body_conflict', None, False),
    (400, 'invalid_extra_body', None, False),
    (404, 'provider_not_found', None, False),
    (402, 'INSUFFICIENT_CREDIT', None, None),
    (401, 'unknown', None, None),
    (403, 'unknown', None, None),
    (401, None, None, None),
    (403, None, None, None),
    (503, 'managed_billing_unavailable', {'x-agentcc-dispatched': 'false'}, False),
    (503, 'unknown', {'x-agentcc-dispatched': 'false'}, False),
    (503, 'managed_billing_unavailable', {'x-agentcc-dispatched': 'true'}, None),
    (429, 'managed_spend_velocity', {'x-agentcc-dispatched': 'true'}, None),
    (503, 'managed_billing_unavailable', None, None),
    (504, 'gateway_timeout', None, None),
    (500, 'invalid_api_key', None, None),
    (502, 'provider_busy', None, None),
    (400, 'unknown', None, None),
    (429, None, None, None),
    (503, None, None, None),
    (None, None, None, None),
    (200, 'rate_limit_exceeded', None, None),
    (500, 'rate_limit_exceeded', None, None),
    (403, 'rate_limit_exceeded', None, None),
    (401, 'permission_denied', None, None),
    (None, 'rate_limit_exceeded', None, None),
    (500, 'invalid_api_key', {'x-agentcc-dispatched': 'false'}, False),
    (401, 'invalid_api_key', {'x-agentcc-dispatched': 'true'}, None),
]


class TestTrustedTransportMoney:
    @pytest.fixture(autouse=True)
    def deterministic_hold_and_no_sleep(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        self.sleeps = []
        monkeypatch.setattr(cl, "_sleep", self.sleeps.append)
        async def no_sleep(delay):
            self.sleeps.append(delay)
        monkeypatch.setattr(cl, "_asleep", no_sleep)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("delivery", ["response", "stream_create", "stream_next"])
    @pytest.mark.parametrize("outer", [False, True])
    @pytest.mark.parametrize("status, code, headers, charged", MONEY_FAILURES)
    def test_sdk_evidence_controls_exactly_one_terminal_outcome(
        self, mr, organization, workspace, is_async, delivery, outer, status, code, headers, charged
    ):
        raw = TimeoutError(PRIVATE_UPSTREAM_TEXT) if status is None and code is None else (
            GatewayStatusError(status, {}, headers=headers) if code is None else gateway_refusal(status, code, headers=headers)
        )
        client = FakeGatewayClient(
            is_async=is_async, error=raw if delivery != "stream_next" else None,
            stream_error=raw if delivery == "stream_next" else None,
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        request = {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES, "stream": delivery != "response"}
        calls = []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = cl.call_meter(
                organization=organization, workspace=workspace, model_name=OPENAI_CHAT_MODEL,
                purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="money", source_id="terminal",
            ) if outer else None
            def dispatch():
                if outer:
                    calls.append(cl.current_managed_call())
                response = mr.managed_chat_completion(binding, request)
                return list(response) if request["stream"] else response
            async def adispatch():
                if outer:
                    calls.append(cl.current_managed_call())
                response = await mr.managed_chat_completion_async(binding, request)
                return [chunk async for chunk in response] if request["stream"] else response
            with pytest.raises(cl.ManagedCreditRefusal if outer else mr.ModelRouteError) as caught:
                if is_async:
                    asyncio.run(cl.arun_metered(meter, adispatch) if outer else adispatch())
                elif outer:
                    cl.run_metered(meter, dispatch)
                else:
                    dispatch()
        error = caught.value
        assert _ops(ledger) == (["hold"] if charged is None else ["hold", "release"])
        assert ledger.balance_of(organization) == Decimal("0.90" if charged else "1")
        assert ledger.held_of(organization) == (Decimal("0.10") if charged is None else 0)
        assert [hold.debits for hold in ledger.holds.values()] == [1 if charged else 0]
        assert len(client.calls) == len(port.authorize_calls) == 1
        assert self.sleeps == []
        assert error.retryable is False
        assert error.retry_after is None
        assert error.dispatched is charged
        assert error.charged is charged
        assert PRIVATE_UPSTREAM_TEXT not in str(error)
        body = error.as_response_body() if outer else error.response_body()
        assert str(error) == error.message  # The frozen meter's exception is unchanged.
        if charged is None:
            assert body["message"] == error.message
        elif code == "unknown" or (status, code) == (500, "invalid_api_key"):
            assert body["code"] == "MANAGED_ROUTE_CALL_FAILED"
            assert body["category"] == "generic"
            assert body["message"] == get_error_message("MANAGED_REFUSAL_UNKNOWN")
        else:
            assert (body["status"], body["code"]) == (status, code)
            assert body["message"] == get_specific_error_message(error, True)
            if code == "invalid_api_key":
                assert body["category"] == "invalid_key"
                assert body["message"] == get_error_message("MANAGED_INVALID_GATEWAY_KEY")
        if outer:
            assert cl.refusal_cell_info(error) == {"reason": body["message"], "error": body}
        assert PRIVATE_UPSTREAM_TEXT not in repr(body)
        assert body["retryable"] is False
        assert body["retry_after"] is None
        if charged is None:
            assert error.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
            assert "provider's usage record" in error.message
            assert get_specific_error_message(error, True) == error.message
        if outer:
            assert isinstance(error, ValueError)
            assert cl.refusal_activity_error(error)["non_retryable"] is True
            assert calls[0].dispatch_count == 1
            assert calls[0].output_seen is False
            expected_attestation = (headers["x-agentcc-dispatched"] == "true") if headers else None
            assert calls[0].gateway_dispatched is expected_attestation
            if charged is not None and code in (None, "unknown"):
                assert error.code == ("MANAGED_CALL_FAILED_AFTER_DISPATCH" if charged else "MANAGED_CALL_REFUSED")
        else:
            assert error.__cause__ is None
            assert error.__context__ is None
            assert getattr(error, "remedy", None) != "retry"
            assert getattr(error, "details", {}).get("remedy_hint") != "retry"

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("status, code", [
        (402, "managed_credits_exhausted"), (402, "managed_credits_insufficient"),
        (402, "agent_budget_exhausted"), (429, "budget_exceeded"),
        (429, "org_budget_exceeded"), (429, "managed_spend_velocity"),
        (429, "rate_limit_exceeded"), (403, "permission_denied"),
        (503, "managed_billing_unavailable"),
    ])
    def test_outer_stream_contradiction_is_pending_across_context_handoff(
        self, mr, organization, workspace, is_async, status, code
    ):
        raw = gateway_refusal(status, code, headers={"x-agentcc-dispatched": "false"})
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("partial")], stream_error=raw)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        calls, chunks = [], []
        request = {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES, "stream": True}
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = cl.call_meter(organization=organization, workspace=workspace,
                                  model_name=OPENAI_CHAT_MODEL, purpose=cl.PURPOSE_PROMPT_PLAYGROUND,
                                  source="money", source_id="stream")
            def run():
                with meter.metered() as call:
                    calls.append(call)
                    stream = mr.managed_chat_completion(binding, request)
                    token = cl._ACTIVE_CALL.set(None)
                    try:
                        chunks.append(next(stream))
                        assert call.output_seen is True
                        next(stream)
                    finally:
                        cl._ACTIVE_CALL.reset(token)
            async def arun():
                async with meter.ametered() as call:
                    calls.append(call)
                    stream = await mr.managed_chat_completion_async(binding, request)
                    token = cl._ACTIVE_CALL.set(None)
                    try:
                        chunks.append(await stream.__anext__())
                        assert call.output_seen is True
                        await stream.__anext__()
                    finally:
                        cl._ACTIVE_CALL.reset(token)
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                asyncio.run(arun()) if is_async else run()
        assert len(chunks) == 1
        assert _ops(ledger) == ["hold"]
        assert ledger.balance_of(organization) == Decimal("1")
        assert calls[0].gateway_dispatched is False
        assert calls[0].output_seen is True
        assert caught.value.dispatched is None
        assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert "provider's usage record" in caught.value.message
        assert caught.value.retryable is False
        assert len(client.calls) == 1

    @pytest.mark.parametrize("is_async", [False, True])
    def test_nonstream_output_is_recorded_before_caller_validation(self, mr, organization, workspace, is_async):
        gateway_response = chat_response("managed ok", model=OPENAI_CHAT_MODEL)
        gateway_response.usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        client = FakeGatewayClient(
            is_async=is_async,
            response=gateway_response,
            response_headers={"x-agentcc-request-id": GATEWAY_RESPONSE_ID},
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = cl.call_meter(organization=organization, workspace=workspace,
                                  model_name=OPENAI_CHAT_MODEL, purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW,
                                  source="money", source_id="postprocess")
            def reject():
                assert cl.current_managed_call().output_seen is True
                raise cl.managed_refusal_from(ValueError("{'code': 'managed_credits_exhausted'}"))
            def run():
                with meter.metered():
                    mr.managed_chat_completion(binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES})
                    reject()
            async def arun():
                async with meter.ametered():
                    await mr.managed_chat_completion_async(binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES})
                    reject()
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                asyncio.run(arun()) if is_async else run()
        assert _ops(ledger) == ["hold", "settle"]
        assert caught.value.charged is True
        assert "credits were charged" in caught.value.message


class TestMoneyR4Transport:
    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02"))

    def _invoke(self, mr, binding, is_async, streaming):
        request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
        async def run():
            result = await mr.managed_chat_completion_async(binding, request)
            return [chunk async for chunk in result] if streaming else result
        if is_async:
            return asyncio.run(run())
        result = mr.managed_chat_completion(binding, request)
        return list(result) if streaming else result

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("lost_ack", [False, True])
    def test_settlement_failure_never_escapes_as_retryable(self, mr, organization, workspace, is_async, streaming, lost_ack):
        class LostAckLedger(cl.MockCreditLedger):
            idempotent_settle = False
            def settle(self, hold_id, amount, **metadata):
                super().settle(hold_id, amount, **metadata)
                raise ConnectionError(PRIVATE_UPSTREAM_TEXT)
        ledger = (LostAckLedger if lost_ack else cl.MockCreditLedger)(default_balance="1")
        if not lost_ack:
            ledger.fail_next_settles = cl.SETTLE_ATTEMPTS
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("output")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                for attempt in cl.ManagedAttempts(True):
                    with attempt:
                        self._invoke(mr, binding, is_async, streaming)
        error = caught.value
        assert isinstance(error, ValueError)
        assert error.code == "CREDIT_SETTLEMENT_FAILED"
        assert error.retryable is False and error.retry_after is None
        assert error.charged is None and error.dispatched is None
        assert "charge is not yet confirmed" in error.message
        assert "were charged" not in error.message and "not run" not in error.message
        assert PRIVATE_UPSTREAM_TEXT not in error.message
        assert error.__cause__ is None and error.__context__ is None
        assert len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert _ops(ledger).count("settle") == (1 if lost_ack else cl.SETTLE_ATTEMPTS)
        assert [h.debits for h in ledger.holds.values()] == [1 if lost_ack else 0]
        assert "release" not in _ops(ledger)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("path", ["success", "error_header", "error_metadata"])
    def test_gateway_response_id_not_client_id(self, mr, organization, workspace, is_async, streaming, path):
        gateway_id = "01JGWRESPONSE00000000000000"
        raw = None if path == "success" else gateway_refusal(504, "gateway_timeout", metadata={"request_id": gateway_id if path == "error_metadata" else "ignored-metadata-id"}, headers={} if path == "error_metadata" else {"X-AgentCC-Request-ID": gateway_id})
        client = FakeGatewayClient(is_async=is_async, error=raw, stream_chunks=[stream_chunk("output")], response_headers={"x-agentcc-request-id": gateway_id})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        calls = []
        real_open = cl._HoldSession.open
        def capture(session):
            call = real_open(session)
            calls.append(call)
            return call
        with patch.object(cl._HoldSession, "open", capture), cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            if path == "success":
                self._invoke(mr, binding, is_async, streaming)
            else:
                with pytest.raises(mr.ModelRouteError):
                    self._invoke(mr, binding, is_async, streaming)
        call = calls[0]
        expected_gateway_id = None if path == "error_metadata" else gateway_id
        assert call.gateway_request_id == expected_gateway_id
        assert call.gateway_request_id != call.request_id
        headers = client.calls[0]["kwargs"]["extra_headers"]
        assert headers["x-agentcc-request-id"] == call.request_id
        assert "x-request-id" not in {key.lower() for key in headers}
        if path != "success":
            assert ledger.get_hold(call.hold_id)["gateway_request_id"] == expected_gateway_id

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("attested, expected", [(None, "settle"), (True, "settle"), (False, "pending")])
    def test_output_precedence_and_contradiction_alert(self, mr, organization, workspace, monkeypatch, is_async, attested, expected):
        gateway_id = "response-stream-id"
        raw = gateway_refusal(429, "rate_limit_exceeded", headers={} if attested is None else {"x-agentcc-dispatched": str(attested).lower()})
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("partial")], stream_error=raw, response_headers={"x-agentcc-request-id": gateway_id})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        events = []
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: events.append((event, kw)))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                self._invoke(mr, binding, is_async, True)
        assert _ops(ledger) == (["hold", "settle"] if expected == "settle" else ["hold"])
        assert ledger.balance_of(organization) == Decimal("0.98" if expected == "settle" else "1")
        contradictions = [(event, kw) for event, kw in events if event == "credit_dispatch_contradiction"]
        if expected == "pending":
            assert contradictions == [("credit_dispatch_contradiction", dict(organization_id=str(organization.id), request_id=port.authorize_calls[0]["request_id"], gateway_request_id=gateway_id))]
            assert caught.value.charged is None
        else:
            assert contradictions == []
            assert caught.value.charged is True
        assert caught.value.retryable is False

    @pytest.mark.parametrize("outer", [False, True])
    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    def test_released_inflight_copy_stays_inflight_without_retry(self, mr, organization, workspace, outer, is_async, streaming):
        raw = gateway_refusal(402, "managed_credits_insufficient", metadata={"limit_source": "agentcc_in_flight", "remedy_hint": "retry"}, headers={"Retry-After": "2"})
        client = FakeGatewayClient(is_async=is_async, error=raw)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            if outer:
                meter = cl.call_meter(organization=organization, workspace=workspace, model_name=OPENAI_CHAT_MODEL, purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="inflight", source_id="test")
                with pytest.raises(cl.ManagedCreditRefusal) as caught:
                    with meter.metered():
                        self._invoke(mr, binding, is_async, streaming)
            else:
                with pytest.raises(mr.ModelRouteError) as caught:
                    self._invoke(mr, binding, is_async, streaming)
        error = caught.value
        assert _ops(ledger) == ["hold", "release"]
        for mode in (False, True):
            assert get_specific_error_message(error, mode) == (
                "Other requests are holding your Future AGI credits right now. "
                "Wait at least 2 seconds before trying again."
            )
        assert get_usage_error_code(error) is None
        body = error.as_response_body() if outer else error.response_body()
        assert body["display_wait_seconds"] == 2
        assert body["message"] == get_specific_error_message(error, True)
        assert body["retryable"] is False and body["retry_after"] is None
        assert body["metadata"].get("remedy_hint") != "retry"
        assert body["metadata"]["limit_source"] == "agentcc_in_flight"
        assert body["remedy"] != "retry"
        if outer:
            assert cl.refusal_cell_info(error)["reason"] == body["message"]
        else:
            assert str(error) == body["message"]
        assert error.retryable is False and error.retry_after is None
        assert error.charged is False
        assert getattr(error, "metadata", {}).get("remedy_hint") != "retry"
        assert getattr(error, "details", {}).get("remedy_hint") != "retry"
        # Both normalizers must preserve the filter even without the later
        # standalone _charged_failure pass over the terminal copy.
        for terminal in (mr.managed_call_error(error), mr.managed_route_failure(error)):
            assert isinstance(terminal, mr.TerminalInsufficientCreditError)
            assert terminal.metadata == {"limit_source": "agentcc_in_flight"}
            assert terminal.details == terminal.metadata
            assert terminal.remedy != "retry"
            assert terminal.retryable is False and terminal.retry_after is None
            assert terminal.charged is False
            terminal_body = terminal.response_body()
            assert terminal_body["retryable"] is False and terminal_body["retry_after"] is None
            assert terminal_body["metadata"].get("remedy_hint") != "retry"
            assert terminal_body["remedy"] != "retry"
            assert terminal_body["display_wait_seconds"] == 2
            assert terminal_body["message"] == body["message"]
            # Serialization filters the response, not the canonical display.
            assert terminal.gateway_display["metadata"]["remedy_hint"] == "retry"
            assert terminal.gateway_display["remedy"] == "retry"

    def test_retryable_inflight_response_keeps_retry_advice(self, mr):
        raw = gateway_refusal(
            402, "managed_credits_insufficient",
            metadata={"limit_source": "agentcc_in_flight", "remedy_hint": "retry"},
            headers={"Retry-After": "2"},
        )
        route_error = mr.managed_call_error(raw)
        ledger_error = cl.managed_refusal_from(raw)
        assert isinstance(ledger_error, cl.CreditsInFlightError)
        # SDK-bound presentation before either refusal is made terminal.
        ledger_error.gateway_display = cl.managed_gateway_display(raw)
        for body in (route_error.response_body(), ledger_error.as_response_body()):
            assert body["retryable"] is True and body["retry_after"] == 2
            assert body["metadata"]["remedy_hint"] == "retry"
            assert body["metadata"]["limit_source"] == "agentcc_in_flight"
            assert body["remedy"] == "retry"
            assert body["display_wait_seconds"] == 2
            assert body["message"] == (
                "Other requests are holding your Future AGI credits right now. "
                "Wait at least 2 seconds before trying again."
            )


class TestRefusalRetryAdviceScope:
    @pytest.mark.parametrize("terminal", [False, True])
    @pytest.mark.parametrize("wait,expected_wait", [(None, None), ("0", None), ("NaN", None), ("600", 600)])
    def test_spend_guard_preserves_checked_retry_metadata(self, mr, terminal, wait, expected_wait):
        metadata = {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry"}
        raw = gateway_refusal(
            429, "managed_spend_velocity",
            metadata=dict(metadata, private="DO_NOT_FORWARD"),
            headers={} if wait is None else {"Retry-After": wait},
        )
        display = cl.managed_gateway_display(raw)
        ledger_error = cl.managed_refusal_from(raw)
        assert isinstance(ledger_error, cl.SpendVelocityError)
        if terminal:
            ledger_error = ledger_error.after_dispatch(charged=False)
        # Match the SDK-bound attachment used by hold and terminal meter paths.
        ledger_error.gateway_display = display
        before = (type(ledger_error), ledger_error.retryable, ledger_error.retry_after,
                  ledger_error.backoff, ledger_error.charged, ledger_error.dispatched,
                  dict(ledger_error.metadata))
        checked_display = dict(display, metadata=dict(display["metadata"]))
        route_errors = [mr.managed_call_error(ledger_error)]
        route_errors.append(mr.managed_route_failure(ledger_error) if terminal
                            else mr.managed_call_error(raw))
        bodies = [ledger_error.as_response_body()]
        for error in route_errors:
            assert error.metadata == metadata
            if terminal:
                assert isinstance(error, mr.TerminalManagedRouteRefused)
                assert error.details == metadata
                assert error.retryable is False and error.retry_after is None
            bodies.append(error.response_body())
        retryable = not terminal and expected_wait is not None
        for body in bodies:
            assert (body["status"], body["code"]) == (429, "managed_spend_velocity")
            assert body["metadata"] == metadata
            assert body["remedy"] == display["remedy"]
            assert body["message"] == display["message"]
            assert body["display_wait_seconds"] == expected_wait
            assert body["retryable"] is retryable
            assert body["retry_after"] == (expected_wait if retryable else None)
            assert "DO_NOT_FORWARD" not in repr(body)
        assert ledger_error.gateway_display == checked_display
        assert (type(ledger_error), ledger_error.retryable, ledger_error.retry_after,
                ledger_error.backoff, ledger_error.charged, ledger_error.dispatched,
                dict(ledger_error.metadata)) == before


class TestMoneyR5Transport:
    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02"))

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("outcome", ["settled", "released", "pending_reconcile"])
    def test_explicit_dispatch_replay_never_sends_or_moves_money(self, mr, organization, workspace, is_async, streaming, outcome):
        raw = (gateway_refusal(503, "unknown", headers={"x-agentcc-dispatched": "false"})
               if outcome == "released" else TimeoutError(PRIVATE_UPSTREAM_TEXT) if outcome == "pending_reconcile" else None)
        client = FakeGatewayClient(is_async=is_async, error=raw, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        entered = []
        request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            def meter():
                # Reconstruct the meter too: a process-local ManagedCall flag is insufficient.
                return cl.call_meter(organization=organization, workspace=workspace, model_name=OPENAI_CHAT_MODEL,
                                     purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="replay", source_id="same-logical-call")
            def run():
                with meter().metered(amount_estimate="0.10", dispatch_id="same-dispatch") as call:
                    entered.append(call)
                    response = mr.managed_chat_completion(binding, request)
                    if streaming:
                        for chunk in response:
                            call.observe(response=chunk)
                    else:
                        call.observe(response=response)
            async def arun():
                from asgiref.sync import sync_to_async

                replay_meter = await sync_to_async(meter, thread_sensitive=True)()
                async with replay_meter.ametered(amount_estimate="0.10", dispatch_id="same-dispatch") as call:
                    entered.append(call)
                    response = await mr.managed_chat_completion_async(binding, request)
                    if streaming:
                        async for chunk in response:
                            call.observe(response=chunk)
                    else:
                        call.observe(response=response)
            def invoke():
                return asyncio.run(arun()) if is_async else run()
            if outcome == "settled":
                invoke()
            else:
                with pytest.raises(cl.ManagedCreditRefusal):
                    invoke()
            original = entered[0]
            assert original.status == {"settled": "settled", "released": "released_refused", "pending_reconcile": "pending_reconcile"}[outcome]
            before = ledger.get_hold(original.hold_id)
            balance, reserved = ledger.balance_of(organization), ledger.held_of(organization)
            operations = list(ledger.calls)
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                invoke()
            assert ledger.get_hold(original.hold_id) == before
            assert ledger.balance_of(organization) == balance
            assert ledger.held_of(organization) == reserved
        error = caught.value
        assert error.code == "MANAGED_CALL_ALREADY_COMPLETED"
        assert isinstance(error, ValueError)
        assert error.retryable is False and error.backoff is False and error.retry_after is None
        assert error.message == "This call already ran or was refused and will not be sent again."
        assert cl.refusal_activity_error(error)["non_retryable"] is True
        assert get_specific_error_message(error, True) == error.message
        assert len(entered) == len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert ledger.calls == operations + [("hold", original.idempotency_key, Decimal("0.10"))]
        assert ledger.holds[original.hold_id].debits == (1 if outcome == "settled" else 0)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("delivery", ["response", "stream_create", "stream_next"])
    @pytest.mark.parametrize("status, code, body_dispatched, header, released", [
        (503, "managed_billing_unavailable", False, None, False),
        (429, "rate_limit_exceeded", True, None, True),
        (503, "unknown", True, "false", True),
        (429, "rate_limit_exceeded", False, "true", False),
        (200, "invalid_api_key", False, None, False),
        (500, "invalid_api_key", False, None, False),
        (403, "invalid_api_key", False, None, False),
        (None, "rate_limit_exceeded", False, None, False),
        (429, "rate_limit_exceeded", False, "False", False),
    ])
    def test_body_cannot_override_header_or_row4_guard(self, mr, organization, workspace, is_async, delivery, status, code, body_dispatched, header, released):
        headers = {} if header is None else {"X-AgentCC-Dispatched": header}
        raw = gateway_refusal(status, code, metadata={"dispatched": body_dispatched}, headers=headers)
        client = FakeGatewayClient(is_async=is_async, error=raw if delivery != "stream_next" else None,
                                   stream_error=raw if delivery == "stream_next" else None)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, delivery != "response")
        assert _ops(ledger) == (["hold", "release"] if released else ["hold"])
        assert caught.value.charged is (False if released else None)
        if not released:
            assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert len(client.calls) == len(port.authorize_calls) == 1
        assert ledger.balance_of(organization) == Decimal("1")

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("header, pending", [("true", False), ("false", True), (None, False)])
    def test_success_response_header_is_captured_before_output(self, mr, organization, workspace, monkeypatch, is_async, streaming, header, pending):
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")],
                                   response_headers={} if header is None else {"X-AgentCC-Dispatched": header})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        warnings = []
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append(event))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            if pending:
                with pytest.raises(mr.ModelRouteError) as caught:
                    TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)
                assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
            else:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)
        assert _ops(ledger) == (["hold"] if pending else ["hold", "settle"])
        assert warnings.count("credit_dispatch_contradiction") == (1 if pending else 0)
        assert ledger.balance_of(organization) == Decimal("1" if pending else "0.98")

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("header, output, failure, expected", [
        ("true", False, "contract", "pending"),
        ("false", False, "timeout", "release"),
        ("false", True, "timeout", "pending"),
        ("true", True, "contract", "settle"),
        ("false", False, "none", "release"),
        ("true", False, "none", "pending"),
        (None, False, "none", "pending"),
    ])
    def test_stream_response_attestation_survives_headerless_iteration(self, mr, organization, workspace, monkeypatch, is_async, header, output, failure, expected):
        raw = gateway_refusal(429, "rate_limit_exceeded", metadata={"dispatched": False}) if failure == "contract" else TimeoutError(PRIVATE_UPSTREAM_TEXT) if failure == "timeout" else None
        client = FakeGatewayClient(is_async=is_async, stream_error=raw, stream_chunks=[stream_chunk("partial")] if output else [],
                                   response_headers={} if header is None else {"x-agentcc-dispatched": header})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        warnings = []
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append(event))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, True)
        assert _ops(ledger) == {"pending": ["hold"], "release": ["hold", "release"], "settle": ["hold", "settle"]}[expected]
        assert caught.value.charged is {"pending": None, "release": False, "settle": True}[expected]
        assert warnings.count("credit_dispatch_contradiction") == (1 if header == "false" and output else 0)
        assert caught.value.retryable is False


class TestMoneyR6Transport:
    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02"))

    def _meter(self, organization, workspace):
        return cl.call_meter(
            organization=organization, workspace=workspace, model_name=OPENAI_CHAT_MODEL,
            purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="atomic-replay", source_id="same-logical-call",
        )

    def _invoke(self, mr, binding, organization, workspace, is_async, streaming, entered):
        request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
        def run():
            with self._meter(organization, workspace).metered(amount_estimate="0.10", dispatch_id="same-dispatch") as call:
                entered.append(call)
                result = mr.managed_chat_completion(binding, request)
                if streaming:
                    for chunk in result:
                        call.observe(response=chunk)
                else:
                    call.observe(response=result)
        async def arun():
            from asgiref.sync import sync_to_async

            meter = await sync_to_async(self._meter, thread_sensitive=True)(organization, workspace)
            async with meter.ametered(amount_estimate="0.10", dispatch_id="same-dispatch") as call:
                entered.append(call)
                result = await mr.managed_chat_completion_async(binding, request)
                if streaming:
                    async for chunk in result:
                        call.observe(response=chunk)
                else:
                    call.observe(response=result)
        return asyncio.run(arun()) if is_async else run()

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    def test_failed_pending_marker_cannot_reopen_consumed_dispatch(self, mr, organization, workspace, is_async, streaming):
        class MissingMarkerLedger(cl.MockCreditLedger):
            def mark_pending_hold(self, hold_id, *, gateway_request_id):
                raise ConnectionError(PRIVATE_UPSTREAM_TEXT)  # fails before any commit
        ledger = MissingMarkerLedger(default_balance="1")
        raw = TimeoutError(PRIVATE_UPSTREAM_TEXT)
        client = FakeGatewayClient(is_async=is_async, error=None if streaming else raw,
                                   stream_error=raw if streaming else None)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        entered = []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                self._invoke(mr, binding, organization, workspace, is_async, streaming, entered)
            original = entered[0]
            before = ledger.get_hold(original.hold_id)
            assert original.status == "pending_reconcile"
            assert before["state"] == "held" and before["pending_reconcile"] is False
            operations = list(ledger.calls)
            # Simulate another worker's fresh breaker state; the port persists.
            cl.reset_meter_safety()
            with pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
                self._invoke(mr, binding, organization, workspace, is_async, streaming, entered)
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert len(entered) == len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert ledger.get_hold(original.hold_id) == before
        assert ledger.calls == operations + [("hold", original.idempotency_key, Decimal("0.10"))]
        assert _ops(ledger) == ["hold", "hold"]
        assert ledger.balance_of(organization) == Decimal("1")
        assert ledger.held_of(organization) == Decimal("0.10")
        assert ledger.holds[original.hold_id].debits == 0

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("mode", ["missing", "raises", "raises_after_commit"])
    def test_unavailable_atomic_port_never_reaches_transport(self, mr, organization, workspace, monkeypatch, is_async, streaming, mode):
        # r8: distinguish an unavailable port AND OSS from the supported fallback.
        monkeypatch.setitem(sys.modules, "agentcc.services.dispatch_claims", None)
        class BrokenAtomicLedger(cl.MockCreditLedger):
            def claim_dispatch_once(self, key):
                if mode == "raises_after_commit":
                    assert super().claim_dispatch_once(key) is True
                raise ConnectionError(PRIVATE_UPSTREAM_TEXT)
        ledger = BrokenAtomicLedger(default_balance="1")
        if mode == "missing":
            ledger.claim_dispatch_once = None
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        entered = []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
                for attempt in cl.ManagedAttempts(True):
                    with attempt:
                        self._invoke(mr, binding, organization, workspace, is_async, streaming, entered)
        assert isinstance(caught.value, ValueError)
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert PRIVATE_UPSTREAM_TEXT not in caught.value.message
        assert entered == client.calls == port.authorize_calls == []
        assert _ops(ledger) == ([] if mode == "missing" else ["hold"])
        assert ledger.balance_of(organization) == Decimal("1")
        assert all(h.state == "held" and h.debits == 0 for h in ledger.holds.values())

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    def test_overlapping_held_identity_has_only_one_transport_winner(self, mr, organization, workspace, is_async, streaming):
        ledger = cl.MockCreditLedger(default_balance="1")
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
        winners, losers = [], []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            def verify_loser(first, before):
                assert cl.current_managed_call() is first
                assert first.dispatched is False
                assert ledger.get_hold(first.hold_id) == before
                assert client.calls == port.authorize_calls == []
                assert ledger.balance_of(organization) == Decimal("1")
                assert ledger.held_of(organization) == Decimal("0.10")
            def run():
                with self._meter(organization, workspace).metered(amount_estimate="0.10", dispatch_id="same-dispatch") as first:
                    winners.append(first)
                    before = ledger.get_hold(first.hold_id)
                    with pytest.raises(cl.ManagedCallAlreadyCompletedError):
                        self._invoke(mr, binding, organization, workspace, False, streaming, losers)
                    verify_loser(first, before)
                    result = mr.managed_chat_completion(binding, request)
                    if streaming:
                        for chunk in result:
                            first.observe(response=chunk)
                    else:
                        first.observe(response=result)
            async def arun():
                from asgiref.sync import sync_to_async

                meter = await sync_to_async(self._meter, thread_sensitive=True)(organization, workspace)
                async with meter.ametered(amount_estimate="0.10", dispatch_id="same-dispatch") as first:
                    winners.append(first)
                    before = ledger.get_hold(first.hold_id)
                    replay = await sync_to_async(self._meter, thread_sensitive=True)(organization, workspace)
                    with pytest.raises(cl.ManagedCallAlreadyCompletedError):
                        async with replay.ametered(amount_estimate="0.10", dispatch_id="same-dispatch") as loser:
                            losers.append(loser)
                            result = await mr.managed_chat_completion_async(binding, request)
                            if streaming:
                                async for chunk in result:
                                    loser.observe(response=chunk)
                            else:
                                loser.observe(response=result)
                    verify_loser(first, before)
                    result = await mr.managed_chat_completion_async(binding, request)
                    if streaming:
                        async for chunk in result:
                            first.observe(response=chunk)
                    else:
                        first.observe(response=result)
            asyncio.run(arun()) if is_async else run()
        assert losers == []
        assert len(winners) == len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert _ops(ledger) == ["hold", "hold", "settle"]
        assert ledger.balance_of(organization) == Decimal("0.98")
        assert ledger.holds[winners[0].hold_id].debits == 1

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("present, value, expected", [
        (False, None, None),  # r10: no header, body request_id present -> keyless (main G05)
        (True, "response-request-id", "response-request-id"),
        (True, "", None),
        (True, "invalid header!", None),
        (True, None, None),
        (True, True, None),
        (True, "x" * 129, None),
    ])
    def test_stream_request_header_presence_persists_to_pending_hold(self, mr, organization, workspace, is_async, present, value, expected):
        raw = gateway_refusal(504, "gateway_timeout", metadata={"request_id": "body-request-id"})
        client = FakeGatewayClient(is_async=is_async, stream_error=raw, keyed=False,
                                   response_headers={"X-AgentCC-Request-ID": value} if present else {})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, True)
        assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        held = next(iter(ledger.holds.values()))
        row = ledger.get_hold(held.hold_id)
        assert row["gateway_request_id"] == expected
        assert row["gateway_request_id"] != row["request_id"]
        assert row["state"] == "held" and row["pending_reconcile"] is True
        assert _ops(ledger) == ["hold"]
        assert held.debits == 0 and ledger.balance_of(organization) == Decimal("1")


class TestMoneyR7Transport:
    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02"))

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("outer", [False, True])
    @pytest.mark.parametrize("creation, iteration", [("true", "false"), ("false", "true")])
    def test_conflicting_creation_and_iteration_headers_keep_hold_open(
        self, mr, organization, workspace, monkeypatch, tripped,
        is_async, outer, creation, iteration
    ):
        from asgiref.sync import async_to_sync

        gateway_id = "r7-stream-response"
        raw = gateway_refusal(429, "rate_limit_exceeded", headers={
            "x-agentcc-dispatched": iteration, "x-agentcc-request-id": gateway_id,
        })
        client = FakeGatewayClient(
            is_async=is_async, stream_chunks=[], stream_error=raw,
            response_headers={"x-agentcc-dispatched": creation,
                              "x-agentcc-request-id": gateway_id},
        )
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        opened, warnings = [], []
        real_open = cl._HoldSession.open
        def capture(session):
            call = real_open(session)
            opened.append(call)
            return call
        monkeypatch.setattr(cl._HoldSession, "open", capture)
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = TestMoneyR6Transport()._meter(organization, workspace) if outer else None
            request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=True)
            def run():
                if outer:
                    with meter.metered(amount_estimate="0.10", dispatch_id="r7-stream"):
                        list(mr.managed_chat_completion(binding, request))
                else:
                    list(mr.managed_chat_completion(binding, request))
            async def arun():
                if outer:
                    async with meter.ametered(amount_estimate="0.10", dispatch_id="r7-stream"):
                        stream = await mr.managed_chat_completion_async(binding, request)
                        return [chunk async for chunk in stream]
                stream = await mr.managed_chat_completion_async(binding, request)
                return [chunk async for chunk in stream]
            with pytest.raises(cl.ManagedCreditRefusal if outer else mr.ModelRouteError) as caught:
                async_to_sync(arun)() if is_async else run()
        assert len(opened) == len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        call = opened[0]
        assert call.output_seen is False and call.dispatch_count == 1
        assert call.gateway_dispatched is None and call.gateway_dispatch_conflict is True
        assert call.refused_before_upstream is False
        assert call.status == "pending_reconcile"
        assert call.actual_amount is None
        assert _ops(ledger) == ["hold"]
        held = ledger.get_hold(call.hold_id)
        assert held["state"] == "held" and held["pending_reconcile"] is True
        assert held["gateway_request_id"] == gateway_id
        assert ledger.holds[call.hold_id].debits == 0
        assert ledger.balance_of(organization) == Decimal("1")
        assert ledger.held_of(organization) == Decimal("0.10")
        assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert PRIVATE_UPSTREAM_TEXT not in caught.value.message
        call.note_dispatch_contradiction()  # A second observer must not emit twice.
        assert warnings == [("credit_dispatch_contradiction", {
            "organization_id": str(organization.id), "request_id": call.request_id,
            "gateway_request_id": gateway_id,
        })]
        assert tripped == []

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("outer_atomic", [False, True])
    def test_explicit_dispatch_claim_requires_committed_hold_outside_atomic(
        self, mr, organization, workspace, monkeypatch, record_property, tripped,
        is_async, streaming, outer_atomic
    ):
        import json
        from contextlib import nullcontext
        from asgiref.sync import async_to_sync
        from django.db import connection, transaction

        events, snapshots, entered = [], [], []
        atomic_requests = connection.settings_dict.get("ATOMIC_REQUESTS")
        assert connection.in_atomic_block is False
        def snapshot(point):
            events.append(point)
            state = {
                "point": point,
                "ATOMIC_REQUESTS": connection.settings_dict.get("ATOMIC_REQUESTS"),
                "in_atomic_block": connection.in_atomic_block,
            }
            snapshots.append(state)
            assert state["ATOMIC_REQUESTS"] == atomic_requests
            assert state["in_atomic_block"] is outer_atomic
        class CommitObservedLedger(cl.MockCreditLedger):
            def hold(self, **kwargs):
                with transaction.atomic():
                    hold_id = super().hold(**kwargs)
                    transaction.on_commit(lambda: events.append("hold_committed"))
                snapshot("hold_return")
                return hold_id
            def claim_dispatch_once(self, key):
                snapshot("primitive")
                assert connection.in_atomic_block is False
                assert "hold_committed" in events
                assert cl.current_managed_call() is None
                return super().claim_dispatch_once(key)
        ledger = CommitObservedLedger(default_balance="1")
        real_open, real_claim = cl._HoldSession.open, cl.require_managed_hold
        def open_probe(session):
            assert session.dispatch_id == "r7-explicit"
            snapshot("meter_open")
            return real_open(session)
        def transport_claim(**kwargs):
            events.append("transport_claim")
            assert "primitive" in events
            return real_claim(**kwargs)
        monkeypatch.setattr(cl._HoldSession, "open", open_probe)
        monkeypatch.setattr(cl, "require_managed_hold", transport_claim)
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = TestMoneyR6Transport()._meter(organization, workspace)
            request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
            def run():
                with meter.metered(amount_estimate="0.10", dispatch_id="r7-explicit") as call:
                    entered.append(call)
                    response = mr.managed_chat_completion(binding, request)
                    if streaming:
                        for chunk in response:
                            call.observe(response=chunk)
                    else:
                        call.observe(response=response)
            async def arun():
                async with meter.ametered(amount_estimate="0.10", dispatch_id="r7-explicit") as call:
                    entered.append(call)
                    response = await mr.managed_chat_completion_async(binding, request)
                    if streaming:
                        async for chunk in response:
                            call.observe(response=chunk)
                    else:
                        call.observe(response=response)
            with transaction.atomic() if outer_atomic else nullcontext():
                # async_to_sync preserves the transaction-owning sync thread for
                # ametered's thread-sensitive session.open. asyncio.run here would
                # instead inspect an unrelated worker's non-atomic connection.
                if outer_atomic:
                    with pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
                        async_to_sync(arun)() if is_async else run()
                    assert caught.value.retryable is False and caught.value.retry_after is None
                    assert caught.value.backoff is False
                    assert cl.refusal_activity_error(caught.value)["non_retryable"] is True
                    assert "primitive" not in events and "transport_claim" not in events
                    assert "hold_committed" not in events
                    assert entered == client.calls == port.authorize_calls == []
                else:
                    async_to_sync(arun)() if is_async else run()
        assert len(ledger.holds) == 1
        held = next(iter(ledger.holds.values()))
        assert events.count("hold_committed") == 1
        assert _ops(ledger) == (["hold"] if outer_atomic else ["hold", "settle"])
        if outer_atomic:
            assert held.state == "held" and held.pending_reconcile is False
            assert held.debits == 0 and ledger.balance_of(organization) == Decimal("1")
            assert ledger.held_of(organization) == Decimal("0.10")
            # The atomic guard did not invoke the primitive or consume the key.
            assert cl.MockCreditLedger.claim_dispatch_once(ledger, held.idempotency_key) is True
            assert [row["point"] for row in snapshots] == ["meter_open", "hold_return"]
        else:
            assert events.index("hold_committed") < events.index("primitive") < events.index("transport_claim")
            assert len(entered) == len(client.calls) == len(port.authorize_calls) == 1
            assert held.debits == 1 and ledger.balance_of(organization) == Decimal("0.98")
            assert [row["point"] for row in snapshots] == ["meter_open", "hold_return", "primitive"]
        assert tripped == []
        record_property("explicit_dispatch_connection_states", json.dumps(snapshots))

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    def test_owned_standalone_caller_runtime_connection_probe(
        self, mr, organization, workspace, monkeypatch, record_property, is_async, streaming
    ):
        import json
        from django.db import connection

        # These are actual owned service paths, not HTTP/Temporal wrapper probes.
        # No explicit identity is supplied by their kwargs dictionaries.
        snapshots = []
        real_open = cl._HoldSession.open
        def open_probe(session):
            state = {
                "ATOMIC_REQUESTS": connection.settings_dict.get("ATOMIC_REQUESTS"),
                "in_atomic_block": connection.in_atomic_block,
                "explicit_dispatch_id": session.dispatch_id is not None,
            }
            snapshots.append(state)
            assert isinstance(state["ATOMIC_REQUESTS"], bool)
            assert state["in_atomic_block"] is False
            assert state["explicit_dispatch_id"] is False
            return real_open(session)
        monkeypatch.setattr(cl._HoldSession, "open", open_probe)
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)
        assert len(snapshots) == len(client.calls) == len(port.authorize_calls) == 1
        assert _ops(ledger) == ["hold", "settle"]
        record_property("owned_standalone_connection", json.dumps(snapshots))

    def test_owned_reconcile_command_has_no_dispatch_caller(
        self, monkeypatch, record_property
    ):
        import json
        from io import StringIO
        from django.core.management import call_command
        from django.db import connection

        snapshots = []
        class ProbeLedger(cl.MockCreditLedger):
            def list_pending_holds(self, older_than):
                state = {
                    "ATOMIC_REQUESTS": connection.settings_dict.get("ATOMIC_REQUESTS"),
                    "in_atomic_block": connection.in_atomic_block,
                }
                snapshots.append(state)
                assert isinstance(state["ATOMIC_REQUESTS"], bool)
                assert state["in_atomic_block"] is False
                return super().list_pending_holds(older_than)
        def unexpected_meter(session):
            pytest.fail("reconciliation must never open a new dispatch meter")
        monkeypatch.setattr(cl._HoldSession, "open", unexpected_meter)
        ledger, stdout = ProbeLedger(default_balance="1"), StringIO()
        with cl.use_credit_ledger(ledger):
            call_command("reconcile_pending_credit_holds", stdout=stdout)
        assert len(snapshots) == 1 and ledger.calls == []
        assert "pending_reconcile=0" in stdout.getvalue()
        record_property("owned_reconcile_connection", json.dumps(snapshots))


class TestMoneyR8Transport:
    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02"))

    def _invoke(self, mr, binding, meter, is_async, streaming, price=None):
        from asgiref.sync import async_to_sync
        from contextlib import nullcontext

        request = dict(model=OPENAI_CHAT_MODEL, messages=MESSAGES, stream=streaming)
        def run():
            with meter.metered(amount_estimate="0.10", dispatch_id="r8-transport") if meter else nullcontext() as call:
                response = mr.managed_chat_completion(binding, request)
                if streaming:
                    list(response)
                if price is not None and call is not None:
                    # Under an outer meter the transport only marks output; the outer call prices it.
                    call.observe(cost=price)
        async def arun():
            async def dispatch():
                response = await mr.managed_chat_completion_async(binding, request)
                if streaming:
                    return [chunk async for chunk in response]
                return response
            if meter:
                async with meter.ametered(amount_estimate="0.10", dispatch_id="r8-transport") as call:
                    result = await dispatch()
                    if price is not None:
                        call.observe(cost=price)
                    return result
            return await dispatch()
        return async_to_sync(arun)() if is_async else run()

    def _assert_pending(self, ledger, organization, call, client, port, caught, warnings):
        assert len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert call.dispatch_count == 1 and call.status == "pending_reconcile"
        assert call.gateway_dispatched is None and call.gateway_dispatch_conflict is True
        assert call.refused_before_upstream is False
        assert call.actual_amount is None and call.cost_source is None
        assert _ops(ledger) == ["hold"]
        row = ledger.get_hold(call.hold_id)
        assert row["state"] == "held" and row["pending_reconcile"] is True
        assert row["gateway_request_id"] == "r8-transport-response"
        assert ledger.holds[call.hold_id].debits == 0
        assert ledger.balance_of(organization) == Decimal("1")
        assert ledger.held_of(organization) == Decimal("0.10")
        assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert PRIVATE_UPSTREAM_TEXT not in caught.value.message
        call.note_dispatch_contradiction()
        assert warnings == [("credit_dispatch_contradiction", {
            "organization_id": str(organization.id), "request_id": call.request_id,
            "gateway_request_id": "r8-transport-response",
        })]

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("outer", [False, True])
    @pytest.mark.parametrize("output", [False, True])
    @pytest.mark.parametrize("priced", [False, True])
    @pytest.mark.parametrize("creation, iteration", [
        ("false", "invalid"), ("invalid", "true"), ("true", "false"),
    ])
    def test_stream_creation_output_and_iteration_evidence_is_monotonic(
        self, mr, organization, workspace, monkeypatch, tripped,
        is_async, outer, output, priced, creation, iteration
    ):
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.02") if priced else None)
        headers = {"x-agentcc-dispatched": creation, "x-agentcc-request-id": "r8-transport-response"}
        raw = gateway_refusal(429, "rate_limit_exceeded", headers={
            **headers, "x-agentcc-dispatched": iteration,
        })
        client = FakeGatewayClient(is_async=is_async, response_headers=headers,
                                   stream_chunks=[stream_chunk("partial")] if output else [],
                                   stream_error=raw)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        opened, warnings = [], []
        real_open = cl._HoldSession.open
        def capture(session):
            call = real_open(session)
            opened.append(call)
            return call
        monkeypatch.setattr(cl._HoldSession, "open", capture)
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = TestMoneyR6Transport()._meter(organization, workspace) if outer else None
            with pytest.raises(cl.ManagedCreditRefusal if outer else mr.ModelRouteError) as caught:
                self._invoke(mr, binding, meter, is_async, True)
        assert len(opened) == 1 and opened[0].output_seen is output
        self._assert_pending(ledger, organization, opened[0], client, port, caught, warnings)
        assert tripped == []

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("outer", [False, True])
    @pytest.mark.parametrize("creation, parse_error", [
        ("false", "invalid"), ("invalid", "true"), ("true", "false"), ("invalid", None),
    ])
    def test_nonstream_creation_and_parse_evidence_is_monotonic(
        self, mr, organization, workspace, monkeypatch, tripped, is_async, outer, creation, parse_error
    ):
        from model_hub.tests.test_model_route_support import _RawResponse, _AsyncRawResponse

        headers = {"x-agentcc-dispatched": creation, "x-agentcc-request-id": "r8-transport-response"}
        if parse_error is not None:
            raw_error = gateway_refusal(429, "rate_limit_exceeded", headers={
                **headers, "x-agentcc-dispatched": parse_error,
            })
            def parse(raw):
                raise raw_error
            async def aparse(raw):
                raise raw_error
            monkeypatch.setattr(_RawResponse, "parse", parse)
            monkeypatch.setattr(_AsyncRawResponse, "parse", aparse)
        client = FakeGatewayClient(is_async=is_async, response_headers=headers)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        opened, warnings = [], []
        real_open = cl._HoldSession.open
        def capture(session):
            call = real_open(session)
            opened.append(call)
            return call
        monkeypatch.setattr(cl._HoldSession, "open", capture)
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = TestMoneyR6Transport()._meter(organization, workspace) if outer else None
            with pytest.raises(cl.ManagedCreditRefusal if outer else mr.ModelRouteError) as caught:
                self._invoke(mr, binding, meter, is_async, False)
        assert len(opened) == 1 and opened[0].output_seen is (parse_error is None)
        self._assert_pending(ledger, organization, opened[0], client, port, caught, warnings)
        assert tripped == []

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("mode", [
        "missing", "noncallable", "true", "false", "none", "truthy", "raises", "lookup_raises",
    ])
    def test_claim_selection_commit_order_and_no_failure_fallback(
        self, mr, organization, workspace, monkeypatch, tripped, is_async, streaming, mode
    ):
        from agentcc.services import dispatch_claims
        from django.db import connection, transaction

        events, port_calls, oss_calls, opened = [], [], [], []
        class ClaimLedger(cl.MockCreditLedger):
            def __getattribute__(self, name):
                if name == "claim_dispatch_once":
                    if mode == "missing":
                        raise AttributeError(name)
                    if mode == "lookup_raises":
                        raise ConnectionError(PRIVATE_UPSTREAM_TEXT)
                return super().__getattribute__(name)
            def hold(self, **kwargs):
                with transaction.atomic():
                    hold_id = super().hold(**kwargs)
                    transaction.on_commit(lambda: events.append("hold_committed"))
                events.append("hold_return")
                return hold_id
            def claim_dispatch_once(self, key):
                port_calls.append(key)
                events.append("port_claim")
                assert events[-3:] == ["hold_committed", "hold_return", "port_claim"]
                assert connection.in_atomic_block is False and connection.get_autocommit() is True
                if mode == "raises":
                    raise ConnectionError(PRIVATE_UPSTREAM_TEXT)
                if mode == "true":
                    return super().claim_dispatch_once(key)
                return {"false": False, "none": None, "truthy": 1}[mode]
        ledger = ClaimLedger(default_balance="1")
        if mode == "noncallable":
            ledger.claim_dispatch_once = None
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("answer")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL},
                             **{"async_client" if is_async else "client": client})
        def oss_claim(key):
            oss_calls.append(key)
            events.append("oss_claim")
            assert events[-3:] == ["hold_committed", "hold_return", "oss_claim"]
            assert connection.in_atomic_block is False and connection.get_autocommit() is True
            assert cl.current_managed_call() is None
            return cl.MockCreditLedger.claim_dispatch_once(ledger, key)
        monkeypatch.setattr(dispatch_claims, "claim_dispatch_once", oss_claim)
        real_open, real_claim = cl._HoldSession.open, cl.require_managed_hold
        def capture(session):
            call = real_open(session)
            opened.append(call)
            return call
        def transport_claim(**kwargs):
            events.append("transport_claim")
            assert events[-2] == ("oss_claim" if mode in ("missing", "noncallable") else "port_claim")
            return real_claim(**kwargs)
        monkeypatch.setattr(cl._HoldSession, "open", capture)
        monkeypatch.setattr(cl, "require_managed_hold", transport_claim)
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = TestMoneyR6Transport()._meter(organization, workspace)
            identity = cl.ManagedCall(meter.envelope, meter.route, dispatch_id="r8-transport")
            success = mode in ("missing", "noncallable", "true")
            if success:
                self._invoke(mr, binding, meter, is_async, streaming, price="0.02")
                before = ledger.get_hold(opened[0].hold_id)
                # The fallback uses the same consumed key on replay, not a new identity.
                with pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
                    self._invoke(mr, binding, meter, is_async, streaming, price="0.02")
                assert ledger.get_hold(opened[0].hold_id) == before
            else:
                with pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
                    self._invoke(mr, binding, meter, is_async, streaming)
        expected_calls = 2 if success else 1
        assert oss_calls == ([identity.idempotency_key] * 2 if mode in ("missing", "noncallable") else [])
        assert port_calls == ([] if mode in ("missing", "noncallable", "lookup_raises") else [identity.idempotency_key] * expected_calls)
        assert len(opened) == len(client.calls) == len(port.authorize_calls) == int(success)
        assert _ops(ledger) == (["hold", "settle", "hold"] if success else [] if mode == "lookup_raises" else ["hold"])
        # r10: the outer call prices the output (settle = actual, never the 0.10 hold estimate).
        assert ledger.balance_of(organization) == Decimal("0.98" if success else "1")
        assert all(h.debits == int(success) for h in ledger.holds.values())
        if not success:
            assert "transport_claim" not in events
            assert all(h.state == "held" for h in ledger.holds.values())
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert PRIVATE_UPSTREAM_TEXT not in caught.value.message
        assert tripped == []


class TestMoneyR9Transport:
    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("priced", [None, Decimal("0")])
    def test_unpriced_output_never_charges_estimate(self, mr, organization, workspace, monkeypatch, is_async, streaming, priced):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: priced)
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("output")],
                                   response_headers={"x-agentcc-request-id": "response-r9"})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)
            assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
            assert caught.value.retryable is False and caught.value.charged is None
            assert len(client.calls) == len(ledger.holds) == 1
            hold = next(iter(ledger.holds.values()))
            assert hold.state == "held" and hold.debits == 0
            monkeypatch.setattr(command, "lookup_settle_record", lambda **kw: None)
            monkeypatch.setattr(command, "utc_now", lambda: hold.metadata["opened_at"] + cl.RECONCILE_TTL)
            command.Command().handle()
            command.Command().handle()
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("1")

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("actual,expected", [("0.02", "0.02"), ("0.20", "0.10")])
    def test_transport_provisional_is_corrected_by_record_once(self, mr, organization, workspace, monkeypatch, settings, is_async, streaming, actual, expected):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.06"))
        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("output")],
                                   response_headers={"x-agentcc-request-id": "response-r9"})
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        incidents = []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = lambda **kw: dict(**kw, actual_amount=actual)
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)
            hold = next(iter(ledger.holds.values()))
            row = ledger.get_hold(hold.hold_id)
            assert row["settlement_source"] == "provider_usage" and row["pending_reconcile"] is True
            assert row["gateway_request_id"] == "response-r9"
            assert ledger.balance_of(organization) == Decimal("0.94")
            command.Command().handle()
            before = list(ledger.calls)
            command.Command().handle()
        assert ledger.calls == before
        assert len(client.calls) == len(ledger.holds) == 1
        assert hold.settled_amount == Decimal(expected)
        assert ledger.balance_of(organization) == Decimal("1") - Decimal(expected)
        assert hold.debits == 1
        assert _ops(ledger).count("adjust_settled") == 1
        assert len(incidents) == (1 if actual == "0.20" else 0)


class TestMoneyR10Transport:
    """OK-COMPOSE-r10 P1/P2 through the real transport, meter and command."""

    @pytest.fixture(autouse=True)
    def stable_prices(self, monkeypatch):
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        monkeypatch.setattr(cl, "cost_from_response", lambda *a, **kw: Decimal("0.06"))

    @staticmethod
    def _client(is_async, delivery, *, keyed):
        raw = gateway_refusal(504, "gateway_timeout", metadata={"request_id": "body-only-id"})
        return FakeGatewayClient(
            is_async=is_async, keyed=keyed,
            stream_chunks=[stream_chunk("output")] if delivery != "response" else [],
            stream_error=raw if delivery == "stream_error" else None)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("delivery", ["response", "stream", "stream_error"])
    def test_priced_output_without_a_response_header_is_r11_and_releases_at_ttl(
        self, mr, organization, workspace, monkeypatch, is_async, delivery
    ):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        client = self._client(is_async, delivery, keyed=False)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        looked_up = []
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            with pytest.raises(mr.ModelRouteError) as caught:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, delivery != "response")
            assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
            assert caught.value.retryable is False and caught.value.charged is None
            assert len(client.calls) == len(ledger.holds) == 1
            hold = next(iter(ledger.holds.values()))
            row = ledger.get_hold(hold.hold_id)
            assert row["state"] == "held" and row["pending_reconcile"] is True
            assert row["gateway_request_id"] is None and "settlement_source" not in row
            assert hold.debits == 0 and _ops(ledger) == ["hold"]
            assert ledger.balance_of(organization) == Decimal("1")
            monkeypatch.setattr(command, "lookup_settle_record", lambda **kw: looked_up.append(kw))
            monkeypatch.setattr(command, "utc_now", lambda: hold.metadata["opened_at"] + cl.RECONCILE_TTL)
            command.Command().handle()
            command.Command().handle()
        assert looked_up == []  # a body request_id is never a lookup key
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("1")

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("delivery", ["response", "stream", "stream_error"])
    def test_the_same_flow_with_a_response_header_takes_the_provisional_debit(
        self, mr, organization, workspace, is_async, delivery
    ):
        client = self._client(is_async, delivery, keyed=True)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            if delivery == "stream_error":
                with pytest.raises(mr.ModelRouteError) as caught:
                    TestMoneyR4Transport()._invoke(mr, binding, is_async, True)
                assert caught.value.charged is True
            else:
                TestMoneyR4Transport()._invoke(mr, binding, is_async, delivery != "response")
        hold = next(iter(ledger.holds.values()))
        row = ledger.get_hold(hold.hold_id)
        assert row["state"] == "settled" and row["settlement_source"] == "provider_usage"
        assert row["pending_reconcile"] is True and row["gateway_request_id"] == GATEWAY_RESPONSE_ID
        assert _ops(ledger) == ["hold", "settle"]
        assert ledger.balance_of(organization) == Decimal("0.94")

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("streaming", [False, True])
    def test_lost_correction_ack_never_drops_the_excess_incident_through_the_command(
        self, mr, organization, workspace, monkeypatch, settings, is_async, streaming
    ):
        from django.core.management.base import CommandError
        from model_hub.management.commands import reconcile_pending_credit_holds as command

        class LostAckCorrection(cl.MockCreditLedger):
            def adjust_settled(self, hold_id, corrected_amount, **metadata):
                super().adjust_settled(hold_id, corrected_amount, **metadata)  # committed ...
                raise ConnectionError(PRIVATE_UPSTREAM_TEXT)  # ... acknowledgement lost

        client = FakeGatewayClient(is_async=is_async, stream_chunks=[stream_chunk("output")])
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, **{"async_client" if is_async else "client": client})
        ledger, incidents = LostAckCorrection(default_balance="1"), []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = lambda **kw: dict(**kw, actual_amount="0.20")
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            TestMoneyR4Transport()._invoke(mr, binding, is_async, streaming)  # provisional 0.06 of 0.10
            hold = next(iter(ledger.holds.values()))
            assert ledger.balance_of(organization) == Decimal("0.94")
            with pytest.raises(CommandError):
                command.Command().handle()  # the correction ack was lost
            assert incidents == []  # lost acknowledgement may omit a best-effort log
            row = ledger.get_hold(hold.hold_id)
            field = dict(row["credit_hold_excess"])
            assert field == {"amount": "0.10", "org": str(organization.id),
                             "request_id": hold.metadata["request_id"], "model": hold.metadata["model"],
                             "recorded_at": field["recorded_at"]}
            assert row["hold_excess_recorded"] is True
            before = list(ledger.calls)
            command.Command().handle()  # terminal: incident is already the durable hold field
            command.Command().handle()
        assert incidents == [] and ledger.calls == before
        assert ledger.get_hold(hold.hold_id)["credit_hold_excess"] == field
        assert hold.settled_amount == Decimal("0.10") and hold.debits == 1
        assert ledger.balance_of(organization) == Decimal("0.90")
        assert ledger.list_pending_holds(hold.metadata["opened_at"]) == []


class TestMoneyR20NonstreamCompletion:
    """Nonstream usage belongs to the held call before caller validation."""

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("with_usage, amount_estimate, keyed", [
        (True, "0.10", True),
        (True, "0.000001", True),
        (False, "0.10", True),
        (True, "0.10", False),
    ])
    def test_outer_nonstream_usage_survives_caller_rejection(
        self, mr, organization, workspace, is_async, with_usage, amount_estimate, keyed
    ):
        gateway_response = chat_response("managed ok", model=OPENAI_CHAT_MODEL)
        gateway_response.usage = (
            GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
            if with_usage else None
        )
        # A body ID cannot replace the trusted response header in the G05 control.
        gateway_response.request_id = "body-only-response"
        usage_amount = cl.cost_from_response(OPENAI_CHAT_MODEL, gateway_response)
        if with_usage:
            assert Decimal("0.000001") < usage_amount < Decimal("0.10")
        else:
            assert usage_amount is None
        client = FakeGatewayClient(
            is_async=is_async, keyed=keyed, response=gateway_response,
            response_headers={"x-agentcc-request-id": GATEWAY_RESPONSE_ID} if keyed else {},
        )
        port = eligible_port(
            mr, organization, {OPENAI_CHAT_MODEL},
            **{"async_client" if is_async else "client": client},
        )
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        calls = []
        request = {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES}
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            meter = cl.call_meter(
                organization=organization, workspace=workspace, model_name=OPENAI_CHAT_MODEL,
                purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="money", source_id="r20-rejection",
            )

            def reject(call):
                calls.append(call)
                assert cl.current_managed_call() is call
                assert call.output_seen is True
                assert call._observed == usage_amount
                # Recording usage must not settle early or charge the estimate.
                assert call.status == "held" and call.actual_amount is None
                assert _ops(ledger) == ["hold"]
                assert ledger.get_hold(call.hold_id)["state"] == "held"
                assert ledger.balance_of(organization) == Decimal("1")
                assert ledger.held_of(organization) == Decimal(amount_estimate)
                raise cl.managed_refusal_from(ValueError("{'code': 'managed_credits_exhausted'}"))

            def run():
                with meter.metered(amount_estimate=amount_estimate) as call:
                    response = mr.managed_chat_completion(binding, request)
                    assert response is gateway_response
                    reject(call)  # Deliberately no caller-side observe().

            async def arun():
                async with meter.ametered(amount_estimate=amount_estimate) as call:
                    response = await mr.managed_chat_completion_async(binding, request)
                    assert response is gateway_response
                    reject(call)  # Deliberately no caller-side observe().

            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                asyncio.run(arun()) if is_async else run()

        assert len(calls) == len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        call = calls[0]
        hold = ledger.holds[call.hold_id]
        row = ledger.get_hold(call.hold_id)
        assert call.dispatch_count == 1
        assert sent_request_ids(client) == [call.request_id]
        assert row["gateway_request_id"] == (GATEWAY_RESPONSE_ID if keyed else None)
        assert row["pending_reconcile"] is True
        assert caught.value.retryable is False and caught.value.retry_after is None
        if not with_usage or not keyed:
            assert _ops(ledger) == ["hold"]
            assert call.status == "pending_reconcile"
            assert call.actual_amount is None and call.cost_source is None
            assert hold.state == "held" and hold.debits == 0
            assert "settlement_source" not in row
            assert ledger.balance_of(organization) == Decimal("1")
            assert ledger.held_of(organization) == Decimal(amount_estimate)
            assert caught.value.code == "MANAGED_OUTCOME_PENDING_RECONCILE"
            assert caught.value.charged is None and caught.value.dispatched is None
        else:
            expected = min(usage_amount, Decimal(amount_estimate))
            assert _ops(ledger) == ["hold", "settle"]
            assert call.status == "settled" and call.actual_amount == expected
            assert call.cost_source == (
                "provider_usage_capped" if usage_amount > Decimal(amount_estimate) else "provider_usage"
            )
            assert hold.state == "settled" and hold.debits == 1
            assert hold.settled_amount == expected
            assert row["settlement_source"] == "provider_usage"
            assert ledger.balance_of(organization) == Decimal("1") - expected
            # Provisional settle keeps the residual reservation until the settle record reconciles (G07).
            assert ledger.held_of(organization) == Decimal(amount_estimate) - expected
            assert caught.value.charged is True and caught.value.dispatched is True
            assert "credits were charged" in caught.value.message

    @pytest.mark.parametrize("is_async", [False, True])
    def test_standalone_nonstream_response_is_observed_once(
        self, mr, organization, workspace, monkeypatch, is_async
    ):
        gateway_response = chat_response("managed ok", model=OPENAI_CHAT_MODEL)
        gateway_response.usage = GatewayUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        usage_amount = cl.cost_from_response(OPENAI_CHAT_MODEL, gateway_response)
        assert Decimal("0") < usage_amount < Decimal("0.10")
        client = FakeGatewayClient(is_async=is_async, response=gateway_response)
        port = eligible_port(
            mr, organization, {OPENAI_CHAT_MODEL},
            **{"async_client" if is_async else "client": client},
        )
        monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
        observations = []
        real_observe = cl.ManagedCall.observe

        def observe(call, **kwargs):
            observations.append((call, kwargs))
            return real_observe(call, **kwargs)

        monkeypatch.setattr(cl.ManagedCall, "observe", observe)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            binding = _judge_binding(mr, organization, workspace, mr.OP_SMART_FILTER)
            response = TestMoneyR4Transport()._invoke(mr, binding, is_async, False)
        assert response is gateway_response
        assert len(observations) == 1
        call, kwargs = observations[0]
        assert kwargs == {"response": gateway_response}
        assert call._observed == call.actual_amount == usage_amount
        assert call.dispatch_count == 1 and call.status == "settled"
        assert _ops(ledger) == ["hold", "settle"]
        assert len(client.calls) == len(port.authorize_calls) == len(ledger.holds) == 1
        assert ledger.holds[call.hold_id].debits == 1
        assert ledger.balance_of(organization) == Decimal("1") - usage_amount

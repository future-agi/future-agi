"""Managed-call metering at the call sites (ONEKEY OK-E r3).

R4-12 dataset Run Prompt and the prompt playground, R4-13 experiment prompt
rows and agent graph nodes, R4-15 optimizer proposal / teacher / trial calls,
R4-16 the optimizer evaluator after a usage refusal, R4-23 stable event
identity.

RED at base 651ab5bb: ``model_hub.services.credit_ledger_port`` is missing.
RED against the prior OK-E bytes (repair round), per STEP-2.json: an
unclaimed managed hold is settled (F2); the two-argument RATE_LIMITED refusal
falls back to the unmetered direct evaluator (F1); managed optimizer calls are
dispatched on direct provider credentials (F2); graph-node managed dispatch
has no disclosure refusal (F6); refusals are not keyed on the gateway's
error.code and nothing backs off per Retry-After (real contract); the
playground refusal is untyped (F8).

Doubles: ``MockCreditLedger``, a payer-route resolver standing in for OK-A's
routing double, and stubbed provider calls; a stub that "dispatches managed"
calls ``require_managed_hold`` first, as the OK-A managed transport must.
Tenancy uses real Organization / Workspace rows where a row is needed. No
credential, money verdict, authorization or tenancy check is mocked.
"""

import asyncio
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from model_hub.services import credit_ledger_port as cl

MESSAGES = [{"role": "user", "content": "Answer briefly."}]
OWN_KEY = object()  # stands in for an organization's stored provider key


@pytest.fixture(autouse=True)
def _meter_safety_and_sleeps(monkeypatch):
    sleeps = []
    # These call-site tests use synthetic costs, not catalog-price estimates.
    monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
    monkeypatch.setattr(cl, "_sleep", sleeps.append)

    async def _asleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(cl, "_asleep", _asleep)
    cl.reset_meter_safety()
    yield sleeps
    cl.reset_meter_safety()


def _managed(**_kwargs):
    return cl.PayerRoute.managed(provider="openai")


def _byok(**_kwargs):
    return cl.PayerRoute.byok(provider="openai")


def _value_info(cost=0.0123):
    return {
        "data": {"response": "answer"},
        "metadata": {
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            "cost": {"total_cost": cost, "prompt_cost": 0.0, "completion_cost": cost},
            "response_time": 0.2,
        },
    }


def _response(content="variant"):
    return SimpleNamespace(
        model="gpt-4o",
        usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        _hidden_params={},
    )


def _ops(ledger):
    return [entry[0] for entry in ledger.calls]


def _transport_claim():
    """The OK-A managed transport: claim the open hold before a managed dispatch,
    then note the x-agentcc-request-id RESPONSE header the gateway sends on every
    response (r10: without it a claimed call is R11, never a provisional debit)."""
    call = cl.current_managed_call()
    if call is not None:
        cl.require_managed_hold(model_name=call.envelope.model)
        cl.note_gateway_response(
            call, SimpleNamespace(headers={"x-agentcc-request-id": "gw-call-site"})
        )
    return call


class _GatewayError(Exception):
    """Shaped like the OpenAI client's APIStatusError for a gateway refusal."""

    def __init__(self, status, code, *, retry_after=None, **metadata):
        error = {
            "type": {402: "payment_required", 429: "rate_limit_error"}.get(
                status, "server_error"
            ),
            "code": code,
            "message": "internal gateway diagnostic text",
            "metadata": metadata,
        }
        super().__init__(f"Error code: {status} - {{'error': {error}}}")
        self.status_code = status
        self.code = code
        self.body = error
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self.response = SimpleNamespace(status_code=status, headers=headers)


# --------------------------------------------------------------------------
# R4-12 dataset Run Prompt (RunPrompts.process_row)
# --------------------------------------------------------------------------


@pytest.fixture
def run_prompt_harness(monkeypatch, organization, workspace):
    emitter = pytest.importorskip("ee.usage.services.emitter")
    from model_hub.views import run_prompt as view

    timeline = []
    emitted = []
    cells = MagicMock()
    behaviour = {"claim": True, "error": None}

    def _emit(event):
        timeline.append("emit")
        emitted.append(event)

    def _litellm_response(self):
        active = _transport_claim() if behaviour["claim"] else cl.current_managed_call()
        timeline.append(("dispatch", active))
        if behaviour["error"] is not None:
            if active is not None and behaviour["claim"]:
                cl.note_gateway_failure(active, behaviour["error"])
            raise behaviour["error"]
        return "answer", _value_info()

    monkeypatch.setattr(emitter, "emit", _emit, raising=False)
    monkeypatch.setattr(view, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(view, "populate_placeholders", lambda *a, **k: list(MESSAGES))
    monkeypatch.setattr(view, "close_old_connections", lambda: None)
    monkeypatch.setattr(view, "Cell", cells)
    # Candidate selective-rerun bookkeeping is unrelated to the billing seam.
    from model_hub.services import selective_rerun

    monkeypatch.setattr(selective_rerun, "input_fingerprints", lambda *args: {})
    monkeypatch.setattr(selective_rerun, "save_fingerprints", lambda *args: None)
    monkeypatch.setattr(view.RunPrompt, "litellm_response", _litellm_response)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=workspace)
    runner = view.RunPrompts(run_prompt_id=str(uuid.uuid4()))
    runner.run_prompt_model = SimpleNamespace(
        organization=organization,
        dataset=dataset,
        messages=list(MESSAGES),
        model="gpt-4o",
        temperature=0.1,
        frequency_penalty=None,
        presence_penalty=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format="string",
        run_prompt_config={},
    )
    runner.tools_config = []
    runner.is_editing = False
    return SimpleNamespace(
        runner=runner,
        row=SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        column=SimpleNamespace(id=uuid.uuid4()),
        timeline=timeline,
        emitted=emitted,
        cells=cells,
        behaviour=behaviour,
    )


def _dispatches(timeline):
    return [entry[1] for entry in timeline if isinstance(entry, tuple)]


def _cell(cells):
    kwargs = cells.objects.create.call_args.kwargs
    return kwargs, json.loads(kwargs["value_infos"])


@pytest.mark.requires_ee
@pytest.mark.django_db
class TestDatasetRunPromptMetering:
    def test_managed_row_claimed_is_held_before_dispatch_and_settled_once(
        self, run_prompt_harness, organization, workspace
    ):
        from model_hub.models.choices import CellStatus

        h = run_prompt_harness
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("2")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            h.runner.process_row(h.row, h.column)

        dispatches = _dispatches(h.timeline)
        assert len(dispatches) == 1
        active = dispatches[0]
        assert active is not None, "the provider call ran without a credit hold"
        assert active.status == "settled"
        assert active.dispatched is True
        assert _ops(ledger) == ["hold", "settle"]
        hold = ledger.holds[active.hold_id]
        assert hold.purpose == cl.PURPOSE_DATASET_RUN_PROMPT
        assert hold.org_id == str(organization.id)
        assert hold.workspace_id == str(workspace.id)
        assert hold.settled_amount == Decimal("0.0123")
        assert active.envelope.row_id == str(h.row.id)
        assert active.envelope.call_ref == str(h.column.id)

        kwargs, info = _cell(h.cells)
        assert kwargs["status"] == CellStatus.PASS.value
        assert info["credit_settlement"]["request_id"] == active.request_id
        assert info["credit_settlement"]["settled"] == "0.0123"

        assert h.emitted == [], "ledger settlement is the sole debit"
        assert ledger.balance_of(organization) == Decimal("2") - hold.settled_amount

    def test_unclaimed_managed_row_is_released_and_the_cell_is_typed(
        self, run_prompt_harness, organization
    ):
        """OKE-SEC-F2: the row was not dispatched on the managed credential."""
        from model_hub.models.choices import CellStatus

        h = run_prompt_harness
        h.behaviour["claim"] = False
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("2")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            h.runner.process_row(h.row, h.column)

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("2")
        assert h.emitted == []
        kwargs, info = _cell(h.cells)
        assert kwargs["status"] == CellStatus.ERROR.value
        assert info["error"]["code"] == "CREDIT_HOLD_MISSING"

    def test_exhausted_credit_fails_the_row_without_dispatch(
        self, run_prompt_harness, organization
    ):
        from model_hub.models.choices import CellStatus

        h = run_prompt_harness
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("0")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            h.runner.process_row(h.row, h.column)

        assert _dispatches(h.timeline) == []
        assert h.emitted == []
        assert "settle" not in _ops(ledger)
        kwargs, info = _cell(h.cells)
        assert kwargs["status"] == CellStatus.ERROR.value
        assert info["error"]["code"] == "managed_credits_exhausted"
        assert info["error"]["billing_url"]
        assert info["reason"] == kwargs["value"]

    def test_claimed_gateway_budget_refusal_releases_and_types_the_cell(
        self, run_prompt_harness, organization
    ):
        from model_hub.models.choices import CellStatus

        h = run_prompt_harness
        h.behaviour["error"] = _GatewayError(429, "org_budget_exceeded", scope="org")
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("2")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            h.runner.process_row(h.row, h.column)

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("2")
        kwargs, info = _cell(h.cells)
        assert kwargs["status"] == CellStatus.ERROR.value
        assert info["error"]["code"] == "org_budget_exceeded"
        assert info["error"]["retryable"] is False
        assert "diagnostic" not in kwargs["value"]

    def test_byok_row_is_unchanged(self, run_prompt_harness, organization):
        from model_hub.models.choices import CellStatus

        h = run_prompt_harness
        ledger = cl.MockCreditLedger()  # zero balance: any hold would refuse

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_byok):
            h.runner.process_row(h.row, h.column)

        assert ledger.calls == []
        assert h.timeline[0] == "emit", "legacy dual-write still precedes the call"
        assert _dispatches(h.timeline) == [None]
        event = h.emitted[0]
        assert event.event_type == "dataset_run_prompt"
        assert event.properties == {
            "source": "dataset_run_prompt",
            "source_id": h.runner.run_prompt_id,
        }
        assert event.amount == 1
        kwargs, info = _cell(h.cells)
        assert kwargs["status"] == CellStatus.PASS.value
        assert "credit_settlement" not in info


# --------------------------------------------------------------------------
# R4-13 experiment prompt rows (provider_policy.prompt_response)
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestExperimentPromptMetering:
    @pytest.fixture
    def experiment(self, monkeypatch, organization, workspace):
        from model_hub.services import experiment_row_budget

        monkeypatch.setattr(
            experiment_row_budget, "budget_for_prompt", lambda *a, **k: None
        )
        return SimpleNamespace(
            id=uuid.uuid4(),
            dataset=SimpleNamespace(organization=organization, workspace=workspace),
        )

    @staticmethod
    def _run_prompt(seen, *, claim=True, errors=None):
        errors = list(errors or [])

        def litellm_response():
            active = _transport_claim() if claim else cl.current_managed_call()
            seen.append(active)
            if errors:
                error = errors.pop(0)
                if active is not None and claim:
                    cl.note_gateway_failure(active, error)
                raise error
            return "answer", _value_info(cost=0.02)

        return SimpleNamespace(
            model="gpt-4o",
            messages=list(MESSAGES),
            max_tokens=None,
            litellm_response=litellm_response,
        )

    def test_managed_prompt_row_settles_on_its_own_meter(
        self, experiment, organization
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})
        seen = []
        row_id, column_id = uuid.uuid4(), uuid.uuid4()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            response, info = prompt_response(
                self._run_prompt(seen), experiment, row_id, column_id
            )

        assert response == "answer"
        active = seen[0]
        assert active is not None, "the prompt row ran without a credit hold"
        hold = ledger.holds[active.hold_id]
        assert hold.purpose == cl.PURPOSE_EXPERIMENT_PROMPT
        assert hold.settled_amount == Decimal("0.02")
        assert active.envelope.source_id == str(experiment.id)
        assert active.envelope.row_id == str(row_id)
        assert active.envelope.call_ref == str(column_id)
        assert info["credit_settlement"]["purpose"] == cl.PURPOSE_EXPERIMENT_PROMPT
        assert info["credit_settlement"]["settled"] == "0.02"

    def test_unclaimed_prompt_row_is_refused_and_released(
        self, experiment, organization
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with pytest.raises(cl.CreditHoldMissingError):
                prompt_response(
                    self._run_prompt([], claim=False),
                    experiment,
                    uuid.uuid4(),
                    uuid.uuid4(),
                )

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("1")

    def test_exhausted_credit_is_non_retryable_and_never_dispatches(
        self, experiment, organization
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("0")})
        seen = []

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with pytest.raises(cl.CreditsExhaustedError) as caught:
                prompt_response(
                    self._run_prompt(seen), experiment, uuid.uuid4(), uuid.uuid4()
                )

        # experiment_runner persists a ValueError row and does not re-raise it.
        assert isinstance(caught.value, ValueError)
        assert seen == []

    def test_in_flight_insufficient_retries_after_retry_after_then_settles(
        self, experiment, organization, _meter_safety_and_sleeps
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        sleeps = _meter_safety_and_sleeps
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})
        ledger.refuse_next_holds = [
            cl.refusal_for_code(
                "managed_credits_insufficient",
                metadata={"limit_source": "agentcc_in_flight", "remedy_hint": "retry"},
                retry_after=2,
            )
        ]
        seen = []

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            response, info = prompt_response(
                self._run_prompt(seen), experiment, uuid.uuid4(), uuid.uuid4()
            )

        assert response == "answer"
        assert sleeps == [2]
        assert _ops(ledger) == ["hold", "hold", "settle"]
        assert len(seen) == 1
        assert info["credit_settlement"]["status"] == "settled"

    def test_spend_velocity_on_dispatch_releases_once_without_retry(
        self, experiment, organization, _meter_safety_and_sleeps
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        sleeps = _meter_safety_and_sleeps
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})
        seen = []
        run_prompt = self._run_prompt(
            seen, errors=[_GatewayError(429, "managed_spend_velocity", retry_after=4)]
        )

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                prompt_response(run_prompt, experiment, uuid.uuid4(), uuid.uuid4())

        assert isinstance(caught.value, ValueError)
        assert caught.value.retryable is False
        assert caught.value.retry_after is None
        assert caught.value.charged is False
        assert sleeps == []
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(organization) == Decimal("1")
        assert len(seen) == 1

    def test_terminal_budget_refusal_is_typed_and_not_retried(
        self, experiment, organization, _meter_safety_and_sleeps
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        sleeps = _meter_safety_and_sleeps
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})
        seen = []
        run_prompt = self._run_prompt(
            seen, errors=[_GatewayError(429, "budget_exceeded")]
        )

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with pytest.raises(cl.BudgetExceededError) as caught:
                prompt_response(run_prompt, experiment, uuid.uuid4(), uuid.uuid4())

        assert isinstance(caught.value, ValueError)
        assert sleeps == []
        assert len(seen) == 1
        assert _ops(ledger) == ["hold", "release"]

    def test_billing_unavailable_is_retryable_and_never_dispatches(
        self, experiment, organization
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})
        ledger.refuse_next_holds = [cl.refusal_for_code("managed_billing_unavailable")]
        seen = []

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with pytest.raises(cl.CreditLedgerUnavailableError) as caught:
                prompt_response(
                    self._run_prompt(seen), experiment, uuid.uuid4(), uuid.uuid4()
                )

        # Not a ValueError: experiment_runner re-raises it and Temporal retries.
        assert not isinstance(caught.value, ValueError)
        assert caught.value.retryable is True
        assert seen == []

    @pytest.mark.parametrize(
        "failure, charged",
        [
            ("empty_503", None),
            ("timeout", None),
            ("unknown_401", None),
            ("unknown_403", None),
        ],
    )
    def test_real_transport_failure_is_terminal_through_prompt_response(
        self, experiment, organization, failure, charged, _meter_safety_and_sleeps
    ):
        from tfc.temporal.experiments.provider_policy import prompt_response

        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
        from model_hub.services import model_route as mr
        from model_hub.tests.test_model_route_support import (
            OPENAI_CHAT_MODEL,
            PRIVATE_UPSTREAM_TEXT,
            FakeGatewayClient,
            GatewayStatusError,
            eligible_port,
        )

        raw = (
            TimeoutError(PRIVATE_UPSTREAM_TEXT)
            if failure == "timeout"
            else GatewayStatusError(
                {"empty_503": 503, "unknown_401": 401, "unknown_403": 403}[failure], {}
            )
        )
        client = FakeGatewayClient(error=raw)
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        prompt = RunPrompt(
            model=OPENAI_CHAT_MODEL,
            messages=list(MESSAGES),
            organization_id=organization.id,
            workspace_id=experiment.dataset.workspace.id,
            output_format="string",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )
        with cl.use_credit_ledger(ledger), mr.managed_routing_port(port):
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                prompt_response(prompt, experiment, uuid.uuid4(), uuid.uuid4())
        error = caught.value
        assert isinstance(
            error, ValueError
        )  # Actual row handler excludes ValueError from retries.
        assert cl.refusal_activity_error(error)["non_retryable"] is True
        assert error.retryable is False
        assert error.retry_after is None
        assert error.charged is charged
        assert error.code == (
            "MANAGED_OUTCOME_PENDING_RECONCILE"
            if charged is None
            else "MANAGED_CALL_REFUSED"
        )
        assert PRIVATE_UPSTREAM_TEXT not in str(error)
        assert _meter_safety_and_sleeps == []
        assert len(client.calls) == len(port.authorize_calls) == 1
        assert _ops(ledger) == (["hold"] if charged is None else ["hold", "release"])
        assert ledger.balance_of(organization) == Decimal("0.90" if charged else "1")

    def test_byok_prompt_row_is_unchanged(self, experiment):
        from tfc.temporal.experiments.provider_policy import prompt_response

        ledger = cl.MockCreditLedger()
        seen = []

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_byok):
            response, info = prompt_response(
                self._run_prompt(seen), experiment, uuid.uuid4(), uuid.uuid4()
            )

        assert response == "answer"
        assert info == _value_info(cost=0.02)
        assert seen == [None]
        assert ledger.calls == []


# --------------------------------------------------------------------------
# Agent graph nodes (provider_policy.run_graph_node), OKE-SEC-F6
# --------------------------------------------------------------------------


class TestGraphNodeDispatch:
    @pytest.fixture(autouse=True)
    def _no_graph_budget(self, monkeypatch):
        from model_hub.services import experiment_row_budget

        monkeypatch.setattr(
            experiment_row_budget, "budget_for_graph", lambda *a, **k: None
        )

    def test_managed_dispatch_inside_a_graph_node_is_refused_before_dispatch(self):
        from tfc.temporal.experiments.provider_policy import run_graph_node

        dispatched = []

        class _ManagedRunner:
            def run(self, config, inputs, execution_context):
                cl.require_managed_hold(model_name="gpt-4o")  # OK-A transport
                dispatched.append(1)
                return {"response": "x"}

        with pytest.raises(cl.ManagedDispatchUnavailableError) as caught:
            run_graph_node(_ManagedRunner(), {}, {}, {}, str(uuid.uuid4()))

        assert dispatched == []
        assert "own provider key" in caught.value.message

    def test_byok_graph_node_is_unchanged(self):
        from tfc.temporal.experiments.provider_policy import run_graph_node

        runner = SimpleNamespace(run=lambda config, inputs, ctx: {"response": "ok"})

        assert run_graph_node(runner, {}, {}, {}, str(uuid.uuid4())) == {
            "response": "ok"
        }


# --------------------------------------------------------------------------
# Prompt playground (async_prompt_runner.run_template_async), F8
# --------------------------------------------------------------------------


class _WsManager:
    def __init__(self):
        self.errors = []

    async def notify_process_started(self, **kwargs):
        return None

    async def send_error_message(self, **kwargs):
        self.errors.append(kwargs)

    async def send_all_completed_message(self, **kwargs):
        return None


@pytest.fixture
def playground(monkeypatch):
    from model_hub.utils import async_prompt_runner as runner_module
    from model_hub.views import prompt_template

    org = SimpleNamespace(id=uuid.uuid4())
    ws = SimpleNamespace(id=uuid.uuid4(), organization_id=org.id)
    state = {"claim": True}

    class _RunPrompt:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def litellm_response_async(self, **kwargs):
            if state["claim"]:
                _transport_claim()
            return "answer", _value_info(cost=0.01)

    monkeypatch.setattr(runner_module, "RunPrompt", _RunPrompt)
    monkeypatch.setattr(runner_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(runner_module, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(
        runner_module,
        "Organization",
        SimpleNamespace(
            objects=SimpleNamespace(get=lambda id: org), DoesNotExist=LookupError
        ),
    )
    monkeypatch.setattr(prompt_template, "replace_variables", lambda m, *a, **k: m)
    monkeypatch.setattr(prompt_template, "remove_empty_text_from_messages", lambda m: m)
    try:
        from ee.usage.services import emitter
    except ImportError:
        emitter = None
    if emitter is not None:
        monkeypatch.setattr(emitter, "emit", lambda event: None, raising=False)

    execution = SimpleNamespace(
        id=uuid.uuid4(),
        variable_names={},
        output=None,
        metadata=None,
        template_version="v1",
        prompt_config_snapshot={
            "messages": list(MESSAGES),
            "configuration": {"model": "gpt-4o", "max_tokens": 64},
        },
        save=lambda update_fields=None: None,
    )
    template = SimpleNamespace(id=uuid.uuid4())
    ws_manager = _WsManager()

    def _run():
        asyncio.run(
            runner_module.run_template_async(
                template, execution, org.id, None, "prompt", None, ws, ws_manager
            )
        )
        return execution

    return SimpleNamespace(org=org, run=_run, ws_manager=ws_manager, state=state)


@pytest.mark.django_db(transaction=True)
class TestPromptPlaygroundMetering:
    def test_managed_playground_call_is_settled_and_annotated(self, playground):
        ledger = cl.MockCreditLedger(balances={playground.org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            execution = playground.run()

        assert _ops(ledger) == ["hold", "settle"]
        assert execution.output == ["answer"]
        settlement = execution.metadata[0]["credit_settlement"]
        assert settlement["purpose"] == cl.PURPOSE_PROMPT_PLAYGROUND
        assert settlement["status"] == "settled"

    def test_playground_refusal_is_typed_in_value_infos(self, playground):
        ledger = cl.MockCreditLedger(balances={playground.org.id: Decimal("0")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            execution = playground.run()

        info = execution.metadata[0]
        assert info["error"]["code"] == "managed_credits_exhausted"
        assert info["reason"] == execution.output[0]
        assert playground.ws_manager.errors[0]["error"] == info["reason"]
        assert _ops(ledger) == ["hold"]


# --------------------------------------------------------------------------
# R4-16 optimizer evaluator refusal is terminal (DirectEvaluator)
# --------------------------------------------------------------------------


def _eval_config():
    template = SimpleNamespace(
        id=uuid.uuid4(),
        config={"eval_type_id": "NotARealEval", "output": "score"},
        model=None,
        name="quality",
        choice_scores=None,
    )
    return SimpleNamespace(
        eval_template=template, mapping={"output": "output"}, config={}
    )


def _usage_refusal():
    exceptions = pytest.importorskip("ee.usage.exceptions")
    events = pytest.importorskip("ee.usage.schemas.events")
    return exceptions.UsageLimitExceeded(
        events.CheckResult(
            allowed=False,
            reason="Free tier limit reached",
            error_code="FREE_TIER_LIMIT",
        )
    )


def _rate_limited_refusal():
    # base model_hub/views/utils/evals.py:323 for a RATE_LIMITED usage row
    return ValueError("API call not allowed : ", "rate_limited")


def _scope(organization, workspace, run_id="run-1"):
    return cl.billing_scope(
        organization=organization,
        workspace=workspace,
        source="dataset_optimization",
        source_id=run_id,
    )


def _fake_trial_model_call(monkeypatch, fake):
    """Replace RunPrompt.litellm_response_async, the trial's model call."""
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    monkeypatch.setattr(RunPrompt, "litellm_response_async", fake)


@pytest.mark.requires_ee
@pytest.mark.django_db
class TestOptimizerEvaluatorRefusalIsTerminal:
    @pytest.fixture
    def module(self):
        return pytest.importorskip("ee.agenthub.fix_your_agent.direct_evaluator")

    @pytest.fixture
    def evals_module(self):
        from model_hub.views.utils import evals

        return evals

    def test_two_argument_rate_limited_refusal_never_falls_back_and_stops_the_run(
        self, module, evals_module, monkeypatch, organization, workspace
    ):
        """OKE-SEC-F1 / R4-16."""
        refusal = _rate_limited_refusal()
        run_eval = MagicMock(side_effect=refusal)
        monkeypatch.setattr(evals_module, "run_eval_func", run_eval)
        fallback = MagicMock(return_value=(1.0, "direct"))

        with cl.use_route_resolver(_byok):
            with _scope(organization, workspace) as scope:
                evaluator = module.DirectEvaluator(
                    user_eval_configs=[_eval_config()],
                    organization=organization,
                    workspace=workspace,
                )
                monkeypatch.setattr(evaluator, "_run_eval_direct", fallback)
                with pytest.raises(ValueError) as caught:
                    evaluator._run_eval_with_config(
                        _eval_config(), {"question": "q"}, "output text", "prompt"
                    )
                with pytest.raises(ValueError):
                    evaluator._run_eval_with_config(
                        _eval_config(), {"question": "q"}, "output text", "prompt"
                    )

        assert caught.value is refusal
        fallback.assert_not_called()
        assert run_eval.call_count == 1  # the second eval never ran
        assert scope.refusal is refusal
        assert cl.refusal_activity_error(caught.value)["type"] == "RATE_LIMITED"

    def test_usage_refusal_never_falls_back_to_the_direct_evaluator(
        self, module, evals_module, monkeypatch, organization, workspace
    ):
        refusal = _usage_refusal()
        monkeypatch.setattr(
            evals_module, "run_eval_func", MagicMock(side_effect=refusal)
        )
        evaluator = module.DirectEvaluator(
            user_eval_configs=[_eval_config()],
            organization=organization,
            workspace=workspace,
        )
        fallback = MagicMock(return_value=(1.0, "direct"))
        monkeypatch.setattr(evaluator, "_run_eval_direct", fallback)

        with pytest.raises(type(refusal)):
            evaluator._run_eval_with_config(
                _eval_config(), {"question": "q"}, "output text", "prompt"
            )

        fallback.assert_not_called()

    def test_ordinary_eval_errors_still_use_the_fallback(
        self, module, evals_module, monkeypatch, organization, workspace
    ):
        monkeypatch.setattr(
            evals_module,
            "run_eval_func",
            MagicMock(side_effect=ValueError("mapping 'output' missing")),
        )
        evaluator = module.DirectEvaluator(
            user_eval_configs=[_eval_config()],
            organization=organization,
            workspace=workspace,
        )
        fallback = MagicMock(return_value=(0.5, "direct"))
        monkeypatch.setattr(evaluator, "_run_eval_direct", fallback)

        result = evaluator._run_eval_with_config(
            _eval_config(), {"question": "q"}, "output text", "prompt"
        )

        assert result == (0.5, "direct")
        fallback.assert_called_once()

    def test_refusal_propagates_out_of_the_trial_instead_of_scoring_zero(
        self, module, evals_module, monkeypatch, organization, workspace
    ):
        refusal = _rate_limited_refusal()

        async def _litellm_response_async(run_prompt, **kwargs):
            return "trial output", _value_info()

        monkeypatch.setattr(
            evals_module, "run_eval_func", MagicMock(side_effect=refusal)
        )
        _fake_trial_model_call(monkeypatch, _litellm_response_async)
        evaluator = module.DirectEvaluator(
            user_eval_configs=[_eval_config()],
            organization=organization,
            workspace=workspace,
        )
        fallback = MagicMock(return_value=(1.0, "direct"))
        monkeypatch.setattr(evaluator, "_run_eval_direct", fallback)

        with pytest.raises(ValueError) as caught:
            asyncio.run(
                evaluator._evaluate_single_input_impl(
                    0, {"agent_prompt": "Say {x}", "input": {"x": "hi"}}
                )
            )

        assert caught.value is refusal
        fallback.assert_not_called()


# --------------------------------------------------------------------------
# R4-15 optimizer trial / proposal / teacher calls inside the run scope
# --------------------------------------------------------------------------


@pytest.mark.requires_ee
@pytest.mark.django_db
class TestOptimizerDirectDispatch:
    def test_managed_trial_route_is_refused_before_repair_and_dispatch(
        self, monkeypatch, organization, workspace
    ):
        """OKE-SEC-F2 / F6: the trial site dispatches on direct provider
        credentials, so a Future AGI credits route is refused before the
        template repair call and before the trial call."""
        module = pytest.importorskip("ee.agenthub.fix_your_agent.direct_evaluator")
        dispatched, repaired = [], []

        async def _litellm_response_async(run_prompt, **kwargs):
            dispatched.append(run_prompt)
            return "variant", _value_info()

        _fake_trial_model_call(monkeypatch, _litellm_response_async)
        monkeypatch.setattr(
            module,
            "repair_template_variables",
            lambda **kwargs: repaired.append(kwargs) or kwargs["candidate_prompt"],
        )
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("10")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with _scope(organization, workspace) as scope:
                evaluator = module.DirectEvaluator(
                    user_eval_configs=[_eval_config()],
                    initial_agent_prompt="Answer {{question}}",
                    template_variables={"question"},
                    organization=organization,
                    workspace=workspace,
                )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    asyncio.run(
                        evaluator._evaluate_single_input_impl(
                            0, {"agent_prompt": "Answer briefly", "input": {}}
                        )
                    )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    asyncio.run(evaluator._call_llm_async("Say hi"))

        assert dispatched == []
        assert repaired == []
        assert isinstance(scope.refusal, cl.ManagedDispatchUnavailableError)
        assert ledger.calls == []

    def test_byok_trial_is_unchanged_and_never_touches_the_ledger(
        self, monkeypatch, organization, workspace
    ):
        module = pytest.importorskip("ee.agenthub.fix_your_agent.direct_evaluator")
        captured = []

        async def _litellm_response_async(run_prompt, **kwargs):
            captured.append(
                {
                    "temperature": run_prompt.temperature,
                    "seed": run_prompt.run_prompt_config.get("seed"),
                    "model": run_prompt.model,
                    "messages": run_prompt.messages,
                }
            )
            return "trial output", _value_info()

        _fake_trial_model_call(monkeypatch, _litellm_response_async)
        ledger = cl.MockCreditLedger()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_byok):
            with _scope(organization, workspace):
                evaluator = module.DirectEvaluator(
                    user_eval_configs=[],
                    organization=organization,
                    workspace=workspace,
                )
                output = asyncio.run(evaluator._call_llm_async("Say hi"))

        assert output == "trial output"
        assert ledger.calls == []
        assert captured == [
            {
                "temperature": 0.1,
                "seed": 47,
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Say hi"}],
            }
        ]

    def test_dispatch_guard_runs_before_every_trial_model_call(
        self, monkeypatch, organization, workspace
    ):
        module = pytest.importorskip("ee.agenthub.fix_your_agent.direct_evaluator")
        order = []
        guard = cl.aguard_direct_dispatch

        async def _recording_guard(scope, **kwargs):
            order.append("guard")
            return await guard(scope, **kwargs)

        async def _litellm_response_async(run_prompt, **kwargs):
            order.append("dispatch")
            return "trial output", _value_info()

        monkeypatch.setattr(cl, "aguard_direct_dispatch", _recording_guard)
        _fake_trial_model_call(monkeypatch, _litellm_response_async)

        with cl.use_credit_ledger(cl.MockCreditLedger()), cl.use_route_resolver(_byok):
            with _scope(organization, workspace):
                evaluator = module.DirectEvaluator(
                    user_eval_configs=[],
                    organization=organization,
                    workspace=workspace,
                )
                asyncio.run(evaluator._call_llm_async("Say hi"))
                asyncio.run(evaluator._call_llm_async("Say bye"))

        # Each own-key model call is guarded right before it is dispatched.
        assert order == ["guard", "dispatch", "guard", "dispatch"]

        order.clear()
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("10")})
        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with _scope(organization, workspace):
                evaluator = module.DirectEvaluator(
                    user_eval_configs=[],
                    organization=organization,
                    workspace=workspace,
                )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    asyncio.run(
                        evaluator._evaluate_single_input_impl(
                            0, {"agent_prompt": "Say {x}", "input": {"x": "hi"}}
                        )
                    )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    asyncio.run(evaluator._call_llm_async("Say hi"))

        # On a managed route the guard refuses and the model is never called.
        assert "guard" in order
        assert "dispatch" not in order

    def test_managed_route_with_an_own_key_is_refused_before_dispatch(
        self, monkeypatch, organization, workspace
    ):
        """OKE-SEC-F2: the generator dispatches the caller's own provider key,
        so a credits route for the same call is a payer split; nothing runs."""
        module = pytest.importorskip("ee.agent_opt.generators.litellm_generator")
        dispatched = []
        monkeypatch.setattr(
            module.litellm,
            "completion",
            lambda **kwargs: dispatched.append(kwargs) or _response(),
        )
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("10")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            with _scope(organization, workspace) as scope:
                proposal = module.LiteLLMGenerator(
                    model="gpt-4o", prompt_template="Improve: {prompt}"
                )
                teacher = module.LiteLLMGenerator(
                    model="gpt-4o",
                    prompt_template="{prompt}",
                    billing_purpose=cl.PURPOSE_OPTIMIZER_TEACHER,
                )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    proposal.generate({"prompt": "p"}, api_key=OWN_KEY)
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    teacher.generate({"prompt": "p"}, api_key=OWN_KEY)

        assert dispatched == []
        assert ledger.calls == []
        assert isinstance(scope.refusal, cl.ManagedDispatchUnavailableError)

    def test_byok_generator_inside_a_run_scope_is_unchanged(
        self, monkeypatch, organization, workspace
    ):
        module = pytest.importorskip("ee.agent_opt.generators.litellm_generator")
        captured = []
        monkeypatch.setattr(
            module.litellm,
            "completion",
            lambda **kwargs: captured.append(kwargs) or _response("proposal"),
        )
        ledger = cl.MockCreditLedger()
        resolved = []

        def _resolver(**kwargs):
            resolved.append(kwargs["operation"])
            return _byok()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_resolver):
            with _scope(organization, workspace):
                generator = module.LiteLLMGenerator(
                    model="gpt-4o", prompt_template="Improve: {prompt}"
                )
                assert (
                    generator.generate(
                        {"prompt": "p"}, api_key=OWN_KEY, temperature=0.3
                    )
                    == "proposal"
                )
                assert (
                    generator.generate({"prompt": "q"}, api_key=OWN_KEY) == "proposal"
                )

        assert ledger.calls == []
        assert resolved == ["run_prompt"]  # resolved once, then cached in the scope
        first = captured[0]
        assert first["model"] == "gpt-4o"
        assert first["messages"][0]["content"] == "Improve: p"
        assert first["api_key"] is OWN_KEY
        assert first["drop_params"] is True
        assert first["temperature"] == 0.3

    def test_generator_outside_a_run_scope_is_unchanged(self, monkeypatch):
        module = pytest.importorskip("ee.agent_opt.generators.litellm_generator")
        captured = []
        monkeypatch.setattr(
            module.litellm,
            "completion",
            lambda **kwargs: captured.append(kwargs) or _response("plain"),
        )
        ledger = cl.MockCreditLedger()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            generator = module.LiteLLMGenerator(
                model="gpt-4o", prompt_template="Q: {q}"
            )
            assert generator.generate({"q": "x"}, temperature=0.2) == "plain"

        assert ledger.calls == []
        kwargs = captured[0]
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["messages"][0]["content"] == "Q: x"
        assert kwargs["api_key"] is None
        assert kwargs["drop_params"] is True
        assert kwargs["temperature"] == 0.2


@pytest.mark.django_db(transaction=True)
class TestComposeOptimizationDisclosure:
    @pytest.mark.parametrize(
        "error",
        [
            cl.ManagedDispatchUnavailableError(),
            ValueError("API call not allowed : ", "rate_limited"),
        ],
    )
    def test_terminal_refusal_survives_finalize(self, organization, workspace, error):
        from tfc.temporal.dataset_optimization import activities

        from model_hub.models.optimize_dataset import OptimizeDataset

        run = OptimizeDataset.objects.create(
            name="compose",
            organization=organization,
            workspace=workspace,
            optimize_type="PromptTemplate",
            environment="Training",
            version="v1",
        )
        refusal = cl.refusal_activity_error(error)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            activities._persist_terminal_refusal(run, [], refusal)
        if connection.vendor == "postgresql":
            root_locks = [
                q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]
            ]
            assert len(root_locks) == 1
            assert (
                f"FOR UPDATE OF {connection.ops.quote_name(OptimizeDataset._meta.db_table)}"
                in root_locks[0]
            )
        result = asyncio.run(
            activities.finalize_run_activity(
                {
                    "run_id": str(run.id),
                    "status": "failed",
                    "error": "Activity task failed",
                }
            )
        )
        run.refresh_from_db()
        assert run.error_message == refusal["message"]
        assert result["error"] == refusal["message"]
        assert result["error_code"] == refusal["type"]

    def test_retryable_refusal_does_not_persist_failure(self, organization, workspace):
        from tfc.temporal.dataset_optimization import activities

        from model_hub.models.optimize_dataset import OptimizeDataset

        run = OptimizeDataset.objects.create(
            name="compose",
            organization=organization,
            workspace=workspace,
            optimize_type="PromptTemplate",
            environment="Training",
            version="v1",
        )
        activities._persist_terminal_refusal(
            run,
            [],
            {
                "message": "Retry later",
                "type": "managed_billing_unavailable",
                "non_retryable": False,
            },
        )
        run.refresh_from_db()
        assert run.status == OptimizeDataset.StatusType.RUNNING

    def test_cancelled_run_is_not_rewritten_by_terminal_refusal(
        self, organization, workspace
    ):
        from tfc.temporal.dataset_optimization import activities

        from model_hub.models.optimize_dataset import OptimizeDataset

        run = OptimizeDataset.objects.create(
            name="cancelled-compose",
            organization=organization,
            workspace=workspace,
            optimize_type="PromptTemplate",
            environment="Training",
            version="v1",
            status=OptimizeDataset.StatusType.CANCELLED,
            optimizer_config={"keep": "cancellation"},
        )
        activities._persist_terminal_refusal(
            run, [], cl.refusal_activity_error(cl.ManagedDispatchUnavailableError())
        )
        run.refresh_from_db()
        assert run.status == OptimizeDataset.StatusType.CANCELLED
        assert run.optimizer_config == {"keep": "cancellation"}

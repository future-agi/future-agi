"""CreditLedgerPort semantics and the managed-call meter (ONEKEY OK-E r3).

RED at base 651ab5bb: ``model_hub.services.credit_ledger_port`` does not
exist, so every test in this module fails at import. The repair-round RED
reasons against the prior OK-E bytes are listed per test in STEP-2.json.

GREEN contract:

- ONEKEY-ASSUMED-CONTRACT-r1 ``holdSettle`` (still assumed): hold before
  dispatch, settle exactly once, release on refusal / failure / cancel, fail
  closed when no ledger is configured, tenancy bound before any route or
  credit operation, stable call and event identity, BYOK never touches the
  ledger.
- ONEKEY-REAL-REFUSAL-CONTRACT-r1: refusals key on ``error.code``;
  exhausted / agent budget / budget / org budget are terminal; insufficient
  retries only for ``agentcc_in_flight`` + ``retry`` after Retry-After; spend
  velocity and rate limits back off per Retry-After; billing unavailable
  fails closed and is retryable.
- Review OKE-SEC F1..F9: usage refusals in every raise form are terminal; a
  hold settles only when the managed transport claimed it; settlement failure
  is terminal and suspends managed holds; settle is retried only on a port
  that declares idempotent settle; tenancy before the route; class-default
  refusal text; provider-qualified model match.

Doubles: ``MockCreditLedger`` for the ledger, a payer-route resolver standing
in for OK-A's routing double, ``_claim()`` standing in for the OK-A managed
transport's ``require_managed_hold`` call, and gateway-shaped exceptions.
Tenancy checks run on real Organization / Workspace rows.
"""

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from model_hub.services import credit_ledger_port as cl

MESSAGES = [{"role": "user", "content": "Summarise the row."}]


@pytest.fixture(autouse=True)
def _meter_safety_and_sleeps(monkeypatch):
    """Fresh breaker / org-refusal memo per test; backoff sleeps are recorded."""
    sleeps = []
    monkeypatch.setattr(cl, "_sleep", sleeps.append)

    async def _asleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(cl, "_asleep", _asleep)
    cl.reset_meter_safety()
    yield sleeps
    cl.reset_meter_safety()


def _org():
    return SimpleNamespace(id=uuid.uuid4())


def _ws(org):
    return SimpleNamespace(id=uuid.uuid4(), organization_id=org.id)


def _managed(**_kwargs):
    return cl.PayerRoute.managed(provider="openai")


def _byok(**_kwargs):
    return cl.PayerRoute.byok(provider="openai")


def _envelope(org, ws, **overrides):
    fields = dict(
        organization=org,
        workspace=ws,
        purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
        source="dataset_run_prompt",
        source_id="rp-1",
        model="gpt-4o",
        row_id="row-1",
        call_ref="col-1",
    )
    fields.update(overrides)
    return cl.build_envelope(**fields)


def _meter(org, ws, *, route, **overrides):
    return cl.CallMeter(
        organization=org,
        workspace=ws,
        route=route,
        envelope=_envelope(org, ws, **overrides),
    )


def _ops(ledger):
    return [entry[0] for entry in ledger.calls]


def _claim(model="gpt-4o", *, gateway_id="gw-claimed"):
    """What the OK-A managed transport does right before a managed dispatch,
    plus what the gateway sends on every response: the x-agentcc-request-id
    RESPONSE header (the only reconcile key; r10). ``gateway_id=None`` models a
    transport that never saw a header; an invalid value models a malformed one."""
    call = cl.require_managed_hold(model_name=model)
    if gateway_id is not None:
        cl.note_gateway_response(call, SimpleNamespace(headers={"x-agentcc-request-id": gateway_id}))
    return call


class _GatewayError(Exception):
    """Shaped like the OpenAI client's APIStatusError for a gateway refusal."""

    TYPES = {402: "payment_required", 429: "rate_limit_error", 503: "server_error"}

    def __init__(self, status, code, *, retry_after=None, headers=None, **metadata):
        error = {
            "type": self.TYPES.get(status, "api_error"),
            "code": code,
            "message": "internal gateway diagnostic text",
            "param": None,
            "metadata": metadata,
        }
        super().__init__(f"Error code: {status} - {{'error': {error}}}")
        self.status_code = status
        self.code = code
        self.body = error
        response_headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        response_headers.update(headers or {})
        self.response = SimpleNamespace(status_code=status, headers=response_headers)


def _raise_at_gateway_boundary(error):
    """Simulate the trusted SDK boundary, not an arbitrary caller wrapper."""
    cl.note_gateway_failure(cl.current_managed_call(), error)
    raise error


def _in_flight(retry_after=2):
    return cl.refusal_for_code(
        cl.CODE_CREDITS_INSUFFICIENT,
        metadata={
            "limit_source": cl.LIMIT_SOURCE_IN_FLIGHT,
            "remedy_hint": cl.REMEDY_RETRY,
            "required_usd": "0.04",
            "available_usd": "0.01",
        },
        retry_after=retry_after,
    )


class _SettingsPort(cl.MockCreditLedger):
    """Stands in for the real port named by ``settings.FI_CREDIT_LEDGER_PORT``."""


def _settings_port_factory():
    return _SettingsPort()


class _OneShotSettlePort(cl.MockCreditLedger):
    """A port that does not declare idempotent settle."""

    idempotent_settle = False


class TestMockCreditLedger:
    def test_zero_balance_refusal_is_typed_credits_exhausted(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("0")})

        with pytest.raises(cl.CreditsExhaustedError) as caught:
            ledger.hold(
                org=org,
                workspace=None,
                amount_estimate=Decimal("0.05"),
                idempotency_key="dsx:k0",
                purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
            )

        error = caught.value
        assert error.code == "managed_credits_exhausted"
        assert isinstance(error, cl.InsufficientCreditError)
        assert isinstance(error, ValueError)
        assert error.retryable is False
        assert error.http_status == 402
        assert error.metadata["limit_source"] == cl.LIMIT_SOURCE_CREDITS
        assert error.metadata["remedy_hint"] == "add_credits"
        body = error.as_response_body()
        assert body["code"] == "managed_credits_exhausted"
        assert body["type"] == "payment_required"
        assert body["retryable"] is False
        assert body["billing_url"]
        assert body["metadata"]["required_usd"] == "0.05"
        assert ledger.holds == {}

    def test_short_balance_refusal_is_typed_insufficient_and_not_retryable(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("0.01")})

        with pytest.raises(cl.InsufficientCreditError) as caught:
            ledger.hold(
                org=org,
                workspace=None,
                amount_estimate=Decimal("0.05"),
                idempotency_key="dsx:k1",
                purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
            )

        error = caught.value
        assert error.code == "managed_credits_insufficient"
        assert not isinstance(error, cl.CreditsExhaustedError)
        assert error.metadata["limit_source"] == cl.LIMIT_SOURCE_CREDITS
        assert error.retryable is False
        assert error.backoff is False
        assert error.required == Decimal("0.05")
        assert error.available == Decimal("0.01")
        body = error.as_response_body()
        assert body["required"] == "0.05"
        assert body["available"] == "0.01"

    def test_hold_is_idempotent_per_key(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        kwargs = dict(
            org=org,
            workspace=None,
            amount_estimate=Decimal("0.2"),
            idempotency_key="dsx:k2",
            purpose="p",
        )

        first = ledger.hold(**kwargs)
        again = ledger.hold(**kwargs)

        assert first == again
        assert ledger.held_of(org) == Decimal("0.2")
        assert ledger.available_of(org) == Decimal("0.8")

    def test_reusing_a_key_for_another_org_is_refused(self):
        org, other = _org(), _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        ledger.hold(
            org=org,
            workspace=None,
            amount_estimate=Decimal("0.1"),
            idempotency_key="dsx:shared",
            purpose="p",
        )

        with pytest.raises(cl.CreditHoldStateError):
            ledger.hold(
                org=other,
                workspace=None,
                amount_estimate=Decimal("0.1"),
                idempotency_key="dsx:shared",
                purpose="p",
            )

    def test_settle_exactly_once(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        hold_id = ledger.hold(
            org=org,
            workspace=None,
            amount_estimate=Decimal("0.2"),
            idempotency_key="dsx:k3",
            purpose="p",
        )

        ledger.settle(hold_id, Decimal("0.05"))
        ledger.settle(hold_id, Decimal("0.05"))  # replay: no second debit

        assert ledger.balance_of(org) == Decimal("0.95")
        assert ledger.held_of(org) == Decimal("0")
        assert ledger.holds[hold_id].debits == 1
        with pytest.raises(cl.CreditHoldStateError):
            ledger.settle(hold_id, Decimal("0.06"))
        ledger.release(hold_id)  # a settled hold stays settled
        assert ledger.holds[hold_id].state == "settled"
        assert ledger.balance_of(org) == Decimal("0.95")

    def test_release_frees_the_hold_and_blocks_a_later_settle(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        hold_id = ledger.hold(
            org=org,
            workspace=None,
            amount_estimate=Decimal("0.5"),
            idempotency_key="dsx:k4",
            purpose="p",
        )

        ledger.release(hold_id)
        ledger.release(hold_id)

        assert ledger.available_of(org) == Decimal("1")
        with pytest.raises(cl.CreditHoldStateError):
            ledger.settle(hold_id, Decimal("0.1"))
        assert ledger.balance_of(org) == Decimal("1")

    def test_the_mock_declares_idempotent_settle(self):
        assert cl.MockCreditLedger.idempotent_settle is True
        assert _OneShotSettlePort.idempotent_settle is False

    def test_unconfigured_ledger_fails_closed_and_is_retryable(self, settings):
        settings.FI_CREDIT_LEDGER_PORT = None

        ledger = cl.get_credit_ledger()

        assert isinstance(ledger, cl.UnconfiguredCreditLedger)
        with pytest.raises(cl.CreditLedgerUnavailableError) as caught:
            ledger.hold(
                org=_org(),
                workspace=None,
                amount_estimate=Decimal("0.01"),
                idempotency_key="dsx:k5",
                purpose="p",
            )
        error = caught.value
        assert error.code == "managed_billing_unavailable"
        assert error.retryable is True
        assert error.backoff is False
        assert isinstance(error, cl.ManagedCallDeferred)
        assert not isinstance(error, ValueError)

    def test_the_real_port_is_named_in_one_setting(self, settings):
        settings.FI_CREDIT_LEDGER_PORT = f"{__name__}:_settings_port_factory"

        assert isinstance(cl.get_credit_ledger(), _SettingsPort)


class TestManagedMeter:
    def test_byok_route_never_touches_the_ledger(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger()  # zero balance: any hold would refuse

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.byok())
            with meter.metered(messages=MESSAGES) as call:
                assert call is None
                assert cl.current_managed_call() is None

        assert meter.is_managed is False
        assert ledger.calls == []

    def test_claimed_call_is_held_before_dispatch_and_settled_once(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("5")})
        seen = {}

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with meter.metered(messages=MESSAGES, max_tokens=64, amount_estimate="0.1") as call:
                seen["state"] = ledger.holds[call.hold_id].state
                seen["active"] = _claim()
                call.observe(value_info={"metadata": {"cost": {"total_cost": 0.0123}}})

        assert seen["active"] is call
        assert seen["state"] == "held"
        assert call.dispatched is True
        assert _ops(ledger) == ["hold", "settle"]
        hold = ledger.holds[call.hold_id]
        assert hold.state == "settled"
        assert hold.settled_amount == Decimal("0.0123")
        assert hold.idempotency_key == call.idempotency_key
        assert hold.purpose == cl.PURPOSE_DATASET_RUN_PROMPT
        assert hold.workspace_id == str(ws.id)
        assert ledger.balance_of(org) == Decimal("5") - Decimal("0.0123")
        assert call.status == "settled"
        summary = call.summary()
        assert summary["settled"] == "0.0123"
        assert summary["cost_source"] == "provider_usage"
        assert summary["payer"] == cl.PAYER_CREDITS
        assert cl.current_managed_call() is None

    def test_unclaimed_managed_call_is_released_and_refused(self):
        """OKE-SEC-F2: no managed transport claimed the hold, so the call was
        not dispatched on the managed credential; nothing is charged."""
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditHoldMissingError) as caught:
                with meter.metered(messages=MESSAGES) as call:
                    call.observe(cost=Decimal("0.01"))  # dispatched elsewhere

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.holds[call.hold_id].state == "released"
        assert ledger.balance_of(org) == Decimal("1")
        assert call.status == "unclaimed"
        assert cl.is_terminal_refusal(caught.value)

    def test_an_unclaimed_call_suspends_further_managed_holds(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditHoldMissingError):
                with meter.metered(messages=MESSAGES):
                    pass
            before = len(ledger.calls)
            with pytest.raises(cl.CreditLedgerUnavailableError):
                with meter.metered(messages=MESSAGES):
                    pytest.fail("dispatched while the meter is suspended")
            assert len(ledger.calls) == before

            cl.reset_meter_safety()
            with meter.metered(messages=MESSAGES) as call:
                _claim().observe(cost="0.01")
        assert ledger.holds[call.hold_id].state == "settled"

    def test_refusal_dispatches_nothing(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger()
        dispatched = []

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditsExhaustedError):
                with meter.metered(messages=MESSAGES) as call:
                    dispatched.append(call)

        assert dispatched == []
        assert _ops(ledger) == ["hold"]
        assert ledger.holds == {}

    def test_provider_failure_is_pending_and_terminal(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                with meter.metered(messages=MESSAGES) as call:
                    _claim()
                    raise RuntimeError("provider unavailable")

        assert _ops(ledger) == ["hold"]
        assert ledger.holds[call.hold_id].state == "held"
        assert call.status == "pending_reconcile"
        assert ledger.held_of(org) == call.amount_estimate

    def test_cancel_releases_the_hold(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(asyncio.CancelledError):
                with meter.metered(messages=MESSAGES) as call:
                    raise asyncio.CancelledError()

        assert ledger.holds[call.hold_id].state == "released"

    def test_unknown_usage_is_pending_never_estimate_and_releases_at_ttl(self):
        org = _org()
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("5")})
        with cl.use_credit_ledger(ledger):
            meter = _meter(org, _ws(org), route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
                with meter.metered(messages=MESSAGES, max_tokens=32) as call:
                    _claim(gateway_id=None).observe(response=SimpleNamespace())
            assert isinstance(caught.value, ValueError) and not caught.value.retryable
            assert call.amount_estimate > 0
            assert call.status == "pending_reconcile"
            assert call.actual_amount is None and call.cost_source is None
            assert ledger.holds[call.hold_id].settled_amount is None
            assert ledger.balance_of(org) == Decimal("5")
            args = dict(organization_id=str(org.id), gateway_request_id=None,
                        hold_id=call.hold_id, amount_estimate=call.amount_estimate,
                        opened_at=call.opened_at)
            assert cl.reconcile_pending_hold(**args, now=call.opened_at + cl.RECONCILE_TTL) == "released"
            assert cl.reconcile_pending_hold(**args, now=call.opened_at + cl.RECONCILE_TTL) == "released"
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("5")

    def test_idempotent_port_settle_is_retried_on_the_same_hold(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.fail_next_settles = 2

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with meter.metered(messages=MESSAGES) as call:
                _claim()
                call.observe(cost=Decimal("0.01"))

        assert _ops(ledger) == ["hold", "settle", "settle", "settle"]
        assert {entry[1] for entry in ledger.calls[1:]} == {call.hold_id}
        assert ledger.holds[call.hold_id].debits == 1
        assert ledger.balance_of(org) == Decimal("0.99")

    def test_non_idempotent_port_gets_exactly_one_settle_attempt(self):
        """OKE-SEC-F4: no replay of settle unless the port declares it safe."""
        org = _org()
        ws = _ws(org)
        ledger = _OneShotSettlePort(balances={org.id: Decimal("1")})
        ledger.fail_next_settles = 1

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditSettlementError):
                with meter.metered(messages=MESSAGES) as call:
                    _claim()
                    call.observe(cost=Decimal("0.01"))

        assert _ops(ledger) == ["hold", "settle"]
        assert ledger.holds[call.hold_id].state == "held"
        assert ledger.balance_of(org) == Decimal("1")

    def test_settlement_failure_is_terminal_and_stops_later_managed_calls(self):
        """OKE-SEC-F3: a failed settle is a terminal refusal and suspends new
        managed holds, so no further call is dispatched that cannot settle."""
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.fail_next_settles = 10

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditSettlementError) as caught:
                with meter.metered(messages=MESSAGES) as call:
                    _claim()
                    call.observe(cost=Decimal("0.01"))
            settled_attempts = len(ledger.calls)
            with pytest.raises(cl.CreditLedgerUnavailableError):
                with meter.metered(messages=MESSAGES):
                    pytest.fail("dispatched after a failed settlement")

        error = caught.value
        assert cl.is_terminal_refusal(error)
        assert cl.is_retryable_refusal(error) is False
        assert isinstance(error, ValueError)
        assert call.status == "settle_failed"
        assert ledger.holds[call.hold_id].state == "held"  # left for reconciliation
        assert ledger.balance_of(org) == Decimal("1")
        assert len(ledger.calls) == settled_attempts

    def test_port_returning_a_refusal_object_is_raised(self):
        class _ReturningPort(cl.MockCreditLedger):
            def hold(self, **kwargs):
                return cl.InsufficientCreditError(
                    required=Decimal("1"), available=Decimal("0")
                )

        org = _org()
        ws = _ws(org)
        dispatched = []

        with cl.use_credit_ledger(_ReturningPort()):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.InsufficientCreditError):
                with meter.metered(messages=MESSAGES):
                    dispatched.append(1)

        assert dispatched == []

    def test_port_refusal_body_is_typed_with_the_class_message(self):
        """Real contract shape from the port; its own message text is dropped (F8)."""

        class _BodyPort(cl.MockCreditLedger):
            def hold(self, **kwargs):
                return {
                    "error": {
                        "type": "payment_required",
                        "code": "managed_credits_exhausted",
                        "message": "internal ledger diagnostic text",
                        "metadata": {
                            "limit_source": "agentcc_credits",
                            "remedy_hint": "add_credits",
                            "required_usd": 0.04,
                            "available_usd": 0.0,
                            "scope": "org",
                            "resets_at": None,
                        },
                    }
                }

        org = _org()
        ws = _ws(org)

        with cl.use_credit_ledger(_BodyPort()):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditsExhaustedError) as caught:
                with meter.metered(messages=MESSAGES):
                    pytest.fail("dispatched without credit")

        error = caught.value
        assert "diagnostic" not in str(error)
        assert "diagnostic" not in error.as_response_body()["message"]
        assert error.message == cl.CreditsExhaustedError().message
        assert error.required == Decimal("0.04")
        assert error.metadata["scope"] == "org"

    def test_ledger_transport_error_fails_closed(self):
        class _BrokenPort(cl.MockCreditLedger):
            def hold(self, **kwargs):
                raise ConnectionError("ledger unreachable")

        org = _org()
        ws = _ws(org)
        dispatched = []

        with cl.use_credit_ledger(_BrokenPort()):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditLedgerUnavailableError) as caught:
                with meter.metered(messages=MESSAGES):
                    dispatched.append(1)

        assert dispatched == []
        assert caught.value.code == cl.CODE_BILLING_UNAVAILABLE
        assert caught.value.retryable is True

    def test_managed_dispatch_without_a_hold_is_refused(self):
        with pytest.raises(cl.CreditHoldMissingError):
            cl.require_managed_hold(model_name="gpt-4o")

        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditHoldMissingError):
                with meter.metered(messages=MESSAGES):
                    cl.require_managed_hold(model_name="another-model")
        assert _ops(ledger) == ["hold", "release"]

    def test_model_match_is_provider_qualified(self):
        """OKE-SEC-F9: azure/gpt-4o does not satisfy a hold for openai/gpt-4o."""
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            qualified = _meter(
                org, ws, route=cl.PayerRoute.managed(), model="openai/gpt-4o"
            )
            with pytest.raises(cl.CreditHoldMissingError):
                with qualified.metered(messages=MESSAGES):
                    cl.require_managed_hold(model_name="azure/gpt-4o")

            with qualified.metered(messages=MESSAGES) as call:
                _claim("gpt-4o").observe(cost="0.01")  # one side unqualified
            assert call.status == "settled"

            plain = _meter(org, ws, route=cl.PayerRoute.managed())
            with plain.metered(messages=MESSAGES) as call:
                _claim("openai/gpt-4o").observe(cost="0.01")
            assert call.status == "settled"

    def test_estimate_is_positive_and_grows_with_max_tokens(self):
        small = cl.estimate_call_cost("gpt-4o", MESSAGES, max_tokens=16)
        large = cl.estimate_call_cost("gpt-4o", MESSAGES, max_tokens=4096)

        assert Decimal("0") < small < large


class TestAsyncMeter:
    def test_async_hold_settle_and_refusal(self):
        org = _org()
        ws = _ws(org)
        meter = _meter(org, ws, route=cl.PayerRoute.managed())

        async def _run():
            async with meter.ametered(messages=MESSAGES) as call:
                assert cl.current_managed_call() is call
                _claim()
                call.observe(cost=Decimal("0.004"))
            return call

        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        with cl.use_credit_ledger(ledger):
            call = asyncio.run(_run())
        assert ledger.holds[call.hold_id].settled_amount == Decimal("0.004")
        assert _ops(ledger) == ["hold", "settle"]

        async def _refused():
            async with meter.ametered(messages=MESSAGES):
                raise AssertionError("dispatched without credit")

        empty = cl.MockCreditLedger()
        with cl.use_credit_ledger(empty):
            with pytest.raises(cl.CreditsExhaustedError):
                asyncio.run(_refused())
        assert _ops(empty) == ["hold"]

    def test_async_unclaimed_call_is_released(self):
        org = _org()
        ws = _ws(org)
        meter = _meter(org, ws, route=cl.PayerRoute.managed())
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        async def _run():
            async with meter.ametered(messages=MESSAGES):
                pass

        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditHoldMissingError):
                asyncio.run(_run())
        assert _ops(ledger) == ["hold", "release"]


@pytest.mark.django_db
class TestTenancyBoundBeforeCredit:
    @pytest.fixture
    def foreign(self, user):
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace

        other = Organization.objects.create(name="Other Org OK-E")
        return Workspace.objects.create(
            name="Foreign Workspace OK-E",
            organization=other,
            is_default=False,
            is_active=True,
            created_by=user,
        )

    @staticmethod
    def _spy(calls, route=_managed):
        def _resolver(**kwargs):
            calls.append(kwargs)
            return route(**kwargs)

        return _resolver

    def test_foreign_workspace_is_refused_before_the_route_is_resolved(
        self, organization, foreign
    ):
        """OKE-SEC-F5: tenancy binds before any route (opaque credential) lookup."""
        resolved = []
        ledger = cl.MockCreditLedger(default_balance=Decimal("10"))

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(self._spy(resolved)):
            with pytest.raises(cl.CreditTenancyError):
                cl.call_meter(
                    organization=organization,
                    workspace=foreign,
                    model_name="gpt-4o",
                    purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
                    source="dataset_run_prompt",
                    source_id="rp-1",
                    row_id="row-1",
                )

        assert resolved == []
        assert ledger.calls == []

    def test_foreign_workspace_is_refused_for_byok_too(self, organization, foreign):
        resolved = []

        with cl.use_route_resolver(self._spy(resolved, route=_byok)):
            with pytest.raises(cl.CreditTenancyError):
                cl.call_meter(
                    organization=organization,
                    workspace=foreign,
                    model_name="gpt-4o",
                    purpose=cl.PURPOSE_EXPERIMENT_PROMPT,
                    source="experiment",
                    source_id="exp-1",
                )

        assert resolved == []

    def test_billing_scope_binds_tenancy_before_any_route(self, organization, foreign):
        resolved = []

        with cl.use_route_resolver(self._spy(resolved)):
            with pytest.raises(cl.CreditTenancyError):
                with cl.billing_scope(
                    organization=organization,
                    workspace=foreign,
                    source="dataset_optimization",
                    source_id="run-1",
                ):
                    pytest.fail("scope opened across tenants")

        assert resolved == []

    def test_legacy_guard_binds_tenancy_first(self, organization, foreign):
        resolved = []

        with cl.use_route_resolver(self._spy(resolved)):
            with pytest.raises(cl.CreditTenancyError):
                cl.refuse_legacy_managed_optimizer(
                    organization=organization,
                    workspace=foreign,
                    model_name="gpt-4o",
                    surface="develop-optimiser",
                )

        assert resolved == []

    def test_managed_call_without_an_organization_is_refused(self, workspace):
        ledger = cl.MockCreditLedger(default_balance=Decimal("10"))
        meter = cl.CallMeter(
            organization=None,
            workspace=workspace,
            route=cl.PayerRoute.managed(),
            envelope=_envelope(None, workspace),
        )

        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditTenancyError):
                with meter.metered(messages=MESSAGES):
                    pytest.fail("dispatched without an organization")

        assert ledger.calls == []

    def test_same_org_workspace_is_held_with_its_identity(
        self, organization, workspace
    ):
        ledger = cl.MockCreditLedger(balances={organization.id: Decimal("1")})

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            meter = cl.call_meter(
                organization=organization,
                workspace=workspace,
                model_name="gpt-4o",
                purpose=cl.PURPOSE_DATASET_RUN_PROMPT,
                source="dataset_run_prompt",
                source_id="rp-1",
                row_id="row-1",
            )
            with meter.metered(messages=MESSAGES) as call:
                _claim()
                call.observe(cost=Decimal("0.001"))

        hold = ledger.holds[call.hold_id]
        assert hold.org_id == str(organization.id)
        assert hold.workspace_id == str(workspace.id)
        assert hold.state == "settled"


class TestStableIdentity:
    def test_logical_call_id_is_stable_and_discriminating(self):
        org = _org()
        ws = _ws(org)
        base = _envelope(org, ws)

        assert base.logical_call_id == _envelope(org, ws).logical_call_id
        assert base.logical_call_id != _envelope(org, ws, row_id="row-2").logical_call_id
        assert base.logical_call_id != _envelope(org, _ws(org)).logical_call_id
        assert (
            base.logical_call_id
            != _envelope(org, ws, purpose=cl.PURPOSE_EXPERIMENT_PROMPT).logical_call_id
        )
        uuid.UUID(base.logical_call_id)

    def test_each_dispatch_has_its_own_request_id_and_stable_event_ids(self):
        org = _org()
        ws = _ws(org)
        envelope = _envelope(org, ws)
        route = cl.PayerRoute.managed()

        first = cl.ManagedCall(envelope, route)
        second = cl.ManagedCall(envelope, route)
        replay = cl.ManagedCall(envelope, route, dispatch_id=first.dispatch_id)

        assert first.request_id != second.request_id
        assert first.idempotency_key == f"dsx:{first.request_id}"
        assert first.event_id("dataset_run_prompt") == first.event_id(
            "dataset_run_prompt"
        )
        assert first.event_id("dataset_run_prompt") != first.event_id("prompt_bench")
        assert replay.request_id == first.request_id
        assert replay.event_id("dataset_run_prompt") == first.event_id(
            "dataset_run_prompt"
        )
        uuid.UUID(first.event_id("dataset_run_prompt"))

    def test_envelope_properties_carry_attribution(self):
        org = _org()
        ws = _ws(org)
        props = _envelope(org, ws).properties()

        assert props["workspace_id"] == str(ws.id)
        assert props["row_id"] == "row-1"
        assert props["call_ref"] == "col-1"
        assert props["model"] == "gpt-4o"
        assert props["purpose"] == cl.PURPOSE_DATASET_RUN_PROMPT
        assert props["logical_call_id"] == _envelope(org, ws).logical_call_id


class TestUsageRefusals:
    """OKE-SEC-F1: every raise form of run_eval_func's usage refusal is terminal."""

    @pytest.mark.parametrize(
        "error, code",
        [
            # base evals.py:323 raises ValueError("API call not allowed : ", status)
            (ValueError("API call not allowed : ", "rate_limited"), "RATE_LIMITED"),
            (
                ValueError("API call not allowed : ", "insufficient_credits"),
                "INSUFFICIENT_CREDITS",
            ),
            (ValueError("API call not allowed : ", "resource_limit"), "RESOURCE_LIMIT"),
            (ValueError("API call not allowed : ", "error"), "API_CALL_NOT_ALLOWED"),
            (
                ValueError("API call not allowed : Error validating the api call."),
                "API_CALL_NOT_ALLOWED",
            ),
            (
                ValueError(
                    "allowed=False reason='Free tier limit reached' "
                    "error_code='FREE_TIER_LIMIT'"
                ),
                "FREE_TIER_LIMIT",
            ),
        ],
    )
    def test_usage_refusals_are_terminal_with_a_typed_code(self, error, code):
        assert cl.is_terminal_refusal(error)
        assert cl.is_retryable_refusal(error) is False
        assert cl.refusal_code(error) == code

    def test_the_rate_limited_refusal_surfaces_a_typed_message(self):
        from tfc.utils.error_codes import get_error_message

        error = ValueError("API call not allowed : ", "rate_limited")

        message = cl.refusal_message(error)
        assert message == get_error_message("RATE_LIMIT_REACHED")
        assert "(" not in message  # never the tuple repr of the two arguments
        args = cl.refusal_activity_error(error)
        assert args == {
            "message": message,
            "type": "RATE_LIMITED",
            "non_retryable": True,
        }

    def test_wrapped_refusal_is_still_terminal(self):
        try:
            try:
                raise ValueError("API call not allowed : ", "rate_limited")
            except ValueError as inner:
                raise RuntimeError("LLM generation failed") from inner
        except RuntimeError as outer:
            assert cl.is_terminal_refusal(outer)
            assert cl.refusal_code(outer) == "RATE_LIMITED"

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("mapping 'output' missing"),
            ValueError("Upstream call failed", "rate_limited"),
            RuntimeError("provider timeout"),
            KeyError("x"),
            # Gateway-looking text outside a managed meter is an ordinary
            # provider error (BYOK paths are not reclassified).
            Exception("Error code: 429 - {'error': {'code': 'budget_exceeded'}}"),
        ],
    )
    def test_ordinary_errors_are_not_terminal(self, error):
        assert not cl.is_terminal_refusal(error)

    @pytest.mark.requires_ee
    def test_usage_limit_exceeded_is_terminal(self):
        exceptions = pytest.importorskip("ee.usage.exceptions")
        events = pytest.importorskip("ee.usage.schemas.events")
        error = exceptions.UsageLimitExceeded(
            events.CheckResult(
                allowed=False,
                reason="Free tier limit reached",
                error_code="FREE_TIER_LIMIT",
            )
        )

        assert cl.is_terminal_refusal(error)
        assert cl.refusal_code(error) == "FREE_TIER_LIMIT"


class TestRealRefusalContract:
    @pytest.mark.parametrize(
        "code, metadata, cls, retryable, backoff, status",
        [
            ("managed_credits_exhausted", {}, "CreditsExhaustedError", False, False, 402),
            (
                "managed_credits_insufficient",
                {"limit_source": "agentcc_credits", "remedy_hint": "add_credits"},
                "InsufficientCreditError",
                False,
                False,
                402,
            ),
            (
                "managed_credits_insufficient",
                {"limit_source": "agentcc_in_flight", "remedy_hint": "retry"},
                "CreditsInFlightError",
                True,
                True,
                402,
            ),
            (
                "managed_credits_insufficient",
                {"limit_source": "agentcc_in_flight", "remedy_hint": "add_credits"},
                "InsufficientCreditError",
                False,
                False,
                402,
            ),
            ("agent_budget_exhausted", {}, "AgentBudgetExhaustedError", False, False, 402),
            ("budget_exceeded", {}, "BudgetExceededError", False, False, 429),
            ("org_budget_exceeded", {}, "OrgBudgetExceededError", False, False, 429),
            ("managed_spend_velocity", {}, "SpendVelocityError", True, True, 429),
            ("rate_limit_exceeded", {}, "ManagedRateLimitError", True, True, 429),
            (
                "managed_billing_unavailable",
                {},
                "CreditLedgerUnavailableError",
                True,
                False,
                503,
            ),
            ("permission_denied", {}, "ManagedPermissionDeniedError", False, False, 403),
        ],
    )
    def test_codes_map_to_typed_refusals(
        self, code, metadata, cls, retryable, backoff, status
    ):
        refusal = cl.refusal_for_code(code, metadata=metadata)

        assert type(refusal).__name__ == cls
        assert refusal.code == code
        assert refusal.retryable is retryable
        assert refusal.backoff is backoff
        assert refusal.http_status == status
        assert cl.is_terminal_refusal(refusal)
        assert cl.is_retryable_refusal(refusal) is retryable
        # Terminal refusals are ValueError (experiment rows are not retried);
        # retryable ones are not (Temporal retries the row).
        assert isinstance(refusal, ValueError) is (not retryable)

    def test_unknown_codes_are_not_managed_refusals(self):
        assert cl.refusal_for_code("insufficient_quota") is None
        assert cl.refusal_for_code("") is None

    def test_gateway_errors_are_keyed_on_error_code_not_type(self):
        budget = cl.managed_refusal_from(_GatewayError(429, "budget_exceeded"))
        limited = cl.managed_refusal_from(_GatewayError(429, "rate_limit_exceeded"))
        text_only = cl.managed_refusal_from(
            Exception(
                "Error code: 402 - {'error': {'type': 'payment_required', "
                "'code': 'managed_credits_exhausted'}}"
            )
        )
        type_only = cl.managed_refusal_from(
            Exception("Error code: 429 - {'error': {'type': 'rate_limit_error'}}")
        )

        assert isinstance(budget, cl.BudgetExceededError)
        assert isinstance(limited, cl.ManagedRateLimitError)
        assert isinstance(text_only, cl.CreditsExhaustedError)
        assert type_only is None

    def test_in_flight_hold_refusal_is_retried_after_retry_after(
        self, _meter_safety_and_sleeps
    ):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [_in_flight(retry_after=2)]

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            for attempt in cl.managed_attempts(meter):
                with attempt:
                    with meter.metered(messages=MESSAGES) as call:
                        _claim()
                        call.observe(cost=Decimal("0.01"))

        assert sleeps == [2]
        assert _ops(ledger) == ["hold", "hold", "settle"]
        assert call.status == "settled"

    @pytest.mark.parametrize(
        "refusal",
        [
            cl.refusal_for_code(
                "managed_credits_insufficient",
                metadata={"limit_source": "agentcc_credits", "remedy_hint": "add_credits"},
            ),
            cl.refusal_for_code(
                "managed_credits_insufficient",
                metadata={"limit_source": "agentcc_in_flight", "remedy_hint": "add_credits"},
            ),
            cl.refusal_for_code("managed_credits_exhausted"),
        ],
    )
    def test_non_retry_credit_refusals_are_not_retried(
        self, refusal, _meter_safety_and_sleeps
    ):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [refusal]

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.InsufficientCreditError):
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES):
                            pytest.fail("dispatched without credit")

        assert sleeps == []
        assert _ops(ledger) == ["hold"]

    @pytest.mark.parametrize(
        "code, retry_after",
        [("managed_spend_velocity", 7), ("rate_limit_exceeded", 3)],
    )
    def test_velocity_and_rate_limits_back_off_per_retry_after(
        self, code, retry_after, _meter_safety_and_sleeps
    ):
        """Refused before the claim (the hold itself): nothing was sent or
        charged, so the contract's backoff applies."""
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [cl.refusal_for_code(code, retry_after=retry_after)]

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            for attempt in cl.managed_attempts(meter):
                with attempt:
                    with meter.metered(messages=MESSAGES) as call:
                        _claim()
                        call.observe(cost=Decimal("0.01"))

        assert sleeps == [retry_after]
        assert _ops(ledger) == ["hold", "hold", "settle"]
        assert call.status == "settled"

    @pytest.mark.parametrize(
        "status, code, metadata",
        [
            (429, "managed_spend_velocity", {}),
            (429, "rate_limit_exceeded", {}),
            (
                402,
                "managed_credits_insufficient",
                {"limit_source": "agentcc_in_flight", "remedy_hint": "retry"},
            ),
        ],
    )
    def test_claimed_backoff_refusal_is_released_uncharged_and_terminal(
        self, status, code, metadata, _meter_safety_and_sleeps
    ):
        """A gateway backoff refusal
        after the claim releases the hold (charge 0) exactly once and is never
        retried."""
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        runs = []

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedRefusedAfterDispatchError) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES, amount_estimate="0.1") as call:
                            _claim()
                            runs.append(1)
                            _raise_at_gateway_boundary(_GatewayError(status, code, retry_after=2, **metadata))

        assert runs == [1]
        assert sleeps == []
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.holds[call.hold_id].state == "released"
        assert ledger.balance_of(org) == Decimal("1")
        assert call.status == "released_refused"
        error = caught.value
        assert isinstance(error, ValueError)
        assert error.code == code
        assert error.http_status == status
        assert error.charged is False
        assert error.dispatched is False
        assert error.retryable is False
        assert error.backoff is False
        assert error.retry_after is None
        assert "remedy_hint" not in error.metadata
        assert error.message == cl.refusal_for_code(code, metadata=metadata).message
        assert "charged" not in error.message
        assert error.as_response_body()["retryable"] is False
        assert error.as_response_body()["retry_after"] is None

    @pytest.mark.parametrize(
        "status, code",
        [
            (401, "invalid_api_key"),
            (403, "future_agi_credits_off"),
            (403, "permission_denied"),
            (402, "managed_credits_exhausted"),
        ],
    )
    def test_claimed_refusal_status_or_code_releases_once(
        self, status, code, _meter_safety_and_sleeps
    ):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                with meter.metered(messages=MESSAGES, amount_estimate="0.1") as call:
                    _claim()
                    _raise_at_gateway_boundary(_GatewayError(status, code))

        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1")
        assert call.status == "released_refused"
        assert isinstance(caught.value, ValueError)
        assert caught.value.retryable is False
        assert caught.value.retry_after is None

    def test_claimed_refusal_after_output_is_charged_once_and_terminal(
        self, _meter_safety_and_sleeps
    ):
        """Output was observed: the provider produced something, so the call
        is settled once (never released) and still never retried."""
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedRefusedAfterDispatchError) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES, amount_estimate="0.1") as call:
                            _claim()
                            call.observe(cost=Decimal("0.02"))
                            _raise_at_gateway_boundary(_GatewayError(429, "managed_spend_velocity", retry_after=2))

        assert sleeps == []
        assert _ops(ledger) == ["hold", "settle"]
        assert call.status == "settled"
        error = caught.value
        assert error.charged is True
        assert error.dispatched is True
        assert error.retryable is False
        assert "charged" in error.message

    def test_text_that_quotes_a_code_never_releases(self, _meter_safety_and_sleeps):
        """Only structured gateway fields release a hold; an error message
        that merely contains a refusal code settles conservatively, once."""
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
                with meter.metered(messages=MESSAGES, amount_estimate="0.1") as call:
                    _claim()
                    raise ValueError("model said {'code': 'managed_spend_velocity'}")

        assert _ops(ledger) == ["hold"]
        assert call.status == "pending_reconcile"
        assert caught.value.charged is None

    @pytest.mark.parametrize(
        "status, code, dispatched, ops",
        [
            (429, "managed_spend_velocity", True, ["hold"]),
            (503, "managed_billing_unavailable", False, ["hold", "release"]),
        ],
    )
    def test_gateway_dispatch_attestation_wins_over_the_code_list(
        self, status, code, dispatched, ops, _meter_safety_and_sleeps
    ):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                with meter.metered(messages=MESSAGES, amount_estimate="0.1"):
                    _claim()
                    _raise_at_gateway_boundary(_GatewayError(status, code, headers={"x-agentcc-dispatched": str(dispatched).lower()}))

        assert _ops(ledger) == ops
        error = caught.value
        assert error.retryable is False
        assert error.charged is (None if dispatched else False)
        if not dispatched:
            assert "charged" not in error.message

    def test_async_claimed_backoff_refusal_is_released_and_terminal(
        self, _meter_safety_and_sleeps
    ):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        meter = _meter(org, ws, route=cl.PayerRoute.managed())
        runs = []

        async def dispatch():
            _claim()
            runs.append(1)
            _raise_at_gateway_boundary(_GatewayError(429, "managed_spend_velocity", retry_after=5))

        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedRefusedAfterDispatchError) as caught:
                asyncio.run(cl.arun_metered(meter, dispatch, messages=MESSAGES))

        assert runs == [1]
        assert sleeps == []
        assert _ops(ledger) == ["hold", "release"]
        assert caught.value.charged is False

    def test_backoff_is_capped_and_bounded(self, _meter_safety_and_sleeps):
        """Hold-time backoff refusals: capped Retry-After, bounded attempts,
        and nothing sent or charged."""
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [
            cl.refusal_for_code("managed_spend_velocity", retry_after=600)
            for _ in range(cl.MANAGED_CALL_ATTEMPTS)
        ]

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.SpendVelocityError) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES):
                            pytest.fail("dispatched while the hold was refused")

        assert caught.value.retryable is True
        assert sleeps == [cl.MAX_BACKOFF_SECONDS] * (cl.MANAGED_CALL_ATTEMPTS - 1)
        assert _ops(ledger) == ["hold"] * cl.MANAGED_CALL_ATTEMPTS

    def test_billing_unavailable_fails_closed_without_in_process_retry(
        self, _meter_safety_and_sleeps
    ):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [cl.refusal_for_code("managed_billing_unavailable")]

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditLedgerUnavailableError) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES):
                            pytest.fail("dispatched while billing is unavailable")

        assert caught.value.retryable is True
        assert sleeps == []
        assert _ops(ledger) == ["hold"]

    @pytest.mark.parametrize(
        "code, cls",
        [
            ("budget_exceeded", "BudgetExceededError"),
            ("org_budget_exceeded", "OrgBudgetExceededError"),
            ("agent_budget_exhausted", "AgentBudgetExhaustedError"),
            ("managed_credits_exhausted", "CreditsExhaustedError"),
        ],
    )
    def test_terminal_dispatch_refusal_releases_the_hold_and_is_typed(
        self, code, cls, _meter_safety_and_sleeps
    ):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        gateway = _GatewayError(429 if "budget_exceeded" in code else 402, code)

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES) as call:
                            _claim()
                            cl.note_gateway_failure(call, gateway)
                            raise gateway

        error = caught.value
        assert type(error).__name__ == cls
        assert error.__cause__ is gateway
        assert "diagnostic" not in str(error)
        assert cl.is_retryable_refusal(error) is False
        assert sleeps == []
        # A refused call is never charged.
        assert ledger.holds[call.hold_id].state == "released"
        assert ledger.balance_of(org) == Decimal("1")
        assert _ops(ledger) == ["hold", "release"]

    def test_org_level_terminal_refusal_stops_managed_dispatch_for_the_org(self):
        org, other = _org(), _org()
        ws, other_ws = _ws(org), _ws(other)
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.managed())
            with pytest.raises(cl.CreditsExhaustedError):
                with meter.metered(messages=MESSAGES):
                    _claim()
                    _raise_at_gateway_boundary(_GatewayError(402, "managed_credits_exhausted"))
            before = len(ledger.calls)
            with pytest.raises(cl.CreditsExhaustedError):
                with meter.metered(messages=MESSAGES):
                    pytest.fail("dispatched after the org ran out of credits")
            assert len(ledger.calls) == before

            other_meter = _meter(other, other_ws, route=cl.PayerRoute.managed())
            with other_meter.metered(messages=MESSAGES) as call:
                _claim().observe(cost="0.01")
            assert call.status == "settled"

            cl.reset_meter_safety()
            with meter.metered(messages=MESSAGES) as again:
                _claim().observe(cost="0.01")
            assert again.status == "settled"

    def test_byok_attempts_never_retry_or_reclassify(self, _meter_safety_and_sleeps):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger()
        error = _GatewayError(429, "rate_limit_exceeded", retry_after=1)
        runs = []

        with cl.use_credit_ledger(ledger):
            meter = _meter(org, ws, route=cl.PayerRoute.byok())
            with pytest.raises(_GatewayError) as caught:
                for attempt in cl.managed_attempts(meter):
                    with attempt:
                        with meter.metered(messages=MESSAGES):
                            runs.append(1)
                            raise error

        assert caught.value is error
        assert runs == [1]
        assert sleeps == []
        assert ledger.calls == []

    def test_async_attempts_back_off(self, _meter_safety_and_sleeps):
        sleeps = _meter_safety_and_sleeps
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(balances={org.id: Decimal("1")})
        ledger.refuse_next_holds = [_in_flight(retry_after=2)]
        meter = _meter(org, ws, route=cl.PayerRoute.managed())

        async def _run():
            async for attempt in cl.managed_attempts(meter):
                async with attempt:
                    async with meter.ametered(messages=MESSAGES) as call:
                        _claim().observe(cost="0.01")
            return call

        with cl.use_credit_ledger(ledger):
            call = asyncio.run(_run())

        assert sleeps == [2]
        assert call.status == "settled"


class TestRefusalBodies:
    def test_body_and_cell_info_are_typed_and_carry_no_provider_text(self):
        refusal = cl.managed_refusal_from(
            _GatewayError(
                402,
                "managed_credits_insufficient",
                limit_source="agentcc_credits",
                remedy_hint="add_credits",
                required_usd=0.04,
                available_usd=0.01,
                scope="org",
            )
        )

        body = refusal.as_response_body()
        assert body["code"] == "managed_credits_insufficient"
        assert body["type"] == "payment_required"
        assert body["required"] == "0.04"
        assert body["available"] == "0.01"
        assert body["metadata"]["limit_source"] == "agentcc_credits"
        assert body["retryable"] is False
        assert "diagnostic" not in body["message"]
        info = cl.refusal_cell_info(refusal)
        assert info == {"reason": refusal.message, "error": body}

    def test_in_flight_body_carries_retry_after(self):
        body = _in_flight(retry_after=2).as_response_body()
        assert body["retryable"] is True
        assert body["retry_after"] == 2
        assert body["metadata"]["remedy_hint"] == "retry"


class TestBillingScope:
    @staticmethod
    def _scope(org, ws, run_id="run-1"):
        return cl.billing_scope(
            organization=org,
            workspace=ws,
            source="dataset_optimization",
            source_id=run_id,
        )

    def test_no_scope_leaves_the_legacy_path_untouched(self):
        ledger = cl.MockCreditLedger()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_managed):
            assert (
                cl.guard_direct_dispatch(
                    None, purpose=cl.PURPOSE_OPTIMIZER_TRIAL, model_name="gpt-4o"
                )
                is None
            )

        assert ledger.calls == []

    def test_managed_route_is_refused_at_a_direct_site_and_is_sticky(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger(default_balance=Decimal("10"))
        resolved = []

        def _resolver(**kwargs):
            resolved.append(kwargs["operation"])
            return _managed()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_resolver):
            with self._scope(org, ws) as scope:
                assert cl.current_billing_scope() is scope
                with pytest.raises(cl.ManagedDispatchUnavailableError) as caught:
                    cl.guard_direct_dispatch(
                        scope, purpose=cl.PURPOSE_OPTIMIZER_TRIAL, model_name="gpt-4o"
                    )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    cl.guard_direct_dispatch(
                        scope,
                        purpose=cl.PURPOSE_OPTIMIZER_PROPOSAL,
                        model_name="gpt-4o",
                    )
                with pytest.raises(cl.ManagedDispatchUnavailableError):
                    scope.raise_if_refused()

        assert cl.is_terminal_refusal(caught.value)
        assert resolved == ["run_prompt"]  # the sticky refusal resolves no more routes
        assert ledger.calls == []
        assert cl.current_billing_scope() is None

    def test_byok_route_passes_and_never_touches_the_ledger(self):
        org = _org()
        ws = _ws(org)
        ledger = cl.MockCreditLedger()

        with cl.use_credit_ledger(ledger), cl.use_route_resolver(_byok):
            with self._scope(org, ws) as scope:
                for purpose in (
                    cl.PURPOSE_OPTIMIZER_TRIAL,
                    cl.PURPOSE_OPTIMIZER_PROPOSAL,
                    cl.PURPOSE_OPTIMIZER_TEACHER,
                ):
                    assert (
                        cl.guard_direct_dispatch(
                            scope, purpose=purpose, model_name="gpt-4o"
                        )
                        is None
                    )
                assert scope.refusal is None

        assert ledger.calls == []

    def test_an_unresolvable_route_keeps_the_legacy_dispatch(self):
        org = _org()
        ws = _ws(org)

        def _not_configured(**kwargs):
            from model_hub.services.model_route import ModelRouteUnavailable
            raise ModelRouteUnavailable("openai")

        with cl.use_route_resolver(_not_configured):
            with self._scope(org, ws) as scope:
                assert (
                    cl.guard_direct_dispatch(
                        scope, purpose=cl.PURPOSE_OPTIMIZER_TRIAL, model_name="gpt-4o"
                    )
                    is None
                )
                assert scope.refusal is None

    def test_other_route_errors_fail_closed(self):
        org = _org()
        ws = _ws(org)

        def _broken(**kwargs):
            raise RuntimeError("routing port unreachable")

        with cl.use_route_resolver(_broken):
            with self._scope(org, ws) as scope:
                with pytest.raises(cl.ManagedRouteUnavailableError):
                    cl.guard_direct_dispatch(
                        scope, purpose=cl.PURPOSE_OPTIMIZER_TRIAL, model_name="gpt-4o"
                    )
                assert isinstance(scope.refusal, cl.ManagedRouteUnavailableError)

    def test_a_usage_refusal_recorded_on_the_scope_stops_later_calls(self):
        org = _org()
        ws = _ws(org)
        refusal = ValueError("API call not allowed : ", "rate_limited")
        resolved = []

        with cl.use_route_resolver(lambda **kw: resolved.append(kw) or _byok()):
            with self._scope(org, ws, run_id="run-2") as scope:
                cl.record_refusal(scope, refusal)
                with pytest.raises(ValueError) as caught:
                    cl.guard_direct_dispatch(
                        scope, purpose=cl.PURPOSE_OPTIMIZER_TRIAL, model_name="gpt-4o"
                    )

        assert caught.value is refusal
        assert resolved == []

    def test_ordinary_errors_are_not_recorded(self):
        org = _org()
        ws = _ws(org)

        with cl.use_route_resolver(_byok):
            with self._scope(org, ws) as scope:
                cl.record_refusal(scope, ValueError("mapping 'output' missing"))
                assert scope.refusal is None

    @pytest.mark.parametrize(
        "error, expected",
        [
            (
                cl.refusal_for_code("managed_credits_exhausted"),
                {"type": "managed_credits_exhausted", "non_retryable": True},
            ),
            (
                cl.refusal_for_code("managed_billing_unavailable"),
                {"type": "managed_billing_unavailable", "non_retryable": False},
            ),
            (
                cl.ManagedDispatchUnavailableError(),
                {"type": "MANAGED_DISPATCH_UNAVAILABLE", "non_retryable": True},
            ),
        ],
    )
    def test_activity_error_args(self, error, expected):
        args = cl.refusal_activity_error(error)
        assert args["type"] == expected["type"]
        assert args["non_retryable"] is expected["non_retryable"]
        assert args["message"] == error.message


class TestRouteSeam:
    def _import_without(self, monkeypatch, replacement):
        real = cl.importlib.import_module

        def _import(name, *args, **kwargs):
            if name == cl.MODEL_ROUTE_MODULE:
                return replacement(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(cl.importlib, "import_module", _import)

    def test_adapter_absent_means_stored_key_dispatch_only(self, monkeypatch):
        def _missing(name):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        self._import_without(monkeypatch, _missing)

        route = cl.resolve_payer_route(
            organization=_org(),
            workspace=None,
            model_name="gpt-4o",
            operation=cl.route_operation(cl.PURPOSE_DATASET_RUN_PROMPT),
        )

        assert route.mode == cl.BYOK
        assert route.is_managed is False

    def test_adapter_import_errors_are_not_swallowed(self, monkeypatch):
        def _broken(name):
            raise ModuleNotFoundError("No module named 'gateway_client'", name="gateway_client")

        self._import_without(monkeypatch, _broken)

        with pytest.raises(ModuleNotFoundError):
            cl.resolve_payer_route(
                organization=_org(),
                workspace=None,
                model_name="gpt-4o",
                operation="run_prompt",
            )

    def test_adapter_route_is_normalised_and_opaque_values_stay_hidden(
        self, monkeypatch
    ):
        opaque = object()
        adapter = SimpleNamespace(
            resolve_model_route=lambda **kwargs: SimpleNamespace(
                mode="managed",
                provider="openai",
                credential=None,
                gateway_route=opaque,
                payer="future_agi_credits",
            )
        )
        self._import_without(monkeypatch, lambda name: adapter)

        route = cl.resolve_payer_route(
            organization=_org(),
            workspace=None,
            model_name="gpt-4o",
            operation="run_prompt",
        )

        assert route.is_managed
        assert route.payer == cl.PAYER_CREDITS
        assert route.route.gateway_route is opaque
        assert "gateway_route" not in repr(route)

    def test_unknown_route_mode_fails_closed(self, monkeypatch):
        adapter = SimpleNamespace(
            resolve_model_route=lambda **kwargs: SimpleNamespace(mode="unavailable")
        )
        self._import_without(monkeypatch, lambda name: adapter)

        with pytest.raises(cl.ManagedRouteUnavailableError):
            cl.resolve_payer_route(
                organization=_org(),
                workspace=None,
                model_name="gpt-4o",
                operation="run_prompt",
            )

    def test_purposes_resolve_with_the_adapter_operations(self):
        org = _org()
        ws = _ws(org)
        seen = []

        def _resolver(**kwargs):
            seen.append(kwargs["operation"])
            return _byok()

        with cl.use_route_resolver(_resolver):
            for purpose in (
                cl.PURPOSE_DATASET_RUN_PROMPT,
                cl.PURPOSE_RUN_PROMPT_PREVIEW,
                cl.PURPOSE_PROMPT_PLAYGROUND,
                cl.PURPOSE_EXPERIMENT_PROMPT,
            ):
                cl.call_meter(
                    organization=org,
                    workspace=ws,
                    model_name="gpt-4o",
                    purpose=purpose,
                    source="s",
                    source_id="1",
                )

        assert seen == ["run_prompt", "run_prompt", "run_prompt", "experiment_prompt"]
        assert set(cl.ROUTE_OPERATIONS.values()) <= {"run_prompt", "experiment_prompt"}


class TestLegacyOptimizerRefusal:
    def test_managed_route_is_refused_and_byok_is_untouched(self):
        org = _org()
        ws = _ws(org)

        with cl.use_route_resolver(_managed):
            with pytest.raises(cl.LegacyOptimizerRetiredError) as caught:
                cl.refuse_legacy_managed_optimizer(
                    organization=org,
                    workspace=ws,
                    model_name="gpt-4o",
                    surface="develop-optimiser",
                )
        assert caught.value.code == "LEGACY_OPTIMIZER_RETIRED"

        with cl.use_route_resolver(_byok):
            assert (
                cl.refuse_legacy_managed_optimizer(
                    organization=org,
                    workspace=ws,
                    model_name="gpt-4o",
                    surface="develop-optimiser",
                )
                is None
            )

    def test_an_unresolvable_route_keeps_the_legacy_run(self):
        org = _org()
        ws = _ws(org)

        def _not_configured(**kwargs):
            from model_hub.services.model_route import ModelRouteUnavailable
            raise ModelRouteUnavailable("openai")

        with cl.use_route_resolver(_not_configured):
            assert (
                cl.refuse_legacy_managed_optimizer(
                    organization=org,
                    workspace=ws,
                    model_name="gpt-4o",
                    surface="develop-optimiser",
                )
                is None
            )

    def test_broken_legacy_constructor_paths_raise_a_typed_refusal(self):
        with pytest.raises(cl.LegacyOptimizerRetiredError) as caught:
            cl.refuse_legacy_optimizer("optimize_dataset_rag")

        assert cl.is_terminal_refusal(caught.value)
        assert "Dataset Optimization" in str(caught.value)


class TestUnmeteredSurface:
    def test_managed_dispatch_inside_an_unmetered_surface_is_refused_with_a_disclosure(
        self,
    ):
        with cl.unmetered_surface("agent_graph_node"):
            with pytest.raises(cl.ManagedDispatchUnavailableError) as caught:
                _claim()
        assert caught.value.code == "MANAGED_DISPATCH_UNAVAILABLE"
        assert "own provider key" in caught.value.message

        with pytest.raises(cl.CreditHoldMissingError):
            _claim()


class TestComposeMeterSafety:
    def test_settlement_failure_is_tenant_local(self):
        org_a, org_b = _org(), _org()
        class StateFailure(cl.MockCreditLedger):
            def settle(self, hold_id, actual_amount, **metadata):
                raise cl.CreditHoldStateError("hold expired")
        ledger = StateFailure(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditSettlementError):
                with _meter(org_a, None, route=_managed()).metered(amount_estimate="0.1"):
                    _claim().observe(cost="0.01")
            with pytest.raises(cl.CreditHoldMissingError):
                with _meter(org_b, None, route=_managed()).metered(amount_estimate="0.1"):
                    pass

    def test_unclaimed_copy_does_not_assert_no_upstream_charge(self):
        org = _org()
        with cl.use_credit_ledger(cl.MockCreditLedger(default_balance=Decimal("1"))):
            with pytest.raises(cl.CreditHoldMissingError) as caught:
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1"):
                    pass
        assert "nothing was charged" not in str(caught.value)
        assert "did not run" not in str(caught.value)

    @pytest.mark.parametrize("failure", [RuntimeError("disconnected"), asyncio.CancelledError()])
    def test_claimed_failure_keeps_hold_pending_once(self, failure):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            expected = cl.ManagedOutcomePendingReconcileError if isinstance(failure, Exception) else type(failure)
            with pytest.raises(expected):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1") as call:
                    _claim()
                    raise failure
        assert _ops(ledger) == ["hold"]
        assert "release" not in _ops(ledger)
        assert call.actual_amount is None
        assert call.status == "pending_reconcile"

    def test_zero_reported_cost_is_not_free_managed_usage(self):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1") as call:
                    _claim()
                    call.observe(value_info={"metadata": {"cost": {"total_cost": 0}, "usage": {"completion_tokens": 4}}})
        assert call.actual_amount is None and call.cost_source is None
        assert call.status == "pending_reconcile"
        assert _ops(ledger) == ["hold"]
        assert ledger.balance_of(org) == Decimal("1")

    def test_one_meter_no_post_settlement_billable_event(self):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        meter = _meter(org, None, route=_managed())
        with cl.use_credit_ledger(ledger):
            with meter.metered(amount_estimate="0.1") as call:
                _claim()
                call.observe(cost="0.04")
            assert meter.emit_usage_event(call, event_type="dataset_run_prompt") is None
            assert meter.emit_usage_event(call, event_type="dataset_run_prompt") is None
        assert _ops(ledger).count("hold") == 1
        assert _ops(ledger).count("settle") == 1
        assert ledger.balance_of(org) == Decimal("0.96")

    def test_hold_cannot_be_claimed_for_two_provider_calls(self):
        org = _org()
        with cl.use_credit_ledger(cl.MockCreditLedger(default_balance=Decimal("1"))):
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.1"):
                _claim().observe(cost="0.01")
                with pytest.raises(cl.CreditHoldMissingError):
                    _claim().observe(cost="0.01")


class TestComposeBillingCut:
    def test_legacy_billing_is_not_silently_bypassed(self):
        org = _org()
        meter = _meter(org, None, route=_managed())
        with pytest.raises(cl.ManagedDispatchUnavailableError, match="legacy billing is active"):
            cl.require_single_billing_path(meter, object())
        cl.require_single_billing_path(_meter(org, None, route=_byok()), object())

    def test_claimed_gateway_error_does_not_prove_no_provider_work(self):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1") as call:
                    _claim()
                    raise RuntimeError("gateway returned an error")
        assert _ops(ledger) == ["hold"]
        assert ledger.balance_of(org) == Decimal("1")


class TestComposeUnknownRoute:
    def test_unknown_operation_fails_closed(self):
        org = _org()
        def unknown(**kwargs):
            raise ValueError("Unknown model route operation")
        with cl.use_route_resolver(unknown):
            with pytest.raises(cl.ManagedRouteUnavailableError):
                cl.refuse_legacy_managed_optimizer(organization=org, workspace=None, model_name="gpt-4o", surface="test")


class TestTrustedBillingEvidence:
    @pytest.mark.parametrize("attested", [True, False, None, 0, 1, "true", "false", {}, []])
    @pytest.mark.parametrize("nested", [False, True])
    @pytest.mark.parametrize("code", ["managed_spend_velocity", "managed_billing_unavailable"])
    def test_sdk_body_never_attests_dispatch(self, attested, nested, code):
        org = _org()
        call = cl.ManagedCall(_envelope(org, None), _managed())
        error = _GatewayError(429 if code == "managed_spend_velocity" else 503, code, dispatched=attested)
        if nested:
            error.body = {"error": error.body}
        cl.note_gateway_failure(call, error)
        assert call.gateway_dispatched is None
        assert call.refused_before_upstream is (code == "managed_spend_velocity")

    @pytest.mark.parametrize("status, code", [(401, "invalid_api_key"), (403, "permission_denied")])
    def test_status_can_come_from_sdk_response(self, status, code):
        org = _org()
        call = cl.ManagedCall(_envelope(org, None), _managed())
        error = _GatewayError(status, code)
        del error.status_code
        cl.note_gateway_failure(call, error)
        assert call.gateway_dispatched is None
        assert call.refused_before_upstream is True

    def test_message_code_attributes_details_and_chain_are_not_evidence(self):
        org = _org()
        call = cl.ManagedCall(_envelope(org, None), _managed())
        error = ValueError("{'code': 'managed_spend_velocity'}")
        error.code = "managed_spend_velocity"
        error.details = {"dispatched": False}
        error.__cause__ = _GatewayError(401, "unknown", dispatched=False)
        cl.note_gateway_failure(call, error)
        assert call.gateway_dispatched is None
        assert call.refused_before_upstream is False

    def test_evidence_reader_never_renders_the_sdk_exception(self):
        class Unprintable(_GatewayError):
            def __str__(self):
                raise AssertionError("billing must not render SDK text")
        org = _org()
        call = cl.ManagedCall(_envelope(org, None), _managed())
        cl.note_gateway_failure(call, Unprintable(429, "managed_spend_velocity"))
        assert call.refused_before_upstream is True

    @pytest.mark.parametrize("promote", [False, True])
    def test_no_boundary_evidence_means_pending_even_for_a_typed_refusal(self, promote):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        raw = _GatewayError(429, "managed_spend_velocity", dispatched=False)
        error = cl.managed_refusal_from(
            ValueError("model said {'code': 'managed_spend_velocity'}")
        ) if promote else raw
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1") as call:
                    _claim()
                    raise error  # Deliberately NOT note_gateway_failure.
        assert call.gateway_dispatched is None
        assert call.refused_before_upstream is False
        assert _ops(ledger) == ["hold"]
        assert ledger.balance_of(org) == Decimal("1")
        assert caught.value.charged is None
        assert caught.value.retryable is False

    def test_outer_wrapper_cannot_override_recorded_dispatch(self):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        raw = _GatewayError(503, "managed_billing_unavailable", headers={"x-agentcc-dispatched": "true"})
        outer = cl.SpendVelocityError(metadata={"remedy_hint": "retry"})
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedCreditRefusal) as caught:
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.1") as call:
                    _claim()
                    cl.note_gateway_failure(call, raw)
                    raise outer from raw
        assert _ops(ledger) == ["hold"]
        assert call.gateway_dispatched is True
        assert caught.value.charged is None

    @pytest.mark.parametrize("code", sorted(cl.MANAGED_REFUSAL_CODES))
    @pytest.mark.parametrize("charged", [False, True])
    def test_every_refusal_terminal_form_preserves_identity_and_non_retry_remedy(self, code, charged):
        from tfc.utils.error_codes import get_specific_error_message
        original = cl.refusal_for_code(code, metadata={"remedy_hint": "add_credits"}, retry_after=9)
        error = original.after_dispatch(charged=charged)
        assert isinstance(error, cl.ManagedCreditRefusal)
        assert isinstance(error, ValueError)
        assert error.code == original.code
        assert error.error_type == original.error_type
        assert error.http_status == original.http_status
        assert error.metadata["remedy_hint"] == "add_credits"
        assert error.retryable is False
        assert error.backoff is False
        assert error.retry_after is None
        assert error.dispatched is charged
        assert error.charged is charged
        if charged:
            assert "may have run" in error.message
            assert "credits were charged" in error.message
            assert get_specific_error_message(error, True) == error.message
            if code == "managed_billing_unavailable":
                assert isinstance(error, cl.CreditLedgerUnavailableAfterDispatchError)
        else:
            assert error.message == original.message

    def test_release_form_removes_only_retry_remedy(self):
        original = _in_flight()
        terminal = original.after_dispatch(charged=False)
        assert terminal.retry_after is None
        assert terminal.metadata.get("remedy_hint") != "retry"
        assert terminal.message == original.message
        assert original.metadata["remedy_hint"] == "retry"

    def test_route_details_preserve_in_flight_classification_only(self):
        from model_hub.services import model_route as mr
        raw = _GatewayError(402, "managed_credits_insufficient",
                            limit_source="agentcc_in_flight", remedy_hint="retry")
        normalized = mr.managed_call_error(raw)
        assert isinstance(cl.managed_refusal_from(normalized), cl.CreditsInFlightError)
        org = _org()
        call = cl.ManagedCall(_envelope(org, None), _managed())
        cl.note_gateway_failure(call, normalized)
        assert call.refused_before_upstream is False  # details never billing evidence


class TestHoldCap:
    @pytest.mark.parametrize("failure", [False, True])
    @pytest.mark.parametrize("settle_retries", [0, 2])
    def test_observed_excess_caps_at_hold_and_records_one_incident(self, monkeypatch, failure, settle_retries):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        ledger.fail_next_settles = settle_retries
        incidents = []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        meter = _meter(org, None, route=_managed())
        calls = []
        def run():
            with meter.metered(amount_estimate="0.10") as call:
                calls.append(call)
                _claim()
                call.observe(cost="0.20")
                if failure:
                    raise RuntimeError("private provider text")
        with cl.use_credit_ledger(ledger):
            if failure:
                with pytest.raises(cl.ManagedCallFailedAfterDispatchError):
                    run()
            else:
                run()
        call = calls[0]
        assert call.actual_amount == Decimal("0.10")
        assert call.cost_source == "provider_usage_capped"
        assert ledger.balance_of(org) == Decimal("0.90")
        assert ledger.holds[call.hold_id].debits == 1
        assert all(row[2] == Decimal("0.10") for row in ledger.calls if row[0] == "settle")
        assert call.settlement_amount() == (Decimal("0.10"), "provider_usage_capped")
        assert incidents == [{"organization_id": str(org.id), "request_id": call.request_id,
                              "model": "gpt-4o", "excess": Decimal("0.10")}]

    def test_small_observed_failure_charges_actual_usage(self):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance=Decimal("1"))
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedCallFailedAfterDispatchError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                    _claim()
                    call.observe(cost="0.02")
                    raise TimeoutError("private timeout")
        assert call.actual_amount == Decimal("0.02")
        assert call.cost_source == "provider_usage"

    def test_incident_is_value_free_decimal_text_and_never_raises(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(cl, "logger", SimpleNamespace(warning=lambda event, **kw: warnings.append((event, kw))))
        fields = dict(organization_id="org", request_id="request", model="model", excess=Decimal("0.10"))
        cl.record_hold_excess_incident(**fields)
        assert warnings == [("credit_hold_excess", {**fields, "excess": "0.10"})]
        def unavailable(*a, **kw):
            raise RuntimeError("logger unavailable")
        monkeypatch.setattr(cl, "logger", SimpleNamespace(warning=unavailable))
        cl.record_hold_excess_incident(**fields)


class TestPendingReconcile:
    def _pending(self, ledger, gateway_id="gateway-response-id"):
        org = _org()
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                    _claim(gateway_id=None)
                    raw = _GatewayError(504, "gateway_timeout")
                    raw.response.headers["x-agentcc-request-id"] = gateway_id
                    _raise_at_gateway_boundary(raw)
        return org, call

    def _args(self, call):
        return dict(organization_id=call.envelope.organization_id,
                    gateway_request_id=call.gateway_request_id, hold_id=call.hold_id,
                    amount_estimate=call.amount_estimate, opened_at=call.opened_at)

    @pytest.mark.parametrize("actual, expected, excess", [
        ("0", "0", None), ("0.02", "0.02", None), ("0.20", "0.10", Decimal("0.10")),
    ])
    def test_record_caps_at_hold_and_repeat_is_noop(self, monkeypatch, actual, expected, excess):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger)
        incidents = []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        record = dict(organization_id=str(org.id), gateway_request_id=call.gateway_request_id,
                      actual_amount=actual)
        with cl.use_credit_ledger(ledger):
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=record) == "settled"
            before = list(ledger.calls)
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=record) == "settled"
        assert ledger.calls == before
        assert ledger.holds[call.hold_id].debits == 1
        assert ledger.holds[call.hold_id].settled_amount == Decimal(expected)
        assert ledger.balance_of(org) == Decimal("1") - Decimal(expected)
        assert [i["excess"] for i in incidents] == ([] if excess is None else [excess])

    @pytest.mark.parametrize("gateway_id", ["gateway-response-id", None])
    def test_missing_record_waits_then_releases_at_exact_ttl_once(self, gateway_id):
        from datetime import timedelta
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger, gateway_id)
        with cl.use_credit_ledger(ledger):
            assert cl.reconcile_pending_hold(**self._args(call), now=call.opened_at + cl.RECONCILE_TTL - timedelta(microseconds=1)) == "pending_reconcile"
            assert _ops(ledger) == ["hold"]
            assert ledger.held_of(org) == Decimal("0.10")
            assert cl.reconcile_pending_hold(**self._args(call), now=call.opened_at + cl.RECONCILE_TTL) == "released"
            before = list(ledger.calls)
            assert cl.reconcile_pending_hold(**self._args(call), now=call.opened_at + cl.RECONCILE_TTL) == "released"
        assert ledger.calls == before
        assert ledger.held_of(org) == 0
        assert ledger.balance_of(org) == Decimal("1")

    def test_pending_snapshot_is_durable_port_metadata_not_mutable_call(self):
        from datetime import timedelta
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger)
        row = ledger.get_hold(call.hold_id)
        assert row["organization_id"] == str(org.id)
        assert row["request_id"] == call.request_id
        assert row["gateway_request_id"] == "gateway-response-id" != call.request_id
        assert row["model"] == "gpt-4o"
        assert row["opened_at"] == call.opened_at
        assert row["amount_estimate"] == Decimal("0.10")
        assert row["pending_reconcile"] is True
        assert ledger.list_pending_holds(call.opened_at - timedelta(seconds=1)) == []
        assert ledger.list_pending_holds(call.opened_at) == [row]
        row["gateway_request_id"] = "mutated"
        assert ledger.get_hold(call.hold_id)["gateway_request_id"] == "gateway-response-id"

    @pytest.mark.parametrize("field, value", [("organization_id", "wrong-org"), ("gateway_request_id", "client-id"), ("actual_amount", "NaN"), ("actual_amount", "-1")])
    def test_invalid_record_never_debits_or_releases(self, field, value):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger)
        record = dict(organization_id=str(org.id), gateway_request_id=call.gateway_request_id, actual_amount="0.02")
        record[field] = value
        with cl.use_credit_ledger(ledger), pytest.raises(cl.CreditHoldStateError):
            cl.reconcile_pending_hold(**self._args(call), settle_record=record)
        assert _ops(ledger) == ["hold"]

    def test_command_looks_up_by_response_key_and_default_releases_at_ttl(self, monkeypatch):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger)
        now = call.opened_at + cl.RECONCILE_TTL
        seen = []
        def lookup(**kw):
            seen.append(kw)
            return dict(**kw, actual_amount="0.02")
        monkeypatch.setattr(command, "lookup_settle_record", lookup)
        monkeypatch.setattr(command, "utc_now", lambda: now)
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
            command.Command().handle()
        assert seen == [{"organization_id": str(org.id), "gateway_request_id": "gateway-response-id"}]
        assert ledger.holds[call.hold_id].settled_amount == Decimal("0.02")
        assert ledger.holds[call.hold_id].debits == 1

    def test_command_without_reader_releases_missing_key_at_ttl(self, monkeypatch, settings):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = None
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._pending(ledger, None)
        monkeypatch.setattr(command, "utc_now", lambda: call.opened_at + cl.RECONCILE_TTL)
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1")

    def test_observed_output_prefers_available_settle_record_even_zero(self):
        ledger = cl.MockCreditLedger(default_balance="1")
        org = _org()
        with cl.use_credit_ledger(ledger):
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                _claim()
                call.observe(cost="0.08")
                call.gateway_request_id = "gateway-response-id"
                call.settle_record = dict(organization_id=str(org.id), gateway_request_id=call.gateway_request_id, actual_amount="0")
        assert call.actual_amount == 0
        assert ledger.balance_of(org) == 1


class TestReplayHoldGuard:
    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("state", ["held", "settled", "released", "pending_reconcile"])
    def test_consumed_identity_is_refused_in_every_hold_state(self, is_async, state):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance="1")
        meter = _meter(org, None, route=_managed())
        identity = cl.ManagedCall(meter.envelope, meter.route, dispatch_id="repeat")
        hold_id = ledger.hold(org=org, workspace=None, amount_estimate="0.1",
                              idempotency_key=identity.idempotency_key,
                              purpose=meter.envelope.purpose)
        assert ledger.claim_dispatch_once(identity.idempotency_key) is True
        if state == "settled":
            ledger.settle(hold_id, "0.02")
        elif state == "released":
            ledger.release(hold_id)
        elif state == "pending_reconcile":
            ledger.mark_pending_hold(hold_id, gateway_request_id=None)
        before = ledger.get_hold(hold_id)
        operations = list(ledger.calls)
        balance, reserved = ledger.balance_of(org), ledger.held_of(org)
        entered = []
        def run():
            with meter.metered(amount_estimate="0.1", dispatch_id="repeat"):
                entered.append(True)
                _claim().observe(cost="0.02")
        async def arun():
            async with meter.ametered(amount_estimate="0.1", dispatch_id="repeat"):
                entered.append(True)
                _claim().observe(cost="0.02")
        # The consumed identity belongs to the port, not a worker's safety cache.
        cl.reset_meter_safety()
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedCreditRefusal) as caught:
            asyncio.run(arun()) if is_async else run()
        assert caught.value.code == "MANAGED_CALL_ALREADY_COMPLETED"
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert entered == []
        assert ledger.calls == operations + [("hold", identity.idempotency_key, Decimal("0.1"))]
        assert ledger.get_hold(hold_id) == before
        assert (ledger.balance_of(org), ledger.held_of(org)) == (balance, reserved)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("mode", ["missing", "noncallable", "lookup_raises", "raises",
                                      "raises_after_commit", "false", "none", "truthy"])
    def test_unavailable_or_unconfirmed_atomic_claim_fails_closed(self, monkeypatch, is_async, mode):
        import sys

        # r8: missing port support is terminal only when OSS is unavailable too.
        monkeypatch.setitem(sys.modules, "agentcc.services.dispatch_claims", None)
        class BrokenClaimLedger(cl.MockCreditLedger):
            def __getattribute__(self, name):
                if name == "claim_dispatch_once" and mode == "missing":
                    raise AttributeError(name)
                if name == "claim_dispatch_once" and mode == "lookup_raises":
                    raise ConnectionError("private ledger details")
                return super().__getattribute__(name)
        org = _org()
        ledger = BrokenClaimLedger(default_balance="1")
        def claim(key):
            assert len(ledger.holds) == 1  # hold precedes the atomic decision
            if mode == "raises_after_commit":
                assert cl.MockCreditLedger.claim_dispatch_once(ledger, key) is True
            if mode in ("raises", "raises_after_commit"):
                raise ConnectionError("private ledger details")
            return {"false": False, "none": None, "truthy": 1}[mode]
        if mode == "noncallable":
            monkeypatch.setattr(ledger, "claim_dispatch_once", None, raising=False)
        elif mode not in ("missing", "lookup_raises"):
            monkeypatch.setattr(ledger, "claim_dispatch_once", claim, raising=False)
        meter = _meter(org, None, route=_managed())
        entered = []
        def run():
            with meter.metered(amount_estimate="0.1", dispatch_id="repeat"):
                entered.append(True)
                _claim().observe(cost="0.02")
        async def arun():
            async with meter.ametered(amount_estimate="0.1", dispatch_id="repeat"):
                entered.append(True)
                _claim().observe(cost="0.02")
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedCreditRefusal) as caught:
            asyncio.run(arun()) if is_async else run()
        assert caught.value.code == "MANAGED_CALL_ALREADY_COMPLETED"
        assert isinstance(caught.value, ValueError)
        assert caught.value.retryable is False and caught.value.backoff is False
        assert caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert cl.refusal_activity_error(caught.value)["non_retryable"] is True
        assert "private ledger details" not in str(caught.value)
        assert entered == []
        preflight = mode in ("missing", "noncallable", "lookup_raises")
        assert _ops(ledger) == ([] if preflight else ["hold"])
        assert ledger.balance_of(org) == Decimal("1")
        assert all(h.state == "held" and h.debits == 0 for h in ledger.holds.values())

    def test_atomic_claim_has_one_winner_under_concurrent_callers(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        ledger = cl.MockCreditLedger(default_balance="1")
        barrier = Barrier(8)
        def claim(_):
            barrier.wait(timeout=5)
            return ledger.claim_dispatch_once("same-key")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        assert sum(result is True for result in results) == 1
        assert sum(result is False for result in results) == 7
        assert ledger.claim_dispatch_once("same-key") is False
        assert ledger.claim_dispatch_once("different-key") is True
        assert ledger.claim_dispatch_once("different-key") is False

    @pytest.mark.parametrize("is_async", [False, True])
    def test_explicit_identity_does_not_use_a_snapshot_for_dispatch_safety(self, is_async):
        ledger = cl.MockCreditLedger(default_balance="1")
        def unexpected_query(hold_id):
            pytest.fail("atomic consumption, not a hold snapshot, must authorize dispatch")
        ledger.get_hold = unexpected_query
        meter = _meter(_org(), None, route=_managed())
        def run():
            with meter.metered(amount_estimate="0.1", dispatch_id="new-key"):
                _claim().observe(cost="0.02")
        async def arun():
            async with meter.ametered(amount_estimate="0.1", dispatch_id="new-key"):
                _claim().observe(cost="0.02")
        with cl.use_credit_ledger(ledger):
            asyncio.run(arun()) if is_async else run()
        assert _ops(ledger) == ["hold", "settle"]

    @pytest.mark.parametrize("is_async", [False, True])
    def test_implicit_identity_still_skips_atomic_consumption(self, is_async):
        ledger = cl.MockCreditLedger(default_balance="1")
        def unexpected_claim(key):
            pytest.fail("implicit UUID calls must remain unchanged")
        ledger.claim_dispatch_once = unexpected_claim
        meter = _meter(_org(), None, route=_managed())
        calls = []
        def run():
            with meter.metered(amount_estimate="0.1") as call:
                calls.append(call)
                _claim().observe(cost="0.02")
        async def arun():
            async with meter.ametered(amount_estimate="0.1") as call:
                calls.append(call)
                _claim().observe(cost="0.02")
        with cl.use_credit_ledger(ledger):
            for _ in range(2):
                asyncio.run(arun()) if is_async else run()
        assert calls[0].request_id != calls[1].request_id
        assert _ops(ledger) == ["hold", "settle", "hold", "settle"]

    @pytest.mark.parametrize("dispatch_id", ["", 0, False])
    def test_explicit_falsy_identity_is_stable(self, dispatch_id):
        envelope = _envelope(_org(), None)
        first = cl.ManagedCall(envelope, _managed(), dispatch_id=dispatch_id)
        second = cl.ManagedCall(envelope, _managed(), dispatch_id=dispatch_id)
        assert first.idempotency_key == second.idempotency_key

    def test_claim_without_output_is_genuinely_pending(self):
        ledger = cl.MockCreditLedger(default_balance="1")
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedOutcomePendingReconcileError):
            with _meter(_org(), None, route=_managed()).metered(amount_estimate="0.1") as call:
                _claim()
        assert call.status == "pending_reconcile"
        assert _ops(ledger) == ["hold"]


class TestGatewayHeaderEvidence:
    @pytest.mark.parametrize("value, expected", [("true", True), ("false", False), ("TRUE", None), ("False", None), ("1", None), (True, None), (False, None), (None, None)])
    @pytest.mark.parametrize("name", ["x-agentcc-dispatched", "X-AgentCC-Dispatched"])
    @pytest.mark.parametrize("boundary", ["response", "failure"])
    def test_only_literal_response_header_attests(self, value, expected, name, boundary):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        headers = {name: value}
        if boundary == "response":
            cl.note_gateway_response(call, SimpleNamespace(headers=headers))
        else:
            cl.note_gateway_failure(call, _GatewayError(503, "unknown", headers=headers, dispatched=True))
        assert call.gateway_dispatched is expected
        assert call.refused_before_upstream is (expected is False)

    @pytest.mark.parametrize("header, expected", [("valid-header", "valid-header"), ("invalid header!", None), ("", None)])
    def test_present_request_header_never_falls_back_to_metadata(self, header, expected):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        cl.note_gateway_failure(call, _GatewayError(504, "gateway_timeout", headers={"X-AgentCC-Request-ID": header}, request_id="body-id"))
        assert call.gateway_request_id == expected

    @pytest.mark.parametrize("status, code", [(None, "rate_limit_exceeded"), (200, "invalid_api_key"), (500, "invalid_api_key"), (403, "invalid_api_key"), (401, "permission_denied"), (401, None), (403, "unknown"), (402, "INSUFFICIENT_CREDIT")])
    def test_no_matching_contract_status_is_unknown(self, status, code):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        cl.note_gateway_failure(call, _GatewayError(status, code))
        assert call.gateway_dispatched is None
        assert call.refused_before_upstream is False


    @pytest.mark.parametrize("headers, expected", [
        ({"X-AgentCC-Request-ID": "valid-header"}, "valid-header"),
        ({"X-AgentCC-Request-ID": "invalid header!"}, None),
        ({"X-AgentCC-Request-ID": ""}, None),
        ({"X-AgentCC-Request-ID": None}, None),
        ({"X-AgentCC-Request-ID": True}, None),
        ({}, None),  # r10: absent header + body request_id stays keyless (main G05)
    ])
    def test_request_header_presence_survives_later_headerless_failure(self, headers, expected):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        cl.note_gateway_response(call, SimpleNamespace(headers=headers))
        cl.note_gateway_response(call, SimpleNamespace(headers={}))
        cl.note_gateway_failure(call, _GatewayError(504, "gateway_timeout", request_id="body-id"))
        assert call.gateway_request_id_header_seen is bool(headers)
        assert call.gateway_request_id == expected


class TestDispatchAttestationHistoryR7:
    @pytest.mark.parametrize("history", [
        ("true", "false"), ("false", "true"),
        ("true", "false", "true"), ("false", "true", "false"),
        ("true", "invalid", "false"), ("false", None, "true"),
    ])
    @pytest.mark.parametrize("output", [False, True])
    @pytest.mark.parametrize("failure", [False, True])
    def test_conflict_is_sticky_and_never_releases_or_settles(
        self, monkeypatch, history, output, failure
    ):
        org = _org()
        ledger = cl.MockCreditLedger(default_balance="1")
        warnings = []
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                _claim(gateway_id=None)
                for value in history:
                    headers = {} if value is None else {"x-agentcc-dispatched": value}
                    cl.note_gateway_response(call, SimpleNamespace(headers=headers))
                # Neither a repeated last header nor a malformed/headerless failure
                # may erase a conflict that was observed before any output.
                cl.note_gateway_response(call, SimpleNamespace(headers={"x-agentcc-dispatched": "invalid"}))
                cl.note_gateway_failure(call, _GatewayError(429, "rate_limit_exceeded"))
                assert call.gateway_dispatched is None
                assert call.gateway_dispatch_conflict is True
                assert call.refused_before_upstream is False
                if output:
                    call.observe(cost="0.02")
                if failure:
                    raise TimeoutError("private iteration diagnostic")
        call.note_dispatch_contradiction()
        assert call.status == "pending_reconcile"
        assert call.actual_amount is None
        assert _ops(ledger) == ["hold"]
        held = ledger.get_hold(call.hold_id)
        assert held["state"] == "held" and held["pending_reconcile"] is True
        assert ledger.holds[call.hold_id].debits == 0
        assert ledger.balance_of(org) == Decimal("1")
        assert ledger.held_of(org) == Decimal("0.10")
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert warnings == [("credit_dispatch_contradiction", {
            "organization_id": str(org.id), "request_id": call.request_id,
            "gateway_request_id": None,
        })]

    @pytest.mark.django_db(transaction=True)
    def test_mock_refuses_atomic_claim_without_consuming_key(self, record_property):
        import json
        from django.db import connection, transaction

        ledger = cl.MockCreditLedger(default_balance="1")
        with transaction.atomic():
            snapshot = {
                "ATOMIC_REQUESTS": connection.settings_dict.get("ATOMIC_REQUESTS"),
                "in_atomic_block": connection.in_atomic_block,
            }
            assert snapshot["in_atomic_block"] is True
            with pytest.raises(cl.CreditHoldStateError):
                ledger.claim_dispatch_once("r7-unconsumed")
        assert connection.in_atomic_block is False
        assert ledger.claim_dispatch_once("r7-unconsumed") is True
        assert ledger.claim_dispatch_once("r7-unconsumed") is False
        assert ledger.calls == []
        record_property("mock_claim_connection", json.dumps(snapshot))


class TestMonotonicAttestationR8:
    @pytest.mark.parametrize("boundary", ["response", "failure"])
    @pytest.mark.parametrize("history, expected, unknown", [
        ((), None, False),
        (("true", "true"), True, False),
        (("false", "false"), False, False),
        (("false", "invalid"), None, True),
        (("invalid", "true"), None, True),
        (("true", "false"), None, True),
        (("false", "invalid", "false"), None, True),
        (("invalid", "true", "true"), None, True),
        (("true", "false", "true"), None, True),
        ((None,), None, True),
        ((False,), None, True),
        (("FALSE",), None, True),
    ])
    def test_set_projection_is_monotonic(self, boundary, history, expected, unknown):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        seen = set()
        for value in history:
            headers = {"X-AgentCC-Dispatched": value}
            if boundary == "response":
                cl.note_gateway_response(call, SimpleNamespace(headers=headers))
            else:
                cl.note_gateway_failure(call, _GatewayError(429, "rate_limit_exceeded", headers=headers))
            seen.add(value if isinstance(value, str) and value in ("true", "false") else "malformed")
            assert call._gateway_dispatch_attestations == seen
            assert call.gateway_dispatch_conflict is (len(seen) > 1 or "malformed" in seen)
        cl.note_gateway_response(call, SimpleNamespace(headers={}))
        cl.note_gateway_failure(call, _GatewayError(429, "rate_limit_exceeded"))
        assert call._gateway_dispatch_attestations == seen
        assert call.gateway_dispatch_header_seen is bool(history)
        assert call.gateway_dispatched is expected
        assert call.gateway_dispatch_conflict is unknown
        # Empty evidence alone permits the guarded row-4 refusal fallback.
        assert call.refused_before_upstream is (not history or expected is False)

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("failure", [False, True])
    @pytest.mark.parametrize("priced", [False, True])
    @pytest.mark.parametrize("history", [
        ("false", "invalid"),
        ("invalid", "true"),
        ("true", "false"),
        ("false", "OUTPUT", "invalid"),
        ("false", "invalid", "OUTPUT"),
        ("invalid", "OUTPUT"),
        ("false", "OUTPUT"),
    ])
    def test_unknown_never_debits_or_refunds(self, monkeypatch, is_async, failure, priced, history):
        from asgiref.sync import async_to_sync

        org = _org()
        ledger = cl.MockCreditLedger(default_balance="1")
        meter = _meter(org, None, route=_managed())
        opened, warnings = [], []
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
        def observe(call):
            opened.append(call)
            _claim()
            for value in history:
                if value == "OUTPUT":
                    call.observe(cost="0.02") if priced else call.observe(value_info={})
                else:
                    cl.note_gateway_response(call, SimpleNamespace(headers={
                        "x-agentcc-dispatched": value,
                        "x-agentcc-request-id": "r8-evidence",
                    }))
            # A guarded-code error with no header cannot clear earlier evidence.
            cl.note_gateway_failure(call, _GatewayError(429, "rate_limit_exceeded"))
            if failure:
                raise TimeoutError("private r8 diagnostic")
        def run():
            with meter.metered(amount_estimate="0.10") as call:
                observe(call)
        async def arun():
            async with meter.ametered(amount_estimate="0.10") as call:
                observe(call)
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
            async_to_sync(arun)() if is_async else run()
        call = opened[0]
        call.note_dispatch_contradiction()
        assert call.status == "pending_reconcile" and call.actual_amount is None
        assert call.cost_source is None and call.dispatch_count == 1
        assert call.output_seen is ("OUTPUT" in history)
        assert _ops(ledger) == ["hold"]
        row = ledger.get_hold(call.hold_id)
        assert row["state"] == "held" and row["pending_reconcile"] is True
        assert row["gateway_request_id"] == "r8-evidence"
        assert ledger.holds[call.hold_id].debits == 0
        assert ledger.balance_of(org) == Decimal("1")
        assert ledger.held_of(org) == Decimal("0.10")
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert warnings == [("credit_dispatch_contradiction", {
            "organization_id": str(org.id), "request_id": call.request_id,
            "gateway_request_id": "r8-evidence",
        })]


@pytest.mark.django_db(transaction=True)
class TestOssClaimFallbackR8:
    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("mode", [
        "missing_module", "missing_function", "noncallable", "raises", "false", "none", "truthy",
    ])
    def test_unavailable_or_unconfirmed_oss_claim_is_terminal(self, monkeypatch, is_async, mode):
        import sys
        from asgiref.sync import async_to_sync
        from django.db import connection

        org = _org()
        ledger = cl.MockCreditLedger(default_balance="1")
        ledger.claim_dispatch_once = None
        meter = _meter(org, None, route=_managed())
        identity = cl.ManagedCall(meter.envelope, meter.route, dispatch_id="r8-oss-refusal")
        primitive_calls, entered = [], []
        if mode == "missing_module":
            monkeypatch.setitem(sys.modules, "agentcc.services.dispatch_claims", None)
        else:
            from agentcc.services import dispatch_claims

            def claim(key):
                primitive_calls.append(key)
                assert key == identity.idempotency_key
                assert _ops(ledger) == ["hold"]
                assert connection.in_atomic_block is False
                assert connection.get_autocommit() is True
                if mode == "raises":
                    raise ConnectionError("private r8 diagnostic")
                return {"false": False, "none": None, "truthy": 1}[mode]
            if mode == "missing_function":
                monkeypatch.delattr(dispatch_claims, "claim_dispatch_once")
            else:
                monkeypatch.setattr(dispatch_claims, "claim_dispatch_once", None if mode == "noncallable" else claim)
        def run():
            with meter.metered(amount_estimate="0.10", dispatch_id="r8-oss-refusal"):
                entered.append(True)
                _claim().observe(cost="0.02")
        async def arun():
            async with meter.ametered(amount_estimate="0.10", dispatch_id="r8-oss-refusal"):
                entered.append(True)
                _claim().observe(cost="0.02")
        with cl.use_credit_ledger(ledger), pytest.raises(cl.ManagedCallAlreadyCompletedError) as caught:
            async_to_sync(arun)() if is_async else run()
        preflight = mode in ("missing_module", "missing_function", "noncallable")
        assert primitive_calls == ([] if preflight else [identity.idempotency_key])
        assert _ops(ledger) == ([] if preflight else ["hold"])
        assert entered == []
        assert ledger.balance_of(org) == Decimal("1")
        assert all(h.state == "held" and h.debits == 0 for h in ledger.holds.values())
        assert caught.value.retryable is False and caught.value.retry_after is None
        assert caught.value.charged is None and caught.value.dispatched is None
        assert cl.refusal_activity_error(caught.value)["non_retryable"] is True
        assert "private r8 diagnostic" not in str(caught.value)

    @pytest.mark.parametrize("is_async", [False, True])
    def test_outer_atomic_refuses_before_oss_invocation(self, monkeypatch, is_async):
        from agentcc.services import dispatch_claims
        from asgiref.sync import async_to_sync
        from django.db import transaction

        ledger = cl.MockCreditLedger(default_balance="1")
        ledger.claim_dispatch_once = None
        primitive_calls, entered = [], []
        def claim(key):
            primitive_calls.append(key)
            return True
        monkeypatch.setattr(dispatch_claims, "claim_dispatch_once", claim)
        meter = _meter(_org(), None, route=_managed())
        def run():
            with meter.metered(amount_estimate="0.10", dispatch_id="r8-atomic"):
                entered.append(True)
        async def arun():
            async with meter.ametered(amount_estimate="0.10", dispatch_id="r8-atomic"):
                entered.append(True)
        with cl.use_credit_ledger(ledger), transaction.atomic(), pytest.raises(cl.ManagedCallAlreadyCompletedError):
            async_to_sync(arun)() if is_async else run()
        assert primitive_calls == entered == []
        assert _ops(ledger) == ["hold"]
        held = next(iter(ledger.holds.values()))
        assert held.state == "held" and held.debits == 0
        assert cl.MockCreditLedger.claim_dispatch_once(ledger, held.idempotency_key) is True


class TestMoneyR9HeaderOnly:
    @pytest.mark.parametrize("nested", [False, True])
    def test_body_id_without_header_never_matches_another_record(self, monkeypatch, nested):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        org = _org()
        ledger = cl.MockCreditLedger(default_balance="1")
        raw = _GatewayError(504, "gateway_timeout", request_id="other-request")
        if nested:
            raw.body = {"error": raw.body}
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                    _claim(gateway_id=None)
                    _raise_at_gateway_boundary(raw)
            assert call.gateway_request_id is None
            assert ledger.get_hold(call.hold_id)["gateway_request_id"] is None
            args = TestPendingReconcile()._args(call)
            foreign_record = dict(organization_id=str(org.id), gateway_request_id="other-request", actual_amount="0.08")
            with pytest.raises(cl.CreditHoldStateError):
                cl.reconcile_pending_hold(**args, settle_record=foreign_record)
            looked_up = []
            def lookup(**kw):
                looked_up.append(kw)
                return foreign_record
            monkeypatch.setattr(command, "lookup_settle_record", lookup)
            monkeypatch.setattr(command, "utc_now", lambda: call.opened_at + cl.RECONCILE_TTL)
            command.Command().handle()
            command.Command().handle()
        assert looked_up == []
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1")

    @pytest.mark.parametrize("nested", [False, True])
    def test_valid_header_wins_over_different_body_id(self, nested):
        call = cl.ManagedCall(_envelope(_org(), None), _managed())
        raw = _GatewayError(504, "gateway_timeout", request_id="spoofed-body-id",
                            headers={"X-AgentCC-Request-ID": "trusted-response-id"})
        if nested:
            raw.body = {"error": raw.body}
        cl.note_gateway_failure(call, raw)
        assert call.gateway_request_id == "trusted-response-id"
        cl.note_gateway_failure(call, _GatewayError(504, "gateway_timeout", request_id="later-body-id"))
        assert call.gateway_request_id == "trusted-response-id"


class TestMoneyR9Corrections:
    def _provisional(self, ledger, cost="0.06"):
        org = _org()
        with cl.use_credit_ledger(ledger):
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                _claim()
                cl.note_gateway_response(call, SimpleNamespace(headers={"x-agentcc-request-id": "response-r9"}))
                call.observe(cost=cost)
        snapshot = ledger.get_hold(call.hold_id)
        assert snapshot["state"] == "settled"
        assert snapshot["settlement_source"] == "provider_usage"
        assert snapshot["pending_reconcile"] is True
        assert snapshot["gateway_request_id"] == "response-r9"
        return org, call

    def _args(self, call):
        return TestPendingReconcile()._args(call)

    def _record(self, call, amount):
        return dict(organization_id=call.envelope.organization_id,
                    gateway_request_id=call.gateway_request_id, actual_amount=amount)

    @pytest.mark.parametrize("cost", [None, "0", "NaN", "-1"])
    @pytest.mark.parametrize("failed", [False, True])
    def test_usage_missing_or_unpriced_is_pending_then_record_settles(self, cost, failed):
        ledger = cl.MockCreditLedger(default_balance="1")
        org = _org()
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedOutcomePendingReconcileError) as caught:
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                    _claim()
                    cl.note_gateway_response(call, SimpleNamespace(headers={"x-agentcc-request-id": "response-r9"}))
                    call.observe(response=SimpleNamespace(), cost=cost)
                    if failed:
                        raise RuntimeError("private provider diagnostic")
            assert call.status == "pending_reconcile"
            assert call.actual_amount is None and call.cost_source is None
            assert not caught.value.retryable and caught.value.charged is None
            assert _ops(ledger) == ["hold"]
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.03")) == "settled"
        assert ledger.balance_of(org) == Decimal("0.97")
        assert ledger.get_hold(call.hold_id)["settlement_source"] == "settle_record"
        assert ledger.holds[call.hold_id].debits == 1

    @pytest.mark.parametrize("actual", ["0", "0.02", "0.06"])
    def test_command_corrects_provisional_downward_once(self, monkeypatch, settings, actual):
        from datetime import timedelta
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        lookups = []
        def lookup(**kw):
            lookups.append(kw)
            return dict(**kw, actual_amount=actual)
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = lookup
        monkeypatch.setattr(command, "utc_now", lambda: call.opened_at + timedelta(seconds=1))
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
            before = list(ledger.calls)
            command.Command().handle()
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, actual)) == "settled"
        assert ledger.calls == before
        assert lookups == [{"organization_id": str(org.id), "gateway_request_id": "response-r9"}]
        row = ledger.get_hold(call.hold_id)
        assert row["settled_amount"] == Decimal(actual)
        assert row["settlement_source"] == "settle_record"
        assert row["pending_reconcile"] is False
        assert ledger.balance_of(org) == Decimal("1") - Decimal(actual)
        assert ledger.holds[call.hold_id].debits == 1
        assert _ops(ledger).count("adjust_settled") == 1
        assert ledger.held_of(org) == 0

    def test_command_corrects_upward_to_hold_once_without_debt(self, monkeypatch, settings):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger, "0.02")
        assert ledger.available_of(org) == Decimal("0.90")
        other = ledger.hold(org=org, workspace=None, amount_estimate="0.90", idempotency_key="other", purpose="test")
        assert ledger.available_of(org) == 0
        incidents = []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = lambda **kw: dict(**kw, actual_amount="0.20")
        monkeypatch.setattr(command, "utc_now", lambda: call.opened_at + cl.RECONCILE_TTL)
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
            before = list(ledger.calls)
            command.Command().handle()
        assert ledger.calls == before
        assert ledger.get_hold(call.hold_id)["settled_amount"] == Decimal("0.10")
        assert ledger.balance_of(org) == Decimal("0.90")
        assert ledger.available_of(org) == 0
        assert ledger.holds[other].state == "held"
        assert incidents == [{"organization_id": str(org.id), "request_id": call.request_id,
                              "model": "gpt-4o", "excess": Decimal("0.10")}]
        assert _ops(ledger).count("settle") == _ops(ledger).count("adjust_settled") == 1

    def test_prior_excess_incident_is_not_repeated_by_correction(self, monkeypatch):
        ledger = cl.MockCreditLedger(default_balance="1")
        incidents = []
        monkeypatch.setattr(cl, "record_hold_excess_incident", lambda **kw: incidents.append(kw))
        org, call = self._provisional(ledger, "0.20")
        assert ledger.get_hold(call.hold_id)["hold_excess_recorded"] is True
        with cl.use_credit_ledger(ledger):
            for _ in range(2):
                assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.30")) == "settled"
        assert len(incidents) == 1 and incidents[0]["excess"] == Decimal("0.10")
        assert ledger.balance_of(org) == Decimal("0.90")

    def test_missing_correction_primitive_keeps_provisional_and_alerts(self, monkeypatch):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        before = ledger.get_hold(call.hold_id)
        operations = list(ledger.calls)
        monkeypatch.setattr(ledger, "adjust_settled", None)
        incidents = []
        monkeypatch.setattr(cl.logger, "error", lambda event, **kw: incidents.append((event, kw)))
        with cl.use_credit_ledger(ledger):
            for _ in range(2):
                with pytest.raises(cl.CreditHoldStateError):
                    cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.02"))
        assert ledger.get_hold(call.hold_id) == before
        assert ledger.calls == operations
        assert ledger.balance_of(org) == Decimal("0.94")
        assert all(event == "credit_settlement_correction_failed" for event, _ in incidents)
        assert len(incidents) == 2
        assert all(set(fields) == {"organization_id", "request_id", "error_type"} for _, fields in incidents)

    @pytest.mark.parametrize("mode", ["raise", "unconfirmed"])
    def test_correction_failure_keeps_provisional_and_alerts(self, monkeypatch, mode):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        before = ledger.get_hold(call.hold_id)
        def broken(*args):
            if mode == "raise":
                raise RuntimeError("private upstream diagnostic")
            return None
        monkeypatch.setattr(ledger, "adjust_settled", broken)
        incidents = []
        monkeypatch.setattr(cl.logger, "error", lambda event, **kw: incidents.append((event, kw)))
        with cl.use_credit_ledger(ledger), pytest.raises(cl.CreditHoldStateError):
            cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.02"))
        assert ledger.get_hold(call.hold_id) == before
        assert ledger.balance_of(org) == Decimal("0.94")
        assert len(incidents) == 1
        assert "private upstream diagnostic" not in repr(incidents)
        assert _ops(ledger).count("settle") == 1 and "release" not in _ops(ledger)

    @pytest.mark.parametrize("field,value", [("organization_id", "wrong"), ("gateway_request_id", "wrong"), ("actual_amount", "NaN"), ("actual_amount", "-1")])
    def test_invalid_record_cannot_correct_provisional(self, field, value):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        before = ledger.get_hold(call.hold_id)
        record = self._record(call, "0.02")
        record[field] = value
        with cl.use_credit_ledger(ledger), pytest.raises(cl.CreditHoldStateError):
            cl.reconcile_pending_hold(**self._args(call), settle_record=record)
        assert ledger.get_hold(call.hold_id) == before
        assert ledger.balance_of(org) == Decimal("0.94")

    @pytest.mark.parametrize("invalid", ["-0.01", "0.11", "NaN", "Infinity", True])
    def test_primitive_is_bounded_final_once_and_replay_safe(self, invalid):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        with pytest.raises(cl.CreditHoldStateError):
            ledger.adjust_settled(call.hold_id, invalid)
        assert ledger.get_hold(call.hold_id)["settled_amount"] == Decimal("0.06")
        assert ledger.adjust_settled(call.hold_id, Decimal("0.02")) is True
        before = list(ledger.calls)
        assert ledger.adjust_settled(call.hold_id, Decimal("0.02")) is False
        assert ledger.calls == before
        with pytest.raises(cl.CreditHoldStateError):
            ledger.adjust_settled(call.hold_id, Decimal("0.03"))
        assert ledger.balance_of(org) == Decimal("0.98")
        held_id = ledger.hold(org=org, workspace=None, amount_estimate="0.10", idempotency_key="not-settled", purpose="test")
        with pytest.raises(cl.CreditHoldStateError):
            ledger.adjust_settled(held_id, Decimal("0.02"))
        ledger.release(held_id)
        with pytest.raises(cl.CreditHoldStateError):
            ledger.adjust_settled(held_id, Decimal("0.02"))

    def test_concurrent_correction_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: ledger.adjust_settled(call.hold_id, Decimal("0.02")), range(2)))
        assert sorted(results) == [False, True]
        assert ledger.balance_of(org) == Decimal("0.98")
        assert _ops(ledger).count("adjust_settled") == 1

    def test_lost_correction_ack_does_not_apply_twice(self, monkeypatch):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        primitive = ledger.adjust_settled
        def lost_ack(*args):
            primitive(*args)
            raise ConnectionError("ack lost")
        monkeypatch.setattr(ledger, "adjust_settled", lost_ack)
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditHoldStateError):
                cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.02"))
            before = list(ledger.calls)
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.02")) == "settled"
        assert ledger.calls == before
        assert ledger.balance_of(org) == Decimal("0.98")
        assert ledger.get_hold(call.hold_id)["pending_reconcile"] is False

    def test_provisional_reserves_remainder_until_record_even_after_ttl(self):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = self._provisional(ledger)
        assert ledger.held_of(org) == Decimal("0.04")
        assert ledger.available_of(org) == Decimal("0.90")
        with pytest.raises(cl.InsufficientCreditError):
            ledger.hold(org=org, workspace=None, amount_estimate="0.91", idempotency_key="overspend", purpose="test")
        with cl.use_credit_ledger(ledger):
            assert cl.reconcile_pending_hold(**self._args(call), now=call.opened_at + cl.RECONCILE_TTL) == "pending_reconcile"
        assert ledger.holds[call.hold_id].state == "settled"
        assert ledger.list_pending_holds(call.opened_at) == [ledger.get_hold(call.hold_id)]
        assert "release" not in _ops(ledger)

    def test_settle_ack_loss_keeps_durable_provisional_discoverable(self):
        class LostAck(cl.MockCreditLedger):
            idempotent_settle = False
            def settle(self, hold_id, amount, **metadata):
                super().settle(hold_id, amount, **metadata)
                raise ConnectionError("ack lost")
        ledger = LostAck(default_balance="1")
        org = _org()
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditSettlementError):
                with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                    _claim().observe(cost="0.06")
                    cl.note_gateway_response(call, SimpleNamespace(headers={"x-agentcc-request-id": "response-r9"}))
            row = ledger.get_hold(call.hold_id)
            assert row["settlement_source"] == "provider_usage" and row["pending_reconcile"] is True
            assert ledger.list_pending_holds(call.opened_at) == [row]
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.02")) == "settled"
        assert ledger.balance_of(org) == Decimal("0.98")
        assert _ops(ledger).count("settle") == 1

    def test_legacy_settle_signature_fails_closed_without_debit(self):
        class Legacy(cl.MockCreditLedger):
            def settle(self, hold_id, amount):
                raise AssertionError("must not retry without durable metadata")
        ledger = Legacy(default_balance="1")
        org = _org()
        with cl.use_credit_ledger(ledger), pytest.raises(cl.CreditSettlementError):
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                _claim().observe(cost="0.06")
        assert ledger.balance_of(org) == Decimal("1")
        assert ledger.get_hold(call.hold_id)["pending_reconcile"] is True
        assert _ops(ledger) == ["hold"]


class TestMoneyR10NoKey:
    """OK-COMPOSE-r10 P1 (money P1-01 = correctness COR-01, main G05).

    A claimed call WITHOUT a validated x-agentcc-request-id RESPONSE header
    never takes the provisional provider_usage debit: it is R11 (pending,
    uncharged) BEFORE any debit, so the 24 h TTL release applies. The same
    outcome with the header is r9's provisional debit. Only a response header
    is a reconcile key; a relayed body request_id never is.
    """

    BAD_HEADERS = [None, "", "invalid header!", "x" * 129]

    def _drive(self, ledger, *, gateway_id, failure, is_async):
        org, seen = _org(), []
        meter = _meter(org, None, route=_managed())

        def body(call):
            seen.append(call)
            _claim(gateway_id=gateway_id)
            call.observe(cost="0.06")
            if failure:
                # A relayed upstream body can carry any request_id: never a key.
                _raise_at_gateway_boundary(_GatewayError(504, "gateway_timeout", request_id="body-only-id"))

        def run():
            with meter.metered(amount_estimate="0.10") as call:
                body(call)

        async def arun():
            async with meter.ametered(amount_estimate="0.10") as call:
                body(call)

        error = None
        with cl.use_credit_ledger(ledger):
            try:
                asyncio.run(arun()) if is_async else run()
            except Exception as exc:
                error = exc
        return org, seen[0], error

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("failure", [False, True])
    @pytest.mark.parametrize("header", BAD_HEADERS)
    def test_priced_output_without_a_response_key_is_r11_and_releases_at_ttl(self, is_async, failure, header):
        from datetime import timedelta
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call, error = self._drive(ledger, gateway_id=header, failure=failure, is_async=is_async)
        assert isinstance(error, cl.ManagedOutcomePendingReconcileError)
        assert isinstance(error, ValueError) and error.retryable is False and error.charged is None
        assert call.output_seen is True and call.observe() == Decimal("0.06")  # priced output
        assert call.gateway_request_id is None
        assert call.status == "pending_reconcile"
        assert call.actual_amount is None and call.cost_source is None
        # No debit, no provisional marker, nothing reserved beyond the hold.
        assert _ops(ledger) == ["hold"]
        row = ledger.get_hold(call.hold_id)
        assert row["state"] == "held" and row["pending_reconcile"] is True
        assert row["gateway_request_id"] is None and row["settled_amount"] is None
        assert "settlement_source" not in row
        assert ledger.holds[call.hold_id].debits == 0
        assert ledger.balance_of(org) == Decimal("1") and ledger.held_of(org) == Decimal("0.10")
        args = TestPendingReconcile()._args(call)
        with cl.use_credit_ledger(ledger):
            assert cl.reconcile_pending_hold(
                **args, now=call.opened_at + cl.RECONCILE_TTL - timedelta(microseconds=1)) == "pending_reconcile"
            assert cl.reconcile_pending_hold(**args, now=call.opened_at + cl.RECONCILE_TTL) == "released"
            before = list(ledger.calls)
            assert cl.reconcile_pending_hold(**args, now=call.opened_at + cl.RECONCILE_TTL) == "released"
        assert ledger.calls == before
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1") and ledger.held_of(org) == 0

    def test_command_never_looks_up_a_headerless_priced_call_and_releases_it(self, monkeypatch):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call, error = self._drive(ledger, gateway_id=None, failure=True, is_async=False)
        assert isinstance(error, cl.ManagedOutcomePendingReconcileError)
        looked_up = []
        monkeypatch.setattr(command, "lookup_settle_record", lambda **kw: looked_up.append(kw))
        monkeypatch.setattr(command, "utc_now", lambda: call.opened_at + cl.RECONCILE_TTL)
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
            command.Command().handle()
        assert looked_up == []
        assert _ops(ledger) == ["hold", "release"]
        assert ledger.balance_of(org) == Decimal("1") and ledger.held_of(org) == 0

    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("failure", [False, True])
    def test_the_same_outcome_with_a_response_header_takes_the_provisional_debit(self, is_async, failure):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call, error = self._drive(ledger, gateway_id="response-r10", failure=failure, is_async=is_async)
        if failure:
            assert isinstance(error, cl.ManagedCallFailedAfterDispatchError)
        else:
            assert error is None
        assert call.gateway_request_id == "response-r10"  # never the body id
        assert call.status == "settled" and call.actual_amount == Decimal("0.06")
        assert call.cost_source == "provider_usage"
        row = ledger.get_hold(call.hold_id)
        assert row["state"] == "settled" and row["settlement_source"] == "provider_usage"
        assert row["pending_reconcile"] is True and row["gateway_request_id"] == "response-r10"
        assert _ops(ledger) == ["hold", "settle"]
        assert ledger.balance_of(org) == Decimal("0.94") and ledger.held_of(org) == Decimal("0.04")

    def test_port_refuses_a_provisional_debit_without_a_validated_response_key(self):
        ledger = cl.MockCreditLedger(default_balance="1")
        org = _org()
        hold_id = ledger.hold(org=org, workspace=None, amount_estimate="0.10",
                              idempotency_key="r10-nokey", purpose="test")
        for key in (None, "", "invalid header!"):
            with pytest.raises(cl.CreditHoldStateError):
                ledger.settle(hold_id, "0.06", source="provider_usage", gateway_request_id=key)
        assert [entry[0] for entry in ledger.calls] == ["hold"]
        assert ledger.balance_of(org) == Decimal("1") and ledger.get_hold(hold_id)["state"] == "held"
        ledger.settle(hold_id, "0.06", source="provider_usage", gateway_request_id="response-r10")
        assert ledger.balance_of(org) == Decimal("0.94")


class TestMoneyR11Incidents:
    """The incident IS the immutable hold field, not its best-effort log hint."""

    def _setup(self, *, provisional=True):
        ledger = cl.MockCreditLedger(default_balance="1")
        helper = TestMoneyR9Corrections() if provisional else TestPendingReconcile()
        org, call = helper._provisional(ledger) if provisional else helper._pending(ledger)
        return ledger, org, call

    def _args(self, call):
        return TestPendingReconcile()._args(call)

    def _record(self, call, amount="0.20"):
        return TestMoneyR9Corrections()._record(call, amount)

    @staticmethod
    def _field(ledger, org, call, excess="0.10"):
        from datetime import datetime, timedelta
        row = ledger.get_hold(call.hold_id)
        field = row["credit_hold_excess"]
        assert isinstance(field, dict)
        assert field == {"amount": excess, "org": str(org.id),
                         "request_id": call.request_id, "model": "gpt-4o",
                         "recorded_at": field["recorded_at"]}
        assert datetime.fromisoformat(field["recorded_at"]).utcoffset() == timedelta(0)
        assert row["hold_excess_recorded"] is True
        assert "excess_incident_amount" not in row and "excess_incident_delivered" not in row
        assert sum("credit_hold_excess" in hold.metadata for hold in ledger.holds.values()) == 1
        return field

    @staticmethod
    def _logs(monkeypatch):
        logs = []
        # Keep the REAL production record_hold_excess_incident helper in use.
        monkeypatch.setattr(cl.logger, "warning", lambda event, **kw: logs.append((event, kw)))
        return logs

    @pytest.mark.parametrize("provisional", [False, True])
    def test_concurrent_stale_snapshot_reconcile_records_one_field_and_one_log(self, monkeypatch, provisional):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        ledger, org, call = self._setup(provisional=provisional)
        logs = self._logs(monkeypatch)
        snapshot, barrier = ledger.get_hold, Barrier(2)

        def stale_snapshot(hold_id):
            row = snapshot(hold_id)
            if row["state"] == "held" or row.get("settlement_source") == "provider_usage":
                barrier.wait(timeout=5)  # BOTH workers see no field before either write.
            return row

        def reconcile(_):
            return cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call))

        with cl.use_credit_ledger(ledger):
            with monkeypatch.context() as patch:
                patch.setattr(ledger, "get_hold", stale_snapshot)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    assert list(pool.map(reconcile, range(2))) == ["settled", "settled"]
            field = self._field(ledger, org, call)
            before = list(ledger.calls)
            assert reconcile(None) == "settled"
            assert reconcile(None) == "settled"
        assert ledger.calls == before and self._field(ledger, org, call) == field
        assert logs == [("credit_hold_excess", {"organization_id": str(org.id),
                         "request_id": call.request_id, "model": "gpt-4o", "excess": "0.10"})]
        assert ledger.balance_of(org) == Decimal("0.90")
        assert ledger.holds[call.hold_id].debits == 1
        assert _ops(ledger).count("adjust_settled") == int(provisional)
        assert ledger.list_pending_holds(call.opened_at) == []

    @pytest.mark.parametrize("provisional", [False, True])
    def test_lost_write_ack_leaves_one_field_without_log_redelivery(self, monkeypatch, provisional):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger, org, call = self._setup(provisional=provisional)
        logs = self._logs(monkeypatch)
        name = "adjust_settled" if provisional else "settle"
        primitive = getattr(ledger, name)

        def lost_ack(*args, **kwargs):
            assert primitive(*args, **kwargs) is True
            self._field(ledger, org, call)  # already committed WITH the capped amount
            assert ledger.get_hold(call.hold_id)["settled_amount"] == Decimal("0.10")
            raise ConnectionError("ack lost")

        monkeypatch.setattr(ledger, name, lost_ack)
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.CreditHoldStateError if provisional else ConnectionError):
                cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call))
            field, before = self._field(ledger, org, call), list(ledger.calls)
            assert logs == []  # log acknowledgement is NOT the durable incident
            monkeypatch.setattr(command, "lookup_settle_record", lambda **kw: pytest.fail("terminal row re-listed"))
            for _ in range(2):
                command.Command().handle()
                assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call)) == "settled"
        assert self._field(ledger, org, call) == field and ledger.calls == before
        assert logs == [] and ledger.list_pending_holds(call.opened_at) == []
        assert ledger.balance_of(org) == Decimal("0.90")
        assert ledger.holds[call.hold_id].debits == 1
        assert _ops(ledger).count("adjust_settled") == int(provisional)

    @pytest.mark.parametrize("provisional", [False, True])
    def test_real_logger_failure_preserves_field_money_and_terminal_enumeration(self, monkeypatch, settings, provisional):
        from model_hub.management.commands import reconcile_pending_credit_holds as command
        ledger, org, call = self._setup(provisional=provisional)
        attempts, lookups = [], []

        def failing_logger(event, **fields):
            attempts.append((event, fields))
            self._field(ledger, org, call)  # logger is called AFTER atomic settlement
            assert ledger.balance_of(org) == Decimal("0.90")
            raise RuntimeError("private logging diagnostic")

        def lookup(**kwargs):
            lookups.append(kwargs)
            return dict(**kwargs, actual_amount="0.20")

        monkeypatch.setattr(cl.logger, "warning", failing_logger)
        settings.FI_CREDIT_SETTLE_RECORD_LOOKUP = lookup
        with cl.use_credit_ledger(ledger):
            command.Command().handle()
            field, before = self._field(ledger, org, call), list(ledger.calls)
            command.Command().handle()
            command.Command().handle()
        assert len(attempts) == 1 and attempts[0][0] == "credit_hold_excess"
        assert set(attempts[0][1]) == {"organization_id", "request_id", "model", "excess"}
        assert len(lookups) == 1 and ledger.calls == before
        assert self._field(ledger, org, call) == field
        assert ledger.balance_of(org) == Decimal("0.90") and ledger.held_of(org) == 0
        assert ledger.list_pending_holds(call.opened_at) == []

    @pytest.mark.parametrize("provisional", [False, True])
    @pytest.mark.parametrize("actual", ["0", "0.02", "0.10"])
    def test_no_excess_has_no_field_or_log(self, monkeypatch, provisional, actual):
        ledger, org, call = self._setup(provisional=provisional)
        logs = self._logs(monkeypatch)
        assert "credit_hold_excess" not in ledger.get_hold(call.hold_id)
        with cl.use_credit_ledger(ledger):
            for _ in range(2):
                assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, actual)) == "settled"
        row = ledger.get_hold(call.hold_id)
        assert "credit_hold_excess" not in row and not row.get("hold_excess_recorded")
        assert logs == [] and ledger.balance_of(org) == Decimal("1") - Decimal(actual)

    @pytest.mark.parametrize("provisional", [False, True])
    def test_failed_money_write_creates_no_incident_then_retry_commits_both(self, monkeypatch, provisional):
        ledger, org, call = self._setup(provisional=provisional)
        logs = self._logs(monkeypatch)
        before, balance = ledger.get_hold(call.hold_id), ledger.balance_of(org)
        name = "adjust_settled" if provisional else "settle"
        primitive = getattr(ledger, name)

        def failed_write(*args, **kwargs):
            assert "credit_hold_excess" not in ledger.get_hold(call.hold_id)
            raise ConnectionError("write never committed")

        with cl.use_credit_ledger(ledger):
            with monkeypatch.context() as patch:
                patch.setattr(ledger, name, failed_write)
                with pytest.raises(cl.CreditHoldStateError if provisional else ConnectionError):
                    cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call))
            assert ledger.get_hold(call.hold_id) == before and ledger.balance_of(org) == balance
            assert logs == []
            assert getattr(ledger, name) == primitive
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call)) == "settled"
        self._field(ledger, org, call)
        assert len(logs) == 1 and ledger.balance_of(org) == Decimal("0.90")

    @pytest.mark.parametrize("source", ["provider_usage", "settle_record"])
    @pytest.mark.parametrize("failures", [0, 2])
    def test_initial_settlement_records_field_before_one_post_write_log(self, monkeypatch, source, failures):
        ledger, org = cl.MockCreditLedger(default_balance="1"), _org()
        ledger.fail_next_settles = failures
        logs, snapshots = [], []

        def warning(event, **fields):
            logs.append((event, fields))
            if event == "credit_hold_excess":
                snapshots.append((ledger.get_hold(call.hold_id), ledger.balance_of(org)))

        monkeypatch.setattr(cl.logger, "warning", warning)
        with cl.use_credit_ledger(ledger):
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                _claim().observe(cost="0.20")
                if source == "settle_record":
                    call.settle_record = self._record(call)
        field = self._field(ledger, org, call)
        assert len(snapshots) == 1
        assert snapshots[0][0]["credit_hold_excess"] == field
        assert snapshots[0][0]["settled_amount"] == Decimal("0.10")
        assert snapshots[0][1] == Decimal("0.90")
        assert [event for event, _ in logs].count("credit_hold_excess") == 1
        assert call.settlement_amount() == (Decimal("0.10"), source + "_capped")
        assert [event for event, _ in logs].count("credit_hold_excess") == 1

    @pytest.mark.parametrize("idempotent", [False, True])
    def test_initial_settle_lost_ack_keeps_field_and_does_not_prelog(self, monkeypatch, idempotent):
        ledger, org = cl.MockCreditLedger(default_balance="1"), _org()
        ledger.idempotent_settle = idempotent
        logs = self._logs(monkeypatch)
        primitive, first = ledger.settle, [True]

        def lost_first_ack(*args, **kwargs):
            changed = primitive(*args, **kwargs)
            if first.pop() if first else False:
                raise ConnectionError("ack lost")
            return changed

        def run():
            with _meter(org, None, route=_managed()).metered(amount_estimate="0.10") as call:
                seen.append(call)
                _claim().observe(cost="0.20")

        monkeypatch.setattr(ledger, "settle", lost_first_ack)
        seen = []
        with cl.use_credit_ledger(ledger):
            if idempotent:
                run()
            else:
                with pytest.raises(cl.CreditSettlementError):
                    run()
            call = seen[0]
            field = self._field(ledger, org, call)
            assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.30")) == "settled"
        assert self._field(ledger, org, call) == field
        assert all(event != "credit_hold_excess" for event, _ in logs)
        assert ledger.balance_of(org) == Decimal("0.90") and ledger.holds[call.hold_id].debits == 1

    def test_initial_field_is_first_wins_across_later_excess_correction(self, monkeypatch):
        logs = self._logs(monkeypatch)
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = TestMoneyR9Corrections()._provisional(ledger, "0.20")
        before = self._field(ledger, org, call)
        with cl.use_credit_ledger(ledger):
            for _ in range(2):
                assert cl.reconcile_pending_hold(**self._args(call), settle_record=self._record(call, "0.30")) == "settled"
        assert self._field(ledger, org, call) == before and len(logs) == 1
        assert ledger.balance_of(org) == Decimal("0.90")

    @pytest.mark.parametrize("provisional", [False, True])
    def test_primitive_replay_never_overwrites_field_or_timestamp(self, provisional):
        ledger, org, call = self._setup(provisional=provisional)
        primitive = ledger.adjust_settled if provisional else ledger.settle
        assert primitive(call.hold_id, Decimal("0.10"), excess=Decimal("0.10")) is True
        field = self._field(ledger, org, call)
        assert primitive(call.hold_id, Decimal("0.10"), excess=Decimal("0.10")) is False
        assert primitive(call.hold_id, Decimal("0.10"), excess=Decimal("0.99")) is False
        assert self._field(ledger, org, call) == field and ledger.balance_of(org) == Decimal("0.90")
        with pytest.raises(cl.CreditHoldStateError):
            primitive(call.hold_id, Decimal("0.02"), excess=Decimal("0.10"))
        assert self._field(ledger, org, call) == field

    @pytest.mark.parametrize("provisional", [False, True])
    @pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", True, None])
    def test_invalid_excess_rejects_before_money_or_field(self, provisional, bad):
        ledger, org, call = self._setup(provisional=provisional)
        before, balance = ledger.get_hold(call.hold_id), ledger.balance_of(org)
        primitive = ledger.adjust_settled if provisional else ledger.settle
        with pytest.raises(cl.CreditHoldStateError):
            primitive(call.hold_id, Decimal("0.10"), excess=bad)
        assert ledger.get_hold(call.hold_id) == before and ledger.balance_of(org) == balance

    @pytest.mark.parametrize("provisional", [False, True])
    def test_positive_excess_requires_the_capped_hold_amount(self, provisional):
        ledger, org, call = self._setup(provisional=provisional)
        before, balance = ledger.get_hold(call.hold_id), ledger.balance_of(org)
        primitive = ledger.adjust_settled if provisional else ledger.settle
        with pytest.raises(cl.CreditHoldStateError):
            primitive(call.hold_id, Decimal("0.05"), excess=Decimal("0.10"))
        assert ledger.get_hold(call.hold_id) == before and ledger.balance_of(org) == balance

    def test_incident_snapshot_cannot_mutate_the_persisted_field(self):
        ledger = cl.MockCreditLedger(default_balance="1")
        org, call = TestMoneyR9Corrections()._provisional(ledger, "0.20")
        before = dict(self._field(ledger, org, call))
        ledger.get_hold(call.hold_id)["credit_hold_excess"]["amount"] = "999"
        ledger.list_pending_holds(call.opened_at)[0]["credit_hold_excess"].clear()
        assert self._field(ledger, org, call) == before
        assert ledger.balance_of(org) == Decimal("0.90")

    def test_port_contract_has_no_outbox_primitives(self):
        for port in (cl.CreditLedgerPort, cl.MockCreditLedger, cl.UnconfiguredCreditLedger):
            assert not hasattr(port, "persist_excess_incident")
            assert not hasattr(port, "ack_excess_incident")
        assert not hasattr(cl, "_persist_excess_incident")
        assert not hasattr(cl, "_deliver_excess_incident")

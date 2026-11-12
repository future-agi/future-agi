"""Managed (Future AGI credits) refusal codes in tfc.utils.error_codes.

ONEKEY-REAL-REFUSAL-CONTRACT-r1: the gateway's credit, budget and billing
refusals are keyed on ``error.code``. Each code maps to its own user message
before the generic substring and 429 rules run:

- managed_credits_exhausted -> add credits
- managed_credits_insufficient (not retryable) -> add credits, with the
  required / available amounts when the refusal carries them
- managed_credits_insufficient with limit_source=agentcc_in_flight and
  remedy_hint=retry -> transient "try again in a few seconds"
- budget_exceeded / org_budget_exceeded / agent_budget_exhausted -> budget
  reached, never the retryable rate-limit message
- managed_spend_velocity -> slow down
- managed_billing_unavailable -> billing temporarily unavailable

A managed credit code is never stamped FREE_TIER_LIMIT. Strings without a
managed code keep their existing classification (test_usage_error_code.py and
test_usage_copy.py are unchanged).

RED at base 651ab5bb: every managed case below falls through to the generic
rules there (429 -> RATE_LIMIT_REACHED / RATE_LIMITED, 'insufficient' ->
INSUFFICIENT_CREDITS / FREE_TIER_LIMIT, exhausted -> unmapped), and the
MANAGED_* message keys do not exist.

Run with: futureagi/bin/test tfc/utils/tests/test_managed_refusal_codes.py -v
"""

import json
from types import SimpleNamespace

import pytest

from model_hub.services.credit_ledger_port import ManagedCallRefusal

from tfc.utils.error_codes import (
    get_error_for_api_status,
    get_error_message,
    get_specific_error_message,
    get_usage_error_code,
)

pytestmark = pytest.mark.unit

RATE_LIMIT_MESSAGE = get_error_message("RATE_LIMIT_REACHED")
NEUTRAL_INSUFFICIENT_MESSAGE = get_error_message("INSUFFICIENT_CREDITS")


def _body(code, error_type="payment_required", **metadata):
    return {
        "error": {
            "type": error_type,
            "code": code,
            "message": "gateway text that must not decide the classification",
            "param": None,
            "metadata": metadata,
        }
    }


class _ProviderStatusError(Exception):
    """Shaped like the OpenAI client's APIStatusError: ``code`` and ``body``
    come from the gateway's ``error`` object, the text is the raw response."""

    def __init__(self, status, body):
        inner = body["error"]
        super().__init__(f"Error code: {status} - {body}")
        self.status_code = status
        self.body = inner
        self.code = inner["code"]
        self.type = inner["type"]
        self.response = SimpleNamespace(headers={"Retry-After": "2"})


class _TypedRefusal(ManagedCallRefusal):
    """Concrete local refusal; a raw SDK marker alone is not managed context."""

    is_managed_refusal = True

    def __init__(self, code, metadata=None):
        super().__init__("Typed refusal")
        self.code = code
        self.metadata = metadata or {}
        from tfc.utils.error_codes import classify_managed_refusal, MANAGED_REFUSAL_CONTRACT
        self.http_status = next(status for status, name in MANAGED_REFUSAL_CONTRACT if name == code)
        display = classify_managed_refusal(self.http_status, code, {"metadata": self.metadata}, 2)
        self.message = display["message"]
        self.contract_valid = True
        self.retry_after = display["retry_after"]
        self.display_wait_seconds = display["display_wait_seconds"]
        self.display_classification = display["display_classification"]


def _carriers(status, body):
    """The same gateway refusal as it reaches the classifiers."""
    inner = body["error"]
    return [
        _ProviderStatusError(status, body),
        _TypedRefusal(inner["code"], inner["metadata"]),
    ]


def _wrapped(error):
    """``RunPrompt`` re-raises ``Exception(concise)`` with the original as context."""
    try:
        try:
            raise error
        except Exception:
            raise Exception("Provider call failed")
    except Exception as outer:
        return outer


EXHAUSTED = _body(
    "managed_credits_exhausted",
    limit_source="agentcc_credits",
    remedy_hint="add_credits",
    required_usd=0.04,
    available_usd=0.0,
    scope="org",
    resets_at=None,
)
INSUFFICIENT = _body(
    "managed_credits_insufficient",
    limit_source="agentcc_credits",
    remedy_hint="add_credits",
    required_usd=0.04,
    available_usd=0.01,
    scope="org",
)
IN_FLIGHT = _body(
    "managed_credits_insufficient",
    limit_source="agentcc_in_flight",
    remedy_hint="retry",
    required_usd=0.04,
    available_usd=0.01,
    scope="org",
)
AGENT_BUDGET = _body("agent_budget_exhausted")
BUDGET = _body("budget_exceeded", error_type="rate_limit_error")
ORG_BUDGET = _body("org_budget_exceeded", error_type="rate_limit_error")
VELOCITY = _body("managed_spend_velocity", error_type="rate_limit_error")
BILLING_DOWN = _body("managed_billing_unavailable", error_type="server_error")


class TestManagedMessagesComeFirst:
    @pytest.mark.parametrize("error", _carriers(402, EXHAUSTED))
    def test_exhausted_maps_to_add_credits(self, error):
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_CREDITS_EXHAUSTED"
        )
        assert get_usage_error_code(error, managed_context=True) == "managed_credits_exhausted"

    @pytest.mark.parametrize("error", _carriers(402, INSUFFICIENT))
    def test_insufficient_maps_to_add_credits_with_amounts(self, error):
        message = get_specific_error_message(error, managed_context=True)
        assert message == get_error_message(
            "MANAGED_CREDITS_INSUFFICIENT_AMOUNTS"
        ).format(required="0.04", available="0.01")
        assert "$0.04" in message and "$0.01" in message
        assert message != NEUTRAL_INSUFFICIENT_MESSAGE
        assert get_usage_error_code(error, managed_context=True) == "managed_credits_insufficient"

    def test_insufficient_without_amounts_still_asks_for_credits(self):
        error = _TypedRefusal(
            "managed_credits_insufficient", {"limit_source": "agentcc_credits"}
        )
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_CREDITS_INSUFFICIENT"
        )

    @pytest.mark.parametrize("error", _carriers(402, IN_FLIGHT))
    def test_in_flight_insufficient_is_a_transient_retry(self, error):
        assert "2 seconds" in get_specific_error_message(error, managed_context=True)
        # Transient: no usage banner, never an add-credits or upgrade nag.
        assert get_usage_error_code(error, managed_context=True) is None

    def test_in_flight_without_the_retry_hint_is_not_retried(self):
        body = _body(
            "managed_credits_insufficient",
            limit_source="agentcc_in_flight",
            remedy_hint="add_credits",
        )
        error = _ProviderStatusError(402, body)
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_CREDITS_INSUFFICIENT"
        )
        assert get_usage_error_code(error, managed_context=True) == "managed_credits_insufficient"

    @pytest.mark.parametrize(
        "body, key",
        [
            (BUDGET, "MANAGED_BUDGET_EXCEEDED"),
            (ORG_BUDGET, "MANAGED_ORG_BUDGET_EXCEEDED"),
        ],
    )
    def test_budget_429s_are_budget_messages_not_rate_limits(self, body, key):
        for error in _carriers(429, body):
            assert get_specific_error_message(error, managed_context=True) == get_error_message(key)
            assert get_specific_error_message(error, managed_context=True) != RATE_LIMIT_MESSAGE
            assert get_usage_error_code(error, managed_context=True) == body["error"]["code"]
            assert get_usage_error_code(error, managed_context=True) != "RATE_LIMITED"

    @pytest.mark.parametrize("error", _carriers(402, AGENT_BUDGET))
    def test_agent_budget_exhausted(self, error):
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_AGENT_BUDGET_EXHAUSTED"
        )
        assert get_usage_error_code(error, managed_context=True) == "agent_budget_exhausted"

    @pytest.mark.parametrize("error", _carriers(429, VELOCITY))
    def test_spend_velocity_asks_to_slow_down(self, error):
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_SPEND_VELOCITY"
        )
        assert get_specific_error_message(error, managed_context=True) != RATE_LIMIT_MESSAGE
        assert get_usage_error_code(error, managed_context=True) == "managed_spend_velocity"

    @pytest.mark.parametrize("error", _carriers(503, BILLING_DOWN))
    def test_billing_unavailable(self, error):
        assert "2 seconds" in get_specific_error_message(error, managed_context=True)
        assert "Billing" in get_specific_error_message(error, managed_context=True)
        assert get_usage_error_code(error, managed_context=True) == "managed_billing_unavailable"

    @pytest.mark.parametrize(
        "body", [EXHAUSTED, INSUFFICIENT, IN_FLIGHT, BUDGET, ORG_BUDGET, VELOCITY]
    )
    def test_wrapped_provider_errors_keep_their_code(self, body):
        error = _wrapped(_ProviderStatusError(402, body))
        assert get_usage_error_code(error, managed_context=True) != "FREE_TIER_LIMIT"
        assert get_specific_error_message(error, managed_context=True) not in (
            RATE_LIMIT_MESSAGE,
            NEUTRAL_INSUFFICIENT_MESSAGE,
        )

    @pytest.mark.parametrize(
        "body", [EXHAUSTED, INSUFFICIENT, IN_FLIGHT, AGENT_BUDGET, BUDGET, ORG_BUDGET]
    )
    def test_managed_credit_codes_are_never_free_tier_limit(self, body):
        for error in _carriers(402, body):
            assert get_usage_error_code(error, managed_context=True) != "FREE_TIER_LIMIT"

    def test_managed_code_wins_over_the_llm_passthrough(self):
        error = _ProviderStatusError(402, EXHAUSTED)
        assert get_specific_error_message(error, is_llm_error=True, managed_context=True) == (
            get_error_message("MANAGED_CREDITS_EXHAUSTED")
        )

    def test_managed_messages_do_not_trip_the_generic_rules_again(self):
        # A message that is normalised a second time keeps its meaning.
        for key in (
            "MANAGED_CREDITS_EXHAUSTED",
            "MANAGED_CREDITS_INSUFFICIENT",
            "MANAGED_CREDITS_IN_FLIGHT",
            "MANAGED_AGENT_BUDGET_EXHAUSTED",
            "MANAGED_BUDGET_EXCEEDED",
            "MANAGED_ORG_BUDGET_EXCEEDED",
            "MANAGED_SPEND_VELOCITY",
            "MANAGED_BILLING_UNAVAILABLE",
        ):
            text = get_error_message(key).lower()
            for token in ("insufficient", "rate limit", "rate_limited", "429"):
                assert token not in text, (key, token)
            assert get_usage_error_code(Exception(get_error_message(key))) is None

    def test_typed_permission_denied_is_the_managed_model_message(self):
        error = _TypedRefusal("permission_denied")
        assert get_specific_error_message(error, managed_context=True) == get_error_message(
            "MANAGED_PERMISSION_DENIED"
        )
        assert get_usage_error_code(error, managed_context=True) == "permission_denied"


class TestApiStatusMapper:
    @pytest.mark.parametrize(
        "status, key",
        [
            ("managed_credits_exhausted", "MANAGED_CREDITS_EXHAUSTED"),
            ("managed_credits_insufficient", "MANAGED_CREDITS_INSUFFICIENT"),
            ("agent_budget_exhausted", "MANAGED_AGENT_BUDGET_EXHAUSTED"),
            ("budget_exceeded", "MANAGED_BUDGET_EXCEEDED"),
            ("org_budget_exceeded", "MANAGED_ORG_BUDGET_EXCEEDED"),
            ("managed_spend_velocity", "MANAGED_SPEND_VELOCITY"),
            ("managed_billing_unavailable", "MANAGED_BILLING_UNAVAILABLE"),
        ],
    )
    def test_managed_status_values_map_to_their_messages(self, status, key):
        assert get_error_for_api_status(status) == get_error_message(key)

    def test_legacy_statuses_are_unchanged(self):
        assert get_error_for_api_status("rate_limited") == RATE_LIMIT_MESSAGE
        assert (
            get_error_for_api_status("insufficient_credits")
            == NEUTRAL_INSUFFICIENT_MESSAGE
        )
        assert get_error_for_api_status("other") == "API call not allowed: other"


class TestNonManagedStringsKeepTheirBehaviour:
    @pytest.mark.parametrize(
        "error, message, code",
        [
            (Exception("rate_limited"), RATE_LIMIT_MESSAGE, "RATE_LIMITED"),
            (
                Exception("Request failed with status 429"),
                RATE_LIMIT_MESSAGE,
                "RATE_LIMITED",
            ),
            (
                Exception("Insufficient credits. Please recharge your account."),
                NEUTRAL_INSUFFICIENT_MESSAGE,
                "FREE_TIER_LIMIT",
            ),
            (
                ValueError("Upstream call failed", "rate_limited"),
                RATE_LIMIT_MESSAGE,
                "RATE_LIMITED",
            ),
            (
                Exception("insufficient input data"),
                NEUTRAL_INSUFFICIENT_MESSAGE,
                None,
            ),
            (
                Exception("Other failure"),
                get_error_message("FAILED_TO_PROCESS_EVALUATION"),
                None,
            ),
        ],
    )
    def test_existing_classification(self, error, message, code):
        assert get_specific_error_message(error) == message
        assert get_usage_error_code(error) == code

    @pytest.mark.parametrize(
        "text",
        [
            # A bare mention of a code word is not an error.code field.
            "The budget_exceeded flag was cleared before the 429 retry",
            "see managed_credits_exhausted in the runbook; insufficient credits",
            # An ordinary provider rate limit keeps the rate-limit mapping.
            "Error code: 429 - {'error': {'type': 'requests', "
            "'code': 'rate_limit_exceeded'}}",
        ],
    )
    def test_code_words_outside_the_code_field_do_not_classify(self, text):
        error = Exception(text)
        assert get_specific_error_message(error) == RATE_LIMIT_MESSAGE or (
            get_specific_error_message(error) == NEUTRAL_INSUFFICIENT_MESSAGE
        )
        assert get_usage_error_code(error) in ("RATE_LIMITED", "FREE_TIER_LIMIT")

    def test_llm_passthrough_is_unchanged_for_other_errors(self):
        assert (
            get_specific_error_message(Exception("Provider refusal"), is_llm_error=True)
            == "Provider refusal"
        )


def test_compose_refusal_amounts_are_bounded():
    from tfc.utils.error_codes import _managed_usd
    assert _managed_usd("1E1000000") is None
    assert _managed_usd("1E999999") is None
    assert _managed_usd("NaN") is None
    assert _managed_usd("0.123456") == "0.123456"


def test_compose_r2_amount_exponent_is_checked_before_arithmetic():
    # OKCOMP-COR-02: abs() of 1E1000000 overflows under the default context;
    # the exponent bound comes first, so these never reach it.
    from decimal import Decimal

    from tfc.utils.error_codes import _managed_usd

    assert _managed_usd("-1E1000000") is None
    assert _managed_usd("1E-1000000") is None
    assert _managed_usd(Decimal("1E1000000")) is None
    assert _managed_usd("1E13") is None
    assert _managed_usd("1E9") is None
    assert _managed_usd("999999999") == "999999999"
    message = get_specific_error_message(
        _ProviderStatusError(
            402,
            _body(
                "managed_credits_insufficient",
                limit_source="agentcc_credits",
                required_usd="1E1000000",
                available_usd="0.01",
            )
        )
    , managed_context=True)
    assert message == get_error_message("MANAGED_CREDITS_INSUFFICIENT")


class _ChargedRefusal(_TypedRefusal):
    # A managed refusal raised after its call was sent and charged; the
    # credit meter and the managed transport set ``dispatched``.
    code = "managed_billing_unavailable"
    dispatched = True


class _UnsentRefusal(_TypedRefusal):
    code = "managed_billing_unavailable"


def test_compose_r2_charged_billing_failure_is_not_told_it_was_not_run():
    # Only a refusal before dispatch may say "not run".
    after = get_error_message("MANAGED_BILLING_UNAVAILABLE_AFTER_DISPATCH")
    assert "not run" not in after
    try:
        raise ValueError("judge failed") from _ChargedRefusal("managed_billing_unavailable")
    except ValueError as wrapped:
        assert get_specific_error_message(wrapped) == after
        assert get_specific_error_message(wrapped, is_llm_error=True) == after
        assert get_usage_error_code(wrapped) == "managed_billing_unavailable"
    unsent = _UnsentRefusal("managed_billing_unavailable")
    assert get_specific_error_message(unsent) == unsent.message


@pytest.mark.parametrize("code", ["managed_credits_exhausted", "managed_spend_velocity", "permission_denied"])
def test_r9_g06_text_is_not_a_managed_envelope(code):
    from tfc.utils.error_codes import _managed_fields
    assert _managed_fields(Exception("{'code': '" + code + "'}")) is None
    assert _managed_fields(ValueError("upstream", json.dumps(_body(code)))) is None


@pytest.mark.parametrize("status,code", [(500, "managed_credits_exhausted"), (402, "permission_denied"), (429, "unknown")])
def test_r9_g06_display_mismatch_never_uses_status_or_prose(status, code):
    raw = _ProviderStatusError(status, _body(code))
    assert get_specific_error_message(raw, True, managed_context=True) == get_error_message("MANAGED_REFUSAL_UNKNOWN")
    assert get_usage_error_code(raw, managed_context=True) is None


@pytest.mark.parametrize("status,code,usage", [(401, "invalid_api_key", None), (429, "rate_limit_exceeded", "RATE_LIMITED"), (429, "provider_private_code", "RATE_LIMITED")])
def test_r10_g06_managed_byok_twins(status, code, usage):
    raw = _ProviderStatusError(status, _body(code))
    # The same SDK shape is not evidence of gateway origin.
    assert get_specific_error_message(raw, True) == str(raw)
    assert get_usage_error_code(raw) == usage
    managed = get_specific_error_message(raw, True, managed_context=True)
    if code == "provider_private_code":
        assert managed == get_error_message("MANAGED_REFUSAL_UNKNOWN")
        assert get_usage_error_code(raw, managed_context=True) is None
    else:
        assert managed != str(raw)
        assert "gateway API key" in managed if status == 401 else "2 seconds" in managed
        assert get_usage_error_code(raw, managed_context=True) == code


@pytest.mark.parametrize("inner_kind", ["sdk", "local", "charged", "pending"])
@pytest.mark.parametrize("outer_status,outer_code", [(500, "unknown_refusal"), (500, "managed_credits_exhausted"), (402, None)])
def test_r10_g06_first_envelope_wins(inner_kind, outer_status, outer_code):
    from model_hub.services import credit_ledger_port as cl
    inner = {"sdk": lambda: _ProviderStatusError(402, EXHAUSTED),
             "local": lambda: cl.CreditsExhaustedError(),
             "charged": lambda: cl.CreditsExhaustedError().after_dispatch(charged=True),
             "pending": cl.ManagedOutcomePendingReconcileError}[inner_kind]()
    outer = _ProviderStatusError(outer_status, _body(outer_code))
    outer.__cause__ = inner
    wrapped = _wrapped(outer)
    assert get_specific_error_message(wrapped, True, managed_context=True) == get_error_message("MANAGED_REFUSAL_UNKNOWN")
    assert get_usage_error_code(wrapped, managed_context=True) is None
    # Without managed context, even a typed inner error cannot re-label a BYOK envelope.
    assert get_specific_error_message(outer, True) == str(outer)


@pytest.mark.parametrize("name", ["ManagedDispatchUnavailableError", "CreditHoldMissingError", "ManagedRouteUnavailableError"])
def test_r10_g06_local_remedies(name):
    from model_hub.services import credit_ledger_port as cl
    error = getattr(cl, name)()
    for carrier in (error, _wrapped(error)):
        assert get_specific_error_message(carrier, True) == error.message
        assert get_usage_error_code(carrier) is None
    assert error.message != get_error_message("MANAGED_REFUSAL_UNKNOWN")


def test_r10_g06_marker_is_not_context():
    raw = _ProviderStatusError(401, _body("invalid_api_key"))
    raw.is_managed_refusal = True
    raw.contract_valid = True
    raw.message = "not trusted as local copy"
    raw.dispatched = True
    assert get_specific_error_message(raw, True) == str(raw)
    assert get_usage_error_code(raw) is None


def test_r10_g11_pending_outer_boundary():
    from model_hub.services import credit_ledger_port as cl
    pending = cl.ManagedOutcomePendingReconcileError()
    pending.__cause__ = _ProviderStatusError(503, BILLING_DOWN)
    assert get_specific_error_message(_wrapped(pending), True) == pending.message
    assert get_usage_error_code(_wrapped(pending)) is None


# OK-PARITY-r11: literal expectations from contract-r3 d990a957 / parity-r2.
# No product-table-derived oracle; source-only writer did not execute these.
R11_CONTRACT_ROWS = [(402, 'managed_credits_exhausted', 'payment_required', 'out_of_credits'),
 (402, 'managed_credits_insufficient', 'payment_required', 'out_of_credits'),
 (402, 'agent_budget_exhausted', 'payment_required', 'agent_budget'),
 (429, 'budget_exceeded', 'rate_limit_error', 'budget_limit'),
 (429, 'org_budget_exceeded', 'rate_limit_error', 'budget_limit'),
 (429, 'managed_spend_velocity', 'rate_limit_error', 'rate_limit'),
 (429, 'rate_limit_exceeded', 'rate_limit_error', 'rate_limit'),
 (503, 'managed_billing_unavailable', 'server_error', 'billing_unavailable'),
 (403, 'permission_denied', 'permission_error', 'permission'),
 (403, 'future_agi_credits_off', 'permission_error', 'permission'),
 (401, 'invalid_api_key', 'authentication_error', 'invalid_key'),
 (402, 'agent_call_cap_exceeded', 'payment_required', 'agent_budget'),
 (402, 'call_cap_unenforceable', 'payment_required', 'agent_budget'),
 (402, 'agent_run_stopped', 'payment_required', 'agent_budget'),
 (503, 'budget_state_unavailable', 'server_error', 'billing_unavailable'),
 (402, 'managed_spend_paused', 'payment_required', 'budget_limit'),
 (402, 'payment_required', 'insufficient_credits', 'out_of_credits'),
 (429, 'budget_throttled', 'rate_limit_error', 'rate_limit'),
 (503, 'key_list_not_loaded', 'server_error', 'generic'),
 (403, 'model_not_permitted', 'permission_error', 'permission'),
 (403, 'managed_router_unbounded', 'permission_error', 'permission'),
 (400, 'unsupported_field_for_managed', 'invalid_request_error', 'generic'),
 (400, 'unsupported_routing_field', 'invalid_request_error', 'generic'),
 (403, 'key_scope_denied', 'permission_error', 'permission'),
 (402, 'agent_key_budget_exhausted', 'payment_required', 'agent_budget'),
 (429, 'token_rate_limit_exceeded', 'rate_limit_error', 'rate_limit'),
 (429, 'usage_paused', 'rate_limit_error', 'budget_limit'),
 (429, 'quota_exceeded', 'rate_limit_error', 'budget_limit'),
 (400, 'usage_unavailable', 'invalid_request_error', 'generic'),
 (503, 'provider_busy', 'server_error', 'generic'),
 (409, 'idempotency_in_progress', 'invalid_request_error', 'generic'),
 (422, 'idempotency_key_reused', 'invalid_request_error', 'generic'),
 (404, 'managed_models_disabled', 'not_found', 'generic'),
 (400, 'deferred_run_unavailable', 'invalid_request_error', 'generic'),
 (404, 'model_not_found', 'not_found', 'generic'),
 (504, 'gateway_timeout', 'gateway_timeout', 'generic'),
 (410, 'model_expired', 'not_found', 'generic'),
 (404, 'no_provider_matches', 'not_found', 'generic'),
 (400, 'provider_preference_unavailable', 'invalid_request_error', 'generic'),
 (400, 'model_ambiguous', 'invalid_request_error', 'generic'),
 (400, 'no_auto_candidate', 'invalid_request_error', 'generic'),
 (400, 'omega_default_unroutable', 'invalid_request_error', 'generic'),
 (400, 'route_hint_unsupported', 'invalid_request_error', 'generic'),
 (400, 'routing_field_not_yet_supported', 'invalid_request_error', 'generic'),
 (400, 'invalid_provider_preference', 'invalid_request_error', 'generic'),
 (400, 'invalid_routing_field', 'invalid_request_error', 'generic'),
 (400, 'extra_body_conflict', 'invalid_request_error', 'generic'),
 (400, 'invalid_extra_body', 'invalid_request_error', 'generic'),
 (404, 'provider_not_found', 'not_found', 'generic')]


def _r11_raw(status, code, error_type="server_error", metadata=None, wait=None, param=None):
    raw = _ProviderStatusError(status, _body(code, error_type, **(metadata or {})))
    raw.body["param"] = param
    raw.response.headers = {} if wait is None else {"Retry-After": wait}
    return raw


def _r11_meter_error(raw, *, async_mode=False, outcome="released_refused", normalize=False):
    """Real meter/dispatch_refusal; only the hold and settlement I/O is a stub.

    The r8 suites separately cover ledger decisions. This control asserts the
    display reaches both existing terminal raise points without changing them.
    """
    import asyncio
    from model_hub.services import credit_ledger_port as cl, model_route as mr

    if normalize:
        raw = mr.managed_call_error(raw)
    meter = cl.CallMeter(organization=None, workspace=None,
                         route=cl.PayerRoute.managed(), envelope=None)
    call = SimpleNamespace(dispatched=outcome != "held", status=outcome)
    session = cl._HoldSession(meter, messages=None, max_tokens=None,
                              amount_estimate=None, dispatch_id=None)
    session.call, session.ledger, session.org_id = call, object(), "r11-display-org"
    session.open = lambda: call
    session.fail = lambda: None
    session.refused_before_upstream = lambda: outcome == "released_refused"
    session.release_refused = lambda: None
    meter._session = lambda *args: session
    async def run():
        async with meter.ametered():
            raise raw
    cl.reset_meter_safety()
    try:
        with pytest.raises(Exception) as caught:
            if async_mode:
                asyncio.run(run())
            else:
                with meter.metered():
                    raise raw
        return caught.value
    finally:
        cl.reset_meter_safety()


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("normalize", [False, True])
@pytest.mark.parametrize("status,code,error_type,category", R11_CONTRACT_ROWS)
def test_r11_g01_all_rows_survive_real_meter_display(status, code, error_type, category, async_mode, normalize):
    from model_hub.services import credit_ledger_port as cl, model_route as mr
    raw = _r11_raw(status, code, error_type)
    refusal = _r11_meter_error(raw, async_mode=async_mode, normalize=normalize)
    assert isinstance(refusal, cl.ManagedCreditRefusal)
    assert refusal.retryable is False and refusal.retry_after is None
    # Meter's exception remains r8; canonical presentation is additive data.
    assert refusal.gateway_display["code"] == code
    routed = mr.managed_route_failure(refusal)
    body = routed.response_body()
    assert isinstance(routed, ValueError)
    assert (body["status"], body["code"], body["type"], body["category"]) == (status, code, error_type, category)
    assert body["retryable"] is False and body["retry_after"] is None
    assert get_specific_error_message(refusal) == body["message"]
    assert get_usage_error_code(refusal) == code
    assert "gateway text" not in repr(body)


@pytest.mark.parametrize("wait,expected", [(None, None), ("0", None), ("-2", None), ("NaN", None), (True, None), ("2", 2), ("600", 600), ("0.25", .25)])
@pytest.mark.parametrize("in_flight", [False, True])
def test_r11_g02_g09_validated_wait_survives_terminalization(wait, expected, in_flight):
    from model_hub.services import model_route as mr
    meta = {"limit_source": "agentcc_in_flight" if in_flight else "agentcc_credits",
            "remedy_hint": "retry" if in_flight else "add_credits"}
    raw = _r11_raw(402, "managed_credits_insufficient", "payment_required", meta, wait)
    routed = mr.managed_route_failure(_r11_meter_error(raw, normalize=True))
    body = routed.response_body()
    expected = expected if in_flight else None
    assert body["display_wait_seconds"] == expected
    assert body["retryable"] is False and body["retry_after"] is None
    if expected is not None:
        assert f"{expected} seconds" in body["message"]
        assert get_usage_error_code(routed) is None
    else:
        assert "Wait at least" not in body["message"]
        assert "lower max_tokens" in body["message"]


@pytest.mark.parametrize("reason,needle", [(None, "spending window is full"), ("request_exceeds_minute_cap", "Lower max_tokens"), ("request_exceeds_hour_cap", "Lower max_tokens"), ("new_org_daily_cap", "Contact support"), ("unknown_reason", "administrator")])
def test_r11_g03_spend_variants_keep_reason(reason, needle):
    from model_hub.services import model_route as mr
    meta = {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry"}
    if reason is not None:
        meta["reason"] = reason
    raw = _r11_raw(429, "managed_spend_velocity", "rate_limit_error", meta, "600")
    routed = mr.managed_route_failure(_r11_meter_error(raw, normalize=True))
    body = routed.response_body()
    assert needle in body["message"]
    assert body["metadata"].get("reason") == reason
    assert body["display_wait_seconds"] == (600 if reason is None else None)
    assert body["retryable"] is False


@pytest.mark.parametrize("normalize", [False, True])
def test_r11_g04_permission_is_access_not_pricing(normalize):
    from model_hub.services import model_route as mr
    routed = mr.managed_route_failure(_r11_meter_error(
        _r11_raw(403, "permission_denied", "permission_error"), normalize=normalize))
    assert routed.response_body()["category"] == "permission"
    assert "access policy" in routed.message
    assert "not available with Future AGI credits" not in routed.message


@pytest.mark.parametrize("status,code", [(500, "managed_credits_exhausted"), (403, "org_budget_exceeded"), (429, "permission_denied")])
@pytest.mark.parametrize("marked", [False, True])
def test_r11_g06_local_gateway_tuple_mismatch_is_generic(status, code, marked):
    from model_hub.services import model_route as mr
    refusal = mr.ManagedRouteRefused(code, http_status=status, retryable=True, retry_after=2)
    if marked:
        refusal.contract_valid = True  # marker alone never overrides tuple validation
    body = refusal.response_body()
    assert body["code"] == "MANAGED_ROUTE_CALL_FAILED"
    assert body["category"] == "generic"
    assert body["retryable"] is False and body["retry_after"] is None
    assert get_specific_error_message(refusal) == get_error_message("MANAGED_REFUSAL_UNKNOWN")
    assert get_usage_error_code(refusal) is None
    assert refusal.code == code and refusal.retryable is True  # display did not mutate control


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("code", ["MANAGED_GATEWAY_UNAVAILABLE", "NO_ENTERPRISE_LICENSE", "MANAGED_ROUTE_UNAVAILABLE"])
def test_r11_g06_c01_local_denials_survive_r8_meter(code, async_mode):
    from model_hub.services import model_route as mr
    denial = mr.ManagedRouteDenied(code, retryable=True)
    raised = _r11_meter_error(denial, async_mode=async_mode, outcome="held")
    assert raised is denial
    routed = mr.managed_route_failure(raised)
    assert type(routed) is mr.ManagedRouteDenied
    assert (routed.code, routed.message, routed.http_status, routed.retryable) == (code, denial.message, 403, True)
    assert routed.response_body()["code"] == code
    assert get_specific_error_message(routed) == denial.message


@pytest.mark.parametrize("error", [ValueError("Invalid context format"), TimeoutError("local deadline"), Exception("rate_limited")])
def test_r11_g06_c04_no_envelope_keeps_generic_rules(error):
    assert get_specific_error_message(error, managed_context=True) == get_specific_error_message(error)
    assert get_usage_error_code(error, managed_context=True) == get_usage_error_code(error)


@pytest.mark.parametrize("status,code", [(500, "managed_credits_exhausted"), (402, None), (429, "unknown")])
def test_r11_g06_raw_mismatch_cannot_inherit_meter_code(status, code):
    from model_hub.services import model_route as mr
    refusal = _r11_meter_error(_r11_raw(status, code))
    routed = mr.managed_route_failure(refusal)
    assert routed.code == "MANAGED_ROUTE_CALL_FAILED"
    assert routed.retryable is False and isinstance(routed, ValueError)
    assert get_usage_error_code(refusal) is None
    assert get_specific_error_message(refusal) == get_error_message("MANAGED_REFUSAL_UNKNOWN")


@pytest.mark.parametrize("name", ["SpendVelocityError", "CreditsInFlightError", "ManagedRateLimitError"])
def test_r11_g09_c06_local_class_default_is_not_gateway_wait(name):
    from model_hub.services import credit_ledger_port as cl
    error = getattr(cl, name)()
    before = (error.retry_after, error.retryable, error.backoff)
    assert "Wait at least" not in get_specific_error_message(error)
    assert (error.retry_after, error.retryable, error.backoff) == before


@pytest.mark.parametrize("status,code,error_type,metadata,param", [
    (402, "call_cap_unenforceable", "invalid_request_error", {"cap_scope": "key", "cap_ref": "cap-1", "cap_kind": "run", "policy_revision": 3}, None),
    (503, "budget_state_unavailable", "invalid_request_error", {"cap_scope": "agent", "cap_ref": "cap-2", "cap_kind": "daily", "policy_revision": 4}, None),
    (404, "model_not_found", "not_found", {"suggestions": ["provider/model"]}, None),
    (400, "invalid_routing_field", "invalid_request_error", {}, "routing.models"),
])
def test_r11_g10_checked_metadata_param_and_variant_type_survive(status, code, error_type, metadata, param):
    from model_hub.services import model_route as mr
    raw = _r11_raw(status, code, error_type, dict(metadata, upstream_message="DO_NOT_FORWARD"), param=param)
    body = mr.managed_route_failure(_r11_meter_error(raw, normalize=True)).response_body()
    assert body["metadata"] == metadata
    assert body["param"] == param
    assert body["type"] == error_type
    assert "DO_NOT_FORWARD" not in repr(body)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("outcome", ["pending_reconcile", "settle_failed", "release_failed"])
@pytest.mark.parametrize("reason,needle", [("redis_unavailable", "billing store"), ("account_state_missing", "being restored")])
def test_r11_g11_original_billing_identity_is_separate_from_outcome(async_mode, outcome, reason, needle):
    from model_hub.services import credit_ledger_port as cl, model_route as mr
    raw = _r11_raw(503, "managed_billing_unavailable", "server_error", {"reason": reason, "remedy_hint": "retry"}, "600")
    refusal = _r11_meter_error(raw, outcome=outcome, normalize=True, async_mode=async_mode)
    cls = cl.ManagedOutcomePendingReconcileError if outcome == "pending_reconcile" else cl.CreditSettlementError
    assert type(refusal) is cls
    routed = mr.managed_route_failure(refusal)
    body = routed.response_body()
    assert body["code"] == cls.code
    assert body["message"] == refusal.message
    assert get_usage_error_code(routed) is None
    original = body["original_refusal"]
    assert original["code"] == "managed_billing_unavailable"
    assert needle in original["message"]
    assert original["metadata"]["reason"] == reason
    assert original["display_wait_seconds"] == 600
    assert original["retryable"] is False and original["retry_after"] is None
    # Standalone charged-failure display must not call a nonexistent r9 ledger helper.
    final = mr._charged_failure(routed, [SimpleNamespace(dispatched=True, status=outcome)])
    assert final.response_body()["original_refusal"] == original


def test_r11_g11_display_enrichment_cannot_replace_terminal_outcome():
    from model_hub.services import credit_ledger_port as cl
    class BrokenEnvelope(Exception):
        @property
        def response(self):
            raise RuntimeError("unreadable display envelope")
    refusal = _r11_meter_error(BrokenEnvelope(), outcome="pending_reconcile")
    assert type(refusal) is cl.ManagedOutcomePendingReconcileError
    assert isinstance(refusal, ValueError) and refusal.retryable is False


@pytest.mark.parametrize("shape", ["object", "raised_exception", "returned_exception", "body_no_status"])
@pytest.mark.parametrize("scope", ["org", "run"])
def test_r11_money01_c02_r8_hold_shapes_keep_org_stop(shape, scope):
    from model_hub.services import credit_ledger_port as cl
    meta = {"scope": scope}
    item = SimpleNamespace(code="budget_exceeded", metadata=meta)
    if shape != "object":
        item = RuntimeError("local ledger refusal")
        if shape == "body_no_status":
            item.body = {"code": "budget_exceeded", "metadata": meta}
        else:
            item.code, item.metadata = "budget_exceeded", meta
    if shape == "raised_exception":
        try:
            raise item
        except RuntimeError as error:
            refusal = cl.managed_refusal_from(error)
    else:
        with pytest.raises(cl.BudgetExceededError) as caught:
            cl._coerce_hold_id(item)
        refusal = caught.value
    assert type(refusal) is cl.BudgetExceededError
    safety, port = cl._MeterSafety(), object()
    safety.note_refusal(port, "org", refusal)
    if scope == "org":
        with pytest.raises(cl.BudgetExceededError):
            safety.check(port, "org")
    else:
        safety.check(port, "org")


@pytest.mark.parametrize("code,metadata,expected", [
    ("managed_credits_insufficient", {"limit_source": "agentcc_in_flight", "remedy_hint": "retry"}, "CreditsInFlightError"),
    ("managed_billing_unavailable", {}, "CreditLedgerUnavailableError"),
    ("managed_spend_velocity", {}, "SpendVelocityError"),
    ("rate_limit_exceeded", {}, "ManagedRateLimitError"),
])
def test_r11_money02_r8_local_retry_classes_are_not_display_decisions(code, metadata, expected):
    from model_hub.services import credit_ledger_port as cl
    with pytest.raises(getattr(cl, expected)) as caught:
        cl._coerce_hold_id({"code": code, "metadata": metadata})
    before = (type(caught.value), caught.value.retryable, caught.value.retry_after)
    get_specific_error_message(caught.value)
    assert (type(caught.value), caught.value.retryable, caught.value.retry_after) == before
    assert caught.value.retryable is True


# OK-COMPOSE-r12: presentation controls exercise the actual outer serializers.
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,code,error_type,category", R11_CONTRACT_ROWS)
def test_r12_c01_outer_meter_and_cell_keep_canonical_display(
    status, code, error_type, category, async_mode
):
    from model_hub.services import credit_ledger_port as cl

    refusal = _r11_meter_error(_r11_raw(status, code, error_type), async_mode=async_mode)
    before = (type(refusal), refusal.code, refusal.http_status, refusal.retryable,
              refusal.retry_after, refusal.charged, refusal.dispatched, refusal.message)
    body = refusal.as_response_body()
    assert (body["status"], body["code"], body["type"], body["category"]) == (
        status, code, error_type, category
    )
    assert body["retryable"] is False and body["retry_after"] is None
    assert cl.refusal_cell_info(refusal) == {"reason": body["message"], "error": body}
    assert "gateway text" not in repr(body)
    if code == "invalid_api_key":
        assert body["message"] == get_error_message("MANAGED_INVALID_GATEWAY_KEY")
    assert (type(refusal), refusal.code, refusal.http_status, refusal.retryable,
            refusal.retry_after, refusal.charged, refusal.dispatched, refusal.message) == before


@pytest.mark.parametrize("outcome", ["pending_reconcile", "settle_failed", "release_failed", "settled"])
def test_r12_c01_primary_outcome_and_charge_disclosure_survive_serialization(outcome):
    from model_hub.services import credit_ledger_port as cl

    raw = _r11_raw(503, "managed_billing_unavailable", metadata={"reason": "redis_unavailable"}, wait="600")
    refusal = _r11_meter_error(raw, outcome=outcome)
    body = refusal.as_response_body()
    assert body["message"] == refusal.message
    assert body["code"] == refusal.code
    assert body["retryable"] is False and body["retry_after"] is None
    assert cl.refusal_cell_info(refusal)["reason"] == refusal.message
    if outcome != "settled":
        assert body["original_refusal"]["code"] == "managed_billing_unavailable"
        assert body["original_refusal"]["metadata"] == {"reason": "redis_unavailable"}
        assert body["original_refusal"]["display_wait_seconds"] == 600
        assert body["original_refusal"]["retryable"] is False
        assert body["original_refusal"]["retry_after"] is None
    else:
        assert "credits were charged" in body["message"]


def test_r12_c01_outer_serializer_preserves_checked_metadata_and_wait():
    from model_hub.services import credit_ledger_port as cl

    refusal = _r11_meter_error(_r11_raw(
        429, "managed_spend_velocity", "rate_limit_error",
        {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry", "private": "DO_NOT_FORWARD"}, "600"
    ))
    body = refusal.as_response_body()
    assert body["metadata"] == {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry"}
    assert body["display_wait_seconds"] == 600
    assert "Wait at least 600 seconds" in body["message"]
    assert body["retryable"] is False and body["retry_after"] is None
    assert cl.refusal_cell_info(refusal)["reason"] == body["message"]
    assert "DO_NOT_FORWARD" not in repr(body)


def _r12_hold_refusal(raw, shape, *, async_mode=False):
    import asyncio
    from model_hub.services import credit_ledger_port as cl

    class HoldLedger(cl.MockCreditLedger):
        attempts = 0

        def hold(self, **kwargs):
            self.attempts += 1
            if shape == "raised":
                raise raw
            if shape == "dictionary":
                return {"status": raw.status_code, "error": raw.body}
            return raw

    org = SimpleNamespace(id="r12-display-org")
    ledger = HoldLedger(default_balance="1")
    envelope = cl.build_envelope(organization=org, workspace=None,
        purpose=cl.PURPOSE_RUN_PROMPT_PREVIEW, source="r12-display", source_id="hold", model="gpt-4o")
    meter = cl.CallMeter(organization=org, workspace=None, route=cl.PayerRoute.managed(), envelope=envelope)
    async def run():
        async with meter.ametered(amount_estimate="0.1"):
            pytest.fail("a refused hold dispatched")
    cl.reset_meter_safety()
    try:
        with cl.use_credit_ledger(ledger):
            with pytest.raises(cl.ManagedCallRefusal) as caught:
                if async_mode:
                    asyncio.run(run())
                else:
                    with meter.metered(amount_estimate="0.1"):
                        pytest.fail("a refused hold dispatched")
            refusal = caught.value
            assert ledger.attempts == 1 and ledger.holds == {} and ledger.calls == []
            if refusal.code == "managed_credits_exhausted":
                with pytest.raises(cl.CreditsExhaustedError):
                    with meter.metered(amount_estimate="0.1"):
                        pytest.fail("organization stop was lost")
                assert ledger.attempts == 1
            return refusal
    finally:
        cl.reset_meter_safety()


@pytest.mark.parametrize("shape", ["raised", "returned", "dictionary"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_r12_c02_hold_mismatch_is_display_only_and_keeps_org_stop(shape, async_mode):
    from model_hub.services import credit_ledger_port as cl, model_route as mr

    refusal = _r12_hold_refusal(_r11_raw(500, "managed_credits_exhausted"), shape, async_mode=async_mode)
    assert type(refusal) is cl.CreditsExhaustedError
    assert (refusal.code, refusal.http_status, refusal.retryable) == ("managed_credits_exhausted", 402, False)
    assert refusal.gateway_display["status"] == 500
    for body in (refusal.as_response_body(), mr.managed_route_failure(refusal).response_body()):
        assert (body["status"], body["code"], body["category"]) == (500, "MANAGED_ROUTE_CALL_FAILED", "generic")
        assert body["message"] == get_error_message("MANAGED_REFUSAL_UNKNOWN")
        assert body["retryable"] is False and body["retry_after"] is None


@pytest.mark.parametrize("shape", ["raised", "returned"])
@pytest.mark.parametrize("wait,expected", [(None, None), ("0", None), ("NaN", None), ("600", 600)])
def test_r12_c02_hold_wait_metadata_are_checked_without_mutating_retry(shape, wait, expected):
    from model_hub.services import credit_ledger_port as cl

    raw = _r11_raw(429, "managed_spend_velocity", "rate_limit_error", {
        "limit_source": "agentcc_spend_guard", "remedy_hint": "retry", "private": "DO_NOT_FORWARD"
    }, wait)
    refusal = _r12_hold_refusal(raw, shape)
    assert type(refusal) is cl.SpendVelocityError and refusal.retryable is True
    before = (refusal.retryable, refusal.retry_after, refusal.backoff, dict(refusal.metadata))
    body = refusal.as_response_body()
    assert body["display_wait_seconds"] == expected
    assert body["retryable"] is (expected is not None)
    assert body["retry_after"] == expected
    assert body["metadata"] == {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry"}
    assert "DO_NOT_FORWARD" not in repr(body)
    assert (refusal.retryable, refusal.retry_after, refusal.backoff, dict(refusal.metadata)) == before


def test_r12_c02_unreadable_display_cannot_replace_hold_refusal():
    from model_hub.services import credit_ledger_port as cl

    class BrokenSdk(RuntimeError):
        status_code = 500
        code = "managed_credits_exhausted"
        body = {"code": code}

        @property
        def response(self):
            raise RuntimeError("display unavailable")

    # The classifier reads Retry-After too; leave its existing descriptor behavior
    # out of this display-only control and exercise the new attachment directly.
    refusal = cl.CreditsExhaustedError()
    assert cl._with_hold_display(refusal, BrokenSdk()) is refusal
    assert type(refusal) is cl.CreditsExhaustedError and refusal.retryable is False


@pytest.mark.parametrize("code", ["provider_private_code", None, ""])
@pytest.mark.parametrize("marked", [False, True])
def test_r12_c03_unknown_and_missing_gateway_carrier_codes_are_generic(code, marked):
    from model_hub.services import model_route as mr

    refusal = mr.ManagedRouteRefused(code, http_status=429, retryable=True, retry_after=2)
    refusal.code = code  # Include a genuinely missing tuple, not the class fallback.
    refusal.contract_valid = marked
    refusal.display_wait_seconds = 2
    body = refusal.response_body()
    assert body["code"] == "MANAGED_ROUTE_CALL_FAILED"
    assert body["category"] == "generic" and body["status"] == 429
    assert body["message"] == get_error_message("MANAGED_REFUSAL_UNKNOWN")
    assert body["retryable"] is False and body["retry_after"] is None
    assert body["display_wait_seconds"] is None
    assert get_usage_error_code(refusal) is None
    assert get_specific_error_message(refusal) == body["message"]
    assert refusal.code == code and refusal.retryable is True and refusal.retry_after == 2


@pytest.mark.parametrize("name", ["CreditHoldMissingError", "ManagedDispatchUnavailableError",
                                 "ManagedRouteUnavailableError", "ManagedOutcomePendingReconcileError",
                                 "CreditSettlementError", "ManagedCallAlreadyCompletedError"])
def test_r12_c04_direct_local_ledger_normalization_preserves_contract(name):
    from model_hub.services import credit_ledger_port as cl, model_route as mr

    original = getattr(cl, name)()
    normalized = mr.managed_call_error(original)
    assert isinstance(normalized, mr.ModelRouteError) and isinstance(normalized, ValueError)
    assert normalized is not original
    body = normalized.response_body()
    assert (body["code"], body["message"], body["status"]) == (original.code, original.message, original.http_status)
    assert body["retryable"] is False and body["retry_after"] is None
    assert normalized.__cause__ is None and normalized.__context__ is None


def test_r12_c04_nonterminal_local_refusal_is_not_terminalized_by_display():
    from model_hub.services import credit_ledger_port as cl, model_route as mr

    original = cl.ManagedCallDeferred("Local-only retry copy", retry_after=3)
    normalized = mr.managed_call_error(original)
    body = normalized.response_body()
    assert (body["code"], body["message"], body["status"]) == (original.code, original.message, original.http_status)
    assert normalized.retryable is True and normalized.retry_after == 3
    assert body["retryable"] is True and body["retry_after"] == 3

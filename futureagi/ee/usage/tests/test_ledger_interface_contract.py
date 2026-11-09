"""BL-LEDGER phase 2a: the service interface GW-WALLET and BL-STRIPE call.

These tests pin each relayed function's parameters (names, kinds, defaults),
the enum values callers pass, the usage-event-v1 section 3 id vectors and the
funding allocator's rules. Changing any of them is a contract change: relay it
to main before changing the test.
"""

import inspect
import uuid
from datetime import UTC, datetime

import pytest

from ee.usage.services import (
    balance_cache,
    funding,
    managed_credits,
    rating,
    reconcile,
    stripe_records,
    usage_ingest,
)
from ee.usage.services.funding import Allocation, Bucket, Buckets

P = inspect.Parameter
POS = P.POSITIONAL_OR_KEYWORD
KW = P.KEYWORD_ONLY
REQ = P.empty


def _params(func):
    return [
        (name, param.kind, param.default)
        for name, param in inspect.signature(func).parameters.items()
    ]


# (function, [(name, kind, default)]) for every function relayed to the lanes.
RELAYED = {
    # GW-WALLET
    "settle_spend": (
        managed_credits.settle_spend,
        [("record", POS, REQ), ("workspace_id", KW, None)],
    ),
    "refund": (
        managed_credits.refund,
        [
            ("org", POS, REQ),
            ("request_id", POS, REQ),
            ("micros", POS, None),
            ("reason", KW, REQ),
            ("actor", KW, REQ),
        ],
    ),
    "push_granted": (managed_credits.push_granted, [("org", POS, REQ)]),
    "ensure_account_pushed": (
        managed_credits.ensure_account_pushed,
        [("org", POS, REQ)],
    ),
    "pushed_value": (managed_credits.pushed_value, [("account", POS, REQ)]),
    "request_push": (managed_credits.request_push, [("org_id", POS, REQ)]),
    "sweep_unacked_pushes": (
        managed_credits.sweep_unacked_pushes,
        [("limit", KW, 500)],
    ),
    "balance": (managed_credits.balance, [("org", POS, REQ)]),
    "ledger_balance": (managed_credits.ledger_balance, [("org", POS, REQ)]),
    "check_gateway": (reconcile.check_gateway, [("org", POS, REQ)]),
    "admission_refusal": (
        balance_cache.admission_refusal,
        [("org_id", POS, REQ), ("event_type", POS, REQ)],
    ),
    # BL-STRIPE
    "fulfil_topup": (
        managed_credits.fulfil_topup,
        [("org", POS, REQ), ("payment", KW, REQ), ("stripe_event_id", KW, "")],
    ),
    "reverse_topup": (
        managed_credits.reverse_topup,
        [
            ("org", POS, REQ),
            ("refund_id", KW, REQ),
            ("payment_intent_id", KW, REQ),
            ("amount_micros", KW, REQ),
            ("mode", KW, REQ),
            ("stripe_event_id", KW, ""),
        ],
    ),
    "hold_dispute": (
        managed_credits.hold_dispute,
        [
            ("org", POS, REQ),
            ("dispute_id", KW, REQ),
            ("payment_intent_id", KW, REQ),
            ("amount_micros", KW, REQ),
            ("mode", KW, REQ),
            ("stripe_event_id", KW, ""),
        ],
    ),
    "release_dispute": (
        managed_credits.release_dispute,
        [
            ("org", POS, REQ),
            ("dispute_id", KW, REQ),
            ("mode", KW, REQ),
            ("stripe_event_id", KW, ""),
        ],
    ),
    "configure_auto_topup": (
        managed_credits.configure_auto_topup,
        [
            ("org", POS, REQ),
            ("enabled", KW, REQ),
            ("threshold_micros", KW, REQ),
            ("amount_micros", KW, REQ),
            ("daily_count_cap", KW, REQ),
            ("daily_amount_cap_micros", KW, REQ),
            ("payment_method_id", KW, ""),
            ("consent_text_version", KW, ""),
            ("actor", KW, None),
        ],
    ),
    "claim_auto_topup": (managed_credits.claim_auto_topup, [("org", POS, REQ)]),
    "finish_auto_topup": (
        managed_credits.finish_auto_topup,
        [("org", POS, REQ), ("seq", KW, REQ), ("outcome", KW, REQ), ("reason", KW, "")],
    ),
    "record_stripe_event": (
        stripe_records.record_stripe_event,
        [
            ("event_id", KW, REQ),
            ("event_type", KW, REQ),
            ("mode", KW, REQ),
            ("object_id", KW, ""),
            ("org", KW, None),
        ],
    ),
    "mark_stripe_event": (
        stripe_records.mark_stripe_event,
        [("event_id", POS, REQ), ("status", KW, REQ), ("error", KW, "")],
    ),
    "upsert_invoice_mirror": (
        stripe_records.upsert_invoice_mirror,
        [
            ("org", POS, REQ),
            ("mode", KW, REQ),
            ("stripe_invoice_id", KW, REQ),
            ("kind", KW, REQ),
            ("status", KW, REQ),
            ("period_start", KW, REQ),
            ("period_end", KW, REQ),
            ("subtotal_cents", KW, REQ),
            ("tax_cents", KW, REQ),
            ("total_cents", KW, REQ),
            ("amount_due_cents", KW, REQ),
            ("hosted_invoice_url", KW, ""),
            ("invoice_pdf_url", KW, ""),
            ("stripe_created_at", KW, REQ),
        ],
    ),
    # Producers (in-process ingest)
    "ingest_events": (
        usage_ingest.ingest_events,
        [
            ("items", POS, REQ),
            ("credential", KW, None),
            ("in_process_system", KW, None),
            ("received_at", KW, None),
        ],
    ),
}


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(RELAYED))
def test_relayed_signature_is_pinned(name):
    func, expected = RELAYED[name]
    assert _params(func) == expected


@pytest.mark.unit
def test_existing_writers_stay_call_compatible():
    # agentcc_managed_credits and every R caller keep working: new params are
    # keyword-only with defaults.
    grant = dict((n, (k, d)) for n, k, d in _params(managed_credits.grant))
    assert grant["idempotency_key"] == (KW, REQ)
    assert grant["bucket"] == (KW, Bucket.PROMO)
    assert grant["expires_at"] == (KW, None) and grant["category"] == (KW, "")
    adjust = dict((n, (k, d)) for n, k, d in _params(managed_credits.adjust))
    assert adjust["bucket"] == (KW, Bucket.PROMO)
    assert _params(managed_credits.apply_topup)[:2] == [
        ("org", POS, REQ),
        ("micros", POS, REQ),
    ]
    assert _params(managed_credits.apply_topup_pi)[:2] == [
        ("org", POS, REQ),
        ("micros", POS, REQ),
    ]


@pytest.mark.unit
def test_payment_value_types():
    assert [s.value for s in managed_credits.TopupSource] == [
        "checkout_session",
        "payment_intent",
        "invoice",
    ]
    assert [p.value for p in managed_credits.TopupPurpose] == ["topup", "auto_topup"]
    assert [m.value for m in managed_credits.StripeMode] == ["test", "live"]
    fields = list(managed_credits.TopupPayment.__dataclass_fields__)
    assert fields == [
        "source",
        "object_id",
        "amount_micros",
        "mode",
        "payment_intent_id",
        "purpose",
        "intent_ref",
        "payment_fee_micros",
        "payment_fee_estimated",
        "auto_topup_seq",
    ]
    assert list(managed_credits.GrantedPush(1, 2, 3).as_request()) == [
        "granted_total_micros",
        "version",
        "settled_spend_floor_micros",
    ]
    assert issubclass(managed_credits.TopupMismatch, managed_credits.IdempotencyConflict)
    assert issubclass(managed_credits.LiveModeRefused, managed_credits.ManagedCreditError)


@pytest.mark.unit
def test_balance_signal_and_error_code():
    assert balance_cache.ERROR_CODE == "BALANCE_EXHAUSTED"
    assert balance_cache.ADD_CREDITS_PATH == "/dashboard/settings/billing"
    assert balance_cache.CACHE_KEY.format(org_id="o") == "ledger:balance:o"


ORG = "0f8fad5b-d9cb-469f-a165-70867728950e"
WS = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"


@pytest.mark.unit
@pytest.mark.parametrize(
    "name, event_id",
    [
        (
            f"agentcc:req:{ORG}:a759b8eb036175b62ae761185ff944053355cec8e99b626bd5f8d9eaa7681fe4",
            "0817dfff-3caa-5fd4-8fde-7fd6cb89e545",
        ),
        ("agentcc:spend:req-a", "2786d922-7f0a-5c5a-ab90-1031ed3cceba"),
        (
            f"fa:usage:v1:evals:{ORG}:error_localizer:error_localizer:"
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8:0",
            "f78506a0-8b12-5a3a-9be0-da42d205ce25",
        ),
        (f"omega:usage:v1:{ORG}:{WS}:task:t_123:run", "fb097b2e-9801-50a8-9376-325ad900a7cc"),
        (
            f"omega:usage:v1:{ORG}:{WS}:task:t_123:sandbox_vcpu_s",
            "bc9de001-afe2-58b2-935e-7cf002ae9510",
        ),
        (
            f"omega:usage:v1:{ORG}:{WS}:connector-call:weather:run_9:call_4",
            "95f897ba-6744-55b2-941d-7cd5fd687e72",
        ),
        (
            f"omega:usage:v1:{ORG}:{WS}:deployment:dep_7:2026110113",
            "b5f428e5-76a6-5a2a-a619-8ce52b9b8942",
        ),
    ],
)
def test_usage_event_v1_id_vectors(name, event_id):
    assert usage_ingest.derive_event_id(name) == event_id
    assert uuid.UUID(event_id).version == 5


@pytest.mark.unit
def test_request_digest_vector():
    import hashlib

    digest = hashlib.sha256("\n".join(sorted(["req-b", "req-a"])).encode()).hexdigest()
    assert digest == "a759b8eb036175b62ae761185ff944053355cec8e99b626bd5f8d9eaa7681fe4"


@pytest.mark.unit
class TestFundingRules:
    def test_debit_takes_promo_commit_purchased_then_debt(self):
        wallet = Buckets(promo=3, commit=2, purchased=4)
        assert funding.allocate_debit(wallet, 4) == Allocation(promo=-3, commit=-1)
        assert funding.allocate_debit(wallet, 12) == Allocation(
            promo=-3, commit=-2, purchased=-4, debt=-3
        )
        after = funding.apply(wallet, funding.allocate_debit(wallet, 12))
        assert after == Buckets(debt=3) and after.balance == -3

    def test_credit_repays_debt_first(self):
        wallet = Buckets(debt=7)
        alloc = funding.allocate_credit(wallet, 10, Bucket.PURCHASED)
        assert alloc == Allocation(purchased=3, debt=7) and alloc.total == 10
        assert funding.apply(wallet, alloc) == Buckets(purchased=3)

    def test_reversal_shortfall_is_debt_never_another_bucket(self):
        wallet = Buckets(promo=50, purchased=4)
        alloc = funding.allocate_reversal(wallet, 10, Bucket.PURCHASED)
        assert alloc == Allocation(purchased=-4, debt=-6)
        assert funding.apply(wallet, alloc) == Buckets(promo=50, debt=6)

    def test_spend_refund_returns_to_its_buckets_debt_first(self):
        wallet = Buckets(promo=0, purchased=0, debt=5)
        spend = Allocation(promo=-2, purchased=-3, debt=-5)
        alloc = funding.allocate_spend_refund(wallet, 9, spend)
        assert alloc == Allocation(purchased=3, promo=1, debt=5) and alloc.total == 9

    def test_every_allocation_sums_to_its_amount(self):
        wallet = Buckets(promo=1, commit=1, purchased=1, debt=0)
        for amount in (1, 2, 3, 4, 10):
            assert funding.allocate_debit(wallet, amount).total == -amount
            assert funding.allocate_credit(wallet, amount, Bucket.PROMO).total == amount
            assert (
                funding.allocate_reversal(wallet, amount, Bucket.PROMO).total == -amount
            )

    @pytest.mark.parametrize("amount", [0, -1, 1.5, True, "3"])
    def test_bad_amounts_refused(self, amount):
        with pytest.raises(funding.FundingError):
            funding.allocate_debit(Buckets(), amount)

    def test_credit_cannot_fill_debt(self):
        with pytest.raises(funding.FundingError):
            funding.allocate_credit(Buckets(), 1, Bucket.DEBT)

    def test_promotional_remaining_earliest_expiry_first(self):
        first = datetime(2026, 11, 1, tzinfo=UTC)
        second = datetime(2026, 12, 1, tzinfo=UTC)
        grants = [(5, first), (5, second), (5, None)]
        assert funding.promotional_remaining(grants, 7) == [(3, second), (5, None)]


@pytest.mark.unit
def test_rating_constants():
    assert rating.MANAGED_SPEND_DIMENSION == "managed_spend"
    assert rating.MANAGED_SPEND_EVENT_TYPE == "gateway_managed_spend"
    assert rating.ALLOW_UNCONFIRMED_SETTING == "BILLING_CATALOG_ALLOW_UNCONFIRMED"
    assert list(rating.RateResult.__dataclass_fields__) == [
        "linked",
        "not_billable",
        "waiting",
        "pending_ledger_path",
    ]

"""BL-LEDGER phase 2a: the replayed-day test (OR:161.2, PLAN 4.1, walk S11).

A captured day is replayed twice, out of order, from two workers, through
everything the phase-2a ledger owns: the settlement drain's ``settle_spend``,
BL-STRIPE's ``fulfil_topup`` (webhook replays, the landing page, the
PaymentIntent event of the same money, an auto top-up), a refund, the
in-process and service-credential ingest, the legacy consumer's rows, and the
rater. Then:

- one ``UsageEventLog`` row per event id, and zero new rows of any kind on the
  second replay;
- I1, I2 and I5 hold, and every billable amount is in the ledger once: each
  managed spend is one SPEND whose wallet split equals it, and no managed
  spend is invoice- or marketplace-funded (so no credit-funded cent can reach
  a Stripe meter; managed_spend is never metered, billing.yaml:93-106@CLOUD);
- the UMP guards the ledger owns hold: (b) a platform-key gateway row is never
  an event; (d) gateway_managed_spend maps only to a prepaid_only dimension;
  (e) one payer per model call; (f) a top-up credits one wallet once; (g) a
  retried or redelivered settlement is never paid twice.

Phase 2b parts of S11 are OWED, not tested here: the reporter and Stripe test
summaries (I3), a GCP org, and events on each side of a ``cutover_at`` (the
rater leaves ledger-path platform events unrated until graduated rating
exists). Guards (a), (c) and (h) belong to DSX-USAGE, UM-G1a and UM-P3.
"""

import copy
import random
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agentcc.auto_reload_terms import AUTO_RELOAD_CONSENT_VERSION
from ee.usage.models.billing_ledger import UsageEventRecord, UsageEventRefusal
from ee.usage.models.managed_credits import ManagedCreditEntry
from ee.usage.models.usage import CreditBalance, UsageEventLog
from ee.usage.services import managed_credits as mc
from ee.usage.services import rating, reconcile, usage_ingest
from ee.usage.tests.ledger_support import (
    FakeGateway,
    migrate_usage_to_latest,
    patched_gateway,
    run_concurrently,
    spend_record,
    v1_event,
    with_catalog,
)

Kind = ManagedCreditEntry.Kind
EXPECTED_ERRORS = (mc.RefundExceedsSpend, mc.DoubleMeteredCall)


@pytest.fixture
def schema_current(transactional_db):
    migrate_usage_to_latest()


def _day(org, workspace, credential):
    """The captured day as idempotent operations, and what it should leave."""
    now = datetime.now(UTC).replace(microsecond=0)
    day = now - timedelta(hours=20)
    ops, spends = [], {}
    session = mc.TopupPayment(
        source=mc.TopupSource.CHECKOUT_SESSION,
        object_id="cs_test_day",
        amount_micros=10_000_000,
        mode=mc.StripeMode.TEST,
        payment_intent_id="pi_test_day",
    )
    same_money = mc.TopupPayment(
        source=mc.TopupSource.PAYMENT_INTENT,
        object_id="pi_test_day",
        amount_micros=10_000_000,
        mode=mc.StripeMode.TEST,
    )
    auto = mc.TopupPayment(
        source=mc.TopupSource.PAYMENT_INTENT,
        object_id="pi_test_auto",
        amount_micros=5_000_000,
        mode=mc.StripeMode.TEST,
        purpose=mc.TopupPurpose.AUTO_TOPUP,
        auto_topup_seq=1,
    )
    for n in range(3):  # webhook replays
        ops.append(
            partial(
                mc.fulfil_topup,
                org,
                payment=session,
                stripe_event_id=f"evt_day_cs_{n % 2}",
            )
        )
    ops.append(partial(mc.fulfil_topup, org, payment=session))  # the landing page
    ops.append(
        partial(mc.fulfil_topup, org, payment=same_money, stripe_event_id="evt_day_pi")
    )
    ops.append(
        partial(mc.fulfil_topup, org, payment=auto, stripe_event_id="evt_day_auto")
    )

    for i in range(30):
        billed = 0 if i == 7 else 600_000 + 1_000 * i
        ts = int((day + timedelta(minutes=i)).timestamp() * 1000)
        record = spend_record(
            org, f"req_day_{i}", billed=billed, ts_ms=ts, api_key_id=f"key_{i % 3}"
        )
        spends[f"req_day_{i}"] = billed
        ws = str(workspace.pk) if i % 2 else None
        ops.append(partial(mc.settle_spend, copy.deepcopy(record), workspace_id=ws))
        if i % 4 == 0:  # the outbox redelivers
            ops.append(partial(mc.settle_spend, copy.deepcopy(record), workspace_id=ws))
    ops.append(
        partial(mc.refund, org, "req_day_5", 100_000, reason="client retry", actor=None)
    )
    # Guard (e): an eval feature that also tries to bill the managed call it
    # made. The ledger refuses whichever payer comes second (PLAN 1.3); in
    # phase 2a no feature debits exist, so it runs after the spends here.
    guard_e = partial(
        mc.debit_usage,
        org,
        dimension="ai_credits",
        product="evals",
        unit="credits",
        quantity=3,
        amount_micros=3_000,
        period=day.strftime("%Y-%m"),
        seq=0,
        funding_source="invoice",
        occurred_at=day,
        price_catalog_version="",
        source_kind="model_call",
        source_ref="req_day_3",
    )
    # The display event the drain may also emit through the stream: a duplicate.
    events = []
    occurred = (day + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Platform events: a BYOK judge, an ordinary eval and a gateway request group.
    events.append(
        v1_event(
            org.pk,
            f"fa:usage:v1:evals:{org.pk}:turing_large_evaluator:eval:cell-9:0",
            dimension="ai_credits",
            event_type="turing_large_evaluator",
            product="evals",
            unit="credits",
            quantity="2",
            occurred_at=occurred,
            ref="cell-9",
            attributes={"credential_type": "byok", "model": "own-key-model"},
        )
    )
    events.append(
        v1_event(
            org.pk,
            f"fa:usage:v1:evals:{org.pk}:error_localizer:eval:cell-10:0",
            dimension="ai_credits",
            event_type="error_localizer",
            product="evals",
            unit="credits",
            quantity="1",
            occurred_at=occurred,
            ref="cell-10",
        )
    )
    gateway_group = v1_event(
        org.pk,
        f"agentcc:req:{org.pk}:digest-of-customer-rows",
        dimension="gateway_requests",
        event_type="gateway_request",
        product="gateway",
        unit="requests",
        quantity="12",
        occurred_at=occurred,
        system="fa_gateway_ingest",
        ref="digest-of-customer-rows",
    )
    ops.append(
        partial(
            usage_ingest.ingest_events,
            events,
            in_process_system="fa_platform",
            received_at=now,
        )
    )
    ops.append(
        partial(
            usage_ingest.ingest_events,
            [gateway_group],
            in_process_system="fa_gateway_ingest",
            received_at=now,
        )
    )
    # A late EE task run (two days back) through the service credential.
    late = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ee = v1_event(
        org.pk,
        f"omega:usage:v1:{org.pk}:{workspace.pk}:task:t_day:run",
        dimension="agent_runs",
        event_type="task_run",
        product="agents",
        unit="runs",
        quantity="1",
        occurred_at=late,
        system="ee_daemon",
        ref="task:t_day",
        workspace_id=workspace.pk,
        agent_id="agent_d",
        agent_run_id="run_d",
    )
    ops.append(
        partial(
            usage_ingest.ingest_events, [ee], credential=credential, received_at=now
        )
    )

    # A legacy ApiCallLog-era row the CLOUD consumer writes (its own dedupe).
    def legacy_row():
        UsageEventLog.objects.get_or_create(
            event_id="0e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
            defaults={
                "organization": org,
                "event_type": "turing_large_evaluator",
                "dimension": "ai_credits",
                "amount_raw": 4,
                "amount_display": 4,
                "period": day.strftime("%Y-%m"),
                "timestamp": day,
                "properties": {"source": "api_call_log", "source_id": "acl-1"},
            },
        )

    ops.append(legacy_row)
    ops.append(rating.rate_pending)
    expected = {
        "spends": spends,
        "event_ids": {e["event_id"] for e in [*events, gateway_group, ee]},
        "platform_key_request": "req_platform_key_row",
        "guard_e": guard_e,
    }
    return ops, expected


def _replay(ops, seed):
    order = list(ops)
    random.Random(seed).shuffle(order)
    expected_failures = []
    for op in order:
        try:
            op()
        except EXPECTED_ERRORS as exc:
            expected_failures.append(type(exc).__name__)
    return expected_failures


def _counts(org):
    return (
        ManagedCreditEntry.objects.filter(organization=org).count(),
        UsageEventLog.objects.filter(organization=org).count(),
        UsageEventRecord.objects.filter(organization=org).count(),
        UsageEventRefusal.objects.count(),
    )


@pytest.mark.django_db(transaction=True)
def test_replayed_day(schema_current):
    org = Organization.objects.create(name="Replayed day org")
    from tfc.constants.roles import OrganizationRoles

    creator = User.objects.create_user(
        email="replay@futureagi.com",
        password="testpassword123",
        name="R",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.no_workspace_objects.create(
        name="day", organization=org, created_by=creator
    )
    credential, _ = usage_ingest.create_service_credential(
        "ee-day",
        systems=["ee_daemon"],
        products=["agents"],
        bindings=[(org, workspace.pk)],
    )
    mc.configure_auto_topup(
        org,
        enabled=True,
        threshold_micros=1_000_000_000,
        amount_micros=5_000_000,
        daily_count_cap=1,
        daily_amount_cap_micros=5_000_000,
        payment_method_id="pm_card_visa",
        consent_text_version=AUTO_RELOAD_CONSENT_VERSION,
        actor=creator,
    )
    assert mc.claim_auto_topup(org).seq == 1
    fake = FakeGateway()
    with with_catalog(), patched_gateway(fake):
        ops, expected = _day(org, workspace, credential)
        # Round 1: two workers, two different orders, at once.
        results, errors = run_concurrently(
            [partial(_replay, ops, 1), partial(_replay, ops, 2)], timeout=300
        )
        assert errors == [], errors
        mc.refund(
            org, "req_day_5", 100_000, reason="client retry", actor=None
        )  # the retried call
        rating.rate_pending()
        with pytest.raises(mc.DoubleMeteredCall):
            expected["guard_e"]()
        after_first = _counts(org)
        # Round 2: the whole day again, two workers, two more orders.
        results2, errors2 = run_concurrently(
            [partial(_replay, ops, 3), partial(_replay, ops, 4)], timeout=300
        )
        assert errors2 == [], errors2
        assert all(failures == [] for failures in results2)  # nothing left to retry
        rating.rate_pending()
        with pytest.raises(mc.DoubleMeteredCall):
            expected["guard_e"]()
        assert _counts(org) == after_first, "the second replay wrote new rows"

    entries = ManagedCreditEntry.objects.filter(organization=org)
    # Top-ups: one per payment object, the session and its PaymentIntent once (guard f).
    topups = entries.filter(kind=Kind.TOPUP)
    assert sorted(topups.values_list("stripe_ref", flat=True)) == [
        "cs_test_day",
        "pi_test_auto",
    ]
    assert (
        CreditBalance.objects.filter(organization=org).count() == 0
    )  # no second wallet
    # Managed spend: once each (guard g), wallet-funded, never invoiced or metered.
    spend_rows = entries.filter(kind=Kind.SPEND)
    assert sorted(spend_rows.values_list("request_id", flat=True)) == sorted(
        expected["spends"]
    )
    assert -sum(spend_rows.values_list("amount_micros", flat=True)) == sum(
        expected["spends"].values()
    )
    for row in spend_rows:
        split = (
            row.funded_promo_micros
            + row.funded_commit_micros
            + row.funded_purchased_micros
            + row.funded_debt_micros
        )
        assert split == row.amount_micros
    assert not entries.filter(
        dimension="managed_spend", funding__in=["invoice", "marketplace"]
    ).exists()
    # Guard (e): the second payer for req_day_3 never landed.
    assert not entries.filter(kind=Kind.USAGE_DEBIT).exists()
    # Every event once; managed spend rated to its SPEND, the rest legacy (2a).
    logs = UsageEventLog.objects.filter(organization=org)
    billed = [r for r, b in expected["spends"].items() if b > 0]
    assert logs.filter(event_type="gateway_managed_spend").count() == len(billed)
    for event_id in expected["event_ids"]:
        assert logs.filter(event_id=event_id).count() == 1
    records = UsageEventRecord.objects.filter(organization=org)
    assert all(
        r.rated_entry is not None and r.rated_entry.kind == Kind.SPEND
        for r in records.filter(event__dimension="managed_spend")
    )
    reasons = dict(
        records.exclude(event__dimension="managed_spend").values_list(
            "event__event_type", "not_billable_reason"
        )
    )
    assert reasons == {
        "turing_large_evaluator": "legacy_period",
        "error_localizer": "legacy_period",
        "gateway_request": "legacy_period",
        "task_run": "legacy_ee",
    }
    # Guard (b): the platform-key row was never an event.
    assert not logs.filter(
        properties__request_id=expected["platform_key_request"]
    ).exists()
    # Guard (d): the catalog maps managed spend only to a prepaid dimension.
    with with_catalog():
        loaded = rating.catalog_for_validation(datetime.now(UTC))
        assert (
            loaded.dimensions[
                loaded.event_types["gateway_managed_spend"]
            ].settlement_class
            == "prepaid_only"
        )
    # The invariants, and the gateway saw the final version.
    for check in (
        reconcile.check_i1(org=org),
        reconcile.check_i2(org=org),
        reconcile.check_i5(org),
    ):
        assert check.ok, (check.name, check.failures)
    account = org.managed_credit_account
    account.refresh_from_db()
    assert account.gateway_acked_version == account.version
    assert (
        account.autoreload_in_flight_seq is None
    )  # the auto top-up's credit cleared its claim
    led = mc.ledger_balance(org)
    assert led.balance_micros == 15_000_000 - sum(expected["spends"].values()) + 100_000

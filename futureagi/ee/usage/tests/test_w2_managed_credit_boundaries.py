"""MONEY-001/002: production imports and monetary publication before mail.

Backend-only regressions; they need Postgres and Redis.
The order test records state at the mail backend boundary, outside assertions
that robust on-commit callbacks could swallow.
"""

from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from unittest.mock import Mock
from uuid import uuid4

import pytest
import redis
from django.core.mail.backends.locmem import EmailBackend

from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import auto_topup, balance_cache, gateway_push
from ee.usage.services import managed_credits as mc
from ee.usage.services.ledger_reads import usage_payload
from ee.usage.tests.ledger_support import spend_record

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
MICROS = 1_000_000


def test_money001_all_seven_public_exports_are_importable():
    # Keep these imports inside the test: a missing export must not prevent
    # the independent MONEY-002 ordering regression from collecting.
    from ee.usage.services.managed_credits import (
        claim_auto_topup,
        configure_auto_topup,
        ensure_account_pushed,
        finish_auto_topup,
        push_granted,
        pushed_value,
        sweep_unacked_pushes,
    )

    for exported, implementation in (
        (claim_auto_topup, auto_topup.claim_auto_topup),
        (configure_auto_topup, auto_topup.configure_auto_topup),
        (finish_auto_topup, auto_topup.finish_auto_topup),
        (ensure_account_pushed, gateway_push.ensure_account_pushed),
        (push_granted, gateway_push.push_granted),
        (pushed_value, gateway_push.pushed_value),
        (sweep_unacked_pushes, gateway_push.sweep_unacked_pushes),
    ):
        assert callable(exported)
        assert exported is implementation


def test_money001_settlement_and_schedules_do_not_take_import_fallback(monkeypatch):
    settlement = import_module("ee.usage.services.managed_settlement")
    schedules = import_module("tfc.temporal.schedules.agentcc_managed")
    assert settlement.ensure_account_pushed is gateway_push.ensure_account_pushed
    assert mc.sweep_unacked_pushes is gateway_push.sweep_unacked_pushes
    drain = Mock(return_value={"settled": 1})
    sweep = Mock(return_value=1)
    skipped = Mock(side_effect=AssertionError("ledger import fallback"))
    monkeypatch.setattr(settlement, "drain_managed_settlements", drain)
    monkeypatch.setattr(mc, "sweep_unacked_pushes", sweep)
    monkeypatch.setattr(schedules, "_skipped", skipped)
    # Replace the module attribute, not a method on the structlog lazy proxy: undoing a
    # proxy setattr leaves a bound method behind and blinds later capture_logs() tests.
    schedule_logger = Mock()
    monkeypatch.setattr(schedules, "logger", schedule_logger)

    assert schedules.drain_managed_settlements_activity._original_func() == {
        "settled": 1
    }
    assert schedules.sweep_unacked_pushes_activity._original_func() == {"swept": 1}
    drain.assert_called_once_with()
    sweep.assert_called_once_with()
    skipped.assert_not_called()
    schedule_logger.warning.assert_not_called()


@pytest.fixture
def isolated_wallet_delivery(monkeypatch, settings, mailoutbox):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.APP_BASE_URL = "https://billing.example.test"
    monkeypatch.setattr(mc, "request_push", lambda *args, **kwargs: None)
    monkeypatch.setattr(balance_cache, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        balance_cache.balance_changed, "send_robust", lambda **kwargs: []
    )


def _fund_with_threshold(org, capture):
    with capture(execute=True):
        mc.grant(
            org,
            10 * MICROS,
            idempotency_key="w2-r2-funding",
            actor=None,
            note="test",
        )
    account = ManagedCreditAccount.objects.get(organization=org)
    account.low_balance_threshold_micros = 5 * MICROS
    account.save(update_fields=["low_balance_threshold_micros"])
    return account


def test_money002_negative_adjustment_publishes_before_email(
    organization,
    user,
    monkeypatch,
    mailoutbox,
    django_capture_on_commit_callbacks,
    isolated_wallet_delivery,
):
    capture = django_capture_on_commit_callbacks
    account = _fund_with_threshold(organization, capture)
    assert gateway_push.pushed_value(account).granted_total_micros == 10 * MICROS
    events = []
    delivery_snapshots = []
    send_messages = EmailBackend.send_messages

    def publish(org_id, *, balance_micros, entry_seq, mode):
        events.append(("cache", org_id, balance_micros, entry_seq))

    def signal(**kwargs):
        events.append(
            (
                "signal",
                kwargs["org_id"],
                kwargs["balance_micros"],
                kwargs["entry_seq"],
            )
        )
        return []

    def request_reduced_grant(org_id):
        current = ManagedCreditAccount.objects.get(organization_id=org_id)
        grant = gateway_push.pushed_value(current)
        events.append(("push", org_id, grant.granted_total_micros, current.entry_seq))

    def record_delivery(backend, messages):
        # Observe synchronously at entry, before delivery can return or block.
        # Assertions below cannot be swallowed by on_commit(robust=True).
        delivery_snapshots.append(
            (list(events), mc.ledger_balance(organization).balance_micros)
        )
        events.append(("mail",))
        return send_messages(backend, messages)

    monkeypatch.setattr(balance_cache, "write", publish)
    monkeypatch.setattr(balance_cache.balance_changed, "send_robust", signal)
    monkeypatch.setattr(mc, "request_push", request_reduced_grant)
    monkeypatch.setattr(EmailBackend, "send_messages", record_delivery)

    with capture(execute=True):
        entry, created = mc.adjust(
            organization,
            -8 * MICROS,
            idempotency_key="w2-r2-negative-adjustment",
            reason="test credit removal",
            actor=user,
        )
        assert created and entry.amount_micros == -8 * MICROS
        assert events == [] and mailoutbox == [], "no effects before commit"

    account.refresh_from_db()
    org_id, seq = str(organization.pk), account.entry_seq
    before_mail = [
        ("cache", org_id, 2 * MICROS, seq),
        ("signal", org_id, 2 * MICROS, seq),
        ("push", org_id, 2 * MICROS, seq),
    ]
    assert delivery_snapshots == [(before_mail, 2 * MICROS)]
    assert events == [*before_mail, ("mail",)]
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    assert "$2.00" in mailoutbox[0].body and "$5.00" in mailoutbox[0].body

    with capture(execute=True):
        replay, created = mc.adjust(
            organization,
            -8 * MICROS,
            idempotency_key="w2-r2-negative-adjustment",
            reason="test credit removal",
            actor=user,
        )
    assert not created and replay.pk == entry.pk
    assert events == [*before_mail, ("mail",)]
    assert len(mailoutbox) == 1


def test_money001_scheduled_outbox_spend_reaches_refund_usage_and_alert(
    organization,
    user,
    settings,
    monkeypatch,
    mailoutbox,
    django_capture_on_commit_callbacks,
    isolated_wallet_delivery,
):
    # Import real production modules, never a stand-in for the ledger boundary.
    settlement = import_module("ee.usage.services.managed_settlement")
    schedules = import_module("tfc.temporal.schedules.agentcc_managed")
    capture = django_capture_on_commit_callbacks
    _fund_with_threshold(organization, capture)
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    monkeypatch.setattr(mc.timezone, "now", lambda: now)
    suffix = uuid4().hex
    discovery = f"test:w2-r2:{suffix}:outbox_orgs"
    needs_push = f"test:w2-r2:{suffix}:needs_push"
    monkeypatch.setattr(settlement, "OUTBOX_ORGS", discovery)
    monkeypatch.setattr(settlement, "NEEDS_PUSH", needs_push)
    org_id = str(organization.pk)
    outbox = settlement.outbox_key(org_id)
    record = spend_record(
        organization,
        f"w2-r2-outbox-{suffix}",
        billed=6 * MICROS,
        ts_ms=int(now.timestamp() * 1000),
    )
    with redis.Redis.from_url(settings.REDIS_URL, decode_responses=True) as rds:
        rds.ping()  # Missing test Redis is a failure, not a skipped integration.
        try:
            rds.xadd(outbox, record)
            rds.sadd(discovery, org_id)
            with capture(execute=True):
                result = schedules.drain_managed_settlements_activity._original_func()
                assert mailoutbox == []
            assert result["settled"] == 1 and result["dead"] == 0
            spend = ManagedCreditEntry.objects.get(
                organization=organization,
                kind="spend",
                request_id=record["request_id"],
            )
            assert spend.amount_micros == -6 * MICROS
            assert spend.quantity == Decimal(6 * MICROS)
            assert rds.xpending(outbox, settlement.GROUP)["pending"] == 0
            assert len(mailoutbox) == 1 and mailoutbox[0].to == [user.email]
            params = {"period": "2026-11", "group_by": "dimension"}
            (usage,) = usage_payload(organization, params)["rows"]
            assert usage["amount_micros"] == 6 * MICROS
            assert usage["quantity"] == "6000000"

            rds.xadd(outbox, record)
            with capture(execute=True):
                replay = schedules.drain_managed_settlements_activity._original_func()
            assert replay["redelivered"] == 1 and replay["settled"] == 0
            assert len(mailoutbox) == 1

            with capture(execute=True):
                refund, created = mc.refund(
                    organization,
                    record["request_id"],
                    reason="test outbox refund",
                    actor=user,
                )
            assert created and refund.related_entry_id == spend.pk
            assert refund.quantity == Decimal(6 * MICROS)
            (usage,) = usage_payload(organization, params)["rows"]
            assert usage["amount_micros"] == 0 and usage["quantity"] == "0"
            assert usage["unit"] == "usd_micros"
            assert sum(usage["funded"].values()) == 0
            assert len(mailoutbox) == 1
        finally:
            rds.delete(outbox, settlement.dead_key(org_id), discovery, needs_push)

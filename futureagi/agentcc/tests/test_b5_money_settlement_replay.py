"""Per-request durable debits survive an interrupted outbox acknowledgement.

Use the existing real Redis drain fixtures and committed Go-produced outbox
records. No ledger, transaction, rating or usage-event write is mocked. Only
the external config-push notification and the crash-before-XACK are injected.
This proves committed PostgreSQL state across client/connection replacement,
not Redis persistence after loss of its disk or a real machine power failure.
"""

import copy
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
import redis
from django.conf import settings
from django.db import connection

# The marker is evaluated after collection; guard enterprise imports first,
# as the existing managed-credit schema tests do in OSS checkouts.
billing_models = pytest.importorskip("ee.usage.models.billing_ledger")
credit_models = pytest.importorskip("ee.usage.models.managed_credits")
usage_models = pytest.importorskip("ee.usage.models.usage")
ledger = pytest.importorskip("ee.usage.services.managed_credits")
drain = pytest.importorskip("ee.usage.services.managed_settlement")
fixtures = pytest.importorskip("ee.usage.tests.test_managed_settlement")
UsageEventRecord = billing_models.UsageEventRecord
ManagedCreditAccount = credit_models.ManagedCreditAccount
ManagedCreditEntry = credit_models.ManagedCreditEntry
UsageEventLog = usage_models.UsageEventLog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_ee,
    pytest.mark.django_db(transaction=True),
]

# Reuse the existing real Redis connection and test-owned discovery/cleanup.
rds = fixtures.rds
own_discovery_sets = fixtures.own_discovery_sets
make_org = fixtures.make_org
billing_dimension = fixtures.billing_dimension


def _account(org):
    return ManagedCreditAccount.objects.get(organization=org)


def _spend_rows(org):
    return list(
        ManagedCreditEntry.objects.filter(
            organization=org, kind=ManagedCreditEntry.Kind.SPEND
        )
        .order_by("request_id")
        .values()
    )


def test_b5_request_debits_commit_once_across_ack_loss_and_redelivery(
    rds, make_org, billing_dimension, monkeypatch
):
    assert connection.vendor == "postgresql"
    assert not connection.in_atomic_block
    notifications = []
    monkeypatch.setattr(ledger, "request_push", notifications.append)
    owner = make_org("B5 settlement replay owner")
    other = make_org("B5 settlement replay other")
    ledger.grant(owner, 1_000_000, idempotency_key="b5-fund", actor=None, note="test")
    ledger.grant(other, 2_000_000, idempotency_key="b5-fund", actor=None, note="test")
    other_account = (
        ManagedCreditAccount.objects.filter(organization=other).values().get()
    )
    other_entries = list(
        ManagedCreditEntry.objects.filter(organization=other).order_by("id").values()
    )

    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "agentcc-gateway/internal/redisstate/testdata/onekey_outbox.json"
    )
    golden = json.loads(fixture_path.read_text())
    records = [copy.deepcopy(golden[name]) for name in ("single", "mixed")]
    expected_costs = [(11, 10, 1), (64, 60, 4)]
    expected = {}
    stream_ids = []
    for record, (billed, upstream, markup) in zip(records, expected_costs, strict=True):
        assert record["kind"] == "spend"
        assert (
            int(record["billed_micros"]),
            int(record["upstream_micros"]),
            int(record["markup_micros"]),
        ) == (billed, upstream, markup)
        # Preserve request, generation and attempt identities from the Go fixture.
        record["org_id"] = str(owner.pk)
        expected[record["request_id"]] = (billed, upstream, markup)
        stream_ids.append(fixtures._xadd(rds, owner, record))
    assert len(expected) == 2
    assert fixtures._spends(owner).count() == 0

    first_request = records[0]["request_id"]

    def crash_before_ack(_run, org_id, _entry_id):
        assert org_id == str(owner.pk)
        # The debit and its rated event must already be committed BEFORE XACK.
        assert not connection.in_atomic_block
        entry = ManagedCreditEntry.objects.get(
            organization=owner, request_id=first_request, kind="spend"
        )
        assert entry.amount_micros == -11
        event_id = uuid5(NAMESPACE_URL, "agentcc:spend:" + first_request)
        event = UsageEventLog.objects.get(event_id=event_id)
        assert event.amount_raw == Decimal(11)
        assert UsageEventRecord.objects.get(event=event).rated_entry_id == entry.pk
        raise fixtures.WorkerKilled()

    with (
        patch.object(drain._Run, "_ack", autospec=True, side_effect=crash_before_ack),
        pytest.raises(fixtures.WorkerKilled),
    ):
        drain.drain_managed_settlements(rds)

    assert fixtures._pending(rds, owner) == 2
    assert fixtures._spends(owner).count() == 1
    assert _account(owner).settled_spend_micros == 11
    # Make the actual pending entries reclaimable, without waiting or rerunning
    # inference. The existing drain's XAUTOCLAIM will consume them next.
    rds.xclaim(
        fixtures._outbox(owner),
        drain.GROUP,
        "b5-interrupted-worker",
        min_idle_time=0,
        message_ids=stream_ids,
        idle=drain.CLAIM_IDLE_MS + 1_000,
    )

    connection.close()
    with redis.Redis.from_url(settings.REDIS_URL, decode_responses=True) as resumed:
        counts = drain.drain_managed_settlements(resumed)
        assert counts["settled"] == 1
        assert counts["redelivered"] == 1
        assert counts["dead"] == counts["markup_drift"] == 0
        assert fixtures._pending(resumed, owner) == 0
        assert fixtures._dead(resumed, owner) == []

        entries = list(fixtures._spends(owner))
        assert {entry.request_id for entry in entries} == set(expected)
        assert len(entries) == 2
        for entry in entries:
            billed, upstream, markup = expected[entry.request_id]
            assert entry.idempotency_key == "spend:" + entry.request_id
            assert (
                entry.amount_micros,
                entry.upstream_cost_micros,
                entry.markup_micros,
            ) == (-billed, upstream, markup)
            assert entry.funded_promo_micros == -billed
            assert (
                entry.funded_commit_micros,
                entry.funded_purchased_micros,
                entry.funded_debt_micros,
            ) == (0, 0, 0)
            event_id = uuid5(NAMESPACE_URL, "agentcc:spend:" + entry.request_id)
            events = UsageEventLog.objects.filter(event_id=event_id)
            assert events.count() == 1
            event = events.get()
            assert event.organization_id == owner.pk
            assert event.amount_raw == Decimal(billed)
            rated = UsageEventRecord.objects.get(event=event)
            assert rated.request_id == entry.request_id
            assert rated.rated_entry_id == entry.pk

        account = _account(owner)
        assert (
            account.granted_total_micros,
            account.settled_spend_micros,
            account.promo_balance_micros,
            account.debt_micros,
        ) == (1_000_000, 75, 999_925, 0)
        assert sum(-entry.amount_micros for entry in entries) == 75
        before_replay = (
            ManagedCreditAccount.objects.filter(organization=owner).values().get()
        )
        before_entries = _spend_rows(owner)
        before_events = list(
            UsageEventLog.objects.filter(organization=owner)
            .order_by("event_id")
            .values()
        )

        # Re-deliver each already acknowledged request, in reverse order. A new
        # stream ID must not become a new monetary debit or a new rated event.
        for record in reversed(records):
            fixtures._xadd(resumed, owner, copy.deepcopy(record))
        counts = drain.drain_managed_settlements(resumed)
        assert (counts["settled"], counts["redelivered"], counts["dead"]) == (0, 2, 0)
        assert fixtures._pending(resumed, owner) == 0
        assert (
            ManagedCreditAccount.objects.filter(organization=owner).values().get()
            == before_replay
        )
        assert _spend_rows(owner) == before_entries
        assert (
            list(
                UsageEventLog.objects.filter(organization=owner)
                .order_by("event_id")
                .values()
            )
            == before_events
        )

    assert (
        ManagedCreditAccount.objects.filter(organization=other).values().get()
        == other_account
    )
    assert (
        list(
            ManagedCreditEntry.objects.filter(organization=other)
            .order_by("id")
            .values()
        )
        == other_entries
    )
    assert not connection.in_atomic_block

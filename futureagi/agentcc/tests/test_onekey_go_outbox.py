"""Real database proof for Go's single v1 request spend and refund projection.

The fixtures are emitted by TestMixedDjangoOutbox from actual Redis XADD rows;
no settlement parser, transaction, wallet or ledger entry is mocked here.
"""

import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_ee,
    pytest.mark.django_db(transaction=True),
]


@pytest.mark.parametrize(
    "name,billed,upstream,hold,refund_micros,attempt_count",
    [
        ("single", 11, 10, 100, 11, 1),
        ("mixed", 64, 60, 200, 53, 2),
        ("estimate", 100, 95, 100, 89, 1),
        ("overhold", 1050, 1000, 100, 1050, 1),
        ("undispatched", 53, 50, 1693692, 53, 2),
    ],
)
def test_go_outbox_settles_and_refunds_once(
    organization,
    monkeypatch,
    name,
    billed,
    upstream,
    hold,
    refund_micros,
    attempt_count,
):
    from django.db import connection

    from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
    from ee.usage.models.usage import UsageEventLog
    from ee.usage.services.managed_credits import refund, settle_spend

    # Only the external gateway push is synthetic. Real transaction commits,
    # immutable entries, wallet allocation and display events remain untouched.
    pushes = []
    monkeypatch.setattr("ee.usage.services.managed_credits.request_push", pushes.append)
    assert not connection.in_atomic_block

    default = (
        Path(__file__).resolve().parents[3]
        / "agentcc-gateway/internal/redisstate/testdata/onekey_outbox.json"
    )
    fixture_env = "ONEKEY_OUTBOX_FIXTURES"
    if name == "undispatched":
        default = default.with_name("onekey_undispatched_outbox.json")
        fixture_env = "ONEKEY_UNDISPATCHED_OUTBOX"
    fixtures = json.loads(Path(os.environ.get(fixture_env, default)).read_text())
    record = copy.deepcopy(fixtures[name])
    assert record["v"] == "1" and record["kind"] == "spend"
    assert "dimensions" not in record
    assert all(isinstance(value, str) for value in record.values())
    assert (
        int(record["billed_micros"]),
        int(record["upstream_micros"]),
        int(record["hold_micros"]),
    ) == (billed, upstream, hold)
    assert record["usage_estimated"] == ("1" if name == "estimate" else "0")
    attempts = json.loads(record["attempts"])
    assert len(attempts) == attempt_count
    if name == "undispatched":
        # Emitted by the real HTTP refusal after reservation, before dispatch.
        assert [attempt["outcome"] for attempt in attempts] == ["error", "skipped"]
        assert [attempt["billed_micros"] for attempt in attempts] == [53, 0]
        assert [attempt["model"] for attempt in attempts] == [
            "openai/o1-pro",
            "google/gemini-3.5-flash",
        ]
    # Only the synthetic organization is rebound to this test database row.
    # The original Go request/generation ID and all money/evidence are retained.
    record["org_id"] = str(organization.pk)
    request_id = record["request_id"]

    spend, created = settle_spend(copy.deepcopy(record))
    again, replay_created = settle_spend(copy.deepcopy(record))
    assert (created, replay_created, again.pk) == (True, False, spend.pk)
    assert (spend.kind, spend.idempotency_key, spend.amount_micros) == (
        "spend",
        f"spend:{request_id}",
        -billed,
    )
    assert (spend.upstream_cost_micros, spend.markup_micros, spend.markup_bps) == (
        upstream,
        billed - upstream,
        500,
    )
    assert spend.attempts == json.loads(record["attempts"])
    assert spend.attempt_count == attempt_count
    assert spend.usage_estimated == (name == "estimate")
    assert spend.funded_debt_micros == -billed
    assert (
        spend.funded_promo_micros
        == spend.funded_commit_micros
        == spend.funded_purchased_micros
        == 0
    )

    event_id = uuid5(NAMESPACE_URL, "agentcc:spend:" + request_id)
    event = UsageEventLog.objects.get(event_id=event_id)
    assert event.amount_raw == Decimal(billed)
    assert UsageEventLog.objects.filter(event_id=event_id).count() == 1
    account = ManagedCreditAccount.objects.get(organization=organization)
    assert account.settled_spend_micros == billed
    assert account.debt_micros == billed

    correction, refunded = refund(
        organization,
        request_id,
        refund_micros,
        reason="synthetic inclusive cost reconciliation",
        actor=None,
    )
    duplicate, duplicate_created = refund(
        organization,
        request_id,
        refund_micros,
        reason="synthetic inclusive cost reconciliation",
        actor=None,
    )
    assert (refunded, duplicate_created, duplicate.pk) == (True, False, correction.pk)
    assert (correction.kind, correction.idempotency_key, correction.amount_micros) == (
        "refund",
        f"refund:{request_id}",
        refund_micros,
    )
    assert correction.related_entry_id == spend.pk
    assert correction.funded_debt_micros == refund_micros
    assert list(
        ManagedCreditEntry.objects.filter(organization=organization)
        .order_by("amount_micros")
        .values_list("kind", "amount_micros")
    ) == [("spend", -billed), ("refund", refund_micros)]
    # A late drain replay cannot undo the refund or emit a second spend/event.
    assert settle_spend(copy.deepcopy(record))[1] is False
    account.refresh_from_db()
    assert account.settled_spend_micros == billed
    assert account.debt_micros == billed - refund_micros
    assert UsageEventLog.objects.filter(event_id=event_id).count() == 1
    assert not connection.in_atomic_block
    assert pushes == [str(organization.pk)]

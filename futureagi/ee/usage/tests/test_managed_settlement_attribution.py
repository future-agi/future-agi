"""Event-owned attribution through the real outbox drain and ledger write.

Use the same real-test-Redis and rollback-only Django transaction conventions
as test_managed_settlement.py. No gateway or ledger operation is mocked.
"""

import time
from uuid import uuid4

import pytest
import redis
from django.conf import settings

from agentcc.models.request_log import AgentccRequestLog
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import managed_settlement as ms

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EVENT_ATTRIBUTION = {
    # synthetic test value
    "api_key_id": "A" * 24,
    # synthetic test value
    "agent_run_id": "B" * 24,
    # synthetic test value
    "agent_id": "C" * 24,
}
LOG_ATTRIBUTION = {
    # synthetic test value
    "api_key_id": "D" * 24,
    # synthetic test value
    "agent_run_id": "E" * 24,
    # synthetic test value
    "agent_id": "F" * 24,
}
ATTRIBUTION_FIELDS = tuple(EVENT_ATTRIBUTION)
BILLED_MICROS = 1_050


@pytest.fixture
def rds(organization, monkeypatch):
    """Only this test's discovery sets and organization stream are cleaned."""
    prefix = f"test:settlement-attribution:{uuid4().hex}"
    monkeypatch.setattr(ms, "OUTBOX_ORGS", f"{prefix}:outbox_orgs")
    monkeypatch.setattr(ms, "NEEDS_PUSH", f"{prefix}:needs_push")
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        client.ping()
        yield client
    finally:
        try:
            client.delete(
                ms.OUTBOX_ORGS,
                ms.NEEDS_PUSH,
                ms.outbox_key(str(organization.pk)),
                ms.dead_key(str(organization.pk)),
            )
        finally:
            client.close()


@pytest.fixture
def record(organization):
    """All outbox values are strings, including the positive billed amount."""
    return {
        "v": "1",
        "kind": "spend",
        "org_id": str(organization.pk),
        "request_id": "req_synthetic_attribution",
        "generation_id": "req_synthetic_attribution",
        "billed_micros": str(BILLED_MICROS),
        "upstream_micros": "1000",
        "markup_micros": "50",
        "markup_bps": "500",
        "model": "synthetic-model",
        "provider": "synthetic-provider",
        "attempts": "[]",
        "ts_ms": str(int(time.time() * 1000)),
        **EVENT_ATTRIBUTION,
    }


@pytest.fixture
def request_log(workspace, record):
    """A matching log supplies workspace, but must never supply attribution."""
    return AgentccRequestLog.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        request_id=record["request_id"],
        model=record["model"],
        **LOG_ATTRIBUTION,
    )


def _drain_record(rds, record):
    stream = ms.outbox_key(record["org_id"])
    rds.xadd(stream, record)
    rds.sadd(ms.OUTBOX_ORGS, record["org_id"])
    counts = ms.drain_managed_settlements(rds)
    assert rds.xpending(stream, ms.GROUP)["pending"] == 0
    return counts


def _entry(organization):
    return ManagedCreditEntry.objects.get(
        organization=organization, kind=ManagedCreditEntry.Kind.SPEND
    )


def _attribution(entry):
    return {name: getattr(entry, name) for name in ATTRIBUTION_FIELDS}


def test_settlement_attribution_comes_from_event_not_request_log(
    rds, organization, record, request_log
):
    counts = _drain_record(rds, record)

    assert (counts["settled"], counts["dead"]) == (1, 0)
    entry = _entry(organization)
    assert _attribution(entry) == EVENT_ATTRIBUTION
    assert entry.workspace_id == request_log.workspace_id
    assert entry.amount_micros == -BILLED_MICROS
    request_log.refresh_from_db()
    assert _attribution(request_log) == LOG_ATTRIBUTION


def test_settlement_attribution_does_not_require_request_log(
    rds, organization, record
):
    assert not AgentccRequestLog.no_workspace_objects.filter(
        organization=organization, request_id=record["request_id"]
    ).exists()

    counts = _drain_record(rds, record)

    assert (counts["settled"], counts["dead"]) == (1, 0)
    entry = _entry(organization)
    assert _attribution(entry) == EVENT_ATTRIBUTION
    assert entry.workspace_id is None


@pytest.mark.parametrize("field", [*ATTRIBUTION_FIELDS, "all"])
@pytest.mark.parametrize("absence", ["omitted", "empty"])
def test_missing_settlement_attribution_stays_blank_despite_request_log(
    rds, organization, record, request_log, field, absence
):
    missing = ATTRIBUTION_FIELDS if field == "all" else (field,)
    expected = dict(EVENT_ATTRIBUTION)
    for name in missing:
        expected[name] = ""
        if absence == "omitted":
            record.pop(name)
        else:
            record[name] = ""

    counts = _drain_record(rds, record)

    assert (counts["settled"], counts["dead"]) == (1, 0)
    entry = _entry(organization)
    assert _attribution(entry) == expected
    assert entry.workspace_id == request_log.workspace_id


@pytest.mark.parametrize("field", ATTRIBUTION_FIELDS)
def test_settlement_attribution_accepts_exact_column_limit(
    rds, organization, record, request_log, field
):
    limit = ManagedCreditEntry._meta.get_field(field).max_length
    # synthetic test value
    record[field] = "X" * limit
    expected = {name: record[name] for name in ATTRIBUTION_FIELDS}

    counts = _drain_record(rds, record)

    assert (counts["settled"], counts["dead"]) == (1, 0)
    entry = _entry(organization)
    assert _attribution(entry) == expected
    assert len(getattr(entry, field)) == limit
    assert entry.workspace_id == request_log.workspace_id


@pytest.mark.parametrize("field", ATTRIBUTION_FIELDS)
def test_settlement_attribution_refuses_overlong_value_without_trimming(
    rds, organization, record, field
):
    limit = ManagedCreditEntry._meta.get_field(field).max_length
    # synthetic test value
    record[field] = "X" * (limit + 1)

    counts = _drain_record(rds, record)

    assert (counts["settled"], counts["dead"]) == (0, 1)
    assert not ManagedCreditEntry.objects.filter(organization=organization).exists()
    dead = rds.xrange(ms.dead_key(str(organization.pk)))
    assert len(dead) == 1
    assert dead[0][1][field] == record[field]
    assert dead[0][1][ms.DEAD_ERROR] == (
        f"InvalidSettlementRecord: {field} is longer than {limit}"
    )

    # A corrected event with the same request id is still able to settle.
    record[field] = EVENT_ATTRIBUTION[field]
    recovered = _drain_record(rds, record)
    assert (recovered["settled"], recovered["dead"]) == (1, 0)
    assert _attribution(_entry(organization)) == EVENT_ATTRIBUTION


def test_settlement_redelivery_preserves_first_event_attribution(
    rds, organization, record
):
    first = _drain_record(rds, record)
    entry_id = _entry(organization).pk
    record.update(LOG_ATTRIBUTION)

    repeated = _drain_record(rds, record)

    assert (first["settled"], first["dead"]) == (1, 0)
    assert (repeated["settled"], repeated["redelivered"], repeated["dead"]) == (0, 1, 0)
    entry = _entry(organization)
    assert entry.pk == entry_id
    assert _attribution(entry) == EVENT_ATTRIBUTION
    account = ManagedCreditAccount.objects.get(organization=organization)
    assert account.settled_spend_micros == BILLED_MICROS

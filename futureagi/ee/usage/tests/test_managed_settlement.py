"""Settlement drain (GW-WALLET, ported from the integration line's lane D3):
the gateway's per-org outbox into the managed credit ledger, exactly once per
``spend:<request_id>`` through the BL-LEDGER-2A ``settle_spend`` (which writes
the spend's usage event itself, so the drain emits none), outbox trimming, the
needs-push set and the managed schedules.

Records are written with ``XADD`` to the real test Redis (the lane's own DB,
``REDIS_URL``), exactly as the gateway's SETTLE writes them: every value a
string. The drain commits each ledger write in a durable block before it
acknowledges the entry; Django's test transaction allows that block, so the
tests run in it.

Invariants (PLAN section 4.4) proven here: I5 (the account's settled spend is
the sum of its spend entries after any mix of redelivery and crashes), I6 on
the push the drain sends (granted clamped at 0, the floor the settled spend)
and I10 (a spend whose markup is not the catalog's is settled and flagged).
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import redis
from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import Sum
from django.test import override_settings
from structlog.testing import capture_logs

from accounts.models import User
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from agentcc.models.request_log import AgentccRequestLog
from agentcc.services.gateway_client import GatewayClientError
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.models.usage import UsageEventLog
from ee.usage.services import managed_settlement as ms
from ee.usage.services.config import BillingConfig
from ee.usage.services.emitter import STREAM_KEY as USAGE_STREAM
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EXAMPLES_DIR = (
    Path(__file__).resolve().parents[4] / "api_contracts" / "gateway" / "examples"
)
GATEWAY_ADMIN = "agentcc.services.gateway_admin.credits.GatewayAdminClient"
DAY_MS = 24 * 3600 * 1000
GOLDEN_BILLED = 11_815
# The module's own set names, read before any test patches them.
GATEWAY_SETS = (ms.OUTBOX_ORGS, ms.NEEDS_PUSH)


class WorkerKilled(Exception):
    """Stands in for the worker dying between two steps of the drain."""


@pytest.fixture
def rds():
    """The real test Redis. Unreachable is a failure, never a skip."""
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    client.ping()
    yield client
    client.close()


@pytest.fixture(autouse=True)
def own_discovery_sets(rds, monkeypatch):
    """The drain's two global sets, under this test's own ``test:D3:`` keys.

    The lane's Redis DB also serves its scenario runs, whose orgs sit in the
    real ``mc:outbox_orgs``: a test drain must never visit them, nor they its.
    """
    own = [f"test:D3:{uuid4().hex}:{name}" for name in ("outbox_orgs", "needs_push")]
    monkeypatch.setattr(ms, "OUTBOX_ORGS", own[0])
    monkeypatch.setattr(ms, "NEEDS_PUSH", own[1])
    yield
    rds.delete(*own)


@pytest.fixture
def make_org(rds, db):
    """Create orgs; their Redis keys and usage events go afterwards."""
    made = []

    def make(name="Drain org"):
        org = Organization.objects.create(name=name)
        made.append(str(org.pk))
        return org

    yield make
    for org_id in made:
        keys = list(rds.scan_iter(match=f"mc:{{{org_id}}}:*"))
        if keys:
            rds.delete(*keys)
    for event_id, fields in rds.xrange(USAGE_STREAM):
        if fields.get("org_id") in made:
            rds.xdel(USAGE_STREAM, event_id)


def _echo_gateway(method, path, json=None, params=None):
    """A gateway that applies every push, answering credits-granted-push-v1."""
    spent = json["settled_spend_floor_micros"]
    return {
        "applied": True,
        "granted_total_micros": json["granted_total_micros"],
        "granted_version": json["version"],
        "spent_micros": spent,
        "held_micros": 0,
        "available_micros": json["granted_total_micros"] - spent,
    }


@pytest.fixture
def gateway():
    with patch(GATEWAY_ADMIN) as client_class:
        client_class.return_value.request.side_effect = _echo_gateway
        yield client_class.return_value


@pytest.fixture
def billing_dimension():
    """billing.yaml maps gateway_managed_spend to a dimension and events are on."""
    with (
        override_settings(USAGE_EVENTS_ENABLED=True),
        patch.object(
            BillingConfig, "event_type_dimension", return_value="managed_spend"
        ) as lookup,
    ):
        yield lookup


def _record(org, request_id, **overrides):
    """The managed-settlement-v1 golden for ``org``, as the outbox carries it."""
    record = json.loads((EXAMPLES_DIR / "managed-settlement-v1.json").read_text())
    record.update(
        org_id=str(org.pk),
        request_id=request_id,
        generation_id=request_id,
        ts_ms=str(int(time.time() * 1000)),
    )
    record.update({k: str(v) for k, v in overrides.items()})
    return record


def _outbox(org):
    return ms.outbox_key(str(org.pk))


def _xadd(rds, org, record, entry_id="*"):
    """Write one outbox entry and the discovery hint, as the gateway does."""
    added = rds.xadd(_outbox(org), record, id=entry_id)
    rds.sadd(ms.OUTBOX_ORGS, str(org.pk))
    return added


def _spends(org):
    return ManagedCreditEntry.objects.filter(
        organization=org, kind=ManagedCreditEntry.Kind.SPEND
    ).order_by("occurred_at", "id")


def _pending(rds, org):
    return rds.xpending(_outbox(org), ms.GROUP)["pending"]


def _dead(rds, org):
    return [fields for _, fields in rds.xrange(ms.dead_key(str(org.pk)))]


def _events(rds, org):
    return [
        fields
        for _, fields in rds.xrange(USAGE_STREAM)
        if fields.get("org_id") == str(org.pk)
    ]


def _age_pending(rds, org, entry_id, consumer=ms.CONSUMER):
    """Make a pending entry look idle for longer than the reclaim threshold."""
    rds.xclaim(
        _outbox(org),
        ms.GROUP,
        consumer,
        min_idle_time=0,
        message_ids=[entry_id],
        idle=ms.CLAIM_IDLE_MS + 1_000,
    )


def _settled_spend(org):
    return ManagedCreditAccount.objects.get(organization=org).settled_spend_micros


# --- redelivery and crash safety --------------------------------------------


def test_redelivery_settles_once(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_redeliver"))
    first = ms.drain_managed_settlements(rds)

    # The same record again (a gateway XADD retried after an unclear reply).
    _xadd(rds, org, _record(org, "req_redeliver"))
    second = ms.drain_managed_settlements(rds)
    third = ms.drain_managed_settlements(rds)

    assert (first["settled"], first["redelivered"]) == (1, 0)
    assert (second["settled"], second["redelivered"]) == (0, 1)
    assert (third["settled"], third["redelivered"]) == (0, 0)
    assert _spends(org).count() == 1
    assert _spends(org).get().idempotency_key == "spend:req_redeliver"
    assert _settled_spend(org) == GOLDEN_BILLED
    assert _pending(rds, org) == 0
    assert _dead(rds, org) == []


def test_redelivery_with_another_amount_is_logged_and_changes_nothing(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_differs"))
    ms.drain_managed_settlements(rds)

    _xadd(rds, org, _record(org, "req_differs", billed_micros=99))
    with capture_logs() as logs:
        ms.drain_managed_settlements(rds)

    assert _spends(org).count() == 1
    assert _settled_spend(org) == GOLDEN_BILLED
    differs = [e for e in logs if e["event"] == "managed_settlement_redelivery_differs"]
    assert len(differs) == 1
    assert (differs[0]["billed_micros"], differs[0]["ledger_micros"]) == (
        99,
        GOLDEN_BILLED,
    )


def test_crash_after_commit_before_ack_no_double_entry(rds, make_org):
    org = make_org()
    entry_id = _xadd(rds, org, _record(org, "req_crash"))

    with (
        patch.object(ms._Run, "_ack", side_effect=WorkerKilled),
        pytest.raises(WorkerKilled),
    ):
        ms.drain_managed_settlements(rds)

    # The ledger write committed; the entry was never acknowledged.
    assert _spends(org).count() == 1
    assert _pending(rds, org) == 1

    # A run before the reclaim threshold leaves the entry to its consumer.
    early = ms.drain_managed_settlements(rds)
    assert (early["settled"], early["redelivered"]) == (0, 0)
    assert _pending(rds, org) == 1

    _age_pending(rds, org, entry_id)
    late = ms.drain_managed_settlements(rds)

    assert (late["settled"], late["redelivered"]) == (0, 1)
    assert _spends(org).count() == 1
    assert _settled_spend(org) == GOLDEN_BILLED
    assert _pending(rds, org) == 0


def test_database_outage_leaves_entries_pending_not_dead(rds, make_org):
    org = make_org()
    entry_id = _xadd(rds, org, _record(org, "req_outage"))

    with (
        patch.object(ms, "settle_spend", side_effect=OperationalError("db down")),
        pytest.raises(OperationalError),
    ):
        ms.drain_managed_settlements(rds)

    assert _spends(org).count() == 0
    assert _dead(rds, org) == []
    assert _pending(rds, org) == 1

    _age_pending(rds, org, entry_id)
    result = ms.drain_managed_settlements(rds)

    assert result["settled"] == 1
    assert _settled_spend(org) == GOLDEN_BILLED
    assert _pending(rds, org) == 0


def test_drain_refuses_to_run_inside_a_transaction(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_in_txn"))

    with transaction.atomic(), pytest.raises(RuntimeError, match="durable"):
        ms.drain_managed_settlements(rds)

    # Nothing was read: the consumer group does not even exist yet.
    assert rds.xinfo_groups(_outbox(org)) == []
    assert rds.xlen(_outbox(org)) == 1


# --- the usage event: settle_spend writes it, the drain never emits ------------


def _usage_events(org):
    """The org's gateway_managed_spend rows in ``UsageEventLog``."""
    return UsageEventLog.objects.filter(
        organization=org, event_type="gateway_managed_spend"
    ).order_by("timestamp", "id")


@pytest.fixture
def emit_spy():
    """Every ``emitter.emit`` call. The BL-LEDGER-2A interface: settle_spend
    writes the spend's gateway_managed_spend row in its own transaction, so
    the drain must never emit it as well."""
    with patch("ee.usage.services.emitter.emit") as emit:
        yield emit


def test_settle_spend_writes_the_one_usage_event_and_the_drain_emits_none(
    rds, make_org, billing_dimension, emit_spy
):
    """Even with usage events on and gateway_managed_spend mapped, the drain
    emits nothing: the spend's one usage event is the row settle_spend wrote,
    event id ``uuid5(NAMESPACE_URL, "agentcc:spend:" + request_id)``, quantity
    the billed micros, at the spend's own time, already rated to the entry
    (I1). A redelivered record writes no second row."""
    org = make_org()
    _xadd(rds, org, _record(org, "req_event"))
    first = ms.drain_managed_settlements(rds)
    _xadd(rds, org, _record(org, "req_event"))
    again = ms.drain_managed_settlements(rds)

    assert (first["settled"], again["redelivered"]) == (1, 1)
    entry = _spends(org).get()
    (event,) = _usage_events(org)
    assert event.event_id == uuid5(NAMESPACE_URL, "agentcc:spend:req_event")
    assert event.amount_raw == GOLDEN_BILLED
    assert event.timestamp == entry.occurred_at
    assert event.ledger_record.rated_entry_id == entry.pk
    emit_spy.assert_not_called()
    assert _events(rds, org) == []


def test_usage_event_is_written_whether_or_not_usage_events_are_on(
    rds, make_org, emit_spy
):
    """The event is the ledger's (BL-LEDGER-2A), not the usage stream's: it is
    written with usage events off, and nothing is held back for a later run."""
    org = make_org()
    _xadd(rds, org, _record(org, "req_events_off"))
    with override_settings(USAGE_EVENTS_ENABLED=False):
        result = ms.drain_managed_settlements(rds)

    assert result["settled"] == 1
    assert [e.event_id for e in _usage_events(org)] == [
        uuid5(NAMESPACE_URL, "agentcc:spend:req_events_off")
    ]
    emit_spy.assert_not_called()


def test_spend_carries_the_request_logs_workspace(rds, make_org, emit_spy):
    """managed-settlement-v1 has no workspace: the drain takes it from the
    request-log row with the same request id, and from the base request id
    for a capability attempt (``<request_id>.<n>``), and passes it to
    ``settle_spend(record, workspace_id=...)``. The entry and its usage event
    both carry it; a spend with no log row has none."""
    org = make_org()
    user = User.objects.create_user(
        email=f"gw-wallet-{uuid4().hex[:8]}@example.com",
        password="testpassword123",
        name="Drain workspace owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"Drain {uuid4().hex[:6]}", organization=org, created_by=user
    )
    for request_id in ("req_in_workspace", "req_capability_ws"):
        AgentccRequestLog.no_workspace_objects.create(
            organization=org, workspace=workspace, request_id=request_id, model="m"
        )
    _xadd(rds, org, _record(org, "req_in_workspace"))
    _xadd(rds, org, _record(org, "req_capability_ws.2"))
    _xadd(rds, org, _record(org, "req_no_log_row"))

    ms.drain_managed_settlements(rds)

    entries = {e.request_id: e for e in _spends(org)}
    events = {e.event_id: e for e in _usage_events(org)}
    for request_id, expected in (
        ("req_in_workspace", workspace.pk),
        ("req_capability_ws.2", workspace.pk),
        ("req_no_log_row", None),
    ):
        assert entries[request_id].workspace_id == expected, request_id
        event = events[uuid5(NAMESPACE_URL, f"agentcc:spend:{request_id}")]
        assert event.workspace_id == expected, request_id
    emit_spy.assert_not_called()


@pytest.mark.parametrize(("flag", "estimated"), [("1", True), ("0", False)])
def test_usage_estimated_reaches_the_ledger_entry(rds, make_org, flag, estimated):
    """A gateway spend billed on an estimate (no upstream usage) carries
    ``usage_estimated "1"`` in its managed-settlement-v1 record; the drain
    passes the record to settle_spend as it is, so the SPEND entry says so."""
    org = make_org()
    _xadd(rds, org, _record(org, f"req_estimated_{flag}", usage_estimated=flag))

    ms.drain_managed_settlements(rds)

    assert _spends(org).get().usage_estimated is estimated


# --- record kinds and poison --------------------------------------------------


def test_void_acked_no_entry(rds, make_org):
    org = make_org()
    void = _record(
        org,
        "req_void",
        kind="void",
        billed_micros=0,
        upstream_micros=0,
        markup_micros=0,
        landed_micros=0,
    )
    _xadd(rds, org, void)

    result = ms.drain_managed_settlements(rds)

    assert (result["voids"], result["settled"], result["dead"]) == (1, 0, 0)
    assert not ManagedCreditEntry.objects.filter(organization=org).exists()
    assert _pending(rds, org) == 0
    assert _dead(rds, org) == []


def test_poison_moved_to_dead(rds, make_org):
    org = make_org()
    garbage_id = _xadd(rds, org, {"garbage": "not a settlement"})
    wrong_version_id = _xadd(rds, org, _record(org, "req_v2", v="2"))
    _xadd(rds, org, _record(org, "req_after_poison"))

    with capture_logs() as logs:
        result = ms.drain_managed_settlements(rds)

    assert (result["dead"], result["settled"]) == (2, 1)
    assert _spends(org).get().request_id == "req_after_poison"
    dead = _dead(rds, org)
    assert [d[ms.DEAD_SOURCE] for d in dead] == [garbage_id, wrong_version_id]
    assert dead[0]["garbage"] == "not a settlement"
    assert dead[1]["request_id"] == "req_v2"
    assert all(d[ms.DEAD_ERROR] for d in dead)
    assert _pending(rds, org) == 0
    logged = [e for e in logs if e["event"] == "managed_settlement_dead_lettered"]
    assert [e["log_level"] for e in logged] == ["error", "error"]
    assert [e["outbox_id"] for e in logged] == [garbage_id, wrong_version_id]


def test_drain_dead_letters_on_any_settle_exception(rds, make_org):
    org = make_org()
    # datetime.fromtimestamp overflows: not a ManagedCreditError.
    huge_ts_id = _xadd(
        rds, org, _record(org, "req_huge_ts", ts_ms="99999999999999999999")
    )
    # 32,768 attempts overflow attempt_count: a DataError from the insert.
    too_many = json.dumps([{}] * 32_768)
    overflow_id = _xadd(rds, org, _record(org, "req_overflow", attempts=too_many))
    _xadd(rds, org, _record(org, "req_next"))

    result = ms.drain_managed_settlements(rds)
    rerun = ms.drain_managed_settlements(rds)

    assert (result["dead"], result["settled"]) == (2, 1)
    assert (rerun["dead"], rerun["settled"], rerun["redelivered"]) == (0, 0, 0)
    assert [e.request_id for e in _spends(org)] == ["req_next"]
    assert _settled_spend(org) == GOLDEN_BILLED
    dead = _dead(rds, org)
    assert [d[ms.DEAD_SOURCE] for d in dead] == [huge_ts_id, overflow_id]
    assert "DataError" in dead[1][ms.DEAD_ERROR]
    assert _pending(rds, org) == 0


def test_record_naming_another_org_is_dead_lettered(rds, make_org):
    org, other = make_org("Outbox owner"), make_org("Named org")
    _xadd(rds, org, _record(other, "req_cross_org"))

    result = ms.drain_managed_settlements(rds)

    assert result["dead"] == 1
    assert not ManagedCreditEntry.objects.filter(request_id="req_cross_org").exists()
    assert "org_id" in _dead(rds, org)[0][ms.DEAD_ERROR]


def test_landed_fields_reach_entry(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_landed"))

    ms.drain_managed_settlements(rds)

    entry = _spends(org).get()
    assert entry.amount_micros == -GOLDEN_BILLED
    assert (entry.upstream_cost_micros, entry.markup_micros, entry.markup_bps) == (
        11_252,
        563,
        500,
    )
    assert entry.landed_cost_micros == 12_249
    assert (entry.upstream_fee_bps, entry.processing_fee_bps) == (550, 320)
    assert entry.served_upstream_provider == "anthropic"
    assert entry.upstream_key_id == "kh_3b9e"
    assert (entry.model, entry.provider, entry.cost_source) == (
        "anthropic/claude-sonnet-5.5",
        "openrouter",
        "provider_reported",
    )
    assert entry.attempt_count == 2
    assert entry.agent_run_id == "run_R2"


def test_capability_attempts_settle_as_separate_entries(
    rds, make_org, billing_dimension, emit_spy
):
    org = make_org()
    rid = "req_capability"
    for n in (1, 2):
        attempt = _record(
            org,
            f"{rid}.{n}",
            generation_id=rid,
            provider="echo-a",
            model="echo/basic",
            billed_micros=1_050,
        )
        _xadd(rds, org, attempt)

    first = ms.drain_managed_settlements(rds)
    # A redelivery of each attempt's record changes nothing.
    for n in (1, 2):
        _xadd(
            rds, org, _record(org, f"{rid}.{n}", generation_id=rid, billed_micros=1_050)
        )
    second = ms.drain_managed_settlements(rds)

    assert first["settled"] == 2
    assert (second["settled"], second["redelivered"]) == (0, 2)
    # One entry per attempt. The two attempts' outbox timestamps usually fall in
    # the same millisecond, and _spends then orders them by the random entry id,
    # so the order is not part of the guarantee: compare the sorted keys.
    assert sorted(e.idempotency_key for e in _spends(org)) == [
        f"spend:{rid}.1",
        f"spend:{rid}.2",
    ]
    assert _settled_spend(org) == 2_100
    assert sorted(str(e.event_id) for e in _usage_events(org)) == sorted(
        [
            str(uuid5(NAMESPACE_URL, f"agentcc:spend:{rid}.1")),
            str(uuid5(NAMESPACE_URL, f"agentcc:spend:{rid}.2")),
        ]
    )
    emit_spy.assert_not_called()


# --- discovery and batching ---------------------------------------------------


def test_key_names_are_the_gateways():
    """The names the gateway's G6 scripts write (plan section 6.5)."""
    assert ms.outbox_key("o1") == "mc:{o1}:outbox"
    assert ms.dead_key("o1") == "mc:{o1}:dead"
    assert ms.GROUP == "django-settlement"
    assert GATEWAY_SETS == ("mc:outbox_orgs", "mc:needs_push")


def test_one_orgs_redis_error_never_blocks_the_others(rds, make_org):
    broken, healthy = make_org("Broken stream"), make_org("Healthy stream")
    rds.set(_outbox(broken), "not a stream")  # XGROUP CREATE: WRONGTYPE
    rds.sadd(ms.OUTBOX_ORGS, str(broken.pk))
    _xadd(rds, healthy, _record(healthy, "req_healthy"))

    with capture_logs() as logs:
        result = ms.drain_managed_settlements(rds)

    assert (result["org_errors"], result["settled"]) == (1, 1)
    assert _spends(healthy).get().request_id == "req_healthy"
    failed = [e for e in logs if e["event"] == "managed_settlement_org_failed"]
    assert [e["org_id"] for e in failed] == [str(broken.pk)]


def test_discovers_account_orgs_without_the_hint(rds, make_org, gateway):
    org = make_org()
    from ee.usage.services import managed_credits

    managed_credits.grant(org, 1_000_000, idempotency_key="g", actor=None, note="")
    # No SADD mc:outbox_orgs: the hint is written after the script, not atomically.
    rds.xadd(_outbox(org), _record(org, "req_no_hint"))

    result = ms.drain_managed_settlements(rds)

    assert result["settled"] == 1
    assert _spends(org).get().request_id == "req_no_hint"


def test_drain_reads_past_one_batch(rds, make_org, monkeypatch):
    monkeypatch.setattr(ms, "BATCH", 2)
    org = make_org()
    for i in range(5):
        _xadd(rds, org, _record(org, f"req_batch_{i}"))

    result = ms.drain_managed_settlements(rds)

    assert result["settled"] == 5
    assert _spends(org).count() == 5
    assert _pending(rds, org) == 0


# --- outbox trimming ------------------------------------------------------------


def test_xtrim_keeps_pending_and_last_7_days(rds, make_org):
    org = make_org()
    stream = _outbox(org)
    eight_days_ago = int(time.time() * 1000) - 8 * DAY_MS
    old = [f"{eight_days_ago + i}-0" for i in range(3)]
    recent_id = f"{int(time.time() * 1000) - DAY_MS}-0"

    # Acknowledged and older than 7 days: trimmed by the run that settled it.
    _xadd(rds, org, _record(org, "req_old_0"), old[0])
    result = ms.drain_managed_settlements(rds)
    assert result["trimmed"] == 1
    assert rds.xlen(stream) == 0

    _xadd(rds, org, _record(org, "req_old_1"), old[1])
    _xadd(rds, org, _record(org, "req_old_2"), old[2])
    _xadd(rds, org, _record(org, "req_recent"), recent_id)
    # Another worker read old[1] and died before acknowledging it.
    rds.xreadgroup(ms.GROUP, "dead-worker", {stream: ">"}, count=1)
    new_id = _xadd(rds, org, _record(org, "req_new"))
    result = ms.drain_managed_settlements(rds)

    # old[1] is pending, so nothing from it on is trimmed: not even old[2],
    # acknowledged and just as old.
    assert (result["settled"], result["trimmed"]) == (3, 0)
    assert [i for i, _ in rds.xrange(stream)] == [old[1], old[2], recent_id, new_id]
    assert _pending(rds, org) == 1

    _age_pending(rds, org, old[1], consumer="dead-worker")
    result = ms.drain_managed_settlements(rds)

    assert (result["settled"], result["trimmed"]) == (1, 2)
    assert [i for i, _ in rds.xrange(stream)] == [recent_id, new_id]
    assert _spends(org).count() == 5
    assert _pending(rds, org) == 0


def test_xtrim_never_drops_undelivered_entries(rds, make_org, monkeypatch):
    monkeypatch.setattr(ms, "BATCH", 2)
    org = make_org()
    stream = _outbox(org)
    nine_days_ago = int(time.time() * 1000) - 9 * DAY_MS
    ids = [f"{nine_days_ago + i}-0" for i in range(4)]
    for i, entry_id in enumerate(ids):
        _xadd(rds, org, _record(org, f"req_backlog_{i}"), entry_id)

    # A run out of time after one batch: two backlog entries never delivered.
    result = ms.drain_managed_settlements(rds, deadline_s=0)

    assert result["settled"] == 2
    assert [i for i, _ in rds.xrange(stream)] == ids[2:]

    result = ms.drain_managed_settlements(rds)

    assert result["settled"] == 2
    assert rds.xlen(stream) == 0
    assert _spends(org).count() == 4


# --- the needs-push set -----------------------------------------------------------


def test_needs_push_serviced_creates_zero_account(rds, make_org, gateway):
    org = make_org()
    rds.sadd(ms.NEEDS_PUSH, str(org.pk))
    assert not ManagedCreditAccount.objects.filter(organization=org).exists()

    result = ms.drain_managed_settlements(rds)

    account = ManagedCreditAccount.objects.get(organization=org)
    assert (account.granted_total_micros, account.settled_spend_micros) == (0, 0)
    assert result["pushed"] == 1
    assert not rds.sismember(ms.NEEDS_PUSH, str(org.pk))
    gateway.request.assert_called_once_with(
        "PUT",
        f"/-/orgs/{org.pk}/credits/granted",
        json={
            "granted_total_micros": 0,
            "version": 0,
            "settled_spend_floor_micros": 0,
        },
    )


def test_needs_push_after_this_runs_spend(rds, make_org, gateway):
    org = make_org()
    _xadd(rds, org, _record(org, "req_before_push"))
    rds.sadd(ms.NEEDS_PUSH, str(org.pk))

    ms.drain_managed_settlements(rds)

    # The floor the gateway is pushed includes the spend settled in this run.
    pushed = gateway.request.call_args.kwargs["json"]
    assert pushed["settled_spend_floor_micros"] == GOLDEN_BILLED


def test_needs_push_failure_requeued(rds, make_org, gateway):
    failing, unknown = make_org(), str(uuid4())
    rds.sadd(ms.NEEDS_PUSH, str(failing.pk), unknown)
    gateway.request.side_effect = GatewayClientError("gateway down", status_code=503)

    result = ms.drain_managed_settlements(rds)

    assert (result["pushed"], result["push_failed"]) == (0, 1)
    assert rds.sismember(ms.NEEDS_PUSH, str(failing.pk))
    # An org Django does not have is dropped: the gateway re-adds it on its next 503.
    assert not rds.sismember(ms.NEEDS_PUSH, unknown)

    gateway.request.side_effect = _echo_gateway
    result = ms.drain_managed_settlements(rds)

    assert result["pushed"] == 1
    assert not rds.sismember(ms.NEEDS_PUSH, str(failing.pk))


# --- the schedule ------------------------------------------------------------------


def test_schedule_registered_30s():
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.agentcc_managed import AGENTCC_MANAGED_SCHEDULES

    (schedule,) = [
        s
        for s in AGENTCC_MANAGED_SCHEDULES
        if s.schedule_id == "agentcc-managed-settlement-drain"
    ]
    assert schedule.activity_name == "drain_managed_settlements_activity"
    assert schedule.interval_seconds == 30
    assert schedule in ALL_SCHEDULES
    assert schedule.activity_name in _ACTIVITY_REGISTRY


def test_activity_runs_the_drain():
    from tfc.temporal.schedules import agentcc_managed

    with patch.object(
        ms, "drain_managed_settlements", return_value={"settled": 3}
    ) as drain:
        result = agentcc_managed.drain_managed_settlements_activity._original_func()

    drain.assert_called_once_with()
    assert result == {"settled": 3}


def test_activity_skips_without_ee():
    from tfc.temporal.schedules import agentcc_managed

    with (
        patch.dict(sys.modules, {"ee.usage.services.managed_settlement": None}),
        capture_logs() as logs,
    ):
        result = agentcc_managed.drain_managed_settlements_activity._original_func()

    assert result == {}
    # PLAN 161.1: a billing path that cannot load is loud, never an info line.
    assert [(e["event"], e["log_level"]) for e in logs] == [
        ("managed_settlement_drain_skipped", "warning")
    ]


# --- invariants (PLAN section 4.4) ---------------------------------------------------


def _entry_sum(org, *kinds):
    total = ManagedCreditEntry.objects.filter(
        organization=org, kind__in=kinds
    ).aggregate(total=Sum("amount_micros"))["total"]
    return total or 0


def test_i5_settled_spend_equals_the_spend_entries(rds, make_org, gateway):
    """I5: after redelivery, a crash between commit and ack and a poison
    record, the account's totals are exactly its entries' sums."""
    from ee.usage.services import managed_credits

    org = make_org()
    managed_credits.grant(org, 5_000_000, idempotency_key="g1", actor=None, note="")
    first = _xadd(rds, org, _record(org, "req_i5_a", billed_micros=1_000))
    _xadd(rds, org, _record(org, "req_i5_b", billed_micros=2_500))
    _xadd(rds, org, {"garbage": "poison"})
    with (
        patch.object(ms._Run, "_ack", side_effect=WorkerKilled),
        pytest.raises(WorkerKilled),
    ):
        ms.drain_managed_settlements(rds)
    # The crashed run had read all three entries: every one is pending now.
    pending = rds.xpending_range(_outbox(org), ms.GROUP, min="-", max="+", count=10)
    assert first in [p["message_id"] for p in pending]
    for item in pending:
        _age_pending(rds, org, item["message_id"])
    ms.drain_managed_settlements(rds)
    _xadd(rds, org, _record(org, "req_i5_b", billed_micros=2_500))  # redelivered
    ms.drain_managed_settlements(rds)
    managed_credits.refund(org, "req_i5_a", reason="test", actor=None)

    account = ManagedCreditAccount.objects.get(organization=org)
    credit_kinds = [str(kind) for kind in managed_credits.CREDIT_KINDS]
    assert account.settled_spend_micros == -_entry_sum(org, "spend") == 3_500
    assert account.granted_total_micros == _entry_sum(org, *credit_kinds)
    assert account.granted_total_micros == 5_000_000 + 1_000
    assert _pending(rds, org) == 0


def test_i6_needs_push_clamps_granted_and_floors_spent(rds, make_org, gateway):
    """I6 on the push the drain sends: an org whose credits fell below zero
    (a negative adjustment) is pushed granted 0, never a negative grant, and
    the spent floor is the settled spend including this run's."""
    from ee.usage.services import managed_credits

    org = make_org()
    managed_credits.grant(org, 1_000, idempotency_key="g", actor=None, note="")
    managed_credits.adjust(org, -4_000, idempotency_key="a", reason="t", actor=None)
    _xadd(rds, org, _record(org, "req_i6", billed_micros=700))
    rds.sadd(ms.NEEDS_PUSH, str(org.pk))
    gateway.request.reset_mock()

    ms.drain_managed_settlements(rds)

    account = ManagedCreditAccount.objects.get(organization=org)
    assert account.granted_total_micros == -3_000
    pushed = gateway.request.call_args.kwargs["json"]
    assert pushed == {
        "granted_total_micros": 0,
        "version": account.version,
        "settled_spend_floor_micros": 700,
    }


def test_i10_markup_drift_is_settled_and_flagged(rds, make_org):
    """I10: every SPEND entry's markup_bps equals the catalog markup (5% =
    500 bps). The money was already charged at the gateway, so a spend at
    another markup is still settled (refusing it would break I5 and I6),
    and it is counted and logged as an error for the R4 reconciler."""
    org = make_org()
    _xadd(rds, org, _record(org, "req_markup_ok"))
    _xadd(rds, org, _record(org, "req_markup_bad", markup_bps=700))
    _xadd(rds, org, _record(org, "req_markup_missing", markup_bps=""))

    with capture_logs() as logs:
        result = ms.drain_managed_settlements(rds)

    assert (result["settled"], result["markup_drift"]) == (3, 2)
    drift = [e for e in logs if e["event"] == "managed_settlement_markup_drift"]
    assert sorted(e["request_id"] for e in drift) == [
        "req_markup_bad",
        "req_markup_missing",
    ]
    assert all(e["log_level"] == "error" for e in drift)
    assert all(e["expected_bps"] == 500 for e in drift)
    assert ms.expected_markup_bps() == 500
    assert _settled_spend(org) == 3 * GOLDEN_BILLED


@override_settings(AGENTCC_MANAGED_MARKUP_BPS=650)
def test_i10_expected_markup_follows_the_catalog_setting(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_markup_650", markup_bps=650))

    result = ms.drain_managed_settlements(rds)

    assert (result["settled"], result["markup_drift"]) == (1, 0)


def test_i10_redelivered_spend_is_not_counted_twice(rds, make_org):
    org = make_org()
    _xadd(rds, org, _record(org, "req_markup_twice", markup_bps=700))
    first = ms.drain_managed_settlements(rds)
    _xadd(rds, org, _record(org, "req_markup_twice", markup_bps=700))
    second = ms.drain_managed_settlements(rds)

    assert (first["markup_drift"], second["markup_drift"]) == (1, 0)

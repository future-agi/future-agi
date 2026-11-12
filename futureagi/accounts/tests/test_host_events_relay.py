"""READINESS 1.2a on Postgres: the relay retries from the outbox until EE accepts, never sends a
row twice at once, never lets a process that cannot send hold rows, and delivers within a second of
commit through the signal fast path. The HTTP tests talk to a real local server that checks the signature.
"""

import hashlib
import hmac
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone

from accounts.host_events import config as channel
from accounts.host_events import relay
from accounts.host_events.signing import SIGNATURE_HEADER
from accounts.host_events.transport import send_batch
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.host_events_support import (
    ReadyConfig,
    RecordingEE,
    make_tenants,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(user, organization, workspace):
    return make_tenants(user, organization, workspace)


def _remove(person):
    OrganizationMembership.no_workspace_objects.filter(user=person).update(
        is_active=False
    )


def test_ee_down_keeps_the_row_and_backs_off_then_delivers_once_ee_is_back(
    tenants,
):
    _remove(tenants["alice"])
    ee = RecordingEE(fail=["ee_unreachable", "ee_timeout"])
    relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    row = HostEventOutbox.objects.get()
    assert (row.status, row.attempts, row.last_error) == (
        HostEventStatus.PENDING,
        1,
        "ee_unreachable",
    )
    first_retry = row.next_attempt_at
    assert (
        timedelta(seconds=0.5) < first_retry - timezone.now() < timedelta(seconds=1.5)
    )
    frozen = row.event
    # Not due yet: nothing is sent.
    relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    assert ee.calls == 1
    HostEventOutbox.objects.update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    row.refresh_from_db()
    assert (row.attempts, row.last_error) == (2, "ee_timeout")
    assert row.next_attempt_at - timezone.now() > timedelta(seconds=1.5)
    HostEventOutbox.objects.update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    row.refresh_from_db()
    assert row.status == HostEventStatus.DELIVERED and row.last_error == ""
    # Every retry sent the same event: same id, same revocation instant.
    assert (
        row.event == frozen
        and len(ee.events) == 1
        and ee.events[0]["id"] == frozen["events"][0]["id"]
    )


def test_backoff_is_exponential_and_capped_at_five_minutes():
    for attempts, low, high in (
        (1, 0.8, 1.2),
        (3, 3.2, 4.8),
        (9, 204.8, 300),
        (40, 240, 300),
    ):
        assert low <= relay.backoff(attempts) <= high


def test_a_refused_event_stays_pending_while_the_rest_of_the_batch_is_delivered(
    tenants,
):
    _remove(tenants["alice"])
    _remove(tenants["bob"])
    rows = list(HostEventOutbox.objects.order_by("id"))
    refused = str(rows[0].event_id)
    ee = RecordingEE(refuse_ids={refused})
    relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    statuses = {
        str(row.event_id): (row.status, row.last_error)
        for row in HostEventOutbox.objects.all()
    }
    assert statuses[refused] == (HostEventStatus.PENDING, "ee_event_refused")
    assert [status for key, status in statuses.items() if key != refused] == [
        (HostEventStatus.DELIVERED, "")
    ]


def test_a_live_lease_is_never_claimed_twice_and_a_lapsed_one_is(tenants):
    _remove(tenants["alice"])
    now = timezone.now()
    claimed = relay._claim(10, now)
    assert len(claimed) == 1
    assert relay._claim(10, now + timedelta(seconds=5)) == []
    # The drain that took it died: after the 30 s lease another drain takes it over.
    assert [
        row.id for row in relay._claim(10, now + relay.LEASE + timedelta(seconds=1))
    ] == [claimed[0].id]


def test_a_process_that_cannot_send_claims_nothing(tenants):
    _remove(tenants["alice"])
    ee = RecordingEE()
    for state in (
        channel.EventChannelConfig(state=channel.DISABLED),
        channel.EventChannelConfig(state=channel.MISCONFIGURED, problem="url_missing"),
    ):
        stats = relay.drain(config=state, send=ee.send)
        assert stats.get("claimed", 0) == 0
    row = HostEventOutbox.objects.get()
    assert (row.status, row.attempts) == (HostEventStatus.PENDING, 0)
    assert ee.calls == 0


def test_purge_removes_only_old_settled_rows_unless_the_channel_is_off(
    tenants,
):
    _remove(tenants["alice"])
    _remove(tenants["bob"])
    old = timezone.now() - relay.RETENTION - timedelta(hours=1)
    first, second = HostEventOutbox.objects.order_by("id")
    HostEventOutbox.objects.filter(id=first.id).update(
        status=HostEventStatus.DELIVERED, captured_at=old
    )
    HostEventOutbox.objects.filter(id=second.id).update(captured_at=old)
    assert relay.purge() == 1
    assert list(HostEventOutbox.objects.values_list("id", flat=True)) == [second.id]
    assert relay.purge(include_pending=True) == 1


def test_large_user_deactivations_split_and_batches_stay_under_the_body_limit():
    events = relay._split(
        {
            "type": "user.deactivated",
            "subject": "u",
            "organizationId": None,
            "workspaceId": None,
            "role": None,
            "organizations": [f"org-{index}" for index in range(1201)],
        },
        "base",
        1,
    )
    assert [len(event["organizations"]) for event in events] == [500, 500, 201]
    assert [event["id"] for event in events] == ["base.0", "base.1", "base.2"]


class _SigningEE(BaseHTTPRequestHandler):
    """A real HTTP receiver that verifies the FA signature the way EE does."""

    secret = b"t" * 40
    seen = []
    mode = "ok"

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        parts = dict(
            item.split("=", 1)
            for item in self.headers.get(SIGNATURE_HEADER, "").split(",")
        )
        expected = hmac.new(
            self.secret, f"{parts.get('t')}.".encode() + body, hashlib.sha256
        ).hexdigest()
        if parts.get("kid") != "fa-1" or not hmac.compare_digest(
            expected, parts.get("v1", "")
        ):
            self._answer(401, {"error": "signature_invalid"})
            return
        if self.mode == "slow":
            time.sleep(3)
        events = json.loads(body)["events"]
        type(self).seen.extend(events)
        self._answer(
            200,
            {
                "results": [
                    {"id": event["id"], "outcome": "applied"} for event in events
                ],
                "receivedAtMs": int(time.time() * 1000),
            },
        )

    def _answer(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def signing_ee():
    _SigningEE.seen, _SigningEE.mode = [], "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SigningEE)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_real_http_delivery_is_signed_and_a_wrong_secret_is_refused(
    tenants, signing_ee
):
    url = f"http://127.0.0.1:{signing_ee.server_address[1]}/host/events"
    _remove(tenants["alice"])
    wrong = channel.EventChannelConfig(
        state=channel.READY,
        issuer="https://futureagi.test",
        url=url,
        key_id="fa-1",
        secret=b"w" * 40,
    )
    relay.drain(config=wrong, purge_old=False)
    assert HostEventOutbox.objects.get().last_error == "ee_refused_signature"
    HostEventOutbox.objects.update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    relay.drain(config=ReadyConfig(url=url), purge_old=False)
    row = HostEventOutbox.objects.get()
    assert row.status == HostEventStatus.DELIVERED
    assert [event["subject"] for event in _SigningEE.seen] == [str(tenants["alice"].id)]
    assert row.ee_received_at_ms >= int(row.sent_at.timestamp() * 1000) - 1000


def test_a_slow_ee_is_a_timeout_not_a_hang(signing_ee):
    _SigningEE.mode = "slow"
    url = f"http://127.0.0.1:{signing_ee.server_address[1]}/host/events"
    started = time.monotonic()
    result = send_batch(
        ReadyConfig(url=url), b'{"events":[]}', ["x"], timeout=(0.5, 0.5)
    )
    assert result.error == "ee_timeout"
    assert time.monotonic() - started < 2.0
    closed = send_batch(ReadyConfig(url="http://127.0.0.1:9/host/events"), b"{}", ["x"])
    assert closed.error == "ee_unreachable"


@pytest.mark.django_db(transaction=True)
def test_a_save_path_removal_is_delivered_within_a_second_of_commit(
    tenants, signing_ee
):
    url = f"http://127.0.0.1:{signing_ee.server_address[1]}/host/events"
    with patch.object(relay, "current_config", return_value=ReadyConfig(url=url)):
        membership = OrganizationMembership.no_workspace_objects.get(
            user=tenants["alice"]
        )
        committed = time.monotonic()
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        while not _SigningEE.seen and time.monotonic() - committed < 5:
            time.sleep(0.02)
        elapsed = time.monotonic() - committed
    assert [event["type"] for event in _SigningEE.seen] == ["membership.removed"]
    assert elapsed < 1.0, elapsed
    connection.close()


def test_the_drain_runs_every_3_s_where_omega_is_configured_and_hourly_where_not():
    import importlib

    from tfc.temporal.schedules import omega_host_events

    with patch.dict("os.environ", {"OMEGA_SQUAD_DELEGATION_ISSUER": "https://fa.test"}):
        configured = importlib.reload(omega_host_events).OMEGA_HOST_EVENT_SCHEDULES[0]
    with patch.dict("os.environ", {}, clear=True):
        idle = importlib.reload(omega_host_events).OMEGA_HOST_EVENT_SCHEDULES[0]
    importlib.reload(omega_host_events)
    assert (configured.interval_seconds, idle.interval_seconds) == (3, 3600)
    assert configured.activity_name == "drain_omega_host_events"
    assert configured.queue == "tasks_s"

"""READINESS lane 1.2a, L06 across both systems: Future AGI's real removal path (endpoint, Postgres
capture trigger, outbox, relay, HMAC over HTTP) against a real Omega EE server (just-in-time host
identity, the host event receiver, a guarded workspace stream).

- Removing a member in tenant A ends that member's open EE stream within one re-check of delivery;
  tenant B's stream stays open.
- With the channel blocked (EE unreachable), the event stays in the outbox, the stream ends at the
  cap and never later, and once the channel is back the retry delivers the event.

Opt-in, because it needs the EE checkout: set ``OMEGA_EE_SERVER_DIR`` to ``<omega-ee>/packages/server``
(with dependencies installed) and put Node 22 on PATH. ``OMEGA_HOST_EVENTS_REAL_TIMING=1`` uses the
production 30 s re-check (the cap test then keeps the scaled cap). ``OMEGA_HOST_EVENTS_RECEIPT`` names a
JSON file the receipt is written to.
"""

import json
import os
import queue
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.host_events import relay
from accounts.host_events.config import READY, EventChannelConfig
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus
from accounts.tests.host_events_support import make_tenants

EE_DIR = os.environ.get("OMEGA_EE_SERVER_DIR", "")
REAL = os.environ.get("OMEGA_HOST_EVENTS_REAL_TIMING") == "1"

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not EE_DIR, reason="needs OMEGA_EE_SERVER_DIR (the Omega EE server package)"
    ),
]


@pytest.fixture
def tenants(user, organization, workspace):
    return make_tenants(user, organization, workspace)


class Harness:
    def __init__(self, streams, recheck_ms, max_ms):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/tmp"),
            "HARNESS_STREAMS": json.dumps(streams),
            "HARNESS_RECHECK_MS": str(recheck_ms),
            "HARNESS_MAX_MS": str(max_ms),
        }
        self.process = subprocess.Popen(
            [
                str(Path(EE_DIR) / "node_modules" / ".bin" / "tsx"),
                "test-support/host-events-interop.harness.ts",
            ],
            cwd=EE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.ready = self.wait(lambda line: line.get("ready"), timeout=90)

    def _read(self):
        for raw in self.process.stdout:
            try:
                self.lines.put(json.loads(raw))
            except ValueError:
                continue

    def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if predicate(line):
                return line
        return None

    def config(self, url=None):
        return EventChannelConfig(
            state=READY,
            issuer=self.ready["issuer"],
            url=url or self.ready["eventsUrl"],
            key_id=self.ready["keyId"],
            secret=self.ready["secret"].encode(),
        )

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _streams(tenants):
    return [
        {
            "label": "alice",
            "sub": str(tenants["alice"].id),
            "org": str(tenants["a"].id),
            "workspace": str(tenants["a_main"].id),
            "role": "member",
        },
        {
            "label": "bob",
            "sub": str(tenants["bob"].id),
            "org": str(tenants["b"].id),
            "workspace": str(tenants["b_main"].id),
            "role": "member",
        },
    ]


def _receipt(name, payload):
    target = os.environ.get("OMEGA_HOST_EVENTS_RECEIPT")
    if not target:
        return
    path = Path(target)
    existing = json.loads(path.read_text()) if path.exists() else {}
    existing[name] = payload
    path.write_text(json.dumps(existing, indent=2, sort_keys=True))


def test_removing_a_member_in_fa_ends_their_open_ee_stream_and_not_tenant_bs(
    auth_client, tenants
):
    recheck_ms = 30_000 if REAL else 1_000
    harness = Harness(_streams(tenants), recheck_ms, 15 * 60_000)
    try:
        assert harness.ready, "the EE harness did not start"
        time.sleep(2.5 if not REAL else 50)  # past a key's life in the real run (45 s)
        response = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(tenants["alice"].id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        stats = relay.drain(config=harness.config(), purge_old=False)
        assert stats["delivered"] == 1, stats
        ended = harness.wait(
            lambda line: line.get("streamEnded"), timeout=recheck_ms / 1000 + 10
        )
        assert ended is not None and ended["label"] == "alice", ended
        bob_ended = harness.wait(lambda line: line.get("streamEnded"), timeout=1.5)
        assert bob_ended is None, "tenant B's stream must stay open"
        row = HostEventOutbox.objects.get(status=HostEventStatus.DELIVERED)
        sent_ms = int(row.sent_at.timestamp() * 1000)
        receipt = {
            "real_timing": REAL,
            "recheck_ms": recheck_ms,
            # The API call's own latency (EE licensing checks in the test settings) is not part of revocation;
            # the change is effective from its commit, which the capture trigger dates.
            "removal_committed_at_ms": int(row.captured_at.timestamp() * 1000),
            "event_sent_at_ms": sent_ms,
            "ee_received_at_ms": row.ee_received_at_ms,
            "stream_ended_at_ms": ended["endedAtMs"],
            "event_sent_to_stream_end_ms": ended["endedAtMs"] - sent_ms,
            "removal_committed_to_stream_end_ms": ended["endedAtMs"]
            - int(row.captured_at.timestamp() * 1000),
            "tenant_b_stream_open": True,
        }
        _receipt("removal_ends_stream", receipt)
        # The bound: one re-check after EE has the event. FA's drain adds at most its 3 s schedule interval.
        assert receipt["event_sent_to_stream_end_ms"] <= recheck_ms + 2_000, receipt
    finally:
        harness.stop()


def test_with_the_channel_blocked_the_stream_ends_at_the_cap_and_the_retry_delivers_later(
    auth_client, tenants
):
    cap_ms = 8_000
    harness = Harness(_streams(tenants)[:1], 500, cap_ms)
    try:
        assert harness.ready, "the EE harness did not start"
        opened = harness.ready["streams"][0]["openedAtMs"]
        response = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(tenants["alice"].id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        blocked = harness.config(url="http://127.0.0.1:9/host/events")
        stats = relay.drain(config=blocked, purge_old=False)
        assert stats.get("delivered", 0) == 0 and stats["retrying"] == 1, stats
        ended = harness.wait(
            lambda line: line.get("streamEnded"), timeout=cap_ms / 1000 + 10
        )
        assert ended is not None
        age = ended["endedAtMs"] - opened
        assert cap_ms - 1_500 <= age <= cap_ms + 2_000, age
        pending = HostEventOutbox.objects.get(status=HostEventStatus.PENDING)
        assert pending.last_error == "ee_unreachable"
        # The channel is back: the outbox's retry delivers the same event.
        HostEventOutbox.objects.filter(id=pending.id).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        assert relay.drain(config=harness.config(), purge_old=False)["delivered"] == 1
        applied = harness.wait(lambda line: line.get("applied"), timeout=5)
        assert applied and applied["applied"]["id"] == str(pending.event_id)
        _receipt(
            "blocked_channel_cap",
            {
                "cap_ms": cap_ms,
                "stream_age_at_end_ms": age,
                "attempts_before_delivery": 2,
                "first_error": "ee_unreachable",
            },
        )
    finally:
        harness.stop()

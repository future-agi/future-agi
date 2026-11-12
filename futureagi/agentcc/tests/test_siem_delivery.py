"""SIEM export delivery (G16-SIEM): what reaches an organization's collector.

Each test runs a real loopback receiver and the product's own delivery
command, so the assertions read the bytes that crossed the wire. A webhook is
signed the Standard Webhooks way; an OTLP/HTTP collector receives OTLP JSON
traces (a SERVER span and, for a dispatched call, its CLIENT child) and logs.
Every export is metadata only: closed, identifier-shaped scalars, never the
call's arguments, result, error text, raw session or user identity, whatever
the org's capture policy. Retries back off, a client error or the last
attempt dead-letters, and an admin sees, retries or discards dead letters.
"""

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.management import call_command
from django.utils import timezone

from accounts.models.audit_log import AuditLog
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.org_config_writer import write_config_version
from agentcc.tests.siem_receiver import RECEIVER_BODY, Receiver, admit_loopback
from agentcc.tests.test_mcp_credentials import _member_client
from agentcc.tests.test_mcp_tool_calls_api import tool_call_entry

pytestmark = pytest.mark.integration

DESTINATIONS = "/agentcc/siem/destinations/"
DELIVERIES = "/agentcc/siem/deliveries/"
CONTENT_KEYS = ("arguments", "result", "error_message", "evidence", "user_id")


@pytest.fixture(autouse=True)
def export_on(settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True


def delivery_model():
    return apps.get_model("prism", "AgentccSIEMDelivery")


def drain():
    call_command("agentcc_siem_deliver")


def make_due():
    delivery_model().objects.filter(status="pending").update(
        next_retry_at=timezone.now() - timedelta(seconds=1)
    )


def add_destination(client, receiver, monkeypatch, **changes):
    admit_loopback(monkeypatch, receiver)
    body = {
        "name": "SOC",
        "kind": "webhook",
        "url": receiver.url + "/hooks",
        "streams": ["audit_events"],
    }
    body.update(changes)
    created = client.post(DESTINATIONS, body, format="json")
    assert created.status_code == 201, created.content
    result = created.json()["result"]
    enabled = client.patch(
        f"{DESTINATIONS}{result['id']}/", {"enabled": True}, format="json"
    )
    assert enabled.status_code == 200, enabled.content
    return result


def canaries():
    marks = {name: f"canary-{name}-{uuid.uuid4().hex}" for name in CONTENT_KEYS}
    marks["key_value"] = "sk-" + uuid.uuid4().hex
    return marks


def ingest_call(organization, marks=None, **changes):
    marks = marks or canaries()
    record = tool_call_entry(
        organization,
        event_id=changes.pop("event_id", f"tc_{uuid.uuid4().hex[:20]}"),
        content_mode="full",
        arguments={"query": marks["arguments"], "api_key": marks["key_value"]},
        result={"content": [{"type": "text", "text": marks["result"]}]},
        error_message=marks["error_message"],
        user_id=marks["user_id"],
        identity={"sponsor": {"user_id": marks["evidence"]}},
        **changes,
    )
    record["tool_call_id"] = record["event_id"]
    record["agent"] = {**record["agent"], "session_id": marks["evidence"] + "-session"}
    ingest_request_logs([record])
    return record, marks


def consent_full_capture(organization, user):
    write_config_version(
        organization,
        {"privacy": {"trace_content": True, "content_mode": "full"}},
        user=user,
        push=lambda config: True,
    )


def verify_standard_webhook(request, secret):
    headers = request["headers"]
    signed = b".".join(
        [
            headers["webhook-id"].encode(),
            headers["webhook-timestamp"].encode(),
            request["body"],
        ]
    )
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    assert f"v1,{expected}" in headers["webhook-signature"].split(" ")


def test_webhook_receives_signed_metadata_only_audit_events(
    auth_client, organization, user, monkeypatch
):
    consent_full_capture(organization, user)
    with Receiver() as receiver:
        destination = add_destination(auth_client, receiver, monkeypatch)
        record, marks = ingest_call(organization)
        drain()
    assert len(receiver.requests) == 1
    request = receiver.requests[0]
    assert request["path"] == "/hooks"
    verify_standard_webhook(request, destination["signing_secret"])
    raw = request["body"].decode()
    for mark in marks.values():
        assert mark not in raw
    payload = json.loads(raw)
    assert payload["schema"] == "agentcc.siem.v1"
    assert payload["stream"] == "audit_events"
    assert payload["organization_id"] == str(organization.id)
    [event] = payload["events"]
    assert event["event_type"] == "mcp.tool_call"
    assert event["event_id"] == record["event_id"]
    assert event["tool"] == "fagi.web_search"
    assert event["outcome"] == "success"
    assert event["latency_ms"] == 412
    assert event["content_mode"] == "full"
    for key in CONTENT_KEYS + ("session_id", "client_id", "guardrail"):
        assert key not in event


def test_scalar_dimensions_are_closed(auth_client, organization, monkeypatch):
    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch, streams=["spans"])
        ingest_call(
            organization,
            tool="lookup Bearer abc.def",
            upstream_tool='say "hi"; drop',
            agent={"agent_id": "agent with spaces", "run_id": "run_ok-1"},
        )
        drain()
    raw = receiver.bodies().decode()
    assert "Bearer" not in raw and "drop" not in raw and "spaces" not in raw
    assert "[redacted]" in raw
    assert "run_ok-1" in raw


def test_otlp_collector_receives_server_and_client_spans_and_logs(
    auth_client, organization, monkeypatch
):
    header_value = "Bearer " + uuid.uuid4().hex
    with Receiver() as receiver:
        add_destination(
            auth_client,
            receiver,
            monkeypatch,
            kind="otlp_http",
            url=receiver.url,
            streams=["spans", "audit_events"],
            auth_header="Authorization",
            auth_value=header_value,
        )
        record, marks = ingest_call(organization)
        drain()
    by_path = {request["path"]: request for request in receiver.requests}
    assert set(by_path) == {"/v1/traces", "/v1/logs"}
    for request in by_path.values():
        assert request["headers"]["content-type"] == "application/json"
        assert request["headers"]["authorization"] == header_value
        for mark in marks.values():
            assert mark.encode() not in request["body"]

    traces = json.loads(by_path["/v1/traces"]["body"])
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    server = next(s for s in spans if s["kind"] == 2)
    client = next(s for s in spans if s["kind"] == 3)
    assert len(spans) == 2
    assert len(server["traceId"]) == 32 and len(server["spanId"]) == 16
    assert client["traceId"] == server["traceId"]
    assert client["parentSpanId"] == server["spanId"]
    assert "parentSpanId" not in server or server["parentSpanId"] == ""
    attrs = {a["key"]: a["value"] for a in server["attributes"]}
    assert attrs["gen_ai.operation.name"] == {"stringValue": "execute_tool"}
    assert attrs["gen_ai.tool.name"] == {"stringValue": "search"}
    assert attrs["gen_ai.tool.call.id"] == {"stringValue": record["event_id"]}
    assert attrs["mcp.outcome"] == {"stringValue": "success"}
    assert attrs["mcp.billed_cost_micros"] == {"intValue": "5250"}
    assert "gen_ai.tool.call.arguments" not in attrs
    start, end = int(server["startTimeUnixNano"]), int(server["endTimeUnixNano"])
    assert end - start == 412 * 1_000_000
    child = int(client["endTimeUnixNano"]) - int(client["startTimeUnixNano"])
    assert child == 389 * 1_000_000
    assert (
        start <= int(client["startTimeUnixNano"])
        and int(client["endTimeUnixNano"]) <= end
    )

    logs = json.loads(by_path["/v1/logs"]["body"])
    [log] = logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert log["traceId"] == server["traceId"] and log["spanId"] == server["spanId"]
    log_attrs = {a["key"]: a["value"] for a in log["attributes"]}
    assert log_attrs["agentcc.event_id"] == {"stringValue": record["event_id"]}


def test_a_refused_call_exports_one_server_span(auth_client, organization, monkeypatch):
    with Receiver() as receiver:
        add_destination(
            auth_client,
            receiver,
            monkeypatch,
            kind="otlp_http",
            url=receiver.url,
            streams=["spans"],
        )
        ingest_call(
            organization,
            outcome="denied",
            dispatched=False,
            error_class="denied",
            error_code="tool_denied",
            upstream_latency_ms=0,
        )
        drain()
    spans = json.loads(receiver.requests[0]["body"])["resourceSpans"][0]["scopeSpans"][
        0
    ]["spans"]
    assert [s["kind"] for s in spans] == [2]
    assert spans[0]["status"]["code"] == 2


def test_server_errors_back_off_then_dead_letter_for_admins(
    auth_client, organization, workspace, monkeypatch
):
    with Receiver(statuses=[503] * 10) as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        before = timezone.now()
        drain()
        [row] = delivery_model().objects.all()
        assert row.status == "pending" and row.attempts == 1
        assert row.next_retry_at >= before + timedelta(seconds=29)
        drain()  # not due yet: no second request
        assert len(receiver.requests) == 1
        for _ in range(10):
            make_due()
            drain()
    row.refresh_from_db()
    assert row.status == "dead_letter"
    assert row.attempts == row.max_attempts == len(receiver.requests)
    assert row.last_response_code == 503

    member = _member_client(organization, workspace)
    assert member.get(DELIVERIES, {"status": "dead_letter"}).status_code == 200
    assert (
        member.post(f"{DELIVERIES}{row.id}/retry/", {}, format="json").status_code
        == 403
    )
    assert (
        member.post(f"{DELIVERIES}{row.id}/discard/", {}, format="json").status_code
        == 403
    )
    listed = auth_client.get(DELIVERIES, {"status": "dead_letter"})
    assert listed.status_code == 200, listed.content
    [shown] = listed.json()["result"]["deliveries"]
    assert shown["id"] == str(row.id) and shown["last_error"] == "HTTP 503"
    assert shown["event_count"] == 1
    assert RECEIVER_BODY.decode() not in listed.content.decode()
    assert RECEIVER_BODY.decode() not in (row.last_error or "")


@pytest.mark.parametrize("status", [400, 302])
def test_client_errors_and_redirects_dead_letter_at_once(
    auth_client, organization, monkeypatch, status
):
    with Receiver(statuses=[status]) as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        drain()
        make_due()
        drain()
    rows = list(delivery_model().objects.all())
    assert [(r.status, r.attempts, r.last_response_code) for r in rows] == [
        ("dead_letter", 1, status)
    ]
    assert len(receiver.requests) == 1


def test_rate_limits_honor_retry_after(auth_client, organization, monkeypatch):
    with Receiver(statuses=[429], retry_after=120) as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        before = timezone.now()
        drain()
    [row] = delivery_model().objects.all()
    assert row.status == "pending" and row.next_retry_at >= before + timedelta(
        seconds=119
    )


def test_admin_retries_and_discards_dead_letters_audited(
    auth_client, organization, monkeypatch
):
    with Receiver(statuses=[400, 400]) as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        ingest_call(organization)
        drain()
        first, second = delivery_model().objects.order_by("created_at")
        assert (first.status, second.status) == ("dead_letter", "dead_letter")
        retried = auth_client.post(f"{DELIVERIES}{first.id}/retry/")
        assert retried.status_code == 200, retried.content
        assert retried.json()["result"]["status"] == "pending"
        drain()
    first.refresh_from_db()
    assert first.status == "delivered"
    assert len(receiver.requests) == 3
    discarded = auth_client.post(f"{DELIVERIES}{second.id}/discard/")
    assert discarded.status_code == 200, discarded.content
    second.refresh_from_db()
    assert second.status == "discarded"
    for action in ("agentcc.siem_delivery_retried", "agentcc.siem_delivery_discarded"):
        assert (
            AuditLog.objects.filter(organization=organization, action=action).count()
            == 1
        )
    assert auth_client.post(f"{DELIVERIES}{first.id}/retry/").status_code == 409
    assert auth_client.post(f"{DELIVERIES}{second.id}/discard/").status_code == 409


def test_disabled_destination_and_export_switch_send_nothing(
    auth_client, organization, settings, monkeypatch
):
    with Receiver() as receiver:
        destination = add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        disabled = auth_client.patch(
            f"{DESTINATIONS}{destination['id']}/", {"enabled": False}, format="json"
        )
        assert disabled.status_code == 200
        drain()
        ingest_call(organization)
        drain()
        assert receiver.requests == []
        assert delivery_model().objects.count() == 1
        settings.AGENTCC_SIEM_EXPORT_ENABLED = False
        apps.get_model("prism", "AgentccSIEMDestination").objects.update(enabled=True)
        ingest_call(organization)
        drain()
        assert receiver.requests == []
    assert delivery_model().objects.count() == 1


def test_export_rebuilds_from_current_rows(auth_client, organization, monkeypatch):
    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        record, _ = ingest_call(organization)
        apps.get_model("prism", "AgentccToolCall").objects.filter(
            event_id=record["event_id"]
        ).delete()
        drain()
    assert receiver.requests == []
    [row] = delivery_model().objects.all()
    assert row.status == "delivered" and row.event_count == 0


def test_unexpected_failures_are_bounded_attempts(
    auth_client, organization, monkeypatch
):
    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        with patch(
            "agentcc.services.siem_signing.headers", side_effect=RuntimeError("boom")
        ):
            drain()
            [row] = delivery_model().objects.all()
            assert (row.status, row.attempts, row.last_error) == (
                "pending",
                1,
                "RuntimeError",
            )
            for _ in range(10):
                make_due()
                drain()
    row.refresh_from_db()
    assert row.status == "dead_letter" and row.attempts == row.max_attempts
    assert receiver.requests == []


def test_ingestion_survives_an_export_queue_failure(organization):
    from agentcc.services import mcp_tool_call_ingestion

    record = tool_call_entry(organization, event_id="tc-queue-failure")
    with patch(
        "agentcc.services.siem_delivery.enqueue", side_effect=RuntimeError("queue down")
    ):
        assert mcp_tool_call_ingestion.ingest([record]) == 1
    assert (
        apps.get_model("prism", "AgentccToolCall")
        .objects.filter(event_id="tc-queue-failure")
        .exists()
    )


def test_other_org_deliveries_are_invisible(auth_client, organization, monkeypatch):
    from agentcc.tests.test_mcp_credentials import _org_with_owner
    from agentcc.tests.test_siem_destinations_api import owner_client

    other_org, other_owner = _org_with_owner("SIEM delivery other org")
    other = owner_client(other_org, other_owner)
    with Receiver(statuses=[400]) as receiver:
        add_destination(other, receiver, monkeypatch)
        ingest_call(other_org)
        drain()
    [foreign] = delivery_model().objects.filter(organization=other_org)
    assert auth_client.get(DELIVERIES).json()["result"]["deliveries"] == []
    assert auth_client.post(f"{DELIVERIES}{foreign.id}/retry/").status_code == 404
    assert auth_client.post(f"{DELIVERIES}{foreign.id}/discard/").status_code == 404


def test_a_lapsed_lease_records_nothing(auth_client, organization, monkeypatch):
    """A worker whose lease another worker took over writes no result."""
    from agentcc.services import siem_transport

    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        [row] = delivery_model().objects.all()
        theirs = timezone.now() + timedelta(minutes=5)
        admit = siem_transport.check_destination_url

        def taken_over(url):
            # Another worker leases the delivery while this one is sending.
            delivery_model().objects.filter(id=row.id).update(next_retry_at=theirs)
            admit(url)

        monkeypatch.setattr(siem_transport, "check_destination_url", taken_over)
        drain()
    assert len(receiver.requests) == 1
    row.refresh_from_db()
    assert (row.status, row.attempts, row.next_retry_at) == ("pending", 0, theirs)
    destination = apps.get_model("prism", "AgentccSIEMDestination").objects.get()
    assert destination.last_delivered_at is None


class _SlowReceiver:
    """A loopback receiver that answers one header byte at a time, slowly."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}"
        self.served = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(self.seconds + 5)
            conn.recv(65536)
            self.served += 1
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            deadline = time.monotonic() + self.seconds
            try:
                while time.monotonic() < deadline:
                    conn.sendall(b"X")
                    time.sleep(0.2)
                conn.sendall(b"-Slow: 1\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.close()
        self.thread.join(self.seconds + 6)


def test_a_slow_receiver_is_cut_off_at_the_deadline(
    auth_client, organization, monkeypatch
):
    from agentcc.services import siem_delivery

    monkeypatch.setattr(siem_delivery, "DELIVERY_DEADLINE_SECONDS", 1, raising=False)
    monkeypatch.setattr(siem_delivery, "REQUEST_TIMEOUT", (2, 2))
    with _SlowReceiver(seconds=6) as slow:
        add_destination(auth_client, slow, monkeypatch)
        ingest_call(organization)
        started = time.monotonic()
        drain()
        took = time.monotonic() - started
    assert slow.served == 1
    assert took < 4, took
    [row] = delivery_model().objects.all()
    assert (row.status, row.attempts, row.last_error) == (
        "pending",
        1,
        "deadline_exceeded",
    )


def test_a_webhook_without_a_signing_secret_is_dead_lettered_unsent(
    auth_client, organization, monkeypatch
):
    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        apps.get_model("prism", "AgentccSIEMDestination").objects.update(
            encrypted_secret=None, secret_hint=""
        )
        ingest_call(organization)
        drain()
    assert receiver.requests == []
    [row] = delivery_model().objects.all()
    assert (row.status, row.attempts, row.last_error) == (
        "dead_letter",
        1,
        "signing_secret_missing",
    )

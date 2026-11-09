"""WT-06 and WT-06b (DESIGN.md sections 6.2 and 17.1) with a test DNS server as the worker's own resolver.

The worker here is the production send path end to end: the real dispatcher loop on Postgres, the real
``SystemResolver`` (``getaddrinfo`` on the absolute name), the real address rules and the real pinned transport. Nothing
is substituted. What makes the answers scriptable is where the test runs: in a container whose ``/etc/resolv.conf``
names only 127.0.0.1, where this test's DNS server listens (and ``WEBHOOKS_LIVE_DNS=1`` is set). So the
name ``hooks.acme.ai`` resolves through the system resolver to whatever the test's table says at that moment.

- WT-06: the name first answers the receiver's address (the event is delivered: the setup works), then 127.0.0.1,
  then 169.254.169.254, then 127.0.0.1 again: those attempts are ``ssrf_blocked``, the canaries see no connection, and
  after the third the endpoint is off with ``unsafe_address`` and its admins get the email, which names no address.
- WT-06b: with ``FI_WEBHOOKS_PRIVATE_CIDRS`` admitting ``64:ff9b::/96``, an AAAA answer of ``64:ff9b::a9fe:a9fe``
  (NAT64 of 169.254.169.254), then the 6to4 and the IPv4-compatible forms of 127.0.0.1, are each ``ssrf_blocked``.
- WT-29c: a name whose server never answers ends ``dns_error`` within the 2 s resolution budget.

**Canaries:** a TCP listener on 127.0.0.1 at the endpoint's port counts every connection, and the ``socket.connect``
audit hook records every address this process connects to (169.254.169.254 cannot be bound without the network
admin capability, so the hook is its canary). The rows pass only if both see none.

Outside that container this test skips (its resolver is not the test's), and the gate fails on the skip. The harness
says it is that container with ``WEBHOOKS_LIVE_DNS=1``; a local ``resolv.conf`` alone is not enough, since a cloud
VM's own one also names only 127.0.0.1 (the VM's resolver, where port 53 is taken and not ours to bind).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler

import pytest
from django.core import mail
from django.db import close_old_connections, transaction

from webhooks import config, notices
from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher
from webhooks.models import Attempt, Delivery, Endpoint
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests import evidence
from webhooks.tests.api_support import EP, client_for
from webhooks.tests.factories import T0, label_event_data, use_test_keys
from webhooks.tests.receiver import Server
from webhooks.transport import PinnedTransport, SystemClock, SystemResolver

pytestmark = pytest.mark.django_db(transaction=True)

SCALE = 0.01  # the retries of the three blocked attempts come 50 ms and 300 ms apart
NEVER = ("127.0.0.1", "169.254.169.254")


def _resolver_is_local() -> bool:
    """True only in the DNS harness's container: it sets ``WEBHOOKS_LIVE_DNS=1`` and mounts a ``resolv.conf`` that
    names only 127.0.0.1, where this test's DNS server listens."""
    if os.environ.get("WEBHOOKS_LIVE_DNS") != "1":
        return False
    try:
        with open("/etc/resolv.conf") as f:
            servers = [
                line.split()[1]
                for line in f
                if line.strip().startswith("nameserver") and len(line.split()) > 1
            ]
    except OSError:
        return False
    return servers == ["127.0.0.1"]


class TestDns:
    """A DNS server on 127.0.0.1:53 (UDP) answering A and AAAA from ``table``: name -> list of answers, each a list of
    addresses (IPv4 for A, IPv6 for AAAA). A name moves to its next answer once both its A and its AAAA question were
    asked (one ``getaddrinfo`` asks both), so each attempt sees the next one. Unknown names get NXDOMAIN. TTL 0."""

    def __init__(self) -> None:
        self.table: dict[str, list[list[str]]] = {}
        self.turn: dict[str, int] = {}
        self.asked: dict[str, set] = {}
        self.silent: set[str] = set()
        self.queries: list[tuple[str, int]] = []
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 53))
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def answers(self, name: str, *rounds: list[str]) -> None:
        with self.lock:
            self.table[name.lower().rstrip(".")] = [list(r) for r in rounds]
            self.turn[name.lower().rstrip(".")] = 0
            self.asked[name.lower().rstrip(".")] = set()

    def _serve(self) -> None:
        self.sock.settimeout(0.5)
        while not self.stop.is_set():
            try:
                q, addr = self.sock.recvfrom(1500)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                reply = self._answer(q)
                if reply is not None:
                    self.sock.sendto(reply, addr)
            except Exception:  # noqa: BLE001 - a malformed query is dropped
                continue

    def _answer(self, q: bytes) -> bytes | None:
        (tid, _flags, qd, _an, _ns, _ar) = struct.unpack(">HHHHHH", q[:12])
        i, labels = 12, []
        while q[i]:
            labels.append(q[i + 1 : i + 1 + q[i]].decode("ascii").lower())
            i += 1 + q[i]
        qname = ".".join(labels)
        qtype, qclass = struct.unpack(">HH", q[i + 1 : i + 5])
        question = q[12 : i + 5]
        with self.lock:
            self.queries.append((qname, qtype))
            if (
                qname in self.silent
            ):  # an authoritative server that never answers (WT-29c)
                return None
            rounds = self.table.get(qname)
            if rounds is None:
                return struct.pack(">HHHHHH", tid, 0x8183, 1, 0, 0, 0) + question
            n = self.turn[qname]
            current = rounds[min(n, len(rounds) - 1)]
            asked = self.asked[qname]
            asked.add(qtype)
            if {
                1,
                28,
            } <= asked:  # this lookup asked both: the next one gets the next answer
                self.turn[qname] = n + 1
                asked.clear()
        rrs = b""
        count = 0
        for ip in current:
            addr = ipaddress.ip_address(ip)
            if (qtype, addr.version) in ((1, 4), (28, 6)):
                rdata = addr.packed
                rrs += (
                    b"\xc0\x0c" + struct.pack(">HHIH", qtype, 1, 0, len(rdata)) + rdata
                )
                count += 1
        return struct.pack(">HHHHHH", tid, 0x8180, 1, count, 0, 0) + question + rrs

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


class Ok(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        Ok.seen.append({"path": self.path, "body": body, "at": time.monotonic()})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class Canary:
    """A listener that counts every connection made to it."""

    def __init__(self, host: str, port: int) -> None:
        self.hits = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(16)
        self.sock.settimeout(0.5)
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                c, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.hits += 1
            c.close()

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


_CONNECTS: list = []
_AUDIT = threading.Event()


def _audit(event, args):
    if event == "socket.connect" and _AUDIT.is_set():
        _CONNECTS.append(args[1])


def _own_address() -> str:
    """This container's address on its network (no packet is sent: a UDP connect only picks the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


@pytest.fixture
def stack(monkeypatch, organization, workspace, user):
    if not _resolver_is_local():
        pytest.skip(
            "WT-06 runs where the test DNS server is the system resolver (see the module docstring)"
        )
    use_test_keys(monkeypatch)
    me = _own_address()
    receiver = Server((me, 0), Ok)
    port = receiver.server_address[1]
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    canary = Canary("127.0.0.1", port)
    dns = TestDns()
    net = ipaddress.ip_network(f"{me}/16", strict=False)
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true")
    monkeypatch.setenv("FI_WEBHOOKS_PRIVATE_CIDRS", f"{net},64:ff9b::/96")
    monkeypatch.setenv("FI_WEBHOOKS_PRIVATE_PORTS", str(port))
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    monkeypatch.delenv("FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS", raising=False)
    Ok.seen = []
    if not getattr(sys, "_wt06_audit", False):
        sys.addaudithook(_audit)
        sys._wt06_audit = True
    owner = client_for(user, organization, workspace)
    yield {
        "me": me,
        "port": port,
        "dns": dns,
        "canary": canary,
        "owner": owner,
        "org": organization,
        "ws": workspace,
    }
    dns.close()
    canary.close()
    receiver.shutdown()
    receiver.server_close()


def _dispatcher() -> Dispatcher:
    """The production adapters (``webhooks.temporal.activities.build_dispatcher`` builds the same), at ``SCALE``."""
    sender = Sender(
        config.profile(),
        config.public_suffix,
        SystemResolver(),
        PinnedTransport(extra_ca_file=config.extra_ca_file()),
        SystemClock(),
    )
    return Dispatcher(
        OrmStore(scale=SCALE),
        sender,
        SystemClock(),
        scale=SCALE,
        after_send=close_old_connections,
    )


def _run_until(d: Dispatcher, done, timeout_s: float) -> bool:
    stop = threading.Event()

    def loop():
        try:
            d.run(stop)
        finally:
            close_old_connections()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    end = time.monotonic() + timeout_s
    ok = False
    while time.monotonic() < end:
        if done():
            ok = True
            break
        time.sleep(0.1)
    stop.set()
    t.join(60)
    return ok


def _pub(s, key: str) -> None:
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=s["org"].id,
            workspace_id=s["ws"].id,
            data=label_event_data(),
            source_key=key,
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )


def _create(s, host: str, name: str) -> str:
    r = s["owner"].post(
        EP,
        {
            "name": name,
            "url": f"http://{host}:{s['port']}/hook",
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    return r.json()["result"]["endpoint"]["id"]


def _blocked_run(s, ep_id: str, key: str) -> dict:
    """One event to ``ep_id`` while its name answers forbidden addresses, until the endpoint is off; then the off
    notice. Returns what the row records."""
    _AUDIT.set()
    _CONNECTS.clear()
    hits_before = s["canary"].hits
    try:
        _pub(s, key)
        off = _run_until(
            _dispatcher(),
            lambda: Endpoint.objects.filter(id=ep_id, status="disabled_auto").exists(),
            60,
        )
    finally:
        _AUDIT.clear()
    connected = sorted({str(a[0]) for a in _CONNECTS if isinstance(a, tuple)})
    ep = Endpoint.objects.get(id=ep_id)
    dl = Delivery.objects.filter(endpoint_id=ep_id, event__source_key=key).get()
    atts = list(Attempt.objects.filter(delivery=dl).order_by("n"))
    mail.outbox = []
    sent = notices.send_turned_off(datetime.now(UTC) + timedelta(seconds=1))
    log = s["owner"].get("/webhooks/deliveries/", {"endpoint": ep_id}).content.decode()
    detail = s["owner"].get(f"/webhooks/deliveries/{dl.id}/").content.decode()
    return {
        "off": off,
        "endpoint": (ep.status, ep.disabled_reason),
        "attempts": [(a.n, a.outcome, a.resolved_ip) for a in atts],
        "error_details": [a.error_detail for a in atts],
        "staff_details": [a.staff_detail for a in atts],
        "connected_to": connected,
        "canary_hits": s["canary"].hits - hits_before,
        "emails": [(m.subject, m.to, m.body) for m in mail.outbox],
        "notices_sent": sent,
        "api_text": log + detail,
    }


def test_wt06_dns_rebinding_to_loopback_and_metadata_is_blocked_then_turned_off(stack):
    s = stack
    dns = s["dns"]
    dns.answers("hooks.acme.ai", [s["me"]])
    ep_id = _create(s, "hooks.acme.ai", "rebind")
    # The name answers the receiver: the event is delivered through the system resolver and the pinned transport.
    _pub(s, "wt06-ok")
    assert _run_until(
        _dispatcher(),
        lambda: Delivery.objects.filter(endpoint_id=ep_id, status="succeeded").exists(),
        30,
    ), list(
        Attempt.objects.filter(delivery__endpoint_id=ep_id).values_list(
            "outcome", "staff_detail"
        )
    )
    assert len(Ok.seen) == 1
    # Rebinding: loopback, the metadata address, loopback again.
    dns.answers("hooks.acme.ai", ["127.0.0.1"], ["169.254.169.254"], ["127.0.0.1"])
    got = _blocked_run(s, ep_id, "wt06-rebind")
    rec = {k: v for k, v in got.items() if k not in ("api_text", "emails")}
    rec["emails"] = [(subj, len(to)) for subj, to, _b in got["emails"]]
    evidence.write(
        "WT-06",
        receiver_address_class="private, admitted by FI_WEBHOOKS_PRIVATE_CIDRS",
        dns_queries=dns.queries[-12:],
        **rec,
    )
    assert got["off"], got["attempts"]
    assert got["endpoint"] == ("disabled_auto", "unsafe_address")
    assert [(n, o) for n, o, _ip in got["attempts"]] == [
        (1, "ssrf_blocked"),
        (2, "ssrf_blocked"),
        (3, "ssrf_blocked"),
    ]
    assert all(ip is None for _n, _o, ip in got["attempts"])
    assert got["canary_hits"] == 0
    assert not [a for a in got["connected_to"] if a in NEVER], got["connected_to"]
    assert len(Ok.seen) == 1  # nothing more reached the receiver either
    # The admins' email, and nothing a tenant sees, names a non-public address.
    assert got["notices_sent"] == 1 and len(got["emails"]) == 1
    subject, to, body = got["emails"][0]
    assert subject == "Webhook turned off: rebind" and to
    tenant_text = body + got["api_text"] + " ".join(got["error_details"])
    for addr in (*NEVER, s["me"]):
        assert addr not in tenant_text, addr
    # The address is kept for staff only.
    assert any("127.0.0.1" in d or "169.254.169.254" in d for d in got["staff_details"])


def test_wt06b_embedded_ipv4_answers_are_blocked_even_inside_an_admitted_prefix(stack):
    s = stack
    dns = s["dns"]
    dns.answers(
        "nat64.acme.ai",
        [
            "64:ff9b::a9fe:a9fe"
        ],  # NAT64 of 169.254.169.254, inside the admitted 64:ff9b::/96
        ["2002:7f00:1::"],  # 6to4 of 127.0.0.1
        ["::127.0.0.1"],  # IPv4-compatible 127.0.0.1
    )
    ep_id = _create(s, "nat64.acme.ai", "nat64")
    got = _blocked_run(s, ep_id, "wt06b")
    rec = {k: v for k, v in got.items() if k not in ("api_text", "emails")}
    rec["emails"] = [(subj, len(to)) for subj, to, _b in got["emails"]]
    evidence.write(
        "WT-06b",
        private_cidrs="<the container network>,64:ff9b::/96",
        dns_queries=dns.queries[-12:],
        **rec,
    )
    assert got["off"], got["attempts"]
    assert got["endpoint"] == ("disabled_auto", "unsafe_address")
    assert [o for _n, o, _ip in got["attempts"]] == ["ssrf_blocked"] * 3
    assert got["canary_hits"] == 0
    blocked = {"64:ff9b::a9fe:a9fe", "2002:7f00:1::", "::127.0.0.1", *NEVER}
    assert not [a for a in got["connected_to"] if a in blocked], got["connected_to"]
    tenant_text = got["emails"][0][2] + got["api_text"] + " ".join(got["error_details"])
    for addr in ("64:ff9b", "2002:7f00", "::127.0.0.1", "169.254.169.254"):
        assert addr not in tenant_text, addr


def test_wt29c_a_dns_black_hole_is_dns_error_within_2_seconds(stack):
    s = stack
    s["dns"].silent.add("blackhole.acme.ai")
    ep_id = _create(s, "blackhole.acme.ai", "blackhole")
    _pub(s, "wt29c")
    assert _run_until(
        _dispatcher(),
        lambda: Attempt.objects.filter(delivery__endpoint_id=ep_id).exists(),
        30,
    )
    att = Attempt.objects.filter(delivery__endpoint_id=ep_id).order_by("n").first()
    asked = [q for q in s["dns"].queries if q[0] == "blackhole.acme.ai"]
    evidence.write(
        "WT-29c",
        outcome=att.outcome,
        duration_ms=att.duration_ms,
        dns_ms=att.dns_ms,
        queries_unanswered=len(asked),
    )
    assert att.outcome == "dns_error"
    assert att.duration_ms <= 2_500, att.duration_ms
    assert asked, "the worker never asked the test DNS server"
    assert Delivery.objects.get(endpoint_id=ep_id).status == "retrying"

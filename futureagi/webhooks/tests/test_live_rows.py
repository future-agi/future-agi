"""Section 17.2 rows run live: the real dispatcher loop on Postgres, the real signing and pinned
transport, and a real receiver on 127.0.0.1, for the rows that had only core tests with fakes or schedule assertions.

- WT-02 and WT-03: the published ``standardwebhooks`` library verifies 1,000 of FA's own deliveries across the 4 event
  types; a changed body byte, another delivery's headers and a ``t`` 301 s old are refused by the library, the 10-line
  recipe and the core's ``verify``.
- WT-07: a 302 to an internal address is ``redirect_refused``, the Location is never connected to, and the delivery
  retries.
- WT-08 (real time) and WT-09 (``scale`` 0.001): the arrival gaps, the same ids on every attempt, and how each
  delivery ends; WT-09's gaps also hold the row's literal window. WT-08b (scaled): the rejected class, 413, and a
  rejected answer followed by a 503.
- WT-09b (``scale`` 0.001): a receiver that answers 500 to everything. Section 7.7's 24 h rule turns the endpoint off
  (``disabled_auto``, ``failing``); a delivery mid-retry, one queued behind the endpoint's one slot, the one in flight
  and one published after the turn-off are all paused with their sequence numbers, never failed, skipped or removed,
  and no request reaches the receiver after the turn-off.
- WT-13: a worker process is SIGKILLed after its request is written and before it settles: ``worker_lost``, the same
  ``webhook-id`` again after the lease, and a receiver deduping on it processes the event once.
- WT-13b: a sender process is SIGSTOPped past its lease, another worker sends and settles, then SIGCONT: the late
  result is a ``superseded`` attempt and the delivery and the endpoint's health are unchanged.
- WT-14: 500 events to one endpoint with ``max_in_flight=4``: sequences 1-500 with no gap or duplicate, and arrivals
  out of sequence order at least once.
- WT-29 and WT-29b over HTTPS: a 50 MB answer read to the 4 KB cap, a 20 s sleep and a 1 byte/s header trickle each
  cut at the 15 s deadline.
- WT-05b at send: on hosted settings a host that became one of ours is refused by the per-attempt URL check.

**The seams** are the ones every live webhooks test uses (``receiver.real_dispatcher``): the resolver answers a public
address for the endpoint's name and the transport connects to the receiver's port on 127.0.0.1 (loopback is never
admitted by the address rules). The dispatcher's idle poll is 1 s (``dispatch.POLL_S``) and it has no timer for a
retry that comes due, so each gap is checked against the delivery's own jittered delay, never early, and at most one
poll late. Each row writes its evidence (``webhooks/.evidence/<row>.json``).

WT-08, WT-09 and WT-09b run for minutes, so they are opt-in like WT-30 (``-m stress``); the integration gate runs
them.
WT-02 needs the ``standardwebhooks`` library, which FA does not depend on: the gate passes a Python that has it in
``WEBHOOKS_SW_PYTHON`` (without one, and without the library here, the test skips and the gate fails on the skip).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import multiprocessing as mp
import os
import random
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler

import pytest
from django.db import close_old_connections, connections, transaction

from webhooks.core import schedule
from webhooks.core.dispatch import POLL_S
from webhooks.core.signing import new_secret, verify
from webhooks.models import Attempt, Delivery, Endpoint, EndpointState
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Server, real_dispatcher

pytestmark = pytest.mark.django_db(transaction=True)

FORK = mp.get_context("fork")
LATE_S = POLL_S + 0.75
"""How late an attempt may start after it came due: one idle poll, plus the pass and the connect."""
PAYLOADS = os.path.join(
    os.path.dirname(__file__), "..", "core", "conformance", "payloads.json"
)


# ---- the receiver ----------------------------------------------------------------------------------------------------


class Script:
    """A receiver on 127.0.0.1 that answers each path from a script, in order (the last answer repeats). An answer is
    ``(status, headers, body)``, ``"hold"`` (keep the request open until :meth:`release`, then answer
    ``release_status``), ``"jitter"`` (200 after 0-30 ms) or ``"seq-fail"`` (503 for the first attempt of every
    sequence number that is 7 modulo 50, else 200). Every request is recorded with its monotonic arrival time, path,
    lower-cased headers and raw body. ``"500-hold-once"`` answers 500 to every request, and holds open the first one
    that arrives at or after ``hold_from`` (monotonic) until :meth:`release`, then answers it 500 too; that request is
    ``held_request``."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.scripts: dict[str, list] = {}
        self.held = threading.Event()
        self._release = threading.Event()
        self.release_status = 500
        self.hold_from: float | None = None
        self.held_request: dict | None = None
        self.rnd = random.Random(17)
        self.t0 = time.monotonic()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                arrived = time.monotonic()
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                path = self.path.split("?")[0]
                seq_header = self.headers.get("X-FutureAGI-Sequence")
                with outer.lock:
                    i = sum(1 for r in outer.requests if r["path"] == path)
                    seq_seen = sum(
                        1
                        for r in outer.requests
                        if r["path"] == path
                        and r["headers"].get("x-futureagi-sequence") == seq_header
                    )
                    outer.requests.append(
                        {
                            "at": arrived,
                            "path": path,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body,
                        }
                    )
                    script = outer.scripts.get(path) or [(200, {}, b"ok")]
                    answer = script[min(i, len(script) - 1)]
                if answer == "hold":
                    outer.held.set()
                    outer._release.wait(120)
                    answer = (outer.release_status, {}, b"late")
                elif answer == "500-hold-once":
                    with outer.lock:
                        hold = (
                            outer.hold_from is not None
                            and arrived >= outer.hold_from
                            and outer.held_request is None
                        )
                        if hold:
                            outer.held_request = outer.requests[-1]
                    if hold:
                        outer.held.set()
                        outer._release.wait(120)
                    answer = (500, {}, b"no")
                elif answer == "jitter":
                    time.sleep(outer.rnd.random() * 0.03)
                    answer = (200, {}, b"ok")
                elif isinstance(answer, str) and answer.startswith("fail-prompt:"):
                    # 500 for the events of one prompt, 200 for every other event.
                    prompt = json.loads(body)["data"]["prompt"]["id"]
                    bad = prompt == answer.split(":", 1)[1]
                    answer = (500, {}, b"no") if bad else (200, {}, b"ok")
                elif answer == "seq-fail":
                    # The first attempt of every sequence number that is 7 modulo 50 gets a 503: it is retried
                    # 5 s later, after higher sequence numbers have arrived.
                    seq = int(seq_header or "0")
                    answer = (
                        (503, {}, b"later")
                        if seq % 50 == 7 and seq_seen == 0
                        else (200, {}, b"ok")
                    )
                status, headers, payload = answer
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # the sender is gone (a killed worker)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://hooks.acme.ai:{self.port}{path}"

    def on(self, path: str) -> list[dict]:
        with self.lock:
            return [r for r in self.requests if r["path"] == path]

    def release(self) -> None:
        self._release.set()

    def close(self) -> None:
        self._release.set()
        self.server.shutdown()
        self.server.server_close()


class _Port:
    """What ``real_dispatcher`` needs of a receiver in another process: its port."""

    def __init__(self, port: int) -> None:
        self.port = port


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def script():
    r = Script()
    yield r
    r.close()


@contextlib.contextmanager
def running(d):
    """The dispatcher's real loop on a thread while the block runs; drained after."""
    stop = threading.Event()

    def loop():
        try:
            d.run(stop)
        finally:
            close_old_connections()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield d
    finally:
        stop.set()
        t.join(90)


def wait_for(predicate, timeout_s: float, step_s: float = 0.1):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step_s)
    return predicate()


def pub(org, ws, key: str, *, type: str = "prompt.label.changed", data=None):  # noqa: A002
    with transaction.atomic():
        return publish(
            type=type,
            organization_id=org.id,
            workspace_id=ws.id,
            data=data or label_event_data(),
            source_key=key,
            source_hash="h",
            producer="test",
            occurred_at=T0,
        ).event_id


def attempts(dl) -> list[tuple[str, int]]:
    return list(
        Attempt.objects.filter(delivery_id=dl.id)
        .order_by("started_at", "n")
        .values_list("outcome", "n")
    )


def gaps(reqs) -> list[float]:
    ats = [r["at"] for r in reqs]
    return [round(b - a, 3) for a, b in zip(ats, ats[1:], strict=False)]


def check_gaps(delivery_id: str, observed: list[float], klass_of, scale: float) -> list:
    """Each gap against the delivery's own jittered delay (``schedule.jitter_permille`` seeded with its id): never
    early, at most ``LATE_S`` late, and the delay itself within [0.8, 1.2] x the section 7.3 base. ``klass_of(n)`` is
    the class whose table gives attempt n's delay. Returns the rows for the evidence."""
    rows = []
    for i, gap in enumerate(observed):
        n = i + 2
        table = (
            schedule.STANDARD_DELAYS_S
            if klass_of(n) == "standard"
            else schedule.REJECTED_DELAYS_S
        )
        base_s = table[n] * scale
        permille = schedule.jitter_permille(delivery_id, n)
        due_s = schedule._scaled(table[n] * permille, scale) / 1000
        rows.append({"attempt": n, "base_s": base_s, "due_s": due_s, "gap_s": gap})
        assert 0.8 * base_s - 0.002 <= due_s <= 1.2 * base_s + 0.002, rows[-1]
        assert due_s - 0.05 <= gap <= due_s + LATE_S, rows[-1]
    return rows


def check_row_window(observed: list[float], klass_of, scale: float) -> list:
    """Each gap within the row's literal window (WT-09's amended text): [0.8, 1.2] x the section 7.3 base,
    plus at most one idle poll (``dispatch.POLL_S``), whatever the delivery's own jitter was. Returns the rows."""
    rows = []
    for i, gap in enumerate(observed):
        n = i + 2
        table = (
            schedule.STANDARD_DELAYS_S
            if klass_of(n) == "standard"
            else schedule.REJECTED_DELAYS_S
        )
        base_s = table[n] * scale
        lo, hi = 0.8 * base_s, 1.2 * base_s + POLL_S
        rows.append(
            {"attempt": n, "window_s": [round(lo, 4), round(hi, 4)], "gap_s": gap}
        )
        assert lo - 1e-9 <= gap <= hi + 1e-9, rows[-1]
    return rows


def same_ids(reqs) -> dict:
    keys = ("webhook-id", "x-futureagi-delivery", "x-futureagi-sequence")
    ids = {k: {r["headers"].get(k) for r in reqs} for k in keys}
    assert all(len(v) == 1 and None not in v for v in ids.values()), ids
    assert [int(r["headers"]["x-futureagi-attempt"]) for r in reqs] == list(
        range(1, len(reqs) + 1)
    )
    return {k: next(iter(v)) for k, v in ids.items()}


# ---- WT-02 and WT-03 -------------------------------------------------------------------------------------------------

_SW_VERIFY = r"""
import base64, datetime as dt, json, sys
import standardwebhooks.webhooks as m
from standardwebhooks.webhooks import Webhook, WebhookVerificationError

secret = open(sys.argv[2]).read().strip()
rows = [json.loads(line) for line in open(sys.argv[1])]
wh = Webhook(secret)
out = {"library": __import__("importlib.metadata").metadata.version("standardwebhooks"), "verified": 0,
       "per_type": {}, "tampered_refused": 0, "other_headers_refused": 0, "stale_refused": 0, "errors": []}

def refused(body, headers):
    try:
        wh.verify(body, headers)
        return False
    except WebhookVerificationError:
        return True

for i, r in enumerate(rows):
    body, h = base64.b64decode(r["body"]), r["headers"]
    try:
        wh.verify(body, h)
        out["verified"] += 1
        out["per_type"][r["type"]] = out["per_type"].get(r["type"], 0) + 1
    except WebhookVerificationError as e:
        out["errors"].append(f"{h['webhook-id']}: {e}")
    out["tampered_refused"] += refused(body[:-2] + bytes([body[-2] ^ 1]) + body[-1:], h)
    other = rows[(i + 1) % len(rows)]["headers"]
    out["other_headers_refused"] += refused(body, other)

# A t 301 s old: the same deliveries, verified with the library's clock 301 s later.
real = m.datetime
class Later(real):
    @classmethod
    def now(cls, tz=None):
        return real.now(tz) + dt.timedelta(seconds=301)
m.datetime = Later
for r in rows:
    out["stale_refused"] += refused(base64.b64decode(r["body"]), r["headers"])
m.datetime = real
print(json.dumps(out))
"""


def _sw_python() -> str | None:
    given = os.environ.get("WEBHOOKS_SW_PYTHON")
    if given:
        return given
    try:
        import standardwebhooks  # noqa: F401
    except ImportError:
        return None
    return sys.executable


def _ten_lines(secret: str, headers, body: bytes, now: float) -> bool:
    """DESIGN.md section 5.4 recipe 3, with the clock passed in."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    msg_id, ts = headers["webhook-id"], headers["webhook-timestamp"]
    if abs(now - int(ts)) > 300:
        return False
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return any(
        s.startswith("v1,") and hmac.compare_digest(s[3:], expected)
        for s in headers["webhook-signature"].split(" ")
    )


def _type_data() -> dict[str, dict]:
    """One conformance build case per event type: its built body's ``data`` (payloads.json, section 22.2)."""
    with open(PAYLOADS) as f:
        cases = json.load(f)["build"]
    out: dict[str, dict] = {}
    for c in cases:
        body = json.loads(base64.b64decode(c["expected"]["body_bytes_b64"]))
        out.setdefault(body["type"], body["data"])
    return out


def test_wt02_wt03_standardwebhooks_verifies_1000_of_fas_own_deliveries(
    keys, script, organization, workspace, tmp_path
):
    sw = _sw_python()
    if sw is None:
        pytest.skip(
            "WT-02 needs the standardwebhooks library: set WEBHOOKS_SW_PYTHON (the integration gate does)"
        )
    data = _type_data()
    assert sorted(data) == sorted(
        [
            "prompt.version.created",
            "prompt.label.changed",
            "deployment.run.completed",
            "deployment.run.failed",
        ]
    )
    secret = new_secret()
    ep = make_endpoint(
        organization,
        workspace,
        url=script.url("/wt02"),
        event_types=("prompt.*", "deployment.run.*"),
        filters={"statuses": ["failed", "interrupted", "cancelled"]},
        secret=secret,
        rate_limit_per_s=100,
        max_in_flight=16,
    )
    types = sorted(data)
    for i in range(1000):
        pub(
            organization,
            workspace,
            f"wt02-{i}",
            type=types[i % 4],
            data=data[types[i % 4]],
        )
    with running(real_dispatcher(script)):
        assert wait_for(
            lambda: (
                Delivery.objects.filter(endpoint=ep, status="succeeded").count() == 1000
            ),
            120,
            0.5,
        ), Delivery.objects.filter(endpoint=ep).exclude(status="succeeded").count()
    reqs = script.on("/wt02")
    assert len(reqs) == 1000
    rows = [
        {
            "type": json.loads(r["body"])["type"],
            "headers": {
                k: r["headers"][k]
                for k in ("webhook-id", "webhook-timestamp", "webhook-signature")
            },
            "body": base64.b64encode(r["body"]).decode(),
        }
        for r in reqs
    ]
    feed, secret_file = tmp_path / "deliveries.jsonl", tmp_path / "secret"
    feed.write_text("".join(json.dumps(r) + "\n" for r in rows))
    secret_file.write_text(secret)
    secret_file.chmod(0o600)
    script_file = tmp_path / "sw_verify.py"
    script_file.write_text(_SW_VERIFY)
    done = subprocess.run(
        [sw, "-I", str(script_file), str(feed), str(secret_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    lib = json.loads(done.stdout)
    # The other two verifiers, in this process, on the same requests.
    now = time.time()
    ours = {"ten_lines": 0, "core": 0, "refused_by_both": 0}
    for i, r in enumerate(reqs):
        body, h = r["body"], r["headers"]
        ours["ten_lines"] += _ten_lines(secret, h, body, now)
        ours["core"] += verify(secret, h, body, int(now)) == "ok"
        bad = body[:-2] + bytes([body[-2] ^ 1]) + body[-1:]
        other = reqs[(i + 1) % len(reqs)]["headers"]
        refused = [
            not _ten_lines(secret, h, bad, now),
            verify(secret, h, bad, int(now)) != "ok",
            not _ten_lines(secret, other, body, now),
            verify(secret, other, body, int(now)) != "ok",
            not _ten_lines(secret, h, body, now + 301),
            verify(secret, h, body, int(now) + 301) != "ok",
        ]
        ours["refused_by_both"] += all(refused)
    evidence.write(
        "WT-02",
        deliveries=len(reqs),
        library=lib["library"],
        library_verified=lib["verified"],
        per_type=lib["per_type"],
        ten_lines_verified=ours["ten_lines"],
        core_verified=ours["core"],
        receiver=evidence.receiver_lines(script, limit=8),
    )
    evidence.write(
        "WT-03",
        deliveries=len(reqs),
        library_refused={
            k: lib[k]
            for k in ("tampered_refused", "other_headers_refused", "stale_refused")
        },
        ten_lines_and_core_refused_all_three=ours["refused_by_both"],
    )
    assert lib["errors"] == [], lib["errors"][:5]
    assert lib["verified"] == 1000
    assert lib["per_type"] == dict.fromkeys(types, 250)
    assert (
        lib["tampered_refused"],
        lib["other_headers_refused"],
        lib["stale_refused"],
    ) == (
        1000,
        1000,
        1000,
    )
    assert (ours["ten_lines"], ours["core"], ours["refused_by_both"]) == (
        1000,
        1000,
        1000,
    )


# ---- WT-07 ----------------------------------------------------------------------------------------------------------

_CONNECTS: list = []
_AUDIT = threading.Event()


def _audit(event, args):
    if event == "socket.connect" and _AUDIT.is_set():
        _CONNECTS.append(args[1])


sys.addaudithook(_audit)


@contextlib.contextmanager
def connects():
    """Every address this process connects a socket to while the block runs (``socket.connect`` audit events)."""
    _CONNECTS.clear()
    _AUDIT.set()
    try:
        yield _CONNECTS
    finally:
        _AUDIT.clear()


def test_wt07_a_redirect_to_an_internal_address_is_refused_never_followed_and_retried(
    keys, script, organization, workspace
):
    script.scripts["/wt07"] = [
        (302, {"Location": "https://10.0.0.5/internal"}, b""),
        (200, {}, b"ok"),
    ]
    ep = make_endpoint(organization, workspace, url=script.url("/wt07"))
    pub(organization, workspace, "wt07")
    with connects() as seen, running(real_dispatcher(script)):
        assert wait_for(
            lambda: Delivery.objects.filter(endpoint=ep, status="succeeded").exists(),
            30,
        )
    dl = Delivery.objects.get(endpoint=ep)
    first = Attempt.objects.get(delivery=dl, n=1)
    hosts = sorted({str(a[0]) for a in seen if isinstance(a, tuple)})
    evidence.write(
        "WT-07",
        attempts=attempts(dl),
        first_error_detail=first.error_detail,
        connected_to=hosts,
        receiver=evidence.receiver_lines(script),
    )
    assert attempts(dl) == [("redirect_refused", 1), ("success", 2)]
    assert first.response_status == 302
    assert "10.0.0.5" not in first.error_detail
    assert "127.0.0.1" in hosts, hosts  # the hook saw the deliveries' connects
    assert "10.0.0.5" not in hosts, hosts  # the Location was never connected to
    assert len(script.on("/wt07")) == 2


# ---- WT-08, WT-08b, WT-09 -------------------------------------------------------------------------------------------


@pytest.mark.stress
def test_wt08_retries_backoff_and_jitter_in_real_time(
    keys, script, organization, workspace
):
    script.scripts["/wt08"] = [(500, {}, b"no")] * 3 + [(200, {}, b"ok")]
    ep = make_endpoint(organization, workspace, url=script.url("/wt08"))
    pub(organization, workspace, "wt08")
    with running(real_dispatcher(script)):
        assert wait_for(
            lambda: Delivery.objects.filter(endpoint=ep, status="succeeded").exists(),
            240,
            0.5,
        )
    dl = Delivery.objects.get(endpoint=ep)
    reqs = script.on("/wt08")
    g = gaps(reqs)
    windows = [(4, 6), (24, 36), (96, 144)]
    evidence.write(
        "WT-08",
        scale=1.0,
        attempts=attempts(dl),
        gaps_s=g,
        row_windows_s=windows,
        within_row_windows=[
            lo <= x <= hi for x, (lo, hi) in zip(g, windows, strict=False)
        ],
        receiver=evidence.receiver_lines(script),
    )
    assert attempts(dl) == [
        ("http_error", 1),
        ("http_error", 2),
        ("http_error", 3),
        ("success", 4),
    ]
    ids = same_ids(reqs)
    assert (
        ids["x-futureagi-delivery"] == dl.id
        and int(ids["x-futureagi-sequence"]) == dl.seq
    )
    rows = check_gaps(dl.id, g, lambda n: "standard", 1.0)
    evidence.write("WT-08", gap_checks=rows)


def test_wt08b_the_rejected_class_413_and_a_switch_to_the_standard_schedule(
    keys, script, organization, workspace
):
    scale = 0.001
    script.scripts["/r400"] = [(400, {}, b"bad")]
    script.scripts["/r413"] = [(413, {}, b"too large")]
    script.scripts["/r400-503"] = (
        [(400, {}, b"bad")] + [(503, {}, b"down")] * 5 + [(200, {}, b"ok")]
    )
    eps = {
        p: make_endpoint(organization, workspace, url=script.url(p), name=p)
        for p in ("/r400", "/r413", "/r400-503")
    }
    pub(organization, workspace, "wt08b")
    t0 = time.monotonic()
    with running(real_dispatcher(script, scale=scale)):
        assert wait_for(
            lambda: (
                Delivery.objects.filter(
                    endpoint__in=eps.values(), status__in=("failed", "succeeded")
                ).count()
                == 3
            ),
            90,
            0.2,
        )
    took = time.monotonic() - t0
    dls = {p: Delivery.objects.get(endpoint=ep) for p, ep in eps.items()}
    out = {
        p: {
            "status": d.status,
            "final_outcome": d.final_outcome,
            "attempts": attempts(d),
            "gaps_s": gaps(script.on(p)),
        }
        for p, d in dls.items()
    }
    evidence.write("WT-08b", scale=scale, deliveries=out, took_s=round(took, 2))
    # 400 forever: 5 attempts on the rejected schedule (5 s, 1 min, 10 min, 45 min: about 56 min unscaled), failed.
    r400 = dls["/r400"]
    assert (r400.status, r400.final_outcome) == ("failed", "max_attempts")
    assert attempts(r400) == [("rejected", n) for n in range(1, 6)]
    rows = check_gaps(r400.id, gaps(script.on("/r400")), lambda n: "rejected", scale)
    assert abs(sum(r["due_s"] for r in rows) - 3365 * scale) <= 0.2 * 3365 * scale
    # 413: one attempt, never retried.
    r413 = dls["/r413"]
    assert (r413.status, r413.final_outcome) == ("failed", "not_retried_413")
    assert attempts(r413) == [("rejected", 1)] and len(script.on("/r413")) == 1
    # 400 then 503: the class follows the last answer, so the 503s run on the standard schedule, past the rejected
    # class's 5 attempts, until the 200.
    sw = dls["/r400-503"]
    assert sw.status == "succeeded"
    assert attempts(sw) == [("rejected", 1)] + [
        ("http_error", n) for n in range(2, 7)
    ] + [("success", 7)]
    check_gaps(
        sw.id,
        gaps(script.on("/r400-503")),
        lambda n: "rejected" if n == 2 else "standard",
        scale,
    )


@pytest.mark.stress
def test_wt09_max_age_and_max_attempts_on_the_standard_schedule_scaled(
    keys, script, organization, workspace
):
    """WT-09's amended text: the receiver answers 500 to one event, forever, while the endpoint's other
    events succeed; scale 0.001.

    - On the default 72 h max age the delivery makes the standard schedule's 14 attempts and ends as the core's
      schedule says for its own history: ``max_attempts`` (or ``max_age_exceeded`` if its jitter pushes an attempt past
      72 h first; max attempts is checked first, section 7.3).
    - On a 1 h max age it ends ``max_age_exceeded``.
    - Every gap of both is within the row's literal window, [0.8, 1.2] x the section 7.3 delay plus at most one idle
      poll (``POLL_S``), and never earlier than the delivery's own jittered delay.

    The other events, one every 2 s that the receiver accepts, keep the endpoint's run of consecutive failures short,
    as on an endpoint that refuses only one event. An endpoint that answers 500 to everything is WT-09b."""
    scale = 0.001
    target = "p-wt09"
    script.scripts["/w72"] = [f"fail-prompt:{target}"]
    script.scripts["/w1h"] = [f"fail-prompt:{target}"]
    long_ = make_endpoint(organization, workspace, url=script.url("/w72"), name="72h")
    short = make_endpoint(
        organization, workspace, url=script.url("/w1h"), name="1h", max_age_s=3600
    )
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=organization.id,
            workspace_id=workspace.id,
            data=label_event_data(prompt_id=target),
            source_key="wt09",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )
    stop = threading.Event()

    def companions():
        i = 0
        try:
            while not stop.wait(2.0):
                i += 1
                pub(
                    organization,
                    workspace,
                    f"wt09-ok-{i}",
                    data=label_event_data(prompt_id="p-ok"),
                )
        finally:
            connections.close_all()

    feeder = threading.Thread(target=companions, daemon=True)
    feeder.start()
    try:
        with running(real_dispatcher(script, scale=scale)):
            assert wait_for(
                lambda: (
                    Delivery.objects.filter(
                        endpoint__in=(long_, short),
                        event__source_key="wt09",
                        status="failed",
                    ).count()
                    == 2
                ),
                480,
                0.5,
            ), list(
                Delivery.objects.filter(event__source_key="wt09").values_list(
                    "endpoint__name", "status", "attempts_count"
                )
            )
    finally:
        stop.set()
        feeder.join(30)

    def target_requests(path):
        return [
            r
            for r in script.on(path)
            if json.loads(r["body"])["data"]["prompt"]["id"] == target
        ]

    out = {}
    for name, ep, path in (("72h", long_, "/w72"), ("1h", short, "/w1h")):
        d = Delivery.objects.get(endpoint=ep, event__source_key="wt09")
        reqs = target_requests(path)
        hist = [
            {"settled_at_ms": int(a.settled_at.timestamp() * 1000), "status": 500}
            for a in Attempt.objects.filter(delivery=d).order_by("n")
        ]
        predicted = schedule.next_action(
            {
                "jitter_seed": d.id,
                "created_at_ms": int(d.created_at.timestamp() * 1000),
                "max_age_s": ep.max_age_s,
            },
            hist,
            scale=scale,
        )
        out[name] = {
            "final_outcome": d.final_outcome,
            "attempts": len(reqs),
            "predicted": predicted.get("final_outcome"),
            "gaps_s": gaps(reqs),
            "outcomes": sorted({o for o, _n in attempts(d)}),
        }
    endpoints = {
        name: (ep.status, ep.disabled_reason)
        for name, ep in (
            ("72h", Endpoint.objects.get(id=long_.id)),
            ("1h", Endpoint.objects.get(id=short.id)),
        )
    }
    evidence.write(
        "WT-09",
        scale=scale,
        row_window="each gap within [0.8, 1.2] x the section 7.3 base plus at most POLL_S",
        poll_s=POLL_S,
        companion_events=len(script.on("/w72")) - out["72h"]["attempts"],
        endpoints=endpoints,
        deliveries=out,
    )
    for name, path in (("72h", "/w72"), ("1h", "/w1h")):
        d = Delivery.objects.get(endpoint__name=name, event__source_key="wt09")
        reqs = target_requests(path)
        same_ids(reqs)
        assert out[name]["outcomes"] == ["http_error"], out[name]
        out[name]["row_window_checks"] = check_row_window(
            gaps(reqs), lambda n: "standard", scale
        )
        out[name]["gap_checks"] = check_gaps(
            d.id, gaps(reqs), lambda n: "standard", scale
        )
    evidence.write("WT-09", deliveries=out)
    assert out["72h"]["final_outcome"] == out["72h"]["predicted"]
    if out["72h"]["final_outcome"] == "max_attempts":
        assert out["72h"]["attempts"] == 14
    else:  # this delivery's jitter put an attempt past 72 h before the 14th
        assert out["72h"]["final_outcome"] == "max_age_exceeded"
        assert out["72h"]["attempts"] < 14
    assert out["1h"]["final_outcome"] == "max_age_exceeded" == out["1h"]["predicted"]
    assert out["1h"]["attempts"] < 14
    # The endpoints themselves are not failing as a whole: their other events succeed, so they stay on.
    assert endpoints == {"72h": ("enabled", ""), "1h": ("enabled", "")}, endpoints
    companions_ = Delivery.objects.filter(endpoint__in=(long_, short)).exclude(
        event__source_key="wt09"
    )
    assert companions_.filter(status="succeeded").count() >= 2
    assert not companions_.filter(status__in=("failed", "skipped", "paused")).exists()


def test_wt09_row_window_refuses_a_late_gap_the_jittered_check_allowed():
    """The literal window of WT-09 (``check_row_window``) is not the per-delivery check (``check_gaps``): a gap of the
    jittered delay plus 1.5 s is inside ``check_gaps`` (at most ``LATE_S`` late) but outside the row when the jitter is
    near +20 %, and the row's window must refuse it. A gap below 0.8 x the base is refused too."""
    scale = 0.001
    last = 14
    seed = next(
        f"dl-{i}"
        for i in range(100_000)
        if schedule.jitter_permille(f"dl-{i}", last) >= 1195
    )

    def due_s(n):
        delay = schedule.STANDARD_DELAYS_S[n] * schedule.jitter_permille(seed, n)
        return schedule._scaled(delay, scale) / 1000

    on_time = [round(due_s(n) + 0.01, 3) for n in range(2, last)]
    late = [*on_time, round(due_s(last) + 1.5, 3)]
    check_gaps(
        seed, late, lambda n: "standard", scale
    )  # the jittered check lets it through
    assert late[-1] > 1.2 * schedule.STANDARD_DELAYS_S[last] * scale + POLL_S
    check_row_window(on_time, lambda n: "standard", scale)
    with pytest.raises(AssertionError):
        check_row_window(late, lambda n: "standard", scale)
    early = [0.79 * schedule.STANDARD_DELAYS_S[2] * scale]
    with pytest.raises(AssertionError):
        check_row_window(early, lambda n: "standard", scale)


def _auto_disabled_entries(ep_id):
    from accounts.models.audit_log import AuditLog
    from webhooks.audit import target_uuid

    return list(
        AuditLog.objects.filter(
            scope="webhooks",
            action="webhook.endpoint.auto_disabled",
            target_id=target_uuid(ep_id),
        )
    )


@pytest.mark.stress
def test_wt09b_an_all_500_endpoint_is_turned_off_at_24h_and_its_deliveries_are_paused_never_dropped(
    keys, script, organization, workspace
):
    """WT-09b, scale 0.001 (24 h = 86.4 s): a receiver that answers 500 to everything.

    The endpoint has one slot (``max_in_flight=1``) and two events, A and B, that fail from the start. The receiver
    holds open the first request that arrives within 2 s of the 24 h mark, so the rule fires on that attempt's settle,
    once it is released after the mark. While it is held, event C is published: its delivery is queued behind the
    endpoint's one slot (pending, never attempted), and the other of A and B is mid-retry. After the turn-off event D
    is published. Then the loop runs on for as long as the next retry of every waiting delivery would have taken.

    Asserted: the endpoint is (``disabled_auto``, ``failing``), turned off at the held attempt's settle, more than 24 h
    (scaled) after its run of failures began, with its audit entry; all four deliveries are ``paused`` with the
    sequence number each had (D the next one), none is failed, skipped or removed; and no request reaches the
    receiver after the held one, the attempt the rule fired on."""
    scale = 0.001
    day_s = schedule.AUTO_DISABLE_AGE_S * scale
    script.scripts["/wall"] = ["500-hold-once"]
    ep = make_endpoint(
        organization,
        workspace,
        url=script.url("/wall"),
        name="all-500",
        max_in_flight=1,
    )
    ev = {
        "A": pub(organization, workspace, "wt09b-a"),
        "B": pub(organization, workspace, "wt09b-b"),
    }

    def row(event_id):
        return Delivery.objects.filter(endpoint=ep, event_id=event_id).first()

    def snap(d):
        return {
            "id": d.id,
            "status": d.status,
            "seq": d.seq,
            "attempts": Attempt.objects.filter(delivery=d).count(),
            "next_attempt_at": d.next_attempt_at.isoformat()
            if d.next_attempt_at
            else None,
            "final_outcome": d.final_outcome,
            "skip_reason": d.skip_reason,
        }

    timeline: dict = {}
    with running(real_dispatcher(script, scale=scale)):
        first = wait_for(lambda: script.on("/wall"), 30, 0.05)
        assert first, "no first attempt"
        t_first = first[0]["at"]
        script.hold_from = t_first + day_s - 2.0
        assert script.held.wait(day_s + 120), "no request after the hold mark"
        held = script.held_request
        held_delivery = held["headers"]["x-futureagi-delivery"]
        timeline["held_arrived_s"] = round(held["at"] - t_first, 3)
        # The endpoint's one slot is taken: C's delivery is queued behind it.
        ev["C"] = pub(organization, workspace, "wt09b-c")
        assert wait_for(lambda: row(ev["C"]) is not None, 15, 0.05), (
            "C was not fanned out"
        )
        while time.monotonic() < t_first + day_s + 1.0:
            time.sleep(0.05)
        before = {k: snap(row(e)) for k, e in ev.items()}
        st = EndpointState.objects.get(endpoint=ep)
        timeline["before_release"] = {
            "endpoint": Endpoint.objects.filter(id=ep.id)
            .values_list("status", flat=True)
            .get(),
            "consecutive_failed_attempts": st.consecutive_failed_attempts,
            "in_flight": st.in_flight,
        }
        released_at = time.monotonic()
        timeline["released_s"] = round(released_at - t_first, 3)
        script.release()
        assert wait_for(
            lambda: Endpoint.objects.filter(id=ep.id, status="disabled_auto").exists(),
            30,
            0.05,
        ), "the 24 h rule did not turn the endpoint off"
        assert wait_for(
            lambda: (
                Delivery.objects.filter(endpoint=ep, event_id__in=list(ev.values()))
                .exclude(status="paused")
                .count()
                == 0
            ),
            30,
            0.05,
        ), list(Delivery.objects.filter(endpoint=ep).values_list("status", flat=True))
        ev["D"] = pub(organization, workspace, "wt09b-d")
        assert wait_for(lambda: row(ev["D"]) is not None, 15, 0.05), (
            "D was not fanned out"
        )
        # How long the waiting deliveries would have taken to be sent again, had they not been paused: A or B (the one
        # held) after its next standard delay, the other at its own next_attempt_at, C and D at once.
        held_row = Delivery.objects.get(id=held_delivery)
        hist = [
            {"settled_at_ms": int(a.settled_at.timestamp() * 1000), "status": 500}
            for a in Attempt.objects.filter(delivery=held_row).order_by("n")
        ]
        would = schedule.next_action(
            {
                "jitter_seed": held_row.id,
                "created_at_ms": int(held_row.created_at.timestamp() * 1000),
                "max_age_s": ep.max_age_s,
            },
            hist,
            scale=scale,
        )
        dues = [would.get("retry_at_ms", 0) / 1000] + [
            datetime.fromisoformat(b["next_attempt_at"]).timestamp()
            for b in before.values()
            if b["next_attempt_at"]
        ]
        quiet_until = min(time.time() + 70, max([time.time() + 10, *dues]) + POLL_S + 1)
        timeline["quiet_s"] = round(quiet_until - time.time(), 3)
        while time.time() < quiet_until:
            time.sleep(0.25)

    ep.refresh_from_db()
    st = EndpointState.objects.get(endpoint=ep)
    after = {k: snap(row(e)) for k, e in ev.items()}
    held_attempt = (
        Attempt.objects.filter(delivery_id=held_delivery).order_by("-n").first()
    )
    reqs = script.on("/wall")
    late = [r for r in reqs if r["at"] > held["at"]]
    entries = _auto_disabled_entries(ep.id)
    evidence.write(
        "WT-09b",
        scale=scale,
        scaled={"24 h": f"{day_s} s"},
        endpoint={
            "status": ep.status,
            "disabled_reason": ep.disabled_reason,
            "disabled_at": ep.disabled_at.isoformat() if ep.disabled_at else None,
            "failing_since": st.failing_since.isoformat() if st.failing_since else None,
            "consecutive_failed_attempts": st.consecutive_failed_attempts,
            "paused_count": st.paused_count,
        },
        timeline=timeline,
        held={
            "delivery": held_delivery,
            "outcome": held_attempt.outcome,
            "n": held_attempt.n,
        },
        would_have_been=would.get("action"),
        before_release=before,
        after=after,
        requests_after_the_held_one=len(late),
        audit=[
            {"actor_id": a.actor_id, "reason": a.metadata.get("reason")}
            for a in entries
        ],
        receiver=evidence.receiver_lines(script, limit=40),
    )
    # Before the release: one delivery in flight (held), one mid-retry, one queued; the endpoint still on.
    assert timeline["before_release"]["endpoint"] == "enabled"
    assert held_delivery in (before["A"]["id"], before["B"]["id"]), held_delivery
    held_key = "A" if before["A"]["id"] == held_delivery else "B"
    mid = before["B" if held_key == "A" else "A"]
    assert before[held_key]["status"] == "sending", before[held_key]
    assert mid["status"] == "retrying" and mid["attempts"] >= 1, mid
    assert before["C"]["status"] == "pending" and before["C"]["attempts"] == 0, before[
        "C"
    ]
    assert (
        timeline["before_release"]["consecutive_failed_attempts"]
        >= schedule.AUTO_DISABLE_MIN_FAILURES - 1
    )
    # The 24 h rule fired on the held attempt's settle.
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", "failing")
    assert held_attempt.outcome == "http_error" and would.get("action") == "retry", (
        would
    )
    assert ep.disabled_at == held_attempt.settled_at
    assert (ep.disabled_at - st.failing_since).total_seconds() >= day_s
    assert st.consecutive_failed_attempts >= schedule.AUTO_DISABLE_MIN_FAILURES
    assert [(a.actor_id, a.metadata["reason"]) for a in entries] == [(None, "failing")]
    # Every delivery is paused, keeps its sequence number, and none is failed, skipped or removed.
    assert Delivery.objects.filter(endpoint=ep).count() == 4
    assert Delivery.objects.filter(event_id__in=list(ev.values())).count() == 4
    for k, a in after.items():
        assert (
            a["status"],
            a["final_outcome"],
            a["skip_reason"],
            a["next_attempt_at"],
        ) == (
            "paused",
            "",
            "",
            None,
        ), (k, a)
    for k in ("A", "B", "C"):
        assert before[k]["seq"] is not None, (k, before[k])
        assert after[k]["seq"] == before[k]["seq"], (k, before[k], after[k])
    assert after["D"]["seq"] == max(before[k]["seq"] for k in ("A", "B", "C")) + 1
    assert sorted(a["seq"] for a in after.values()) == [1, 2, 3, 4]
    assert st.paused_count == 4
    # Nothing was sent after the turn-off: the held attempt is the last request, C and D were never attempted.
    assert late == [], [
        (round(r["at"] - t_first, 3), r["headers"].get("x-futureagi-delivery"))
        for r in late
    ]
    assert after["C"]["attempts"] == 0 and after["D"]["attempts"] == 0
    sent = {r["headers"]["x-futureagi-delivery"] for r in reqs}
    assert sent == {before["A"]["id"], before["B"]["id"]}, sent


# ---- WT-13 and WT-13b ------------------------------------------------------------------------------------------------


def _one_pass_worker(port_r, done_w):
    """A worker process: waits for the receiver's port, makes one dispatcher pass (which leases the due delivery and
    starts its send), then waits for its sender and exits. It never passes again, so it never renews the lease."""
    port = port_r.recv()
    d = real_dispatcher(_Port(port))
    try:
        d.tick()
        done_w.send("ticked")
        d.drain(180)
        done_w.send("drained")
    finally:
        connections.close_all()


@contextlib.contextmanager
def worker_process():
    """A forked worker (forked before any thread of the test starts, with no database connection open)."""
    connections.close_all()
    port_r, port_w = FORK.Pipe(duplex=False)
    done_r, done_w = FORK.Pipe(duplex=False)
    p = FORK.Process(target=_one_pass_worker, args=(port_r, done_w), daemon=True)
    p.start()
    try:
        yield p, port_w, done_r
    finally:
        if p.is_alive():
            with contextlib.suppress(ProcessLookupError):
                os.kill(p.pid, signal.SIGCONT)
            p.kill()
        p.join(10)


def test_wt13_a_worker_killed_mid_attempt_is_worker_lost_then_resent_with_the_same_id(
    keys, organization, workspace
):
    with worker_process() as (proc, port_w, done_r):
        script = Script()
        try:
            script.scripts["/wt13"] = ["hold", (200, {}, b"ok")]
            ep = make_endpoint(
                organization, workspace, url=script.url("/wt13"), timeout_ms=5000
            )
            pub(organization, workspace, "wt13")
            port_w.send(script.port)
            assert done_r.poll(30) and done_r.recv() == "ticked"
            assert script.held.wait(15), "the worker never sent"
            dl = Delivery.objects.get(endpoint=ep)
            assert dl.lease_token is not None and attempts(dl) == []
            os.kill(
                proc.pid, signal.SIGKILL
            )  # the request is written; no answer has been read or settled
            proc.join(10)
            killed_at = time.monotonic()
            assert proc.exitcode == -signal.SIGKILL
            with running(real_dispatcher(script)):
                assert wait_for(
                    lambda: Delivery.objects.filter(
                        id=dl.id, status="succeeded"
                    ).exists(),
                    90,
                    0.2,
                )
            resent_after = script.on("/wt13")[-1]["at"] - killed_at
            dl.refresh_from_db()
            reqs = script.on("/wt13")
            lost = Attempt.objects.get(delivery=dl, outcome="worker_lost")
            # The SDK's dedupe recipe (section 7.4: receivers dedupe on webhook-id): process each id once.
            processed, seen = [], set()
            for r in reqs:
                if r["headers"]["webhook-id"] not in seen:
                    seen.add(r["headers"]["webhook-id"])
                    processed.append(json.loads(r["body"])["id"])
            evidence.write(
                "WT-13",
                attempts=attempts(dl),
                worker_lost_detail=lost.error_detail,
                resent_after_kill_s=round(resent_after, 2),
                lease_s=schedule.lease_ms(5000) / 1000,
                deduped_processed=len(processed),
                receiver=evidence.receiver_lines(script),
            )
            assert attempts(dl) == [("worker_lost", 1), ("success", 2)]
            assert len(reqs) == 2
            same_ids(reqs)
            assert processed == [reqs[0]["headers"]["webhook-id"]]
            assert EndpointState.objects.get(endpoint=ep).in_flight == 0
        finally:
            script.close()


def _health(ep) -> dict:
    st = EndpointState.objects.get(endpoint=ep)
    ep.refresh_from_db()
    return {
        "status": ep.status,
        "disabled_reason": ep.disabled_reason,
        "consecutive_failed_attempts": st.consecutive_failed_attempts,
        "failing_since": st.failing_since,
        "last_outcome": st.last_outcome,
        "last_status_code": st.last_status_code,
        "in_flight": st.in_flight,
    }


def test_wt13b_a_sender_stopped_past_its_lease_settles_as_superseded(
    keys, organization, workspace
):
    with worker_process() as (proc, port_w, done_r):
        script = Script()
        try:
            script.scripts["/wt13b"] = ["hold", (200, {}, b"ok")]
            script.release_status = 500
            ep = make_endpoint(
                organization, workspace, url=script.url("/wt13b"), timeout_ms=5000
            )
            pub(organization, workspace, "wt13b")
            port_w.send(script.port)
            assert done_r.poll(30) and done_r.recv() == "ticked"
            assert script.held.wait(15), "the worker never sent"
            os.kill(
                proc.pid, signal.SIGSTOP
            )  # mid-attempt: the request is out, the answer not read
            dl = Delivery.objects.get(endpoint=ep)
            with running(real_dispatcher(script)):
                assert wait_for(
                    lambda: Delivery.objects.filter(
                        id=dl.id, status="succeeded"
                    ).exists(),
                    90,
                    0.2,
                )
            before = _health(ep)
            # The stopped sender's request is answered (500) and it runs again, long past its lease and deadline.
            script.release()
            os.kill(proc.pid, signal.SIGCONT)
            assert done_r.poll(60) and done_r.recv() == "drained"
            proc.join(30)
            late = wait_for(
                lambda: Attempt.objects.filter(
                    delivery=dl, outcome="superseded"
                ).first(),
                10,
            )
            dl.refresh_from_db()
            after = _health(ep)
            evidence.write(
                "WT-13b",
                attempts=attempts(dl),
                superseded_observed=late.observed_outcome if late else None,
                delivery_status=dl.status,
                health_before_late_settle=before,
                health_after=after,
                receiver=evidence.receiver_lines(script),
            )
            assert late is not None, attempts(dl)
            assert late.observed_outcome in (
                "http_error",
                "timeout",
                "aborted",
                "connect_error",
            )
            assert sorted(attempts(dl)) == sorted(
                [("worker_lost", 1), ("success", 2), ("superseded", late.n)]
            )
            assert dl.status == "succeeded" and dl.final_outcome == ""
            assert after == before, (before, after)
            assert after["in_flight"] == 0 and after["consecutive_failed_attempts"] == 0
            assert len(script.on("/wt13b")) == 2
            same_ids(script.on("/wt13b"))
        finally:
            script.close()


# ---- WT-14 ----------------------------------------------------------------------------------------------------------


def test_wt14_500_events_keep_their_sequence_with_four_in_flight(
    keys, script, organization, workspace
):
    """500 events to one endpoint with ``max_in_flight=4``: sequences 1-500 with no gap and no duplicate, and the
    receiver sees them out of sequence order (here at least for the ten first attempts it answers 503, retried 5 s
    later). The recovery reads (``seq_from = seq_to`` and the events list) are ``test_api_lifecycle.py::test_wt14_*``."""
    script.scripts["/wt14"] = ["seq-fail"]
    ep = make_endpoint(
        organization,
        workspace,
        url=script.url("/wt14"),
        max_in_flight=4,
        rate_limit_per_s=100,
    )
    with transaction.atomic():
        for i in range(500):
            publish(
                type="prompt.label.changed",
                organization_id=organization.id,
                workspace_id=workspace.id,
                data=label_event_data(),
                source_key=f"wt14-{i}",
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )
    with running(real_dispatcher(script)):
        assert wait_for(
            lambda: (
                Delivery.objects.filter(endpoint=ep, status="succeeded").count() == 500
            ),
            120,
            0.5,
        )
    seqs = sorted(Delivery.objects.filter(endpoint=ep).values_list("seq", flat=True))
    reqs = script.on("/wt14")
    arrived = [int(r["headers"]["x-futureagi-sequence"]) for r in reqs]
    first_seen: list[int] = []
    for n in arrived:
        if n not in first_seen:
            first_seen.append(n)
    inversions = sum(1 for a, b in zip(arrived, arrived[1:], strict=False) if b < a)
    retried = sorted(n for n in set(arrived) if arrived.count(n) > 1)
    evidence.write(
        "WT-14",
        deliveries=len(seqs),
        requests=len(reqs),
        retried_sequences=retried,
        inversions=inversions,
        first_arrivals=arrived[:40],
    )
    assert seqs == list(range(1, 501))
    assert sorted(first_seen) == list(
        range(1, 501)
    )  # every sequence: no gap, no duplicate number
    assert retried == [n for n in range(1, 501) if n % 50 == 7]
    assert len(reqs) == 510 and inversions >= 1
    # A retried delivery keeps its ids: one webhook-id per sequence number.
    ids = {}
    for r in reqs:
        ids.setdefault(r["headers"]["x-futureagi-sequence"], set()).add(
            r["headers"]["webhook-id"]
        )
    assert all(len(v) == 1 for v in ids.values())
    assert len({next(iter(v)) for v in ids.values()}) == 500


# ---- WT-29 and WT-29b over HTTPS ------------------------------------------------------------------------------------


class SlowTls(BaseHTTPRequestHandler):
    """An HTTPS receiver's answers by path: ``/big`` a 50 MB body (``sent`` counts the bytes it got out before the
    sender closed), ``/sleep`` 20 s then 200 on the first request and 200 at once after, ``/trickle`` the status line
    and headers at 1 byte/s for 25 s."""

    protocol_version = "HTTP/1.1"
    sent = {"big": 0}
    hits: dict[str, int] = {}

    def log_message(self, *_args):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        path = self.path.split("?")[0]
        n = SlowTls.hits[path] = SlowTls.hits.get(path, 0) + 1
        try:
            if path == "/big":
                self.send_response(200)
                self.send_header("Content-Length", str(50 * 1024 * 1024))
                self.end_headers()
                chunk = b"<p>x</p>" * 8192  # 64 KB
                for _ in range(800):
                    self.wfile.write(chunk)
                    SlowTls.sent["big"] += len(chunk)
            elif path == "/sleep":
                if n == 1:
                    time.sleep(20)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
            elif path == "/trickle":
                for b in b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Slow: aaaaaaaaaaaaaaaaaaaa\r\n\r\nok":
                    self.wfile.write(bytes([b]))
                    self.wfile.flush()
                    time.sleep(1)
        except OSError:
            pass  # the sender closed the connection


def test_wt29_wt29b_big_slow_and_trickling_https_receivers(
    keys, organization, workspace, tmp_path, monkeypatch
):
    """The real dispatcher over HTTPS (a receiver certificate for hooks.acme.ai from a root installed as the certifi
    bundle) with the default 15 s timeout:

    - WT-29: a 50 MB answer is read only to the 4 KB cap and the connection closed: outcome ``success``, an excerpt
      of at most 2 KB, and the receiver got out a small part of the 50 MB; a receiver that sleeps 20 s ends ``timeout``
      at 15 s, then the retry succeeds;
    - WT-29b: a receiver that trickles its headers at 1 byte/s ends ``timeout`` at 15 s +- 1 s."""
    import certifi

    from webhooks.core.attempt import Sender
    from webhooks.core.dispatch import Dispatcher
    from webhooks.core.ipclass import Profile
    from webhooks.core.tests.fakes import FakeResolver
    from webhooks.tests.receiver import PUBLIC, Clock, psl
    from webhooks.tests.test_transport_tls import _ca, _leaf, _pem, _PinnedTo127

    ca_key, ca = _ca("wt29 public root")
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(_pem(ca))
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    key, cert = _leaf(ca_key, ca, "hooks.acme.ai")
    (tmp_path / "r.key").write_bytes(_pem(key, key=True))
    (tmp_path / "r.crt").write_bytes(_pem(cert))
    import ssl as _ssl

    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(tmp_path / "r.crt"), str(tmp_path / "r.key"))
    srv = Server(("127.0.0.1", 0), SlowTls)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    SlowTls.sent, SlowTls.hits = {"big": 0}, {}
    port = srv.server_address[1]
    eps = {
        p: make_endpoint(
            organization, workspace, url=f"https://hooks.acme.ai:{port}{p}", name=p
        )
        for p in ("/big", "/sleep", "/trickle")
    }
    pub(organization, workspace, "wt29")
    clock = Clock()
    sender = Sender(
        Profile(mode="self_hosted"),
        psl,
        FakeResolver([PUBLIC]),
        _PinnedTo127(),
        clock,
    )
    d = Dispatcher(OrmStore(), sender, clock, after_send=close_old_connections)
    try:
        with running(d):
            assert wait_for(
                lambda: (
                    Attempt.objects.filter(
                        delivery__endpoint=eps["/sleep"], outcome="success"
                    ).exists()
                    and Attempt.objects.filter(
                        delivery__endpoint=eps["/trickle"]
                    ).exists()
                    and Delivery.objects.filter(
                        endpoint=eps["/big"], status="succeeded"
                    ).exists()
                ),
                60,
                0.2,
            )
    finally:
        srv.shutdown()
        srv.server_close()

    def rows(p):
        return list(
            Attempt.objects.filter(delivery__endpoint=eps[p])
            .order_by("n")
            .values("n", "outcome", "duration_ms", "response_body_excerpt")
        )

    big, sleep, trickle = rows("/big"), rows("/sleep"), rows("/trickle")
    evidence.write(
        "WT-29",
        scheme="https",
        big=[
            {**r, "response_body_excerpt": len(r["response_body_excerpt"] or "")}
            for r in big
        ],
        big_bytes_the_receiver_got_out=SlowTls.sent["big"],
        sleep=[{k: r[k] for k in ("n", "outcome", "duration_ms")} for r in sleep],
    )
    evidence.write(
        "WT-29b",
        scheme="https",
        trickle=[{k: r[k] for k in ("n", "outcome", "duration_ms")} for r in trickle],
    )
    assert [(r["n"], r["outcome"]) for r in big] == [(1, "success")]
    assert len((big[0]["response_body_excerpt"] or "").encode()) <= 2048
    assert (
        SlowTls.sent["big"] < 5 * 1024 * 1024
    )  # the connection closed after the 4 KB read
    assert [(r["n"], r["outcome"]) for r in sleep] == [(1, "timeout"), (2, "success")]
    assert 14_500 <= sleep[0]["duration_ms"] <= 16_000, sleep
    assert trickle[0]["outcome"] == "timeout", trickle
    assert 14_000 <= trickle[0]["duration_ms"] <= 16_000, trickle


# ---- WT-05b at send -------------------------------------------------------------------------------------------------


def test_wt05b_a_host_that_became_ours_is_refused_at_send_on_hosted_settings(
    keys, script, organization, workspace
):
    """WT-05b's second clause, "after a DNS change, at send": on hosted settings an endpoint whose host passed at
    create is refused by the real dispatcher's per-attempt URL check once the name is one of ours (the operator's
    ``WEBHOOKS_DENY_HOSTS`` names it, as after a DNS change that points it at our own service): ``ssrf_blocked``,
    nothing connected, and the log names no address."""
    from webhooks.core.addresses import url_decision
    from webhooks.core.attempt import Sender
    from webhooks.core.dispatch import Dispatcher
    from webhooks.core.ipclass import Profile
    from webhooks.core.tests.fakes import FakeResolver
    from webhooks.tests.receiver import PUBLIC, Clock, LoopbackTransport, psl

    url = f"https://hooks.acme.ai:{script.port}/wt05b"
    assert url_decision(
        url, Profile(mode="hosted"), psl, ()
    ).allow  # a tenant's host at create
    ep = make_endpoint(organization, workspace, url=url)
    pub(organization, workspace, "wt05b")
    clock = Clock()
    ours = Profile(mode="hosted", deny_hosts=("hooks.acme.ai",))
    sender = Sender(
        ours, psl, FakeResolver([PUBLIC]), LoopbackTransport(script.port), clock
    )
    d = Dispatcher(OrmStore(), sender, clock, after_send=close_old_connections)
    with connects() as seen, running(d):
        assert wait_for(
            lambda: Attempt.objects.filter(delivery__endpoint=ep).exists(), 30
        )
    att = Attempt.objects.filter(delivery__endpoint=ep).order_by("n").first()
    hosts = sorted({str(a[0]) for a in seen if isinstance(a, tuple)})
    evidence.write(
        "WT-05b",
        clause="after a DNS change, at send",
        outcome=att.outcome,
        error_detail=att.error_detail,
        connected_to=hosts,
        receiver_requests=len(script.on("/wt05b")),
    )
    assert att.outcome == "ssrf_blocked"
    assert att.resolved_ip is None and PUBLIC not in att.error_detail
    assert script.on("/wt05b") == []
    assert not [a for a in seen if isinstance(a, tuple) and a[1] == script.port], hosts

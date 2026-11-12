"""WT-30 (DESIGN.md sections 7.8, 13.1 and 17): load on real Postgres, the real dispatcher loops and a real receiver.

10,000 events over 5 minutes to 20 endpoints (500 each, by a prompt filter), on the hosted shape (2 dispatcher loops):
- commit to first attempt p95 <= 2 s, once with LISTEN (``WEBHOOKS_LISTEN_DSN``: a direct connection to the test
  database) and once with the poll only;
- the backlog is drained within 30 s after publishing stops: every event's first attempt has arrived and every
  delivery has succeeded;
- then a 30-minute soak at the same rate with LISTEN: the worker's RSS stays within +-10 % of its level after a
  3-minute warm-up, and the p95 stays <= 2 s.

**Where each part runs, as in production.** The test process is the worker: it runs both dispatcher loops (the
hosted placement where both loops' activities land on one worker pod, the stricter of the two). The publisher is its
own process (producers commit in the web pods), and so is the receiver (a customer's server). The first version of
this row ran all three in one process; there the publisher and the receiver took about a third of the one core the
GIL allows, and the loops fell behind. Both children are forked
before any thread of a phase starts.

Opt-in (``-m stress``, about 45 minutes; FA's default run deselects it). Run it with ``-p no:logging``: pytest's log
capture keeps every record of a test in memory, which would grow the RSS this row measures. ``WT30_SCALE`` (default
1) shortens every phase for a quick check, keeping the rate; the design figures need 1.

Latency: the publisher records each event's time just before its transaction commits and the receiver the first
arrival of each event id, both on the host's monotonic clock (``CLOCK_MONOTONIC`` is system-wide on Linux).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from django.db import close_old_connections, connection, connections, transaction
from psycopg.conninfo import make_conninfo

from webhooks.models import Delivery
from webhooks.publish import publish
from webhooks.queue import PgQueue
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Server, real_dispatcher

SCALE = float(os.environ.get("WT30_SCALE", "1"))
ENDPOINTS = 20
LOOPS = 2  # the hosted shape (config.dispatcher_loops on hosted settings)
RATE = 10_000 / 300.0
PUBLISH_S = 300.0 * SCALE
DRAIN_S = 30.0
SOAK_S = 1800.0 * SCALE
WARMUP_S = 180.0 * SCALE
SAMPLE_S = 15.0 * SCALE
P95_S = 2.0
FORK = mp.get_context("fork")


class _Receiver:
    """A real HTTP server on 127.0.0.1 (run in its own process) that keeps each event's first arrival."""

    def __init__(self, seen) -> None:
        self.first: dict[str, float] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                arrived = time.monotonic()
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                event_id = json.loads(body)["id"]
                if event_id not in outer.first:
                    outer.first[event_id] = arrived
                    with seen.get_lock():
                        seen.value += 1
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def _receiver_main(port_w, seen, stop, out_path):
    r = _Receiver(seen)
    port_w.send(r.port)
    stop.wait()
    Path(out_path).write_text(json.dumps(r.first))
    r.server.shutdown()


def _publisher_main(go, org_id, ws_id, tag, n, seconds, out_path):
    """``n`` events spread evenly over ``seconds``, a few per transaction every 100 ms, round-robin over the 20
    prompts. Each event's time is taken just before its transaction commits."""
    go.wait()
    commit_at: dict[str, float] = {}
    t0 = time.monotonic()
    done = 0
    while done < n:
        due = min(n, int(n * (time.monotonic() - t0) / seconds) + 1)
        if due > done:
            with transaction.atomic():
                ids = []
                for i in range(done, due):
                    r = publish(
                        type="prompt.label.changed",
                        organization_id=org_id,
                        workspace_id=ws_id,
                        data=label_event_data(prompt_id=f"p{i % ENDPOINTS}"),
                        source_key=f"{tag}-{i}",
                        source_hash="h",
                        producer="test",
                        occurred_at=T0,
                    )
                    ids.append(r.event_id)
                t = time.monotonic()
                for event_id in ids:
                    commit_at[event_id] = t
            done = due
        time.sleep(0.1)
    connections.close_all()
    Path(out_path).write_text(json.dumps(commit_at))


def _rss_kb() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise AssertionError("no VmRSS in /proc/self/status")


def _listen_dsn() -> str:
    s = connection.settings_dict
    return make_conninfo(
        host=s["HOST"] or "127.0.0.1",
        port=str(s["PORT"] or 5432),
        dbname=s["NAME"],
        user=s["USER"],
        password=s["PASSWORD"],
    )


class _Port:
    """What the dispatcher's test transport needs of a receiver: its port."""

    def __init__(self, port: int) -> None:
        self.port = port


def _run(phase, port, seen, *, listen, sample=None) -> dict:
    """One phase: the loops run while the publisher process publishes, then until the backlog is drained (at most
    ``DRAIN_S``). Returns the commit times, the drain time and the RSS samples (when ``sample`` is set)."""
    tag, n, seconds, go, proc, out_path = phase
    first = seen.value
    loops = [real_dispatcher(_Port(port)) for _ in range(LOOPS)]
    for d in loops:
        d.queue = PgQueue(_listen_dsn() if listen else None)
    stop = threading.Event()

    def loop(d):
        try:
            d.run(stop)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=loop, args=(d,), daemon=True) for d in loops]
    for t in threads:
        t.start()
    t_start, cpu_start = time.monotonic(), time.process_time()
    go.set()
    rss: list[tuple[float, int]] = []
    while proc.is_alive():
        if sample:
            rss.append((round(time.monotonic() - t_start, 1), _rss_kb()))
        proc.join(sample or 1.0)
    ended = time.monotonic()
    # The worker's CPU over wall time while publishing: above 1.0 means threads outside the GIL (libpq, sockets).
    cpu = (time.process_time() - cpu_start) / (ended - t_start)
    assert proc.exitcode == 0, (tag, proc.exitcode)
    while seen.value - first < n and time.monotonic() - ended < DRAIN_S:
        time.sleep(0.05)
    drained_in = time.monotonic() - ended
    stop.set()
    for t in threads:
        t.join(60)
    for d in loops:
        d.queue.close()
    mine = Delivery.objects.filter(event__source_key__startswith=f"{tag}-")
    assert mine.count() == n, (tag, mine.count())
    return {
        "commit_at": json.loads(Path(out_path).read_text()),
        "drained_in_s": drained_in,
        "worker_cpu_over_wall": round(cpu, 3),
        "left_due": mine.exclude(status="succeeded").count(),
        "rss": rss,
    }


def _latency(commit_at: dict, first: dict) -> dict:
    lat = sorted(first[e] - t for e, t in commit_at.items() if e in first)
    return {
        "events": len(commit_at),
        "first_attempts": len(lat),
        "p50_s": statistics.median(lat) if lat else None,
        "p95_s": statistics.quantiles(lat, n=20)[18] if len(lat) >= 20 else None,
        "max_s": lat[-1] if lat else None,
    }


@pytest.mark.stress
@pytest.mark.django_db(transaction=True)
def test_wt30_load_listen_poll_and_a_30_minute_soak(
    organization, workspace, monkeypatch, tmp_path
):
    use_test_keys(monkeypatch)
    soak_n = int(RATE * SOAK_S)
    plan = [
        ("listen", int(RATE * PUBLISH_S), PUBLISH_S),
        ("poll", int(RATE * PUBLISH_S), PUBLISH_S),
        ("soak", soak_n, SOAK_S),
    ]
    # Every child is forked here, before any thread of the run starts, with no database connection open.
    connections.close_all()
    seen = FORK.Value("q", 0)
    rstop = FORK.Event()
    port_r, port_w = FORK.Pipe(duplex=False)
    first_path = tmp_path / "first.json"
    receiver = FORK.Process(
        target=_receiver_main, args=(port_w, seen, rstop, str(first_path)), daemon=True
    )
    receiver.start()
    phases = []
    for tag, n, seconds in plan:
        go = FORK.Event()
        out_path = tmp_path / f"commit-{tag}.json"
        p = FORK.Process(
            target=_publisher_main,
            args=(go, organization.id, workspace.id, tag, n, seconds, str(out_path)),
            daemon=True,
        )
        p.start()
        phases.append((tag, n, seconds, go, p, out_path))
    port = port_r.recv()
    try:
        for i in range(ENDPOINTS):
            make_endpoint(
                organization,
                workspace,
                url=f"http://hooks.acme.ai:{port}/ep{i}",
                filters={"prompt_ids": [f"p{i}"]},
                name=f"load-{i}",
            )
        results = {}
        for phase, listen in zip(phases, (True, False, True), strict=True):
            results[phase[0]] = _run(
                phase,
                port,
                seen,
                listen=listen,
                sample=SAMPLE_S if phase[0] == "soak" else None,
            )
    finally:
        rstop.set()
        receiver.join(30)
        for *_rest, p, _out in phases:
            if p.is_alive():  # a phase that never ran (the run failed before it)
                p.kill()
            p.join(5)
    first = json.loads(first_path.read_text())
    summary = {}
    for tag, r in results.items():
        summary[tag] = {
            **_latency(r["commit_at"], first),
            "drained_in_s": r["drained_in_s"],
            "worker_cpu_over_wall": r["worker_cpu_over_wall"],
            "left_due": r["left_due"],
        }
        print(f"WT-30 {tag}: {json.dumps(summary[tag])}")
    soak = results["soak"]
    after = [kb for at, kb in soak["rss"] if at >= WARMUP_S]
    base = after[0]
    band = (min(after) / base, max(after) / base)
    print(f"WT-30 soak RSS kB (t s, kB): {soak['rss']}")
    print(
        f"WT-30 soak RSS after warm-up: base {base} kB, band {band[0]:.3f}-{band[1]:.3f}"
    )
    evidence.write(
        "WT-30",
        scale=SCALE,
        topology="worker = the test process with both dispatcher loops; publisher and receiver in their own processes",
        rate_per_s=round(RATE, 2),
        endpoints=ENDPOINTS,
        loops=LOOPS,
        phases=summary,
        soak_rss={
            "base_kb": base,
            "band": [round(band[0], 3), round(band[1], 3)],
            "samples": soak["rss"],
        },
    )

    for tag in ("listen", "poll"):
        s = summary[tag]
        assert s["first_attempts"] == s["events"], (tag, s)
        assert s["p95_s"] <= P95_S, (tag, s["p95_s"])
        assert s["drained_in_s"] <= DRAIN_S and s["left_due"] == 0, (tag, s)
    s = summary["soak"]
    assert s["first_attempts"] == soak_n and s["left_due"] == 0, s
    assert s["p95_s"] <= P95_S, s["p95_s"]
    assert len(after) >= (SOAK_S - WARMUP_S) / SAMPLE_S - 2, len(after)
    assert 0.90 <= band[0] and band[1] <= 1.10, band

"""The Insights evidence broker on 127.0.0.1, for the cross-language contract
test in the agent's repository (``test/contract-broker.test.mjs``). Not a
pytest module: the test spawns it.

    cd futureagi && DJANGO_SETTINGS_MODULE=tfc.settings.test \\
        NO_STARTUP_DB_MUTATIONS=true \\
        python -m ee.agenthub.insights.tests.contract_broker_server

``POST /tracer/internal/insights/evidence/<op>`` goes through Django's URL
resolver to the real ``InsightEvidenceView`` and ``broker``: the capability,
its budgets, the run checks, the 'local' evidence store (built on a pod's
first call, as there is no worker here), field pages and image
downscaling. Snapshots are built by the real ``evidence.build_unit_snapshot``
over ``test_evidence.FakeClickHouse``. Stubbed, and nothing else:

- ``broker._load_run``, ``broker._load_unit`` and
  ``evidence_store._run_states``: one in-memory run of traces, with unit 17
  (trace T1: wide and deep, a long field, a 3000x2000 image as a data URI)
  and unit 18 (trace T2), in place of Postgres;
- ``broker._analytics``: the fake ClickHouse, in place of ClickHouse;
- settings: a random secret and a temporary cache directory (the test
  settings' cache is already local memory);
- Django's middleware does not run: the resolved view gets the request.

Control routes for the test, outside the broker's base:

    POST /control/mint      {units, stage?, ttl_s?, age_s?}  -> {capability}
    POST /control/run       {status}                         -> {status}
    POST /control/calls     {}                               -> {calls, builds}
    POST /control/receipts  {key, evidence}                  -> {kept, dropped}

``calls`` drains the log of broker calls answered (op, status, error code)
and gives the number of snapshot builds so far; ``receipts`` is Django's own
check of a read-units v2 unit's evidence (``agent.checked_receipts``). Once
listening, the server prints one line, ``CONTRACT_BROKER_READY {...}``; it
stops when its stdin closes. It prints no trace content.
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import shutil
import signal
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

RUN_ID = "99999999-9999-4999-8999-999999999999"
# The run's creation pins every build: its built_at and its version cutoff.
CREATED = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
UNITS = {"17": "T1", "18": "T2"}
BASE = "/tracer/internal/insights/evidence"
READY = "CONTRACT_BROKER_READY"
DECISIVE = "DECISION: REFUND_DECLINED reason=outside_30_day_window order_age_days=41"


def photo() -> str:
    """A 3000x2000 PNG as a data URI: the broker must downscale it to view."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (3000, 2000), (222, 200, 160))
    draw = ImageDraw.Draw(image)
    draw.rectangle((600, 500, 2400, 1700), fill=(150, 110, 60), outline=(60, 40, 20))
    draw.line((600, 500, 2400, 1700), fill=(60, 40, 20), width=30)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def engine_log() -> str:
    """About 177k UTF-16 units of rule-engine log with accented and astral text
    on every line (UTF-16 offsets are not code points), the decision near the
    end."""
    lines = [
        f"2026-10-01T12:{step // 60 % 60:02d}:{step % 60:02d}Z INFO step "
        f"{step:04d} rule=window_check café 東京 🙂 ok"
        for step in range(2_600)
    ]
    lines.insert(2_550, DECISIVE)
    return "\n".join(lines)


def rows() -> list[dict]:
    """Physical span rows: T1 is one agent turn with a 24-deep planning chain,
    a 60-wide lookup fan-out (one lookup failed), a long tool log and an image
    in the user's message; T2 is one short exchange."""
    from ee.agenthub.insights.tests.test_evidence import span

    question = {
        "messages": [
            {"role": "system", "content": "You are Harbor, a support assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "My parcel arrived crushed. Refund? 📦"},
                    {"type": "image_url", "image_url": {"url": photo()}},
                ],
            },
        ]
    }
    out = [
        span(
            "T1",
            "root",
            name="support_agent",
            observation_type="agent",
            input=json.dumps(question, ensure_ascii=False),
            output="Good news 🎉 your refund of €84.50 has been approved and "
            "will reach your card in 3–5 days.",
        )
    ]
    parent = "root"
    for depth in range(24):
        out.append(
            span(
                "T1",
                f"plan{depth:02d}",
                at=1 + depth,
                parent=parent,
                name=f"plan_step_{depth:02d}",
                output=f"step {depth}: narrow the refund policy",
            )
        )
        parent = f"plan{depth:02d}"
    out[-1].update(
        observation_type="llm",
        model="gpt-4o-mini",
        provider="openai",
        prompt_tokens=1_234,
        completion_tokens=56,
        total_tokens=1_290,
        cost=1e-05,
        output="Plan: check the order window, then call refund_engine.",
    )
    out.append(span("T1", "fanout", at=30, parent="root", name="lookup_fanout"))
    for i in range(60):
        failed = i == 37
        out.append(
            span(
                "T1",
                f"lookup{i:02d}",
                at=31 + i / 10,
                parent="fanout",
                name="inventory_lookup",
                observation_type="tool",
                input=json.dumps({"sku": f"sku-{i:04d}"}),
                output=f"sku-{i:04d}: in stock ({i % 7} left) ✓",
                status="ERROR" if failed else "OK",
                status_message="warehouse API 503 — retried" if failed else "",
            )
        )
    out.append(
        span(
            "T1",
            "engine",
            at=100,
            parent="root",
            name="refund_engine",
            observation_type="tool",
            output=engine_log(),
            attrs_string={"tool.name": "refund_engine"},
            attrs_number={"retry.count": 2},
            cost=0.000321,
        )
    )
    out.append(
        span(
            "T2",
            "root2",
            at=500,
            name="support_agent",
            observation_type="agent",
            input="Where is my order?",
            output="It ships tomorrow.",
        )
    )
    return out


def install(world, analytics) -> None:
    """Point the broker's run, unit and ClickHouse lookups at this process."""
    from ee.agenthub.insights import broker, evidence_store

    run = world.run

    def load_unit(_run, key):
        if key not in UNITS:
            return None
        return SimpleNamespace(pk=int(key), unit_id=UNITS[key])

    broker._load_run = lambda run_id: run if run_id == RUN_ID else None
    broker._load_unit = load_unit
    evidence_store._run_states = lambda ids: {
        i: (run.status, False) for i in ids if i == RUN_ID
    }

    def build_reads():
        # Asked for once per snapshot build.
        with world.lock:
            world.builds += 1
        return analytics

    broker._analytics = build_reads


def control(world, op: str, body: dict) -> tuple[int, dict]:
    from ee.agenthub.insights import agent, broker

    if op == "mint":
        capability = broker.mint(
            world.run,
            body["units"],
            stage=body.get("stage", "read"),
            ttl_s=body.get("ttl_s", 120),
            now=time.time() - body.get("age_s", 0),
        )
        return 200, {"capability": capability}
    if op == "run":
        world.run.status = body["status"]
        return 200, {"status": world.run.status}
    if op == "calls":
        # The broker calls answered since the last ask (op, status, error code)
        # and the snapshot builds so far.
        with world.lock:
            calls = list(world.calls)
            world.calls.clear()
            return 200, {"calls": calls, "builds": world.builds}
    if op == "receipts":
        kept, dropped = agent.checked_receipts(body["evidence"], body["key"])
        return 200, {"kept": kept, "dropped": dropped}
    return 404, {"error": {"code": "not_found"}}


def error_code(response) -> str | None:
    if "json" not in response.get("Content-Type", ""):
        return None
    return (json.loads(response.content).get("error") or {}).get("code")


def handler_for(world):
    from django.test import RequestFactory
    from django.urls import Resolver404, resolve

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.startswith("/control/"):
                op = self.path.removeprefix("/control/")
                status, body = control(world, op, json.loads(raw or b"{}"))
                content = json.dumps(body).encode()
                return self.reply(status, {"Content-Type": "application/json"}, content)
            try:
                match = resolve(self.path)
            except Resolver404:
                return self.reply(404, {}, b"")
            extra = {}
            if "Authorization" in self.headers:
                extra["HTTP_AUTHORIZATION"] = self.headers["Authorization"]
            request = RequestFactory().generic(
                "POST",
                self.path,
                raw,
                content_type=self.headers.get("Content-Type") or "",
                **extra,
            )
            response = match.func(request, *match.args, **match.kwargs)
            call = {
                "op": self.path.rsplit("/", 1)[-1],
                "status": response.status_code,
                "code": error_code(response),
            }
            with world.lock:
                world.calls.append(call)
            self.reply(response.status_code, dict(response.items()), response.content)

        def reply(self, status: int, headers: dict, content: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                if name.lower() != "content-length":
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                # The agent gave up on the call (a cancelled reading).
                pass

    return Handler


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
    os.environ.setdefault("NO_STARTUP_DB_MUTATIONS", "true")
    import django

    django.setup()
    from django.test.utils import override_settings

    from ee.agenthub.insights.tests.test_evidence import ORG, PROJECT, FakeClickHouse

    cache_dir = tempfile.mkdtemp(prefix="insights-contract-")
    overrides = override_settings(
        INSIGHTS_EVIDENCE_SECRET=secrets.token_urlsafe(32),
        INSIGHTS_EVIDENCE_SECRET_PREVIOUS="",
        INSIGHTS_EVIDENCE_STORE="local",
        INSIGHTS_EVIDENCE_CACHE_DIR=os.path.join(cache_dir, "cache"),
        INSIGHTS_EVIDENCE_FETCH_EXTERNAL_MEDIA=False,
    )
    overrides.enable()
    run = SimpleNamespace(
        id=uuid.UUID(RUN_ID),
        organization_id=uuid.UUID(ORG),
        project_id=uuid.UUID(PROJECT),
        unit="traces",
        status="running",
        scope={},
        created_at=CREATED,
    )
    world = SimpleNamespace(run=run, calls=[], builds=0, lock=threading.Lock())
    install(world, FakeClickHouse(rows()))
    # Import the URLconf before serving: the first resolve imports every app's
    # routes, which here takes longer than the agent's 15 s broker timeout.
    from django.urls import resolve

    resolve(f"{BASE}/manifest")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(world))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    ready = {
        "base": origin + BASE,
        "control": origin + "/control",
        "run": RUN_ID,
        "created_at": CREATED.isoformat(),
    }
    print(f"{READY} {json.dumps(ready)}", flush=True)
    try:
        # Until the test closes stdin (or goes away).
        sys.stdin.read()
    finally:
        server.shutdown()
        overrides.disable()
        shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

"""Insights on the agent's insights-agent/v2 engine, without a database or
network: the engine pin and its header, the 409 retry, usage from every
reply (and the ceiling for a model nothing prices), read-units v2 over the
evidence broker, fail-closed verification of reports, questions and naming,
and estimates that scale with a unit's size (PROTOCOL_V2). A stopped run's
call is given up against an agent on this host, to see its connection
close."""

from __future__ import annotations

import contextlib
import http.server
import json
import select
import socket
import threading
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ee.agenthub.insights import agent, billing, broker, llm, pipeline, report
from ee.agenthub.insights.tests.test_insights_core import unit

PIN = "a" * 64
RUN_ID = "99999999-9999-4999-8999-999999999999"
ORG = "33333333-3333-4333-8333-333333333333"
PROJECT = "11111111-1111-4111-8111-111111111111"
V2 = {
    "kind": "omega",
    "contract": "insights-agent/v2",
    "version": "0.9.0",
    "runtime_build": "build-7",
    "models": {"fast": "turing_flash", "thinking": "turing_large"},
    "modalities": {"image_models": ["turing_flash"], "audio": False},
    "engine_pin": PIN,
}
HEALTH = {
    "ok": True,
    "service": "insights-agent",
    "version": "0.9.0",
    "models": V2["models"],
    "contract": "insights-agent/v2",
    "contracts": ["insights-agent/v1", "insights-agent/v2"],
    "runtime_build": "build-7",
    "modalities": V2["modalities"],
    "engine_pin": PIN,
}
EMPTY_USAGE = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}


@pytest.fixture(autouse=True)
def evidence_secret(settings):
    settings.INSIGHTS_EVIDENCE_SECRET = "evidence-secret"
    settings.INSIGHTS_EVIDENCE_SECRET_PREVIOUS = ""


class Agent:
    """The agent over HTTP: answers each call from a script of (status,
    body) per path and records what it was sent."""

    def __init__(self, monkeypatch, **script):
        self.script = {path: list(replies) for path, replies in script.items()}
        self.calls = []
        self.sleeps = []
        monkeypatch.setattr(
            agent,
            "_setting",
            lambda name, default="": "http://agent" if "URL" in name else "token",
        )
        monkeypatch.setattr(agent.httpx, "stream", self.stream)
        monkeypatch.setattr(agent.time, "sleep", self.sleeps.append)

    def stream(self, method, url, json=None, headers=None, timeout=None):
        path = url.removeprefix("http://agent")
        self.calls.append(
            SimpleNamespace(method=method, path=path, body=json, headers=headers or {})
        )
        status, body = self.script[path].pop(0)
        content = body if isinstance(body, bytes) else _json(body)
        reply = SimpleNamespace(status_code=status, iter_bytes=lambda: iter([content]))
        return contextlib.nullcontext(reply)


def _json(value) -> bytes:
    return json.dumps(value).encode()


@contextlib.contextmanager
def held_agent(monkeypatch, answer_after=10.0):
    """An agent on this host that holds each request until its caller goes
    away (``seen.left``), as the agent stops the work of a request whose
    caller closes it, or answers after ``answer_after`` seconds."""
    seen = SimpleNamespace(left=threading.Event())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            deadline = time.monotonic() + answer_after
            while time.monotonic() < deadline:
                ready, _, _ = select.select([self.connection], [], [], 0.02)
                try:
                    gone = ready and not self.connection.recv(1, socket.MSG_PEEK)
                except OSError:
                    gone = True
                if gone:
                    seen.left.set()
                    return
            body = _json({"units": [], "usage": {"model_calls": 1}})
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(
        agent, "_setting", lambda name, default="": url if "URL" in name else "token"
    )
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()


def make_run(**fields):
    run = {
        "id": uuid.UUID(RUN_ID),
        "organization_id": uuid.UUID(ORG),
        "project_id": uuid.UUID(PROJECT),
        "unit": "sessions",
        "status": "running",
        "settings": {"lens": "failures", "question": "Where?", "engine": V2},
        "created_at": datetime(2026, 10, 20, tzinfo=UTC),
    }
    run.update(fields)
    return SimpleNamespace(**run)


class TestEnginePin:
    def test_a_v2_agent_pins_its_reported_engine_verbatim(self, monkeypatch):
        fake = Agent(monkeypatch, **{"/healthz": [(200, HEALTH)]})
        assert agent.resolve_engine() == V2
        assert [(c.method, c.path) for c in fake.calls] == [("GET", "/healthz")]

    def test_without_an_evidence_secret_the_run_stays_on_v1(
        self, monkeypatch, settings
    ):
        settings.INSIGHTS_EVIDENCE_SECRET = ""
        Agent(monkeypatch, **{"/healthz": [(200, HEALTH)]})
        assert agent.resolve_engine() == {
            "kind": "omega",
            "contract": "insights-agent/v1",
            "version": "insights-agent/v1",
            "engine_pin": PIN,
        }

    def test_an_agent_from_before_pins_and_no_agent(self, monkeypatch):
        old = {"ok": True, "version": "0.4.0", "models": V2["models"]}
        Agent(monkeypatch, **{"/healthz": [(200, old)]})
        assert agent.resolve_engine() == {
            "kind": "omega",
            "contract": "insights-agent/v1",
            "version": "insights-agent/v1",
        }
        monkeypatch.setattr(agent, "enabled", lambda: False)
        assert agent.resolve_engine() == {"kind": "builtin", "version": "builtin/v1"}

    @pytest.mark.parametrize("reply", [(503, {"ok": False}), (200, b"<html>")])
    def test_an_agent_that_cannot_say_refuses_the_run(self, monkeypatch, reply):
        Agent(monkeypatch, **{"/healthz": [reply]})
        with pytest.raises(llm.InsightModelError) as error:
            agent.resolve_engine()
        assert error.value.unavailable

    def test_runs_without_a_pin_keep_the_setting(self, monkeypatch):
        assert agent.pinned({"engine": V2}) == V2
        monkeypatch.setattr(agent, "enabled", lambda: True)
        assert agent.pinned({}) == {"kind": "omega", "version": "insights-agent/v1"}
        assert agent.is_v2(V2) and not agent.is_v2(agent.pinned({}))


class TestCalls:
    def test_a_pinned_call_names_its_engine(self, monkeypatch):
        fake = Agent(monkeypatch, **{"/v1/ask": [(200, {"answer": []})] * 2})
        agent._post("/v1/ask", {}, llm.Spend(), engine=V2)
        agent._post("/v1/ask", {}, llm.Spend())
        assert fake.calls[0].headers[agent.ENGINE_HEADER] == PIN
        assert agent.ENGINE_HEADER not in fake.calls[1].headers

    def test_a_mismatch_is_retried_then_fails_the_run(self, monkeypatch):
        mismatch = (409, {"error": {"code": "engine_mismatch"}, "usage": EMPTY_USAGE})
        fake = Agent(
            monkeypatch,
            **{"/v1/ask": [mismatch, mismatch, (200, {"answer": ["ok"]})]},
        )
        assert agent._post("/v1/ask", {}, llm.Spend(), engine=V2) == {"answer": ["ok"]}
        assert fake.sleeps == [2.0, 5.0]

        fake = Agent(monkeypatch, **{"/v1/ask": [mismatch] * 3})
        with pytest.raises(agent.InsightEngineMismatch) as error:
            agent._post("/v1/ask", {}, llm.Spend(), engine=V2)
        assert not error.value.unavailable  # the run fails; Temporal won't retry
        assert len(fake.calls) == 3 and fake.sleeps == [2.0, 5.0]

        # Without a pin a 409 is a plain refusal, never a mismatch.
        Agent(monkeypatch, **{"/v1/ask": [mismatch]})
        with pytest.raises(llm.InsightModelError) as error:
            agent._post("/v1/ask", {}, llm.Spend())
        assert not isinstance(error.value, agent.InsightEngineMismatch)

    def test_usage_on_an_error_reply_is_spend(self, monkeypatch):
        usage = {
            "model_calls": 2,
            "input_tokens": 9000,
            "output_tokens": 700,
            "cost_usd": None,
            "by_model": {"turing_flash": {"input_tokens": 9000, "output_tokens": 700}},
        }
        Agent(
            monkeypatch,
            **{
                "/v1/read-units": [
                    (504, {"error": {"code": "deadline"}, "usage": usage})
                ]
            },
        )
        spend = llm.Spend()
        with pytest.raises(llm.InsightModelError) as error:
            agent._post("/v1/read-units", {}, spend)
        assert error.value.unavailable
        assert spend.cost_usd == pytest.approx(
            llm.call_cost("turing_flash", 0.0, 9000, 700)
        )
        assert spend.cost_usd > 0 and not spend.unpriced

    def test_a_model_nothing_prices_is_charged_the_ceiling_and_flagged(
        self, monkeypatch
    ):
        usage = {
            "input_tokens": 1000,
            "output_tokens": 100,
            "image_parts": 2,
            "audio_seconds": 1.5,
            "cost_usd": None,
            "by_model": {
                "vendor-secret-model": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "image_parts": 2,
                    "cost_usd": None,
                },
                "turing_flash": {"cost_usd": 0.25, "input_tokens": 10},
            },
        }
        spend = llm.Spend()
        agent._add_usage(spend, usage)
        price_in, price_out = llm.ceiling_price()
        ceiling = (1000 + 2 * llm.IMAGE_PART_TOKENS) * price_in + 100 * price_out
        assert spend.cost_usd == pytest.approx(ceiling / 1_000_000 + 0.25)
        assert spend.unpriced
        assert (spend.image_parts, spend.audio_seconds) == (2, 1.5)
        # Never below UNPRICED_CEILING, even when the tier models are cheaper.
        assert (price_in, price_out) == llm.UNPRICED_CEILING == (15.0, 75.0)

    def test_a_cost_the_agent_does_not_know_is_flagged(self):
        priced = {"turing_flash": {"input_tokens": 1000, "output_tokens": 100}}
        for usage in (
            {"cost_known": False, "cost_usd": None, "by_model": priced},
            {
                "by_model": priced,
                "calls": [{"model": "turing_flash", "status": "aborted"}],
            },
            {"by_model": priced, "calls": [{"estimated": True}]},
        ):
            spend = llm.Spend()
            agent._add_usage(spend, usage)
            assert spend.unpriced, usage
            assert spend.cost_usd == pytest.approx(
                llm.call_cost("turing_flash", 0.0, 1000, 100)
            )
        spend = llm.Spend()
        agent._add_usage(spend, {"by_model": priced, "cost_known": True})
        assert not spend.unpriced

    @pytest.mark.parametrize(
        ("error", "charged"),
        [
            (agent.httpx.ReadTimeout("slow"), True),
            (agent.httpx.RemoteProtocolError("reset"), True),
            (agent.httpx.ConnectError("refused"), False),
            (agent.httpx.ConnectTimeout("no route"), False),
        ],
    )
    def test_a_reply_lost_after_sending_is_estimated(self, monkeypatch, error, charged):
        Agent(monkeypatch)

        def stream(*args, **kwargs):
            raise error

        monkeypatch.setattr(agent.httpx, "stream", stream)
        spend = llm.Spend()
        payload = {"run": {"question": "q" * 3000}}
        with pytest.raises(llm.InsightModelError) as failed:
            agent._post("/v1/read-units", payload, spend)
        assert failed.value.unavailable
        assert spend.unpriced is charged
        assert (spend.cost_usd > 0) is charged
        if charged:
            tokens_in = -(-len(json.dumps(payload)) // 3)
            assert (spend.prompt_tokens, spend.completion_tokens) == (
                tokens_in,
                agent.LOST_REPLY_OUTPUT_TOKENS,
            )

    def test_an_oversized_reply_is_estimated_too(self, monkeypatch):
        fake = Agent(monkeypatch, **{"/v1/ask": [(200, b"x")]})
        monkeypatch.setattr(agent, "MAX_RESPONSE_BYTES", 0)
        spend = llm.Spend()
        with pytest.raises(llm.InsightModelError):
            agent._post("/v1/ask", {}, spend)
        assert len(fake.calls) == 1 and spend.unpriced and spend.cost_usd > 0

    def test_a_stopped_runs_call_is_given_up_and_its_connection_closed(
        self, monkeypatch
    ):
        """Stopped while the agent reads: the call is given up within a
        poll, and its connection is shut, so the agent stops the work. What
        it may have spent is never reported: estimated at the ceiling, for
        the stopped run's record."""
        monkeypatch.setattr(agent, "CANCEL_POLL_S", 0.05)
        stopped = threading.Event()
        spend = llm.Spend()
        with held_agent(monkeypatch) as seen:
            threading.Timer(0.3, stopped.set).start()
            started = time.monotonic()
            with pytest.raises(agent.InsightCallCancelled):
                agent._post(
                    "/v1/read-units", {"units": []}, spend, cancelled=stopped.is_set
                )
            assert time.monotonic() - started < 3
            assert seen.left.wait(3)
        assert spend.unpriced and spend.cost_usd > 0

    def test_a_call_that_may_be_given_up_answers_as_any_other(self, monkeypatch):
        monkeypatch.setattr(agent, "CANCEL_POLL_S", 0.05)
        spend = llm.Spend()
        with held_agent(monkeypatch, answer_after=0.2) as seen:
            body = agent._post(
                "/v1/read-units", {"units": []}, spend, cancelled=lambda: False
            )
        assert body["units"] == [] and spend.calls == 1
        assert not seen.left.is_set()

    def test_usage_costs(self):
        assert llm.usage_cost("turing_flash", 0.5, 10, 10) == (0.5, True)
        assert llm.usage_cost("whatever", None, 0, 0) == (0.0, True)
        assert llm.usage_cost("turing_flash", None, 1000, 100) == (
            llm.call_cost("turing_flash", 0.0, 1000, 100),
            True,
        )
        cost, priced = llm.usage_cost("unknown-model", 0, 1000, 0)
        assert cost > 0 and not priced
        assert llm.usage_cost("unknown-model", True, 1000, 0)[1] is False


def receipt(key, span="s3", field="output", start=10, length=15):
    return f"ev2|{key}|{span}|{field}|{start}|{length}"


def unit_result(key, **fields):
    result = {
        "key": key,
        "ok": True,
        "facets": {
            "summary": "Refund refused",
            "lens_answer": "The refund tool declined.",
            "topic": "refunds",
            "outcome": "unresolved",
            "sentiment": 2,
            "failure_present": True,
            "failure_mode": "tool_error",
        },
        "evidence": [
            {
                "kind": "text",
                "evidence_id": receipt(key),
                "span": "s3",
                "field": "output",
                "quote": "refund=declined",
                "supports": "failure",
                "span_id": "span-3",
                "trace_id": "trace-1",
                "field_sha256": "f" * 64,
                "context": "text that must not be stored",
            }
        ],
        "dropped_evidence": 1,
        "dropped_reasons": {"unread_region": 1},
        "coverage_note": "Read the refund span and the final answer.",
        "decisive_unread": False,
        "forced": {},
        "coverage": {
            "spans_total": 40,
            "spans_read": 6,
            "chars_total": 182_000,
            "chars_read": 21_000,
            "media_total": 1,
            "media_viewed": 1,
            "media_inaccessible": {},
            "budget_exhausted": [],
            "stop_reason": "answered",
        },
        "source": {"kind": "broker", "ref": key, "digest": "d" * 64},
        "usage": EMPTY_USAGE,
    }
    result.update(fields)
    return result


class TestReadUnitsV2:
    def test_the_request_names_broker_refs_and_a_capability(self, monkeypatch):
        fake = Agent(
            monkeypatch,
            **{"/v1/read-units": [(200, {"units": [unit_result("17")]})]},
        )
        run = make_run()
        agent.read_units_v2(
            run, {"run_id": RUN_ID}, ["17", "18"], [], llm.Spend(), engine=V2
        )

        [call] = fake.calls
        assert call.headers[agent.ENGINE_HEADER] == PIN
        body = call.body
        assert body["contract"] == "insights-agent/v2"
        assert body["units"] == [
            {"key": "17", "evidence": {"source": "broker", "ref": "17"}},
            {"key": "18", "evidence": {"source": "broker", "ref": "18"}},
        ]
        assert "content" not in json.dumps(body["units"])
        claims = broker.verify(body["capability"])
        assert claims["units"] == ["17", "18"]
        assert (claims["run"], claims["kind"], claims["stage"]) == (
            RUN_ID,
            "session",
            "read",
        )

    def test_receipts_coverage_and_failures_are_checked_and_stored(self, monkeypatch):
        foreign = unit_result("17")["evidence"][0] | {"evidence_id": receipt("99")}
        long = unit_result("17")["evidence"][0] | {"quote": "x" * 301}
        misplaced = unit_result("17")["evidence"][0] | {"span": "s4"}
        # Not the text its receipt spans: one character longer.
        stretched = unit_result("17")["evidence"][0] | {"quote": "refund=declined!"}
        replies = [
            unit_result(
                "17",
                evidence=[
                    unit_result("17")["evidence"][0],
                    foreign,
                    long,
                    misplaced,
                    stretched,
                    "not an object",
                ],
            ),
            unit_result(
                "18",
                evidence=[
                    unit_result("18")["evidence"][0] | {"supports": "context"},
                    {
                        "kind": "media",
                        "evidence_id": "ev2m|18|m1|full",
                        "media": "m1",
                        "span": "s2",
                        "supports": "context",
                        "observation": "A crushed   box.",
                    },
                ],
            ),
            {"key": "19", "ok": False, "error": {"code": "evidence_refused"}},
            unit_result("77"),
        ]
        Agent(monkeypatch, **{"/v1/read-units": [(200, {"units": replies})]})
        out = agent.read_units_v2(
            make_run(), {}, ["17", "18", "19", "20"], [], llm.Spend(), engine=V2
        )

        facets = out["17"]["facets"]
        # A list, as v1 stores it and the report page reads it.
        evidence, meta = facets["evidence"], facets["evidence_meta"]
        assert [e["evidence_id"] for e in evidence] == [receipt("17")]
        assert "context" not in evidence[0]
        assert evidence[0]["quote"] == "refund=declined"
        # Five dropped here, one by the agent.
        assert meta["dropped"] == facets["dropped_quotes"] == 6
        assert meta["source"]["ref"] == "17"
        assert meta["coverage_note"] == "Read the refund span and the final answer."
        assert facets["failure_present"] is True
        assert out["17"]["coverage"]["chars_total"] == 182_000

        # A failure needs a receipt that supports it.
        assert out["18"]["facets"]["failure_present"] is False
        media = out["18"]["facets"]["evidence"][1]
        assert media["kind"] == "media" and media["observation"] == "A crushed box."
        assert "error" in out["19"] and "error" in out["20"]
        assert "77" not in out

    def test_reader_uncertainty_and_incomplete_units_survive_ingestion(self, monkeypatch):
        uncertain = unit_result("17")
        uncertain["verification"] = {
            "status": "incomplete", "checks": 1, "held": 0, "rejected": 0, "calls": 1
        }
        uncertain["decisive_unread"] = True
        uncertain["forced"] = {"decisive_unread": "failure_check_unverified"}
        replies = [
            uncertain,
            {"key": "18", "ok": False, "error": {"code": "unreconciled_reading"}},
        ]
        Agent(monkeypatch, **{"/v1/read-units": [(200, {"units": replies})]})
        out = agent.read_units_v2(
            make_run(), {}, ["17", "18"], [], llm.Spend(), engine=V2
        )
        meta = out["17"]["facets"]["evidence_meta"]
        assert meta["verification"] == uncertain["verification"]
        assert meta["decisive_unread"] is True
        assert meta["forced"] == uncertain["forced"]
        assert "error" in out["18"] and "facets" not in out["18"]


class TestPipelineV2:
    @pytest.fixture(autouse=True)
    def prebuilt(self, monkeypatch):
        """Every unit's snapshot is built and stored, unless its pk is in
        ``prebuilt.failing`` (pk -> the builder's code); ``prebuilt.calls``
        are the keys of each pre-build, ``prebuilt.order`` what happened."""
        state = SimpleNamespace(calls=[], failing={}, order=[])

        def prebuild(run, rows, *, stage, pool=None, ttl_s=0.0):
            keys = [str(row.pk) for row in rows]
            state.calls.append((stage, keys, pool is not None))
            state.order.append(("built", keys))
            failed = {k: state.failing[k] for k in keys if k in state.failing}
            return broker.Prebuilt([k for k in keys if k not in failed], failed)

        monkeypatch.setattr(broker, "prebuild", prebuild)
        return state

    def test_a_batch_reads_in_calls_of_eight_and_keeps_what_was_read(self, monkeypatch):
        calls = []

        def read_units_v2(run, context, keys, attributes, spend, *, engine, **_):
            calls.append(list(keys))
            spend.add(0.01 * len(keys))
            if "9" in keys:
                raise llm.InsightModelError("down", unavailable=True)
            return {
                key: {
                    "facets": {
                        **unit_result(key)["facets"],
                        "attributes": {},
                        "evidence": [],
                    },
                    "coverage": {"chars_total": 1000},
                }
                for key in keys
            }

        monkeypatch.setattr(agent, "read_units_v2", read_units_v2)
        units = [SimpleNamespace(pk=i, metrics={"turns": 2}) for i in range(1, 21)]
        saves = []
        error = pipeline._read_with_agent_v2(
            make_run(),
            units,
            [],
            V2,
            lambda read, spend: saves.append((read, round(spend.cost_usd, 6))),
        )

        assert sorted(len(c) for c in calls) == [4, 8, 8]
        assert error is not None and error.unavailable
        # Each call is saved as it returns, with what it spent; the call with
        # unit 9 failed: its units stay pending for a retry, its spend kept.
        assert sorted((sorted(u.pk for u in read), cost) for read, cost in saves) == [
            ([], 0.08),
            (list(range(1, 9)), 0.08),
            (list(range(17, 21)), 0.04),
        ]
        finished = [u for read, _ in saves for u in read]
        assert all(u.status == "done" for u in finished)
        assert finished[0].metrics == {"turns": 2, "coverage": {"chars_total": 1000}}

    def test_a_stopped_run_makes_no_further_call(self, monkeypatch):
        """Stopped while its first call reads (the flag a batch under way
        reads): the batch's other calls are never made, and only what the
        first call read is saved."""
        from django.core.cache import cache

        run = make_run(id=uuid.uuid4())
        sent = []

        def read_units_v2(run, context, keys, attributes, spend, *, engine, **_):
            sent.append(list(keys))
            cache.set(pipeline.STOP_KEY.format(run.id), True)
            return {
                key: {
                    "facets": {**unit_result(key)["facets"], "evidence": []},
                    "coverage": {},
                }
                for key in keys
            }

        monkeypatch.setattr(agent, "read_units_v2", read_units_v2)
        monkeypatch.setattr(pipeline, "V2_PARALLEL_CALLS", 1)
        units = [SimpleNamespace(pk=i, metrics={}) for i in range(1, 25)]
        saves = []
        error = pipeline._read_with_agent_v2(
            run, units, [], V2, lambda read, spend: saves.append(read)
        )

        assert error is None
        assert sent == [[str(i) for i in range(1, 9)]]
        assert [[u.pk for u in read] for read in saves] == [list(range(1, 9))]

    def test_a_mismatch_wins_over_an_outage(self, monkeypatch):
        def read_units_v2(run, context, keys, attributes, spend, *, engine, **_):
            if "1" in keys:
                raise llm.InsightModelError("down", unavailable=True)
            raise agent.InsightEngineMismatch()

        monkeypatch.setattr(agent, "read_units_v2", read_units_v2)
        units = [SimpleNamespace(pk=i, metrics={}) for i in range(1, 17)]
        error = pipeline._read_with_agent_v2(
            make_run(), units, [], V2, lambda read, spend: None
        )
        assert isinstance(error, agent.InsightEngineMismatch)

    def test_a_unit_that_cannot_be_built_is_never_sent(self, monkeypatch, prebuilt):
        sent = []

        def read_units_v2(run, context, keys, attributes, spend, *, engine, **_):
            prebuilt.order.append(("read", list(keys)))
            sent.extend(keys)
            return {
                key: {
                    "facets": {**unit_result(key)["facets"], "evidence": []},
                    "coverage": {},
                }
                for key in keys
            }

        monkeypatch.setattr(agent, "read_units_v2", read_units_v2)
        prebuilt.failing = {"2": "deadline_exceeded", "9": "unit_not_found"}
        prebuilt.failing.update({str(i): "unit_not_found" for i in range(10, 17)})
        units = [SimpleNamespace(pk=i, metrics={"turns": 1}) for i in range(1, 17)]
        saves = []
        error = pipeline._read_with_agent_v2(
            make_run(), units, [], V2, lambda read, spend: saves.append(read)
        )
        finished = [u for read in saves for u in read]

        assert error is None
        # Built before each call, on the batch's own builders; a call whose
        # units all failed to build is not made.
        assert [(s, k) for s, k, _ in prebuilt.calls] == [
            ("read", [str(i) for i in range(1, 9)]),
            ("read", [str(i) for i in range(9, 17)]),
        ]
        assert all(shared for _, _, shared in prebuilt.calls)
        assert prebuilt.order.index(("built", [str(i) for i in range(1, 9)])) < (
            prebuilt.order.index(("read", ["1", *map(str, range(3, 9))]))
        )
        assert sent == ["1", *map(str, range(3, 9))]
        by_pk = {u.pk: u for u in finished}
        assert len(finished) == 16
        assert by_pk[2].status == "failed"
        assert by_pk[2].metrics == {"turns": 1, "evidence_error": "deadline_exceeded"}
        assert "deadline_exceeded" in by_pk[2].error
        assert by_pk[9].metrics["evidence_error"] == "unit_not_found"
        assert by_pk[1].status == "done"

    def test_a_store_that_is_down_leaves_the_call_pending(self, monkeypatch):
        def prebuild(run, rows, *, stage, pool=None, ttl_s=0.0):
            raise llm.InsightModelError("store down", unavailable=True)

        monkeypatch.setattr(broker, "prebuild", prebuild)
        monkeypatch.setattr(agent, "read_units_v2", pytest.fail)
        units = [SimpleNamespace(pk=i, metrics={}) for i in range(1, 5)]
        saves = []
        error = pipeline._read_with_agent_v2(
            make_run(), units, [], V2, lambda read, spend: saves.append(read)
        )
        assert saves == [[]] and error.unavailable


RESULT = {
    "totals": {"count": 40, "users": 30, "sessions": 40},
    "population_size": 40,
    "sample_size": 40,
    "categories": [
        {
            "id": "a",
            "name": "Refund refusals",
            "level": 2,
            "description": "",
            "stats": {"count": 10, "users": 9, "share": 0.25, "failure_rate": 1.0},
            "notable": [],
        }
    ],
}


def v2_units():
    units = [unit(17, category="a", sub="a"), unit(18, category="a", sub="a")]
    for item in units:
        item["facets"]["evidence"] = [
            {
                "kind": "text",
                "evidence_id": receipt(item["key"]),
                "span": "s3",
                "field": "output",
                "quote": "refund=declined",
                "supports": "failure",
            }
        ]
        item["facets"]["evidence_meta"] = {"dropped": 0, "coverage_note": "Read it."}
    return units


def finding(key="17", **fields):
    return {
        "headline": "Refunds are refused",
        "detail": "The refund tool declines in every one of these.",
        "action": "Fix the refund tool.",
        "category_ids": ["a"],
        "unit_keys": [key],
        "evidence": [{"unit_key": key, "evidence_id": receipt(key), "quote": "edited"}],
        **fields,
    }


class TestReportV2:
    def test_the_payload_carries_receipts_and_a_capability_never_text(self):
        run = make_run()
        payload = report.agent_payload(run, RESULT, v2_units(), {"17": "excerpt"})

        assert all("content" not in u for u in payload["units"])
        assert payload["units"][0]["evidence"][0]["evidence_id"] == receipt("17")
        assert "evidence_meta" not in payload["units"][0]["facets"]
        source = payload["evidence_source"]
        assert source["source"] == "broker"
        claims = broker.verify(source["capability"])
        assert (claims["stage"], sorted(claims["units"])) == ("report", ["17", "18"])

        ask = report.agent_payload(run, RESULT, v2_units(), {}, stage="ask")
        assert broker.verify(ask["evidence_source"]["capability"])["stage"] == "ask"

    def test_investigators_get_every_category_in_turn(self):
        units = [
            unit(i, category=f"c{i % 3}", sub=f"c{i % 3}", failure=i % 2 == 0)
            for i in range(200)
        ]
        keys = report._investigated(units)
        assert len(keys) == report.MAX_INVESTIGATED_UNITS
        categories = [units[int(k)]["sub_category_id"] for k in keys[:3]]
        assert sorted(categories) == ["c0", "c1", "c2"]
        assert units[int(keys[0])]["facets"]["failure_present"]

    def test_verified_findings_keep_their_reader_receipts(self):
        reply = {
            "title": "Refunds",
            "findings": [finding()],
            "verification": {"status": "verified", "reason": None},
        }
        summary = report.from_agent(reply, RESULT, v2_units(), {}, v2=True)

        [kept] = summary["findings"]
        assert kept["evidence"] == [
            {
                "unit_id": "u17",
                "quote": "refund=declined",  # the stored quote, not the edit
                "evidence_id": receipt("17"),
                "span": "s3",
                "field": "output",
                "read_by": "reader",
            }
        ]
        assert summary["verification"]["audit"] is True
        assert "unverified" not in summary

    def test_an_image_citation_keeps_what_was_seen_never_as_a_quote(self):
        image = finding(
            evidence=[
                {
                    "unit_key": "17",
                    "kind": "media",
                    "evidence_id": "ev2m|17|m1|full",
                    "span": "s2",
                    "observation": "A crushed box.",
                }
            ]
        )
        reply = {"findings": [image], "verification": {"status": "verified"}}
        [kept] = report.from_agent(reply, RESULT, v2_units(), {}, v2=True)["findings"]
        assert kept["evidence"][0]["quote"] == ""
        assert kept["evidence"][0]["observation"] == "A crushed box."

    def test_a_receipt_of_another_unit_backs_nothing(self):
        other = finding(evidence=[{"unit_key": "17", "evidence_id": receipt("18")}])
        cited = {
            "unit_key": "18",
            "evidence_id": receipt("18", span="s9", start=0, length=17),
            "span": "s9",
            "quote": "timeout after 30s",
        }
        investigator = finding(evidence=[cited])
        # A quote that is not as long as the region it claims to be.
        made_up = finding(evidence=[cited | {"quote": "timeout after 300 seconds"}])
        reply = {
            "findings": [other, investigator, made_up],
            "verification": {"status": "verified"},
        }
        summary = report.from_agent(reply, RESULT, v2_units(), {}, v2=True)
        assert summary["findings"][0]["evidence"] == []
        assert summary["findings"][1]["evidence"][0]["read_by"] == "investigator"
        assert summary["findings"][1]["evidence"][0]["quote"] == "timeout after 30s"
        assert summary["findings"][2]["evidence"] == []

    def test_a_receipt_length_counts_utf16_units_as_the_agent_does(self):
        quote = "price 💶 9"  # the emoji is two UTF-16 units
        item = {"evidence_id": receipt("17", length=10), "span": "s3", "quote": quote}
        assert agent.checked_receipts([item], "17")[0][0]["quote"] == quote
        short = {**item, "evidence_id": receipt("17", length=9)}
        assert agent.checked_receipts([short], "17") == ([], 1)

    @pytest.mark.parametrize(
        ("verification", "shown", "apart", "status"),
        [
            ({"status": "incomplete", "reason": "claims_unjudged"}, 1, 1, "incomplete"),
            ({"status": "failed", "reason": "verifier_failed"}, 0, 2, "failed"),
            ({"model_verifier": "passed"}, 0, 2, "incomplete"),
            (None, 0, 2, "incomplete"),
        ],
        ids=["incomplete", "failed", "no-status", "no-verification"],
    )
    def test_what_the_verifier_did_not_pass_stays_apart(
        self, verification, shown, apart, status
    ):
        reply = {
            "findings": [finding()],
            "unverified_findings": [finding("18", headline="Second finding")],
            "category_insights": [
                {"category_id": "a", "root_cause": "The refund tool is down."}
            ],
            "verification": verification,
        }
        summary = report.from_agent(reply, RESULT, v2_units(), {}, v2=True)

        assert len(summary["findings"]) == shown
        assert len(summary["unverified"]["findings"]) == apart
        assert summary["verification"]["status"] == summary["unverified"]["status"]
        assert summary["verification"]["status"] == status
        assert summary["verification"]["audit"] is False
        assert bool(summary["category_insights"]) is (shown == 1)

    def test_v1_reports_are_read_as_before(self):
        reply = {"findings": [], "verification": {"model_verifier": "passed"}}
        summary = report.from_agent(reply, RESULT, v2_units(), {})
        assert summary["verification"] == {
            "model_verifier": "passed",
            "numbers": True,
            "quotes": False,
            "audit": True,
        }
        assert "unverified" not in summary

    def test_an_unverified_answer_is_not_the_answer(self):
        paragraph = {"text": "Refunds are refused.", "unit_keys": ["17"]}
        reply = {
            "answer": [],
            "unverified_answer": [paragraph],
            "verification": {"status": "failed", "reason": "verifier_failed"},
        }
        out = report._answer_payload(reply, RESULT, v2_units(), {}, v2=True)
        assert out["answer"] == []
        assert out["unverified_answer"][0]["text"] == "Refunds are refused."
        assert out["verification"] == {"status": "failed", "reason": "verifier_failed"}

        # An answer without a verdict is not presented as verified either.
        out = report._answer_payload(
            {"answer": [paragraph]}, RESULT, v2_units(), {}, v2=True
        )
        assert out["answer"] == [] and len(out["unverified_answer"]) == 1
        verified = {**reply, "answer": [paragraph], "unverified_answer": []}
        verified["verification"] = {"status": "verified"}
        out = report._answer_payload(verified, RESULT, v2_units(), {}, v2=True)
        assert len(out["answer"]) == 1 and out["unverified_answer"] == []

    def test_the_namers_audit_fails_closed_on_v2(self):
        assert agent.naming_audit({"subs": []}, V2) == {
            "status": "incomplete",
            "reason": "audit_missing",
        }
        failed = {"audit": {"status": "failed", "reason": "audit_failed"}}
        assert agent.naming_audit(failed, V2) == {
            "status": "failed",
            "reason": "audit_failed",
        }
        assert agent.naming_audit({"subs": []}, {"kind": "omega"}) == {}


class TestEstimates:
    def test_a_run_is_sized_for_the_engine_it_is_pinned_to(self, monkeypatch):
        from tracer.views import insights as views

        monkeypatch.setattr(agent, "enabled", lambda: True)
        # An agent and a secret would suggest v2, but the run was pinned to v1.
        v1 = {"kind": "omega", "contract": "insights-agent/v1"}
        assert views._sizing("sessions", None, v1) == {
            "contract": "insights-agent/v1",
            "unit": "sessions",
        }
        assert views._sizing("sessions", None, V2)["contract"] == "insights-agent/v2"
        assert views._sizing("sessions", None, {"kind": "builtin"})["contract"] is None
        assert views._sizing("sessions")["contract"] == "insights-agent/v2"
        assert billing.run_cost(100, **views._sizing("sessions", None, v1)) < (
            billing.run_cost(100, **views._sizing("sessions", None, V2))
        )

    def test_v1_and_the_built_in_engine_cost_what_they_did(self):
        assert billing.run_cost(300) == billing.run_cost(
            300, contract=None, unit="users"
        )
        assert billing.read_tokens("insights-agent/v1", "users") == billing.READ_TOKENS

    def test_a_v2_unit_costs_its_size_up_to_the_readers_budget(self):
        v2 = "insights-agent/v2"
        small = billing.read_tokens(v2, "traces", 8_000)
        assert small == (4_000 + 2_000, 1_000)  # read whole in one call
        session = billing.read_tokens(v2, "sessions")
        assert session == billing.read_tokens(v2, "sessions", 60_000)
        huge = billing.read_tokens(v2, "users", 5_000_000)
        assert huge == billing.read_tokens(v2, "users", billing.V2_BUDGET_CHARS)
        assert small[0] < session[0] < huge[0]
        costs = [
            billing.run_cost(100, contract=v2, unit="sessions", unit_chars=chars)
            for chars in (10_000, 60_000, 180_000)
        ]
        assert costs == sorted(costs) and costs[0] < costs[-1]
        assert billing.run_cost(100, contract=v2, unit="sessions") > billing.run_cost(
            100
        )
        left = billing.credits(costs[1])
        fits = billing.largest_sample(
            left, 10, 1000, contract=v2, unit="sessions", unit_chars=60_000
        )
        assert fits == 100


class TestUnitTextForV2:
    def test_units_whose_roots_are_empty_are_kept_with_their_last_answer(
        self, monkeypatch
    ):
        from ee.agenthub.insights import units as units_module

        def root(trace, text_in, text_out):
            return SimpleNamespace(
                trace_id=trace,
                trace_session_id="S1",
                end_user_id=None,
                attrs_string={},
                input=text_in,
                output=text_out,
                start_time=datetime(2026, 10, 1, tzinfo=UTC),
                end_time=None,
                trace_name="agent",
                name="agent",
                status="OK",
                latency_ms=10,
                cost=0,
                total_tokens=0,
            )

        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def roots_by_trace_ids(self, ids, project_id):
                return [root("T1", "", ""), root("T2", "hi", "final answer")]

            def roots_by_session_ids(self, ids, project_id):
                return [root("T2", "hi", "final answer"), root("T3", "and?", "")]

        monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", Reader)
        monkeypatch.setattr(units_module, "_user_labels", lambda ids: {})

        assert set(units_module.load_units("traces", PROJECT, ["T1", "T2"])) == {"T2"}
        # As a v2 run loads them: kept when empty, steps left to the agent.
        kept = units_module.load_units(
            "traces", PROJECT, ["T1", "T2"], keep_empty=True, read_steps=False
        )
        assert set(kept) == {"T1", "T2"}
        assert kept["T2"].output == "final answer" and kept["T1"].output == ""
        # What the example panel shows: the exchange, or nothing for a root
        # without text (never empty "User:" lines).
        assert kept["T2"].content == "User: hi\nAssistant: final answer"
        assert kept["T1"].content == ""
        session = units_module.load_units(
            "sessions", PROJECT, ["S1"], keep_empty=True, read_steps=False
        )
        assert session["S1"].content == (
            "[Turn 1] User: hi\nAssistant: final answer\n\n"
            "[Turn 2] User: and?\nAssistant: "
        )

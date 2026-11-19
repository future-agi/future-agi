import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx
import pytest
from ctxhub_support.application import Application
from ctxhub_support.business import Business, server
from ctxhub_support.entrypoint import fixture_url, reset
from ctxhub_support.registry import Registry
from ctxhub_support.store import Store, canonical, digest
from fi.platform import Client, ContextAuth
from support import BINDING, RUNTIME, SESSION, TOKEN, FixtureRegistry, serving

CASE = {
    "case_id": "refund-boundary",
    "order_id": "synthetic-order-1",
    "request": "Refund 25 units, purchased 30 days ago.",
}
REFUND = "00000000-0000-4000-8000-000000000001"
CHARGEBACK = "00000000-0000-4000-8000-000000000002"


def call(name, args=None, ident="call-1"):
    return {
        "id": ident,
        "type": "function",
        "function": {"name": name, "arguments": canonical(args or {})},
    }


def message(*calls, text=None):
    return {
        "role": "assistant",
        "content": text,
        **({"tool_calls": list(calls)} if calls else {}),
    }


class Model:
    def __init__(self, messages):
        self.messages = list(messages)
        self.requests = []

    def handle(self, request):
        self.requests.append(json.loads(request.content))
        if not self.messages:
            return httpx.Response(409, json={"error": "script_exhausted"})
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json={"choices": [{"message": item}]})


@pytest.fixture
def harness(tmp_path):
    with ExitStack() as stack:
        fixture = FixtureRegistry()
        registry_url = stack.enter_context(serving(fixture.server()))
        sdk = Client(auth=ContextAuth(TOKEN), base_url=registry_url, max_retries=0)
        stack.callback(sdk.close)
        business = Business(tmp_path / "business.sqlite")
        business.seed(CASE["order_id"])
        business_url = stack.enter_context(serving(server(business)))
        business_http = stack.enter_context(
            httpx.Client(base_url=business_url, trust_env=False)
        )
        store = Store(tmp_path / "state.sqlite")
        stack.callback(store.close)
        model = Model(
            [
                message(call("load_skill", {"skill_id": REFUND})),
                message(call("issue_refund", {"amount": 25}, "refund-1")),
                message(text="Refund applied."),
            ]
        )
        provider = stack.enter_context(
            httpx.Client(
                base_url="http://fixture.invalid/v1/",
                transport=httpx.MockTransport(model.handle),
            )
        )
        registry = Registry(sdk, BINDING, RUNTIME)
        app = Application(
            store=store, registry=registry, provider=provider, business=business_http
        )
        yield app, fixture, business, model, store


def test_refund_real_business_receipt_not_final_text(harness):
    app, fixture, business, model, store = harness
    result = app.run(thread="one", case=CASE)
    assert result["status"] == "completed", result
    assert result["selected"] == [REFUND]
    assert business.ledger()["operations"][0]["amount"] == 25
    assert business.ledger()["request_count"] == 1
    catalog = next(
        event for event in store.events("one") if event["kind"] == "catalog"
    )["body"]["members"]
    assert len(fixture.catalog["skills"]) == 15 and len(catalog) == 14
    assert len([s for s in fixture.catalog["skills"] if s["unsupported"]]) == 1
    assert not any("resolve" in request["path"] for request in fixture.requests)
    assert len(model.requests) == 3


def test_outage_after_receipt_before_checkpoint_then_process_restart(harness):
    app, fixture, business, model, store = harness

    def interrupt(call, result):
        if call["function"]["name"] == "issue_refund":
            fixture.available = False

    app.after_receipt = interrupt
    paused = app.run(thread="one", case=CASE)
    assert paused["status"] == "paused", paused
    assert business.ledger()["request_count"] == 1
    assert len(model.requests) == 2  # No next model request under registry outage.
    saved = store.load("one")
    assert not any(m.get("tool_call_id") == "refund-1" for m in saved["messages"])
    assert (
        store.receipt(
            "one", "refund-1", {"name": "issue_refund", "arguments": '{"amount":25}'}
        )["status"]
        == "applied"
    )
    original = saved["lock"]
    fixture.available = True
    app.after_receipt = None
    restarted = Store(store.path)
    try:
        app.store = restarted
        completed = app.run(thread="one", case=CASE, expected_lock=original)
        assert completed["status"] == "completed", completed
        assert completed["session_id"] == paused["session_id"] == SESSION
        assert fixture.opens[-1]["resume_session_id"] == SESSION
        assert business.ledger()["request_count"] == 1
        events = restarted.events("one")
        receipt_seq = [e["sequence"] for e in events if e["kind"] == "tool_receipt"][-1]
        assert any(
            e["sequence"] > receipt_seq and e["kind"] == "checkpoint" for e in events
        )
    finally:
        restarted.close()


def test_lost_business_response_reconciles_idempotently(harness):
    app, _fixture, business, _model, _store = harness
    original = app.business

    class LostOnce:
        lost = False

        def post(self, *args, **kwargs):
            response = original.post(*args, **kwargs)
            if not self.lost:
                self.lost = True
                raise httpx.ReadError("synthetic lost response")
            return response

    app.business = LostOnce()
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert len(business.ledger()["operations"]) == 1
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    assert business.ledger()["request_count"] == 2
    assert len(business.ledger()["operations"]) == 1


@pytest.mark.parametrize(
    "failure", ["available", "allowed", "lost_mapping", "mutate_resume"]
)
def test_ineligible_or_unknown_session_never_re_resolves_or_calls_model(
    harness, failure
):
    app, fixture, business, model, _store = harness
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    setattr(fixture, failure, failure in {"lost_mapping", "mutate_resume"})
    result = app.run(thread="one", case=CASE)
    assert result["status"] == "paused", result
    assert len(model.requests) == 3
    assert business.ledger()["request_count"] == 1
    assert not any("resolve" in request["path"] for request in fixture.requests)


def test_snapshot_corruption_fails_before_model(harness):
    app, fixture, business, model, _store = harness
    fixture.corrupt_file = True
    result = app.run(thread="one", case=CASE)
    assert result["status"] == "paused", result
    assert not model.requests and not business.ledger()["operations"]


def test_model_unknown_is_not_silently_retried(harness):
    app, _fixture, _business, model, _store = harness
    model.messages = [httpx.ReadError("lost response")]
    first = app.run(thread="one", case=CASE)
    assert first["reason"] == "model_response_unknown_requires_operator"
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert len(model.requests) == 1


def test_text_pass_does_not_imply_business_pass(harness):
    app, _fixture, business, model, store = harness
    model.messages = [message(text="Refund successfully processed for 25 units.")]
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    assert business.ledger()["operations"] == []
    assert not any(e["kind"] == "tool_result" for e in store.events("one"))


def test_chargeback_near_miss_has_no_refund(harness):
    app, _fixture, business, model, _store = harness
    case = {
        **CASE,
        "order_id": "synthetic-dispute",
        "case_id": "chargeback-near-miss",
        "request": "I disputed the card transaction.",
    }
    business.seed(case["order_id"], disputed=True)
    model.messages = [
        message(call("load_skill", {"skill_id": CHARGEBACK})),
        message(call("open_chargeback", ident="cb-1")),
        message(text="Dispute opened."),
    ]
    result = app.run(thread="dispute", case=case)
    assert result["selected"] == [CHARGEBACK] and REFUND not in result["selected"]
    assert [row["action"] for row in business.ledger()["operations"]] == [
        "open_chargeback"
    ]


def test_shipping_neighbor_preserved(harness):
    app, _fixture, business, model, _store = harness
    shipping = "00000000-0000-4000-8000-000000000003"
    model.messages = [
        message(call("load_skill", {"skill_id": shipping})),
        message(call("track_shipment", ident="ship-1")),
        message(text="Shipment tracked."),
    ]
    result = app.run(
        thread="shipping",
        case={**CASE, "case_id": "shipping-neighbor", "request": "Track the shipment"},
    )
    assert result["status"] == "completed" and result["selected"] == [shipping]
    assert [row["action"] for row in business.ledger()["operations"]] == [
        "track_shipment"
    ]


@pytest.mark.parametrize(
    "messages",
    [
        [
            message(
                call("issue_refund", {"amount": 25}),
                call("issue_refund", {"amount": 25}),
            )
        ],
        [message(call("unknown_tool"))],
    ],
)
def test_invalid_model_tools_do_not_execute(harness, messages):
    app, _fixture, business, model, _store = harness
    model.messages = messages
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert not business.ledger()["operations"]


def test_business_backend_concurrent_keys_and_conflicting_amount(tmp_path):
    business = Business(tmp_path / "business.sqlite")
    business.seed("synthetic-concurrent")
    body = {"action": "issue_refund", "order_id": "synthetic-concurrent", "amount": 25}
    key = digest({k: body[k] for k in ("order_id", "action")})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: business.execute(key, body), range(16)))
    assert all(result == results[0] for result in results)
    assert len(business.ledger()["operations"]) == 1
    assert business.execute(key, {**body, "amount": 24})[0] == 409


def test_new_thread_requires_review_and_does_not_copy_history(harness):
    app, _fixture, _business, _model, _store = harness
    with pytest.raises(ValueError):
        reset({"schema": 1, "case": CASE, "reconstruction_reviewed": False})
    first = reset({"schema": 1, "case": CASE, "reconstruction_reviewed": True})
    second = reset({"schema": 1, "case": CASE, "reconstruction_reviewed": True})
    assert first["thread"] != second["thread"]
    assert "session" not in second and "messages" not in second
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    with pytest.raises(ValueError, match="identity_conflict"):
        app.run(thread="one", case={**CASE, "request": "different request"})
    app.registry.runtime_digest = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="identity_conflict"):
        app.run(thread="one", case=CASE)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://user:pass@localhost/",
        "http://example.com/",
        "http://localhost/?token=secret",
    ],
)
def test_no_live_model_or_business_fallback(url):
    with pytest.raises(ValueError):
        fixture_url(url)

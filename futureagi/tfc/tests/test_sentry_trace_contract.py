"""Check the linkage the initialized SDK sends, not just its pre-send dictionaries."""

import time

import pytest
import sentry_sdk
from sentry_sdk.tracing_utils import Baggage

from tfc.logging import sentry
from tfc.tests.sentry_capture import MemoryWire


def wait_samples():
    profile = sentry_sdk.get_isolation_scope().profile
    assert profile is not None and profile.sampled
    deadline = time.monotonic() + 5
    while profile.unique_samples < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert profile.unique_samples >= 2


@pytest.mark.parametrize("case", ["ordinary", "child", "sampling"])
def test_trace_wire_contract(monkeypatch, case):
    wire = MemoryWire()
    global_scope = sentry_sdk.get_global_scope()
    previous = global_scope.client
    original_init = sentry_sdk.init

    def use_memory(**options):
        return original_init(transport=wire, **options)

    monkeypatch.setattr(sentry_sdk, "init", use_memory)
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(
        sentry,
        "SENTRY_DSN",
        "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@localhost.invalid/1",
    )
    monkeypatch.setattr(sentry, "TRACES_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(sentry, "PROFILES_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(sentry, "_initialized_components", set())
    try:
        with sentry_sdk.isolation_scope() as scope:
            scope.clear_breadcrumbs()
            assert sentry.init_sentry(component="temporal")
            assert sentry_sdk.get_client().transport is wire
            kw = {}
            if case == "sampling":
                kw["baggage"] = Baggage(
                    {"trace_id": "0" * 32, "sampled": "true"}, mutable=False
                )
            with sentry_sdk.start_transaction(
                name="QA trace check", op="qa.root", **kw
            ) as root:
                with root.start_child(op="qa.sibling") as sibling:
                    pass
                if case == "child":
                    with root.start_child(op="qa.child", span_id="0" * 16):
                        pass
                root.set_context("response", {"status_code": 503})
                wait_samples()
            if case == "ordinary":
                try:
                    raise RuntimeError("QA ordinary condition")
                except RuntimeError as exc:
                    sentry_sdk.capture_exception(exc)
    finally:
        try:
            if global_scope.client is not previous:
                global_scope.client.flush()
                global_scope.client.close()
        finally:
            global_scope.set_client(previous)

    [(headers, txn)] = [
        (h, body) for h, kind, body in wire.items if kind == "transaction"
    ]
    [profile] = [body for _, kind, body in wire.items if kind == "profile"]
    trace = txn["contexts"]["trace"]
    assert trace["trace_id"] == root.trace_id
    assert trace["span_id"] == root.span_id
    assert txn["contexts"]["response"] == {"status_code": 503}
    [child] = txn["spans"]
    assert child["op"] == "qa.sibling"
    assert child["trace_id"] == root.trace_id
    assert child["span_id"] == sibling.span_id
    assert child["parent_span_id"] == root.span_id
    [linked] = profile["transactions"]
    assert linked["id"] == txn["event_id"]
    assert linked["trace_id"] == root.trace_id
    assert profile["profile"]["samples"]
    if case == "sampling":
        assert "trace" not in headers
    else:
        assert headers["trace"]["trace_id"] == root.trace_id

    if case == "ordinary":
        errors = [
            error
            for _, kind, body in wire.items
            if kind == "event"
            for error in body.get("exception", {}).get("values", [])
        ]
        assert any(
            error.get("type") == "RuntimeError"
            and error.get("value") == "QA ordinary condition"
            and any(
                frame.get("function") == "test_trace_wire_contract"
                and frame.get("lineno", 0) > 0
                for frame in error["stacktrace"]["frames"]
            )
            for error in errors
        )

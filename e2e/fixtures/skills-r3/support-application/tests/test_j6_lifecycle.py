"""J6 operator lifecycle: inspection never resumes; closure is explicit and final."""

import pytest
from ctxhub_support.lifecycle import classify, inspect
from ctxhub_support.reporting import ReportError, report_closed, report_deliveries
from support import BINDING, SESSION
from test_application import CASE


class Recorder:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def request(self, method, path, body=None, **_):
        self.calls.append({"method": method, "path": path, "body": body})
        if self.fail:
            raise self.fail
        return {"allowed": True}


class Status(Exception):
    def __init__(self, status_code):
        super().__init__(status_code)
        self.status_code = status_code


def test_delivery_reported_once_with_exact_locked_root(harness):
    app, fixture, business, model, store = harness
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    reporter = Recorder()
    assert report_deliveries(store, "one", reporter, BINDING) == 1
    assert report_deliveries(store, "one", reporter, BINDING) == 0
    receipt = reporter.calls[0]["body"]
    assert reporter.calls[0]["path"].endswith(f"/bindings/{BINDING}/receipts/")
    assert (
        receipt["stage"] == "instructions_delivered"
        and receipt["session_id"] == SESSION
    )
    assert receipt["details"]["path"] == "AGENTS.md"
    assert receipt["details"]["ranges"] == [[0, len(fixture.files["AGENTS.md"])]]


def test_changed_staged_root_is_never_reported_as_delivered(harness):
    app, fixture, business, model, store = harness
    app.run(thread="one", case=CASE)
    state = store.load("one")
    state["messages"][0]["content"] = "tampered" + state["messages"][0]["content"]
    store.save("one", state)
    with pytest.raises(ReportError):
        report_deliveries(store, "one", Recorder(), BINDING)


def test_inspection_classifies_lost_mapping_without_opening_a_session(harness):
    app, fixture, business, model, store = harness
    app.run(thread="one", case=CASE)
    opens = len(fixture.opens)
    for status, reason in [
        (404, "thread_mapping_unavailable"),
        (409, "thread_mapping_unavailable"),
        (401, "authority_unavailable"),
        (503, "registry_unavailable"),
    ]:
        client = Recorder(Status(status))
        result = inspect(store, "one", client, BINDING)
        assert result["resumable"] is False and result["reason"] == reason
        assert [call["path"].endswith("/access-check/") for call in client.calls] == [
            True
        ]
    assert len(fixture.opens) == opens  # Inspection never re-resolves or reopens.
    assert classify(None) == "registry_unavailable"


def test_closed_thread_is_never_resumed(harness):
    app, fixture, business, model, store = harness
    app.run(thread="one", case=CASE)
    reporter = Recorder()
    state = report_closed(store, "one", reporter, BINDING, "replaced_by_clean_thread")
    assert reporter.calls[0]["body"]["stage"] == "closed"
    state["phase"] = "closed"
    store.save("one", state)
    requests = business.ledger()["request_count"]
    with pytest.raises(ValueError, match="thread_closed"):
        app.run(thread="one", case=CASE)
    assert business.ledger()["request_count"] == requests
    assert inspect(store, "one", Recorder(), BINDING)["reason"] == "thread_closed"

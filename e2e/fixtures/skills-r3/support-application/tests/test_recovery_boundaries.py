import httpx
import pytest
from fi.context.adapters.simulation_entrypoint import execute
from test_application import CASE, message
from test_native_entrypoint import payload


def test_first_snapshot_failure_retains_session_mapping(harness):
    app, fixture, _business, _model, store = harness
    fixture.corrupt_file = True
    assert app.run(thread="first-load", case=CASE)["status"] == "paused"
    retained = store.load("first-load")["session"]["session_id"]
    fixture.corrupt_file = False
    assert app.run(thread="first-load", case=CASE)["status"] == "completed"
    assert fixture.opens[-1]["resume_session_id"] == retained
    assert (
        sum(request["path"].endswith("/manifest/") for request in fixture.requests) >= 4
    )


def test_registry_authority_is_part_of_checkpoint_identity(harness):
    app, _fixture, _business, model, _store = harness
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    app.registry.authority = {
        **app.registry.authority,
        "origin": "https://different.invalid",
    }
    with pytest.raises(ValueError, match="thread_identity_conflict"):
        app.run(thread="one", case=CASE)
    assert len(model.requests) == 3


def test_unknown_first_model_response_does_not_claim_delivery(harness):
    app, _fixture, _business, model, store = harness
    model.messages = [httpx.ReadError("unknown provider response")]
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert not any(
        event["kind"] in {"root_delivery", "file_delivery"}
        for event in store.events("one")
    )


def test_skill_read_paused_before_next_model_call_is_not_delivery(harness):
    app, fixture, _business, model, store = harness
    app.after_receipt = lambda *_: setattr(fixture, "available", False)
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert len(model.requests) == 1
    assert any(event["kind"] == "file_read" for event in store.events("one"))
    assert not any(event["kind"] == "file_delivery" for event in store.events("one"))
    fixture.available = True
    app.after_receipt = None
    assert app.run(thread="one", case=CASE)["status"] == "completed"
    assert any(event["kind"] == "file_delivery" for event in store.events("one"))


def test_wrong_arm_identity_cannot_be_ingested():
    received = []
    with pytest.raises(ValueError, match="application_context_identity_mismatch"):
        execute(
            payload(),
            launch_cell=lambda **_: {
                "status": "completed",
                "evidence_class": "synthetic fixture",
                "context_digest": "wrong-arm",
            },
            ingest=received.append,
        )
    assert not received


def test_malformed_call_is_a_paused_checkpoint_not_a_crash(harness):
    app, _fixture, business, model, _store = harness
    model.messages = [message("not-a-tool-object")]
    assert app.run(thread="one", case=CASE)["status"] == "paused"
    assert not business.ledger()["operations"]

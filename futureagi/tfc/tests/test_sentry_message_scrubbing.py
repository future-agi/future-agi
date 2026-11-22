"""Check both envelopes, including breadcrumbs retained on the isolation scope."""

import json
import logging
import time

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.tracing import Transaction

from tfc.logging import sentry
from tfc.logging.sentry_payloads import PayloadBoundsIntegration
from tfc.tests.sentry_capture import sentry_capture

MARKER = "synthetic-payload-marker"
LOGGER = "tfc.tests.ordinary"


@pytest.mark.parametrize("at_ingestion", [True, False])
def test_ordinary_event_and_next_event(at_ingestion):
    # With no ingestion hook the raw breadcrumb remains on the SDK scope;
    # send-time scrubbing must stand on its own for *each* envelope.
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        before_breadcrumb=sentry._before_breadcrumb if at_ingestion else None,
        include_local_variables=False,
    ) as transport:
        crumb = {"method": "POST", "detail": MARKER}
        sentry_sdk.add_breadcrumb(category="http", message=MARKER, data=crumb)
        logging.getLogger(LOGGER).error(
            "job %s stopped", MARKER, extra={"note": MARKER}
        )
        sentry_sdk.capture_event(
            {
                "exception": {
                    "values": [{"type": "LookupError", "value": "row missing"}]
                },
                "extra": {
                    "count": 2,
                    "note": MARKER,
                    "input_tokens": 17,
                    "max_tokens": MARKER,
                },
                "request": {
                    "method": "GET",
                    "headers": {"Cookie": f"first=ok; second={MARKER}"},
                    "cookies": {"session": MARKER},
                },
            }
        )
        first, second = transport.events
        for event in (first, second):
            assert MARKER not in json.dumps(event)
            trail = event["breadcrumbs"]["values"]
            assert any(
                c.get("category") == "http" and c.get("data") == {"method": "POST"}
                for c in trail
            )
        assert first["logger"] == LOGGER
        assert first["logentry"] == {
            "message": "job %s stopped",
            "params": [sentry._REDACTED],
        }
        assert second["exception"]["values"][-1]["type"] == "LookupError"
        assert second["exception"]["values"][-1]["value"] == "row missing"
        assert second["extra"] == {"count": 2, "input_tokens": 17}
        assert second["request"]["method"] == "GET"
        assert second["request"]["cookies"] == sentry._REDACTED
        assert crumb["detail"] == MARKER


@pytest.mark.parametrize(
    ("message", "kept"),
    [
        ("job failed (max_tokens=4096)", True),
        ("job failed token_count=12", True),
        ("job failed synthetic_tokens=12", False),
        (f"job failed input_tokens={MARKER}", False),
        (f"Cookie: session=ok; additional={MARKER}", False),
        (f"job failed password={MARKER} trailing detail", False),
    ],
)
def test_bounded_message_policy(message, kept):
    with sentry_capture(include_local_variables=False) as transport:
        sentry_sdk.capture_event({"message": message})
        sentry_sdk.capture_message("next diagnostic")
        first, second = transport.events
        assert first["message"] == (message if kept else sentry._REDACTED)
        assert second["message"] == "next diagnostic"
        assert MARKER not in json.dumps(transport.events)


def test_small_limits_discard_uninspectable_payloads(monkeypatch):
    # Exercise the size boundaries with ordinary, small fixtures.
    monkeypatch.setattr(sentry, "_MAX_ITEMS", 3)
    monkeypatch.setattr(sentry, "_MAX_TEXT", 40)
    deep = MARKER
    for _ in range(8):
        deep = [deep]
    with sentry_capture(include_local_variables=False) as transport:
        sentry_sdk.capture_event(
            {
                "message": "ordinary synthetic diagnostic longer than forty characters",
                "contexts": {"fixture": {"nested": deep}},
                "extra": {"note": MARKER},
                "request": {"headers": dict.fromkeys(("a", "b", "c", "d"), MARKER)},
            }
        )
        sentry_sdk.capture_message("next diagnostic")
        first, second = transport.events
        assert first["message"] == sentry._REDACTED
        assert "fixture" not in first["contexts"]
        assert second["message"] == "next diagnostic"
        assert MARKER not in json.dumps(transport.events)


@pytest.mark.parametrize("url", ["/jobs/", "/telemetry/register/"])
def test_over_budget_stack_keeps_the_exception_type(monkeypatch, url):
    monkeypatch.setattr(sentry, "_MAX_ITEMS", 2)
    frames = [{"function": name} for name in ("start", "read", "finish")]
    with sentry_capture(include_local_variables=False) as transport:
        sentry_sdk.add_breadcrumb(category="job", message=MARKER)
        sentry_sdk.capture_event(
            {
                "request": {"url": url},
                "exception": {
                    "values": [
                        {
                            "type": "LookupError",
                            "value": MARKER,
                            "stacktrace": {"frames": frames},
                        }
                    ]
                },
            }
        )
        sentry_sdk.capture_message("next diagnostic")
        first, second = transport.events
        [error] = first["exception"]["values"]
        assert error["type"] == "LookupError"
        assert error["value"] == sentry._REDACTED
        assert second["message"] == "next diagnostic"
        assert MARKER not in json.dumps(transport.events)


def test_transaction_with_null_frames_keeps_the_event():
    event = sentry._before_send_transaction(
        {"stacktrace": {"frames": None}, "message": MARKER}, {}
    )
    assert event == {"stacktrace": {}, "message": sentry._REDACTED}


def test_span_exception_text_is_not_sent_with_the_transaction_or_next_event():
    with sentry_capture(
        before_send_transaction=sentry._before_send_transaction,
        traces_sample_rate=1.0,
        include_local_variables=False,
        max_value_length=sentry._MAX_TEXT,
    ) as transport:
        # A truncated custom value also puts its key in the SDK's _meta tree.
        sentry_sdk.set_context("job", {MARKER: "x" * 10_000})
        with sentry_sdk.start_transaction(name="process job", op="task") as txn:
            txn.set_context("error", {"detail": MARKER})
            txn.set_data("exception.message", MARKER)
            txn.set_tag("error.detail", MARKER)
            with sentry_sdk.start_span(op="task.read", description=MARKER) as span:
                span.set_data("exception.type", "LookupError")
                span.set_data("exception.message", MARKER)
                span.set_tag("error.detail", MARKER)
                sentry_sdk.add_breadcrumb(category="job", message=MARKER)
                sentry_sdk.capture_event(
                    {
                        "exception": {
                            "values": [
                                {
                                    "type": "LookupError",
                                    "value": f"password={MARKER}",
                                    "mechanism": {
                                        "type": "generic",
                                        "handled": True,
                                        "description": MARKER,
                                    },
                                }
                            ]
                        }
                    }
                )
        sentry_sdk.capture_message("next diagnostic")

        issue, transaction, following = transport.envelopes
        assert MARKER.encode() not in issue.serialize()
        assert MARKER.encode() not in transaction.serialize()
        assert MARKER.encode() not in following.serialize()
        first, second = transport.events
        [error] = first["exception"]["values"]
        assert error["type"] == "LookupError"
        assert error["value"] == sentry._REDACTED
        assert error["mechanism"] == {"type": "generic", "handled": True}
        assert "job" not in first["contexts"]
        assert "job" not in second["contexts"]
        [item] = transaction.items
        assert item.headers["type"] == "transaction"
        event = item.payload.json
        assert set(event["contexts"]) == {"trace"}
        assert event["contexts"]["trace"]["trace_id"] == txn.trace_id
        [child] = event["spans"]
        assert child["span_id"] == span.span_id
        assert child["op"] == "task.read"
        assert child["data"] == {"exception.type": "LookupError"}
        assert second["message"] == "next diagnostic"


def test_rebuilt_fields_discard_their_serializer_annotations():
    with sentry_capture(
        before_send_transaction=sentry._before_send_transaction,
        traces_sample_rate=1.0,
        max_value_length=128,
    ) as transport:
        # Truncation gives the SDK a reason to annotate each original field.
        note = "x" * 160
        sentry_sdk.capture_event(
            {"message": "job finished", "extra": {"count": 2, MARKER: note}}
        )
        with sentry_sdk.start_transaction(name="process job", op="task") as txn:
            txn.set_tag("component", "worker")
            txn.set_tag(MARKER, note)

        issue, transaction = transport.envelopes
        [event] = transport.events
        assert event["extra"] == {"count": 2}
        assert "extra" not in event.get("_meta", {})
        [item] = transaction.items
        assert item.payload.json["tags"]["component"] == "worker"
        assert "tags" not in item.payload.json.get("_meta", {})
        assert MARKER.encode() not in issue.serialize()
        assert MARKER.encode() not in transaction.serialize()


def test_unknown_capture_type_keeps_the_exception_chain():
    with sentry_capture() as transport:
        sentry_sdk.capture_event(
            {
                "exception": {
                    "values": [
                        {
                            "type": "LookupError",
                            "mechanism": {
                                "type": "job.runner",
                                "handled": False,
                                "exception_id": 1,
                                "parent_id": 0,
                                "description": MARKER,
                            },
                        },
                        {
                            "type": "RuntimeError",
                            "mechanism": {
                                "type": "logging",
                                "handled": True,
                                "exception_id": 0,
                            },
                        },
                    ]
                }
            }
        )
        [event] = transport.events
        cause, parent = event["exception"]["values"]
        assert cause["mechanism"] == {
            "type": "generic",
            "handled": False,
            "exception_id": 1,
            "parent_id": 0,
        }
        assert parent["mechanism"] == {
            "type": "logging",
            "handled": True,
            "exception_id": 0,
        }


@pytest.mark.parametrize("status", [200, 503, "200", True, 99, 600])
def test_response_context_keeps_only_http_status_alongside_trace(status):
    with sentry_capture(
        before_send_transaction=sentry._before_send_transaction,
        traces_sample_rate=1.0,
    ) as transport:
        with sentry_sdk.start_transaction(name="process job", op="http.server") as txn:
            assert isinstance(txn, Transaction)
            txn.set_context(
                "response", {"status_code": status, "headers": {"Example": MARKER}}
            )

        [envelope] = transport.envelopes
        [item] = envelope.items
        contexts = item.payload.json["contexts"]
        assert contexts["trace"]["trace_id"] == txn.trace_id
        assert contexts["trace"]["span_id"] == txn.span_id
        if status in (200, 503):
            assert contexts["response"] == {"status_code": status}
        else:
            assert "response" not in contexts
        assert MARKER.encode() not in envelope.serialize()


def test_over_budget_span_list_is_dropped_without_losing_the_transaction(monkeypatch):
    monkeypatch.setattr(sentry, "_MAX_ITEMS", 2)
    with sentry_capture(
        before_send_transaction=sentry._before_send_transaction,
        traces_sample_rate=1.0,
    ) as transport:
        with sentry_sdk.start_transaction(name="process job", op="task"):
            for _ in range(3):
                with sentry_sdk.start_span(op="task.read", description=MARKER):
                    pass
        sentry_sdk.capture_message("next diagnostic")
        transaction, following = transport.envelopes
        [item] = transaction.items
        assert item.headers["type"] == "transaction"
        assert item.payload.json["transaction"] == "process job"
        assert not item.payload.json.get("spans")
        assert transport.events[0]["message"] == "next diagnostic"
        assert MARKER.encode() not in transaction.serialize()
        assert MARKER.encode() not in following.serialize()


@pytest.mark.parametrize("key", ["trace_id", "span_id"])
def test_zero_root_id_rejects_transaction(key):
    with sentry_capture(
        PayloadBoundsIntegration(),
        before_send_transaction=sentry._before_send_transaction,
    ) as transport:
        trace = {"trace_id": "a" * 32, "span_id": "b" * 16}
        trace[key] = "0" * len(trace[key])
        result = sentry_sdk.capture_event(
            {
                "type": "transaction",
                "transaction": "process job",
                "contexts": {"trace": trace},
            }
        )
        assert result is None
        assert not transport.envelopes


def test_zero_optional_span_and_sampling_ids_are_not_forwarded():
    span = sentry._span_structure(
        {
            "parent_span_id": "0" * 16,
            "dynamic_sampling_context": {"trace_id": "0" * 32, "sampled": "true"},
        }
    )
    assert span == {}


def test_profile_and_transaction_share_trace_despite_over_budget_context():
    with sentry_capture(
        PayloadBoundsIntegration(),
        before_send_transaction=sentry._before_send_transaction,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    ) as transport:
        with sentry_sdk.start_transaction(name="process job", op="task") as txn:
            txn.set_context("error", {"details": [MARKER] * (sentry._MAX_ITEMS + 1)})
            # A short transaction may finish before the SDK takes two samples.
            # Wait for real samples rather than bypassing Profile.valid().
            profile = sentry_sdk.get_isolation_scope().profile
            assert profile is not None and profile.sampled
            deadline = time.monotonic() + 5
            while profile.unique_samples < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert profile.unique_samples >= 2

        [envelope] = transport.envelopes
        items = {item.headers["type"]: item.payload.json for item in envelope.items}
        event = items["transaction"]
        trace = event["contexts"]["trace"]
        assert trace["trace_id"] == txn.trace_id
        assert trace["span_id"] == txn.span_id
        assert "error" not in event["contexts"]
        assert envelope.headers["trace"]["sampled"] == "true"
        assert envelope.headers["trace"]["sample_rate"] == "1.0"
        [linked] = items["profile"]["transactions"]
        assert linked["id"] == event["event_id"]
        assert linked["trace_id"] == txn.trace_id
        assert items["profile"]["profile"]["samples"]
        assert MARKER.encode() not in envelope.serialize()


def test_parameterized_log_entries_preserve_the_template_not_the_rendering():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        include_local_variables=False,
    ) as transport:
        logger = logging.getLogger(LOGGER)
        logger.error("%s stopped after %d retries", "GET", 3)
        logger.error("job stopped: %s", MARKER)
        sentry_sdk.capture_message("next diagnostic")

        safe, unsafe, following = transport.events
        assert safe["logentry"] == {
            "message": "%s stopped after %d retries",
            "params": ["GET", 3],
        }
        assert unsafe["logentry"] == {
            "message": "job stopped: %s",
            "params": [sentry._REDACTED],
        }
        for event in (safe, unsafe):
            assert event["logger"] == LOGGER
            assert event["level"] == "error"
        assert following["message"] == "next diagnostic"
        assert len(transport.envelopes) == 3
        assert all(MARKER.encode() not in e.serialize() for e in transport.envelopes)


def test_usage_log_templates_only_retain_validated_count_arguments():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        include_local_variables=False,
    ) as transport:
        logger = logging.getLogger(LOGGER)
        logger.error("job failed input_tokens=%d", 17)
        logger.error("job %s failed output_tokens=%d", MARKER, 23)
        # Python logging rejects a string for %d before Sentry sees the record.
        # Exercise the same SDK hook with an inconsistent structured log entry.
        sentry_sdk.capture_event(
            {"logentry": {"message": "job failed input_tokens=%d", "params": [MARKER]}}
        )
        logger.error("job failed input_tokens=%s", MARKER)
        sentry_sdk.capture_message("next diagnostic")

        count, mixed, malformed, wrong_placeholder, following = transport.events
        assert count["logentry"] == {
            "message": "job failed input_tokens=%d",
            "params": [17],
        }
        assert mixed["logentry"] == {
            "message": "job %s failed output_tokens=%d",
            "params": [sentry._REDACTED, 23],
        }
        for event in (malformed, wrong_placeholder):
            assert event["logentry"] == {"message": sentry._REDACTED}
        assert following["message"] == "next diagnostic"
        assert all(MARKER.encode() not in e.serialize() for e in transport.envelopes)


def test_formatted_only_entry_keeps_the_logger_and_level():
    with sentry_capture(include_local_variables=False) as transport:
        sentry_sdk.add_breadcrumb(category="job", message=MARKER)
        sentry_sdk.capture_event(
            {
                "logger": LOGGER,
                "level": "error",
                "logentry": {"formatted": MARKER},
            }
        )
        sentry_sdk.capture_message("next diagnostic")
        first, second = transport.events
        assert first["logger"] == LOGGER
        assert first["level"] == "error"
        assert first["logentry"] == {"message": sentry._REDACTED}
        assert second["message"] == "next diagnostic"
        assert MARKER not in json.dumps(transport.events)


def test_uninspectable_arguments_are_not_rendered():
    calls = []

    class Uninspectable:
        def __str__(self):
            calls.append("render")
            return MARKER

        __repr__ = __str__

    production_hook = sentry._get_before_send()

    def inject_after_sdk_normalization(event, hint):
        if event.get("message") == "first diagnostic":
            event["logentry"] = {"message": "job %s", "params": [Uninspectable()]}
            event["extra"] = {"detail": Uninspectable()}
        return production_hook(event, hint)

    with sentry_capture(before_send=inject_after_sdk_normalization) as transport:
        sentry_sdk.capture_message("first diagnostic")
        sentry_sdk.capture_message("next diagnostic")
        first, second = transport.events
        assert first["logentry"] == {"message": "job %s", "params": [sentry._REDACTED]}
        assert second["message"] == "next diagnostic"
        assert MARKER not in json.dumps(transport.events)
        assert calls == []

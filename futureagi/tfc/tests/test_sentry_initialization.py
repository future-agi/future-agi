"""Exercise centralized initialization with the real SDK and an in-memory transport."""

import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest
import sentry_sdk
import structlog
from redis.exceptions import RedisError
from sentry_sdk.utils import iter_event_frames

from tfc.logging import sentry
from tfc.temporal.common.sentry_interceptor import SentryActivityInboundInterceptor
from tfc.utils.payload_storage import get_payload_storage
from tracer.utils import otel, trace_ingestion
from tracer.views.http_otlp import OTLPTraceHTTPView


@pytest.fixture
def initialized_sentry(request, monkeypatch):
    events = []
    real_init = sentry_sdk.init
    # A test can ask for another event level: @pytest.mark.parametrize(..., indirect=True).
    monkeypatch.setenv("SENTRY_LOG_LEVEL", getattr(request, "param", "ERROR"))
    global_scope = sentry_sdk.get_global_scope()
    previous_client = global_scope.client

    def init_with_memory_transport(**options):
        # Keep the application's options and explicit integrations, but avoid
        # network I/O and auto-instrumenting unrelated installed test packages.
        return real_init(
            **options,
            transport=events.append,
            default_integrations=False,
            auto_enabling_integrations=False,
        )

    monkeypatch.setattr(sentry_sdk, "init", init_with_memory_transport)
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(sentry, "IS_STAGING", False)
    monkeypatch.setattr(sentry, "_initialized_components", set())
    with sentry_sdk.isolation_scope():
        try:
            assert sentry.init_sentry(component="temporal", tags={"queue": "tasks_l"})
            yield sentry_sdk.get_client(), events
        finally:
            client = global_scope.client
            if client is not previous_client:
                client.close()
            global_scope.set_client(previous_client)


def test_init_sentry_caps_event_strings_at_8192(initialized_sentry):
    client, events = initialized_sentry

    assert client.options["max_value_length"] == 8192
    assert sentry_sdk.capture_event({"message": "x" * 9000}) is not None
    assert len(events) == 1
    assert len(events[0]["message"]) <= 8192


def test_init_sentry_preserves_strings_at_the_limit(initialized_sentry):
    _client, events = initialized_sentry
    message = "x" * 8192

    assert sentry_sdk.capture_event({"message": message}) is not None
    assert len(events) == 1
    assert events[0]["message"] == message
    assert events[0]["tags"]["component"] == "temporal"
    assert events[0]["tags"]["queue"] == "tasks_l"


# Trace ingestion: its frames hold the raw span batch, which the key-name
# scrubbers cannot see. An event that carries one of those frames sends no frame
# variables at all; an event from other code keeps them.

SPAN_TEXT = "raw-span-sentinel-7f3a jane@acme.com"


def _frame_modules(event):
    return {frame.get("module") for frame in iter_event_frames(event)}


def _assert_sent_without_frame_vars(events, module):
    assert events, "no event reached Sentry"
    for event in events:
        assert module in _frame_modules(event)
        assert [frame for frame in iter_event_frames(event) if "vars" in frame] == []
    assert SPAN_TEXT not in json.dumps(events, default=str)


class _RunActivity:
    """The interceptor after Sentry's: runs the activity inline."""

    async def execute_activity(self, input):
        return input.fn(*input.args)


def _run_ingestion_task():
    """The task body under the worker's Sentry interceptor."""
    task = trace_ingestion.bulk_create_observation_span_task
    activity = SimpleNamespace(
        fn=task._original_func, args=("payload:in", "org", "user", "ws", "json")
    )
    interceptor = SentryActivityInboundInterceptor(_RunActivity())
    return asyncio.run(interceptor.execute_activity(activity))


@contextlib.contextmanager
def _ingestion_batch(**stubs):
    """A batch that holds SPAN_TEXT, with no database, metering or Redis I/O."""
    spans = [{"attributes": {"fi.input.value": SPAN_TEXT}}]
    stubs = {
        "deserialize_trace_payload": mock.Mock(return_value={"resource_spans": spans}),
        "_parse_otel_request": mock.Mock(return_value=spans),
        "normalize_span_attributes": mock.Mock(),
        "bulk_convert_otel_spans_to_observation_spans": mock.Mock(return_value=[]),
        **stubs,
    }
    with (
        mock.patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=True),
        mock.patch.object(
            trace_ingestion.transaction, "atomic", contextlib.nullcontext
        ),
        mock.patch.object(
            trace_ingestion.payload_storage,
            "retrieve",
            return_value=SPAN_TEXT.encode(),
        ),
        mock.patch.multiple(trace_ingestion, **stubs),
    ):
        yield


@pytest.fixture
def payload_store():
    """Request it before initialized_sentry: the store connects on first use, and
    a Redis the test run lacks would otherwise log an ERROR into the events."""
    return get_payload_storage()


@pytest.fixture
def refusing_redis(payload_store, monkeypatch):
    redis = mock.Mock()
    redis.setex.side_effect = RedisError("OOM command not allowed")
    monkeypatch.setattr(payload_store, "_redis_available", True)
    monkeypatch.setattr(payload_store, "_redis_client", redis)
    return redis


def test_a_failed_ingestion_batch_reaches_sentry_without_frame_vars(
    payload_store, initialized_sentry
):
    """The task's catch-all logger.exception and the interceptor each send one."""
    _client, events = initialized_sentry
    with _ingestion_batch(
        _parse_otel_request=mock.Mock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(RuntimeError, match="boom"):
            _run_ingestion_task()

    assert len(events) == 2
    _assert_sent_without_frame_vars(events, "tracer.utils.trace_ingestion")


def test_an_error_logged_inside_the_task_reaches_sentry_without_frame_vars(
    payload_store, initialized_sentry
):
    """A plain ERROR log carries the current stack (attach_stacktrace)."""
    _client, events = initialized_sentry
    log = structlog.get_logger("tracer.utils.adapters.base")
    with _ingestion_batch(
        normalize_span_attributes=mock.Mock(
            side_effect=lambda spans: log.error("adapter_failed")
        )
    ):
        _run_ingestion_task()

    [event] = events
    assert "threads" in event
    _assert_sent_without_frame_vars(events, "tracer.utils.trace_ingestion")


def test_a_span_helper_that_logs_its_own_failure_sends_no_frame_vars(
    initialized_sentry,
):
    """logger.exception keeps only the frames below its catch: no task frame."""
    _client, events = initialized_sentry
    image = otel.MessageContentAttributes.MESSAGE_CONTENT_IMAGE
    key = f"{otel.SpanAttributes.INPUT_MESSAGES}.0.{image}"
    with mock.patch.object(
        otel, "upload_content_to_s3", side_effect=RuntimeError("upload refused")
    ):
        otel.process_attributes({key: SPAN_TEXT, "fi.input.value": SPAN_TEXT})

    [event] = events
    assert "tracer.utils.trace_ingestion" not in _frame_modules(event)
    _assert_sent_without_frame_vars(events, "tracer.utils.otel")


@pytest.mark.parametrize("failure", ["store", "enqueue"])
def test_a_receiver_that_cannot_queue_a_batch_sends_no_frame_vars(
    refusing_redis, initialized_sentry, failure
):
    """The OTLP receiver logs the failure while its frame holds the request body."""
    _client, events = initialized_sentry
    if failure == "enqueue":
        refusing_redis.setex.side_effect = None
    request = mock.Mock(data={"resourceSpans": [{"note": SPAN_TEXT}]})
    with mock.patch.object(
        trace_ingestion.bulk_create_observation_span_task,
        "apply_async",
        side_effect=RuntimeError("Temporal is not reachable"),
    ):
        response = OTLPTraceHTTPView().post(request)

    assert response.status_code == 500
    refusing_redis.setex.assert_called_once()
    assert len(events) == 1
    _assert_sent_without_frame_vars(events, "tracer.views.http_otlp")


@pytest.mark.parametrize("initialized_sentry", ["WARNING"], indirect=True)
def test_a_payload_store_failure_at_warning_level_sends_no_frame_vars(
    refusing_redis, initialized_sentry
):
    """SENTRY_LOG_LEVEL=WARNING turns the store's own failure log into an event."""
    _client, events = initialized_sentry
    with pytest.raises(RuntimeError, match="Failed to store payload"):
        get_payload_storage().store(SPAN_TEXT)

    assert len(events) == 1
    _assert_sent_without_frame_vars(events, "tfc.utils.payload_storage")


def test_events_from_other_code_keep_their_frame_vars(initialized_sentry):
    """A raised exception and an ERROR log outside ingestion keep their locals."""
    _client, events = initialized_sentry
    kept = "visible-local-2b9d"
    try:
        raise RuntimeError("unrelated")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
    structlog.get_logger("accounts.views").error("unrelated_failed")

    assert len(events) == 2
    for event in events:
        assert __name__ in _frame_modules(event)
        frame_vars = [frame.get("vars") for frame in iter_event_frames(event)]
        assert kept in json.dumps(frame_vars, default=str)

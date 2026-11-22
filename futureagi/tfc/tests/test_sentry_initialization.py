"""Exercise centralized initialization with the real SDK and an in-memory transport."""

import asyncio
import contextlib
import json
import logging
import sys
from types import SimpleNamespace
from unittest import mock

import grpc
import jinja2
import pytest
import sentry_sdk
import structlog
from django.core.handlers.asgi import ASGIHandler
from django.core.signals import request_finished, request_started
from django.db import close_old_connections
from django_socio_grpc.settings import grpc_settings
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from redis.exceptions import RedisError
from sentry_sdk.utils import iter_event_frames

from tfc.logging import sentry
from tfc.temporal.common.sentry_interceptor import SentryActivityInboundInterceptor
from tfc.utils.payload_storage import get_payload_storage
from tracer.services.grpc import ObservationSpanService
from tracer.utils import otel, pii_scrubber, trace_ingestion
from tracer.views import langfuse_ingestion
from tracer.views.langfuse_ingestion import LangfuseIngestionView


@pytest.fixture
def initialized_sentry(request, monkeypatch):
    # A test can ask for another event level: @pytest.mark.parametrize(..., indirect=True).
    level = getattr(request, "param", "ERROR")
    with _sentry_for(monkeypatch, "temporal", level, tags={"queue": "tasks_l"}) as pair:
        yield pair


@pytest.fixture
def django_sentry(monkeypatch):
    """The web process's Sentry: its Django integration attaches the request."""
    with _sentry_for(monkeypatch, "django", "ERROR") as pair:
        yield pair


@contextlib.contextmanager
def _sentry_for(monkeypatch, component, level, tags=None):
    events = []
    real_init = sentry_sdk.init
    monkeypatch.setenv("SENTRY_LOG_LEVEL", level)
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
            assert sentry.init_sentry(component=component, tags=tags)
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
# scrubbers cannot see. Keep frame locations, never arbitrary frame variables.

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
    # The task's metering import, stubbed so the run needs no ee/ (OSS boots without it).
    oss = SimpleNamespace(DeploymentMode=SimpleNamespace(is_oss=lambda: True))
    with (
        mock.patch.dict(sys.modules, {"ee.usage.deployment": oss}),
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


def test_a_pii_helper_that_logs_its_own_failure_sends_no_frame_vars(
    initialized_sentry,
):
    """find_pii catches a failed analysis and logs it: no task frame either."""
    _client, events = initialized_sentry
    analyzer = SimpleNamespace(
        BatchAnalyzerEngine=mock.Mock(side_effect=RuntimeError("analyzer down"))
    )
    with (
        mock.patch.object(pii_scrubber, "_ensure_engines", return_value=True),
        mock.patch.dict(sys.modules, {"presidio_analyzer": analyzer}),
    ):
        assert pii_scrubber.find_pii([SPAN_TEXT]) is None

    [event] = events
    assert "tracer.utils.trace_ingestion" not in _frame_modules(event)
    _assert_sent_without_frame_vars(events, "tracer.utils.pii_scrubber")


class _GrpcContext:
    """The servicer context Export receives once django_socio_grpc has authenticated."""

    def __init__(self):
        self.user = SimpleNamespace(id=7, organization=SimpleNamespace(id=8))
        self.workspace = None
        self.aborted = None

    async def abort(self, code, details):
        self.aborted = code


# The ee rate limiter, stubbed so the run needs no ee/.
_RATE_LIMIT_ALLOWS = {
    "ee.usage.services.rate_limiter": SimpleNamespace(
        RateLimiter=SimpleNamespace(
            check=lambda org, kind: SimpleNamespace(allowed=True)
        )
    )
}


def _export_request():
    export = ExportTraceServiceRequest()
    export.resource_spans.add().scope_spans.add().spans.add().name = SPAN_TEXT
    return export


@pytest.mark.parametrize("failure", ["store", "enqueue"])
def test_the_grpc_receiver_that_cannot_queue_a_batch_sends_no_frame_vars(
    refusing_redis, initialized_sentry, failure
):
    """The OTLP receiver logs the failure while its frame holds the request bytes."""
    _client, events = initialized_sentry
    if failure == "enqueue":
        refusing_redis.setex.side_effect = None
    context = _GrpcContext()
    with (
        mock.patch.dict(sys.modules, _RATE_LIMIT_ALLOWS),
        mock.patch.object(
            trace_ingestion.bulk_create_observation_span_task,
            "apply_async",
            side_effect=RuntimeError("Temporal is not reachable"),
        ),
    ):
        asyncio.run(ObservationSpanService().Export(_export_request(), context))

    assert context.aborted == grpc.StatusCode.INTERNAL
    refusing_redis.setex.assert_called_once()
    assert len(events) == 1
    _assert_sent_without_frame_vars(events, "tracer.services.grpc")


class _GrpcServerContext:
    """The grpc.aio servicer context a client's call arrives with."""

    def __init__(self):
        self._code = None

    def invocation_metadata(self):
        return (("x-api-key", "key-not-sent"), ("x-secret-key", "secret-not-sent"))

    def peer(self):
        return "ipv4:10.0.0.1:50000"

    def code(self):
        return self._code

    async def abort(self, code, details):
        self._code = code
        raise grpc.aio.AbortError()


@contextlib.contextmanager
def _as_grpcrunaioserver_serves(close_old_connections=None):
    """django_socio_grpc in the async mode grpcrunaioserver sets. Its middleware
    closes stale database connections around each call. A test skips that, as
    Django's test client skips close_old_connections, so a connection an
    earlier test left open is never touched. Pass a failing mock to make it fail."""
    with (
        mock.patch.object(grpc_settings, "GRPC_ASYNC", True),
        mock.patch(
            "django_socio_grpc.middlewares._close_old_connections",
            close_old_connections or mock.Mock(),
        ),
    ):
        yield


def test_a_failed_grpc_export_through_the_framework_sends_no_raw_request(
    refusing_redis, initialized_sentry
):
    """django_socio_grpc logs a failed call with extra={"request": its container},
    and the container's repr holds the whole protobuf request. Export runs here
    through the framework's own servicer proxy, as grpcrunaioserver serves it."""
    _client, events = initialized_sentry
    context = _GrpcServerContext()
    with (
        _as_grpcrunaioserver_serves(),
        mock.patch.object(
            ObservationSpanService, "authentication_classes", [_ResolvedApiKey]
        ),
        mock.patch.dict(sys.modules, _RATE_LIMIT_ALLOWS),
    ):
        servicer = ObservationSpanService.as_servicer()
        with pytest.raises(grpc.aio.AbortError):
            asyncio.run(servicer.Export(_export_request(), context))

    assert len(events) == 2  # the receiver's logger.exception and the framework's
    [framework] = [e for e in events if e.get("logger") == "django_socio_grpc.request"]
    assert framework["extra"]["status_code"] == context.code().name
    assert "request" not in framework["extra"]
    _assert_sent_without_frame_vars(events, "tracer.services.grpc")
    sent = json.dumps(events, default=str)
    assert "key-not-sent" not in sent and "secret-not-sent" not in sent


class _ApiKeyLookupDown:
    """The API-key lookup with its database unreachable."""

    def authenticate(self, request):
        raise RuntimeError("database unavailable")


def test_a_rejected_grpc_call_leaves_a_breadcrumb_without_the_request(django_sentry):
    """A call that fails before Export logs at WARNING: no event, only a
    breadcrumb. The gRPC server's calls share one scope, so that crumb rides on
    later events, unrelated ones too."""
    _client, events = django_sentry
    with (
        _as_grpcrunaioserver_serves(),
        mock.patch.object(
            ObservationSpanService, "authentication_classes", [_ApiKeyLookupDown]
        ),
    ):
        servicer = ObservationSpanService.as_servicer()
        with pytest.raises(grpc.aio.AbortError):
            asyncio.run(servicer.Export(_export_request(), _GrpcServerContext()))
    assert events == []  # a WARNING: a breadcrumb only

    logging.getLogger("accounts.views").error("unrelated_failure_later")

    [event] = events
    crumbs = event["breadcrumbs"]["values"]
    [crumb] = [c for c in crumbs if c.get("category") == "django_socio_grpc.request"]
    assert crumb["level"] == "warning"
    assert crumb["message"].startswith("Unauthenticated")
    assert crumb["data"]["status_code"] == "UNAUTHENTICATED"
    assert "request" not in crumb["data"]
    assert SPAN_TEXT not in json.dumps(events, default=str)


def test_a_grpc_framework_breadcrumb_it_cannot_read_is_dropped(initialized_sentry):
    """Fail closed: a crumb from that logger whose data is not a mapping cannot
    be cleaned, so it is not kept. Other crumbs are untouched."""
    _client, events = initialized_sentry
    sentry_sdk.add_breadcrumb(
        category="django_socio_grpc.request", message="x", data=f"[{SPAN_TEXT}]"
    )
    sentry_sdk.add_breadcrumb(category="accounts.views", message="kept", data={"a": 1})
    sentry_sdk.capture_message("later")

    [event] = events
    categories = [c.get("category") for c in event["breadcrumbs"]["values"]]
    assert "django_socio_grpc.request" not in categories
    assert "accounts.views" in categories
    assert SPAN_TEXT not in json.dumps(events, default=str)


def test_a_grpc_call_that_fails_in_the_framework_sends_no_raw_request(
    initialized_sentry,
):
    """A failure before Export, inside the framework (its connection-closing
    middleware with the database down), logs at ERROR with the container and
    no ingestion frame on the stack. The framework's logger marks the event."""
    _client, events = initialized_sentry
    context = _GrpcServerContext()
    database_down = mock.Mock(side_effect=RuntimeError("database unavailable"))
    with _as_grpcrunaioserver_serves(close_old_connections=database_down):
        servicer = ObservationSpanService.as_servicer()
        with pytest.raises(grpc.aio.AbortError):
            asyncio.run(servicer.Export(_export_request(), context))

    [event] = events
    assert event["logger"] == "django_socio_grpc.request"
    assert "tracer.services.grpc" not in _frame_modules(event)
    assert event["extra"]["status_code"] == context.code().name
    assert "request" not in event["extra"]
    assert [frame for frame in iter_event_frames(event) if "vars" in frame] == []
    assert SPAN_TEXT not in json.dumps(events, default=str)


def test_a_sibling_grpc_framework_logger_cannot_carry_the_request(
    initialized_sentry,
):
    """The rule matches the framework's logger prefix, not one logger name."""
    _client, events = initialized_sentry
    held = SPAN_TEXT  # a local, as a framework frame holds the request
    logging.getLogger("django_socio_grpc.middlewares").warning(
        "x", extra={"request": f"[{held}]"}
    )
    logging.getLogger("django_socio_grpc.services").error(
        "y", extra={"request": f"[{held}]"}
    )

    [event] = events
    assert event["logger"] == "django_socio_grpc.services"
    assert "request" not in event["extra"]
    [crumb] = [
        c
        for c in event["breadcrumbs"]["values"]
        if c.get("category") == "django_socio_grpc.middlewares"
    ]
    assert "request" not in crumb["data"]
    assert [frame for frame in iter_event_frames(event) if "vars" in frame] == []
    assert SPAN_TEXT not in json.dumps(events, default=str)


@pytest.mark.parametrize("shape", ["mapping", "list"])
def test_the_transaction_hook_drops_a_langfuse_body(initialized_sentry, shape):
    """init_sentry's before_send_transaction shares the body rule: a transaction
    for the ingestion endpoint keeps no body, in either breadcrumb shape."""
    client, _events = initialized_sentry
    url = "http://testserver/api/public/ingestion"
    crumbs = [
        {
            "category": "httplib",
            "data": {"url": url, "method": "POST", "body": SPAN_TEXT},
        },
        {"category": "django_socio_grpc.request", "data": SPAN_TEXT},
        {
            "category": "django_socio_grpc.request",
            "data": {"status_code": "INTERNAL", "request": SPAN_TEXT},
        },
    ]
    trace = {"trace_id": "a" * 32, "span_id": "b" * 16}
    event = {
        "type": "transaction",
        "contexts": {"trace": trace},
        "message": SPAN_TEXT,
        "logentry": {"formatted": SPAN_TEXT},
        "exception": {"values": [{"type": "LookupError", "value": SPAN_TEXT}]},
        "request": {"url": url, "method": "POST", "data": {"batch": [SPAN_TEXT]}},
        "breadcrumbs": {"values": crumbs} if shape == "mapping" else crumbs,
        "_meta": {"breadcrumbs": {SPAN_TEXT: {}}, "": {"err": [SPAN_TEXT]}},
    }

    sent = client.options["before_send_transaction"](event, {})

    assert sent is not None
    assert sent["contexts"]["trace"] == trace
    assert sent["exception"]["values"] == [
        {"type": "LookupError", "value": sentry._REDACTED}
    ]
    assert sent["request"] == {"url": url, "method": "POST"}
    kept = [
        {"category": "httplib", "data": {"method": "POST"}},
        {"category": "django_socio_grpc.request", "data": {"status_code": "INTERNAL"}},
    ]
    assert sent["breadcrumbs"] == ({"values": kept} if shape == "mapping" else kept)
    assert SPAN_TEXT not in json.dumps(sent, default=str)


class _ResolvedApiKey:
    """What the API-key authenticator resolves, without a database."""

    def authenticate(self, request):
        user = SimpleNamespace(
            id=7, is_authenticated=True, organization=SimpleNamespace(id=8)
        )
        return user, None


def _post_through_asgi(path, body):
    """One HTTP request through Django's ASGIHandler, as granian serves it.
    Sentry wraps that handler: its middleware adds the request URL, and the
    Django integration adds the parsed body."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    incoming = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        if incoming:
            return incoming.pop(0)
        # The client stays connected: Django cancels this wait once it responds.
        await asyncio.Future()

    async def send(message):
        sent.append(message)

    # As Django's test client does: the request must not close the run's connections.
    request_started.disconnect(close_old_connections)
    request_finished.disconnect(close_old_connections)
    try:
        asyncio.run(ASGIHandler()(scope, receive, send))
    finally:
        request_started.connect(close_old_connections)
        request_finished.connect(close_old_connections)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_a_failed_langfuse_batch_sends_no_frame_vars_and_no_body(django_sentry):
    """The Langfuse-compatible receiver, through Django's ASGI handler: its
    frames and the parsed body the Django integration attaches hold the trace."""
    _client, events = django_sentry
    batch = [
        {"id": "e1", "type": "trace-create", "body": {"id": "t1", "input": SPAN_TEXT}}
    ]
    with (
        mock.patch.object(
            LangfuseIngestionView, "authentication_classes", [_ResolvedApiKey]
        ),
        mock.patch.object(
            langfuse_ingestion, "_resolve_project", side_effect=RuntimeError("db down")
        ),
    ):
        status = _post_through_asgi(
            "/api/public/ingestion", json.dumps({"batch": batch}).encode()
        )

    assert status == 207
    [event] = events
    assert event["request"]["url"].endswith("/api/public/ingestion")
    assert "data" not in event["request"]
    _assert_sent_without_frame_vars(events, "tracer.views.langfuse_ingestion")


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


def test_ordinary_errors_keep_type_and_location_but_no_locals(initialized_sentry):
    client, events = initialized_sentry
    assert client.options["include_local_variables"] is False
    held = SPAN_TEXT
    try:
        raise RuntimeError("unrelated")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
    structlog.get_logger("accounts.views").error("unrelated_failed")

    issue, logged = events
    assert issue["exception"]["values"][-1]["type"] == "RuntimeError"
    assert logged["logger"] == "accounts.views"
    for event in events:
        assert any(
            frame.get("module") == __name__ and frame.get("lineno", 0) > 0
            for frame in iter_event_frames(event)
        )
        assert all("vars" not in frame for frame in iter_event_frames(event))
    assert held not in json.dumps(events, default=str)


def test_template_error_keeps_location_but_no_render_context(initialized_sentry):
    _client, events = initialized_sentry
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    try:
        env.from_string("Hi {{ user.name }}").render(user={"note": SPAN_TEXT})
    except jinja2.UndefinedError as exc:
        sentry_sdk.capture_exception(exc)

    [event] = events
    assert event["exception"]["values"][-1]["type"] == "UndefinedError"
    assert any(
        frame.get("module") == "<template>" and frame.get("lineno", 0) > 0
        for frame in iter_event_frames(event)
    )
    _assert_sent_without_frame_vars(events, "<template>")


def test_an_error_logged_while_a_template_renders_loses_frame_vars(
    initialized_sentry,
):
    """Even a live template frame without a module name sends no locals."""
    _client, events = initialized_sentry
    env = jinja2.Environment()
    env.globals["report"] = lambda: logging.getLogger("accounts.views").error("x")
    env.from_string("{{ report() }}").render()

    [event] = events
    assert None in _frame_modules(event)
    assert [frame for frame in iter_event_frames(event) if "vars" in frame] == []

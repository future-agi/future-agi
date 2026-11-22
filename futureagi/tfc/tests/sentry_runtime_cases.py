"""Wire contracts run by test_sentry_runtime in a fresh Django process."""

import asyncio
import json
import logging
import os
import sys
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
import requests
import sentry_sdk
import sentry_sdk.client as sdk_client
import structlog
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth import get_user_model
from django.core.handlers.asgi import ASGIHandler
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from django_structlog.signals import bind_extra_request_finished_metadata
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ExecuteActivityInput

from tfc.logging import sentry
from tfc.logging.middleware import CallbackRequestMiddleware
from tfc.temporal.common.sentry_interceptor import SentryActivityInboundInterceptor
from tfc.temporal.drop_in.decorator import _create_real_activity
from tfc.tests.sentry_capture import MemoryWire
from tracer.utils import otel, trace_ingestion


@contextmanager
def capture(monkeypatch, component):
    wire = MemoryWire()
    real_init = sentry_sdk.init
    global_scope = sentry_sdk.get_global_scope()
    previous = global_scope.client
    monkeypatch.setattr(
        sentry_sdk, "init", lambda **opts: real_init(**opts, transport=wire)
    )
    monkeypatch.setattr(
        sentry,
        "SENTRY_DSN",
        "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@fixture.invalid/1",
    )
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "TRACES_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(sentry, "PROFILES_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(sentry, "_initialized_components", set())
    with sentry_sdk.isolation_scope() as scope, sentry_sdk.new_scope():
        scope.clear_breadcrumbs()
        try:
            assert sentry.init_sentry(component=component)
            assert sentry_sdk.get_client().transport is wire
            yield wire
        finally:
            try:
                if global_scope.client is not previous:
                    global_scope.client.flush()
                    global_scope.client.close()
            finally:
                global_scope.set_client(previous)


def ordinary_diagnostic():
    try:
        raise RuntimeError("Fixture worker unavailable")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)


class InlineAsyncDispatch:
    async def execute_activity(self, input):
        return await input.fn(*input.args)


def test_registered_activity(monkeypatch):
    project = "-".join(("example", "fixture", "project"))
    attrs = [
        {"key": k, "value": {"string_value": v}}
        for k, v in (("project_name", project), ("project_type", "observe"))
    ]
    resource = {
        "resource": {"attributes": attrs},
        "scope_spans": [{"spans": [{"name": "fixture span"}]}],
    }
    storage = {"fixture-input": json.dumps({"resource_spans": [resource]}).encode()}
    # Only replace backing stores, not parsing, conversion, logging, or heartbeats.
    monkeypatch.setattr(
        trace_ingestion,
        "payload_storage",
        SimpleNamespace(retrieve=storage.__getitem__),
    )
    monkeypatch.setattr(trace_ingestion.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        trace_ingestion, "get_pii_settings_for_projects", lambda *args: {}
    )
    monkeypatch.setattr(otel, "_bulk_get_or_create_projects", lambda keys: {})
    task = trace_ingestion.bulk_create_observation_span_task
    registered = _create_real_activity(task._activity_name, task._sync_wrapper)
    env = ActivityEnvironment()
    env.info = replace(
        env.info,
        activity_type=task._activity_name,
        task_queue="trace_ingestion",
        heartbeat_timeout=timedelta(seconds=120),
    )
    beats = []
    env.on_heartbeat = lambda *details: beats.append(details)
    call = ExecuteActivityInput(
        fn=registered,
        args=({"args": ["fixture-input", "org", "user", "ws", "json"]},),
        executor=None,
        headers={},
    )
    with capture(monkeypatch, "temporal") as wire:
        ordinary_diagnostic()
        with pytest.raises(ValueError, match=project):
            interceptor = SentryActivityInboundInterceptor(InlineAsyncDispatch())
            asyncio.run(env.run(interceptor.execute_activity, call))

    assert (task._activity_name, "processing") in beats
    events = [body for _, kind, body in wire.items if kind == "event"]
    [wrapper] = [e for e in events if e.get("logger") == "temporalio.activity"]
    assert task._activity_name in wrapper["logentry"]["message"]
    assert "FAILED (ValueError)" in wrapper["logentry"]["message"]
    [task_log] = [
        e for e in events if e.get("logger") == "tracer.utils.trace_ingestion"
    ]
    assert any(
        error["type"] == "ValueError"
        and any(
            f.get("module") == "tracer.utils.trace_ingestion"
            and f.get("function") == "bulk_create_observation_span_task"
            and f.get("lineno", 0) > 0
            for f in error["stacktrace"]["frames"]
        )
        for error in task_log["exception"]["values"]
    )
    assert any(
        error["type"] == "RuntimeError"
        and error["value"] == sentry._REDACTED
        and any(
            f.get("function") == "ordinary_diagnostic" and f.get("lineno", 0) > 0
            for f in error["stacktrace"]["frames"]
        )
        for e in events
        for error in e.get("exception", {}).get("values", [])
    )
    assert all(project.encode() not in packet for packet in wire.raw)


def temporarily_unavailable(request, reference):
    return HttpResponse(status=503)


def account_error(request, account, scoped):
    request.user = get_user_model()(pk=account, email=f"{account.hex}@fixture.invalid")
    assert request.user.is_authenticated
    assert request.user.get_username() == request.user.email
    if scoped:
        sentry_sdk.set_user({"id": str(account), "email": request.user.email})
    raise RuntimeError("fixture view failed")


def many_spans(request):
    for index in range(20):
        with sentry_sdk.start_span(op="qa.lookup"):
            if index == 0:
                sentry_sdk.capture_message("lookup diagnostic")
    return HttpResponse(status=200)


urlpatterns = [
    path("qa/status/<str:reference>/", temporarily_unavailable),
    path("qa/account/<uuid:account>/<int:scoped>/", account_error),
    path("qa/spans/", many_spans),
]


async def get_status(url):
    app = ApplicationCommunicator(
        ASGIHandler(),
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": url,
            "raw_path": url.encode(),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        },
    )
    await app.send_input({"type": "http.request", "body": b"", "more_body": False})
    start = await app.receive_output(timeout=30)
    assert start["type"] == "http.response.start"
    while (await app.receive_output(timeout=30)).get("more_body"):
        pass
    await app.wait(timeout=30)
    return start["status"]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
def test_middleware_503(monkeypatch):
    marker = "-".join(("fixture", "reference"))
    finished = []

    def observe(sender, response, log_kwargs, **kwargs):
        finished.append((sender, response.status_code, log_kwargs.get("code")))

    # Observe the real sender, without constructing/replacing any log record.
    bind_extra_request_finished_metadata.connect(observe, weak=False)
    try:
        with capture(monkeypatch, "django") as wire:
            assert asyncio.run(get_status(f"/qa/status/{marker}/")) == 503
    finally:
        bind_extra_request_finished_metadata.disconnect(observe)
    assert finished == [(CallbackRequestMiddleware, 503, 503)]
    [logged] = [
        b
        for _, kind, b in wire.items
        if kind == "event" and b.get("logger") == "django_structlog.middlewares.request"
    ]
    [txn] = [b for _, kind, b in wire.items if kind == "transaction"]
    assert (
        logged["extra"]["status_code"]
        == txn["contexts"]["response"]["status_code"]
        == 503
    )
    assert "request_finished" in logged["logentry"]["message"]
    for item in (logged, txn):
        assert item["request"]["method"] == "GET"
        assert (
            item["request"]["url"] == item["transaction"] == "/qa/status/{reference}/"
        )
    assert all(marker.encode() not in packet for packet in wire.raw)


@pytest.mark.parametrize("extended", [False, True], ids=["three-lines", "other-fields"])
def test_supplied_frame(monkeypatch, extended):
    event_id = uuid4().hex
    location = {
        "filename": "qa_frame.py",
        "function": "qa_step",
        "module": "tfc.qa_frame",
        "lineno": 17,
    }
    lines = ["first line", "second line", "third line"]
    frame = {**location, "pre_context": lines}
    if extended:
        long_line = "x" * (sentry._FRAME_TEXT_LIMIT + 1)
        frame.update(
            post_context=lines,
            context_line=long_line,
            abs_path=long_line,
            colno={"unexpected": 2},
            extension={"extra": lines},
        )
    event = {
        "event_id": event_id,
        "exception": {
            "values": [
                {
                    "type": "LookupError",
                    "value": "ordinary fixture",
                    "stacktrace": {"frames": [frame]},
                }
            ]
        },
    }
    seen = []

    def snapshot(source):
        [error] = source["exception"]["values"]
        [current] = error["stacktrace"]["frames"]
        # Snapshot now: handle_in_app may subsequently modify this very frame.
        return error["type"], json.loads(json.dumps(current))

    def observe(target, attr):
        original = getattr(target, attr)

        def wrapped(source, *args, **kwargs):
            if isinstance(source, dict) and source.get("event_id") == event_id:
                seen.append((attr, snapshot(source)))
            return original(source, *args, **kwargs)

        monkeypatch.setattr(target, attr, wrapped)

    with capture(monkeypatch, "temporal") as wire:
        client = sentry_sdk.get_client()
        assert client.integrations.get("tfc_payload_bounds")
        observe(sdk_client, "handle_in_app")
        observe(client.options["event_scrubber"], "scrub_event")
        observe(sdk_client, "serialize")
        with sentry_sdk.start_transaction(
            name="ordinary fixture", op="qa.step"
        ) as root:
            root.set_context("response", {"status_code": 503})
            with monkeypatch.context() as budget:
                budget.setattr(sentry, "_MAX_ITEMS", 2)
                assert sentry_sdk.capture_event(event) == event_id

    [(error_header, error)] = [
        (h, b) for h, k, b in wire.items if k == "event" and b["event_id"] == event_id
    ]
    [(txn_header, txn)] = [(h, b) for h, k, b in wire.items if k == "transaction"]
    assert int(root.trace_id, 16) and len(root.trace_id) == 32
    assert int(root.span_id, 16) and len(root.span_id) == 16
    for header, body in ((error_header, error), (txn_header, txn)):
        trace = body["contexts"]["trace"]
        assert header["event_id"] == body["event_id"]
        assert header["trace"]["trace_id"] == trace["trace_id"] == root.trace_id
        assert trace["span_id"] == root.span_id
    assert txn["contexts"]["response"]["status_code"] == 503
    assert [label for label, _ in seen] == ["handle_in_app", "scrub_event", "serialize"]
    final = snapshot(error)
    assert final[1]["in_app"] is True
    expected = {**location, "pre_context": lines[-2:]}
    if extended:
        expected["post_context"] = lines[:2]
    for typ, current in [s for _, s in seen] + [final]:
        assert typ == "LookupError"
        assert {k: v for k, v in current.items() if k != "in_app"} == expected


@pytest.mark.parametrize("scoped", [False, True], ids=["django-user", "explicit-user"])
@override_settings(
    ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"], MIDDLEWARE=[], DEBUG=False
)
def test_authenticated_request(monkeypatch, scoped):
    account = uuid4()
    with capture(monkeypatch, "django") as wire:
        assert sentry_sdk.get_client().options["send_default_pii"] is False
        assert asyncio.run(get_status(f"/qa/account/{account}/{int(scoped)}/")) == 500
    [issue] = [b for _, kind, b in wire.items if kind == "event" and "exception" in b]
    assert issue["exception"]["values"][-1]["type"] == "RuntimeError"
    assert issue["request"]["url"] == "/qa/account/{account}/{scoped}/"
    assert any(k == "transaction" for _, k, _ in wire.items)
    for _, kind, body in wire.items:
        if kind in ("event", "transaction"):
            assert "user" not in body
    for marker in (str(account), account.hex):
        assert all(marker.encode() not in packet for packet in wire.raw)


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"], MIDDLEWARE=[])
def test_request_span_overflow(monkeypatch):
    with capture(monkeypatch, "django") as wire:
        monkeypatch.setattr(sentry, "_MAX_ITEMS", 12)
        assert asyncio.run(get_status("/qa/spans/")) == 200
    [txn] = [b for _, k, b in wire.items if k == "transaction"]
    [issue] = [b for _, k, b in wire.items if k == "event"]
    assert txn["transaction"] == "/qa/spans/"
    assert len(txn["spans"]) == 12
    trace = issue["contexts"]["trace"]
    assert trace["trace_id"] == txn["contexts"]["trace"]["trace_id"]
    kept = {s["span_id"]: s for s in txn["spans"]}
    assert kept[trace["span_id"]]["op"] == "qa.lookup"


def stub_provider_http(monkeypatch, replies):
    calls = []

    def send(adapter, request, **kwargs):
        key = (request.method, request.url)
        calls.append(key)
        status, text = replies[key]
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response._content = text.encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return calls


@contextmanager
def provider_capture(monkeypatch, pipeline, component):
    component = component if pipeline == "django" else "temporal"
    with capture(monkeypatch, component) as wire:
        if pipeline != "django":
            from tfc.logging.temporal import configure_temporal_logging

            # Same order as run_worker: SDK first, then the worker's processors.
            monkeypatch.setenv("ENV_TYPE", pipeline)
            configure_temporal_logging()
        yield wire


def assert_local_error(pipeline, logger_name, marker, **expected):
    # The real file handler in sentry_runtime_settings writes JSON. A worker
    # renders its message *before* that handler; Django hands it an event dict.
    with (Path(os.environ["TMPDIR"]) / "logs" / "wsgi.log").open() as stream:
        rows = (json.loads(line) for line in stream if marker in line)
        [row] = [
            r for r in rows if r["logger"] == logger_name and r["level"] == "error"
        ]
    if pipeline == "local":
        rendered = row["event"]
        assert "\x1b[" in rendered
        for key, value in expected.items():
            assert key == "event" or key in rendered
            assert str(value) in rendered
    else:
        fields = json.loads(row["event"]) if pipeline == "production" else row
        assert {k: fields[k] for k in expected} == expected


@pytest.mark.parametrize("pipeline", ["django", "local", "production"])
def test_structured_provider_log(monkeypatch, pipeline):
    marker, unknown_name = uuid4().hex, uuid4().hex
    base = "https://vapi.fixture.invalid"
    monkeypatch.setenv("VAPI_API_BASE_URL", base)
    request = ("GET", f"{base}/session/example")
    calls = stub_provider_http(monkeypatch, {request: (400, marker)})
    with provider_capture(monkeypatch, pipeline, "django") as wire:
        from ee.voice.services.vapi_service import VapiService

        assert VapiService(api_key="fixture").get_chat_session("example") is None
        structlog.get_logger(VapiService.__module__).error(
            unknown_name, status_code=unknown_name, count=2
        )
        sentry_sdk.capture_message("next diagnostic")
    assert calls == [request]  # A 429 would have gone through the retry loop.
    assert_local_error(
        pipeline,
        VapiService.__module__,
        marker,
        event="vapi_api_error",
        action="get chat session",
        status_code=400,
        response_body=marker,
    )
    events = [b for _, k, b in wire.items if k == "event"]
    known, unknown = [b for b in events if b.get("logger") == VapiService.__module__]
    assert known["logentry"] == {"message": "vapi_api_error"}
    assert known["extra"] == {"status_code": 400}
    assert unknown["logentry"] == {"message": "Structured log"}
    assert unknown["extra"] == {"count": 2}
    assert any(b.get("message") == "next diagnostic" for b in events)
    for value in (marker, unknown_name):
        assert all(value.encode() not in packet for packet in wire.raw)


@pytest.mark.parametrize("pipeline", ["django", "local", "production"])
def test_embedding_rejection(monkeypatch, pipeline):
    marker = uuid4().hex
    base = "https://embedding.fixture.invalid"
    health, embed = ("GET", f"{base}/health"), ("POST", f"{base}/model/v1/embed")
    calls = stub_provider_http(monkeypatch, {health: (200, ""), embed: (400, marker)})
    with provider_capture(monkeypatch, pipeline, "temporal") as wire:
        from agentic_eval.core.embeddings.serving_client import ModelServingClient

        with pytest.raises(ValueError, match=marker) as raised:
            ModelServingClient(base_url=base).embed_text("fixture input")
        error_id = sentry_sdk.capture_exception(raised.value)
    assert calls == [health, embed]
    assert_local_error(
        pipeline,
        ModelServingClient.__module__,
        marker,
        event=f"Text embedding failed: Bad request: {marker}",
    )
    events = [b for _, k, b in wire.items if k == "event"]
    [logged] = [b for b in events if b.get("logger") == ModelServingClient.__module__]
    assert logged["logentry"] == {"message": "Structured log"}
    [issue] = [b for b in events if b["event_id"] == error_id]
    [error] = issue["exception"]["values"]
    assert error["type"] == "ValueError"
    assert error["value"] == sentry._REDACTED
    assert error["stacktrace"]["frames"][-1]["function"] == "_make_request"
    assert all(marker.encode() not in packet for packet in wire.raw)


def run_intercepted(fn, args, workflow_id):
    env = ActivityEnvironment()
    env.info = replace(env.info, activity_type=fn.__name__, workflow_id=workflow_id)
    interceptor = SentryActivityInboundInterceptor(InlineAsyncDispatch())
    call = ExecuteActivityInput(fn=fn, args=args, headers={}, executor=None)
    return asyncio.run(env.run(interceptor.execute_activity, call))


def test_activity_scope_tags(monkeypatch):
    marker = uuid4().hex
    workflow_id = "task-eval-cluster-" + marker

    async def cluster():
        raise RuntimeError("fixture read failed")

    with capture(monkeypatch, "temporal") as wire:
        with sentry_sdk.start_transaction(name="cluster", op="task") as txn:
            txn.set_tag("temporal.workflow_id", workflow_id)
            with pytest.raises(RuntimeError):
                run_intercepted(cluster, (), workflow_id)
    [issue] = [b for _, k, b in wire.items if k == "event"]
    [transaction] = [b for _, k, b in wire.items if k == "transaction"]
    assert issue["fingerprint"] == ["temporal", "activity", "cluster", "RuntimeError"]
    for item in (issue, transaction):
        assert item["tags"]["component"] == "temporal"
        assert set(item["tags"]) <= {"component", "region", "cloud_deployment"}
    assert all(marker.encode() not in packet for packet in wire.raw)


def test_missing_stage_retryable(monkeypatch):
    from tfc.temporal.insights import activities
    from tfc.temporal.insights.types import InsightRunInput

    # Model an installed, older package (successful import, no retry_failed).
    parent = None
    for path_name in (
        "ee",
        "ee.agenthub",
        "ee.agenthub.insights",
        "ee.agenthub.insights.pipeline",
    ):
        module = ModuleType(path_name)
        monkeypatch.setitem(sys.modules, path_name, module)
        if parent is not None:
            setattr(parent, path_name.rsplit(".", 1)[1], module)
        parent = module

    with capture(monkeypatch, "temporal") as wire:
        with pytest.raises(ApplicationError) as raised:
            run_intercepted(
                activities.insight_retry_failed,
                (InsightRunInput("fixture-run"),),
                "fixture-workflow",
            )
    assert not raised.value.non_retryable
    assert isinstance(raised.value.__cause__, AttributeError)
    [issue] = [b for _, k, b in wire.items if k == "event"]
    cause, outer = issue["exception"]["values"]
    assert cause["type"] == "AttributeError" and cause["value"] == sentry._REDACTED
    assert cause["stacktrace"]["frames"][-1]["function"] == "_pipeline"
    assert outer["type"] == "ApplicationError"
    assert activities.PIPELINE_IMPORT_FAILED in outer["value"]


@pytest.mark.parametrize("enabled", [False, True], ids=["sentry-off", "sentry-on"])
def test_worker_local_error(monkeypatch, caplog, enabled):
    reasons = [f"{reason} {uuid4().hex}" for reason in ("queue down", "shutting down")]

    def fail(reason):
        raise RuntimeError(reason)

    registered = _create_real_activity("qa_worker", fail)
    logger = logging.getLogger("temporalio.activity")
    # The production Temporal logger does not propagate to the root handler.
    logger.addHandler(caplog.handler)
    try:
        if not enabled:
            assert not sentry.SENTRY_ENABLED and not sentry_sdk.is_initialized()
        recording = capture(monkeypatch, "temporal") if enabled else nullcontext()
        with recording as wire:
            for reason in reasons:
                with pytest.raises(RuntimeError, match=reason):
                    env = ActivityEnvironment()
                    asyncio.run(env.run(registered, {"args": [reason]}))
        ordinary = [
            r.getMessage()
            for r in caplog.records
            if r.name == logger.name and r.levelno == logging.ERROR
        ]
        for message, reason in zip(ordinary, reasons, strict=True):
            assert f"Activity 'qa_worker' FAILED: {reason}" in message
        if enabled:
            sent = [
                b["logentry"]
                for _, k, b in wire.items
                if k == "event" and b.get("logger") == logger.name
            ]
            summary = {"message": "Activity 'qa_worker' FAILED (RuntimeError)"}
            assert sent == [summary, summary]
            assert all(reason.encode() not in p for reason in reasons for p in wire.raw)
    finally:
        logger.removeHandler(caplog.handler)

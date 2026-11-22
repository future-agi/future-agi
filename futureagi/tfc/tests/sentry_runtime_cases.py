"""Wire contracts run by test_sentry_runtime in a fresh Django process."""

import asyncio
import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sentry_sdk
import sentry_sdk.client as sdk_client
from asgiref.testing import ApplicationCommunicator
from django.core.handlers.asgi import ASGIHandler
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from django_structlog.signals import bind_extra_request_finished_metadata
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
        and error["value"] == "Fixture worker unavailable"
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


urlpatterns = [path("qa/status/<str:reference>/", temporarily_unavailable)]


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

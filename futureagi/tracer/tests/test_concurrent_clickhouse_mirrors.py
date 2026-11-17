"""Concurrent ClickHouse mirrors keep every row; a refused batch is sent again.

The span and trace mirrors share one cached clickhouse-connect client per
process (``trace_writer._get_client``). Temporal runs sync activities on a
thread pool, so two imports, or two OTLP batches, insert on that client at
once. The stand-in replaces ``clickhouse_connect.get_client`` and keeps the
driver's session rule: a client carries one session id unless it is built with
``autogenerate_session_id=False``, and a second query on that session while the
first is open raises ``ProgrammingError`` before anything is sent.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import clickhouse_connect
import pytest
from clickhouse_connect.driver.exceptions import ProgrammingError
from django.db import connections
from structlog.testing import capture_logs

from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import span_writer, trace_writer
from tracer.tests.test_langfuse_span_writer import _row

# The driver's refusal, word for word (clickhouse-connect 1.1.1).
REFUSAL = (
    "Attempt to execute concurrent queries within the same session. "
    "Please use a separate client instance per thread/process."
)
CONFIG = {
    "host": "clickhouse.invalid",
    "http_port": 8123,
    "user": "default",
    "password": "",
    "database": "test_mirrors",
}
SPAN_INPUT = "customer question kept out of logs"
WAIT_S = 10


class Server:
    """What the stand-in ClickHouse received, in order of arrival."""

    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = 0
        self.rows = []
        self.overlap = False
        self.first_open = threading.Event()
        self.second_tried = threading.Event()

    def attempt(self):
        with self.lock:
            self.attempts += 1
            number = self.attempts
        if number == 2:
            self.second_tried.set()
        return number

    def receive(self, number, client, table, rows, column_names):
        if self.overlap and number == 1:
            # Hold the first insert open until the second one has started.
            self.first_open.set()
            assert self.second_tried.wait(WAIT_S)
        with self.lock:
            self.rows.extend(
                (table, client, dict(zip(column_names, row, strict=True)))
                for row in rows
            )

    def ids(self, table):
        return sorted(row["id"] for name, _, row in self.rows if name == table)


class SessionClient:
    """clickhouse-connect's HTTP client, reduced to its session rule."""

    def __init__(self, server, session):
        self.server = server
        self.session = session
        self.open_session = None
        self.closed = False

    def insert(self, table, rows, column_names):
        number = self.server.attempt()
        if self.session is not None:
            if self.open_session == self.session:
                raise ProgrammingError(REFUSAL)
            self.open_session = self.session
        try:
            self.server.receive(number, self, table, rows, column_names)
        finally:
            if self.session is not None:
                self.open_session = None

    def close(self):
        self.closed = True


@pytest.fixture
def clickhouse(monkeypatch):
    """Mirrors on; every client the writers build comes from the stand-in."""
    server = Server()
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.delenv("CH25_SPAN_DUAL_WRITE", raising=False)
    monkeypatch.delenv("CH25_TRACE_DUAL_WRITE", raising=False)
    monkeypatch.setattr(trace_writer, "_client", None)
    monkeypatch.setattr(trace_writer, "get_v2_config", lambda: CONFIG)

    def get_client(**kwargs):
        # The driver's default: one session id per client unless switched off.
        session = kwargs.get("session_id")
        if session is None and kwargs.get("autogenerate_session_id") is not False:
            session = uuid.uuid4()
        return SessionClient(server, session)

    def busy_client():
        """A client whose session already has a query open, so it refuses."""
        client = SessionClient(server, uuid.uuid4())
        client.open_session = client.session
        return client

    monkeypatch.setattr(clickhouse_connect, "get_client", get_client)
    return SimpleNamespace(server=server, busy_client=busy_client)


def _span(span_id):
    return _row(id=span_id, trace_id=f"trace-{span_id}", input=SPAN_INPUT)


def _traces(project, count):
    return [
        str(Trace.objects.create(project=project, name=f"mirrored {n}").id)
        for n in range(count)
    ]


def _overlap(server, first, second):
    """Run both mirrors on threads; the second starts while the first inserts."""

    def run(mirror):
        try:
            mirror()
        finally:
            connections.close_all()

    server.overlap = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(run, first)
        assert server.first_open.wait(WAIT_S)
        two = pool.submit(run, second)
        one.result(timeout=2 * WAIT_S)
        two.result(timeout=2 * WAIT_S)


def test_two_concurrent_span_mirrors_lose_no_span(clickhouse):
    first = [_span("first-1"), _span("first-2")]
    second = [_span("second-1"), _span("second-2")]
    _overlap(
        clickhouse.server,
        lambda: span_writer.mirror_spans_to_clickhouse(first),
        lambda: span_writer.mirror_spans_to_clickhouse(second),
    )
    assert clickhouse.server.ids("spans") == [
        "first-1",
        "first-2",
        "second-1",
        "second-2",
    ]


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_trace_mirrors_lose_no_trace(clickhouse, observe_project):
    first = _traces(observe_project, 2)
    second = _traces(observe_project, 2)
    _overlap(
        clickhouse.server,
        lambda: trace_writer.mirror_traces_to_clickhouse(first),
        lambda: trace_writer.mirror_traces_to_clickhouse(second),
    )
    assert clickhouse.server.ids("traces") == sorted(first + second)


def test_one_span_mirror_writes_each_row_once(clickhouse):
    span_writer.mirror_spans_to_clickhouse([_span("only-1"), _span("only-2")])
    assert clickhouse.server.attempts == 1
    assert clickhouse.server.ids("spans") == ["only-1", "only-2"]


@pytest.mark.django_db
def test_one_trace_mirror_writes_each_row_once(clickhouse, observe_project):
    ids = _traces(observe_project, 2)
    trace_writer.mirror_traces_to_clickhouse(ids)
    assert clickhouse.server.attempts == 1
    assert clickhouse.server.ids("traces") == sorted(ids)


def test_a_refused_span_batch_is_sent_again_on_a_fresh_client(clickhouse, monkeypatch):
    server = clickhouse.server
    refusing = clickhouse.busy_client()
    monkeypatch.setattr(trace_writer, "_client", refusing)
    span_writer.mirror_spans_to_clickhouse([_span("again-1")])
    received = [(client is refusing, row["id"]) for _, client, row in server.rows]
    assert server.attempts == 2
    assert received == [(False, "again-1")]
    assert refusing.closed


@pytest.mark.django_db
def test_a_refused_trace_batch_is_sent_again_on_a_fresh_client(
    clickhouse, observe_project, monkeypatch
):
    server = clickhouse.server
    ids = _traces(observe_project, 1)
    refusing = clickhouse.busy_client()
    monkeypatch.setattr(trace_writer, "_client", refusing)
    trace_writer.mirror_traces_to_clickhouse(ids)
    received = [(client is refusing, row["id"]) for _, client, row in server.rows]
    assert server.attempts == 2
    assert received == [(False, ids[0])]
    assert refusing.closed


def test_a_span_batch_refused_twice_is_logged_with_its_row_count(
    clickhouse, monkeypatch
):
    monkeypatch.setattr(trace_writer, "_get_client", clickhouse.busy_client)
    with capture_logs() as logs:
        span_writer.mirror_spans_to_clickhouse([_span("lost-1"), _span("lost-2")])
    failed = [line for line in logs if line["event"] == "span_dual_write_failed"]
    assert clickhouse.server.attempts == 2
    assert clickhouse.server.rows == []
    assert [line["n_rows"] for line in failed] == [2]
    assert SPAN_INPUT not in repr(logs)


@pytest.mark.django_db
def test_a_trace_batch_refused_twice_is_logged_with_its_count(
    clickhouse, observe_project, monkeypatch
):
    ids = _traces(observe_project, 1)
    monkeypatch.setattr(trace_writer, "_get_client", clickhouse.busy_client)
    with capture_logs() as logs:
        trace_writer.mirror_traces_to_clickhouse(ids)
    failed = [line for line in logs if line["event"] == "trace_dual_write_failed"]
    assert clickhouse.server.attempts == 2
    assert clickhouse.server.rows == []
    assert [line["n"] for line in failed] == [1]

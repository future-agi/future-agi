"""A trace sent through the REST span route is listed in the Observe trace grid.

The Observe trace grid loads ``/tracer/trace/list_traces_of_session/``, which
reads the ClickHouse ``spans`` table. SDK clients that post OTel spans to
``/tracer/observation-span/create_otel_span/`` write Postgres; unless the route
also copies every span into ClickHouse, the trace never shows up in the grid.

The route writes each span on a worker thread with its own database
connection. Those threads cannot see rows inside the per-test transaction, so
these tests post into a committed project of their own and read back only
their own trace ids. Rows the worker threads commit outlive the test: the
module removes its ClickHouse rows and soft-deletes the project when it ends.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from structlog.testing import capture_logs

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_trace_session_id,
)
from tracer.services.clickhouse.v2.query_service import (
    V2AnalyticsQueryService,
    reset_v2_query_client,
)
from tracer.utils.create_otel_span import create_single_otel_span

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CREATE_URL = "/tracer/observation-span/create_otel_span/"
LIST_URL = "/tracer/trace/list_traces_of_session/"

ROOT_NAME = "rest mirror root"
ROOT_INPUT = "what is the refund window"
ROOT_OUTPUT = "thirty days from delivery"
ROOT_LATENCY_MS = 1500

CHILD_NAME = "rest mirror llm call"
CHILD_INPUT = "child prompt kept out of logs"
CHILD_OUTPUT = "child answer"
CHILD_LATENCY_MS = 900
CHILD_MODEL = "rest-mirror-model"
CHILD_PROMPT_TOKENS = 7
CHILD_COMPLETION_TOKENS = 5
CHILD_TOTAL_TOKENS = 12
CHILD_COST = 0.25

# The grid cells of a listed trace: the root's own cells, then the usage the
# root does not send. Tokens, cost and model reach the row only when the child
# span reached ClickHouse under the same trace as the root.
GRID_CELLS = (
    "trace_name",
    "node_type",
    "status",
    "latency",
    "input",
    "output",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
)
EXPECTED_CELLS = {
    "trace_name": ROOT_NAME,
    "node_type": "chain",
    "status": "OK",
    "latency": ROOT_LATENCY_MS,
    "input": ROOT_INPUT,
    "output": ROOT_OUTPUT,
    "model": CHILD_MODEL,
    "prompt_tokens": CHILD_PROMPT_TOKENS,
    "completion_tokens": CHILD_COMPLETION_TOKENS,
    "total_tokens": CHILD_TOTAL_TOKENS,
    "cost": CHILD_COST,
}


@pytest.fixture(scope="module")
def rest_project(integration_setup, django_db_blocker, ch_schema):
    """An Observe project committed outside the per-test transaction.

    The session organization, workspace and user from ``integration_setup``
    are committed too, so the route's worker threads resolve all of them. A
    project of its own keeps these committed traces out of the shared corpus.
    """
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    with django_db_blocker.unblock():
        project = Project.objects.create(
            name=f"REST span mirror {uuid.uuid4().hex[:8]}",
            organization=integration_setup.organization,
            workspace=integration_setup.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
            session_config=[
                {"id": "session_input", "name": "Session Input", "is_visible": True},
            ],
        )
    yield project

    db = settings.CLICKHOUSE["CH_DATABASE"]
    for table in ("spans", "traces"):
        try:
            ch_schema.command(
                f"ALTER TABLE {db}.{table} DELETE "
                f"WHERE project_id = '{project.id}' SETTINGS mutations_sync = 2"
            )
        except Exception:
            pass
    with django_db_blocker.unblock():
        try:
            project.delete()
        except Exception:
            pass


@pytest.fixture
def mirrors_on(monkeypatch, ch_routes_on):
    """The app's post-commit ClickHouse mirrors on, as once the CDC chain is gone.

    The mirrors write through ``trace_writer``'s shared client and the list
    route reads through the v2 query service's client. Both are built from
    ``get_v2_config()``; both caches are dropped under this test's settings and
    the two targets are checked to name the same host and database.
    """
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.delenv("CH25_SPAN_DUAL_WRITE", raising=False)
    monkeypatch.delenv("CH25_TRACE_DUAL_WRITE", raising=False)
    trace_writer._reset_client()
    reset_v2_query_client()
    writer = trace_writer.get_v2_config()
    reader = V2AnalyticsQueryService().ch_client
    assert (reader.host, reader.database) == (writer["host"], writer["database"])
    yield
    trace_writer._reset_client()
    reset_v2_query_client()


def _span(
    project,
    trace_id,
    span_id,
    *,
    name,
    kind,
    latency_ms,
    attributes,
    parent_id=None,
    start_offset_ms=0,
):
    """One span in the OTel list body an SDK client posts (times in ns)."""
    start_ns = (
        int(timezone.now().timestamp() * 1_000_000_000)
        - 2_000_000_000
        + start_offset_ms * 1_000_000
    )
    return {
        "project_name": project.name,
        "project_type": "observe",
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": name,
        "start_time": start_ns,
        "end_time": start_ns + latency_ms * 1_000_000,
        "latency": latency_ms,
        "status": "OK",
        "attributes": {"fi.span.kind": kind, **attributes},
        "events": [],
    }


def _root(project, trace_id, span_id):
    return _span(
        project,
        trace_id,
        span_id,
        name=ROOT_NAME,
        kind="CHAIN",
        latency_ms=ROOT_LATENCY_MS,
        attributes={"input.value": ROOT_INPUT, "output.value": ROOT_OUTPUT},
    )


def _child(project, trace_id, span_id, root_id):
    return _span(
        project,
        trace_id,
        span_id,
        name=CHILD_NAME,
        kind="LLM",
        latency_ms=CHILD_LATENCY_MS,
        parent_id=root_id,
        start_offset_ms=100,
        attributes={
            "llm.model_name": CHILD_MODEL,
            "llm.token_count.prompt": CHILD_PROMPT_TOKENS,
            "llm.token_count.completion": CHILD_COMPLETION_TOKENS,
            "llm.token_count.total": CHILD_TOTAL_TOKENS,
            "gen_ai.cost.total": CHILD_COST,
            "input.value": CHILD_INPUT,
            "output.value": CHILD_OUTPUT,
        },
    )


def _with_session(span, session_name):
    """The span as an SDK client sends it when the span names a session."""
    return {**span, "attributes": {**span["attributes"], "session.id": session_name}}


def _ids():
    return (
        str(uuid.uuid4()),
        f"root-{uuid.uuid4().hex[:16]}",
        f"child-{uuid.uuid4().hex[:16]}",
    )


def _canonical(trace_id):
    """The dashed lowercase text of a trace id, or None if it is not a UUID."""
    try:
        return str(uuid.UUID(str(trace_id)))
    except ValueError:
        return None


def _send(auth_client, capture_on_commit, spans):
    """POST the span list the way an SDK client does.

    The route's worker threads write on their own autocommit connections, so
    the callbacks they register run as soon as each write commits; the capture
    runs any callback registered on the request thread itself.
    """
    with capture_on_commit(execute=True):
        return auth_client.post(CREATE_URL, spans, format="json")


def _list_page(auth_client, project):
    """The first page the Observe trace grid loads for this project."""
    now = timezone.now()
    window = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "col_type": "SYSTEM_METRIC",
            "filter_value": [
                (now - timedelta(days=1)).isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ],
        },
    }
    return auth_client.get(
        LIST_URL,
        {
            "project_id": str(project.id),
            "page_number": 0,
            "page_size": 100,
            "filters": json.dumps([window]),
        },
    )


def test_rest_trace_is_listed_in_observe_with_its_spans(
    auth_client, rest_project, mirrors_on, django_capture_on_commit_callbacks
):
    """The grid lists the trace with the root's cells and the child's usage.

    The list response carries no span count. The tokens, cost and model cells
    are the measure of "a trace with its spans": the root sends none of them,
    so they reach the row only when the child span reached ClickHouse too.
    """
    trace_id, root_id, child_id = _ids()
    sent = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [
            _root(rest_project, trace_id, root_id),
            _child(rest_project, trace_id, child_id, root_id),
        ],
    )
    assert sent.status_code == 201, sent.content
    assert sorted(sent.data["result"]["ids"]) == sorted([root_id, child_id])

    page = _list_page(auth_client, rest_project)
    assert page.status_code == 200, page.content
    rows = [row for row in page.data["result"]["table"] if row["trace_id"] == trace_id]
    assert len(rows) == 1, (
        f"trace {trace_id} sent through the REST span route is not listed "
        "in the Observe trace grid"
    )
    assert {key: rows[0][key] for key in GRID_CELLS} == EXPECTED_CELLS


def test_uppercase_undashed_trace_id_is_listed_once_with_both_spans(
    auth_client, rest_project, mirrors_on, django_capture_on_commit_callbacks
):
    """The form of the sender's trace id never splits a trace in the grid.

    The first request sends the root: it creates the trace, and the trace it
    holds keeps the sender's id text. The second request sends the child: it
    finds the trace by a database read, so it holds the canonical id. The grid
    must still list one trace, with the root's cells and the child's usage.
    """
    canonical_id, root_id, child_id = _ids()
    sent_id = uuid.UUID(canonical_id).hex.upper()
    first = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [_root(rest_project, sent_id, root_id)],
    )
    assert first.status_code == 201, first.content
    second = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [_child(rest_project, sent_id, child_id, root_id)],
    )
    assert second.status_code == 201, second.content

    page = _list_page(auth_client, rest_project)
    assert page.status_code == 200, page.content
    rows = [
        row
        for row in page.data["result"]["table"]
        if _canonical(row["trace_id"]) == canonical_id
    ]
    assert len(rows) == 1, (
        f"trace {sent_id} is listed {len(rows)} times in the Observe trace "
        "grid; expected one row carrying both spans"
    )
    assert {key: rows[0][key] for key in ("trace_id", *GRID_CELLS)} == {
        "trace_id": canonical_id,
        **EXPECTED_CELLS,
    }


def test_rest_trace_row_still_reaches_clickhouse_traces(
    auth_client, rest_project, mirrors_on, django_capture_on_commit_callbacks
):
    """The REST route keeps writing the trace row to ClickHouse ``traces``."""
    trace_id, root_id, _ = _ids()
    sent = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [_root(rest_project, trace_id, root_id)],
    )
    assert sent.status_code == 201, sent.content

    rows = (
        V2AnalyticsQueryService()
        .execute_ch_query(
            "SELECT toString(id) AS id, toString(project_id) AS project_id, "
            "input, is_deleted FROM traces FINAL "
            "WHERE toString(id) = %(trace_id)s",
            {"trace_id": trace_id},
        )
        .data
    )
    assert rows == [
        {
            "id": trace_id,
            "project_id": str(rest_project.id),
            "input": ROOT_INPUT,
            "is_deleted": 0,
        }
    ]


class _OutageClient:
    """Every insert fails, as when ClickHouse is down."""

    def __init__(self):
        self.attempts = []

    def insert(self, table, rows, column_names=None):
        self.attempts.append((table, len(rows)))
        raise RuntimeError("injected ClickHouse outage")

    def close(self):
        pass


def test_clickhouse_outage_is_logged_and_rest_ingestion_still_succeeds(
    auth_client,
    rest_project,
    mirrors_on,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """A ClickHouse outage never breaks REST ingestion.

    The request still answers 201, both spans stay in Postgres, and every
    batch the mirrors tried is logged with its row count and none of the
    span content.
    """
    outage = _OutageClient()
    monkeypatch.setattr(trace_writer, "_get_client", lambda: outage)
    trace_id, root_id, child_id = _ids()
    with capture_logs() as logs:
        sent = _send(
            auth_client,
            django_capture_on_commit_callbacks,
            [
                _root(rest_project, trace_id, root_id),
                _child(rest_project, trace_id, child_id, root_id),
            ],
        )

    assert sent.status_code == 201, sent.content
    assert sorted(sent.data["result"]["ids"]) == sorted([root_id, child_id])
    stored = ObservationSpan.no_workspace_objects.filter(
        id__in=[root_id, child_id]
    ).values_list("id", flat=True)
    assert sorted(stored) == sorted([root_id, child_id])

    trace_failed = [line for line in logs if line["event"] == "trace_dual_write_failed"]
    span_failed = [line for line in logs if line["event"] == "span_dual_write_failed"]
    assert [table for table, _ in outage.attempts if table == "traces"] == ["traces"]
    assert [line["n"] for line in trace_failed] == [1]
    # One logged failure per span batch the mirror tried, each with its count.
    assert sorted(line["n_rows"] for line in span_failed) == sorted(
        count for table, count in outage.attempts if table == "spans"
    )
    assert CHILD_INPUT not in repr(trace_failed + span_failed)


def test_span_sent_without_a_session_is_copied_with_its_trace_session(
    rest_project, integration_setup, mirrors_on, django_capture_on_commit_callbacks
):
    """A span that names no session reaches ClickHouse with its trace's session.

    The route writes the spans of one request on separate worker threads, so
    a span that names no session can load its trace before a sibling span
    stamps the session on it. Here the root names no session and its child
    does. Both go through the route's create path, root first, inside one
    transaction: the root loads the trace before the child stamps it, and the
    copies run only at the commit, after both writes. That order comes from
    the code, not from thread timing, so the stamp is in place before the
    root's copy runs on every run.
    """
    trace_id, root_id, child_id = _ids()
    session_name = f"rest-mirror-session-{uuid.uuid4().hex[:8]}"
    session_id = str(deterministic_trace_session_id(rest_project.id, session_name))
    sessionless = _root(rest_project, trace_id, root_id)
    sibling = _with_session(
        _child(rest_project, trace_id, child_id, root_id), session_name
    )

    with django_capture_on_commit_callbacks(execute=True):
        for span in (sessionless, sibling):
            create_single_otel_span(
                span,
                rest_project.organization_id,
                integration_setup.user.id,
                rest_project.workspace_id,
            )

    assert str(Trace.objects.get(id=trace_id).session_id) == session_id
    rows = (
        V2AnalyticsQueryService()
        .execute_ch_query(
            "SELECT id, toString(trace_session_id) AS trace_session_id "
            "FROM spans FINAL WHERE trace_id = %(trace_id)s ORDER BY id",
            {"trace_id": trace_id},
        )
        .data
    )
    assert rows == [
        {"id": child_id, "trace_session_id": session_id},
        {"id": root_id, "trace_session_id": session_id},
    ], f"the span without a session lost its trace's session: {rows}"


# A ten-span REST request against a ClickHouse that never answers must be
# answered within this many seconds: one short wait for the ClickHouse copy,
# plus the request's own Postgres writes and room for a busy runner. Waiting
# the writers' 15 seconds for each span's copy takes minutes.
TEN_SPAN_BOUND_S = 5.0


class _HungClickHouse:
    """A local TCP server that accepts every connection and never answers."""

    def __init__(self):
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._held = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._held.append(connection)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)
        for connection in self._held:
            connection.close()
        self._listener.close()


@pytest.fixture
def hung_clickhouse(mirrors_on):
    """The span and trace writers pointed at a ClickHouse that never answers.

    The writers build their client from ``get_v2_config()``, which reads the
    ``CLICKHOUSE_V2`` setting before the environment. The cached client is
    dropped on the way in and on the way out, so the writers build one
    against this server and keep none of it after the test.
    """
    server = _HungClickHouse()
    hung = {
        **settings.CLICKHOUSE_V2,
        "CH25_HOST": "127.0.0.1",
        "CH25_HTTP_PORT": server.port,
    }
    try:
        with override_settings(CLICKHOUSE_V2=hung):
            trace_writer._reset_client()
            config = trace_writer.get_v2_config()
            assert (config["host"], config["http_port"]) == ("127.0.0.1", server.port)
            yield server
            trace_writer._reset_client()
    finally:
        server.close()


def test_a_hung_clickhouse_holds_a_ten_span_rest_request_for_seconds(
    auth_client, rest_project, hung_clickhouse, django_capture_on_commit_callbacks
):
    """A ClickHouse that accepts and never answers holds REST ingestion briefly.

    The route copies the spans to ClickHouse while the request waits. Against
    a store that never answers, the request still answers 201 with all ten
    spans in Postgres, within ``TEN_SPAN_BOUND_S``. The measured seconds are
    printed on a ``REST_TEN_SPAN_SECONDS`` line.
    """
    trace_id, root_id, _ = _ids()
    child_ids = [f"child-{uuid.uuid4().hex[:16]}" for _ in range(9)]
    span_ids = [root_id, *child_ids]
    spans = [_root(rest_project, trace_id, root_id)]
    for child_id in child_ids:
        spans.append(_child(rest_project, trace_id, child_id, root_id))

    started = time.monotonic()
    sent = _send(auth_client, django_capture_on_commit_callbacks, spans)
    seconds = time.monotonic() - started
    print("REST_TEN_SPAN_SECONDS", f"{seconds:.2f}")

    assert sent.status_code == 201, sent.content
    assert sorted(sent.data["result"]["ids"]) == sorted(span_ids)
    stored = ObservationSpan.no_workspace_objects.filter(id__in=span_ids)
    assert sorted(stored.values_list("id", flat=True)) == sorted(span_ids)
    assert seconds < TEN_SPAN_BOUND_S, (
        f"a ten-span REST request took {seconds:.1f} s against a ClickHouse "
        f"that never answers; the bound is {TEN_SPAN_BOUND_S:.0f} s"
    )

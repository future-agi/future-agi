"""How the REST span route refuses a request, and what a refusal leaves behind.

``/tracer/observation-span/create_otel_span/`` takes the OTel spans of one
trace. A database that cannot answer is not a bad request: the route answers
503 in the product's ordinary retry shape, so the client sends the spans
again. No answer carries the error's own text. The driver's words, connection
details and SQL go to the log; the body holds a fixed sentence. A refused
request leaves nothing of its trace in Postgres or ClickHouse, so the retry
stores the whole trace once. A project that another writer creates between
this request's read and its create is read again, not refused.

The route stores spans on a worker thread with its own database connection.
That thread cannot see rows inside the per-test transaction, so these tests
post into committed projects of their own, and the module removes those
projects and their ClickHouse rows when it ends.

Each failure is forced at one database read, through the manager the code
under test calls: the read raises, or a rival row appears before it returns.
The code that answers the request is never replaced.
"""

from __future__ import annotations

import uuid

import pytest
from django.conf import settings
from django.db import DatabaseError, OperationalError
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from model_hub.models.custom_models import CustomAIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.query_service import (
    V2AnalyticsQueryService,
    reset_v2_query_client,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CREATE_URL = "/tracer/observation-span/create_otel_span/"

# The fixed sentences the route answers with.
INVALID_SPANS = (
    "The spans could not be read. Check each span's fields and send them again."
)
STORAGE_UNAVAILABLE = "Span storage is temporarily unavailable. Please retry."
NOT_STORED = (
    "Error creating observation span: "
    "Unable to create observation span. Please try again."
)

# Synthetic database errors. The address is a documentation-only private one.
EXHAUSTED = (
    "connection to server at 10.1.2.3, port 5432 failed: "
    "FATAL: sorry, too many clients already"
)
SQL_FAILURE = (
    'relation "tracer_project" does not exist\n'
    'LINE 1: SELECT "tracer_project"."id" FROM "tracer_project" '
    'WHERE "tracer_project"."name" = %s\n'
    "CONTEXT: connection to server at 10.1.2.3, port 5432"
)

# Text of those errors, and of the wrappers the route once put round them,
# that no response body may carry.
ERROR_TEXT = (
    "10.1.2.3",
    "5432",
    "FATAL",
    "too many clients",
    "connection to server",
    "tracer_project",
    "relation",
    "SELECT",
    "FROM",
    "WHERE",
    "LINE 1",
    "CONTEXT",
    "OperationalError",
    "DatabaseError",
    "Traceback",
    "Error converting OTel span",
    "Failed to create or get project",
)
# The keys of the product's ordinary error envelope.
ENVELOPE_KEYS = ("status", "type", "code", "detail", "message", "error", "result")

ROOT_NAME = "refusal root"
CHILD_NAME = "refusal child"


@pytest.fixture(scope="module")
def committed_project_names(integration_setup, django_db_blocker, ch_schema):
    """Names of the committed projects this module makes; removed at its end.

    A worker thread commits its rows, so they outlive each test. The reads
    here use the unfiltered manager, which needs no request context.
    """
    names = []
    yield names

    with django_db_blocker.unblock():
        projects = list(
            Project.with_simulation.filter(
                organization=integration_setup.organization,
                name__in=names,
                deleted=False,
            )
        )
    db = settings.CLICKHOUSE["CH_DATABASE"]
    for project in projects:
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


@pytest.fixture(scope="module")
def refusal_project(integration_setup, django_db_blocker, committed_project_names):
    """An Observe project committed outside the per-test transaction."""
    name = f"REST span refusals {uuid.uuid4().hex[:8]}"
    committed_project_names.append(name)
    with django_db_blocker.unblock():
        project = Project.objects.create(
            name=name,
            organization=integration_setup.organization,
            workspace=integration_setup.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
            session_config=[
                {"id": "session_input", "name": "Session Input", "is_visible": True},
            ],
        )
    return project


@pytest.fixture
def mirrors_on(monkeypatch, ch_routes_on):
    """The app's post-commit ClickHouse mirrors on, as once the CDC chain is gone."""
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
    project_name,
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
        "project_name": project_name,
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


def _root(project_name, trace_id, span_id):
    return _span(
        project_name,
        trace_id,
        span_id,
        name=ROOT_NAME,
        kind="CHAIN",
        latency_ms=1500,
        attributes={"input.value": "a question", "output.value": "an answer"},
    )


def _child(project_name, trace_id, span_id, root_id, *, attributes):
    return _span(
        project_name,
        trace_id,
        span_id,
        name=CHILD_NAME,
        kind="LLM",
        latency_ms=900,
        parent_id=root_id,
        start_offset_ms=100,
        attributes={
            "input.value": "a prompt",
            "output.value": "a reply",
            **attributes,
        },
    )


def _ids():
    return (
        str(uuid.uuid4()),
        f"root-{uuid.uuid4().hex[:16]}",
        f"child-{uuid.uuid4().hex[:16]}",
    )


def _send(auth_client, capture_on_commit, spans):
    """POST the span list the way an SDK client does."""
    with capture_on_commit(execute=True):
        return auth_client.post(CREATE_URL, spans, format="json")


def _fail_project_read(monkeypatch, project_name, error):
    """The read of ``project_name`` raises ``error``, as a database that cannot answer.

    The project manager's own ``get`` still serves every other read.
    """
    manager = Project.no_workspace_objects
    real_get = manager.get

    def get(*args, **kwargs):
        if kwargs.get("name") == project_name:
            raise error
        return real_get(*args, **kwargs)

    monkeypatch.setattr(manager, "get", get)


def _rival_creates_after_the_read(monkeypatch, project_name):
    """Another writer creates ``project_name`` just after this request's read misses it.

    The read is the project manager's own. When it misses the name for the
    first time, the rival row is made with the model's own create, on the same
    thread, before the miss reaches the caller. The route's own create then
    meets the unique constraint, as a second worker's would. Returns the list
    that receives the rival project.
    """
    manager = Project.no_workspace_objects
    real_get = manager.get
    rival = []

    def get(*args, **kwargs):
        try:
            return real_get(*args, **kwargs)
        except Project.DoesNotExist:
            if kwargs.get("name") != project_name or rival:
                raise
            rival.append(
                manager.create(
                    name=project_name,
                    trace_type=kwargs["trace_type"],
                    organization=kwargs["organization"],
                    workspace=kwargs["workspace"],
                    model_type=AIModel.ModelTypes.GENERATIVE_LLM,
                    metadata={},
                )
            )
            raise

    monkeypatch.setattr(manager, "get", get)
    return rival


def _fail_pricing_read(monkeypatch, model_name, error):
    """The custom-price read of ``model_name`` raises ``error`` while switched on.

    A span whose model the price table does not know reads its organization's
    custom price while it is converted, after its project is resolved. Returns
    the switch.
    """
    manager = CustomAIModel.objects
    real_get = manager.get
    switch = {"on": True}

    def get(*args, **kwargs):
        if switch["on"] and kwargs.get("user_model_id") == model_name:
            raise error
        return real_get(*args, **kwargs)

    monkeypatch.setattr(manager, "get", get)
    return switch


def _stored_span_ids(span_ids):
    return sorted(
        ObservationSpan.no_workspace_objects.filter(id__in=span_ids).values_list(
            "id", flat=True
        )
    )


def _trace_stored(trace_id):
    return Trace.no_workspace_objects.filter(id=trace_id).exists()


def _clickhouse_rows(trace_id):
    """Distinct ids of this trace's rows in ClickHouse ``spans`` and ``traces``."""
    query = V2AnalyticsQueryService()
    params = {"trace_id": trace_id}
    spans = query.execute_ch_query(
        "SELECT uniqExact(id) AS n FROM spans WHERE toString(trace_id) = %(trace_id)s",
        params,
    ).data
    traces = query.execute_ch_query(
        "SELECT uniqExact(id) AS n FROM traces WHERE toString(id) = %(trace_id)s",
        params,
    ).data
    return {"spans": spans[0]["n"], "traces": traces[0]["n"]}


def _assert_fixed_answer(response, *, status_code, error_type, code, message):
    """The product's ordinary error envelope around one fixed sentence.

    The text check covers the whole body, so no key carries error text.
    """
    assert response.status_code == status_code, response.content
    envelope = response.json()
    assert {key: envelope.get(key) for key in ENVELOPE_KEYS} == {
        "status": False,
        "type": error_type,
        "code": code,
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
    }
    body = response.content.decode()
    carried = [text for text in ERROR_TEXT if text in body]
    assert carried == [], f"the response body carries error text: {carried}"


def test_database_out_of_connections_answers_503_with_a_fixed_sentence(
    auth_client, refusal_project, monkeypatch, django_capture_on_commit_callbacks
):
    """Out of connections at the project read: 503, retry shape, no driver text."""
    _fail_project_read(monkeypatch, refusal_project.name, OperationalError(EXHAUSTED))
    trace_id, root_id, child_id = _ids()

    response = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [
            _root(refusal_project.name, trace_id, root_id),
            _child(refusal_project.name, trace_id, child_id, root_id, attributes={}),
        ],
    )

    _assert_fixed_answer(
        response,
        status_code=503,
        error_type="service_unavailable",
        code="service_unavailable",
        message=STORAGE_UNAVAILABLE,
    )
    assert _stored_span_ids([root_id, child_id]) == []
    assert not _trace_stored(trace_id)


def test_database_error_answers_500_without_driver_or_sql_text(
    auth_client, refusal_project, monkeypatch, django_capture_on_commit_callbacks
):
    """Any other database error at the same read: 500 and a fixed sentence.

    The error names a table, carries SQL and a connection's details; the body
    carries none of it.
    """
    _fail_project_read(monkeypatch, refusal_project.name, DatabaseError(SQL_FAILURE))
    trace_id, root_id, _ = _ids()

    response = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [_root(refusal_project.name, trace_id, root_id)],
    )

    _assert_fixed_answer(
        response,
        status_code=500,
        error_type="server_error",
        code="server_error",
        message=NOT_STORED,
    )
    assert _stored_span_ids([root_id]) == []


def test_unreadable_span_answers_400_with_a_fixed_sentence(
    auth_client, refusal_project, django_capture_on_commit_callbacks
):
    """A span the route cannot read is still a 400, minus the converter's text.

    A start time that is not a number fails the conversion with Python's own
    message, which the body never carries.
    """
    trace_id, root_id, _ = _ids()
    span = _root(refusal_project.name, trace_id, root_id)
    span["start_time"] = "not-a-time"

    response = _send(auth_client, django_capture_on_commit_callbacks, [span])

    _assert_fixed_answer(
        response,
        status_code=400,
        error_type="validation_error",
        code="invalid",
        message=INVALID_SPANS,
    )
    body = response.content.decode()
    assert "unsupported operand" not in body
    assert "TypeError" not in body
    assert _stored_span_ids([root_id]) == []


def test_span_without_a_trace_id_answers_400_and_makes_no_trace(
    auth_client, refusal_project, django_capture_on_commit_callbacks
):
    """A span with no trace id is the client's mistake, so the answer is a 400.

    The request stores nothing: no span, and no trace made up for it.
    """
    _, root_id, _ = _ids()
    span = _root(refusal_project.name, None, root_id)
    traces_before = Trace.no_workspace_objects.filter(project=refusal_project).count()

    response = _send(auth_client, django_capture_on_commit_callbacks, [span])

    _assert_fixed_answer(
        response,
        status_code=400,
        error_type="validation_error",
        code="invalid",
        message=INVALID_SPANS,
    )
    assert _stored_span_ids([root_id]) == []
    assert (
        Trace.no_workspace_objects.filter(project=refusal_project).count()
        == traces_before
    )


def test_refused_request_leaves_nothing_of_its_trace_and_the_retry_stores_it_once(
    auth_client,
    refusal_project,
    mirrors_on,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """One child fails conversion on a database error: nothing of the trace stays.

    The project resolves and the root and the first child convert; the second
    child's custom-price read finds the database out of connections. Neither
    Postgres nor ClickHouse keeps a row of the trace, and the answer is 503.
    Sent again once the database answers, the same body stores all three
    spans once, which also shows these reads see what the worker commits.
    """
    trace_id, root_id, child_id = _ids()
    priced_id = f"priced-{uuid.uuid4().hex[:16]}"
    model_name = f"refusal-pricing-{uuid.uuid4().hex[:12]}"
    pricing = _fail_pricing_read(monkeypatch, model_name, OperationalError(EXHAUSTED))
    span_ids = [root_id, child_id, priced_id]
    spans = [
        _root(refusal_project.name, trace_id, root_id),
        _child(
            refusal_project.name,
            trace_id,
            child_id,
            root_id,
            attributes={"gen_ai.cost.total": 0.25},
        ),
        _child(
            refusal_project.name,
            trace_id,
            priced_id,
            root_id,
            attributes={
                "llm.model_name": model_name,
                "llm.token_count.prompt": 3,
                "llm.token_count.completion": 2,
                "llm.token_count.total": 5,
            },
        ),
    ]

    refused = _send(auth_client, django_capture_on_commit_callbacks, spans)

    assert _stored_span_ids(span_ids) == [], (
        f"a refused request ({refused.status_code}) left spans in Postgres"
    )
    assert not _trace_stored(trace_id), "a refused request left its trace in Postgres"
    assert _clickhouse_rows(trace_id) == {"spans": 0, "traces": 0}, (
        "a refused request left rows in ClickHouse"
    )
    _assert_fixed_answer(
        refused,
        status_code=503,
        error_type="service_unavailable",
        code="service_unavailable",
        message=STORAGE_UNAVAILABLE,
    )

    pricing["on"] = False
    sent = _send(auth_client, django_capture_on_commit_callbacks, spans)

    assert sent.status_code == 201, sent.content
    assert sorted(sent.data["result"]["ids"]) == sorted(span_ids)
    assert _stored_span_ids(span_ids) == sorted(span_ids)
    assert _trace_stored(trace_id)
    assert _clickhouse_rows(trace_id) == {"spans": 3, "traces": 1}


def test_project_created_by_another_writer_meanwhile_is_read_again(
    auth_client,
    integration_setup,
    committed_project_names,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """A first request for a new project that loses the create race answers 201.

    Another writer creates the project between this request's read and its
    create. ``Project.save`` validates the model first, so the route's create
    meets the unique constraint as a validation error before any insert. The
    route reads the project again and stores the span under it; one project
    of that name exists.
    """
    name = f"REST span race {uuid.uuid4().hex[:8]}"
    committed_project_names.append(name)
    rival = _rival_creates_after_the_read(monkeypatch, name)
    trace_id, root_id, _ = _ids()

    response = _send(
        auth_client,
        django_capture_on_commit_callbacks,
        [_root(name, trace_id, root_id)],
    )

    assert rival, "the route never read the new project before creating it"
    assert response.status_code == 201, response.content
    assert response.data["result"]["ids"] == [root_id]
    projects = Project.with_simulation.filter(
        organization=integration_setup.organization,
        name=name,
        trace_type="observe",
        deleted=False,
    ).values_list("id", flat=True)
    assert list(projects) == [rival[0].id]
    stored = ObservationSpan.no_workspace_objects.get(id=root_id)
    assert stored.project_id == rival[0].id

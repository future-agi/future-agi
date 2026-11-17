"""Bulk trace ingest stores each span under a trace of its own project.

Every request goes through the bulk ingest task the trace server hands each
export to. The stored payload is stood in by the request's OTLP JSON, and the
task body runs without its connection wrapper, as the inline task fixture
does, so the test transaction stays open.
"""

import base64
import json
import time
import uuid
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_trace_session_id,
)
from tracer.utils.trace_ingestion import bulk_create_observation_span_task

REFUSED_EVENT = "bulk_ingest_trace_ids_refused"
HELD_TEXT = "content-of-the-held-trace"
SENT_TEXT = "content-of-the-sent-trace"


def _kv(key, value):
    return {"key": key, "value": {"stringValue": value}}


def _b64(hex_str):
    return base64.b64encode(bytes.fromhex(hex_str)).decode()


def _trace_hex():
    return uuid.uuid4().hex


def _span_hex():
    return uuid.uuid4().hex[:16]


def _span(trace_hex, span_hex, text, session, parent_hex=None):
    now = time.time()
    span = {
        "trace_id": _b64(trace_hex),
        "span_id": _b64(span_hex),
        "name": text,
        "start_time_unix_nano": str(int((now - 1) * 1e9)),
        "end_time_unix_nano": str(int(now * 1e9)),
        "attributes": [
            _kv("input.value", text),
            _kv("output.value", text),
            _kv("session.id", session),
            _kv("openinference.span.kind", "CHAIN"),
        ],
        "status": {"code": "STATUS_CODE_OK"},
        "events": [],
    }
    if parent_hex:
        span["parent_span_id"] = _b64(parent_hex)
    return span


def _request(project, spans):
    scope = {"scope": {"name": "bulk-ingest-scope"}, "spans": spans}
    resource = {
        "attributes": [
            _kv("project_name", project.name),
            _kv("project_type", "observe"),
            _kv("service.name", "bulk-ingest-scope"),
        ]
    }
    return {"resource_spans": [{"resource": resource, "scope_spans": [scope]}]}


def _send(request, organization, user, workspace):
    """Runs one export through the bulk ingest task, as the trace server does."""
    raw = json.dumps(request).encode("utf-8")
    with patch("tracer.utils.trace_ingestion.payload_storage") as storage:
        storage.retrieve.return_value = raw
        bulk_create_observation_span_task._original_func(
            "bulk-ingest-scope",
            str(organization.id),
            str(user.id),
            str(workspace.id),
            "json",
        )


def _hold_trace(project, organization, user, workspace):
    """The project ingests one root span; returns the id of its trace."""
    trace_hex = _trace_hex()
    root = _span(trace_hex, _span_hex(), HELD_TEXT, "held-session")
    _send(_request(project, [root]), organization, user, workspace)
    return uuid.UUID(trace_hex)


def _stored_fields(trace_id):
    """Every column of the trace row, read with deleted rows included."""
    row = Trace.all_objects.get(id=trace_id)
    return {f.attname: getattr(row, f.attname) for f in Trace._meta.concrete_fields}


def _span_ids(trace_id):
    spans = ObservationSpan.objects.filter(trace_id=trace_id)
    return set(spans.values_list("id", flat=True))


def _refused_lines(logs):
    return [line for line in logs if line["event"] == REFUSED_EVENT]


def _assert_refused_once(logs, held_id, holder, *, traces, spans):
    """One log line counts what the batch does not store; it names no holder id."""
    lines = _refused_lines(logs)
    assert len(lines) == 1, lines
    assert lines[0]["refused_traces"] == traces
    assert lines[0]["refused_spans"] == spans
    text = repr(lines[0])
    assert str(held_id) not in text
    assert held_id.hex not in text
    assert str(holder.id) not in text
    assert str(holder.organization_id) not in text


def _assert_stored(trace_hex, project, *, spans):
    """The trace is in the project, with that many spans, all in the project."""
    trace = Trace.objects.get(id=uuid.UUID(trace_hex))
    assert trace.project_id == project.id
    stored = ObservationSpan.objects.filter(trace_id=trace.id)
    assert stored.count() == spans
    assert set(stored.values_list("project_id", flat=True)) == {project.id}
    return trace


def _observe_project(name, organization, workspace):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def second_project(db, organization, workspace):
    """A second observe project of the same organization."""
    return _observe_project("Second Observe Project", organization, workspace)


@pytest.fixture
def other_organization(db):
    """Another organization with its own user, workspace and observe project."""
    org = Organization.objects.create(name="Other Organization")
    other_user = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    other_workspace = Workspace.objects.create(
        name="Other Workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    project = _observe_project("Other Observe Project", org, other_workspace)
    return org, other_user, other_workspace, project


@pytest.mark.django_db
def test_a_trace_id_another_organization_holds_is_refused_and_the_rest_is_stored(
    organization, user, workspace, observe_project, other_organization
):
    other_org, other_user, other_workspace, sender = other_organization
    held_id = _hold_trace(observe_project, organization, user, workspace)
    before = _stored_fields(held_id)
    spans_before = _span_ids(held_id)
    assert before["project_id"] == observe_project.id
    assert len(spans_before) == 1

    refused_span, other_hex = _span_hex(), _trace_hex()
    spans = [
        _span(held_id.hex, refused_span, SENT_TEXT, "sent-session"),
        _span(other_hex, _span_hex(), SENT_TEXT, "sent-session"),
    ]
    with capture_logs() as logs:
        _send(_request(sender, spans), other_org, other_user, other_workspace)

    assert _stored_fields(held_id) == before
    assert _span_ids(held_id) == spans_before
    assert not ObservationSpan.objects.filter(id=refused_span).exists()
    _assert_refused_once(logs, held_id, observe_project, traces=1, spans=1)
    _assert_stored(other_hex, sender, spans=1)


@pytest.mark.django_db
def test_a_trace_id_another_project_of_the_organization_holds_is_refused(
    organization, user, workspace, observe_project, second_project
):
    held_id = _hold_trace(observe_project, organization, user, workspace)
    before = _stored_fields(held_id)
    spans_before = _span_ids(held_id)
    assert len(spans_before) == 1

    refused_span, other_hex = _span_hex(), _trace_hex()
    spans = [
        _span(held_id.hex, refused_span, SENT_TEXT, "sent-session"),
        _span(other_hex, _span_hex(), SENT_TEXT, "sent-session"),
    ]
    with capture_logs() as logs:
        _send(_request(second_project, spans), organization, user, workspace)

    assert _stored_fields(held_id) == before
    assert _span_ids(held_id) == spans_before
    assert not ObservationSpan.objects.filter(id=refused_span).exists()
    _assert_refused_once(logs, held_id, observe_project, traces=1, spans=1)
    _assert_stored(other_hex, second_project, spans=1)


@pytest.mark.django_db
def test_spans_sent_again_under_a_trace_of_the_same_project_update_it(
    organization, user, workspace, observe_project
):
    trace_hex, root_hex = _trace_hex(), _span_hex()
    child = _span(trace_hex, _span_hex(), "child", "first", parent_hex=root_hex)
    _send(_request(observe_project, [child]), organization, user, workspace)
    assert Trace.objects.get(id=uuid.UUID(trace_hex)).input is None

    root = _span(trace_hex, root_hex, "root", "second")
    with capture_logs() as logs:
        _send(_request(observe_project, [root]), organization, user, workspace)

    trace = _assert_stored(trace_hex, observe_project, spans=2)
    root_span = ObservationSpan.objects.get(id=root_hex)
    assert root_span.input is not None
    assert (trace.input, trace.output) == (root_span.input, root_span.output)
    session = deterministic_trace_session_id(observe_project.id, "second")
    assert str(trace.session_id) == str(session)
    assert not _refused_lines(logs)


@pytest.mark.django_db
def test_a_new_trace_id_is_created_in_the_sending_project(
    organization, user, workspace, observe_project
):
    trace_hex, root_hex = _trace_hex(), _span_hex()
    root = _span(trace_hex, root_hex, "root", "new")
    with capture_logs() as logs:
        _send(_request(observe_project, [root]), organization, user, workspace)

    trace = _assert_stored(trace_hex, observe_project, spans=1)
    root_span = ObservationSpan.objects.get(id=root_hex)
    assert root_span.input is not None
    assert (trace.input, trace.output) == (root_span.input, root_span.output)
    session = deterministic_trace_session_id(observe_project.id, "new")
    assert str(trace.session_id) == str(session)
    assert not _refused_lines(logs)


@pytest.mark.django_db
def test_a_trace_id_a_deleted_trace_of_another_project_keeps_is_refused(
    organization, user, workspace, observe_project, other_organization
):
    other_org, other_user, other_workspace, sender = other_organization
    held_id = _hold_trace(observe_project, organization, user, workspace)
    Trace.objects.get(id=held_id).delete()
    before = _stored_fields(held_id)
    spans_before = _span_ids(held_id)
    assert before["deleted"] is True
    assert len(spans_before) == 1

    refused_span, other_hex = _span_hex(), _trace_hex()
    spans = [
        _span(held_id.hex, refused_span, SENT_TEXT, "sent-session"),
        _span(other_hex, _span_hex(), SENT_TEXT, "sent-session"),
    ]
    with capture_logs() as logs:
        _send(_request(sender, spans), other_org, other_user, other_workspace)

    assert _stored_fields(held_id) == before
    assert _span_ids(held_id) == spans_before
    assert not ObservationSpan.objects.filter(id=refused_span).exists()
    _assert_refused_once(logs, held_id, observe_project, traces=1, spans=1)
    _assert_stored(other_hex, sender, spans=1)

"""Historical Imagine content needs guarded-run provenance, not a status label.

Use the real HTTP endpoints, ORM, and starter. Only external Temporal and
ClickHouse boundaries are replaced by the same fixtures as the cache tests.
Running fixtures reconstruct the old completed-save / running-overwrite order.
"""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.trace import Trace
from tracer.tests import (
    test_imagine_analysis_trace_scope,
    test_imagine_cache_provenance,
)
from tracer.tests.test_imagine_analysis_api import IMAGINE_ANALYSIS_PATH, result

imagine_scope = test_imagine_analysis_trace_scope.imagine_scope
shared_trace_reader = test_imagine_cache_provenance.shared_trace_reader

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reader_projection_contract(shared_trace_reader):
    """Accept the real reader's projection option without changing scope filtering."""
    scoped_read = shared_trace_reader.reader.list_by_trace.side_effect

    def list_by_trace(trace_id, *, project_id=None, include_heavy=True):
        # These fixtures only need identity filtering; projection changes no rows.
        return scoped_read(trace_id, project_id=project_id)

    shared_trace_reader.reader.list_by_trace.side_effect = list_by_trace


@pytest.fixture
def dispatch(monkeypatch):
    starter = Mock()
    monkeypatch.setattr("tfc.temporal.imagine.client.start_workflow_sync", starter)
    return starter


def _analysis(scope, row_status, content, error, workflow_kind="legacy", trace_id=None):
    analysis_id = uuid.uuid4()
    workflow_id = {
        "legacy": f"imagine-analysis-{analysis_id}",
        "null": None,
        "empty": "",
        "marked": f"imagine-pb1-{analysis_id}",
    }[workflow_kind]
    row = ImagineAnalysis.no_workspace_objects.create(
        id=analysis_id,
        saved_view=scope.saved_view,
        project=scope.project,
        organization=scope.organization,
        widget_id="summary",
        trace_id=str(trace_id or scope.local_trace.id),
        prompt="Historical prompt",
        content=content,
        error=error,
        status="completed"
        if row_status in ("running", "stale_running")
        else row_status,
        workflow_id=workflow_id,
    )
    if row_status in ("running", "stale_running"):
        # Historical worker saved first; old POST then overwrote only status.
        updated_at = timezone.now()
        if row_status == "stale_running":
            updated_at -= timedelta(minutes=5)
        ImagineAnalysis.no_workspace_objects.filter(pk=row.pk).update(
            status="running", updated_at=updated_at
        )
        row.refresh_from_db()
    assert (row.status, row.content, row.error, row.workflow_id) == (
        "running" if row_status == "stale_running" else row_status,
        content,
        error,
        workflow_id,
    ), "SETUP_AQ-XWS-W3F: historical row retains its payload and provenance"
    return row


def _request(client, scope, method, trace_id=None):
    data = {
        "saved_view_id": str(scope.saved_view.id),
        "trace_id": str(trace_id or scope.local_trace.id),
    }
    if method == "post":
        data.update(
            project_id=str(scope.project.id),
            widgets=[{"widget_id": "summary", "prompt": "Fresh scoped summary"}],
        )
        return client.post(IMAGINE_ANALYSIS_PATH, data, format="json")
    return client.get(IMAGINE_ANALYSIS_PATH, data)


def _stored_state(row):
    return (
        row.status,
        row.content,
        row.error,
        row.workflow_id,
        row.prompt,
        row.updated_at,
    )


@pytest.mark.parametrize("row_status", ["running", "pending", "failed"])
@pytest.mark.parametrize("workflow_kind", ["legacy", "null", "empty"])
@pytest.mark.parametrize("payload", ["content_and_error", "error_only"])
def test_poll_hides_every_unmarked_noncompleted_payload(
    auth_client,
    imagine_scope,
    shared_trace_reader,
    dispatch,
    row_status,
    workflow_kind,
    payload,
):
    content = (
        shared_trace_reader.legacy_content if payload == "content_and_error" else None
    )
    error = f"Historical diagnostic derived from {shared_trace_reader.foreign.output}"
    row = _analysis(imagine_scope, row_status, content, error, workflow_kind)
    before = _stored_state(row)
    with CaptureQueriesContext(connection) as queries:
        response = _request(auth_client, imagine_scope, "get")
    assert (response.status_code, result(response).get("analyses")) == (
        200,
        [
            {
                "id": str(row.id),
                "widget_id": "summary",
                "status": "pending",
                "content": None,
                "error": None,
            }
        ],
    ), "AQ-XWS-W3F: polling must hide unmarked content and error for every status"
    row.refresh_from_db()
    assert (
        _stored_state(row) == before
        and dispatch.call_count == 0
        and not any(
            f'{verb} "{ImagineAnalysis._meta.db_table}"' in query["sql"]
            for query in queries
            for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
        )
    ), "AQ-XWS-W3F: suppressing historical payloads must leave polling read-only"


@pytest.mark.parametrize("row_status", ["pending", "running", "completed", "failed"])
def test_marked_same_scope_poll_retains_each_status_payload(
    auth_client, imagine_scope, shared_trace_reader, dispatch, row_status
):
    row = _analysis(
        imagine_scope,
        row_status,
        f"Scoped summary derived from {shared_trace_reader.local.output}",
        "Scoped diagnostic",
        workflow_kind="marked",
    )
    before = _stored_state(row)
    response = _request(auth_client, imagine_scope, "get")
    row.refresh_from_db()
    assert (
        response.status_code,
        result(response).get("analyses"),
        _stored_state(row),
        dispatch.call_count,
    ) == (
        200,
        [
            {
                "id": str(row.id),
                "widget_id": "summary",
                "status": row_status,
                "content": row.content,
                "error": row.error,
            }
        ],
        before,
        0,
    ), "AQ-XWS-W3F: marked same-scope polling must preserve every status and payload"


@pytest.mark.parametrize(
    "row_status", ["pending", "running", "stale_running", "failed", "completed"]
)
@pytest.mark.parametrize("workflow_kind", ["legacy", "null", "empty", "marked"])
def test_post_preserves_cache_wait_and_redispatch_rules_without_legacy_payloads(
    auth_client,
    imagine_scope,
    shared_trace_reader,
    dispatch,
    row_status,
    workflow_kind,
):
    content = (
        f"Scoped summary derived from {shared_trace_reader.local.output}"
        if workflow_kind == "marked"
        else shared_trace_reader.legacy_content
    )
    error = (
        "Scoped diagnostic"
        if workflow_kind == "marked"
        else f"Historical diagnostic derived from {shared_trace_reader.foreign.output}"
    )
    row = _analysis(imagine_scope, row_status, content, error, workflow_kind)
    before = _stored_state(row)
    response = _request(auth_client, imagine_scope, "post")
    row.refresh_from_db()
    expected_item = {"id": str(row.id), "widget_id": "summary", "status": "running"}
    cached = workflow_kind == "marked" and row_status == "completed"
    if cached:
        expected_item.update(status="completed", content=content)
    assert (response.status_code, result(response).get("analyses")) == (
        200,
        [expected_item],
    ), "AQ-XWS-W3F: POST must keep its existing shape without returning legacy payloads"
    if cached or row_status == "running":
        assert (_stored_state(row), dispatch.call_count) == (before, 0), (
            "AQ-XWS-W3F: POST must preserve marked cache hits and fresh-running waits"
        )
    else:
        assert (
            row.status,
            row.content,
            row.error,
            row.prompt,
            row.workflow_id,
            dispatch.call_count,
        ) == (
            "running",
            None,
            None,
            "Fresh scoped summary",
            f"imagine-pb1-{row.id}",
            1,
        ), "AQ-XWS-W3F: POST must reset and redispatch stale or retryable rows"


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("foreign_scope", ["other_workspace", "other_project"])
@pytest.mark.parametrize("row_status", ["running", "pending", "failed"])
def test_foreign_and_absent_traces_keep_identical_notfound_envelopes(
    auth_client,
    imagine_scope,
    shared_trace_reader,
    dispatch,
    method,
    foreign_scope,
    row_status,
):
    foreign = imagine_scope.foreign_traces[foreign_scope]
    missing = uuid.uuid4()
    row = _analysis(
        imagine_scope,
        row_status,
        shared_trace_reader.legacy_content,
        "Historical foreign diagnostic",
        trace_id=foreign.id,
    )
    before = _stored_state(row)
    assert Trace.no_workspace_objects.filter(pk=foreign.id).exists(), (
        "SETUP_AQ-XWS-W3F: foreign trace exists"
    )
    assert not Trace.no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-W3F: nonexistent trace is absent"
    )
    control = _request(auth_client, imagine_scope, "get")
    assert control.status_code == 200, (
        "SETUP_AQ-XWS-W3F: same-scope trace passes real admission"
    )
    responses = [
        _request(auth_client, imagine_scope, method, trace_id)
        for trace_id in (foreign.id, missing)
    ]
    observed = [(response.status_code, response.json()) for response in responses]
    row.refresh_from_db()
    assert (
        observed[0] == observed[1]
        and observed[0][0] == 404
        and observed[0][1]["code"] == "not_found"
        and "Trace not found" in str(observed[0][1])
        and dispatch.call_count == 0
        and _stored_state(row) == before
    ), "AQ-XWS-W3F: foreign and absent traces must have identical not-found envelopes"

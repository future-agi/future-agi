"""Trace, span and voice-call detail for an id held by several projects.

A trace id is not unique across projects: an OTLP replay of the same corpus,
a re-import, or a voice provider account shared by several voice projects
writes the same ``trace_id``/``span_id`` into more than one project. When two
of those projects are inside the caller's scope, the detail endpoints must
serve one project's copy instead of answering 503 "retry" forever, and must
still never select a copy from a project outside that scope.

Opened from a specific project, the caller passes ``project_id`` and gets that
project's copy even when another in-scope project holds a newer one; a
``project_id`` outside the caller's scope answers exactly like a missing id.

The ClickHouse row layer is faked; Postgres scope, the views and the bounded
readers run for real.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project

STARTED = datetime(2026, 9, 22, 16, 27, 36, 543000, tzinfo=UTC)


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )


def _copy(project, trace_id, span_id, *, version, parent="", kind="conversation"):
    """One project's latest-state physical row of one span."""

    return {
        "project_id": str(project.id),
        "id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent,
        "name": span_id,
        "observation_type": kind,
        "start_time": STARTED,
        "end_time": STARTED + timedelta(seconds=1),
        "input": json.dumps({"copy": project.name}),
        "output": "",
        "model": "",
        "latency_ms": 1000,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "status": "OK",
        "status_message": "",
        "tags": "[]",
        "span_events": "[]",
        "provider": "vapi",
        "span_attributes": "{}",
        "project_version_id": None,
        "custom_eval_config_id": None,
        "trace_session_id": None,
        "metadata_json": "{}",
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
        "_version": version,
    }


class _PhysicalCopies:
    """Answer the bounded detail reads from per-project physical copies.

    Every query honours the project scope the reader binds, as the real
    ``PREWHERE project_id IN ...`` does, and identity candidates come back
    newest write first.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params))
        scoped = [
            row
            for row in self.rows
            if row["project_id"] in params.get("detail_project_ids", ())
        ]
        if "latest_physical_spans" in query:
            wanted = {
                (project_id, trace_id, span_id)
                for project_id, trace_id, span_id, _start_us in params[
                    "detail_span_identities"
                ]
            }
            return SimpleNamespace(
                data=[
                    row
                    for row in scoped
                    if (row["project_id"], row["trace_id"], row["id"]) in wanted
                ]
            )
        if "%(detail_span_id)s" in query:
            candidates = [
                row for row in scoped if row["id"] == params["detail_span_id"]
            ]
        elif "%(detail_trace_id)s" in query:
            candidates = [
                row for row in scoped if row["trace_id"] == params["detail_trace_id"]
            ]
        else:
            return SimpleNamespace(data=[])  # evals / annotations
        return SimpleNamespace(
            data=[
                {
                    "project_id": row["project_id"],
                    "trace_id": row["trace_id"],
                    "span_id": row["id"],
                    "start_time": row["start_time"],
                    "latest_is_deleted": 0,
                    "latest_version": row["_version"],
                }
                for row in sorted(
                    candidates, key=lambda row: row["_version"], reverse=True
                )
            ]
        )


@pytest.fixture
def replayed_trace(user, workspace, monkeypatch):
    """One voice trace written into two of the caller's projects and a foreign one."""

    older = _project(user.organization, workspace, "older")
    newer = _project(user.organization, workspace, "newer")
    empty = _project(user.organization, workspace, "empty")
    foreign_org = Organization.objects.create(name=f"Foreign {uuid.uuid4().hex[:8]}")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="Foreign Workspace",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    foreign = _project(foreign_org, foreign_workspace, "foreign")
    trace_id = str(uuid.uuid4())
    rows = []
    for project, version in ((older, 10), (newer, 20), (foreign, 30)):
        rows.append(_copy(project, trace_id, "root", version=version))
        rows.append(
            _copy(
                project,
                trace_id,
                "child",
                version=version,
                parent="root",
                kind="llm",
            )
        )
    analytics = _PhysicalCopies(rows)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        "tracer.views.observation_span.V2AnalyticsQueryService", lambda: analytics
    )
    monkeypatch.setattr(
        "tracer.views.trace.ObservabilityService.process_raw_logs",
        lambda *_args, **_kwargs: {},
    )
    return SimpleNamespace(
        trace_id=trace_id,
        older=older,
        newer=newer,
        empty=empty,
        foreign=foreign,
        analytics=analytics,
    )


def _read_projects(analytics):
    """Projects bound by every span read after identity resolution."""

    return {
        project_id
        for query, params in analytics.calls
        if "latest_physical_spans" in query
        for project_id in params["detail_project_ids"]
    }


@pytest.mark.django_db
def test_trace_detail_serves_newest_in_scope_copy(auth_client, replayed_trace):
    response = auth_client.get(f"/tracer/trace/{replayed_trace.trace_id}/")

    assert response.status_code == status.HTTP_200_OK, response.data
    roots = response.data["result"]["observation_spans"]
    assert [entry["observation_span"]["id"] for entry in roots] == ["root"]
    root = roots[0]["observation_span"]
    assert root["project"] == str(replayed_trace.newer.id)
    assert root["input"] == {"copy": "newer"}
    assert [child["observation_span"]["id"] for child in roots[0]["children"]] == [
        "child"
    ]
    assert _read_projects(replayed_trace.analytics) == {str(replayed_trace.newer.id)}


@pytest.mark.django_db
def test_span_detail_serves_newest_in_scope_copy(auth_client, replayed_trace):
    response = auth_client.get("/tracer/observation-span/child/")

    assert response.status_code == status.HTTP_200_OK, response.data
    span = response.data["result"]["observation_span"]
    assert span["id"] == "child"
    assert span["project"] == str(replayed_trace.newer.id)
    assert span["input"] == {"copy": "newer"}
    assert _read_projects(replayed_trace.analytics) == {str(replayed_trace.newer.id)}


@pytest.mark.django_db
def test_voice_call_detail_serves_newest_in_scope_copy(auth_client, replayed_trace):
    response = auth_client.get(
        "/tracer/trace/voice_call_detail/", {"trace_id": replayed_trace.trace_id}
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    result = response.data["result"]
    assert result["project_id"] == str(replayed_trace.newer.id)
    assert [span["id"] for span in result["observation_span"]] == ["root", "child"]
    assert json.loads(result["observation_span"][1]["input"]) == {"copy": "newer"}
    assert _read_projects(replayed_trace.analytics) == {str(replayed_trace.newer.id)}


# (request for an id, served project of a 200 response) per detail endpoint.
_ENDPOINTS = {
    "trace": (
        lambda trace_id, params: (f"/tracer/trace/{trace_id}/", params),
        lambda result: result["observation_spans"][0]["observation_span"]["project"],
    ),
    "span": (
        lambda trace_id, params: (
            "/tracer/observation-span/child/"
            if trace_id
            else "/tracer/observation-span/missing/",
            params,
        ),
        lambda result: result["observation_span"]["project"],
    ),
    "voice": (
        lambda trace_id, params: (
            "/tracer/trace/voice_call_detail/",
            {"trace_id": trace_id or str(uuid.uuid4()), **params},
        ),
        lambda result: result["project_id"],
    ),
}


def _get(auth_client, endpoint, trace_id, **params):
    url, query = _ENDPOINTS[endpoint][0](trace_id, params)
    return auth_client.get(url, query)


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
def test_pinned_project_serves_its_copy_over_a_newer_one(
    auth_client, replayed_trace, endpoint
):
    older = str(replayed_trace.older.id)

    response = _get(auth_client, endpoint, replayed_trace.trace_id, project_id=older)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert _ENDPOINTS[endpoint][1](response.data["result"]) == older
    assert all(
        params["detail_project_ids"] == (older,)
        for _query, params in replayed_trace.analytics.calls
        if "detail_project_ids" in params
    )


@pytest.mark.django_db
@pytest.mark.parametrize("extra", [{"format": "json"}, {"unrelated": "1"}])
def test_trace_detail_ignores_unknown_query_params(auth_client, replayed_trace, extra):
    """The project pin must not make trace detail reject params it used to ignore.

    Before the pin, GET /tracer/trace/{id}/ read no query serializer, so DRF's
    ``?format=json`` and any param an external caller sends were ignored.
    """
    older = str(replayed_trace.older.id)

    unpinned = auth_client.get(f"/tracer/trace/{replayed_trace.trace_id}/", extra)
    pinned = auth_client.get(
        f"/tracer/trace/{replayed_trace.trace_id}/", {"project_id": older, **extra}
    )

    assert unpinned.status_code == status.HTTP_200_OK, unpinned.data
    assert _ENDPOINTS["trace"][1](unpinned.data["result"]) == str(
        replayed_trace.newer.id
    )
    assert pinned.status_code == status.HTTP_200_OK, pinned.data
    assert _ENDPOINTS["trace"][1](pinned.data["result"]) == older


@pytest.mark.django_db
def test_trace_detail_still_rejects_a_malformed_project_pin(
    auth_client, replayed_trace
):
    response = auth_client.get(
        f"/tracer/trace/{replayed_trace.trace_id}/",
        {"project_id": "not-a-uuid", "format": "json"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "project_id" in json.dumps(response.data)
    assert "format" not in json.dumps(response.data)
    assert replayed_trace.analytics.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
@pytest.mark.parametrize("pin", ["foreign", "unknown", "empty"])
def test_pin_outside_scope_or_without_the_id_answers_like_a_missing_id(
    auth_client, replayed_trace, endpoint, pin
):
    project_id = {
        "foreign": str(replayed_trace.foreign.id),
        "unknown": str(uuid.uuid4()),
        "empty": str(replayed_trace.empty.id),
    }[pin]
    missing = _get(auth_client, endpoint, None)
    replayed_trace.analytics.calls.clear()

    response = _get(
        auth_client, endpoint, replayed_trace.trace_id, project_id=project_id
    )

    assert missing.status_code in (
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    )
    assert (response.status_code, response.data) == (
        missing.status_code,
        missing.data,
    )
    # A project outside the caller's scope never reaches ClickHouse.
    bound = {
        project
        for _query, params in replayed_trace.analytics.calls
        for project in params.get("detail_project_ids", ())
    }
    assert bound <= {str(replayed_trace.empty.id)}

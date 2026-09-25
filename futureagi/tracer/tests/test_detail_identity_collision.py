"""Trace, span and voice-call detail for an id held by several projects.

A trace id is not unique across projects: an OTLP replay of the same corpus,
a re-import, or a voice provider account shared by several voice projects
writes the same ``trace_id``/``span_id`` into more than one project. When two
of those projects are inside the caller's scope, the detail endpoints must
serve one project's copy instead of answering 503 "retry" forever, and must
still never select a copy from a project outside that scope.

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
    return SimpleNamespace(
        trace_id=trace_id,
        older=older,
        newer=newer,
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
def test_voice_call_detail_serves_newest_in_scope_copy(
    auth_client, replayed_trace, monkeypatch
):
    monkeypatch.setattr(
        "tracer.views.trace.ObservabilityService.process_raw_logs",
        lambda *_args, **_kwargs: {},
    )

    response = auth_client.get(
        "/tracer/trace/voice_call_detail/", {"trace_id": replayed_trace.trace_id}
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    result = response.data["result"]
    assert result["project_id"] == str(replayed_trace.newer.id)
    assert [span["id"] for span in result["observation_span"]] == ["root", "child"]
    assert json.loads(result["observation_span"][1]["input"]) == {"copy": "newer"}
    assert _read_projects(replayed_trace.analytics) == {str(replayed_trace.newer.id)}

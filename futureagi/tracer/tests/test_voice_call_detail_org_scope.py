"""Ownership scoping for the voice call detail endpoint.

The endpoint resolves a trace's project from ClickHouse and then checks that the
caller owns it. That check must use the *request-scoped* organization (the one
the user is currently acting in, injected from ``X-Organization-Id``), not the
organization stored on the user row. A user whose active organization differs
from their home organization owns projects the user-row check cannot see, and
every lookup 404s — which strands the drawer on its list-row stub.

Scope note: the test client injects ``request.organization`` straight from the
header, so these cases pin the view's *scoping* behaviour only. That the header
itself cannot name an organization the user has no membership in is enforced
upstream in authentication and covered by ``accounts.tests.test_multi_org_auth``.
"""

import uuid
from datetime import UTC, datetime

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailRead,
    TraceDetailReadUnavailable,
)

VOICE_CALL_DETAIL_URL = "/tracer/trace/voice_call_detail/"


def _make_org_project(user, label):
    """An organization + workspace + project that is NOT the user's home org."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} Org {suffix}")
    workspace = Workspace.no_workspace_objects.create(
        name=f"{label} Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    project = Project.objects.create(
        name=f"{label} Project",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )
    return org, workspace, project


def _detail(project_id, trace_id):
    return TraceDetailRead(
        project_id=str(project_id),
        spans=(
            {
                "id": "root",
                "project_id": str(project_id),
                "trace_id": str(trace_id),
                "parent_span_id": "",
                "name": "conversation",
                "observation_type": "conversation",
                "start_time": datetime(2026, 7, 30, tzinfo=UTC),
                "end_time": datetime(2026, 7, 30, 0, 0, 1, tzinfo=UTC),
                "latency_ms": 1000,
                "status": "OK",
                "provider": "vapi",
                "span_attributes": "{}",
                "metadata_json": "{}",
                "attrs_string": {},
                "attrs_number": {},
                "attrs_bool": {},
            },
        ),
        eval_config_ids=(),
        evals=(),
        annotations=(),
        query_count=3,
        elapsed_ms=1.0,
    )


@pytest.mark.django_db
def test_detail_allows_project_in_active_org_not_user_home_org(
    auth_client, user, monkeypatch
):
    # The user's home organization stays whatever the fixture made it; the
    # request acts in a different organization that owns the project.
    _, active_workspace, project = _make_org_project(user, "Active")
    assert project.organization_id != user.organization_id
    auth_client.set_workspace(active_workspace)
    trace_id = str(uuid.uuid4())
    direct_write_analytics = object()
    monkeypatch.setattr(
        "tracer.views.trace.V2AnalyticsQueryService",
        lambda: direct_write_analytics,
    )

    def fake_read(**kwargs):
        assert kwargs["analytics"] is direct_write_analytics
        assert kwargs["project_ids"] == [str(project.id)]
        assert kwargs["trace_id"] == trace_id
        assert kwargs["include_annotations"] is False
        assert kwargs["deadline_ms"] == 6000
        kwargs["eval_config_ids_resolver"](str(project.id))
        return _detail(project.id, trace_id)

    monkeypatch.setattr("tracer.views.trace.read_trace_detail", fake_read)

    response = auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": trace_id})

    assert response.status_code == status.HTTP_200_OK
    assert str(project.id) in str(response.data)
    assert trace_id in str(response.data)


@pytest.mark.django_db
def test_detail_still_rejects_project_in_unrelated_org(auth_client, user, monkeypatch):
    # Guard on the loosened check: acting in one organization must not grant
    # access to a project owned by a different one.
    _, active_workspace, _ = _make_org_project(user, "Active")
    _, _, foreign_project = _make_org_project(user, "Foreign")
    auth_client.set_workspace(active_workspace)

    def fake_read(**kwargs):
        assert str(foreign_project.id) not in kwargs["project_ids"]
        raise TraceDetailNotFound

    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr("tracer.views.trace.read_trace_detail", fake_read)

    response = auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "trace_id not found" in str(response.data)


@pytest.mark.django_db
def test_detail_sanitizes_bounded_clickhouse_failure(auth_client, user, monkeypatch):
    _, active_workspace, _ = _make_org_project(user, "Active")
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: object())

    def unavailable(**_kwargs):
        raise TraceDetailReadUnavailable("clickhouse_query_failed")

    monkeypatch.setattr("tracer.views.trace.read_trace_detail", unavailable)

    response = auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())})

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "temporarily unavailable" in str(response.data)
    assert "clickhouse_query_failed" not in str(response.data)
    assert "DB::Exception" not in str(response.data)


@pytest.mark.django_db
def test_detail_preserves_sanitized_generic_bad_request(auth_client, user, monkeypatch):
    _, active_workspace, _ = _make_org_project(user, "Active")
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: object())

    def fail_with_private_detail(**_kwargs):
        raise RuntimeError("private compiler state and SQL")

    monkeypatch.setattr(
        "tracer.views.trace.read_trace_detail", fail_with_private_detail
    )

    response = auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["result"] == "Voice call details could not be loaded"
    assert "private compiler state" not in str(response.data)

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
from tracer.serializers.trace import (
    TraceVoiceCallDetailQuerySerializer,
    TraceVoiceCallDetailResponseSerializer,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailRead,
    TraceDetailReadUnavailable,
)

VOICE_CALL_DETAIL_URL = "/tracer/trace/voice_call_detail/"
VOICE_CALL_LIST_URL = "/tracer/trace/list_voice_calls/"


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
@pytest.mark.parametrize("trace_id_parameter", ("trace_id", "traceId"))
def test_detail_allows_project_in_active_org_not_user_home_org(
    auth_client, user, monkeypatch, trace_id_parameter
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
    monkeypatch.setattr(
        "tracer.views.trace.ObservabilityService.process_raw_logs",
        lambda *_args, **_kwargs: {
            # Real Vapi rows can preserve present-but-empty optional text.
            # Strict response validation must publish the successful detail
            # response instead of turning these values into HTTP 400.
            "recording_url": "",
            "call_summary": "",
        },
    )

    response = auth_client.get(
        VOICE_CALL_DETAIL_URL,
        {trace_id_parameter: trace_id},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] is True
    assert response.data["result"]["project_id"] == str(project.id)
    assert response.data["result"]["trace_id"] == trace_id
    assert response.data["result"]["recording_url"] == ""
    assert response.data["result"]["call_summary"] == ""


@pytest.mark.parametrize(
    "aliases",
    (
        ("trace_id",),
        ("traceId",),
        ("trace_id", "traceId"),
    ),
)
def test_detail_query_contract_normalizes_compatibility_aliases(aliases):
    trace_id = uuid.uuid4()
    serializer = TraceVoiceCallDetailQuerySerializer(
        data={alias: str(trace_id) for alias in aliases}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"trace_id": trace_id}


def test_detail_success_contract_requires_envelope_and_named_result_shape():
    trace_id = str(uuid.uuid4())
    serializer = TraceVoiceCallDetailResponseSerializer(
        data={
            "status": True,
            "result": {
                "id": trace_id,
                "trace_id": trace_id,
                "project_id": str(uuid.uuid4()),
                "provider_call_id": None,
                "phone_number": None,
                "duration_seconds": None,
                # Provider normalization distinguishes unavailable-but-present
                # text from an omitted value with an empty string. Strict
                # response validation must publish this shape instead of
                # converting an otherwise successful detail read into HTTP 400.
                "recording_url": "",
                "call_summary": "",
                "cost_breakdown": None,
                "transcript": [{"role": "user", "content": "hello"}],
                "messages": None,
                "analysis_data": None,
                "scenario_id": None,
                "turn_count": 3,
                "recording": {},
                "recording_available": False,
                "call_metadata": {},
                "observation_span": [],
                "eval_outputs": {},
                "talk_ratio": None,
                "agent_talk_percentage": None,
                "bot_talk_pct": None,
                "user_talk_pct": None,
                "avg_agent_latency_ms": None,
                "user_wpm": None,
                "bot_wpm": None,
                "user_interruption_count": None,
                "ai_interruption_count": None,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["result"]["transcript"] == [
        {"role": "user", "content": "hello"}
    ]
    assert serializer.validated_data["result"]["recording_url"] == ""
    assert serializer.validated_data["result"]["call_summary"] == ""
    missing_status = TraceVoiceCallDetailResponseSerializer(data={"result": {}})
    assert not missing_status.is_valid()
    assert "status" in missing_status.errors

    missing_result_identity = TraceVoiceCallDetailResponseSerializer(
        data={"status": True, "result": {}}
    )
    assert not missing_result_identity.is_valid()
    assert "trace_id" in missing_result_identity.errors["result"]


@pytest.mark.parametrize(
    ("query", "error_field"),
    (
        ({}, "trace_id"),
        ({"trace_id": "not-a-uuid"}, "trace_id"),
        (
            {"trace_id": str(uuid.uuid4()), "traceId": str(uuid.uuid4())},
            "traceId",
        ),
        ({"trace_id": str(uuid.uuid4()), "allow_sampled": "true"}, "allow_sampled"),
    ),
)
def test_detail_query_contract_rejects_invalid_or_ambiguous_identity(
    query, error_field
):
    serializer = TraceVoiceCallDetailQuerySerializer(data=query)

    assert not serializer.is_valid()
    assert error_field in serializer.errors


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
def test_detail_returns_sanitized_server_error_for_unexpected_failure(
    auth_client, user, monkeypatch
):
    _, active_workspace, _ = _make_org_project(user, "Active")
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: object())

    def fail_with_private_detail(**_kwargs):
        raise RuntimeError("private compiler state and SQL")

    monkeypatch.setattr(
        "tracer.views.trace.read_trace_detail", fail_with_private_detail
    )

    response = auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())})

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.data["result"] == "Voice call details could not be loaded"
    assert "private compiler state" not in str(response.data)


@pytest.mark.django_db
def test_list_returns_sanitized_server_error_for_unexpected_failure(
    auth_client, user, monkeypatch
):
    _, active_workspace, project = _make_org_project(user, "Active")
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: object())

    def fail_with_private_detail(*_args, **_kwargs):
        raise RuntimeError("private compiler state and SQL")

    monkeypatch.setattr(
        "tracer.views.trace.TraceView._list_voice_calls_clickhouse",
        fail_with_private_detail,
    )

    response = auth_client.get(
        VOICE_CALL_LIST_URL,
        {"project_id": str(project.id)},
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.data["result"] == "Voice call data could not be loaded"
    assert "private compiler state" not in str(response.data)

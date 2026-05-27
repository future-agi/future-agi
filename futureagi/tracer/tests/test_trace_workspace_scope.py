import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


def make_same_org_other_workspace_trace(organization, user, *, trace_type="experiment"):
    suffix = uuid.uuid4().hex[:8]
    other_workspace = Workspace.objects.create(
        name=f"Other Trace Workspace {suffix}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name=f"Other Trace Project {suffix}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
        metadata={},
    )
    other_project_version = ProjectVersion.objects.create(
        project=other_project,
        name=f"Other Trace Run {suffix}",
        version="v1",
        metadata={},
        config=[],
    )
    other_session = TraceSession.objects.create(
        project=other_project,
        name=f"Other Trace Session {suffix}",
    )
    other_trace = Trace.objects.create(
        project=other_project,
        project_version=other_project_version,
        session=other_session if trace_type == "observe" else None,
        name=f"Other Trace {suffix}",
        input={"prompt": "hidden"},
        output={"response": "hidden"},
        tags=["hidden"],
    )
    other_span = ObservationSpan.objects.create(
        id=f"other_trace_span_{suffix}",
        project=other_project,
        project_version=other_project_version,
        trace=other_trace,
        name="Other Trace Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=3),
        end_time=timezone.now(),
        latency_ms=250,
        cost=0.02,
        status="OK",
    )
    return (
        other_workspace,
        other_project,
        other_project_version,
        other_session,
        other_trace,
        other_span,
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestTraceWorkspaceScopeAPI:
    def test_create_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, other_project_version, _, _, _ = (
            make_same_org_other_workspace_trace(organization, user)
        )

        response = auth_client.post(
            "/tracer/trace/",
            {
                "project": str(other_project.id),
                "project_version": str(other_project_version.id),
                "name": "Cross Workspace Trace",
                "input": {"prompt": "cross"},
                "output": {"response": "cross"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Trace.no_workspace_objects.filter(
            project=other_project,
            name="Cross Workspace Trace",
        ).exists()

    def test_patch_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user, trace
    ):
        _, other_project, _, _, _, _ = make_same_org_other_workspace_trace(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/trace/{trace.id}/",
            {"project": str(other_project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        trace.refresh_from_db()
        assert trace.project_id != other_project.id

    def test_update_tags_rejects_same_org_other_workspace_trace_without_mutating(
        self, auth_client, organization, user
    ):
        _, _, _, _, other_trace, _ = make_same_org_other_workspace_trace(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/trace/{other_trace.id}/tags/",
            {"tags": ["leaked"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_trace.refresh_from_db()
        assert other_trace.tags == ["hidden"]

    def test_custom_actions_reject_same_org_other_workspace_project_or_run(
        self, auth_client, organization, user
    ):
        _, other_project, other_project_version, _, other_trace, _ = (
            make_same_org_other_workspace_trace(
                organization, user, trace_type="observe"
            )
        )

        list_response = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(other_project_version.id)},
        )
        index_response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index/",
            {
                "project_version_id": str(other_project_version.id),
                "trace_id": str(other_trace.id),
            },
        )
        observe_index_response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index_observe/",
            {
                "project_id": str(other_project.id),
                "trace_id": str(other_trace.id),
            },
        )
        export_response = auth_client.get(
            "/tracer/trace/get_trace_export_data/",
            {"project_id": str(other_project.id)},
        )
        eval_names_response = auth_client.get(
            "/tracer/trace/get_eval_names/",
            {"project_id": str(other_project.id)},
        )
        graph_response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
            {
                "project_id": str(other_project.id),
                "property": "Average",
                "filters": [],
                "interval": "day",
                "req_data_config": {"type": "SYSTEM_METRIC", "id": "latency"},
            },
            format="json",
        )
        agent_graph_response = auth_client.get(
            "/tracer/trace/agent_graph/",
            {"project_id": str(other_project.id)},
        )
        compare_response = auth_client.post(
            "/tracer/trace/compare_traces/",
            {
                "project_version_ids": [str(other_project_version.id)],
                "index": 0,
            },
            format="json",
        )

        assert list_response.status_code == status.HTTP_400_BAD_REQUEST
        assert index_response.status_code == status.HTTP_400_BAD_REQUEST
        assert observe_index_response.status_code == status.HTTP_400_BAD_REQUEST
        assert export_response.status_code == status.HTTP_400_BAD_REQUEST
        assert eval_names_response.status_code == status.HTTP_400_BAD_REQUEST
        assert graph_response.status_code == status.HTTP_400_BAD_REQUEST
        assert agent_graph_response.status_code == status.HTTP_400_BAD_REQUEST
        assert compare_response.status_code == status.HTTP_200_OK
        assert compare_response.data["result"]["total_traces"] == 0
        assert compare_response.data["result"]["trace_comparison"] == {}

    def test_get_properties_returns_graph_property_catalog(self, auth_client):
        response = auth_client.get("/tracer/trace/get_properties/")

        assert response.status_code == status.HTTP_200_OK
        assert "Average" in response.data["result"]
        assert "P95" in response.data["result"]

    def test_agent_graph_falls_back_to_postgres_when_clickhouse_fails(
        self, auth_client, project, observation_span, child_span, monkeypatch
    ):
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ch down")),
        )

        response = auth_client.get(
            "/tracer/trace/agent_graph/",
            {"project_id": str(project.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        node_ids = {node["id"] for node in result["nodes"]}
        root_node_id = f"{observation_span.observation_type}:{observation_span.name}"
        child_node_id = f"{child_span.observation_type}:{child_span.name}"
        assert root_node_id in node_ids
        assert child_node_id in node_ids
        assert any(
            edge["source"] == root_node_id
            and edge["target"] == child_node_id
            and edge["transition_count"] == 1
            for edge in result["edges"]
        )

    def test_generic_delete_cascades_trace_spans_and_eval_logs(
        self, auth_client, trace, observation_span, custom_eval_config
    ):
        eval_log = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            output_float=0.8,
        )

        response = auth_client.delete(f"/tracer/trace/{trace.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        trace.refresh_from_db()
        observation_span.refresh_from_db()
        eval_log.refresh_from_db()
        assert trace.deleted is True
        assert observation_span.deleted is True
        assert eval_log.deleted is True

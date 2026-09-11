import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from agentcc.models.request_log import AgentccRequestLog
from agentcc.models.session import AgentccSession


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSessionStats:
    def test_sessions_list_includes_total_tokens_and_avg_latency(
        self, auth_client, user
    ):
        AgentccSession.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-stats",
            name="Stats Session",
            status=AgentccSession.ACTIVE,
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-stats",
            request_id="req-1",
            total_tokens=30,
            cost="0.001000",
            latency_ms=1697,
            started_at=timezone.now(),
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-stats",
            request_id="req-2",
            total_tokens=63,
            cost="0.002500",
            latency_ms=1593,
            started_at=timezone.now(),
        )

        response = auth_client.get("/agentcc/sessions/")

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        session = next(
            item for item in payload["results"] if item["session_id"] == "sess-stats"
        )
        assert session["stats"]["request_count"] == 2
        assert session["stats"]["total_tokens"] == 93
        assert session["stats"]["total_cost"] == pytest.approx(0.0035)
        assert session["stats"]["avg_latency_ms"] == pytest.approx(1645.0)

    def test_session_detail_includes_total_tokens_and_avg_latency(
        self, auth_client, user
    ):
        session = AgentccSession.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-detail-stats",
            name="Detail Stats Session",
            status=AgentccSession.ACTIVE,
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-detail-stats",
            request_id="req-3",
            total_tokens=48,
            cost="0.004000",
            latency_ms=696,
            started_at=timezone.now(),
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-detail-stats",
            request_id="req-4",
            total_tokens=61,
            cost="0.005500",
            latency_ms=675,
            started_at=timezone.now(),
        )

        response = auth_client.get(f"/agentcc/sessions/{session.id}/")

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()["result"]
        assert payload["stats"]["request_count"] == 2
        assert payload["stats"]["total_tokens"] == 109
        assert payload["stats"]["total_cost"] == pytest.approx(0.0095)
        assert payload["stats"]["avg_latency_ms"] == pytest.approx(685.5)

    def test_session_create_sets_workspace_and_search_filters(
        self, auth_client, workspace
    ):
        response = auth_client.post(
            "/agentcc/sessions/",
            {
                "session_id": "sess-search-workspace",
                "name": "Searchable Gateway Session",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        session = AgentccSession.no_workspace_objects.get(
            session_id="sess-search-workspace"
        )
        assert session.workspace_id == workspace.id

        by_name = auth_client.get("/agentcc/sessions/?search=Searchable")
        assert by_name.status_code == status.HTTP_200_OK
        assert any(
            item["id"] == str(session.id) for item in by_name.json()["results"]
        )

        by_id = auth_client.get("/agentcc/sessions/?search=search-workspace")
        assert by_id.status_code == status.HTTP_200_OK
        assert any(item["id"] == str(session.id) for item in by_id.json()["results"])

    def test_session_stats_and_requests_use_session_workspace(
        self, auth_client, user, workspace
    ):
        other_workspace = Workspace.objects.create(
            name="Other AgentCC Workspace",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        session = AgentccSession.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            session_id="sess-workspace-scope",
            name="Workspace Scoped Session",
            status=AgentccSession.ACTIVE,
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            session_id="sess-workspace-scope",
            request_id="in-workspace",
            total_tokens=10,
            cost="0.001000",
            latency_ms=100,
            started_at=timezone.now(),
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            workspace=other_workspace,
            session_id="sess-workspace-scope",
            request_id="other-workspace",
            total_tokens=999,
            cost="9.990000",
            latency_ms=999,
            started_at=timezone.now(),
        )

        detail = auth_client.get(f"/agentcc/sessions/{session.id}/")
        assert detail.status_code == status.HTTP_200_OK
        stats = detail.json()["result"]["stats"]
        assert stats["request_count"] == 1
        assert stats["total_tokens"] == 10
        assert stats["total_cost"] == pytest.approx(0.001)
        assert stats["avg_latency_ms"] == pytest.approx(100.0)

        requests = auth_client.get(f"/agentcc/sessions/{session.id}/requests/")
        assert requests.status_code == status.HTTP_200_OK
        rows = requests.json()["result"]
        assert [row["request_id"] for row in rows] == ["in-workspace"]

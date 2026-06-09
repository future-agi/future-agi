import pytest
from django.utils import timezone
from rest_framework import status

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
            latency_ms=1697,
            started_at=timezone.now(),
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-stats",
            request_id="req-2",
            total_tokens=63,
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
            latency_ms=696,
            started_at=timezone.now(),
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=user.organization,
            session_id="sess-detail-stats",
            request_id="req-4",
            total_tokens=61,
            latency_ms=675,
            started_at=timezone.now(),
        )

        response = auth_client.get(f"/agentcc/sessions/{session.id}/")

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()["result"]
        assert payload["stats"]["request_count"] == 2
        assert payload["stats"]["total_tokens"] == 109
        assert payload["stats"]["avg_latency_ms"] == pytest.approx(685.5)

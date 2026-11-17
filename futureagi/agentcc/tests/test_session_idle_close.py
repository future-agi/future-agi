"""Sessions close after their idle timeout.

Every session read Active forever: ingestion creates each one active and
nothing closed an idle one, so 52 of 52 demo sessions said Active a day after
their last request. A session now reads Closed once its idle timeout (30
minutes by default, AGENTCC_SESSION_IDLE_TIMEOUT_SECONDS) passes with no
request; a later request with its id reads Active again; a session someone
closed stays closed. The list carries each session's last request time.

The clock is frozen by patching django.utils.timezone.now, which the views
and the models' created_at read.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from agentcc.models.request_log import AgentccRequestLog
from agentcc.models.session import AgentccSession

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _rows(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    return {row["session_id"]: row for row in body.get("results", body.get("result"))}


def _session(user, workspace, session_id, **fields):
    return AgentccSession.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        session_id=session_id,
        name=session_id,
        **fields,
    )


def _request(user, workspace, session_id, started_at, request_id):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
    )


@pytest.fixture
def frozen():
    """A clock that stays where a test puts it."""

    class Clock:
        now = T0

    clock = Clock()
    with patch("django.utils.timezone.now", side_effect=lambda: clock.now):
        yield clock


@pytest.mark.integration
@pytest.mark.api
class TestSessionIdleClose:
    def test_a_session_closes_after_its_idle_timeout(
        self, auth_client, user, workspace, frozen
    ):
        _session(user, workspace, "sess-idle")
        _request(user, workspace, "sess-idle", T0 - timedelta(minutes=5), "r-idle")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-idle"]
        assert row["status"] == "active"
        assert row["closed_reason"] == ""
        assert row["idle_timeout_seconds"] == 30 * 60

        frozen.now = T0 + timedelta(minutes=26)  # 31 minutes after its request
        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-idle"]
        assert row["status"] == "closed"
        assert row["closed_reason"] == "idle"

    def test_the_list_carries_the_last_request_time(
        self, auth_client, user, workspace, frozen
    ):
        _session(user, workspace, "sess-last")
        _request(user, workspace, "sess-last", T0 - timedelta(hours=2), "r-old")
        _request(user, workspace, "sess-last", T0 - timedelta(minutes=7), "r-new")
        _session(user, workspace, "sess-none")

        rows = _rows(auth_client.get("/agentcc/sessions/"))
        last = datetime.fromisoformat(rows["sess-last"]["last_request_at"])
        assert last == T0 - timedelta(minutes=7)
        assert rows["sess-none"]["last_request_at"] is None

    def test_a_new_request_reads_active_again(
        self, auth_client, user, workspace, frozen
    ):
        _session(user, workspace, "sess-back")
        _request(user, workspace, "sess-back", T0 - timedelta(hours=3), "r-1")
        assert _rows(auth_client.get("/agentcc/sessions/"))["sess-back"]["status"] == (
            "closed"
        )

        _request(user, workspace, "sess-back", T0 - timedelta(minutes=1), "r-2")
        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-back"]
        assert row["status"] == "active"
        assert row["closed_reason"] == ""

    def test_a_closed_session_stays_closed_whatever_its_activity(
        self, auth_client, user, workspace, frozen
    ):
        _session(user, workspace, "sess-shut", status=AgentccSession.CLOSED)
        _request(user, workspace, "sess-shut", T0 - timedelta(minutes=1), "r-shut")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-shut"]
        assert row["status"] == "closed"
        assert row["closed_reason"] == "closed"

    def test_a_session_with_no_request_idles_from_its_creation(
        self, auth_client, user, workspace, frozen
    ):
        frozen.now = T0 - timedelta(minutes=40)
        _session(user, workspace, "sess-empty-old")
        frozen.now = T0 - timedelta(minutes=10)
        _session(user, workspace, "sess-empty-new")
        frozen.now = T0

        rows = _rows(auth_client.get("/agentcc/sessions/"))
        assert rows["sess-empty-old"]["status"] == "closed"
        assert rows["sess-empty-new"]["status"] == "active"

    def test_the_status_filter_and_count_follow_the_idle_close(
        self, auth_client, user, workspace, frozen
    ):
        _session(user, workspace, "sess-f-live")
        _request(user, workspace, "sess-f-live", T0 - timedelta(minutes=2), "f-1")
        _session(user, workspace, "sess-f-idle")
        _request(user, workspace, "sess-f-idle", T0 - timedelta(hours=1), "f-2")
        _session(user, workspace, "sess-f-shut", status=AgentccSession.CLOSED)

        active = auth_client.get("/agentcc/sessions/?status=active")
        closed = auth_client.get("/agentcc/sessions/?status=closed")

        assert set(_rows(active)) == {"sess-f-live"}
        assert active.json()["count"] == 1
        assert set(_rows(closed)) == {"sess-f-idle", "sess-f-shut"}
        assert closed.json()["count"] == 2

    def test_detail_reads_the_same_status(self, auth_client, user, workspace, frozen):
        session = _session(user, workspace, "sess-detail-idle")
        _request(user, workspace, "sess-detail-idle", T0 - timedelta(hours=1), "d-1")

        response = auth_client.get(f"/agentcc/sessions/{session.id}/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["status"] == "closed"
        assert result["closed_reason"] == "idle"
        assert result["last_request_at"] is not None

    def test_a_request_in_another_workspace_does_not_keep_it_open(
        self, auth_client, user, workspace, frozen
    ):
        other = Workspace.objects.create(
            name="Other idle workspace",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        _session(user, workspace, "sess-ws")
        _request(user, workspace, "sess-ws", T0 - timedelta(hours=1), "ws-own")
        _request(user, other, "sess-ws", T0 - timedelta(minutes=1), "ws-other")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-ws"]
        assert row["status"] == "closed"
        assert datetime.fromisoformat(row["last_request_at"]) == T0 - timedelta(hours=1)

    def test_the_timeout_is_a_setting(
        self, auth_client, user, workspace, frozen, settings
    ):
        settings.AGENTCC_SESSION_IDLE_TIMEOUT_SECONDS = 120
        _session(user, workspace, "sess-short")
        _request(user, workspace, "sess-short", T0 - timedelta(minutes=3), "s-1")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-short"]
        assert row["status"] == "closed"
        assert row["idle_timeout_seconds"] == 120

    def test_close_answers_closed(self, auth_client, user, workspace, frozen):
        session = _session(user, workspace, "sess-close-now")
        _request(user, workspace, "sess-close-now", T0 - timedelta(minutes=1), "c-1")

        response = auth_client.post(f"/agentcc/sessions/{session.id}/close/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["status"] == "closed"
        assert result["closed_reason"] == "closed"

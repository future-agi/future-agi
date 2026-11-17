"""A session's start is its first request, not when ingestion stored it.

The Sessions "Started" column read the
session row's created_at. Ingestion creates sessions in bulk per batch
(log_ingestion._ensure_sessions_exist), so 12 of 19 sessions shared one
batch time and some "started" after their last request. The API now carries
``first_request_at``, the oldest request counted as the session's own stats
count them (its org and id, and its workspace when it has one).

The clock is frozen by patching django.utils.timezone.now, which the models'
created_at reads, so a session row is "ingested" after its requests.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from agentcc.models.request_log import AgentccRequestLog
from agentcc.models.session import AgentccSession

T0 = datetime(2026, 10, 23, 1, 1, 23, tzinfo=UTC)


def _rows(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    return {row["session_id"]: row for row in body.get("results", body.get("result"))}


def _session(user, workspace, session_id):
    return AgentccSession.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        session_id=session_id,
        name=session_id,
    )


def _request(user, workspace, session_id, started_at, request_id, **fields):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        **fields,
    )


def _time(value):
    return datetime.fromisoformat(value) if value else None


@pytest.fixture
def ingested_at_t0():
    """Every row created in the test is stored at T0, as one ingestion batch."""
    with patch("django.utils.timezone.now", return_value=T0):
        yield


@pytest.mark.integration
@pytest.mark.api
class TestSessionStartedIsTheFirstRequest:
    def test_the_list_carries_the_first_request_time(
        self, auth_client, user, workspace, ingested_at_t0
    ):
        _session(user, workspace, "sess-batch")
        _request(user, workspace, "sess-batch", T0 - timedelta(minutes=9), "b-2")
        _request(user, workspace, "sess-batch", T0 - timedelta(minutes=30), "b-1")
        _request(user, workspace, "sess-batch", T0 - timedelta(minutes=2), "b-3")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-batch"]

        first = _time(row["first_request_at"])
        assert first == T0 - timedelta(minutes=30)
        assert first <= _time(row["last_request_at"])
        # The stored row is the ingestion batch's, later than every request.
        assert _time(row["created_at"]) == T0

    def test_sessions_of_one_batch_keep_their_own_starts(
        self, auth_client, user, workspace, ingested_at_t0
    ):
        for index, minutes in enumerate((50, 20, 5)):
            _session(user, workspace, f"sess-{index}")
            _request(
                user,
                workspace,
                f"sess-{index}",
                T0 - timedelta(minutes=minutes),
                f"r-{index}",
            )

        rows = _rows(auth_client.get("/agentcc/sessions/"))

        assert [_time(rows[f"sess-{i}"]["first_request_at"]) for i in range(3)] == [
            T0 - timedelta(minutes=50),
            T0 - timedelta(minutes=20),
            T0 - timedelta(minutes=5),
        ]

    def test_a_session_with_no_request_has_no_first_request(
        self, auth_client, user, workspace, ingested_at_t0
    ):
        _session(user, workspace, "sess-empty")

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-empty"]

        assert row["first_request_at"] is None
        assert row["last_request_at"] is None

    def test_only_its_own_live_requests_count(
        self, auth_client, user, workspace, ingested_at_t0
    ):
        other = Workspace.objects.create(
            name="Other started workspace",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        _session(user, workspace, "sess-own")
        _request(user, workspace, "sess-own", T0 - timedelta(minutes=10), "own")
        _request(user, other, "sess-own", T0 - timedelta(hours=3), "other-ws")
        _request(
            user,
            workspace,
            "sess-own",
            T0 - timedelta(hours=2),
            "deleted",
            deleted=True,
        )

        row = _rows(auth_client.get("/agentcc/sessions/"))["sess-own"]

        assert _time(row["first_request_at"]) == T0 - timedelta(minutes=10)

    def test_the_detail_carries_it_too(
        self, auth_client, user, workspace, ingested_at_t0
    ):
        session = _session(user, workspace, "sess-detail")
        _request(user, workspace, "sess-detail", T0 - timedelta(minutes=4), "d-1")

        response = auth_client.get(f"/agentcc/sessions/{session.id}/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert _time(result["first_request_at"]) == T0 - timedelta(minutes=4)

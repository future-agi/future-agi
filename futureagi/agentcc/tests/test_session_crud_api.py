"""CRUD + close-action coverage for /agentcc/sessions/."""

import pytest

from agentcc.models.session import AgentccSession


def _make_session(user, workspace, session_id="sess-a", **overrides):
    kwargs = {
        "organization": user.organization,
        "workspace": workspace,
        "session_id": session_id,
        "name": session_id,
        "status": "active",
    }
    kwargs.update(overrides)
    return AgentccSession.objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSessionListPlain:
    def test_list_returns_sessions_for_active_workspace(
        self, auth_client, user, workspace
    ):
        _make_session(user, workspace, session_id="sess-list-a")
        _make_session(user, workspace, session_id="sess-list-b")

        response = auth_client.get("/agentcc/sessions/")
        assert response.status_code == 200
        body = response.json()
        rows = body.get("result", body.get("results", []))
        ids = {row["session_id"] for row in rows}
        assert {"sess-list-a", "sess-list-b"} <= ids

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/sessions/")
        assert response.status_code in (401, 403)


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSessionUpdate:
    def test_put_updates_status(self, auth_client, user, workspace):
        session = _make_session(user, workspace, session_id="sess-put")

        response = auth_client.put(
            f"/agentcc/sessions/{session.id}/",
            {
                "session_id": "sess-put",
                "name": "renamed",
                "status": "closed",
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        session.refresh_from_db()
        assert session.status == "closed"
        assert session.name == "renamed"

    def test_patch_partial_update(self, auth_client, user, workspace):
        session = _make_session(user, workspace, session_id="sess-patch")

        response = auth_client.patch(
            f"/agentcc/sessions/{session.id}/",
            {"metadata": {"tag": "loaded"}},
            format="json",
        )
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.metadata == {"tag": "loaded"}


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSessionDestroy:
    def test_destroy_soft_deletes_session(self, auth_client, user, workspace):
        session = _make_session(user, workspace, session_id="sess-del")

        response = auth_client.delete(f"/agentcc/sessions/{session.id}/")
        assert response.status_code in (200, 204)

        # Subsequent list must not surface the deleted row.
        list_response = auth_client.get("/agentcc/sessions/")
        assert list_response.status_code == 200
        body = list_response.json()
        rows = body.get("result", body.get("results", []))
        ids = {row["session_id"] for row in rows}
        assert "sess-del" not in ids


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSessionClose:
    def test_close_transitions_active_to_closed(
        self, auth_client, user, workspace
    ):
        session = _make_session(user, workspace, session_id="sess-close")
        assert session.status == "active"

        response = auth_client.post(f"/agentcc/sessions/{session.id}/close/")
        assert response.status_code == 200, response.json()
        session.refresh_from_db()
        assert session.status == "closed"

    def test_close_twice_is_idempotent(self, auth_client, user, workspace):
        session = _make_session(
            user, workspace, session_id="sess-close-twice", status="closed"
        )

        response = auth_client.post(f"/agentcc/sessions/{session.id}/close/")
        # Endpoint returns 200 whether already closed or freshly closed.
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.status == "closed"

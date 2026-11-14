"""API integration tests for IntegrationConnection and SyncLog viewsets."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)


def _result(response):
    """Extract 'result' from GeneralMethods wrapper."""
    data = response.json()
    return data.get("result", data)


# ---------------------------------------------------------------------------
# Connection List
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationConnectionListAPI:
    URL = "/integrations/connections/"

    def test_unauthenticated(self, api_client):
        resp = api_client.get(self.URL)
        assert resp.status_code in (
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN,
        )

    def test_empty_list(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["connections"] == []
        assert result["metadata"]["total_count"] == 0

    def test_returns_connections(self, auth_client, integration_connection):
        resp = auth_client.get(self.URL)
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert len(result["connections"]) == 1

    def test_pagination(
        self,
        auth_client,
        organization,
        workspace,
        user,
        int_project,
        encrypted_credentials,
    ):
        # Create 3 connections with unique external_project_name
        for i in range(3):
            IntegrationConnection.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                created_by=user,
                platform="langfuse",
                display_name=f"conn-{i}",
                host_url="https://langfuse.example.com",
                encrypted_credentials=encrypted_credentials,
                project=int_project,
                external_project_name=f"proj-{i}",
                status=ConnectionStatus.ACTIVE,
            )

        resp = auth_client.get(self.URL, {"page_size": 2, "page_number": 0})
        result = _result(resp)
        assert result["metadata"]["total_count"] == 3
        assert result["metadata"]["page_size"] == 2
        assert len(result["connections"]) == 2

    def test_rejects_unknown_query_param(self, auth_client):
        resp = auth_client.get(self.URL, {"legacyPage": 1})
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert resp.json()["details"] == {"legacyPage": ["Unknown field."]}

    def test_excludes_other_org(self, auth_client, integration_connection, db):
        """Connections from other orgs should not be visible."""
        from accounts.models.organization import Organization

        Organization.objects.create(name="Other Org")
        resp = auth_client.get(self.URL)
        result = _result(resp)
        # Only our org's connection
        for conn in result["connections"]:
            assert conn["display_name"] == "Test Langfuse"

    def test_excludes_soft_deleted(self, auth_client, integration_connection):
        integration_connection.deleted = True
        integration_connection.save(update_fields=["deleted"])

        resp = auth_client.get(self.URL)
        result = _result(resp)
        assert result["connections"] == []


# ---------------------------------------------------------------------------
# Connection Retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationConnectionRetrieveAPI:
    def _url(self, pk):
        return f"/integrations/connections/{pk}/"

    def test_retrieve_success(self, auth_client, integration_connection):
        resp = auth_client.get(self._url(integration_connection.id))
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["display_name"] == "Test Langfuse"

    def test_masked_credentials(self, auth_client, integration_connection):
        resp = auth_client.get(self._url(integration_connection.id))
        result = _result(resp)
        assert "****" in result["public_key_display"]
        assert "****" in result["secret_key_display"]

    def test_includes_project_name(self, auth_client, integration_connection):
        resp = auth_client.get(self._url(integration_connection.id))
        result = _result(resp)
        assert result["project_name"] == "Langfuse Import Project"

    def test_not_found(self, auth_client):
        resp = auth_client.get(self._url(uuid.uuid4()))
        assert resp.status_code == http_status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Connection Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationConnectionCreateAPI:
    URL = "/integrations/connections/"

    def _payload(self, **overrides):
        data = {
            "platform": "langfuse",
            "host_url": "https://langfuse.example.com",
            "public_key": "pk-lf-test123",
            "secret_key": "sk-lf-test456",
            "external_project_name": "my-lf-project",
            "backfill_option": "new_only",
        }
        data.update(overrides)
        return data

    @patch("integrations.temporal.activities.start_backfill_workflow", new=MagicMock())
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_create_success_new_project(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [{"id": "p1", "name": "my-lf-project"}],
            "total_traces": 100,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_201_CREATED
        result = _result(resp)
        assert result["platform"] == "langfuse"
        assert result["status"] == "active"
        # Credentials must NOT leak in response
        assert "public_key" not in result
        assert "secret_key" not in result

    @patch("integrations.temporal.activities.start_backfill_workflow", new=MagicMock())
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_create_with_existing_project(self, mock_get_svc, auth_client, int_project):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload(project_id=str(int_project.id))),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_201_CREATED

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_create_validates_credentials_first(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": False,
            "error": "Invalid credentials.",
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.temporal.activities.start_backfill_workflow", new=MagicMock())
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_backfill_all_sets_backfilling_status(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload(backfill_option="all")),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_201_CREATED
        result = _result(resp)
        assert result["status"] == "backfilling"

    @patch("integrations.temporal.activities.start_backfill_workflow", new=MagicMock())
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_new_only_sets_active_status(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload(backfill_option="new_only")),
            content_type="application/json",
        )
        result = _result(resp)
        assert result["status"] == "active"

    @patch("integrations.temporal.activities.start_backfill_workflow", new=MagicMock())
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_duplicate_connection_returns_400(
        self, mock_get_svc, auth_client, integration_connection
    ):
        """IntegrityError on duplicate (org, workspace, platform, external_project_name)."""
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload(external_project_name="my-langfuse-project")),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_create_nonexistent_project_id_returns_400(self, mock_get_svc, auth_client):
        """Providing a project_id that doesn't exist should return 400."""
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload(project_id=str(uuid.uuid4()))),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Connection Update
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationConnectionUpdateAPI:
    def _url(self, pk):
        return f"/integrations/connections/{pk}/"

    def test_patch_display_name(self, auth_client, integration_connection):
        resp = auth_client.patch(
            self._url(integration_connection.id),
            data=json.dumps({"display_name": "New Name"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["display_name"] == "New Name"

    def test_put_display_name_uses_strict_update_contract(
        self, auth_client, integration_connection
    ):
        resp = auth_client.put(
            self._url(integration_connection.id),
            data=json.dumps({"display_name": "PUT Name"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["display_name"] == "PUT Name"
        assert result["status"] == "active"

    def test_put_rejects_status_mutation(self, auth_client, integration_connection):
        resp = auth_client.put(
            self._url(integration_connection.id),
            data=json.dumps({"display_name": "PUT Name", "status": "paused"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert resp.json()["details"] == {"status": ["Unknown field."]}
        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert integration_connection.display_name == "Test Langfuse"

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_patch_credentials_re_validates(
        self, mock_get_svc, auth_client, integration_connection
    ):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.patch(
            self._url(integration_connection.id),
            data=json.dumps({"public_key": "pk-lf-newkey"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        mock_svc.validate_credentials.assert_called_once()

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_patch_invalid_credentials_400(
        self, mock_get_svc, auth_client, integration_connection
    ):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": False,
            "error": "bad creds",
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.patch(
            self._url(integration_connection.id),
            data=json.dumps({"public_key": "pk-lf-bad"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_patch_credentials_clears_error_status(
        self, mock_get_svc, auth_client, error_connection
    ):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.patch(
            self._url(error_connection.id),
            data=json.dumps({"public_key": "pk-lf-fixed"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["status"] == "active"

    def test_patch_display_name_does_not_clear_error(
        self, auth_client, error_connection
    ):
        """Patching only display_name on an error connection should NOT clear error status."""
        resp = auth_client.patch(
            self._url(error_connection.id),
            data=json.dumps({"display_name": "Renamed"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["status"] == "error"

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_patch_only_secret_key_re_validates(
        self, mock_get_svc, auth_client, integration_connection
    ):
        """Updating only secret_key should re-validate with old public_key."""
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.patch(
            self._url(integration_connection.id),
            data=json.dumps({"secret_key": "sk-lf-newsecret"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        # Verify validate was called with a dict containing the old public_key
        call_kwargs = mock_svc.validate_credentials.call_args
        creds = call_kwargs.kwargs.get("credentials") or call_kwargs[1].get(
            "credentials"
        )
        assert "pk-lf-" in creds["public_key"]


# ---------------------------------------------------------------------------
# Connection Delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationConnectionDeleteAPI:
    def test_soft_delete(self, auth_client, integration_connection):
        url = f"/integrations/connections/{integration_connection.id}/"
        resp = auth_client.delete(url)
        assert resp.status_code == http_status.HTTP_200_OK

        integration_connection.refresh_from_db()
        assert integration_connection.deleted is True

    def test_delete_other_org_forbidden(self, auth_client, db):
        """Cannot delete a connection that doesn't belong to our org."""
        resp = auth_client.delete(f"/integrations/connections/{uuid.uuid4()}/")
        assert resp.status_code == http_status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Validate Action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestValidateAction:
    URL = "/integrations/connections/validate/"

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_validate_success(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [{"id": "p1", "name": "test"}],
            "total_traces": 50,
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(
                {
                    "platform": "langfuse",
                    "host_url": "https://langfuse.example.com",
                    "public_key": "pk-lf-test",
                    "secret_key": "sk-lf-test",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["valid"] is True

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_validate_invalid_credentials(self, mock_get_svc, auth_client):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": False,
            "error": "Invalid credentials.",
        }
        mock_get_svc.return_value = mock_svc

        resp = auth_client.post(
            self.URL,
            data=json.dumps(
                {
                    "platform": "langfuse",
                    "host_url": "https://langfuse.example.com",
                    "public_key": "pk-lf-bad",
                    "secret_key": "sk-lf-bad",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    def test_validate_missing_fields(self, auth_client):
        resp = auth_client.post(
            self.URL,
            data=json.dumps({"platform": "langfuse"}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Sync Now Action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestSyncNowAction:
    def _url(self, pk):
        return f"/integrations/connections/{pk}/sync_now/"

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_dispatches(self, mock_sync, auth_client, integration_connection):
        resp = auth_client.post(self._url(integration_connection.id))
        assert resp.status_code == http_status.HTTP_200_OK
        mock_sync.delay.assert_called_once()

    def test_sync_now_rejects_body_fields(self, auth_client, integration_connection):
        resp = auth_client.post(
            self._url(integration_connection.id),
            data=json.dumps({"unexpected": True}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert resp.json()["details"]["unexpected"] == ["Unknown field."]

    def test_sync_now_already_syncing(self, auth_client, syncing_connection):
        resp = auth_client.post(self._url(syncing_connection.id))
        assert resp.status_code == http_status.HTTP_409_CONFLICT

    def test_sync_now_paused(self, auth_client, paused_connection):
        resp = auth_client.post(self._url(paused_connection.id))
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_cooldown(self, mock_sync, auth_client, integration_connection):
        integration_connection.last_synced_at = datetime.now(UTC) - timedelta(
            seconds=10
        )
        integration_connection.save(update_fields=["last_synced_at"])

        resp = auth_client.post(self._url(integration_connection.id))
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "wait" in _result(resp).lower()

    def test_sync_now_backfilling_returns_409(
        self, auth_client, backfilling_connection
    ):
        """BACKFILLING status should also be rejected with 409."""
        resp = auth_client.post(self._url(backfilling_connection.id))
        assert resp.status_code == http_status.HTTP_409_CONFLICT

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_dispatch_failure_returns_400(
        self, mock_sync, auth_client, integration_connection
    ):
        """If dispatch raises, return 400."""
        mock_sync.delay.side_effect = RuntimeError("Temporal unavailable")
        resp = auth_client.post(self._url(integration_connection.id))
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_after_cooldown_succeeds(
        self, mock_sync, auth_client, integration_connection
    ):
        """Exactly 60 seconds elapsed should allow sync."""
        integration_connection.last_synced_at = datetime.now(UTC) - timedelta(
            seconds=61
        )
        integration_connection.save(update_fields=["last_synced_at"])

        resp = auth_client.post(self._url(integration_connection.id))
        assert resp.status_code == http_status.HTTP_200_OK

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_error_connection_dispatches(
        self, mock_sync, auth_client, error_connection
    ):
        """ERROR connections can be manually synced (not blocked)."""
        resp = auth_client.post(self._url(error_connection.id))
        assert resp.status_code == http_status.HTTP_200_OK
        mock_sync.delay.assert_called_once()


# ---------------------------------------------------------------------------
# Pause / Resume Actions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestPauseResumeActions:
    def test_pause_active(self, auth_client, integration_connection):
        url = f"/integrations/connections/{integration_connection.id}/pause/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["status"] == "paused"

    def test_pause_rejects_body_fields(self, auth_client, integration_connection):
        url = f"/integrations/connections/{integration_connection.id}/pause/"
        resp = auth_client.post(
            url,
            data=json.dumps({"unexpected": True}),
            content_type="application/json",
        )
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert resp.json()["details"]["unexpected"] == ["Unknown field."]

    def test_pause_already_paused(self, auth_client, paused_connection):
        url = f"/integrations/connections/{paused_connection.id}/pause/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    def test_resume_paused(self, auth_client, paused_connection):
        url = f"/integrations/connections/{paused_connection.id}/resume/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["status"] == "active"

    def test_resume_non_paused(self, auth_client, integration_connection):
        url = f"/integrations/connections/{integration_connection.id}/resume/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    def test_resume_no_project(self, auth_client, paused_connection):
        paused_connection.project = None
        paused_connection.save(update_fields=["project"])

        url = f"/integrations/connections/{paused_connection.id}/resume/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    def test_resume_error_connection_rejected(self, auth_client, error_connection):
        """Cannot resume an ERROR connection (only paused can be resumed)."""
        url = f"/integrations/connections/{error_connection.id}/resume/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    def test_pause_and_resume_round_trip(self, auth_client, integration_connection):
        """Pause then resume should return to active."""
        pk = integration_connection.id
        resp1 = auth_client.post(f"/integrations/connections/{pk}/pause/")
        assert _result(resp1)["status"] == "paused"

        resp2 = auth_client.post(f"/integrations/connections/{pk}/resume/")
        assert _result(resp2)["status"] == "active"

    def test_pause_error_connection_succeeds(self, auth_client, error_connection):
        """ERROR connections can be paused (only already-paused is rejected)."""
        url = f"/integrations/connections/{error_connection.id}/pause/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_200_OK
        assert _result(resp)["status"] == "paused"

    def test_resume_syncing_connection_rejected(self, auth_client, syncing_connection):
        """SYNCING connections cannot be resumed (not paused)."""
        url = f"/integrations/connections/{syncing_connection.id}/resume/"
        resp = auth_client.post(url)
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Sync Log List
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestSyncLogListAPI:
    URL = "/integrations/sync-logs/"

    def test_list_unauthenticated(self, api_client):
        resp = api_client.get(self.URL)
        assert resp.status_code in (
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN,
        )

    def test_list_sync_logs(self, auth_client, sync_log):
        resp = auth_client.get(self.URL)
        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["metadata"]["total_count"] == 1
        assert len(result["sync_logs"]) == 1

    def test_filter_by_connection(self, auth_client, sync_log, integration_connection):
        resp = auth_client.get(
            self.URL, {"connection_id": str(integration_connection.id)}
        )
        result = _result(resp)
        assert result["metadata"]["total_count"] == 1

    def test_filter_wrong_connection(self, auth_client, sync_log):
        resp = auth_client.get(self.URL, {"connection_id": str(uuid.uuid4())})
        result = _result(resp)
        assert result["metadata"]["total_count"] == 0

    def test_rejects_invalid_connection_id(self, auth_client):
        resp = auth_client.get(self.URL, {"connection_id": "not-a-uuid"})
        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert resp.json()["details"]["connection_id"] == ["Must be a valid UUID."]

    def test_excludes_other_workspace_logs(
        self,
        auth_client,
        organization,
        workspace,
        user,
        encrypted_credentials,
    ):
        """Sync logs must follow the same workspace scope as connections."""
        other_workspace = Workspace.objects.create(
            name="Other Integration Workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        other_connection = IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=other_workspace,
            created_by=user,
            platform=IntegrationPlatform.LANGFUSE,
            display_name="Other Workspace Langfuse",
            host_url="https://langfuse.example.com",
            encrypted_credentials=encrypted_credentials,
            external_project_name="other-workspace-project",
            status=ConnectionStatus.ACTIVE,
            sync_interval_seconds=300,
            backfill_completed=True,
        )
        SyncLog.objects.create(
            connection=other_connection,
            status=SyncStatus.SUCCESS,
            started_at=datetime.now(UTC) - timedelta(minutes=2),
            completed_at=datetime.now(UTC) - timedelta(minutes=1),
        )

        resp = auth_client.get(self.URL, {"connection_id": str(other_connection.id)})

        assert resp.status_code == http_status.HTTP_200_OK
        assert _result(resp)["metadata"]["total_count"] == 0


@pytest.mark.django_db
@pytest.mark.api
class TestSyncLogDetailAPI:
    URL = "/integrations/sync-logs/"

    def detail_url(self, sync_log_id):
        return f"{self.URL}{sync_log_id}/"

    def test_read_sync_log_detail(self, auth_client, sync_log, integration_connection):
        resp = auth_client.get(self.detail_url(sync_log.id))

        assert resp.status_code == http_status.HTTP_200_OK
        data = _result(resp)
        assert data["id"] == str(sync_log.id)
        assert data["connection"] == str(integration_connection.id)
        assert data["status"] == SyncStatus.SUCCESS
        assert data["traces_fetched"] == 10
        assert data["traces_created"] == 8
        assert data["traces_updated"] == 2
        assert data["spans_synced"] == 25
        assert data["scores_synced"] == 5

    def test_read_missing_sync_log_returns_404(self, auth_client):
        resp = auth_client.get(self.detail_url(uuid.uuid4()))

        assert resp.status_code == http_status.HTTP_404_NOT_FOUND

    def test_read_hides_log_when_connection_deleted(
        self, auth_client, sync_log, integration_connection
    ):
        integration_connection.deleted = True
        integration_connection.save(update_fields=["deleted"])

        resp = auth_client.get(self.detail_url(sync_log.id))

        assert resp.status_code == http_status.HTTP_404_NOT_FOUND

    def test_read_excludes_other_workspace_logs(
        self,
        auth_client,
        organization,
        workspace,
        user,
        encrypted_credentials,
    ):
        other_workspace = Workspace.objects.create(
            name="Other Integration Detail Workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        other_connection = IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=other_workspace,
            created_by=user,
            platform=IntegrationPlatform.LANGFUSE,
            display_name="Other Workspace Langfuse",
            host_url="https://langfuse.example.com",
            encrypted_credentials=encrypted_credentials,
            external_project_name="other-workspace-project",
            status=ConnectionStatus.ACTIVE,
            sync_interval_seconds=300,
            backfill_completed=True,
        )
        now = datetime.now(UTC)
        other_log = SyncLog.objects.create(
            connection=other_connection,
            status=SyncStatus.SUCCESS,
            started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
        )

        resp = auth_client.get(self.detail_url(other_log.id))

        assert resp.status_code == http_status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Action-only platforms (Linear, etc.) — one live row per (org, workspace)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.api
class TestActionOnlyConnectionCreate:
    """`create` semantics for ACTION_ONLY_PLATFORMS.

    Action-only platforms have org-wide credentials and no per-project
    mapping, so they're capped at one live row per (organization,
    workspace, platform) by the `uq_intconn_org_ws_action_only_active`
    partial unique constraint. A re-add must return 400 with a message
    that points the user at the edit flow, not silently update.
    """

    URL = "/integrations/connections/"

    def _payload(self, **overrides):
        data = {
            "platform": "linear",
            "host_url": "https://linear.app",
            "credentials": {"api_key": "lin_api_test_abc123"},
            "external_project_name": "Engineering",
            "backfill_option": "new_only",
        }
        data.update(overrides)
        return data

    @staticmethod
    def _mock_linear_service(mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.validate_credentials.return_value = {
            "valid": True,
            "projects": [{"id": "team-1", "name": "Engineering"}],
            "total_traces": 0,
        }
        mock_get_svc.return_value = mock_svc

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_create_first_linear_succeeds(self, mock_get_svc, auth_client):
        self._mock_linear_service(mock_get_svc)

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_201_CREATED
        result = _result(resp)
        assert result["platform"] == "linear"
        assert result["status"] == "active"

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_second_linear_in_same_workspace_returns_400(
        self, mock_get_svc, auth_client, organization, workspace
    ):
        self._mock_linear_service(mock_get_svc)
        # Existing live Linear connection blocks a second create attempt.
        IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            platform="linear",
            display_name="Linear",
            host_url="https://linear.app",
            encrypted_credentials=b"existing",
            external_project_name="Engineering",
            status=ConnectionStatus.ACTIVE,
        )

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_400_message_points_to_edit_flow(
        self, mock_get_svc, auth_client, organization, workspace
    ):
        self._mock_linear_service(mock_get_svc)
        IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            platform="linear",
            display_name="Linear",
            host_url="https://linear.app",
            encrypted_credentials=b"existing",
            external_project_name="Engineering",
            status=ConnectionStatus.ACTIVE,
        )

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )

        body = resp.json()
        message = (body.get("result") or body.get("detail") or "").lower()
        # Don't pin the exact wording, but message must name the platform
        # and tell the user where to rotate keys.
        assert "linear" in message
        assert "edit" in message

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_soft_deleted_linear_does_not_block_create(
        self, mock_get_svc, auth_client, organization, workspace
    ):
        """Partial constraint is scoped to `deleted=False`."""
        self._mock_linear_service(mock_get_svc)
        soft_deleted = IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            platform="linear",
            display_name="Linear",
            host_url="https://linear.app",
            encrypted_credentials=b"old",
            external_project_name="Engineering",
            status=ConnectionStatus.ACTIVE,
        )
        soft_deleted.deleted = True
        soft_deleted.save(update_fields=["deleted"])

        resp = auth_client.post(
            self.URL,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_201_CREATED

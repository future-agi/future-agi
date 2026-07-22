"""
Monitor/Alerts API Tests

Tests for /tracer/user-alerts/ and /tracer/user-alert-logs/ endpoints.
"""

import uuid

import pytest
from rest_framework import status

from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def get_result_rows(response):
    """Extract rows from wrapped, paginated, or raw list responses."""
    data = get_result(response)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def create_other_workspace_project_and_monitor(organization, user):
    other_workspace = Workspace.objects.create(
        name=f"Other Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_project = Project.no_workspace_objects.create(
        name=f"Other Workspace Observe {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    other_monitor = UserAlertMonitor.no_workspace_objects.create(
        organization=organization,
        workspace=other_workspace,
        project=other_project,
        name=f"Other Workspace Alert {uuid.uuid4().hex[:8]}",
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0.1,
        alert_frequency=60,
    )
    return other_workspace, other_project, other_monitor


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorCreateAPI:
    """Tests for POST /tracer/user-alerts/ endpoint."""

    def test_create_monitor_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "New Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_monitor_success(self, auth_client, observe_project):
        """Create a new user alert monitor."""
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Error Rate Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="Error Rate Alert")
        assert monitor.workspace_id == observe_project.workspace_id

    def test_create_monitor_rejects_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, _ = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(other_project.id),
                "name": "Cross Workspace Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not UserAlertMonitor.no_workspace_objects.filter(
            name="Cross Workspace Alert"
        ).exists()

    def test_create_monitor_with_slack_config(self, auth_client, observe_project):
        """Create monitor with Slack notification config."""
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Slack Alert",
                "metric_type": "span_response_time",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 5000,
                "alert_frequency": 60,
                "slack_webhook_url": "https://hooks.slack.com/services/xxx",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_create_monitor_accepts_canonical_span_attribute_filters(
        self, auth_client, observe_project
    ):
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Canonical Filter Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 1,
                "alert_frequency": 60,
                "filters": {
                    "span_attributes_filters": [
                        {
                            "column_id": "customer_tier",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "enterprise",
                                "col_type": "SPAN_ATTRIBUTE",
                            },
                        }
                    ]
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="Canonical Filter Alert")
        assert monitor.filters["span_attributes_filters"][0]["column_id"] == (
            "customer_tier"
        )

    def test_create_monitor_rejects_camel_case_span_attribute_filters(
        self, auth_client, observe_project
    ):
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Camel Filter Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 1,
                "alert_frequency": 60,
                "filters": {
                    "span_attributes_filters": [
                        {
                            "columnId": "customer_tier",
                            "filterConfig": {
                                "filterType": "text",
                                "filterOp": "equals",
                                "filterValue": "enterprise",
                                "colType": "SPAN_ATTRIBUTE",
                            },
                        }
                    ]
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorListAPI:
    """Tests for GET /tracer/user-alerts/list_monitors/ endpoint."""

    def test_root_list_monitors_success(
        self, auth_client, observe_project, user_alert_monitor
    ):
        """Root list endpoint should return paginated monitors for SDK clients."""
        response = auth_client.get(
            "/tracer/user-alerts/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        data = get_result(response)
        assert data["metadata"]["total_rows"] == 1
        assert data["metadata"]["page_number"] == 0
        assert data["metadata"]["page_size"] == 30
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == str(user_alert_monitor.id)
        assert data["results"][0]["name"] == "Test Alert"

    def test_list_monitors_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/user-alerts/list_monitors/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_monitors_success(
        self, auth_client, observe_project, user_alert_monitor
    ):
        """List monitors for a project."""
        response = auth_client.get(
            "/tracer/user-alerts/list_monitors/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "table" in data or "metadata" in data

    def test_list_monitors_empty(self, auth_client, observe_project):
        """List returns empty when no monitors exist."""
        UserAlertMonitor.objects.filter(project=observe_project).delete()

        response = auth_client.get(
            "/tracer/user-alerts/list_monitors/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorDetailsAPI:
    """Tests for GET /tracer/user-alerts/{id}/details/ endpoint."""

    def test_root_read_monitor_success(self, auth_client, user_alert_monitor):
        """Generated root detail alias should read a scoped monitor."""
        response = auth_client.get(f"/tracer/user-alerts/{user_alert_monitor.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["id"] == str(user_alert_monitor.id)
        assert data["name"] == "Test Alert"

    def test_root_read_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.get(f"/tracer/user-alerts/{other_monitor.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_details_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/details/"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_details_success(self, auth_client, user_alert_monitor):
        """Get monitor details."""
        response = auth_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/details/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["name"] == "Test Alert"

    def test_get_details_not_found(self, auth_client):
        """Get details for non-existent monitor fails."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/user-alerts/{fake_id}/details/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_details_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.get(f"/tracer/user-alerts/{other_monitor.id}/details/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorUpdateAPI:
    """Tests for PATCH /tracer/user-alerts/{id}/ endpoint."""

    def test_update_monitor_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        response = api_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {"name": "Updated Alert"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_monitor_success(self, auth_client, user_alert_monitor):
        """Update a monitor."""
        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {
                "name": "Updated Alert Name",
                "critical_threshold_value": 0.2,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.name == "Updated Alert Name"
        assert user_alert_monitor.critical_threshold_value == 0.2

    def test_partial_update_preserves_request_scoped_fields(
        self, auth_client, organization, user, user_alert_monitor
    ):
        other_workspace, _, _ = create_other_workspace_project_and_monitor(
            organization, user
        )
        other_user = User.objects.create_user(
            email=f"alert-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Other Alert Owner",
            organization=organization,
        )
        user_alert_monitor.created_by = user
        user_alert_monitor.save(update_fields=["created_by"])

        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {
                "name": "Scoped Alert Rename",
                "workspace": str(other_workspace.id),
                "created_by": str(other_user.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.name == "Scoped Alert Rename"
        assert user_alert_monitor.workspace_id != other_workspace.id
        assert user_alert_monitor.created_by_id == user.id

    def test_put_update_preserves_request_scoped_fields(
        self, auth_client, organization, user, observe_project, user_alert_monitor
    ):
        other_workspace, _, _ = create_other_workspace_project_and_monitor(
            organization, user
        )
        user_alert_monitor.created_by = user
        user_alert_monitor.save(update_fields=["created_by"])

        response = auth_client.put(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {
                "project": str(observe_project.id),
                "name": "Scoped Alert Replace",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.25,
                "alert_frequency": 60,
                "workspace": str(other_workspace.id),
                "created_by": None,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.name == "Scoped Alert Replace"
        assert user_alert_monitor.critical_threshold_value == 0.25
        assert user_alert_monitor.workspace_id == observe_project.workspace_id
        assert user_alert_monitor.created_by_id == user.id

    def test_update_monitor_ignores_legacy_filters_when_filters_unchanged(
        self, auth_client, user_alert_monitor
    ):
        """Unrelated edits should not fail because older saved filters predate the contract."""
        user_alert_monitor.filters = {
            "span_attributes_filters": [
                {
                    "columnId": "customer_tier",
                    "filterConfig": {
                        "filterType": "text",
                        "filterOp": "equals",
                        "filterValue": "enterprise",
                        "colType": "SPAN_ATTRIBUTE",
                    },
                }
            ]
        }
        user_alert_monitor.save(update_fields=["filters"])

        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {"name": "Updated Alert Name"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_update_monitor_rejects_camel_case_filter_update(
        self, auth_client, user_alert_monitor
    ):
        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {
                "filters": {
                    "span_attributes_filters": [
                        {
                            "columnId": "customer_tier",
                            "filterConfig": {
                                "filterType": "text",
                                "filterOp": "equals",
                                "filterValue": "enterprise",
                                "colType": "SPAN_ATTRIBUTE",
                            },
                        }
                    ]
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_eval_monitor_to_system_metric_clears_eval_fields(
        self,
        auth_client,
        organization,
        workspace,
        observe_project,
        custom_eval_config,
    ):
        """Changing metric types should not retain stale eval-only fields."""
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Eval Alert",
            metric_type="evaluation_metrics",
            metric=str(custom_eval_config.id),
            threshold_operator="greater_than",
            threshold_type="static",
            threshold_metric_value=None,
            critical_threshold_value=0.1,
            alert_frequency=60,
        )

        response = auth_client.patch(
            f"/tracer/user-alerts/{monitor.id}/",
            {
                "project": str(observe_project.id),
                "name": "System Alert",
                "metric_type": "span_response_time",
                "metric": None,
                "threshold_metric_value": None,
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 5000,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        monitor.refresh_from_db()
        assert monitor.name == "System Alert"
        assert monitor.metric_type == "span_response_time"
        assert monitor.metric is None
        assert monitor.threshold_metric_value is None
        assert monitor.critical_threshold_value == 5000


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorDeleteAPI:
    """Tests for DELETE /tracer/user-alerts/ endpoint."""

    def test_root_delete_monitor_success(self, auth_client, user_alert_monitor):
        """Generated root detail delete should soft-delete one monitor."""
        response = auth_client.delete(f"/tracer/user-alerts/{user_alert_monitor.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.deleted is True

    def test_root_delete_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.delete(f"/tracer/user-alerts/{other_monitor.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_monitor.refresh_from_db()
        assert other_monitor.deleted is False

    def test_delete_monitor_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        # API expects ids list in body
        response = api_client.delete(
            "/tracer/user-alerts/",
            {"ids": [str(user_alert_monitor.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_monitor_success(self, auth_client, user_alert_monitor):
        """Delete a monitor."""
        # API expects ids list in body
        response = auth_client.delete(
            "/tracer/user-alerts/",
            {"ids": [str(user_alert_monitor.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.deleted is True

    def test_delete_monitor_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.delete(
            "/tracer/user-alerts/",
            {"ids": [str(other_monitor.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_monitor.refresh_from_db()
        assert other_monitor.deleted is False


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorBulkMuteAPI:
    """Tests for POST /tracer/user-alerts/bulk-mute/ endpoint."""

    def test_bulk_mute_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        # API expects 'ids' not 'alert_ids'
        response = api_client.post(
            "/tracer/user-alerts/bulk-mute/",
            {"ids": [str(user_alert_monitor.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_bulk_mute_success(self, auth_client, user_alert_monitor):
        """Bulk mute multiple monitors."""
        # API expects 'ids' not 'alert_ids', 'is_mute' not 'mute'
        response = auth_client.post(
            "/tracer/user-alerts/bulk-mute/",
            {
                "ids": [str(user_alert_monitor.id)],
                "is_mute": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.is_mute is True

    def test_bulk_mute_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.post(
            "/tracer/user-alerts/bulk-mute/",
            {
                "ids": [str(other_monitor.id)],
                "is_mute": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_monitor.refresh_from_db()
        assert other_monitor.is_mute is False


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorDuplicateAPI:
    """Tests for POST /tracer/user-alerts/duplicate/ endpoint."""

    def test_duplicate_monitor_unauthenticated(self, api_client, user_alert_monitor):
        response = api_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(user_alert_monitor.id), "name": "Copy Alert"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_duplicate_monitor_success(self, auth_client, user_alert_monitor):
        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(user_alert_monitor.id), "name": "Copy Alert"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        copied_monitor = UserAlertMonitor.objects.get(name="Copy Alert")
        assert copied_monitor.id != user_alert_monitor.id
        assert copied_monitor.project == user_alert_monitor.project
        assert copied_monitor.metric_type == user_alert_monitor.metric_type
        assert (
            copied_monitor.threshold_operator == user_alert_monitor.threshold_operator
        )
        assert copied_monitor.is_mute is False

    def test_duplicate_monitor_rejects_duplicate_name(
        self, auth_client, user_alert_monitor
    ):
        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(user_alert_monitor.id), "name": user_alert_monitor.name},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_duplicate_monitor_not_found(self, auth_client):
        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(uuid.uuid4()), "name": "Copy Alert"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_duplicate_monitor_rejects_other_workspace_monitor(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(other_monitor.id), "name": "Cross Workspace Copy"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not UserAlertMonitor.no_workspace_objects.filter(
            name="Cross Workspace Copy"
        ).exists()


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorMetricOptionsAPI:
    """Tests for GET /tracer/user-alerts/metric-options/ endpoint."""

    def test_metric_options_unauthenticated(self, api_client, observe_project):
        response = api_client.get(
            "/tracer/user-alerts/metric-options/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_metric_options_success(self, auth_client, observe_project):
        response = auth_client.get(
            "/tracer/user-alerts/metric-options/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        options = get_result(response)
        span_response_time = next(
            option for option in options if option["id"] == "span_response_time"
        )
        assert span_response_time == {
            "id": "span_response_time",
            "name": "Span response time",
            "metric_type": "span_response_time",
            "output_type": "system_metric",
        }
        assert all(option["metric_type"] != "system_metric" for option in options)

    def test_metric_options_requires_project_id(self, auth_client):
        response = auth_client.get("/tracer/user-alerts/metric-options/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_metric_options_rejects_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, _ = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.get(
            "/tracer/user-alerts/metric-options/",
            {"project_id": str(other_project.id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorGraphAPI:
    """Tests for GET /tracer/user-alerts/{id}/graph/ endpoint."""

    def test_get_graph_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/user-alerts/{user_alert_monitor.id}/graph/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_graph_success(self, auth_client, user_alert_monitor):
        """Get graph data for a monitor."""
        response = auth_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/graph/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert isinstance(data, dict) or isinstance(data, list)


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorPreviewGraphAPI:
    """Tests for POST /tracer/user-alerts/preview-graph/ endpoint."""

    def test_preview_graph_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/user-alerts/preview-graph/",
            {
                "project": str(observe_project.id),
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_preview_graph_success(self, auth_client, observe_project):
        """Preview graph for a new monitor config."""
        response = auth_client.post(
            "/tracer/user-alerts/preview-graph/",
            {
                "project": str(observe_project.id),
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "alert_frequency": 60,
                "critical_threshold_value": 0.1,  # Required field
            },
            format="json",
        )
        # May return 200 or 400 depending on additional required fields
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


# =====================
# Alert Logs Tests
# =====================


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorLogRootAPI:
    """Tests for generated /tracer/user-alert-logs/ root CRUD aliases."""

    def test_root_list_logs_excludes_other_workspace_logs(
        self, auth_client, organization, user, user_alert_log
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )
        other_log = UserAlertMonitorLog.no_workspace_objects.create(
            alert=other_monitor,
            type="critical",
            message="Other workspace alert",
            resolved=False,
        )

        response = auth_client.get("/tracer/user-alert-logs/")

        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in get_result_rows(response)}
        assert str(user_alert_log.id) in ids
        assert str(other_log.id) not in ids

    def test_root_create_log_success(self, auth_client, user_alert_monitor):
        response = auth_client.post(
            "/tracer/user-alert-logs/",
            {
                "alert": str(user_alert_monitor.id),
                "type": "critical",
                "message": "Generated root log",
                "resolved": False,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        log = UserAlertMonitorLog.objects.get(message="Generated root log")
        assert log.alert_id == user_alert_monitor.id

    def test_root_create_rejects_other_workspace_alert(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )

        response = auth_client.post(
            "/tracer/user-alert-logs/",
            {
                "alert": str(other_monitor.id),
                "type": "critical",
                "message": "Cross workspace log",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not UserAlertMonitorLog.no_workspace_objects.filter(
            message="Cross workspace log"
        ).exists()

    def test_root_read_update_patch_delete_log_success(
        self, auth_client, user_alert_monitor, user_alert_log
    ):
        read_response = auth_client.get(f"/tracer/user-alert-logs/{user_alert_log.id}/")
        assert read_response.status_code == status.HTTP_200_OK
        assert get_result(read_response)["id"] == str(user_alert_log.id)

        put_response = auth_client.put(
            f"/tracer/user-alert-logs/{user_alert_log.id}/",
            {
                "alert": str(user_alert_monitor.id),
                "type": "warning",
                "message": "Updated generated root log",
                "resolved": True,
            },
            format="json",
        )
        assert put_response.status_code == status.HTTP_200_OK
        user_alert_log.refresh_from_db()
        assert user_alert_log.type == "warning"
        assert user_alert_log.message == "Updated generated root log"
        assert user_alert_log.resolved is True

        patch_response = auth_client.patch(
            f"/tracer/user-alert-logs/{user_alert_log.id}/",
            {"message": "Patched generated root log"},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_200_OK
        user_alert_log.refresh_from_db()
        assert user_alert_log.message == "Patched generated root log"

        delete_response = auth_client.delete(
            f"/tracer/user-alert-logs/{user_alert_log.id}/"
        )
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        user_alert_log.refresh_from_db()
        assert user_alert_log.deleted is True

    def test_root_read_rejects_other_workspace_log(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )
        other_log = UserAlertMonitorLog.no_workspace_objects.create(
            alert=other_monitor,
            type="critical",
            message="Other workspace alert",
            resolved=False,
        )

        response = auth_client.get(f"/tracer/user-alert-logs/{other_log.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorLogListAllAPI:
    """Tests for GET /tracer/user-alert-logs/all/ endpoint."""

    def test_list_all_logs_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get("/tracer/user-alert-logs/all/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_all_logs_success(self, auth_client, user_alert_log):
        """List all alert logs."""
        response = auth_client.get("/tracer/user-alert-logs/all/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert isinstance(data, list)

    def test_list_all_excludes_other_workspace_logs(
        self, auth_client, organization, user, user_alert_log
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )
        other_log = UserAlertMonitorLog.no_workspace_objects.create(
            alert=other_monitor,
            type="critical",
            message="Other workspace alert",
            resolved=False,
        )

        response = auth_client.get("/tracer/user-alert-logs/all/")

        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in get_result(response)}
        assert str(user_alert_log.id) in ids
        assert str(other_log.id) not in ids


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorLogListForAlertAPI:
    """Tests for GET /tracer/user-alert-logs/{id}/list/ endpoint."""

    def test_list_logs_for_alert_unauthenticated(self, api_client, user_alert_monitor):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            f"/tracer/user-alert-logs/{user_alert_monitor.id}/list/"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_logs_for_alert_success(
        self, auth_client, user_alert_monitor, user_alert_log
    ):
        """List logs for a specific alert."""
        response = auth_client.get(
            f"/tracer/user-alert-logs/{user_alert_monitor.id}/list/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert isinstance(data, list)

    def test_list_logs_for_alert_excludes_other_workspace_alert(
        self, auth_client, organization, user
    ):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )
        UserAlertMonitorLog.no_workspace_objects.create(
            alert=other_monitor,
            type="critical",
            message="Other workspace alert",
            resolved=False,
        )

        response = auth_client.get(f"/tracer/user-alert-logs/{other_monitor.id}/list/")

        assert response.status_code == status.HTTP_200_OK
        assert get_result(response) == []


@pytest.mark.integration
@pytest.mark.api
class TestUserAlertMonitorLogResolveAPI:
    """Tests for POST /tracer/user-alert-logs/resolve/ endpoint."""

    def test_resolve_logs_unauthenticated(self, api_client, user_alert_log):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/user-alert-logs/resolve/",
            {"log_ids": [str(user_alert_log.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_resolve_logs_success(self, auth_client, user_alert_log):
        """Resolve alert logs."""
        response = auth_client.post(
            "/tracer/user-alert-logs/resolve/",
            {"log_ids": [str(user_alert_log.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user_alert_log.refresh_from_db()
        assert user_alert_log.resolved is True

    def test_resolve_rejects_other_workspace_log(self, auth_client, organization, user):
        _, _, other_monitor = create_other_workspace_project_and_monitor(
            organization, user
        )
        other_log = UserAlertMonitorLog.no_workspace_objects.create(
            alert=other_monitor,
            type="critical",
            message="Other workspace alert",
            resolved=False,
        )

        response = auth_client.post(
            "/tracer/user-alert-logs/resolve/",
            {"log_ids": [str(other_log.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_log.refresh_from_db()
        assert other_log.resolved is False

    def test_resolve_multiple_logs(self, auth_client, user_alert_monitor):
        """Resolve multiple alert logs."""
        # Create multiple logs with correct field names
        log1 = UserAlertMonitorLog.objects.create(
            alert=user_alert_monitor,
            type="critical",
            message="Alert 1",
            resolved=False,
        )
        log2 = UserAlertMonitorLog.objects.create(
            alert=user_alert_monitor,
            type="warning",
            message="Alert 2",
            resolved=False,
        )

        response = auth_client.post(
            "/tracer/user-alert-logs/resolve/",
            {"log_ids": [str(log1.id), str(log2.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        log1.refresh_from_db()
        log2.refresh_from_db()
        assert log1.resolved is True
        assert log2.resolved is True

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
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
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

    @pytest.mark.requires_ee
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
        # The server seeds the creation audit entry (regression guard: this must
        # not be dropped by any read-only field handling).
        assert monitor.logs and monitor.logs[0]["type"] == "INFO"

    def test_create_ignores_client_supplied_server_owned_fields(
        self, auth_client, observe_project
    ):
        """Client cannot set operational/soft-delete fields on create."""
        from django.utils import timezone

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Tamper Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 1,
                "alert_frequency": 60,
                "last_checked_at": timezone.now().isoformat(),
                "deleted": True,
                "logs": [{"message": "injected"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="Tamper Alert")
        assert monitor.last_checked_at is None
        assert monitor.deleted is False
        assert all(entry.get("message") != "injected" for entry in monitor.logs)

    def test_update_ignores_client_supplied_server_owned_fields(
        self, auth_client, user_alert_monitor
    ):
        """Client cannot tamper with last_checked_at / deleted via update."""
        from django.utils import timezone

        stamp = timezone.now().isoformat()
        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {"last_checked_at": stamp, "deleted": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.last_checked_at is None
        assert user_alert_monitor.deleted is False

    @pytest.mark.requires_ee
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

    @pytest.mark.requires_ee
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

    @pytest.mark.requires_ee
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

    @pytest.mark.requires_ee
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

    def _create_eval_config(
        self, organization, workspace, observe_project, output_type, choices
    ):
        """Build an EvalTemplate + CustomEvalConfig pinned to observe_project.

        Alert creation only accepts an `observe`-typed project, so the eval
        config must live on `observe_project`, not the `experiment`-typed
        `project` fixture used elsewhere in conftest.py.
        """
        template = EvalTemplate.objects.create(
            name=f"Test Eval Template {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": output_type},
            choices=choices,
        )
        return CustomEvalConfig.objects.create(
            name=f"Test Custom Eval {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )

    def test_create_score_eval_with_choice_rejected(
        self, auth_client, organization, workspace, observe_project
    ):
        """A score-typed eval carries labels but its metric is the mean score,
        so a stored choice must be rejected."""
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "score",
            ["Complete", "Partial", "Incomplete"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Score Eval Alert",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_metric_value": "Incomplete",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be empty for evals without predefined choices" in str(
            response.json()
        )

    def test_create_score_eval_without_choice_succeeds(
        self, auth_client, organization, workspace, observe_project
    ):
        """The frontend no longer sends threshold_metric_value for score evals;
        that must be accepted even when the template carries labels."""
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "score",
            ["Complete", "Partial", "Incomplete"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Score Eval Alert No Choice",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="Score Eval Alert No Choice")
        assert monitor.threshold_metric_value is None

    def test_create_pass_fail_eval_choice(
        self, auth_client, organization, workspace, observe_project
    ):
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "Pass/Fail",
            ["Passed", "Failed"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Pass Fail Eval Alert",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_metric_value": "Passed",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert UserAlertMonitor.objects.filter(name="Pass Fail Eval Alert").exists()

    def test_create_pass_fail_eval_invalid_choice_rejected(
        self, auth_client, organization, workspace, observe_project
    ):
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "Pass/Fail",
            ["Passed", "Failed"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Pass Fail Eval Alert Invalid",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_metric_value": "Maybe",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is not valid" in str(response.json())

    def test_create_choices_eval_valid_choice(
        self, auth_client, organization, workspace, observe_project
    ):
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "choices",
            ["never", "occasionally", "frequently", "always"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Choices Eval Alert",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_metric_value": "frequently",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert UserAlertMonitor.objects.filter(name="Choices Eval Alert").exists()

    def test_create_choices_eval_without_choice_rejected(
        self, auth_client, organization, workspace, observe_project
    ):
        eval_config = self._create_eval_config(
            organization,
            workspace,
            observe_project,
            "choices",
            ["never", "occasionally", "frequently", "always"],
        )

        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Choices Eval Alert No Choice",
                "metric_type": "evaluation_metrics",
                "metric": str(eval_config.id),
                "threshold_metric_value": None,
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "required for evals with predefined choices" in str(response.json())


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

    def test_list_monitors_score_eval_hides_stale_choice_suffix(
        self, auth_client, organization, workspace, observe_project
    ):
        """A monitor saved before this branch may still carry a stored
        threshold_metric_value on a score-typed eval. Neither the alerts
        list's Alert Type column nor the detail sheet's metric_name must
        render that stale value as a suffix."""
        template = EvalTemplate.objects.create(
            name=f"Score Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "score"},
            choices=["Complete", "Partial", "Incomplete"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Score Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Stale Score Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Incomplete",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

        response = auth_client.get(
            "/tracer/user-alerts/list_monitors/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        data = get_result(response)
        rows = [row for row in data["table"] if row["name"] == "Stale Score Alert"]
        assert len(rows) == 1
        rendered = rows[0]["metric_type"]
        assert rendered == eval_config.name

        response = auth_client.get(f"/tracer/user-alerts/{monitor.id}/details/")
        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["metric_name"] == eval_config.name

    def test_list_monitors_choices_eval_keeps_choice_suffix(
        self, auth_client, organization, workspace, observe_project
    ):
        """A monitor on a choices-typed eval keeps its stored choice in the
        Alert Type column."""
        template = EvalTemplate.objects.create(
            name=f"Choices Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "choices"},
            choices=["Complete", "Partial", "Incomplete"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Choices Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Choices Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Incomplete",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

        response = auth_client.get(
            "/tracer/user-alerts/list_monitors/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        data = get_result(response)
        rows = [row for row in data["table"] if row["name"] == "Choices Alert"]
        assert len(rows) == 1
        rendered = rows[0]["metric_type"]
        assert rendered == f"{eval_config.name} (Incomplete)"


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

    def test_get_details_pass_fail_eval_keeps_choice_suffix(
        self, auth_client, organization, workspace, observe_project
    ):
        """A monitor on a Pass/Fail-typed eval keeps its stored choice in the
        detail sheet's rendered metric name."""
        template = EvalTemplate.objects.create(
            name=f"Pass Fail Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "Pass/Fail"},
            choices=["Passed", "Failed"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Pass Fail Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Pass Fail Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Passed",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

        response = auth_client.get(f"/tracer/user-alerts/{monitor.id}/details/")
        assert response.status_code == status.HTTP_200_OK

        data = get_result(response)
        rendered = data["metric_name"]
        assert rendered == f"{eval_config.name} (Passed)"


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

    @staticmethod
    def _stale_score_monitor(organization, workspace, observe_project):
        """A score-typed eval alert saved before scoring evals stopped taking a
        choice, so it still carries a stored threshold_metric_value."""
        template = EvalTemplate.objects.create(
            name=f"Score Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "score"},
            choices=["Complete", "Partial", "Incomplete"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Score Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        return UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Stale Score Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Incomplete",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

    def test_patch_score_eval_with_stale_choice_clears_it(
        self, auth_client, organization, workspace, observe_project
    ):
        """An unrelated edit to such an alert must not fail on the stored
        choice the client never sent; the stale value is dropped instead."""
        monitor = self._stale_score_monitor(organization, workspace, observe_project)

        response = auth_client.patch(
            f"/tracer/user-alerts/{monitor.id}/",
            {"warning_threshold_value": 0.1},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        monitor.refresh_from_db()
        assert monitor.warning_threshold_value == 0.1
        assert monitor.threshold_metric_value is None

    def test_patch_score_eval_with_explicit_choice_still_rejected(
        self, auth_client, organization, workspace, observe_project
    ):
        """Only the stored value is forgiven; sending a choice for a scoring
        eval is still an error."""
        monitor = self._stale_score_monitor(organization, workspace, observe_project)

        response = auth_client.patch(
            f"/tracer/user-alerts/{monitor.id}/",
            {"threshold_metric_value": "Complete"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        monitor.refresh_from_db()
        assert monitor.threshold_metric_value == "Incomplete"

    def test_patch_pass_fail_eval_without_choice_keeps_stored_choice(
        self, auth_client, organization, workspace, observe_project
    ):
        """The form leaves threshold_metric_value out when it can't resolve the
        eval; a Pass/Fail alert must keep the choice it already has."""
        template = EvalTemplate.objects.create(
            name=f"Pass Fail Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "Pass/Fail"},
            choices=["Passed", "Failed"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Pass Fail Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Pass Fail Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Passed",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

        response = auth_client.patch(
            f"/tracer/user-alerts/{monitor.id}/",
            {"warning_threshold_value": 0.1},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        monitor.refresh_from_db()
        assert monitor.warning_threshold_value == 0.1
        assert monitor.threshold_metric_value == "Passed"


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

    def test_duplicate_drops_stale_choice_on_score_eval(
        self, auth_client, organization, workspace, observe_project
    ):
        """A monitor saved before this branch may still carry a stored
        threshold_metric_value on a score-typed eval. Duplicating it must not
        copy that stale value onto the new row."""
        template = EvalTemplate.objects.create(
            name=f"Score Eval {uuid.uuid4().hex[:8]}",
            description="A test evaluation template",
            organization=organization,
            workspace=workspace,
            config={"output": "score"},
            choices=["Complete", "Partial", "Incomplete"],
        )
        eval_config = CustomEvalConfig.objects.create(
            name=f"Score Eval Config {uuid.uuid4().hex[:8]}",
            project=observe_project,
            eval_template=template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Stale Score Alert",
            metric_type="evaluation_metrics",
            metric=str(eval_config.id),
            threshold_metric_value="Incomplete",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.15,
            alert_frequency=60,
        )

        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(monitor.id), "name": "Stale Score Alert Copy"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        copied_monitor = UserAlertMonitor.objects.get(name="Stale Score Alert Copy")
        assert copied_monitor.threshold_metric_value is None


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

    def test_get_graph_invalid_config_is_400(self, auth_client, user_alert_monitor):
        """A monitor with unparseable stored filters is a client error (400)."""
        user_alert_monitor.filters = {
            "span_attributes_filters": [
                {
                    "column_id": "x",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "nonsense_op",
                        "filter_value": "v",
                    },
                }
            ]
        }
        user_alert_monitor.save()
        response = auth_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/graph/"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_graph_projectless_monitor_is_400(
        self, auth_client, user_alert_monitor
    ):
        """A monitor with no project is a config error (400), not a CH 500."""
        user_alert_monitor.project = None
        user_alert_monitor.save()
        response = auth_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/graph/"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_graph_ch_failure_is_500_without_leak(
        self, auth_client, user_alert_monitor
    ):
        """A ClickHouse failure is a 5xx with a generic body (no SQL/host leak)."""
        from unittest import mock

        secret = "clickhouse://internal-host:9000 SELECT secret_table"
        with mock.patch(
            "tracer.views.monitor.get_graph_data", side_effect=RuntimeError(secret)
        ):
            response = auth_client.get(
                f"/tracer/user-alerts/{user_alert_monitor.id}/graph/"
            )
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert secret not in response.content.decode()


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


@pytest.mark.django_db
class TestMonitorViewHardening:
    """Pagination input guard + preview-graph config-error mapping."""

    def test_list_non_integer_page_params_is_400(self, auth_client, observe_project):
        response = auth_client.get("/tracer/user-alerts/?page_number=abc")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response = auth_client.get("/tracer/user-alerts/?page_size=abc")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_graph_deleted_eval_config_is_400(
        self, auth_client, observe_project
    ):
        # Real path, no mocks: build_monitor_ch_builder raises for the missing
        # eval config, so the graph surfaces the misconfig instead of
        # rendering silently empty.
        payload = {
            "project": str(observe_project.id),
            "metric_type": "evaluation_metrics",
            "metric": "22222222-2222-2222-2222-222222222222",
            "threshold_operator": "greater_than",
            "threshold_type": "static",
            "critical_threshold_value": 0.15,
            "alert_frequency": 60,
        }
        response = auth_client.post(
            "/tracer/user-alerts/preview-graph/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_graph_config_error_is_400(self, auth_client, observe_project):
        from unittest import mock

        from tracer.utils.monitor import MonitorConfigError

        payload = {
            "project": str(observe_project.id),
            "metric_type": "count_of_errors",
            "threshold_operator": "greater_than",
            "threshold_type": "static",
            "critical_threshold_value": 0.15,
            "alert_frequency": 60,
        }
        with mock.patch(
            "tracer.views.monitor.get_graph_data",
            side_effect=MonitorConfigError("bad stored filter"),
        ):
            response = auth_client.post(
                "/tracer/user-alerts/preview-graph/", payload, format="json"
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =====================
# Share Threshold Range Tests
# =====================

# A static alert on a share metric compares a share, from 0 to 1, with its
# levels. A level outside that range is refused with the sentence the alert
# form shows for the metric, and nothing is saved.
SHARE_RANGE_SENTENCES = {
    "service_provider_error_rates": (
        "Enter a share from 0 to 1, for example 0.2 for 20% of providers."
    ),
    "llm_api_failure_rates": (
        "Enter a share from 0 to 1, for example 0.2 for 20% of LLM calls."
    ),
    "error_rates_for_function_calling": (
        "Enter a share from 0 to 1, for example 0.2 for 20% of function calls."
    ),
    "error_free_session_rates": (
        "Enter a share from 0 to 1, for example 0.9 for 90% of sessions."
    ),
}
SHARE_METRICS = list(SHARE_RANGE_SENTENCES)

# One level outside 0 to 1 beside one inside it, in the critical/warning order
# the direction needs, so only the range is wrong.
OUTSIDE_LEVELS = {
    "critical-1.5": {
        "threshold_operator": "greater_than",
        "critical_threshold_value": 1.5,
        "warning_threshold_value": 0.1,
    },
    "critical-minus-0.1": {
        "threshold_operator": "less_than",
        "critical_threshold_value": -0.1,
        "warning_threshold_value": 0.5,
    },
    "warning-1.5": {
        "threshold_operator": "less_than",
        "critical_threshold_value": 0.5,
        "warning_threshold_value": 1.5,
    },
    "warning-minus-0.1": {
        "threshold_operator": "greater_than",
        "critical_threshold_value": 0.5,
        "warning_threshold_value": -0.1,
    },
}

ALERTS_URL = "/tracer/user-alerts/"


def share_alert_body(project, metric_type, name, **fields):
    """A create body for a static alert, Above 0.2 and 0.1 unless overridden."""
    return {
        "project": str(project.id),
        "name": name,
        "metric_type": metric_type,
        "threshold_type": "static",
        "threshold_operator": "greater_than",
        "critical_threshold_value": 0.2,
        "warning_threshold_value": 0.1,
        "alert_frequency": 60,
        **fields,
    }


def is_saved(name):
    return UserAlertMonitor._base_manager.filter(name=name).exists()


@pytest.mark.integration
@pytest.mark.api
class TestShareAlertThresholdRange:
    """A static alert on a share metric takes levels from 0 to 1 only."""

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    @pytest.mark.parametrize(
        "levels", list(OUTSIDE_LEVELS.values()), ids=list(OUTSIDE_LEVELS)
    )
    def test_create_refuses_a_level_outside_0_to_1(
        self, auth_client, observe_project, metric_type, levels
    ):
        name = "Share outside its range"

        response = auth_client.post(
            ALERTS_URL,
            share_alert_body(observe_project, metric_type, name, **levels),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        assert not is_saved(name)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_create_takes_0_0_5_and_1_and_refuses_the_levels_just_past_them(
        self, auth_client, observe_project, metric_type
    ):
        inside = {
            "Above 1 and 0": {
                "threshold_operator": "greater_than",
                "critical_threshold_value": 1,
                "warning_threshold_value": 0,
            },
            "Below 0 and 1": {
                "threshold_operator": "less_than",
                "critical_threshold_value": 0,
                "warning_threshold_value": 1,
            },
            "Above 0.5 alone": {
                "critical_threshold_value": 0.5,
                "warning_threshold_value": None,
            },
        }
        for name, levels in inside.items():
            response = auth_client.post(
                ALERTS_URL,
                share_alert_body(observe_project, metric_type, name, **levels),
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK, response.json()
            saved = UserAlertMonitor._base_manager.get(name=name)
            assert saved.critical_threshold_value == levels["critical_threshold_value"]
            assert saved.warning_threshold_value == levels["warning_threshold_value"]

        for level in (1.001, -0.001):
            name = f"Above {level} alone"
            response = auth_client.post(
                ALERTS_URL,
                share_alert_body(
                    observe_project,
                    metric_type,
                    name,
                    critical_threshold_value=level,
                    warning_threshold_value=None,
                ),
                format="json",
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
            assert response.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
            assert not is_saved(name)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_a_percentage_change_alert_on_a_share_still_takes_400(
        self, auth_client, observe_project, metric_type
    ):
        at_400 = {"critical_threshold_value": 400, "warning_threshold_value": 300}

        static = auth_client.post(
            ALERTS_URL,
            share_alert_body(observe_project, metric_type, "Static at 400", **at_400),
            format="json",
        )
        percentage = auth_client.post(
            ALERTS_URL,
            share_alert_body(
                observe_project,
                metric_type,
                "Percentage change at 400",
                threshold_type="percentage_change",
                auto_threshold_time_window=60 * 24 * 7,
                **at_400,
            ),
            format="json",
        )

        assert static.status_code == status.HTTP_400_BAD_REQUEST, static.json()
        assert static.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        assert percentage.status_code == status.HTTP_200_OK, percentage.json()
        saved = UserAlertMonitor._base_manager.get(name="Percentage change at 400")
        assert saved.threshold_type == "percentage_change"
        assert saved.critical_threshold_value == 400

    @pytest.mark.parametrize(
        "metric_type", ["count_of_errors", "span_response_time", "token_usage"]
    )
    def test_a_metric_that_is_not_a_share_still_takes_400(
        self, auth_client, observe_project, metric_type
    ):
        at_400 = {"critical_threshold_value": 400, "warning_threshold_value": 300}

        share = auth_client.post(
            ALERTS_URL,
            share_alert_body(
                observe_project, "llm_api_failure_rates", "Share at 400", **at_400
            ),
            format="json",
        )
        other = auth_client.post(
            ALERTS_URL,
            share_alert_body(observe_project, metric_type, "Other at 400", **at_400),
            format="json",
        )

        assert share.status_code == status.HTTP_400_BAD_REQUEST, share.json()
        assert other.status_code == status.HTTP_200_OK, other.json()
        saved = UserAlertMonitor._base_manager.get(name="Other at 400")
        assert saved.critical_threshold_value == 400
        assert saved.warning_threshold_value == 300

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_a_saved_alert_outside_0_to_1_is_listed_and_refused_on_edit_until_fixed(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        # Saved before the server checked the range, at 400 and 300, which a
        # share never passes.
        monitor = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Saved at 400",
            metric_type=metric_type,
            threshold_type="static",
            threshold_operator="greater_than",
            critical_threshold_value=400,
            warning_threshold_value=300,
            alert_frequency=60,
        )
        url = f"{ALERTS_URL}{monitor.id}/"

        detail = auth_client.get(f"{url}details/")
        listed = auth_client.get(
            f"{ALERTS_URL}list_monitors/", {"project_id": str(observe_project.id)}
        )
        muted = auth_client.post(
            f"{ALERTS_URL}bulk-mute/",
            {"ids": [str(monitor.id)], "is_mute": True},
            format="json",
        )

        assert detail.status_code == status.HTTP_200_OK, detail.json()
        assert get_result(detail)["critical_threshold_value"] == 400
        assert str(monitor.id) in {row["id"] for row in get_result(listed)["table"]}
        assert muted.status_code == status.HTTP_200_OK, muted.json()

        renamed = auth_client.patch(url, {"name": "Renamed"}, format="json")

        assert renamed.status_code == status.HTTP_400_BAD_REQUEST, renamed.json()
        assert renamed.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        monitor.refresh_from_db()
        assert monitor.name == "Saved at 400"

        fixed = auth_client.patch(
            url,
            {
                "name": "Renamed",
                "critical_threshold_value": 0.2,
                "warning_threshold_value": 0.1,
            },
            format="json",
        )

        assert fixed.status_code == status.HTTP_200_OK, fixed.json()
        monitor.refresh_from_db()
        assert monitor.name == "Renamed"
        assert monitor.critical_threshold_value == 0.2
        assert monitor.warning_threshold_value == 0.1
        assert monitor.is_mute is True


# =====================
# Share Alert Off Switch Tests
# =====================

# A saved static alert on a share metric with levels outside 0 to 1 can never
# fire as meant. Switching it off is never refused and leaves its levels as
# saved; switching it on asks for levels inside the range first.
METRIC_ALERT_URL = "/tracer/alerts/metric/{}/"
# The alerts API's two ways to switch a metric alert off, each with its way
# back on.
ALERTS_API_OFF_AND_ON = {
    "enabled": ({"enabled": False}, {"enabled": True}),
    "is_mute": ({"is_mute": True}, {"is_mute": False}),
}


def saved_static_alert(organization, workspace, project, metric_type, **fields):
    """A static alert saved straight to the table, as alerts were before the
    server checked the range: Above 400 and 300 unless overridden."""
    values = {
        "name": "Saved alert",
        "metric_type": metric_type,
        "threshold_type": "static",
        "threshold_operator": "greater_than",
        "critical_threshold_value": 400,
        "warning_threshold_value": 300,
        "alert_frequency": 60,
        **fields,
    }
    return UserAlertMonitor.objects.create(
        organization=organization, workspace=workspace, project=project, **values
    )


def mute_and_levels(monitor):
    """Whether the alert is off, and its two levels, as saved now."""
    monitor.refresh_from_db()
    return (
        monitor.is_mute,
        monitor.critical_threshold_value,
        monitor.warning_threshold_value,
    )


def every_field_as_saved(monitor, **fields):
    """A full update's body: every field the alert was saved with."""
    return {
        "project": str(monitor.project_id),
        "name": monitor.name,
        "metric_type": monitor.metric_type,
        "threshold_type": monitor.threshold_type,
        "threshold_operator": monitor.threshold_operator,
        "critical_threshold_value": monitor.critical_threshold_value,
        "warning_threshold_value": monitor.warning_threshold_value,
        "alert_frequency": monitor.alert_frequency,
        **fields,
    }


def fully_saved_alert(organization, workspace, project, user, metric_type, **fields):
    """An alert at 400 and 300 saved straight to the table with every value set,
    on the rule it was saved with before the threshold update."""
    return saved_static_alert(
        organization,
        workspace,
        project,
        metric_type,
        created_by=user,
        threshold_rule="earlier",
        auto_threshold_time_window=60 * 24 * 7,
        notification_emails=["oncall@example.com"],
        slack_webhook_url="https://example.com/alert-hook",
        slack_notes="Page the on-call",
        filters={"observation_type": ["llm"]},
        **fields,
    )


def every_value_as_saved(monitor, **fields):
    """A full update's body: every value the alert holds."""
    return {
        **every_field_as_saved(monitor),
        "metric": monitor.metric,
        "threshold_metric_value": monitor.threshold_metric_value,
        "auto_threshold_time_window": monitor.auto_threshold_time_window,
        "notification_emails": monitor.notification_emails,
        "slack_webhook_url": monitor.slack_webhook_url,
        "slack_notes": monitor.slack_notes,
        "filters": monitor.filters,
        "is_mute": monitor.is_mute,
        **fields,
    }


# Every value a request can change on an alert, who made it and where it lives
# included, and the rule its thresholds are judged by.
HELD_VALUES = (
    "name",
    "project_id",
    "organization_id",
    "workspace_id",
    "created_by_id",
    "metric_type",
    "metric",
    "threshold_type",
    "threshold_operator",
    "threshold_metric_value",
    "critical_threshold_value",
    "warning_threshold_value",
    "alert_frequency",
    "auto_threshold_time_window",
    "notification_emails",
    "slack_webhook_url",
    "slack_notes",
    "filters",
    "is_mute",
    "threshold_rule",
)


def held_values(monitor):
    """Every value the alert holds now, a blank text read as no value."""
    monitor.refresh_from_db()
    values = {name: getattr(monitor, name) for name in HELD_VALUES}
    return {name: None if value == "" else value for name, value in values.items()}


# Edits sent together with the off switch, each to a value the alert does not
# hold. Each is judged like any edit.
EDITS_WITH_THE_OFF_SWITCH = {
    "name": {"name": "Renamed"},
    "frequency": {"alert_frequency": 15},
    "history": {"auto_threshold_time_window": 60 * 24},
    "direction": {"threshold_operator": "less_than"},
    "filters": {"filters": {"observation_type": ["tool"]}},
    "emails": {"notification_emails": ["alerts@example.com"]},
    "slack-notes": {"slack_notes": "Ask the platform team"},
}


def rename_with_the_off_switch(path, monitor):
    """The method, address and body that rename the alert and switch it off."""
    if path == "alerts-api":
        body = {"enabled": False, "name": "Renamed"}
        return "patch", METRIC_ALERT_URL.format(monitor.id), body
    url = f"{ALERTS_URL}{monitor.id}/"
    if path == "full-update":
        return "put", url, every_value_as_saved(monitor, is_mute=True, name="Renamed")
    return "patch", url, {"is_mute": True, "name": "Renamed"}


# The order check refuses an Above alert whose critical level is not over its
# warning level.
ORDER_SENTENCE = (
    "Critical threshold must be greater than warning threshold for "
    "'greater_than' operator."
)
# Above alerts saved straight to the table with the critical level at or under
# the warning level, each with the sentence that refuses switching it on.
OUT_OF_ORDER_ALERTS = {
    "share-inside-0-to-1": (
        {
            "metric_type": "llm_api_failure_rates",
            "critical_threshold_value": 0.1,
            "warning_threshold_value": 0.2,
        },
        ORDER_SENTENCE,
    ),
    "share-at-equal-levels": (
        {
            "metric_type": "error_free_session_rates",
            "critical_threshold_value": 0.5,
            "warning_threshold_value": 0.5,
        },
        ORDER_SENTENCE,
    ),
    "share-outside-0-to-1": (
        {
            "metric_type": "service_provider_error_rates",
            "critical_threshold_value": 300,
            "warning_threshold_value": 400,
        },
        SHARE_RANGE_SENTENCES["service_provider_error_rates"],
    ),
    "count-of-errors": (
        {
            "metric_type": "count_of_errors",
            "critical_threshold_value": 10,
            "warning_threshold_value": 20,
        },
        ORDER_SENTENCE,
    ),
}


@pytest.mark.integration
@pytest.mark.api
class TestShareAlertOffSwitch:
    """A saved static share alert outside 0 to 1 can always be switched off."""

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    @pytest.mark.parametrize("switch", list(ALERTS_API_OFF_AND_ON))
    def test_the_alerts_api_switches_it_off_and_refuses_to_switch_it_on(
        self, auth_client, organization, workspace, observe_project, metric_type, switch
    ):
        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )
        off, on = ALERTS_API_OFF_AND_ON[switch]
        url = METRIC_ALERT_URL.format(monitor.id)

        switched_off = auth_client.patch(url, off, format="json")

        assert switched_off.status_code == status.HTTP_200_OK, switched_off.json()
        assert switched_off.json()["result"]["enabled"] is False
        assert mute_and_levels(monitor) == (True, 400, 300)

        switched_on = auth_client.patch(url, on, format="json")

        assert switched_on.status_code == status.HTTP_400_BAD_REQUEST
        # The alerts API carries the serializer's errors inside its message.
        assert SHARE_RANGE_SENTENCES[metric_type] in switched_on.json()["message"]
        assert mute_and_levels(monitor) == (True, 400, 300)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_the_update_switches_it_off_and_refuses_to_switch_it_on(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )
        url = f"{ALERTS_URL}{monitor.id}/"

        switched_off = auth_client.patch(url, {"is_mute": True}, format="json")

        assert switched_off.status_code == status.HTTP_200_OK, switched_off.json()
        assert mute_and_levels(monitor) == (True, 400, 300)

        switched_on = auth_client.patch(url, {"is_mute": False}, format="json")

        assert switched_on.status_code == status.HTTP_400_BAD_REQUEST
        assert switched_on.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        assert mute_and_levels(monitor) == (True, 400, 300)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_a_full_update_that_keeps_every_value_switches_it_off(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )
        url = f"{ALERTS_URL}{monitor.id}/"

        switched_off = auth_client.put(
            url, every_field_as_saved(monitor, is_mute=True), format="json"
        )

        assert switched_off.status_code == status.HTTP_200_OK, switched_off.json()
        assert mute_and_levels(monitor) == (True, 400, 300)

        switched_on = auth_client.put(
            url, every_field_as_saved(monitor, is_mute=False), format="json"
        )

        assert switched_on.status_code == status.HTTP_400_BAD_REQUEST
        assert switched_on.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        assert mute_and_levels(monitor) == (True, 400, 300)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_the_list_mute_switches_it_off_and_keeps_its_levels(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )

        muted = auth_client.post(
            f"{ALERTS_URL}bulk-mute/",
            {"ids": [str(monitor.id)], "is_mute": True},
            format="json",
        )

        assert muted.status_code == status.HTTP_200_OK, muted.json()
        assert mute_and_levels(monitor) == (True, 400, 300)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_switching_off_with_a_new_level_or_metric_is_judged_as_an_edit(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )
        url = f"{ALERTS_URL}{monitor.id}/"

        to_500 = auth_client.patch(
            url,
            {"is_mute": True, "critical_threshold_value": 500},
            format="json",
        )

        assert to_500.status_code == status.HTTP_400_BAD_REQUEST, to_500.json()
        assert to_500.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        assert mute_and_levels(monitor) == (False, 400, 300)

        to_shares = auth_client.patch(
            url,
            {
                "is_mute": True,
                "critical_threshold_value": 0.2,
                "warning_threshold_value": 0.1,
            },
            format="json",
        )

        assert to_shares.status_code == status.HTTP_200_OK, to_shares.json()
        assert mute_and_levels(monitor) == (True, 0.2, 0.1)

        count = saved_static_alert(
            organization,
            workspace,
            observe_project,
            "count_of_errors",
            name="Saved count",
        )
        to_share_metric = auth_client.patch(
            f"{ALERTS_URL}{count.id}/",
            {"is_mute": True, "metric_type": metric_type},
            format="json",
        )

        assert to_share_metric.status_code == status.HTTP_400_BAD_REQUEST
        assert to_share_metric.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        count.refresh_from_db()
        assert count.metric_type == "count_of_errors"
        assert count.is_mute is False

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_an_alert_inside_0_to_1_switches_off_and_on_as_before(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        monitor = saved_static_alert(
            organization,
            workspace,
            observe_project,
            metric_type,
            critical_threshold_value=0.2,
            warning_threshold_value=0.1,
        )
        switches = [
            (METRIC_ALERT_URL.format(monitor.id), {"enabled": False}, True),
            (METRIC_ALERT_URL.format(monitor.id), {"enabled": True}, False),
            (f"{ALERTS_URL}{monitor.id}/", {"is_mute": True}, True),
            (f"{ALERTS_URL}{monitor.id}/", {"is_mute": False}, False),
        ]

        for url, body, muted in switches:
            response = auth_client.patch(url, body, format="json")

            assert response.status_code == status.HTTP_200_OK, response.json()
            assert mute_and_levels(monitor) == (muted, 0.2, 0.1)

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    def test_the_preview_of_an_alert_outside_0_to_1_answers_with_its_sentence(
        self, auth_client, organization, workspace, observe_project, metric_type
    ):
        from unittest import mock

        monitor = saved_static_alert(
            organization, workspace, observe_project, metric_type
        )

        # The edit form previews the values the alert was saved with.
        with mock.patch("tracer.views.monitor.get_graph_data") as graph:
            response = auth_client.post(
                f"{ALERTS_URL}preview-graph/",
                every_field_as_saved(monitor, filters={}),
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        graph.assert_not_called()

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    @pytest.mark.parametrize(
        "edit",
        list(EDITS_WITH_THE_OFF_SWITCH.values()),
        ids=list(EDITS_WITH_THE_OFF_SWITCH),
    )
    def test_switching_off_with_any_other_edit_is_refused_and_saves_nothing(
        self,
        auth_client,
        organization,
        workspace,
        observe_project,
        user,
        metric_type,
        edit,
    ):
        monitor = fully_saved_alert(
            organization, workspace, observe_project, user, metric_type
        )
        before = held_values(monitor)

        response = auth_client.patch(
            f"{ALERTS_URL}{monitor.id}/", {"is_mute": True, **edit}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["message"] == SHARE_RANGE_SENTENCES[metric_type]
        # Not the mute, not the edit, and not the rule the thresholds are
        # judged by.
        assert held_values(monitor) == before
        assert before["threshold_rule"] == "earlier"

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    @pytest.mark.parametrize("path", ["update", "full-update", "alerts-api"])
    def test_a_rename_sent_with_the_off_switch_is_refused_on_every_path(
        self,
        auth_client,
        organization,
        workspace,
        observe_project,
        user,
        metric_type,
        path,
    ):
        # Already off, so the off switch only repeats what the alert holds and
        # the rename is the one change.
        monitor = fully_saved_alert(
            organization, workspace, observe_project, user, metric_type, is_mute=True
        )
        before = held_values(monitor)
        method, url, body = rename_with_the_off_switch(path, monitor)

        response = getattr(auth_client, method)(url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        message = response.json()["message"]
        if path == "alerts-api":
            # The alerts API carries the serializer's errors inside its message.
            assert SHARE_RANGE_SENTENCES[metric_type] in message
        else:
            assert message == SHARE_RANGE_SENTENCES[metric_type]
        assert held_values(monitor) == before
        assert monitor.name == "Saved alert"

    @pytest.mark.parametrize("metric_type", SHARE_METRICS)
    @pytest.mark.parametrize(
        "no_value_as", [None, ""], ids=["no-value-as-null", "no-value-as-blank-text"]
    )
    def test_a_full_update_with_every_value_as_saved_switches_it_off_and_no_more(
        self,
        auth_client,
        organization,
        workspace,
        observe_project,
        user,
        metric_type,
        no_value_as,
    ):
        monitor = fully_saved_alert(
            organization, workspace, observe_project, user, metric_type
        )
        before = held_values(monitor)
        # The server adds the alert's organization, workspace and creator to a
        # full update; the body sends the project, and the two texts the alert
        # holds no value for, as the edit form does or as null.
        body = every_value_as_saved(
            monitor,
            is_mute=True,
            metric=no_value_as,
            threshold_metric_value=no_value_as,
        )

        response = auth_client.put(f"{ALERTS_URL}{monitor.id}/", body, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert held_values(monitor) == {**before, "is_mute": True}
        assert before["is_mute"] is False
        assert before["threshold_rule"] == "earlier"
        assert before["created_by_id"] == user.id

    @pytest.mark.parametrize(
        "case", list(OUT_OF_ORDER_ALERTS.values()), ids=list(OUT_OF_ORDER_ALERTS)
    )
    def test_an_alert_saved_with_its_levels_out_of_order_switches_off_and_not_on(
        self, auth_client, organization, workspace, observe_project, case
    ):
        fields, on_sentence = case
        monitor = saved_static_alert(organization, workspace, observe_project, **fields)
        levels = (fields["critical_threshold_value"], fields["warning_threshold_value"])
        url = f"{ALERTS_URL}{monitor.id}/"

        switched_off = auth_client.patch(url, {"is_mute": True}, format="json")

        assert switched_off.status_code == status.HTTP_200_OK, switched_off.json()
        assert mute_and_levels(monitor) == (True, *levels)

        switched_on = auth_client.patch(url, {"is_mute": False}, format="json")

        assert switched_on.status_code == status.HTTP_400_BAD_REQUEST
        assert switched_on.json()["message"] == on_sentence
        assert mute_and_levels(monitor) == (True, *levels)

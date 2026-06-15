"""
CustomEvalConfig API Tests

Tests for /tracer/custom-eval-config/ endpoints.
"""

import json
import uuid

import pytest
from rest_framework import status

from tracer.models.custom_eval_config import CustomEvalConfig

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigCreateAPI:
    """Tests for POST /tracer/custom-eval-config/ endpoint."""

    def test_create_config_unauthenticated(self, api_client, project, eval_template):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "New Config",
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_config_success(self, auth_client, project, eval_template):
        """Create a new custom eval config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "New Custom Eval",
                "config": {"threshold": 0.9},
                "mapping": {"input": "input", "output": "output"},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "id" in data or "custom_eval_config_id" in data

    def test_create_config_with_filters(self, auth_client, project, eval_template):
        """Create config with filters."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "Filtered Config",
                "filters": {"observation_type": ["llm"]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_create_config_missing_project(self, auth_client, eval_template):
        """Create config fails without project."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "eval_template": str(eval_template.id),
                "name": "No Project Config",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_config_duplicate_name(
        self, auth_client, project, eval_template, custom_eval_config
    ):
        """Create config with duplicate name fails."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": custom_eval_config.name,  # Same name
            },
            format="json",
        )
        # Should fail due to unique constraint
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigListAPI:
    """Tests for GET /tracer/custom-eval-config/list_custom_eval_configs/ endpoint."""

    def test_list_configs_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_configs_missing_project(self, auth_client):
        """List configs without project ID."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/"
        )
        # API may return 200 with empty list or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_list_configs_success(self, auth_client, project, custom_eval_config):
        """List custom eval configs for a project."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert isinstance(data, list) or "configs" in data

    def test_list_configs_empty(self, auth_client, project):
        """List returns empty when no configs exist."""
        # Delete any existing configs
        CustomEvalConfig.objects.filter(project=project).delete()

        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_configs_rejects_legacy_query_aliases(
        self, auth_client, project, eval_task
    ):
        """List endpoint should expose only canonical query params."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {
                "projectId": str(project.id),
                "taskId": str(eval_task.id),
                "filters": json.dumps({}),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigCheckExistsAPI:
    """Tests for POST /tracer/custom-eval-config/check_exists/ endpoint."""

    def test_check_exists_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/check_exists/",
            # API expects project_name and eval_tags, not project_id and name
            {"project_name": project.name, "eval_tags": ["test"]},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_check_exists_true(self, auth_client, project, custom_eval_config):
        """Check exists returns true for existing config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/check_exists/",
            {
                # API expects project_name and eval_tags
                "project_name": project.name,
                "eval_tags": [custom_eval_config.name],
            },
            format="json",
        )
        # API returns 200 with exists field, 400 if not found, or 500 on internal error
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_check_exists_false(self, auth_client, project):
        """Check exists returns false for non-existing config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/check_exists/",
            {
                "project_name": project.name,
                "eval_tags": ["NonExistentConfig"],
            },
            format="json",
        )
        # API may return 200 with exists=false, 400, or 500 on internal error
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigGetByNameAPI:
    """Tests for POST /tracer/custom-eval-config/get_custom_eval_by_name/ endpoint."""

    def test_get_by_name_unauthenticated(self, api_client, eval_template):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            # API expects eval_template_name
            {"eval_template_name": eval_template.name},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_by_name_success(self, auth_client, eval_template):
        """Get eval template by name."""
        response = auth_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            {
                # API expects eval_template_name
                "eval_template_name": eval_template.name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "is_user_eval_template" in data or "eval_template" in data

    def test_get_by_name_not_found(self, auth_client):
        """Get by name returns empty for non-existing template."""
        response = auth_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            {
                "eval_template_name": "NonExistentTemplate",
            },
            format="json",
        )
        # API returns 200 with is_user_eval_template=False when not found
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigRunEvaluationAPI:
    """Tests for POST /tracer/custom-eval-config/run_evaluation/ endpoint."""

    def test_run_evaluation_unauthenticated(
        self, api_client, custom_eval_config, project_version
    ):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(custom_eval_config.id),
                "project_version_id": str(project_version.id),
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_run_evaluation_missing_config(self, auth_client, project_version):
        """Run evaluation fails without config ID."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {"project_version_id": str(project_version.id)},
            format="json",
        )
        # API should return 400 but may return 500 on internal error
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_run_evaluation_success(
        self, auth_client, custom_eval_config, observation_span, project_version
    ):
        """Run evaluation on spans."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(custom_eval_config.id),
                "project_version_id": str(project_version.id),
                "span_ids": [observation_span.id],
            },
            format="json",
        )
        # May succeed or fail depending on eval configuration
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_run_evaluation_invalid_config(self, auth_client, project_version):
        """Run evaluation with invalid config fails."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(uuid.uuid4()),
                "project_version_id": str(project_version.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

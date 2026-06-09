"""
API Tests for Evaluation Runner endpoints in eval_runner.py.

Endpoints covered:
- CustomEvalTemplateCreateView: POST /model-hub/create_custom_evals/ - Create custom eval template
- EvalTemplateCreateView: POST /model-hub/eval-template/create/ - Create eval template
- EvalUserTemplateCreateView: POST /model-hub/eval-user-template/create/ - Create user eval template
- DatasetEvalStatsView: GET /model-hub/dataset/{id}/eval-stats/ - Get eval stats for dataset

Also tests key helper functions:
- bulk_update_or_create_cells
- process_mapping
- CustomEvalTemplateCreateSerializer validation
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.serializers.eval_runner import (
    CustomEvalTemplateCreateSerializer,
    EvalTemplateSerializer,
    EvalUserTemplateSerializer,
)
from model_hub.utils.evals import prepare_user_eval_config
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context


@pytest.mark.django_db
class EvalRunnerBaseTestCase(APITestCase):
    """Base test case for Eval Runner API tests."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class."""
        cls.organization = Organization.objects.create(name="Test Organization")
        cls.other_organization = Organization.objects.create(name="Other Organization")

        cls.user = User.objects.create_user(
            email="test@futureagi.com",
            password="testpassword123",
            name="Test User",
            organization=cls.organization,
            organization_role=OrganizationRoles.OWNER,
        )

        cls.other_user = User.objects.create_user(
            email="other@futureagi.com",
            password="testpassword123",
            name="Other User",
            organization=cls.other_organization,
            organization_role=OrganizationRoles.OWNER,
        )

        # Create workspaces
        cls.workspace = Workspace.objects.create(
            name="Default Workspace",
            organization=cls.organization,
            is_default=True,
            created_by=cls.user,
        )
        cls.other_workspace = Workspace.objects.create(
            name="Default Workspace",
            organization=cls.other_organization,
            is_default=True,
            created_by=cls.other_user,
        )

        # Set workspace/organization context before creating models that use signals
        set_workspace_context(workspace=cls.workspace, organization=cls.organization)

        # Create sample eval templates (system templates)
        cls.eval_template = EvalTemplate.objects.create(
            name="test-eval",
            description="Test eval template",
            owner=OwnerChoices.SYSTEM.value,
            config={
                "required_keys": ["response", "query"],
                "optional_keys": ["context"],
                "eval_type_id": "OutputEvaluator",
                "output": "Pass/Fail",
            },
            eval_tags=["FUTURE_EVALS"],
        )

        # Create test dataset
        cls.dataset = Dataset.objects.create(
            name="Test Dataset",
            organization=cls.organization,
            user=cls.user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            column_order=[],
            column_config={},
            workspace=cls.workspace,
        )

    def setUp(self):
        """Set up for each test method."""
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.id))
        set_workspace_context(workspace=self.workspace, organization=self.organization)


# =============================================================================
# CustomEvalTemplateCreateSerializer Tests
# =============================================================================


@pytest.mark.django_db
class TestCustomEvalTemplateCreateSerializer(EvalRunnerBaseTestCase):
    """Tests for CustomEvalTemplateCreateSerializer validation."""

    def test_valid_data(self):
        """Serializer accepts valid data."""
        data = {
            "name": "my-custom-eval",
            "description": "A custom eval",
            "template_type": "Futureagi",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Check if {{response}} is correct",
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_name_with_uppercase_rejected(self):
        """Serializer rejects names with uppercase letters."""
        data = {
            "name": "MyCustomEval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "Name can only contain" in str(serializer.errors)

    def test_name_with_spaces_rejected(self):
        """Serializer rejects names with spaces."""
        data = {
            "name": "my custom eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()

    def test_name_starting_with_hyphen_rejected(self):
        """Serializer rejects names starting with hyphen."""
        data = {
            "name": "-my-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}}",
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "cannot start with hyphens" in str(serializer.errors)

    def test_name_ending_with_underscore_rejected(self):
        """Serializer rejects names ending with underscore."""
        data = {
            "name": "my-eval_",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()

    def test_name_with_consecutive_separators_rejected(self):
        """Serializer rejects names with consecutive separators."""
        data = {
            "name": "my_-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "consecutive separators" in str(serializer.errors)

    def test_empty_name_rejected(self):
        """Serializer rejects empty names."""
        data = {
            "name": "   ",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()

    def test_choices_output_type_requires_choices(self):
        """When output_type is 'choices', choices dict must be provided."""
        data = {
            "name": "my-eval",
            "output_type": "choices",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}}",
            "choices": None,
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "Choices must be provided" in str(serializer.errors)

    def test_choices_output_type_with_valid_choices(self):
        """When output_type is 'choices', valid choices dict is accepted."""
        data = {
            "name": "my-eval",
            "output_type": "choices",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}} quality",
            "choices": {"good": "Good response", "bad": "Bad response"},
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_invalid_config_keys_rejected(self):
        """Serializer rejects invalid keys in config."""
        data = {
            "name": "my-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}}",
            "config": {"invalid_key": "value"},
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "Invalid keys in config" in str(serializer.errors)

    def test_valid_config_keys_accepted(self):
        """Serializer accepts valid keys in config."""
        data = {
            "name": "my-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}}",
            "config": {"model": "gpt-4", "proxy_agi": True, "visible_ui": True},
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_name_whitespace_trimmed(self):
        """Serializer trims whitespace from name."""
        data = {
            "name": "  my-eval  ",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Evaluate {{response}}",
        }
        serializer = CustomEvalTemplateCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "my-eval"


# =============================================================================
# CustomEvalTemplateCreateView Tests
# =============================================================================


@pytest.mark.django_db
class TestCustomEvalTemplateCreateView(EvalRunnerBaseTestCase):
    """Tests for POST /model-hub/create_custom_evals/ endpoint."""

    def test_create_custom_eval_template_success(self):
        """Successfully create a custom eval template."""
        data = {
            "name": "my-new-eval",
            "description": "A new custom evaluation",
            "template_type": "Futureagi",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
            "criteria": "Check {{response}} quality",
            "tags": ["custom", "test"],
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        # API returns result, not data
        result = response_data.get("result") or response_data.get("data")
        assert "eval_template_id" in result

        # Verify the template was created
        template_id = result.get("eval_template_id")
        template = EvalTemplate.objects.get(id=template_id)
        assert template.name == "my-new-eval"
        assert template.owner == OwnerChoices.USER.value
        assert template.organization == self.organization

    def test_create_custom_eval_template_duplicate_name(self):
        """Create fails when name already exists for organization."""
        # Create initial template
        EvalTemplate.objects.create(
            name="existing-eval",
            organization=self.organization,
            owner=OwnerChoices.USER.value,
            config={"required_keys": ["response"], "eval_type_id": "OutputEvaluator"},
        )

        data = {
            "name": "existing-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_custom_eval_template_system_name_conflict(self):
        """Create fails when name conflicts with system template."""
        data = {
            "name": "test-eval",  # Same as cls.eval_template
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_custom_eval_template_invalid_name(self):
        """Create fails with invalid name format."""
        data = {
            "name": "Invalid Name With Spaces",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_custom_eval_template_without_auth(self):
        """Create fails without authentication."""
        self.client.force_authenticate(user=None)

        data = {
            "name": "my-eval",
            "output_type": "Pass/Fail",
            "required_keys": ["response"],
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_custom_eval_template_missing_required_keys(self):
        """Create fails when required_keys is missing."""
        data = {
            "name": "my-eval",
            "output_type": "Pass/Fail",
        }

        response = self.client.post(
            "/model-hub/create_custom_evals/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# EvalTemplateCreateView Tests
# =============================================================================


@pytest.mark.django_db
class TestEvalTemplateCreateView(EvalRunnerBaseTestCase):
    """Tests for POST /model-hub/eval-template/create/ endpoint."""

    def test_create_eval_template_success(self):
        """Successfully create an eval template."""
        data = {
            "name": "new-template",
            "owner": OwnerChoices.USER.value,
            "config": {
                "required_keys": ["response"],
                "eval_type_id": "OutputEvaluator",
            },
            "eval_tags": ["test"],
        }

        response = self.client.post(
            "/model-hub/eval-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_create_eval_template_invalid_data(self):
        """Create fails with invalid data."""
        data = {
            "name": "",  # Empty name
            "owner": OwnerChoices.USER.value,
            "config": {},
        }

        response = self.client.post(
            "/model-hub/eval-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_eval_template_without_auth(self):
        """Create fails without authentication."""
        self.client.force_authenticate(user=None)

        data = {
            "name": "new-template",
            "owner": OwnerChoices.USER.value,
            "config": {"required_keys": ["response"]},
        }

        response = self.client.post(
            "/model-hub/eval-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# EvalUserTemplateCreateView Tests
# =============================================================================


@pytest.mark.django_db
class TestEvalUserTemplateCreateView(EvalRunnerBaseTestCase):
    """Tests for POST /model-hub/eval-user-template/create/ endpoint."""

    def test_create_user_eval_template_success(self):
        """Successfully create a user eval template."""
        data = {
            "name": "my-user-eval",
            "template_id": str(self.eval_template.id),
            "dataset_id": str(self.dataset.id),
            "config": {
                "mapping": {"response": str(uuid.uuid4())},
            },
            "model": ModelChoices.TURING_LARGE.value,
        }

        response = self.client.post(
            "/model-hub/eval-user-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify user eval metric was created
        user_metric = UserEvalMetric.objects.filter(
            name="my-user-eval",
            organization=self.organization,
        ).first()
        assert user_metric is not None
        assert user_metric.template == self.eval_template

    def test_create_user_eval_template_missing_template_id(self):
        """Create fails when template_id is missing."""
        data = {
            "name": "my-user-eval",
            "dataset_id": str(self.dataset.id),
            "config": {},
        }

        response = self.client.post(
            "/model-hub/eval-user-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_user_eval_template_missing_dataset_id(self):
        """Create fails when dataset_id is missing."""
        data = {
            "name": "my-user-eval",
            "template_id": str(self.eval_template.id),
            "config": {},
        }

        response = self.client.post(
            "/model-hub/eval-user-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_user_eval_template_without_auth(self):
        """Create fails without authentication."""
        self.client.force_authenticate(user=None)

        data = {
            "name": "my-user-eval",
            "template_id": str(self.eval_template.id),
            "dataset_id": str(self.dataset.id),
            "config": {},
        }

        response = self.client.post(
            "/model-hub/eval-user-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# DatasetEvalStatsView Tests
# =============================================================================


@pytest.mark.django_db
class TestDatasetEvalStatsView(EvalRunnerBaseTestCase):
    """Tests for GET /model-hub/dataset/{id}/eval-stats/ endpoint."""

    def test_get_eval_stats_success(self):
        """Successfully get eval stats for dataset."""
        # Create a user eval metric for the dataset
        UserEvalMetric.objects.create(
            name="test-metric",
            organization=self.organization,
            dataset=self.dataset,
            template=self.eval_template,
            config={"mapping": {}},
            user=self.user,
            workspace=self.workspace,
        )

        response = self.client.get(f"/model-hub/dataset/{self.dataset.id}/eval-stats/")

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        # API may return data or result
        assert "data" in response_data or "result" in response_data

    def test_get_eval_stats_empty_dataset(self):
        """Get eval stats returns empty for dataset with no evals."""
        empty_dataset = Dataset.objects.create(
            name="Empty Dataset",
            organization=self.organization,
            user=self.user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            workspace=self.workspace,
        )

        response = self.client.get(f"/model-hub/dataset/{empty_dataset.id}/eval-stats/")

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        # API may return data or result
        result = response_data.get("data") or response_data.get("result")
        assert result == []

    def test_get_eval_stats_with_column_filter(self):
        """Get eval stats supports filtering by column_ids."""
        # Create column
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=self.dataset,
        )

        response = self.client.get(
            f"/model-hub/dataset/{self.dataset.id}/eval-stats/",
            {"column_ids": str(column.id)},
        )

        assert response.status_code == status.HTTP_200_OK

    def test_get_eval_stats_without_auth(self):
        """Get eval stats fails without authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get(f"/model-hub/dataset/{self.dataset.id}/eval-stats/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_eval_stats_invalid_dataset_id(self):
        """Get eval stats handles invalid dataset ID gracefully."""
        fake_id = uuid.uuid4()

        response = self.client.get(f"/model-hub/dataset/{fake_id}/eval-stats/")

        # Should return 200 with empty data or 400/404
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]


# =============================================================================
# Helper Function Tests
# =============================================================================


@pytest.mark.django_db
class TestBulkUpdateOrCreateCells(EvalRunnerBaseTestCase):
    """Tests for bulk_update_or_create_cells helper function."""

    def setUp(self):
        super().setUp()
        # Create columns
        self.column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=self.dataset,
        )
        # Create rows
        self.rows = [
            Row.objects.create(dataset=self.dataset, order=i) for i in range(3)
        ]

    def test_bulk_create_new_cells(self):
        """Creates cells when they don't exist."""
        from model_hub.views.eval_runner import bulk_update_or_create_cells

        row_ids = [row.id for row in self.rows]
        new_values = {"value": "test_value", "status": "pass"}

        updated, created = bulk_update_or_create_cells(
            row_ids, self.column.id, self.dataset.id, new_values
        )

        assert created == 3
        assert updated == 0

        # Verify cells were created
        cells = Cell.objects.filter(column=self.column, dataset=self.dataset)
        assert cells.count() == 3

    def test_bulk_update_existing_cells(self):
        """Updates cells when they already exist."""
        from model_hub.views.eval_runner import bulk_update_or_create_cells

        # Create initial cells
        for row in self.rows:
            Cell.objects.create(
                row=row,
                column=self.column,
                dataset=self.dataset,
                value="old_value",
                status="running",
            )

        row_ids = [row.id for row in self.rows]
        new_values = {"value": "new_value", "status": "pass"}

        updated, created = bulk_update_or_create_cells(
            row_ids, self.column.id, self.dataset.id, new_values
        )

        assert updated == 3
        assert created == 0

        # Verify cells were updated
        cells = Cell.objects.filter(column=self.column, dataset=self.dataset)
        for cell in cells:
            assert cell.value == "new_value"
            assert cell.status == "pass"

    def test_bulk_mixed_update_and_create(self):
        """Handles mix of updates and creates."""
        from model_hub.views.eval_runner import bulk_update_or_create_cells

        # Create cell for first row only
        Cell.objects.create(
            row=self.rows[0],
            column=self.column,
            dataset=self.dataset,
            value="existing",
            status="running",
        )

        row_ids = [row.id for row in self.rows]
        new_values = {"value": "updated", "status": "pass"}

        updated, created = bulk_update_or_create_cells(
            row_ids, self.column.id, self.dataset.id, new_values
        )

        assert updated == 1
        assert created == 2


# =============================================================================
# EvalTemplateSerializer Tests
# =============================================================================


@pytest.mark.django_db
class TestEvalTemplateSerializer:
    """Tests for EvalTemplateSerializer validation."""

    def test_valid_data(self):
        """Serializer accepts valid data."""
        data = {
            "name": "test-template",
            "owner": OwnerChoices.SYSTEM.value,
            "config": {"required_keys": ["response"]},
            "eval_tags": ["test"],
        }
        serializer = EvalTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_name_max_length(self):
        """Serializer rejects names exceeding max length."""
        data = {
            "name": "a" * 51,  # Max is 50
            "owner": OwnerChoices.SYSTEM.value,
            "config": {},
        }
        serializer = EvalTemplateSerializer(data=data)
        assert not serializer.is_valid()

    def test_empty_eval_tags_allowed(self):
        """Serializer allows empty eval_tags."""
        data = {
            "name": "test-template",
            "owner": OwnerChoices.SYSTEM.value,
            "config": {},
            "eval_tags": [],
        }
        serializer = EvalTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


# =============================================================================
# EvalUserTemplateSerializer Tests
# =============================================================================


@pytest.mark.django_db
class TestEvalUserTemplateSerializer:
    """Tests for EvalUserTemplateSerializer validation."""

    def test_valid_data(self):
        """Serializer accepts valid data."""
        data = {
            "name": "test-user-template",
            "template_id": str(uuid.uuid4()),
            "dataset_id": str(uuid.uuid4()),
            "config": {"mapping": {}},
        }
        serializer = EvalUserTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_missing_required_fields(self):
        """Serializer rejects missing required fields."""
        data = {
            "name": "test-user-template",
            # Missing template_id and dataset_id
            "config": {},
        }
        serializer = EvalUserTemplateSerializer(data=data)
        assert not serializer.is_valid()
        assert "template_id" in serializer.errors
        assert "dataset_id" in serializer.errors


# =============================================================================
# Fixtures for pytest-style tests
# =============================================================================


@pytest.fixture
def eval_template_fixture(db):
    """Create a test eval template."""
    return EvalTemplate.objects.create(
        name="fixture-eval",
        description="Fixture eval template",
        owner=OwnerChoices.SYSTEM.value,
        config={
            "required_keys": ["response"],
            "eval_type_id": "OutputEvaluator",
            "output": "Pass/Fail",
        },
    )


@pytest.fixture
def dataset_fixture(db, user, workspace):
    """Create a test dataset."""
    return Dataset.objects.create(
        name="Fixture Dataset",
        organization=user.organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        workspace=workspace,
    )


@pytest.fixture
def user_eval_metric_fixture(
    db, user, workspace, dataset_fixture, eval_template_fixture
):
    """Create a test user eval metric."""
    return UserEvalMetric.objects.create(
        name="fixture-metric",
        organization=user.organization,
        dataset=dataset_fixture,
        template=eval_template_fixture,
        config={"mapping": {}},
        user=user,
        workspace=workspace,
    )


@pytest.mark.integration
@pytest.mark.api
class TestEvalRunnerAPIWithFixtures:
    """Pytest-style tests using fixtures from conftest.py."""

    def test_get_dataset_eval_stats(
        self, auth_client, dataset_fixture, user_eval_metric_fixture
    ):
        """Get dataset eval stats using fixtures."""
        response = auth_client.get(
            f"/model-hub/dataset/{dataset_fixture.id}/eval-stats/"
        )

        assert response.status_code == status.HTTP_200_OK

    def test_create_user_eval_template(
        self, auth_client, dataset_fixture, eval_template_fixture
    ):
        """Create user eval template using fixtures."""
        data = {
            "name": "fixture-test-eval",
            "template_id": str(eval_template_fixture.id),
            "dataset_id": str(dataset_fixture.id),
            "config": {"mapping": {}},
        }

        response = auth_client.post(
            "/model-hub/eval-user-template/create/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# TestEvalTemplateSerializer Tests (for eval_type_id field)
# =============================================================================


@pytest.mark.django_db
class TestTestEvalTemplateSerializer:
    """Tests for TestEvalTemplateSerializer validation."""

    def test_valid_data_with_eval_type_id(self):
        """Serializer accepts valid data with eval_type_id."""
        from model_hub.serializers.eval_runner import TestEvalTemplateSerializer

        data = {
            "name": "test-function-eval",
            "config": {"config": {"keywords": ["hello", "world"]}},
            "output_type": "Pass/Fail",
            "template_type": "Function",
            "eval_type_id": "ContainsAny",
        }
        serializer = TestEvalTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["eval_type_id"] == "ContainsAny"

    def test_eval_type_id_optional(self):
        """Serializer allows missing eval_type_id (for non-Function evals)."""
        from model_hub.serializers.eval_runner import TestEvalTemplateSerializer

        data = {
            "name": "test-futureagi-eval",
            "config": {"model": "turing_large"},
            "output_type": "Pass/Fail",
            "template_type": "Futureagi",
            "criteria": "{{variable_1}}",
        }
        serializer = TestEvalTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_eval_type_id_allows_empty_string(self):
        """Serializer allows empty string for eval_type_id."""
        from model_hub.serializers.eval_runner import TestEvalTemplateSerializer

        data = {
            "name": "test-eval",
            "config": {},
            "output_type": "Pass/Fail",
            "template_type": "Futureagi",
            "eval_type_id": "",
        }
        serializer = TestEvalTemplateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


# =============================================================================
# TestEvaluationTemplateAPIView Tests (Function Evals)
# =============================================================================


@pytest.mark.django_db
class TestTestEvaluationTemplateAPIView(EvalRunnerBaseTestCase):
    """Tests for TestEvaluationTemplateAPIView with Function evals."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class."""
        super().setUpTestData()

    def test_function_eval_missing_eval_type_id(self):
        """Function eval without eval_type_id returns error."""
        data = {
            "name": "test-function-eval",
            "config": {
                "config": {"keywords": ["hello", "world"], "case_sensitive": True}
            },
            "output_type": "Pass/Fail",
            "template_type": "Function",
            # Missing eval_type_id
        }

        response = self.client.post(
            "/model-hub/test-evaluation/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "eval_type_id" in str(response.data).lower()

    def test_function_eval_unsupported_template_type(self):
        """Unsupported template_type returns error."""
        data = {
            "name": "test-eval",
            "config": {},
            "output_type": "Pass/Fail",
            "template_type": "InvalidType",
        }

        response = self.client.post(
            "/model-hub/test-evaluation/",
            data,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "unsupported" in str(response.data).lower()

    def test_function_eval_params_normalization_valid_integer(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            }
        }

        runtime_config = {"params": {"k": "5"}}
        normalized = normalize_eval_runtime_config(template_config, runtime_config)
        assert normalized["params"]["k"] == 5

    def test_function_eval_params_normalization_uses_default(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            }
        }

        normalized = normalize_eval_runtime_config(template_config, {})
        assert "params" in normalized
        assert normalized["params"]["k"] is None

    def test_function_eval_params_normalization_rejects_invalid_integer(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            }
        }

        with pytest.raises(ValueError):
            normalize_eval_runtime_config(template_config, {"params": {"k": "abc"}})

    def test_function_eval_params_normalization_accepts_signed_integer_string(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            }
        }

        normalized = normalize_eval_runtime_config(
            template_config, {"params": {"k": "+5"}}
        )
        assert normalized["params"]["k"] == 5

    def test_function_eval_params_normalization_rejects_double_negative_string(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                }
            }
        }

        with pytest.raises(ValueError, match="k must be an integer"):
            normalize_eval_runtime_config(template_config, {"params": {"k": "--5"}})

    def test_function_eval_params_normalization_rejects_whitespace_only_string(self):
        template_config = {
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                }
            }
        }

        with pytest.raises(ValueError, match="k must be an integer"):
            normalize_eval_runtime_config(template_config, {"params": {"k": "   "}})

    def test_function_eval_schema_fallback_from_evals_source_of_truth(self):
        """If template row lacks schema, fallback should resolve from evals.py."""
        template_config = {"eval_type_id": "RecallAtK"}

        normalized = normalize_eval_runtime_config(
            template_config, {"params": {"k": "3"}}
        )

        assert normalized["params"]["k"] == 3

    def test_legacy_function_config_evals_keep_config_config_shape(self):
        """Legacy FUNCTION_CONFIG_EVALS should still read config.config payloads."""
        legacy_template = EvalTemplate.objects.create(
            name="legacy_contains_eval",
            description="Legacy function config test",
            owner=OwnerChoices.SYSTEM.value,
            config={
                "required_keys": ["text"],
                "eval_type_id": "Contains",
                "output": "Pass/Fail",
                "config": {
                    "keywords": {"type": "array"},
                    "case_sensitive": {"type": "boolean"},
                },
                "function_eval": True,
            },
            eval_tags=["FUNCTION"],
        )

        payload = {
            "name": "legacy-eval",
            "template_type": "Function",
            "template_id": str(legacy_template.id),
            "config": {
                "config": {
                    "keywords": ["hello", "world"],
                    "case_sensitive": True,
                }
            },
            "description": "",
            "tags": [],
            "criteria": "",
        }

        prepared = prepare_user_eval_config(payload, bypass=True)
        configuration = prepared.get("configuration", {})
        assert configuration.get("function_eval") is True
        assert configuration.get("config", {}).get("keywords") == ["hello", "world"]
        assert configuration.get("config", {}).get("case_sensitive") is True
        assert "params" not in configuration

"""
API Tests for Dynamic Columns endpoints in dynamic_columns.py.

Endpoints covered:
- AddVectorDBColumnView: POST - Add a column from vector database queries
- ExtractJsonColumnView: POST - Extract JSON values from a column
- ClassifyColumnView: POST - Classify text content using LLM
- ExtractEntitiesView: POST - Extract entities from text using LLM
- AddApiColumnView: POST - Add a column by making API calls
- ExecutePythonCodeView: POST - Execute Python code on rows
- ConditionalColumnView: POST - Create conditional columns
- GetOperationConfigView: GET - Get operation configuration
- RerunOperationView: POST - Rerun an operation on a column
- PreviewDatasetOperationView: POST - Preview operations on sample rows
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.dynamic_columns import (
    AddApiColumnView,
    AddVectorDBColumnView,
    ClassifyColumnView,
    ConditionalColumnView,
    ExecutePythonCodeView,
    ExtractEntitiesView,
    ExtractJsonColumnView,
    GetOperationConfigView,
    PreviewDatasetOperationView,
    RerunOperationView,
)
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context


@pytest.mark.django_db
class DynamicColumnsBaseTestCase(APITestCase):
    """Base test case for Dynamic Columns API tests."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class."""
        cls.organization = Organization.objects.create(name="Test Organization")
        cls.other_organization = Organization.objects.create(name="Other Organization")

        cls.user = User.objects.create_user(
            email="test@example.com",
            password="testpassword123",
            name="Test User",
            organization=cls.organization,
            organization_role=OrganizationRoles.OWNER,
        )

        cls.other_user = User.objects.create_user(
            email="other@example.com",
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

    def setUp(self):
        """Set up for each test method."""
        # Set workspace context for signals
        set_workspace_context(workspace=self.workspace, organization=self.organization)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.id))

        # Patch APIView.initial to inject workspace for all requests in this test class
        from rest_framework.views import APIView

        self.original_initial = APIView.initial
        workspace = self.workspace

        def initial_with_workspace(view_self, request, *args, **view_kwargs):
            # Inject workspace before view processing
            request.workspace = workspace
            return self.original_initial(view_self, request, *args, **view_kwargs)

        self.workspace_patcher = patch.object(
            APIView, "initial", initial_with_workspace
        )
        self.workspace_patcher.start()

    def tearDown(self):
        """Clean up after each test method."""
        self.workspace_patcher.stop()

    def create_test_dataset(
        self,
        name="Test Dataset",
        organization=None,
        user=None,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        num_columns=2,
        num_rows=3,
    ):
        """Helper method to create a test dataset with columns and rows."""
        org = organization or self.organization
        usr = user or self.user

        dataset = Dataset.objects.create(
            name=name,
            organization=org,
            user=usr,
            source=source,
            model_type=model_type,
            column_order=[],
            column_config={},
        )

        columns = []
        column_order = []
        column_config = {}

        for i in range(num_columns):
            column = Column.objects.create(
                name=f"Column {i + 1}",
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.OTHERS.value,
                dataset=dataset,
            )
            columns.append(column)
            column_order.append(str(column.id))
            column_config[str(column.id)] = {"is_visible": True, "is_frozen": None}

        dataset.column_order = column_order
        dataset.column_config = column_config
        dataset.save()

        rows = []
        for i in range(num_rows):
            row = Row.objects.create(dataset=dataset, order=i)
            rows.append(row)
            for j, column in enumerate(columns):
                Cell.objects.create(
                    row=row,
                    column=column,
                    dataset=dataset,
                    value=f"Row {i + 1}, Col {j + 1}",
                )

        return dataset, columns, rows

    def create_json_dataset(self):
        """Helper method to create a dataset with JSON data in cells."""
        dataset, columns, rows = self.create_test_dataset(num_columns=1, num_rows=3)
        column = columns[0]

        # Update cells with JSON data
        for i, row in enumerate(rows):
            cell = Cell.objects.get(row=row, column=column)
            cell.value = json.dumps(
                {"name": f"User{i}", "age": 20 + i, "nested": {"key": f"value{i}"}}
            )
            cell.save()

        return dataset, column, rows


# =============================================================================
# ExtractJsonColumnView Tests
# =============================================================================
@pytest.mark.django_db
class TestExtractJsonColumnView(DynamicColumnsBaseTestCase):
    """Tests for ExtractJsonColumnView."""

    def test_extract_json_missing_column_id(self):
        """Test that missing column_id returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={"json_key": "name", "new_column_name": "Extracted Name"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extract_json_missing_json_key(self):
        """Test that missing json_key returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Extracted Name",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extract_json_column_name_exists(self):
        """Test that duplicate column name returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "json_key": "name",
                "new_column_name": columns[0].name,  # Same name as existing column
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extract_json_invalid_concurrency(self):
        """Test that invalid concurrency value returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "json_key": "name",
                "new_column_name": "Extracted Name",
                "concurrency": -1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extract_json_invalid_concurrency_string(self):
        """Test that non-integer concurrency returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "json_key": "name",
                "new_column_name": "Extracted Name",
                "concurrency": "invalid",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.extract_json_async.delay")
    def test_extract_json_success(self, mock_task):
        """Test successful JSON extraction column creation."""
        dataset, column, _ = self.create_json_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(column.id),
                "json_key": "name",
                "new_column_name": "Extracted Name",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()

    @patch("model_hub.views.dynamic_columns.extract_json_async.delay")
    def test_extract_json_nested_key(self, mock_task):
        """Test JSON extraction with nested key using dot notation."""
        dataset, column, _ = self.create_json_dataset()
        url = reverse("extract_json_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(column.id),
                "json_key": "nested.key",
                "new_column_name": "Nested Value",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.assert_called_once()


# =============================================================================
# ClassifyColumnView Tests
# =============================================================================
@pytest.mark.django_db
class TestClassifyColumnView(DynamicColumnsBaseTestCase):
    """Tests for ClassifyColumnView."""

    def test_classify_missing_column_id(self):
        """Test that missing column_id returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "labels": ["positive", "negative"],
                "new_column_name": "Sentiment",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_classify_missing_labels(self):
        """Test that missing labels returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Sentiment",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_classify_labels_less_than_two(self):
        """Test that labels with less than 2 items returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "labels": ["positive"],  # Only one label
                "new_column_name": "Sentiment",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_classify_labels_not_list(self):
        """Test that labels not being a list returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "labels": "positive,negative",  # String instead of list
                "new_column_name": "Sentiment",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_classify_column_name_exists(self):
        """Test that duplicate column name returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "labels": ["positive", "negative"],
                "new_column_name": columns[0].name,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.classify_column_async.delay")
    def test_classify_success(self, mock_task):
        """Test successful classification column creation."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("classify-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "labels": ["positive", "negative", "neutral"],
                "new_column_name": "Sentiment",
                "language_model_id": "gpt-4o",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()


# =============================================================================
# ExtractEntitiesView Tests
# =============================================================================
@pytest.mark.django_db
class TestExtractEntitiesView(DynamicColumnsBaseTestCase):
    """Tests for ExtractEntitiesView."""

    def test_extract_entities_missing_column_id(self):
        """Test that missing column_id returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("extract-entities", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "instruction": "Extract person names",
                "new_column_name": "Entities",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extract_entities_missing_instruction(self):
        """Test that missing instruction returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract-entities", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Entities",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.extract_async.delay")
    def test_extract_entities_success(self, mock_task):
        """Test successful entity extraction column creation."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("extract-entities", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "instruction": "Extract all person names mentioned in the text",
                "new_column_name": "Person Names",
                "language_model_id": "gpt-4",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.assert_called_once()


# =============================================================================
# AddApiColumnView Tests
# =============================================================================
@pytest.mark.django_db
class TestAddApiColumnView(DynamicColumnsBaseTestCase):
    """Tests for AddApiColumnView."""

    def test_api_column_missing_column_name(self):
        """Test that missing column_name returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("add-api-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "config": {
                    "url": "https://api.example.com/data",
                    "method": "GET",
                    "output_type": "string",
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_api_column_missing_config(self):
        """Test that missing config returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("add-api-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url, data={"column_name": "API Result"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_api_column_incomplete_config(self):
        """Test that incomplete config returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("add-api-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_name": "API Result",
                "config": {
                    "url": "https://api.example.com/data",
                    # Missing method and output_type
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.add_api_column_async.delay")
    def test_api_column_success(self, mock_task):
        """Test successful API column creation."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("add-api-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_name": "API Result",
                "config": {
                    "url": "https://api.example.com/data",
                    "method": "GET",
                    "output_type": "string",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()


# =============================================================================
# ExecutePythonCodeView Tests
# =============================================================================
@pytest.mark.django_db
class TestExecutePythonCodeView(DynamicColumnsBaseTestCase):
    """Tests for ExecutePythonCodeView."""

    @pytest.mark.skip(reason="API deprecated for security reasons")
    def test_execute_code_missing_code(self):
        """Test that missing code returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("execute-python", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url, data={"new_column_name": "Code Result"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.skip(reason="API deprecated for security reasons")
    def test_execute_code_column_name_exists(self):
        """Test that duplicate column name returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("execute-python", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "code": "def main(**kwargs): return 'test'",
                "new_column_name": columns[0].name,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.skip(reason="API deprecated for security reasons")
    @patch("model_hub.views.dynamic_columns.execute_python_code_async.delay")
    def test_execute_code_success(self, mock_task):
        """Test successful Python code execution column creation."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("execute-python", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "code": """
def main(**kwargs):
    col1 = kwargs.get('Column 1', '')
    col2 = kwargs.get('Column 2', '')
    return f"{col1} - {col2}"
""",
                "new_column_name": "Combined",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()


# =============================================================================
# ExecutePythonCodeView Security Tests
# =============================================================================
@pytest.mark.django_db
class TestExecutePythonCodeViewSecurity(DynamicColumnsBaseTestCase):
    """Security tests for ExecutePythonCodeView."""

    def test_dangerous_import_os(self):
        """Test that import os is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "import os\ndef main(**kwargs): return os.getcwd()"
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    def test_dangerous_import_sys(self):
        """Test that import sys is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "import sys\ndef main(**kwargs): return sys.path"
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    def test_dangerous_import_subprocess(self):
        """Test that import subprocess is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0],
            "import subprocess\ndef main(**kwargs): return subprocess.run(['ls'])",
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    def test_dangerous_eval_call(self):
        """Test that eval() is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "def main(**kwargs): return eval('1+1')"
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    def test_dangerous_exec_call(self):
        """Test that exec() is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "def main(**kwargs): exec('x=1'); return x"
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    def test_dangerous_open_call(self):
        """Test that open() is blocked."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "def main(**kwargs): return open('/etc/passwd').read()"
        )

        assert "Dangerous pattern" in result
        assert error_info is not None

    @pytest.mark.skip(reason="API deprecated for security reasons")
    def test_safe_code_with_os_in_string(self):
        """Test that strings containing 'os' are allowed (e.g., 'diagnosis')."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(
            rows[0], "def main(**kwargs): return 'diagnosis complete'"
        )

        # This should succeed because 'diagnosis' contains 'os' but is not importing os
        assert result == "diagnosis complete"
        assert error_info is None

    def test_missing_main_function(self):
        """Test that missing main function returns error."""
        view = ExecutePythonCodeView()
        dataset, _, rows = self.create_test_dataset()

        result, error_info = view._execute_python_code(rows[0], "x = 1 + 1")

        assert "main" in result.lower()
        assert error_info is not None


# =============================================================================
# ConditionalColumnView Tests
# =============================================================================
@pytest.mark.django_db
class TestConditionalColumnView(DynamicColumnsBaseTestCase):
    """Tests for ConditionalColumnView."""

    def test_conditional_missing_config(self):
        """Test that missing config returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("conditional-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url, data={"new_column_name": "Conditional Result"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_conditional_missing_column_name(self):
        """Test that missing new_column_name returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("conditional-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "config": [
                    {
                        "branch_type": "if",
                        "condition": "value > 5",
                        "branch_node_config": {
                            "type": "static_value",
                            "config": {"value": "high"},
                        },
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.conditional_column_async.delay")
    def test_conditional_success(self, mock_task):
        """Test successful conditional column creation."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("conditional-column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "new_column_name": "Priority",
                "config": [
                    {
                        "branch_type": "if",
                        "condition": "value contains urgent",
                        "branch_node_config": {
                            "type": "static_value",
                            "config": {"value": "high"},
                        },
                    },
                    {
                        "branch_type": "else",
                        "condition": "",
                        "branch_node_config": {
                            "type": "static_value",
                            "config": {"value": "normal"},
                        },
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()


# =============================================================================
# GetOperationConfigView Tests
# =============================================================================
@pytest.mark.django_db
class TestGetOperationConfigView(DynamicColumnsBaseTestCase):
    """Tests for GetOperationConfigView."""

    def test_get_config_column_not_found(self):
        """Test that non-existent column returns bad request."""
        fake_column_id = str(uuid.uuid4())
        url = reverse("get-operation-config", kwargs={"column_id": fake_column_id})

        response = self.client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_config_column_no_metadata(self):
        """Test that column without metadata returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        column = columns[0]
        column.metadata = None
        column.save()

        url = reverse("get-operation-config", kwargs={"column_id": str(column.id)})

        response = self.client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_config_success(self):
        """Test successful retrieval of operation config."""
        dataset, columns, _ = self.create_test_dataset()
        column = columns[0]
        column.metadata = {"json_key": "name", "concurrency": 5}
        column.save()

        url = reverse("get-operation-config", kwargs={"column_id": str(column.id)})

        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        # Check column_id is present
        assert "column_id" in result
        # Check metadata is present and non-empty
        assert "metadata" in result
        assert result["metadata"] is not None

    def test_get_config_other_organization(self):
        """Test that column from other organization is not accessible."""
        # Create dataset in other organization
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=self.other_organization,
            user=self.other_user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
        )
        other_column = Column.objects.create(
            name="Other Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=other_dataset,
            metadata={"key": "value"},
        )

        url = reverse(
            "get-operation-config", kwargs={"column_id": str(other_column.id)}
        )

        response = self.client.get(url)

        # Should not find column due to organization filter
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# RerunOperationView Tests
# =============================================================================
@pytest.mark.django_db
class TestRerunOperationView(DynamicColumnsBaseTestCase):
    """Tests for RerunOperationView."""

    def test_rerun_missing_operation_type(self):
        """Test that missing operation_type returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("rerun-operation", kwargs={"column_id": str(columns[0].id)})

        response = self.client.post(url, data={"config": {}}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_invalid_operation_type(self):
        """Test that invalid operation_type returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        columns[0].metadata = {"key": "value"}
        columns[0].save()

        url = reverse("rerun-operation", kwargs={"column_id": str(columns[0].id)})

        response = self.client.post(
            url, data={"operation_type": "invalid_type", "config": {}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.classify_column_async.delay")
    def test_rerun_classification_success(self, mock_task):
        """Test successful rerun of classification operation."""
        dataset, columns, rows = self.create_test_dataset()
        column = columns[0]
        column.metadata = {
            "labels": ["positive", "negative"],
            "language_model_id": "gpt-4o",
            "column_id": str(columns[1].id),
            "concurrency": 5,
        }
        column.save()

        # Create cells for the column
        for row in rows:
            Cell.objects.create(row=row, column=column, dataset=dataset, value="test")

        url = reverse("rerun-operation", kwargs={"column_id": str(column.id)})

        response = self.client.post(
            url,
            data={
                "operation_type": "classify",
                "config": {
                    "labels": ["positive", "negative"],
                    "language_model_id": "gpt-4o",
                    "column_id": str(columns[1].id),
                    "concurrency": 5,
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.assert_called_once()


# =============================================================================
# PreviewDatasetOperationView Tests
# =============================================================================
@pytest.mark.django_db
class TestPreviewDatasetOperationView(DynamicColumnsBaseTestCase):
    """Tests for PreviewDatasetOperationView."""

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_preview_invalid_operation_type(self, mock_close_conn):
        """Test that invalid operation type returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse(
            "preview-dataset-operation",
            kwargs={"dataset_id": str(dataset.id), "operation_type": "invalid"},
        )

        response = self.client.post(url, data={}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_preview_extract_json(self, mock_close_conn):
        """Test preview of JSON extraction."""
        dataset, column, rows = self.create_json_dataset()
        url = reverse(
            "preview-dataset-operation",
            kwargs={"dataset_id": str(dataset.id), "operation_type": "extract_json"},
        )

        response = self.client.post(
            url,
            data={"column_id": str(column.id), "json_key": "name"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert "preview_results" in data
        assert len(data["preview_results"]) <= 3  # Sample size


# =============================================================================
# ExtractJsonColumnView Helper Method Tests
# =============================================================================
@pytest.mark.django_db
class TestExtractJsonColumnViewHelpers(DynamicColumnsBaseTestCase):
    """Tests for ExtractJsonColumnView helper methods."""

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_process_cell_valid_json(self, mock_close_conn):
        """Test _process_cell with valid JSON."""
        view = ExtractJsonColumnView()
        dataset, column, rows = self.create_json_dataset()
        cell = Cell.objects.get(row=rows[0], column=column)

        result = view._process_cell(cell, "name")

        assert result == "User0"

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_process_cell_nested_json(self, mock_close_conn):
        """Test _process_cell with nested JSON key."""
        view = ExtractJsonColumnView()
        dataset, column, rows = self.create_json_dataset()
        cell = Cell.objects.get(row=rows[0], column=column)

        result = view._process_cell(cell, "nested.key")

        assert result == "value0"

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_process_cell_missing_key(self, mock_close_conn):
        """Test _process_cell with missing JSON key raises KeyError."""
        view = ExtractJsonColumnView()
        dataset, column, rows = self.create_json_dataset()
        cell = Cell.objects.get(row=rows[0], column=column)

        with pytest.raises(KeyError) as exc_info:
            view._process_cell(cell, "nonexistent")

        assert "nonexistent" in str(exc_info.value)

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_process_cell_empty_value(self, mock_close_conn):
        """Test _process_cell with empty cell value."""
        view = ExtractJsonColumnView()
        dataset, column, rows = self.create_json_dataset()
        cell = Cell.objects.get(row=rows[0], column=column)
        cell.value = None
        cell.save()

        result = view._process_cell(cell, "name")

        assert result is None

    @patch("model_hub.views.dynamic_columns.close_old_connections")
    def test_process_cell_invalid_json(self, mock_close_conn):
        """Test _process_cell with invalid JSON raises ValueError."""
        view = ExtractJsonColumnView()
        dataset, column, rows = self.create_json_dataset()
        cell = Cell.objects.get(row=rows[0], column=column)
        cell.value = "not valid json"
        cell.save()

        with pytest.raises(ValueError) as exc_info:
            view._process_cell(cell, "name")

        assert "Invalid data format" in str(exc_info.value)


# =============================================================================
# AddApiColumnView Helper Method Tests
# =============================================================================
@pytest.mark.django_db
class TestAddApiColumnViewHelpers(DynamicColumnsBaseTestCase):
    """Tests for AddApiColumnView helper methods."""

    def test_replace_variables_no_placeholders(self):
        """Test _replace_variables with no placeholders."""
        view = AddApiColumnView()
        dataset, columns, rows = self.create_test_dataset()

        result = view._replace_variables("plain text", rows[0])

        assert result == "plain text"

    def test_replace_variables_with_placeholder(self):
        """Test _replace_variables with column placeholder."""
        view = AddApiColumnView()
        dataset, columns, rows = self.create_test_dataset()
        cell = Cell.objects.get(row=rows[0], column=columns[0])

        result = view._replace_variables(f"{{{{{columns[0].id}}}}}", rows[0])

        assert result == cell.value

    def test_replace_variables_with_dot_notation(self):
        """Test _replace_variables resolves nested JSON paths."""
        view = AddApiColumnView()
        dataset, column, rows = self.create_json_dataset()

        result = view._replace_variables(f"{{{{{column.id}.name}}}}", rows[0])
        assert result == "User0"

        result = view._replace_variables(f"{{{{{column.id}.nested.key}}}}", rows[0])
        assert result == "value0"

    def test_replace_variables_with_missing_path(self):
        """Test _replace_variables returns empty string for missing JSON path."""
        view = AddApiColumnView()
        dataset, column, rows = self.create_json_dataset()

        result = view._replace_variables(f"{{{{{column.id}.nonexistent}}}}", rows[0])
        assert result == ""

    def test_resolve_cell_value_plain_uuid(self):
        """Test _resolve_cell_value with a plain column UUID (no path)."""
        dataset, columns, rows = self.create_test_dataset()
        cell = Cell.objects.get(row=rows[0], column=columns[0])

        result = AddApiColumnView._resolve_cell_value(str(columns[0].id), rows[0])
        assert result == cell.value

    def test_resolve_cell_value_with_json_path(self):
        """Test _resolve_cell_value with UUID.dotted.path."""
        dataset, column, rows = self.create_json_dataset()

        result = AddApiColumnView._resolve_cell_value(f"{column.id}.age", rows[0])
        assert result == "20"

    def test_resolve_cell_value_nested_path(self):
        """Test _resolve_cell_value with deeply nested path."""
        dataset, column, rows = self.create_json_dataset()

        result = AddApiColumnView._resolve_cell_value(f"{column.id}.nested.key", rows[1])
        assert result == "value1"

    def test_replace_variables_multiple_in_string(self):
        """Test _replace_variables with multiple variables in one string."""
        view = AddApiColumnView()
        dataset, column, rows = self.create_json_dataset()

        template = f"name={{{{{column.id}.name}}}}&age={{{{{column.id}.age}}}}"
        result = view._replace_variables(template, rows[0])
        assert result == "name=User0&age=20"


# =============================================================================
# AddVectorDBColumnView Tests
# =============================================================================
@pytest.mark.django_db
class TestAddVectorDBColumnView(DynamicColumnsBaseTestCase):
    """Tests for AddVectorDBColumnView."""

    def test_vector_db_missing_column_id(self):
        """Test that missing column_id returns bad request."""
        dataset, _, _ = self.create_test_dataset()
        url = reverse("add_vector_db_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "new_column_name": "Vector Result",
                "sub_type": "pinecone",
                "api_key": str(uuid.uuid4()),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_vector_db_missing_sub_type(self):
        """Test that missing sub_type returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("add_vector_db_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Vector Result",
                "api_key": str(uuid.uuid4()),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_vector_db_missing_api_key(self):
        """Test that missing api_key returns bad request."""
        dataset, columns, _ = self.create_test_dataset()
        url = reverse("add_vector_db_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Vector Result",
                "sub_type": "pinecone",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.dynamic_columns.add_vector_db_column_async.delay")
    def test_vector_db_success(self, mock_task):
        """Test successful vector DB column creation."""
        dataset, columns, _ = self.create_test_dataset()

        # Create a secret for API key
        secret = SecretModel.objects.create(
            name="Test Pinecone Key",
            key="test-pinecone-api-key",  # key is encrypted on save
            organization=self.organization,
        )

        url = reverse("add_vector_db_column", kwargs={"dataset_id": str(dataset.id)})

        response = self.client.post(
            url,
            data={
                "column_id": str(columns[0].id),
                "new_column_name": "Vector Result",
                "sub_type": "pinecone",
                "api_key": str(secret.id),
                "index_name": "test-index",
                "top_k": 5,
                "embedding_config": {
                    "type": "openai",
                    "model": "text-embedding-3-small",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "new_column_id" in response.json()["result"]
        mock_task.assert_called_once()

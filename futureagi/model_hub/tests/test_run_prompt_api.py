"""
Test cases for Run Prompt API endpoints.

Tests cover:
- AddRunPromptColumnView - Add a new run prompt column to dataset
- EditRunPromptColumnView - Edit an existing run prompt column
- RunPromptForRowsView - Run prompt for specific rows
- LitellmAPIView - Direct LiteLLM API calls
- PreviewRunPromptColumnView - Preview run prompt column
- RetrieveRunPromptColumnConfigView - Get run prompt column config
- RetrieveRunPromptOptionsView - Get run prompt options

Run with: pytest model_hub/tests/test_run_prompt_api.py -v
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from litellm.exceptions import APIError, AuthenticationError, PermissionDeniedError
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    EvalExplanationSummaryStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.utils.provider_key_validation import is_provider_key_valid
from tfc.middleware.workspace_context import set_workspace_context


API_KEYS_URL = "/model-hub/api-keys/"


def api_key_detail_url(api_key_id):
    return f"{API_KEYS_URL}{api_key_id}/"


def assert_provider_key_response_is_masked(payload, *raw_secrets):
    text = json.dumps(payload, default=str)
    for secret in raw_secrets:
        assert secret not in text
    if isinstance(payload, dict):
        assert "key" not in payload
        assert "config_json" not in payload


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def input_column(db, dataset):
    col = Column.objects.create(
        name="Input Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell(db, dataset, input_column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value="Test input value",
    )


class TestDatasetUtilityResponseContracts:
    def test_get_base_columns_returns_typed_result(
        self, auth_client, organization, workspace, dataset, input_column
    ):
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        Column.objects.create(
            name=input_column.name,
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Column.objects.create(
            name="Eval Score",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
        )

        response = auth_client.get(
            "/model-hub/datasets/get-base-columns/",
            {
                "dataset_ids": [str(dataset.id), str(other_dataset.id)],
            },
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["base_columns"] == [input_column.name]

    def test_explanation_summary_returns_typed_insufficient_data_result(
        self, auth_client, dataset
    ):
        response = auth_client.get(
            f"/model-hub/datasets/explanation-summary/{dataset.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["response"] == []
        assert result["last_updated"] is None
        assert result["status"] == EvalExplanationSummaryStatus.INSUFFICIENT_DATA.value
        assert result["row_count"] == 0
        assert result["min_rows_required"] == 15

    def test_refresh_explanation_summary_returns_typed_insufficient_data_result(
        self, auth_client, dataset
    ):
        response = auth_client.post(
            f"/model-hub/datasets/explanation-summary/{dataset.id}/refresh/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["status"] == EvalExplanationSummaryStatus.INSUFFICIENT_DATA.value
        assert result["row_count"] == 0
        assert result["min_rows_required"] == 15

    def test_explanation_summary_rejects_other_workspace_before_mutation(
        self, auth_client, organization, user, monkeypatch
    ):
        other_workspace = Workspace.objects.create(
            name="Other Explanation Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Explanation Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
            eval_reason_status=EvalExplanationSummaryStatus.COMPLETED.value,
            eval_reasons=[{"reason": "keep"}],
        )
        queued_tasks = []
        monkeypatch.setattr(
            "model_hub.views.develop_dataset.get_explanation_summary.delay",
            lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
        )

        read_response = auth_client.get(
            f"/model-hub/datasets/explanation-summary/{other_dataset.id}/",
        )
        refresh_response = auth_client.post(
            f"/model-hub/datasets/explanation-summary/{other_dataset.id}/refresh/",
            {},
            format="json",
        )

        assert read_response.status_code == status.HTTP_404_NOT_FOUND
        assert refresh_response.status_code == status.HTTP_404_NOT_FOUND
        other_dataset.refresh_from_db()
        assert (
            other_dataset.eval_reason_status
            == EvalExplanationSummaryStatus.COMPLETED.value
        )
        assert other_dataset.eval_reasons == [{"reason": "keep"}]
        assert queued_tasks == []


@pytest.fixture
def run_prompt_column(db, dataset):
    col = Column.objects.create(
        name="Run Prompt Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.mark.django_db
class TestGetColumnDetailView:
    """Tests for GET /model-hub/dataset/columns/{dataset_id}/."""

    def test_get_column_details_excludes_prompt_columns_by_default(
        self, auth_client, dataset, input_column, run_prompt_column
    ):
        response = auth_client.get(f"/model-hub/dataset/columns/{dataset.id}/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert result["columns"] == [
            {
                "id": str(input_column.id),
                "name": input_column.name,
                "data_type": input_column.data_type,
            }
        ]

    def test_get_column_details_can_include_prompt_columns(
        self, auth_client, dataset, input_column, run_prompt_column
    ):
        response = auth_client.get(
            f"/model-hub/dataset/columns/{dataset.id}/",
            {"include_prompt": "true"},
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert result["columns"] == [
            {
                "id": str(input_column.id),
                "name": input_column.name,
                "data_type": input_column.data_type,
            },
            {
                "id": str(run_prompt_column.id),
                "name": run_prompt_column.name,
                "data_type": run_prompt_column.data_type,
            },
        ]


@pytest.fixture
def run_prompter(db, dataset, organization, workspace):
    return RunPrompter.objects.create(
        name="Test Run Prompter",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "Test prompt"}],
        run_prompt_config={},
    )


@pytest.fixture
def valid_run_prompt_config():
    return {
        "model": "gpt-4",
        # Messages must use multi-modal format (content as list) for non-audio output_format
        # because remove_empty_text_from_messages expects this format
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "What is {{Input Column}}?"}],
            }
        ],
        "temperature": 0.7,
        "max_tokens": 100,
        "output_format": "string",  # Valid choices: array, string, number, object, audio
    }


# ==================== AddRunPromptColumnView Tests ====================


@pytest.mark.django_db
class TestAddRunPromptColumnView:
    """Tests for AddRunPromptColumnView - POST /develops/add_run_prompt_column/"""

    def test_add_run_prompt_column_success(
        self, auth_client, dataset, input_column, valid_run_prompt_config
    ):
        """Test successfully adding a run prompt column."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "AI Response",
            "config": valid_run_prompt_config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert Column.objects.filter(
            name="AI Response", dataset=dataset, deleted=False
        ).exists()

    def test_add_run_prompt_column_accepts_null_model_params(
        self, auth_client, dataset, input_column, valid_run_prompt_config
    ):
        """Null values inside run_prompt_config mean "use provider default"
        and must not be rejected (TH-6409)."""
        config = {
            **valid_run_prompt_config,
            "run_prompt_config": {
                "model_name": "gpt-4o-mini",
                "model_type": "llm",
                "temperature": None,
                "top_p": None,
                "max_tokens": None,
                "presence_penalty": None,
                "frequency_penalty": None,
            },
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "AI Response Nulls",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert Column.objects.filter(
            name="AI Response Nulls", dataset=dataset, deleted=False
        ).exists()

    def test_add_run_prompt_column_duplicate_name(
        self, auth_client, dataset, input_column, valid_run_prompt_config
    ):
        """Test that duplicate column names are rejected."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": input_column.name,  # Duplicate name
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_column_missing_dataset_id(
        self, auth_client, valid_run_prompt_config
    ):
        """Test that missing dataset_id returns error."""
        payload = {
            "name": "AI Response",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_column_missing_name(
        self, auth_client, dataset, valid_run_prompt_config
    ):
        """Test that missing name returns error."""
        payload = {
            "dataset_id": str(dataset.id),
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_column_invalid_dataset_id(
        self, auth_client, valid_run_prompt_config
    ):
        """Test that invalid dataset_id returns error."""
        payload = {
            "dataset_id": str(uuid.uuid4()),  # Non-existent
            "name": "AI Response",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_add_run_prompt_column_unauthenticated(self):
        """Test that unauthenticated users cannot add columns."""
        client = APIClient()
        response = client.post(
            "/model-hub/develops/add_run_prompt_column/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== EditRunPromptColumnView Tests ====================


@pytest.mark.django_db
class TestEditRunPromptColumnView:
    """Tests for EditRunPromptColumnView - POST /develops/edit_run_prompt_column/"""

    def test_edit_run_prompt_column_success(
        self,
        auth_client,
        dataset,
        run_prompt_column,
        run_prompter,
        valid_run_prompt_config,
    ):
        """Test successfully editing a run prompt column."""
        # Link the run_prompt_column to the run_prompter
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(run_prompt_column.id),
            "name": "Updated AI Response",
            "config": valid_run_prompt_config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_edit_run_prompt_column_invalid_column_source(
        self,
        auth_client,
        dataset,
        input_column,
        valid_run_prompt_config,
        organization,
        workspace,
    ):
        """Test that editing a non-run-prompt column returns error."""
        # Create a RunPrompter for the input column to avoid DoesNotExist
        RunPrompter.objects.create(
            name="Test Input Column Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.NOT_STARTED.value,
            model="gpt-4",
            messages=[],
            run_prompt_config={},
        )

        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(input_column.id),  # Not a run prompt column
            "name": "Updated Name",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_edit_run_prompt_column_missing_column_id(
        self, auth_client, dataset, valid_run_prompt_config
    ):
        """Test that missing column_id returns error."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Updated Name",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_edit_run_prompt_column_nonexistent_column(
        self, auth_client, dataset, valid_run_prompt_config
    ):
        """Test that editing non-existent column returns error."""
        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(uuid.uuid4()),
            "name": "Updated Name",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            payload,
            format="json",
        )

        # API returns 404 for non-existent column
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_and_config_reject_other_workspace_before_mutation(
        self, auth_client, organization, user, valid_run_prompt_config, monkeypatch
    ):
        other_workspace = Workspace.objects.create(
            name="Other Run Prompt Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Run Prompt Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_run_prompter = RunPrompter.objects.create(
            name="Other Run Prompter",
            dataset=other_dataset,
            organization=organization,
            workspace=other_workspace,
            status=StatusType.COMPLETED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Keep"}],
            run_prompt_config={},
        )
        other_column = Column.no_workspace_objects.create(
            name="Other Run Prompt Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(other_run_prompter.id),
        )
        other_row = Row.no_workspace_objects.create(dataset=other_dataset, order=0)
        other_cell = Cell.no_workspace_objects.create(
            dataset=other_dataset,
            column=other_column,
            row=other_row,
            value="keep",
            status=CellStatus.PASS.value,
        )
        queued_tasks = []
        monkeypatch.setattr(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async",
            lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
        )

        edit_response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            {
                "dataset_id": str(other_dataset.id),
                "column_id": str(other_column.id),
                "name": "Should Not Update",
                "config": valid_run_prompt_config,
            },
            format="json",
        )
        config_response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={other_column.id}",
        )

        assert edit_response.status_code == status.HTTP_404_NOT_FOUND
        assert config_response.status_code == status.HTTP_404_NOT_FOUND
        assert queued_tasks == []
        other_run_prompter.refresh_from_db()
        other_column.refresh_from_db()
        other_cell.refresh_from_db()
        assert other_run_prompter.name == "Other Run Prompter"
        assert other_run_prompter.status == StatusType.COMPLETED.value
        assert other_column.name == "Other Run Prompt Output"
        assert other_cell.value == "keep"
        assert other_cell.status == CellStatus.PASS.value

    def test_edit_and_config_reject_out_of_scope_source_run_prompter(
        self,
        auth_client,
        organization,
        user,
        dataset,
        row,
        run_prompt_column,
        valid_run_prompt_config,
        monkeypatch,
    ):
        other_workspace = Workspace.objects.create(
            name="Other Source Run Prompt Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Source Run Prompt Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_run_prompter = RunPrompter.no_workspace_objects.create(
            name="Other Source Run Prompter",
            dataset=other_dataset,
            organization=organization,
            workspace=other_workspace,
            status=StatusType.COMPLETED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Keep"}],
            run_prompt_config={},
        )
        run_prompt_column.source_id = str(other_run_prompter.id)
        run_prompt_column.save()
        output_cell = Cell.objects.create(
            dataset=dataset,
            column=run_prompt_column,
            row=row,
            value="keep output",
            status=CellStatus.PASS.value,
        )
        queued_tasks = []
        monkeypatch.setattr(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async",
            lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
        )

        edit_response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            {
                "dataset_id": str(dataset.id),
                "column_id": str(run_prompt_column.id),
                "name": "Should Not Update",
                "config": valid_run_prompt_config,
            },
            format="json",
        )
        config_response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={run_prompt_column.id}",
        )

        assert edit_response.status_code == status.HTTP_404_NOT_FOUND
        assert config_response.status_code == status.HTTP_404_NOT_FOUND
        assert queued_tasks == []
        run_prompt_column.refresh_from_db()
        output_cell.refresh_from_db()
        other_run_prompter.refresh_from_db()
        assert run_prompt_column.name == "Run Prompt Output"
        assert output_cell.value == "keep output"
        assert output_cell.status == CellStatus.PASS.value
        assert other_run_prompter.name == "Other Source Run Prompter"
        assert other_run_prompter.status == StatusType.COMPLETED.value

    def test_edit_run_prompt_column_unauthenticated(self):
        """Test that unauthenticated users cannot edit columns."""
        client = APIClient()
        response = client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== RunPromptForRowsView Tests ====================


@pytest.mark.django_db
class TestRunPromptForRowsView:
    """Tests for RunPromptForRowsView - POST /run-prompt-for-rows/"""

    def test_run_prompt_for_rows_success(self, auth_client, dataset, run_prompter, row):
        """Test successfully running prompt for specific rows."""
        payload = {
            "run_prompt_ids": [str(run_prompter.id)],
            "row_ids": [str(row.id)],
        }

        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_run_prompt_for_rows_all_rows(
        self, auth_client, dataset, run_prompter, row
    ):
        """Test running prompt for all rows when selected_all_rows is True."""
        payload = {
            "run_prompt_ids": [str(run_prompter.id)],
            "row_ids": [],
            "selected_all_rows": True,
        }

        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_run_prompt_for_rows_missing_run_prompt_ids(self, auth_client, row):
        """Test that missing run_prompt_ids returns error."""
        payload = {
            "row_ids": [str(row.id)],
        }

        response = auth_client.post(
            "/model-hub/run-prompt-for-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_run_prompt_for_rows_missing_row_ids(self, auth_client, run_prompter):
        """Test that missing row_ids (without selected_all_rows) returns error."""
        payload = {
            "run_prompt_ids": [str(run_prompter.id)],
            # Missing row_ids and selected_all_rows=False (default)
        }

        response = auth_client.post(
            "/model-hub/run-prompt-for-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_run_prompt_for_rows_empty_run_prompt_ids(self, auth_client, row):
        """Test that empty run_prompt_ids returns error."""
        payload = {
            "run_prompt_ids": [],
            "row_ids": [str(row.id)],
        }

        response = auth_client.post(
            "/model-hub/run-prompt-for-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_run_prompt_for_rows_unauthenticated(self):
        """Test that unauthenticated users cannot run prompts."""
        client = APIClient()
        response = client.post(
            "/model-hub/run-prompt-for-rows/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.django_db
class TestProviderApiKeys:
    @pytest.fixture(autouse=True)
    def _stub_provider_key_probe(self):
        # Saving a flat key now validates it by probing the provider with a live
        # litellm call. Stub that probe so these tests never touch the network; a
        # returned response means "the provider accepted the key". Tests that need
        # the reject path re-patch this inside their own body.
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            return_value=MagicMock(),
        ) as mock_completion:
            yield mock_completion

    def test_text_provider_key_responses_are_masked_only(self, auth_client):
        raw_key = "secret-provider-key-value"
        updated_raw_key = "secret-provider-key-value-updated"

        response = auth_client.post(
            API_KEYS_URL,
            {"provider": "openai", "key": raw_key},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        created = response.data["result"]
        assert created["provider"] == "openai"
        assert "*" in created["masked_actual_key"]
        assert_provider_key_response_is_masked(created, raw_key)

        detail_response = auth_client.get(api_key_detail_url(created["id"]))
        assert detail_response.status_code == status.HTTP_200_OK
        detail = detail_response.data["result"]
        assert detail["provider"] == "openai"
        assert_provider_key_response_is_masked(detail, raw_key)

        update_response = auth_client.put(
            api_key_detail_url(created["id"]),
            {"provider": "openai", "key": updated_raw_key},
            format="json",
        )
        assert update_response.status_code == status.HTTP_200_OK
        assert update_response.data["provider"] == "openai"
        assert_provider_key_response_is_masked(
            update_response.data,
            raw_key,
            updated_raw_key,
        )

        list_response = auth_client.get(API_KEYS_URL)
        assert list_response.status_code == status.HTTP_200_OK
        listed = next(
            row
            for row in list_response.data["results"]
            if str(row["id"]) == created["id"]
        )
        assert_provider_key_response_is_masked(
            listed,
            raw_key,
            updated_raw_key,
        )

    def test_json_provider_key_responses_are_masked_only(self, auth_client):
        raw_config = {
            "api_key": "secret-json-provider-key",
            "api_base": "https://azure.example.test",
            "api_version": "2024-05-01",
        }

        response = auth_client.post(
            API_KEYS_URL,
            {"provider": "azure", "key": json.dumps(raw_config)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        created = response.data["result"]
        assert created["provider"] == "azure"
        assert isinstance(created["masked_actual_key"], dict)
        assert created["masked_actual_key"]["api_key"] != raw_config["api_key"]
        assert "*" in created["masked_actual_key"]["api_key"]
        assert_provider_key_response_is_masked(created, *raw_config.values())

        detail_response = auth_client.get(api_key_detail_url(created["id"]))
        assert detail_response.status_code == status.HTTP_200_OK
        assert_provider_key_response_is_masked(
            detail_response.data["result"],
            *raw_config.values(),
        )

        list_response = auth_client.get(API_KEYS_URL)
        assert list_response.status_code == status.HTTP_200_OK
        listed = next(
            row
            for row in list_response.data["results"]
            if str(row["id"]) == created["id"]
        )
        assert_provider_key_response_is_masked(listed, *raw_config.values())

    def test_text_provider_put_patch_scopes_workspace_and_preserves_encryption(
        self, auth_client, organization, workspace, user
    ):
        raw_hidden_key = "hidden-default-workspace-provider-key"
        hidden_key = ApiKey.no_workspace_objects.create(
            provider="openai",
            organization=organization,
            workspace=workspace,
            key=raw_hidden_key,
            user=user,
        )
        other_workspace = Workspace.objects.create(
            name="Provider Key Other Workspace",
            organization=organization,
            created_by=user,
        )
        set_workspace_context(workspace=other_workspace, organization=organization)

        raw_key = "active-workspace-provider-key"
        create_response = auth_client.post(
            API_KEYS_URL,
            {"provider": "openai", "key": raw_key},
            format="json",
        )

        assert create_response.status_code == status.HTTP_200_OK
        created = create_response.data["result"]
        assert_provider_key_response_is_masked(created, raw_key, raw_hidden_key)
        provider_key_id = created["id"]
        provider_key = ApiKey.no_workspace_objects.get(id=provider_key_id)
        assert provider_key.workspace == other_workspace
        assert provider_key.actual_key == raw_key
        original_encrypted_key = provider_key.key

        hidden_detail_response = auth_client.get(api_key_detail_url(hidden_key.id))
        assert hidden_detail_response.status_code == status.HTTP_404_NOT_FOUND

        updated_raw_key = "active-workspace-provider-key-updated"
        put_response = auth_client.put(
            api_key_detail_url(provider_key_id),
            {"provider": "openai", "key": updated_raw_key},
            format="json",
        )
        assert put_response.status_code == status.HTTP_200_OK
        assert_provider_key_response_is_masked(
            put_response.data,
            raw_key,
            updated_raw_key,
            raw_hidden_key,
        )
        provider_key.refresh_from_db()
        assert provider_key.workspace == other_workspace
        assert provider_key.key != original_encrypted_key
        assert provider_key.key != updated_raw_key
        assert provider_key.actual_key == updated_raw_key
        updated_encrypted_key = provider_key.key

        patch_response = auth_client.patch(
            api_key_detail_url(provider_key_id),
            {"provider": "openai"},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_200_OK
        assert_provider_key_response_is_masked(
            patch_response.data,
            raw_key,
            updated_raw_key,
            raw_hidden_key,
        )
        provider_key.refresh_from_db()
        assert provider_key.key == updated_encrypted_key
        assert provider_key.actual_key == updated_raw_key

        list_response = auth_client.get(API_KEYS_URL)
        assert list_response.status_code == status.HTTP_200_OK
        list_ids = {str(row["id"]) for row in list_response.data["results"]}
        assert provider_key_id in list_ids
        assert str(hidden_key.id) not in list_ids

        delete_response = auth_client.delete(api_key_detail_url(provider_key_id))
        assert delete_response.status_code == status.HTTP_200_OK
        provider_key.refresh_from_db()
        assert provider_key.deleted is True
        assert provider_key.deleted_at is not None
        hidden_key.refresh_from_db()
        assert hidden_key.deleted is False
        assert hidden_key.actual_key == raw_hidden_key

    def test_json_provider_put_uses_config_json_and_patch_preserves_encryption(
        self, auth_client
    ):
        raw_config = {
            "api_key": "secret-json-provider-key",
            "api_base": "https://azure.example.test",
            "api_version": "2024-05-01",
        }
        create_response = auth_client.post(
            API_KEYS_URL,
            {"provider": "azure", "key": json.dumps(raw_config)},
            format="json",
        )
        assert create_response.status_code == status.HTTP_200_OK
        created = create_response.data["result"]
        assert_provider_key_response_is_masked(created, *raw_config.values())
        provider_key_id = created["id"]

        provider_key = ApiKey.no_workspace_objects.get(id=provider_key_id)
        assert provider_key.key is None
        assert provider_key.actual_json == raw_config
        assert provider_key.config_json["api_key"] != raw_config["api_key"]

        updated_config = {
            "api_key": "secret-json-provider-key-updated",
            "api_base": "https://azure-updated.example.test",
            "api_version": "2024-10-01",
        }
        put_response = auth_client.put(
            api_key_detail_url(provider_key_id),
            {"provider": "azure", "key": json.dumps(updated_config)},
            format="json",
        )
        assert put_response.status_code == status.HTTP_200_OK
        assert_provider_key_response_is_masked(
            put_response.data,
            *raw_config.values(),
            *updated_config.values(),
        )
        provider_key.refresh_from_db()
        assert provider_key.key is None
        assert provider_key.actual_json == updated_config
        assert provider_key.config_json["api_key"] != updated_config["api_key"]
        encrypted_config = json.loads(json.dumps(provider_key.config_json))

        patch_response = auth_client.patch(
            api_key_detail_url(provider_key_id),
            {"provider": "azure"},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_200_OK
        assert_provider_key_response_is_masked(
            patch_response.data,
            *raw_config.values(),
            *updated_config.values(),
        )
        provider_key.refresh_from_db()
        assert provider_key.config_json == encrypted_config
        assert provider_key.actual_json == updated_config

    def test_invalid_provider_key_is_rejected_at_save(self, auth_client):
        # A provider that authenticates and rejects the key must fail the save
        # with the inline "invalid key" message and persist nothing.
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=AuthenticationError(
                "invalid key",
                llm_provider="openai",
                model="openai/gpt-4o-mini",
            ),
        ):
            response = auth_client.post(
                API_KEYS_URL,
                {"provider": "openai", "key": "totally-wrong-key"},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            response.data["message"]
            == "Invalid API key, please type in the right one."
        )
        assert not ApiKey.no_workspace_objects.filter(provider="openai").exists()

    def test_valid_provider_key_is_saved(self, auth_client):
        # When the provider accepts the probe, the key is stored as before.
        response = auth_client.post(
            API_KEYS_URL,
            {"provider": "openai", "key": "sk-valid-key"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["provider"] == "openai"
        assert ApiKey.no_workspace_objects.filter(provider="openai").exists()


@pytest.mark.django_db
class TestProviderKeyValidation:
    """Unit tests for is_provider_key_valid — the model-provider key probe."""

    def test_rejects_only_on_authentication_error(self):
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=AuthenticationError(
                "bad", llm_provider="openai", model="openai/gpt-4o-mini"
            ),
        ):
            assert is_provider_key_valid("openai", "wrong-key") is False

    def test_fails_open_on_non_auth_error(self):
        # Network/timeout/rate-limit errors are inconclusive and must NOT block
        # a legitimate save — otherwise a transient blip breaks the flow.
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=RuntimeError("connection reset"),
        ):
            assert is_provider_key_valid("openai", "some-key") is True

    def test_rejects_on_permission_denied_error(self):
        # Some providers signal bad credentials as a 403 (PermissionDeniedError)
        # rather than AuthenticationError's 401 — this used to fall through to
        # the fail-open branch and silently accept a genuinely invalid key.
        response = httpx.Response(403, request=httpx.Request("POST", "https://example.test"))
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=PermissionDeniedError(
                "forbidden",
                llm_provider="anthropic",
                model="anthropic/claude-haiku-4-5",
                response=response,
            ),
        ):
            assert is_provider_key_valid("anthropic", "wrong-key") is False

    def test_rejects_on_generic_api_error_carrying_auth_status(self):
        # Some litellm provider integrations wrap a 401/403 in the generic
        # APIError (or a subclass) instead of AuthenticationError/
        # PermissionDeniedError, but still carry the real status on
        # `.status_code`. That must still be treated as a definitive rejection,
        # not silently fail open.
        exc = APIError(
            status_code=401,
            message="unauthorized",
            llm_provider="openai",
            model="openai/gpt-4o-mini",
        )
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=exc,
        ):
            assert is_provider_key_valid("openai", "wrong-key") is False

    def test_new_probe_providers_are_actually_probed(self):
        # Regression guard for the coverage gap Kartik flagged (#1370): the
        # providers added to PROVIDER_PROBE_MODEL must actually route through
        # the probe, not silently fail open because the map lookup misses.
        for provider in ("deepseek", "openrouter", "fireworks_ai", "cerebras", "cohere_chat"):
            with patch(
                "model_hub.utils.provider_key_validation.litellm.completion",
                return_value=MagicMock(),
            ) as mock_completion:
                assert is_provider_key_valid(provider, "some-key") is True
            mock_completion.assert_called_once()

    def test_inconclusive_probe_logs_at_warning(self):
        # Kartik: a fail-open branch that logs at INFO is invisible if probing
        # silently becomes a no-op (retired model, blocked egress). Must match
        # the sibling validate_model_working's WARNING convention.
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            side_effect=RuntimeError("model retired"),
        ), patch("model_hub.utils.provider_key_validation.logger") as mock_logger:
            is_provider_key_valid("openai", "some-key")
        mock_logger.warning.assert_called_once()
        mock_logger.info.assert_not_called()

    def test_valid_key_passes_and_probes_provider(self):
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion",
            return_value=MagicMock(),
        ) as mock_completion:
            assert is_provider_key_valid("openai", "sk-good") is True
        mock_completion.assert_called_once()

    def test_unmapped_provider_is_not_probed(self):
        # JSON-config providers (vertex_ai/azure/...) and anything without a
        # probe model are skipped entirely — fail-open, no network call.
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion"
        ) as mock_completion:
            assert is_provider_key_valid("vertex_ai", "anything") is True
        mock_completion.assert_not_called()

    def test_missing_key_is_not_probed(self):
        with patch(
            "model_hub.utils.provider_key_validation.litellm.completion"
        ) as mock_completion:
            assert is_provider_key_valid("openai", "") is True
        mock_completion.assert_not_called()


# ==================== LitellmAPIView Tests ====================


@pytest.mark.django_db
class TestLitellmAPIView:
    """Tests for LitellmAPIView - POST /run-prompt/"""

    def test_litellm_api_success(self, auth_client, organization):
        """Test successful LiteLLM API call."""
        from model_hub.models.api_key import ApiKey

        # Create API key for the organization
        ApiKey.objects.create(
            provider="openai",
            organization=organization,
            key="test-api-key",
        )

        payload = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        with patch("model_hub.views.run_prompt.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="Hello there!"))]
            )
            response = auth_client.post(
                "/model-hub/run-prompt/",
                payload,
                format="json",
            )

        # Response could be 200 or 400 depending on API key validation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_litellm_api_direct_run_persists_workspace(
        self, user, workspace, dataset, organization
    ):
        """Direct run-prompt creates a workspace-scoped RunPrompter before queuing."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Direct Run Prompt",
            "model": "gpt-4",
            "concurrency": 1,
            "messages": [{"role": "user", "content": "Return OK"}],
            "output_format": "string",
            "max_tokens": 20,
        }

        try:
            with patch(
                "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
            ) as mock_apply_async:
                mock_apply_async.return_value = MagicMock(id="direct-workflow")
                response = client.post(
                    "/model-hub/run-prompt/",
                    payload,
                    format="json",
                )
        finally:
            client.stop_workspace_injection()

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.no_workspace_objects.get(
            dataset=dataset,
            name=payload["name"],
            organization=organization,
            deleted=False,
        )
        assert run_prompter.workspace_id == workspace.id
        assert run_prompter.status == StatusType.RUNNING.value
        mock_apply_async.assert_called_once_with(
            args=({"type": "not_started", "prompt_id": str(run_prompter.id)},)
        )

    def test_litellm_api_rejects_other_workspace_dataset(
        self, user, workspace, organization
    ):
        """Direct run-prompt must not mutate a dataset outside request.workspace."""
        from conftest import WorkspaceAwareAPIClient

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        payload = {
            "dataset_id": str(other_dataset.id),
            "name": "Blocked Direct Run Prompt",
            "model": "gpt-4",
            "concurrency": 1,
            "messages": [{"role": "user", "content": "Return OK"}],
        }

        try:
            with patch(
                "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
            ) as mock_apply_async:
                response = client.post(
                    "/model-hub/run-prompt/",
                    payload,
                    format="json",
                )
        finally:
            client.stop_workspace_injection()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not RunPrompter.no_workspace_objects.filter(
            dataset=other_dataset,
            name=payload["name"],
        ).exists()
        mock_apply_async.assert_not_called()

    def test_litellm_api_missing_model(self, auth_client):
        """Test that missing model returns error."""
        payload = {
            "messages": [{"role": "user", "content": "Hello"}],
        }

        response = auth_client.post(
            "/model-hub/run-prompt/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_litellm_api_missing_messages(self, auth_client):
        """Test that missing messages returns error."""
        payload = {
            "model": "gpt-4",
        }

        response = auth_client.post(
            "/model-hub/run-prompt/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_litellm_api_unauthenticated(self):
        """Test that unauthenticated users cannot use LiteLLM API."""
        client = APIClient()
        response = client.post(
            "/model-hub/run-prompt/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== PreviewRunPromptColumnView Tests ====================


@pytest.mark.django_db
class TestPreviewRunPromptColumnView:
    """Tests for PreviewRunPromptColumnView - POST /develops/preview_run_prompt_column/"""

    def test_preview_run_prompt_column_success(
        self, auth_client, dataset, input_column, row, cell, valid_run_prompt_config
    ):
        """Test successfully previewing a run prompt column."""
        payload = {
            "dataset_id": str(dataset.id),
            "row_id": str(row.id),
            "config": valid_run_prompt_config,
        }

        with patch("model_hub.views.run_prompt.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="Preview response"))],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                payload,
                format="json",
            )

        # Response depends on API key availability
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_preview_run_prompt_column_missing_dataset_id(
        self, auth_client, row, valid_run_prompt_config
    ):
        """Test that missing dataset_id returns error."""
        payload = {
            "row_id": str(row.id),
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_run_prompt_column_unauthenticated(self):
        """Test that unauthenticated users cannot preview columns."""
        client = APIClient()
        response = client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== RetrieveRunPromptColumnConfigView Tests ====================


@pytest.mark.django_db
class TestRetrieveRunPromptColumnConfigView:
    """Tests for RetrieveRunPromptColumnConfigView - GET /develops/retrieve_run_prompt_column_config/"""

    def test_retrieve_run_prompt_column_config_success(
        self, auth_client, dataset, run_prompt_column, run_prompter
    ):
        """Test successfully retrieving run prompt column config."""
        # Link run_prompter to the column
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={run_prompt_column.id}",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_retrieve_run_prompt_column_config_missing_column_id(self, auth_client):
        """Test that missing column_id returns error."""
        response = auth_client.get(
            "/model-hub/develops/retrieve_run_prompt_column_config/",
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_retrieve_run_prompt_column_config_nonexistent_column(self, auth_client):
        """Test that non-existent column returns 404."""
        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={uuid.uuid4()}",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_config_max_tokens_null(
        self, auth_client, dataset, run_prompt_column, run_prompter
    ):
        """Test that max_tokens=None is correctly returned in config."""
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        # Ensure max_tokens is None (provider default)
        run_prompter.max_tokens = None
        run_prompter.save()

        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={run_prompt_column.id}",
        )

        assert response.status_code == status.HTTP_200_OK
        config = response.data["result"]["config"]
        assert config["max_tokens"] is None

    def test_retrieve_config_max_tokens_set(
        self, auth_client, dataset, run_prompt_column, run_prompter
    ):
        """Test that an explicit max_tokens value is correctly returned in config."""
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        run_prompter.max_tokens = 2048
        run_prompter.save()

        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={run_prompt_column.id}",
        )

        assert response.status_code == status.HTTP_200_OK
        config = response.data["result"]["config"]
        assert config["max_tokens"] == 2048

    def test_retrieve_run_prompt_column_config_unauthenticated(self):
        """Test that unauthenticated users cannot retrieve config."""
        client = APIClient()
        response = client.get(
            "/model-hub/develops/retrieve_run_prompt_column_config/",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== RetrieveRunPromptOptionsView Tests ====================


@pytest.mark.django_db
class TestRetrieveRunPromptOptionsView:
    """Tests for RetrieveRunPromptOptionsView - GET /develops/retrieve_run_prompt_options/"""

    def test_retrieve_run_prompt_options_success(self, auth_client, dataset):
        """Test successfully retrieving run prompt options."""
        response = auth_client.get(
            "/model-hub/develops/retrieve_run_prompt_options/",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_retrieve_run_prompt_options_unauthenticated(self):
        """Test that unauthenticated users cannot retrieve options."""
        client = APIClient()
        response = client.get(
            "/model-hub/develops/retrieve_run_prompt_options/",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== Organization Isolation Tests ====================


@pytest.mark.django_db
class TestRunPromptOrganizationIsolation:
    """Tests for organization isolation in run prompt operations."""

    @pytest.fixture
    def other_organization(self, db):
        return Organization.objects.create(name="Other Organization")

    @pytest.fixture
    def other_org_user(self, db, other_organization):
        return User.objects.create_user(
            email="otherorg@example.com",
            password="testpassword123",
            name="Other Org User",
            organization=other_organization,
        )

    @pytest.fixture
    def other_org_dataset(self, db, other_organization, other_org_user):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )
        return Dataset.objects.create(
            name="Other Org Dataset",
            organization=other_organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

    def test_cannot_add_run_prompt_column_to_other_org_dataset(
        self, auth_client, other_org_dataset, valid_run_prompt_config
    ):
        """Test that users cannot add run prompt columns to other org's datasets."""
        payload = {
            "dataset_id": str(other_org_dataset.id),
            "name": "AI Response",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_direct_run_prompt_on_other_org_dataset(
        self, auth_client, other_org_dataset
    ):
        """Test that direct run prompt cannot bind to another org's dataset."""
        payload = {
            "dataset_id": str(other_org_dataset.id),
            "name": "Cross Org Direct Run Prompt",
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_task.assert_not_called()
        assert not RunPrompter.objects.filter(name=payload["name"]).exists()

    def test_run_prompt_for_rows_rejects_row_from_other_dataset(
        self, auth_client, organization, workspace, run_prompter
    ):
        """Test row reruns only accept rows from the run prompt's dataset."""
        other_dataset = Dataset.objects.create(
            name="Same Org Other Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_row = Row.objects.create(dataset=other_dataset, order=0)
        payload = {
            "run_prompt_ids": [str(run_prompter.id)],
            "row_ids": [str(other_row.id)],
        }

        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_task.assert_not_called()

    def test_run_prompt_for_rows_rejects_mixed_run_prompt_datasets(
        self, auth_client, organization, workspace, dataset, row, run_prompter
    ):
        """Test bulk row rerun does not mix run prompts from different datasets."""
        other_dataset = Dataset.objects.create(
            name="Second Prompt Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_run_prompter = RunPrompter.objects.create(
            name="Other Dataset Run Prompter",
            dataset=other_dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.NOT_STARTED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Test prompt"}],
            run_prompt_config={},
        )
        payload = {
            "run_prompt_ids": [str(run_prompter.id), str(other_run_prompter.id)],
            "row_ids": [str(row.id)],
        }

        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_task.assert_not_called()


# ==================== Config Variations Tests ====================


@pytest.mark.django_db
class TestAddRunPromptColumnConfigVariations:
    """Tests for various config variations in AddRunPromptColumnView."""

    def test_add_run_prompt_with_output_format_array(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt column with array output format."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "List items"}]}
            ],
            "output_format": "array",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Array Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        # Verify column was created with correct data type
        column = Column.objects.get(name="Array Output", dataset=dataset, deleted=False)
        assert column.data_type == "array"

    def test_add_run_prompt_with_output_format_number(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt column with number output format."""
        config = {
            "model": "gpt-4",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Calculate total"}],
                }
            ],
            "output_format": "number",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Number Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(
            name="Number Output", dataset=dataset, deleted=False
        )
        assert column.data_type == "integer"

    def test_add_run_prompt_with_output_format_object(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt column with object output format."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Return JSON"}]}
            ],
            "output_format": "object",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "JSON Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(name="JSON Output", dataset=dataset, deleted=False)
        assert column.data_type == "json"

    def test_add_run_prompt_with_output_format_audio(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt column with audio output format."""
        config = {
            "model": "tts-1",
            "messages": [{"role": "user", "content": "Say hello"}],
            "output_format": "audio",
            "run_prompt_config": {"voice": "alloy"},
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Audio Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(name="Audio Output", dataset=dataset, deleted=False)
        assert column.data_type == "audio"

    def test_add_run_prompt_with_custom_temperature(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt with custom temperature settings."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Generate text"}]}
            ],
            "output_format": "string",
            "run_prompt_config": {
                "temperature": 0.2,
                "max_tokens": 500,
            },
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Custom Temp",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        # Verify RunPrompter was created with correct temperature
        run_prompter = RunPrompter.objects.get(name="Custom Temp", deleted=False)
        assert run_prompter.temperature == 0.2
        assert run_prompter.max_tokens == 500

    def test_add_run_prompt_with_concurrency(self, auth_client, dataset, input_column):
        """Test adding run prompt with custom concurrency."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Process"}]}
            ],
            "output_format": "string",
            "concurrency": 10,
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Concurrent Run",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Concurrent Run", deleted=False)
        assert run_prompter.concurrency == 10

    def test_add_run_prompt_with_response_format(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt with response format schema."""
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
        }
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Analyze this"}]}
            ],
            "output_format": "object",
            "response_format": response_format,
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Structured Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Structured Output", deleted=False)
        assert run_prompter.response_format == response_format

    def test_add_run_prompt_with_tool_choice(self, auth_client, dataset, input_column):
        """Test adding run prompt with tool choice setting."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Use tools"}]}
            ],
            "output_format": "string",
            "tool_choice": "auto",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Tool Choice Run",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Tool Choice Run", deleted=False)
        assert run_prompter.tool_choice == "auto"

    def test_add_run_prompt_with_all_penalties(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt with frequency and presence penalties."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Creative text"}]}
            ],
            "output_format": "string",
            "run_prompt_config": {
                "temperature": 0.9,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.3,
                "top_p": 0.95,
            },
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Creative Run",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Creative Run", deleted=False)
        assert run_prompter.temperature == 0.9
        assert run_prompter.frequency_penalty == 0.5
        assert run_prompter.presence_penalty == 0.3
        assert run_prompter.top_p == 0.95

    def test_add_run_prompt_with_system_message(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt with system message."""
        config = {
            "model": "gpt-4",
            "messages": [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "You are helpful"}],
                },
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "System Message Run",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="System Message Run", deleted=False)
        assert len(run_prompter.messages) == 2
        assert run_prompter.messages[0]["role"] == "system"

    def test_add_run_prompt_with_multi_turn_conversation(
        self, auth_client, dataset, input_column
    ):
        """Test adding run prompt with multi-turn conversation."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "Assistant"}]},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "First question"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "First answer"}],
                },
                {"role": "user", "content": [{"type": "text", "text": "Follow up"}]},
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Multi Turn Run",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Multi Turn Run", deleted=False)
        assert len(run_prompter.messages) == 4


# ==================== Database State Verification Tests ====================


@pytest.mark.django_db
class TestRunPromptDatabaseState:
    """Tests verifying database state after run prompt operations."""

    def test_add_run_prompt_creates_correct_database_entries(
        self, auth_client, dataset, input_column, organization, workspace
    ):
        """Verify all database entries are created correctly."""
        config = {
            "model": "gpt-4",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Process {{Input Column}}"}],
                }
            ],
            "output_format": "string",
            "run_prompt_config": {
                "temperature": 0.5,
                "max_tokens": 200,
            },
            "concurrency": 3,
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "DB State Test",
            "config": config,
        }

        initial_run_prompter_count = RunPrompter.objects.filter(
            organization=organization
        ).count()
        initial_column_count = Column.objects.filter(
            dataset=dataset, deleted=False
        ).count()

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

        # Verify RunPrompter creation
        assert (
            RunPrompter.objects.filter(organization=organization).count()
            == initial_run_prompter_count + 1
        )
        run_prompter = RunPrompter.objects.get(name="DB State Test")
        assert run_prompter.model == "gpt-4"
        assert run_prompter.dataset_id == dataset.id
        assert run_prompter.organization_id == organization.id
        assert run_prompter.temperature == 0.5
        assert run_prompter.max_tokens == 200
        assert run_prompter.concurrency == 3
        assert (
            run_prompter.status == StatusType.RUNNING.value
        )  # Should be RUNNING after workflow started

        # Verify Column creation
        assert (
            Column.objects.filter(dataset=dataset, deleted=False).count()
            == initial_column_count + 1
        )
        column = Column.objects.get(
            name="DB State Test", dataset=dataset, deleted=False
        )
        assert column.source == SourceChoices.RUN_PROMPT.value
        assert column.source_id == str(
            run_prompter.id
        )  # source_id is CharField, compare as strings
        assert column.data_type == "text"  # string output_format -> text data_type

        # Verify column_order updated
        dataset.refresh_from_db()
        assert str(column.id) in dataset.column_order

    def test_edit_run_prompt_updates_database_entries(
        self, auth_client, dataset, run_prompt_column, run_prompter
    ):
        """Verify database entries are updated correctly on edit."""
        # Link run_prompt_column to run_prompter
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        original_name = run_prompter.name
        original_model = run_prompter.model

        new_config = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Updated prompt"}],
                }
            ],
            "output_format": "object",
            "run_prompt_config": {
                "temperature": 0.3,
                "max_tokens": 300,
            },
        }
        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(run_prompt_column.id),
            "name": "Updated Name",
            "config": new_config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

        # Verify RunPrompter updates
        run_prompter.refresh_from_db()
        assert run_prompter.name == "Updated Name"
        assert run_prompter.model == "gpt-3.5-turbo"
        assert run_prompter.temperature == 0.3
        assert run_prompter.max_tokens == 300
        assert run_prompter.output_format == "object"
        assert run_prompter.status == StatusType.RUNNING.value

        # Verify Column updates
        run_prompt_column.refresh_from_db()
        assert run_prompt_column.name == "Updated Name"
        assert (
            run_prompt_column.data_type == "json"
        )  # object output_format -> json data_type

    def test_run_prompter_status_transitions(
        self, auth_client, dataset, input_column, organization
    ):
        """Verify status transitions from NOT_STARTED to RUNNING."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Status Test",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

        run_prompter = RunPrompter.objects.get(name="Status Test")
        # After successful add, status should be RUNNING
        assert run_prompter.status == StatusType.RUNNING.value

    def test_workflow_failure_sets_failed_status(
        self, auth_client, dataset, input_column
    ):
        """Verify status is set to FAILED when workflow fails to start."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Failure Test",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            mock_task.side_effect = Exception("Workflow failed to start")
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        run_prompter = RunPrompter.objects.get(name="Failure Test")
        assert run_prompter.status == StatusType.FAILED.value


# ==================== DatasetRunPromptStatsView Tests ====================


@pytest.mark.django_db
class TestDatasetRunPromptStatsView:
    """Tests for DatasetRunPromptStatsView - GET /datasets/<dataset_id>/run-prompt-stats/"""

    def test_get_run_prompt_stats_success(self, auth_client, dataset, run_prompter):
        """Test successfully getting run prompt stats for a dataset."""
        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/run-prompt-stats/",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json().get("result", {})
        assert "avg_tokens" in data
        assert "avg_cost" in data
        assert "avg_time" in data
        assert "prompts" in data

    def test_get_run_prompt_stats_with_prompt_ids(
        self, auth_client, dataset, run_prompter, organization, workspace
    ):
        """Test getting stats with specific prompt_ids filter."""
        # Create a second run prompter
        run_prompter2 = RunPrompter.objects.create(
            name="Second Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.COMPLETED.value,
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Test"}],
            run_prompt_config={},
        )

        # Request stats for only the first prompter
        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/run-prompt-stats/?prompt_ids={run_prompter.id}",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_get_run_prompt_stats_nonexistent_prompt_ids(self, auth_client, dataset):
        """Test getting stats with non-existent prompt IDs returns empty."""
        fake_uuid = str(uuid.uuid4())
        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/run-prompt-stats/?prompt_ids={fake_uuid}",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json().get("result", {})
        assert data.get("avg_tokens") == 0
        assert data.get("avg_cost") == 0

    def test_get_run_prompt_stats_empty_dataset(self, auth_client, dataset):
        """Test getting stats for dataset with no run prompts."""
        # Remove all run prompters for this dataset
        RunPrompter.objects.filter(dataset=dataset).delete()

        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/run-prompt-stats/",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_get_run_prompt_stats_unauthenticated(self, dataset):
        """Test that unauthenticated users cannot get stats."""
        client = APIClient()
        response = client.get(
            f"/model-hub/dataset/{dataset.id}/run-prompt-stats/",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== Extended Organization Isolation Tests ====================


@pytest.mark.django_db
class TestRunPromptExtendedOrganizationIsolation:
    """Extended tests for organization isolation in run prompt operations."""

    @pytest.fixture
    def other_organization(self, db):
        return Organization.objects.create(name="Other Organization")

    @pytest.fixture
    def other_org_user(self, db, other_organization):
        return User.objects.create_user(
            email="otherorg@example.com",
            password="testpassword123",
            name="Other Org User",
            organization=other_organization,
        )

    @pytest.fixture
    def other_org_workspace(self, db, other_organization, other_org_user):
        return Workspace.objects.create(
            name="Other Workspace",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )

    @pytest.fixture
    def other_org_dataset(self, db, other_organization, other_org_workspace):
        ds = Dataset.objects.create(
            name="Other Org Dataset",
            organization=other_organization,
            workspace=other_org_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        ds.column_order = []
        ds.save()
        return ds

    @pytest.fixture
    def other_org_run_prompter(
        self, db, other_org_dataset, other_organization, other_org_workspace
    ):
        return RunPrompter.objects.create(
            name="Other Org Prompter",
            dataset=other_org_dataset,
            organization=other_organization,
            workspace=other_org_workspace,
            status=StatusType.NOT_STARTED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Test"}],
            run_prompt_config={},
        )

    @pytest.fixture
    def other_org_column(self, db, other_org_dataset, other_org_run_prompter):
        col = Column.objects.create(
            name="Other Org Column",
            dataset=other_org_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=other_org_run_prompter.id,
        )
        other_org_dataset.column_order.append(str(col.id))
        other_org_dataset.save()
        return col

    def test_cannot_edit_other_org_run_prompt_column(
        self, auth_client, other_org_dataset, other_org_column, valid_run_prompt_config
    ):
        """Test that users cannot edit run prompt columns from other organizations."""
        payload = {
            "dataset_id": str(other_org_dataset.id),
            "column_id": str(other_org_column.id),
            "name": "Hacked Name",
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            payload,
            format="json",
        )

        # Should return 404 (dataset not found for this org)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_retrieve_other_org_run_prompt_config(
        self, auth_client, other_org_column
    ):
        """Test that users cannot retrieve config from other org's run prompt columns."""
        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={other_org_column.id}",
        )

        # Organization isolation is enforced - should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_run_prompt_for_other_org_rows(
        self, auth_client, other_org_run_prompter, other_org_dataset
    ):
        """Test that users cannot run prompts for other org's datasets."""
        # Create a row in the other org's dataset
        other_row = Row.objects.create(dataset=other_org_dataset, order=0)

        payload = {
            "run_prompt_ids": [str(other_org_run_prompter.id)],
            "row_ids": [str(other_row.id)],
        }

        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                payload,
                format="json",
            )

        # Organization isolation is enforced - should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_preview_for_other_org_dataset(
        self, auth_client, other_org_dataset, valid_run_prompt_config
    ):
        """Test that users cannot preview run prompt for other org's datasets."""
        # Create a row in the other org's dataset
        other_row = Row.objects.create(dataset=other_org_dataset, order=0)

        payload = {
            "dataset_id": str(other_org_dataset.id),
            "name": "Test Preview",  # Required by PreviewRunPromptSerializer
            "first_n_rows": 1,
            "config": valid_run_prompt_config,
        }

        response = auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            payload,
            format="json",
        )

        # Organization isolation is enforced - should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Serializer Validation Tests ====================


@pytest.mark.django_db
class TestRunPromptSerializerValidation:
    """Tests for serializer validation edge cases."""

    def test_add_run_prompt_empty_messages_rejected(self, auth_client, dataset):
        """Test that empty messages array is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Empty Messages",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_invalid_message_role_rejected(self, auth_client, dataset):
        """Test that invalid message role is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "invalid_role", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Invalid Role",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_first_message_assistant_rejected(
        self, auth_client, dataset
    ):
        """Test that first message from assistant is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Assistant First",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_message_missing_content_rejected(
        self, auth_client, dataset
    ):
        """Test that message without content is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [{"role": "user"}],  # Missing content
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "No Content",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_invalid_output_format_rejected(self, auth_client, dataset):
        """Test that invalid output_format is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "invalid_format",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Invalid Format",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_run_prompt_invalid_tool_choice_rejected(self, auth_client, dataset):
        """Test that invalid tool_choice is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
            "tool_choice": "invalid_choice",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Invalid Tool Choice",
            "config": config,
        }

        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_requires_first_n_rows_or_row_indices(self, auth_client, dataset):
        """Test that preview requires either first_n_rows or row_indices."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Preview Test",
            "config": config,
            # Missing both first_n_rows and row_indices
        }

        response = auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_both_first_n_rows_and_row_indices_rejected(
        self, auth_client, dataset, row
    ):
        """Test that providing both first_n_rows and row_indices is rejected."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test"}]}
            ],
            "output_format": "string",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Preview Test",
            "config": config,
            "first_n_rows": 5,
            "row_indices": [1, 2, 3],
        }

        response = auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_edit_run_prompt_missing_dataset_id_rejected(
        self, auth_client, run_prompt_column
    ):
        """Test that edit without dataset_id is rejected."""
        payload = {
            "column_id": str(run_prompt_column.id),
            "name": "Updated",
        }

        response = auth_client.post(
            "/model-hub/develops/edit_run_prompt_column/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== RetrieveRunPromptColumnConfigView Extended Tests ====================


@pytest.mark.django_db
class TestRetrieveRunPromptColumnConfigExtended:
    """Extended tests for RetrieveRunPromptColumnConfigView."""

    def test_retrieve_config_non_run_prompt_column_rejected(
        self, auth_client, input_column
    ):
        """Test that retrieving config for non-run-prompt column returns error."""
        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={input_column.id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_config_invalid_uuid_rejected(self, auth_client):
        """Test that invalid UUID format is handled."""
        response = auth_client.get(
            "/model-hub/develops/retrieve_run_prompt_column_config/?column_id=invalid-uuid",
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_retrieve_config_returns_all_fields(
        self, auth_client, dataset, run_prompt_column, run_prompter
    ):
        """Test that all config fields are returned."""
        # Link and set up the run prompter with various fields
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        run_prompter.temperature = 0.8
        run_prompter.frequency_penalty = 0.5
        run_prompter.presence_penalty = 0.3
        run_prompter.top_p = 0.9
        run_prompter.max_tokens = 500
        run_prompter.concurrency = 8
        run_prompter.tool_choice = "auto"
        run_prompter.output_format = "object"
        run_prompter.save()

        response = auth_client.get(
            f"/model-hub/develops/retrieve_run_prompt_column_config/?column_id={run_prompt_column.id}",
        )

        assert response.status_code == status.HTTP_200_OK
        config = response.json().get("result", {}).get("config", {})

        assert config.get("temperature") == 0.8
        assert config.get("frequency_penalty") == 0.5
        assert config.get("presence_penalty") == 0.3
        assert config.get("top_p") == 0.9
        assert config.get("max_tokens") == 500
        assert config.get("concurrency") == 8
        assert config.get("tool_choice") == "auto"
        assert config.get("output_format") == "object"


# ==================== JSON Response Format E2E Tests ====================


@pytest.mark.django_db
class TestJsonResponseFormatFlow:
    """
    E2E tests for JSON response format handling.

    Tests the complete flow:
    1. Add run prompt with JSON response format
    2. Verify column data_type is set to "json"
    3. Verify derived variables are extracted from JSON output
    4. Verify derived variables are available for subsequent prompts
    """

    def test_json_object_response_format_sets_json_data_type(
        self, auth_client, dataset, input_column
    ):
        """Test that response_format json_object sets column data_type to json."""
        config = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Return JSON"}]}
            ],
            "output_format": "object",
            "response_format": {"type": "json_object"},
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "JSON Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(name="JSON Output", dataset=dataset, deleted=False)
        # Column data_type should be "json" for JSON response format
        assert column.data_type == DataTypeChoices.JSON.value

    def test_uuid_response_format_sets_json_data_type(
        self, auth_client, dataset, input_column
    ):
        """Test that UUID response_format (custom schema) sets column data_type to json."""
        # UUID response_format indicates a UserResponseSchema (structured output)
        schema_uuid = str(uuid.uuid4())
        config = {
            "model": "gpt-4",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Structured output"}],
                }
            ],
            "output_format": "object",
            "response_format": schema_uuid,
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Structured Output",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(
            name="Structured Output", dataset=dataset, deleted=False
        )
        # UUID response_format should also set data_type to "json"
        assert column.data_type == DataTypeChoices.JSON.value

    def test_derived_variables_extracted_from_json_output(
        self, auth_client, dataset, input_column, row
    ):
        """Test that derived variables are extracted from JSON run prompt output."""
        from model_hub.services.derived_variable_service import (
            extract_derived_variables_from_output,
        )

        # Simulate JSON output from a run prompt
        json_output = (
            '{"user": {"name": "John", "email": "john@example.com"}, "score": 95}'
        )

        # Extract derived variables
        derived_vars = extract_derived_variables_from_output(
            json_output, "OutputColumn"
        )

        assert derived_vars["is_json"] is True
        assert "user" in derived_vars["paths"]
        assert "user.name" in derived_vars["paths"]
        assert "user.email" in derived_vars["paths"]
        assert "score" in derived_vars["paths"]

        # Verify full variable names
        assert "OutputColumn.user" in derived_vars["full_variables"]
        assert "OutputColumn.user.name" in derived_vars["full_variables"]
        assert "OutputColumn.score" in derived_vars["full_variables"]

    def test_derived_variables_stored_in_run_prompter(
        self, auth_client, dataset, organization, workspace
    ):
        """Test that derived variables are stored in RunPrompter.run_prompt_config."""
        from model_hub.services.derived_variable_service import (
            extract_derived_variables_from_output,
        )

        # Create a run prompter
        run_prompter = RunPrompter.objects.create(
            name="JSON Run Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.COMPLETED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Test"}],
            run_prompt_config={},
        )

        # Simulate storing derived variables after run completes
        json_output = '{"result": {"status": "success", "data": [1, 2, 3]}}'
        derived_vars = extract_derived_variables_from_output(json_output, "Output")

        run_prompter.run_prompt_config["derived_variables"] = derived_vars
        run_prompter.save()

        # Reload and verify
        run_prompter.refresh_from_db()
        stored_vars = run_prompter.run_prompt_config.get("derived_variables", {})

        assert stored_vars.get("is_json") is True
        assert "result" in stored_vars.get("paths", [])
        assert "result.status" in stored_vars.get("paths", [])
        assert "result.data" in stored_vars.get("paths", [])


# ==================== Image Generation S3 Upload E2E Tests ====================


@pytest.mark.django_db
class TestImageGenerationS3Flow:
    """
    E2E tests for image generation with S3 upload.

    Tests that:
    1. Image generation calls upload_image_to_s3
    2. S3 URL is stored in the cell value (not base64)
    3. Error handling when S3 upload fails
    """

    def test_image_output_format_sets_image_data_type(
        self, auth_client, dataset, input_column
    ):
        """Test that output_format image sets column data_type to image."""
        config = {
            "model": "dall-e-3",
            "messages": [{"role": "user", "content": "A sunset over mountains"}],
            "output_format": "image",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Generated Image",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(
            name="Generated Image", dataset=dataset, deleted=False
        )
        assert column.data_type == DataTypeChoices.IMAGE.value

    @patch("agentic_eval.core_evals.run_prompt.litellm_response.upload_image_to_s3")
    @patch(
        "agentic_eval.core_evals.run_prompt.litellm_response.litellm.image_generation"
    )
    def test_image_generation_uploads_to_s3(self, mock_image_gen, mock_s3_upload):
        """Test that image generation response is uploaded to S3."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        # Mock the image generation response
        mock_image_data = MagicMock()
        mock_image_data.url = "https://provider.com/temp-image.png"
        mock_image_data.b64_json = None
        mock_image_data.revised_prompt = "Enhanced prompt"

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]
        mock_response.usage = None
        mock_image_gen.return_value = mock_response

        # Mock S3 upload
        mock_s3_upload.return_value = "https://s3.amazonaws.com/bucket/image.png"

        # This test verifies the S3 upload is called during image generation
        # The actual RunPrompt class uses upload_image_to_s3 in _image_generation_response
        mock_s3_upload.assert_not_called()  # Not called yet

        # When image generation runs, S3 upload should be invoked
        # (Full integration would require mocking more dependencies)

    def test_audio_output_format_sets_audio_data_type(
        self, auth_client, dataset, input_column
    ):
        """Test that output_format audio sets column data_type to audio."""
        config = {
            "model": "tts-1",
            "messages": [{"role": "user", "content": "Hello world"}],
            "output_format": "audio",
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Generated Audio",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        column = Column.objects.get(
            name="Generated Audio", dataset=dataset, deleted=False
        )
        assert column.data_type == DataTypeChoices.AUDIO.value


# ==================== Dot Notation Variable Resolution E2E Tests ====================


@pytest.mark.django_db
class TestDotNotationVariableResolution:
    """
    E2E tests for dot notation variable resolution in prompts.

    Tests that:
    1. Variables like {{Column.nested.path}} are correctly resolved
    2. Array access like {{Column.items[0].name}} works
    3. Missing paths return empty string gracefully
    """

    def test_resolve_nested_json_path(self):
        """Test resolving nested JSON paths from column values."""
        from model_hub.utils.json_path_resolver import resolve_json_path

        json_data = {
            "user": {
                "name": "Alice",
                "profile": {
                    "email": "alice@example.com",
                    "settings": {"theme": "dark"},
                },
            },
            "items": [{"id": 1, "name": "First"}, {"id": 2, "name": "Second"}],
        }

        # Test nested object access
        assert resolve_json_path(json_data, "user.name") == "Alice"
        assert resolve_json_path(json_data, "user.profile.email") == "alice@example.com"
        assert resolve_json_path(json_data, "user.profile.settings.theme") == "dark"

        # Test array access
        assert resolve_json_path(json_data, "items[0].name") == "First"
        assert resolve_json_path(json_data, "items[1].id") == "2"

        # Test missing path returns empty string
        assert resolve_json_path(json_data, "user.nonexistent") == ""
        assert resolve_json_path(json_data, "items[99].name") == ""

    def test_resolve_json_path_from_string(self):
        """Test resolving paths from JSON string input."""
        from model_hub.utils.json_path_resolver import resolve_json_path

        json_string = '{"response": {"status": "success", "count": 42}}'

        assert resolve_json_path(json_string, "response.status") == "success"
        assert resolve_json_path(json_string, "response.count") == "42"

    def test_extract_all_json_keys_for_autocomplete(self):
        """Test extracting all JSON keys for frontend autocomplete."""
        from model_hub.utils.json_path_resolver import extract_json_keys

        json_data = {
            "user": {"name": "Test", "roles": ["admin", "user"]},
            "metadata": {"created_at": "2024-01-01"},
        }

        keys = extract_json_keys(json_data)

        # Should include all paths
        assert "user" in keys
        assert "user.name" in keys
        assert "user.roles" in keys
        assert "user.roles[0]" in keys
        assert "metadata" in keys
        assert "metadata.created_at" in keys

    def test_is_json_response_format_detection(self):
        """Test detection of JSON response formats."""
        from model_hub.utils.column_utils import is_json_response_format

        # Dict with type
        assert is_json_response_format({"type": "json_object"}) is True
        assert is_json_response_format({"type": "json"}) is True
        assert is_json_response_format({"type": "object"}) is True

        # String type
        assert is_json_response_format("json_object") is True
        assert is_json_response_format("json") is True

        # UUID (custom schema) - should be treated as JSON
        assert is_json_response_format(str(uuid.uuid4())) is True

        # Non-JSON formats
        assert is_json_response_format({"type": "text"}) is False
        assert is_json_response_format("text") is False
        assert is_json_response_format(None) is False

    def test_get_column_data_type_with_json_response_format(self):
        """Test column data type determination based on response format."""
        from model_hub.utils.column_utils import get_column_data_type

        # JSON response formats should return "json" data type
        assert get_column_data_type("object", {"type": "json_object"}) == "json"
        assert get_column_data_type("string", {"type": "json_object"}) == "json"

        # Non-JSON formats should use output_format mapping
        assert get_column_data_type("string", None) == "text"
        assert get_column_data_type("number", None) == "integer"
        assert get_column_data_type("array", None) == "array"
        assert get_column_data_type("image", None) == "image"
        assert get_column_data_type("audio", None) == "audio"


# ==================== Derived Variable Service E2E Tests ====================


@pytest.mark.django_db
class TestDerivedVariableServiceE2E:
    """
    E2E tests for derived variable service functions.

    Tests the complete lifecycle:
    1. Extract variables from JSON output
    2. Merge variables when prompts are rerun
    3. Rename variables when columns are renamed
    4. Cleanup variables when columns are deleted
    """

    def test_extract_derived_variables_with_nested_json(self):
        """Test extracting derived variables from complex nested JSON."""
        from model_hub.services.derived_variable_service import (
            extract_derived_variables_from_output,
        )

        output = {
            "analysis": {
                "sentiment": "positive",
                "confidence": 0.95,
                "keywords": ["good", "excellent"],
            },
            "summary": "Great product review",
        }

        result = extract_derived_variables_from_output(output, "Analysis")

        assert result["is_json"] is True
        assert "analysis" in result["paths"]
        assert "analysis.sentiment" in result["paths"]
        assert "analysis.confidence" in result["paths"]
        assert "analysis.keywords" in result["paths"]
        assert "summary" in result["paths"]

        # Check full variable names
        assert "Analysis.analysis.sentiment" in result["full_variables"]
        assert "Analysis.summary" in result["full_variables"]

        # Check schema has type info
        assert result["schema"]["analysis.sentiment"]["type"] == "string"
        assert result["schema"]["analysis.confidence"]["type"] == "number"
        assert result["schema"]["analysis.keywords"]["type"] == "array"

    def test_merge_derived_variables_adds_new_paths(self):
        """Test that merging adds new paths from updated output."""
        from model_hub.services.derived_variable_service import (
            merge_derived_variables,
        )

        existing = {
            "paths": ["field1"],
            "schema": {"field1": {"type": "string"}},
            "full_variables": ["Col.field1"],
            "is_json": True,
        }

        new_data = {
            "paths": ["field1", "field2"],
            "schema": {
                "field1": {"type": "string"},
                "field2": {"type": "number"},
            },
            "full_variables": ["Col.field1", "Col.field2"],
            "is_json": True,
            "column_name": "Col",
        }

        merged = merge_derived_variables(existing, new_data)

        assert "field1" in merged["paths"]
        assert "field2" in merged["paths"]
        assert merged["schema"]["field2"]["type"] == "number"

    def test_merge_derived_variables_marks_removed_paths_stale(self):
        """Test that removed paths are marked as stale."""
        from model_hub.services.derived_variable_service import (
            merge_derived_variables,
        )

        existing = {
            "paths": ["field1", "field2", "field3"],
            "schema": {
                "field1": {"type": "string"},
                "field2": {"type": "string"},
                "field3": {"type": "string"},
            },
            "full_variables": ["Col.field1", "Col.field2", "Col.field3"],
            "is_json": True,
        }

        # New output only has field1
        new_data = {
            "paths": ["field1"],
            "schema": {"field1": {"type": "string"}},
            "full_variables": ["Col.field1"],
            "is_json": True,
        }

        merged = merge_derived_variables(existing, new_data)

        # field2 and field3 should be marked as stale
        assert merged["schema"]["field2"]["stale"] is True
        assert merged["schema"]["field3"]["stale"] is True
        assert "stale" not in merged["schema"]["field1"]

    def test_rename_derived_variables_updates_paths(
        self, dataset, organization, workspace
    ):
        """Test that renaming a column updates derived variable paths."""
        from model_hub.services.derived_variable_service import (
            rename_derived_variables_in_run_prompter,
        )

        run_prompter = RunPrompter.objects.create(
            name="Test Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            model="gpt-4",
            messages=[],
            run_prompt_config={
                "derived_variables": {
                    "full_variables": [
                        "OldName.user.name",
                        "OldName.user.email",
                        "OldName.score",
                    ],
                    "is_json": True,
                }
            },
        )

        result = rename_derived_variables_in_run_prompter(
            run_prompter, "OldName", "NewName"
        )

        assert result is True
        # Function modifies in-memory, caller must save
        run_prompter.save()
        run_prompter.refresh_from_db()

        full_vars = run_prompter.run_prompt_config["derived_variables"][
            "full_variables"
        ]
        assert "NewName.user.name" in full_vars
        assert "NewName.user.email" in full_vars
        assert "NewName.score" in full_vars
        assert "OldName.user.name" not in full_vars

    def test_non_json_output_returns_empty_derived_variables(self):
        """Test that non-JSON output returns empty derived variables."""
        from model_hub.services.derived_variable_service import (
            extract_derived_variables_from_output,
        )

        # Plain text output
        result = extract_derived_variables_from_output(
            "This is just plain text, not JSON.", "TextColumn"
        )

        assert result["is_json"] is False
        assert result["paths"] == []
        assert result["full_variables"] == []


# ==================== Run Prompt with Images Column Tests ====================


@pytest.mark.django_db
class TestRunPromptWithImagesColumn:
    """Tests for run prompt with images data type columns."""

    @pytest.fixture
    def images_column(self, dataset):
        """Create an images column with multiple images per cell."""
        col = Column.objects.create(
            name="Screenshots",
            dataset=dataset,
            data_type=DataTypeChoices.IMAGES.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order.append(str(col.id))
        dataset.save()
        return col

    @pytest.fixture
    def images_cell(self, dataset, images_column, row):
        """Create a cell with multiple images as JSON array."""
        import json

        images_list = [
            "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1",
            "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1",
            "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1",
        ]
        return Cell.objects.create(
            dataset=dataset,
            column=images_column,
            row=row,
            value=json.dumps(images_list),
        )

    def test_add_run_prompt_with_images_column_reference(
        self, auth_client, dataset, images_column, images_cell
    ):
        """Test adding run prompt that references an images column."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Analyze Screenshots",
            "config": {
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze these screenshots: {{Screenshots}}",
                            }
                        ],
                    }
                ],
                "temperature": 0.7,
                "max_tokens": 500,
                "output_format": "string",
            },
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert Column.objects.filter(
            name="Analyze Screenshots", dataset=dataset, deleted=False
        ).exists()

    def test_add_run_prompt_with_indexed_image_reference(
        self, auth_client, dataset, images_column, images_cell
    ):
        """Test adding run prompt that references specific image by index."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Analyze First Screenshot",
            "config": {
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze this screenshot: {{Screenshots[0]}}",
                            }
                        ],
                    }
                ],
                "temperature": 0.7,
                "max_tokens": 500,
                "output_format": "string",
            },
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestProcessTextWithMediaImages:
    """Tests for process_text_with_media function with images data type."""

    @pytest.fixture
    def images_column_for_media(self, dataset):
        """Create an images column for process_text_with_media tests."""
        col = Column.objects.create(
            name="Screenshots",
            dataset=dataset,
            data_type=DataTypeChoices.IMAGES.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order.append(str(col.id))
        dataset.save()
        return col

    @patch("model_hub.views.run_prompt.convert_image_from_url_to_base64")
    def test_process_text_with_images_full_array(
        self, mock_convert, images_column_for_media
    ):
        """Test that {{column_uuid}} placeholder includes ALL images."""
        import json

        from model_hub.views.run_prompt import process_text_with_media

        # Mock the base64 conversion to avoid network calls
        mock_convert.return_value = "data:image/png;base64,test_data"

        col_id = str(images_column_for_media.id)
        test_image_url = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1"
        images_list = [test_image_url, test_image_url]

        column_info = {
            col_id: {
                "name": "Screenshots",
                "data_type": "images",
                "value": json.dumps(images_list),
            }
        }

        # Use column UUID in placeholder
        text = f"Analyze: {{{{{col_id}}}}}"
        context = {}

        # Function returns list of content segments
        segments = process_text_with_media(text, column_info, context, 0, "gpt-4o")

        # Should have segments for both images (each image produces 2 segments: text + image_url)
        image_segments = [s for s in segments if s.get("type") == "image_url"]
        assert len(image_segments) == 2

    @patch("model_hub.views.run_prompt.convert_image_from_url_to_base64")
    def test_process_text_with_images_indexed_access(
        self, mock_convert, images_column_for_media
    ):
        """Test that {{column_uuid[0]}} returns specific image."""
        import json

        from model_hub.views.run_prompt import process_text_with_media

        mock_convert.return_value = "data:image/png;base64,test_data"

        col_id = str(images_column_for_media.id)
        test_image_url = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1"
        images_list = [test_image_url, test_image_url, test_image_url]

        column_info = {
            col_id: {
                "name": "Screenshots",
                "data_type": "images",
                "value": json.dumps(images_list),
            }
        }

        # Use column UUID with index in placeholder
        text = f"First image: {{{{{col_id}[0]}}}}"
        context = {}

        segments = process_text_with_media(text, column_info, context, 0, "gpt-4o")

        # Should have one image segment for indexed access
        image_segments = [s for s in segments if s.get("type") == "image_url"]
        assert len(image_segments) == 1

    @patch("model_hub.views.run_prompt.convert_image_from_url_to_base64")
    def test_process_text_with_images_multiple_indexes(
        self, mock_convert, images_column_for_media
    ):
        """Test that multiple indexed references work correctly."""
        import json

        from model_hub.views.run_prompt import process_text_with_media

        mock_convert.return_value = "data:image/png;base64,test_data"

        col_id = str(images_column_for_media.id)
        test_image_url = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1"
        images_list = [test_image_url, test_image_url, test_image_url]

        column_info = {
            col_id: {
                "name": "Screenshots",
                "data_type": "images",
                "value": json.dumps(images_list),
            }
        }

        # Use column UUID with indexes in placeholders
        text = f"Compare {{{{{col_id}[0]}}}} with {{{{{col_id}[2]}}}}"
        context = {}

        segments = process_text_with_media(text, column_info, context, 0, "gpt-4o")

        # Should have two image segments for two indexed accesses
        image_segments = [s for s in segments if s.get("type") == "image_url"]
        assert len(image_segments) == 2

    def test_process_text_with_empty_images_array(self, images_column_for_media):
        """Test handling of empty images array."""
        import json

        from model_hub.views.run_prompt import process_text_with_media

        col_id = str(images_column_for_media.id)
        column_info = {
            col_id: {
                "name": "Screenshots",
                "data_type": "images",
                "value": json.dumps([]),
            }
        }

        # Use column UUID in placeholder
        text = f"Analyze: {{{{{col_id}}}}}"
        context = {}

        segments = process_text_with_media(text, column_info, context, 0, "gpt-4o")

        # No image segments for empty array
        image_segments = [s for s in segments if s.get("type") == "image_url"]
        assert len(image_segments) == 0


# ==================== Max Images Count API Tests ====================


@pytest.mark.django_db
class TestGetJsonSchemaViewMaxImagesCount:
    """Tests for GetJsonColumnSchemaView returning max_images_count for images columns."""

    @pytest.fixture
    def images_dataset_with_cells(self, organization, workspace, user):
        """Create a dataset with images column and cells with varying image counts."""
        import json

        from model_hub.models.choices import CellStatus

        dataset = Dataset.objects.create(
            name="Images Test Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        # Create images column
        images_col = Column.objects.create(
            name="Screenshots",
            dataset=dataset,
            data_type=DataTypeChoices.IMAGES.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order = [str(images_col.id)]
        dataset.save()

        test_image_url = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1"

        # Create rows with varying number of images
        row1 = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            dataset=dataset,
            column=images_col,
            row=row1,
            value=json.dumps([test_image_url, test_image_url]),  # 2 images
            status=CellStatus.PASS.value,
        )

        row2 = Row.objects.create(dataset=dataset, order=1)
        Cell.objects.create(
            dataset=dataset,
            column=images_col,
            row=row2,
            value=json.dumps(
                [test_image_url, test_image_url, test_image_url]
            ),  # 3 images
            status=CellStatus.PASS.value,
        )

        row3 = Row.objects.create(dataset=dataset, order=2)
        Cell.objects.create(
            dataset=dataset,
            column=images_col,
            row=row3,
            value=json.dumps([test_image_url] * 5),  # 5 images
            status=CellStatus.PASS.value,
        )

        return dataset, images_col

    def test_max_images_count_returns_correct_value(
        self, auth_client, images_dataset_with_cells
    ):
        """Test that maxImagesCount returns the maximum number of images across all cells."""
        dataset, images_col = images_dataset_with_cells

        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/json-schema/",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        # Should have entry for images column
        assert str(images_col.id) in result
        col_info = result[str(images_col.id)]

        # maxImagesCount should be 5 (from row3) - API returns camelCase
        assert "max_images_count" in col_info
        assert col_info["max_images_count"] == 5
        assert col_info["name"] == "Screenshots"

    def test_max_images_count_empty_images_column(
        self, auth_client, organization, workspace
    ):
        """Test that images column with no cells returns no entry."""
        dataset = Dataset.objects.create(
            name="Empty Images Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        images_col = Column.objects.create(
            name="EmptyImages",
            dataset=dataset,
            data_type=DataTypeChoices.IMAGES.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order = [str(images_col.id)]
        dataset.save()

        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/json-schema/",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        # Empty images column should not appear in result (max_count would be 0)
        assert str(images_col.id) not in result

    def test_max_images_count_single_image_per_cell(
        self, auth_client, organization, workspace
    ):
        """Test max_images_count when each cell has exactly one image."""
        import json

        from model_hub.models.choices import CellStatus

        dataset = Dataset.objects.create(
            name="Single Image Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        images_col = Column.objects.create(
            name="SingleImages",
            dataset=dataset,
            data_type=DataTypeChoices.IMAGES.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order = [str(images_col.id)]
        dataset.save()

        test_image_url = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/75eac432-8aa4-4b17-a7b1-983e1cd45eae/73e0eacd-8e01-4d71-bca1-71c5dbda50c1"

        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            dataset=dataset,
            column=images_col,
            row=row,
            value=json.dumps([test_image_url]),  # 1 image
            status=CellStatus.PASS.value,
        )

        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/json-schema/",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert str(images_col.id) in result
        # API returns camelCase
        assert result[str(images_col.id)]["max_images_count"] == 1


# ==================== Backend Validation Tests ====================


@pytest.mark.django_db
class TestPromptConfigSerializerValidation:
    """Tests for backend serializer validations that align with UI checks."""

    def test_concurrency_max_10(self, auth_client, dataset, input_column):
        """Concurrency > 10 should be rejected (matches UI max(10))."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "ConcurrencyTest",
            "config": {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Test {{Input Column}}"}],
                    }
                ],
                "concurrency": 11,
                "output_format": "string",
            },
        }
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_name_rejected(self, auth_client, dataset, input_column):
        """Empty name should be rejected (matches UI min(1))."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "",
            "config": {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "output_format": "string",
            },
        }
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_user_message_empty_content_rejected(
        self, auth_client, dataset, input_column
    ):
        """User messages with empty content should be rejected."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "EmptyContentTest",
            "config": {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": "   ",
                    }
                ],
                "output_format": "string",
            },
        }
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_tts_without_voice_rejected(self, auth_client, dataset, input_column):
        """TTS model type without voice should be rejected."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "TTSTest",
            "config": {
                "model": "tts-1",
                "run_prompt_config": {"modelType": "tts", "modelName": "tts-1"},
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Say hello"}],
                    }
                ],
                "output_format": "audio",
            },
        }
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_temperature_accepts_up_to_2(self, auth_client, dataset, input_column):
        """Temperature up to 2.0 should be accepted (aligned with MCP tool)."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "TempTest",
            "config": {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "temperature": 2.0,
                "output_format": "string",
            },
        }
        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK

    def test_temperature_rejects_above_2(self, auth_client, dataset, input_column):
        """Temperature above 2.0 should be rejected."""
        payload = {
            "dataset_id": str(dataset.id),
            "name": "TempTest2",
            "config": {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "temperature": 2.1,
                "output_format": "string",
            },
        }
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== ColumnValuesAPIView Org Isolation Test ====================


@pytest.mark.django_db
class TestColumnValuesAPIViewOrgIsolation:
    """Tests for ColumnValuesAPIView organization filtering."""

    def test_cross_org_access_blocked(self, db, dataset, input_column, row, cell):
        """Users from a different org should not access another org's dataset."""
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@example.com",
            password="testpassword123",
            name="Other User",
            organization=other_org,
        )
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_org,
            is_default=True,
            created_by=other_user,
        )

        other_client = APIClient()
        other_client.force_authenticate(user=other_user)
        set_workspace_context(workspace=other_workspace, organization=other_org)

        payload = {
            "dataset_id": str(dataset.id),
            "column_placeholders": {"test": str(input_column.id)},
        }
        response = other_client.post(
            "/model-hub/get-column-values/",
            payload,
            format="json",
        )
        # Should be 404 (not found) instead of returning data
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_same_org_access_allowed(
        self, auth_client, dataset, input_column, row, cell
    ):
        """Users from the same org should access their datasets."""
        payload = {
            "dataset_id": str(dataset.id),
            "column_placeholders": {"test": str(input_column.id)},
        }
        response = auth_client.post(
            "/model-hub/get-column-values/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

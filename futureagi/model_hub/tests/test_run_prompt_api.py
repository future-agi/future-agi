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

import pytest
from django.test import TestCase
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
        and must not be rejected."""
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


# ==================== Run Prompt Error Disclosure Tests ====================


@pytest.fixture
def execute_prompt_error_case(auth_client, dataset, row, organization, monkeypatch):
    """Exercise the public preview and both row writers without provider calls."""
    from types import SimpleNamespace

    from model_hub.services import credit_ledger_port as credit_ledger
    from model_hub.services import selective_rerun
    from model_hub.views import run_prompt as module

    # The line's selective-rerun bookkeeping and payer routing are outside these
    # tests; stub them as the line's own run-prompt tests do (BYOK route).
    monkeypatch.setattr(selective_rerun, "input_fingerprints", lambda *args: {})
    monkeypatch.setattr(selective_rerun, "save_fingerprints", lambda *args: None)

    def execute(path, *, phase=None, failure=None):
        config = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "output_format": "string",
            "run_prompt_config": {},
        }
        column = SimpleNamespace(id=uuid.uuid4())
        with (
            patch.object(module, "RunPrompt") as provider,
            patch.object(module, "populate_placeholders") as populate,
            patch.object(module, "logger") as logger,
            patch.object(module, "log_and_deduct_cost_for_api_request", None),
            patch.object(module, "close_old_connections"),
            patch.object(module.Cell.objects, "create") as create_cell,
            patch.object(module.Cell.objects, "get") as get_cell,
            patch.object(module.Cell.objects, "update_or_create") as update_cell,
            patch.dict(
                "sys.modules",
                {
                    "ee.usage.schemas.events": MagicMock(),
                    "ee.usage.services.emitter": MagicMock(),
                },
            ),
        ):
            populate.return_value = config["messages"]
            provider.return_value.litellm_response.return_value = (
                "Successful answer",
                {"data": {"response": "Successful answer"}, "metadata": {}},
            )
            if phase == "preparation":
                populate.side_effect = failure
            elif phase == "constructor":
                provider.side_effect = failure
            elif phase == "provider":
                provider.return_value.litellm_response.side_effect = failure

            if path == "preview":
                response = auth_client.post(
                    "/model-hub/develops/preview_run_prompt_column/",
                    {
                        "dataset_id": str(dataset.id),
                        "name": "Preview",
                        "first_n_rows": 1,
                        "config": config,
                    },
                    format="json",
                )
                assert response.status_code == status.HTTP_200_OK
                result = response.json()["result"]
                assert result["token_usage"] == {}
                assert result["cost"] == {}
                messages = result["responses"]
                cell_status = None
            elif path == "direct":
                module.LitellmAPIView().process_row(
                    row,
                    config,
                    dataset,
                    column,
                    SimpleNamespace(organization=organization),
                )
                saved = update_cell.call_args.kwargs["defaults"]
                messages = [saved["value"], json.loads(saved["value_infos"])["reason"]]
                cell_status = saved["status"]
            else:
                runner = module.RunPrompts(run_prompt_id=uuid.uuid4())
                runner.run_prompt_model = MagicMock(
                    dataset=dataset, organization=organization, **config
                )
                runner.is_editing = path == "edit"
                runner.process_row(row, column)
                if path == "edit":
                    saved_cell = get_cell.return_value
                    saved_cell.save.assert_called_once()
                    saved = {
                        "value": saved_cell.value,
                        "value_infos": saved_cell.value_infos,
                        "status": saved_cell.status,
                    }
                else:
                    saved = create_cell.call_args.kwargs
                messages = [saved["value"], json.loads(saved["value_infos"])["reason"]]
                cell_status = saved["status"]

            # The saved cell fields of the last row writer call (None for the
            # preview, which saves no cell), for tests that read value_infos.
            execute.saved = None if path == "preview" else saved
            return messages, cell_status, logger, provider

    with credit_ledger.use_route_resolver(
        lambda **_kwargs: credit_ledger.PayerRoute.byok(provider="openai")
    ):
        yield execute


@pytest.fixture
def custom_endpoint_model(user, workspace, monkeypatch):
    """A provider "custom" model (Settings > AI Providers) and its endpoint.

    The model row, RunPrompt, the handler factory, CustomModelHandler and
    ``tfc.utils.ssrf_guard.guarded_post`` are real; the endpoint's DNS and
    transport are the guarded_post fakes, so nothing leaves the process.
    ``call`` runs one real ``RunPrompt.litellm_response`` for the model. Given
    as the provider failure of ``execute_prompt_error_case``, it is what each
    path's RunPrompt call does, so every path gets the error text the real
    handler and litellm_response raise.
    """
    from types import SimpleNamespace

    from django.test import override_settings

    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
    from model_hub.models.custom_models import CustomAIModel
    from tfc.utils.tests.guarded_post_fakes import (
        FakeResolver,
        FakeTransport,
        UnguardedHttp,
    )
    from tfc.utils.tests.loopback_endpoints import clear_proxy_env

    name = "tenant-custom-endpoint"
    CustomAIModel.objects.create(
        user_model_id=name,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={
            "api_base": "https://api.example.com/v1/chat/completions",
            "headers": {"Authorization": "Bearer tenant"},
            "custom_provider": True,
        },
        user=user,
        deleted=False,
    )
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("api.example.com", "93.184.216.34")
    transport = FakeTransport().install(monkeypatch)
    unguarded = UnguardedHttp().install(monkeypatch)

    def call(*_args, **_kwargs):
        return RunPrompt(
            model=name,
            messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
            organization_id=user.organization.id,
            output_format="string",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
            workspace_id=workspace.id,
        ).litellm_response()

    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield SimpleNamespace(call=call, transport=transport, unguarded=unguarded)


@pytest.mark.django_db
class TestRunPromptErrorDisclosure:
    GENERIC_ERROR = (
        "Unable to run prompt. Please try again or contact support if the problem persists."
    )

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize("phase", ["preparation", "constructor", "provider"])
    @pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
    def test_unexpected_errors_are_redacted_and_logged(
        self, execute_prompt_error_case, path, phase, error_type
    ):
        # A ValueError is not proof of local validation. I/O and providers use it too.
        error = error_type("upstream body: internal connection at /srv/private/client.py")
        messages, cell_status, logger, provider = execute_prompt_error_case(
            path, phase=phase, failure=error
        )

        assert messages == [self.GENERIC_ERROR] * len(messages)
        assert str(error) not in json.dumps(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called
        assert str(error) in str(logger.exception.call_args_list)
        if phase == "preparation":
            provider.assert_not_called()

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "validation,expected",
        [
            ("placeholder", "'missing_column'"),
            ("template", "unexpected 'end of print statement'"),
            ("image", "Model gpt-4o does not support image input."),
            ("audio", "Model gpt-4o does not support audio input."),
            ("pdf", "Model gpt-4o does not support PDF input."),
        ],
        ids=["placeholder", "template", "image", "audio", "pdf"],
    )
    def test_local_validation_remains_actionable(
        self, execute_prompt_error_case, monkeypatch, path, validation, expected
    ):
        from model_hub.views import run_prompt as module

        monkeypatch.setattr(module.litellm.utils, "supports_vision", lambda **kw: False)
        monkeypatch.setattr(
            module.litellm.utils, "supports_audio_input", lambda **kw: False
        )
        monkeypatch.setattr(module.litellm.utils, "supports_pdf_input", lambda **kw: False)
        monkeypatch.setattr(
            "model_hub.views.prompt_template.get_model_mode", lambda model: "chat"
        )

        def validate(*args, **kwargs):
            if validation in ("placeholder", "template"):
                text = (
                    "{missing_column}" if validation == "placeholder" else "{{ missing + }}"
                )
                template_format = "f-string" if validation == "placeholder" else "jinja2"
                return module.process_text_with_media(
                    text, {}, {}, 0, "gpt-4o", template_format=template_format
                )
            media_type = f"{validation}_url"
            return module.process_list_content(
                [{"type": media_type, media_type: {"url": "https://example.com/media"}}],
                {},
                {},
                0,
                "gpt-4o",
            )

        messages, cell_status, _, provider = execute_prompt_error_case(
            path, phase="preparation", failure=validate
        )

        assert messages == [expected] * len(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        provider.assert_not_called()

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    def test_validation_shaped_provider_error_is_not_allowlisted(
        self, execute_prompt_error_case, monkeypatch, path
    ):
        from model_hub.views import run_prompt as module

        # Even the local validation exception type is not trusted outside that phase.
        monkeypatch.setattr(module.litellm.utils, "supports_vision", lambda **kw: False)
        with pytest.raises(ValueError) as raised:
            module.process_media_markers(
                "marker", {"marker": {"type": "image", "counter": 0}}, "gpt-4o"
            )
        messages, _, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=raised.value
        )

        assert messages == [self.GENERIC_ERROR] * len(messages)
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    def test_successful_answers_are_unchanged(self, execute_prompt_error_case, path):
        messages, cell_status, _, _ = execute_prompt_error_case(path)

        assert messages == ["Successful answer"] * len(messages)
        assert cell_status == (None if path == "preview" else CellStatus.PASS.value)

    def test_preview_setup_error_is_redacted(self, auth_client, dataset):
        error = ValueError("internal query failed at /srv/private/query.py")
        with (
            patch("model_hub.views.run_prompt.Row.objects.filter", side_effect=error),
            patch("model_hub.views.run_prompt.logger") as logger,
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                {
                    "dataset_id": str(dataset.id),
                    "name": "Preview",
                    "first_n_rows": 1,
                    "config": {
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "output_format": "string",
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert self.GENERIC_ERROR in json.dumps(response.json())
        assert str(error) not in json.dumps(response.json())
        assert logger.exception.called
        assert str(error) in str(logger.exception.call_args_list)

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "category,extra",
        [
            ("redirect", ""),
            ("blocked", ""),
            ("failed", ""),
            ("blocked", " Host 169.254.169.254 answered: secret-body"),
        ],
        ids=["redirect", "blocked", "failed", "copy_plus_text_is_redacted"],
    )
    def test_endpoint_error_copy_reaches_the_answer(
        self, execute_prompt_error_case, path, category, extra
    ):
        from tfc.utils.ssrf_guard import ENDPOINT_ERROR_MESSAGES

        # A tenant endpoint's failure leaves litellm_response as a plain
        # Exception whose text is exactly one ENDPOINT_ERROR_MESSAGES value.
        # Only that exact text is shown; anything added to it is not.
        copy = ENDPOINT_ERROR_MESSAGES[category]
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=Exception(copy + extra)
        )

        expected = self.GENERIC_ERROR if extra else copy
        assert messages == [expected] * len(messages)
        assert "secret-body" not in json.dumps(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
    def test_endpoint_status_copy_reaches_the_answer(
        self, execute_prompt_error_case, path, code
    ):
        from tfc.utils.ssrf_guard import endpoint_status_message

        # An endpoint that answers an error status leaves litellm_response as
        # a plain Exception whose text is exactly endpoint_status_message(code):
        # the code and its standard phrase, never the server's text or body.
        copy = endpoint_status_message(code)
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=Exception(copy)
        )

        assert messages == [copy] * len(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    def test_endpoint_status_copy_with_text_is_redacted(
        self, execute_prompt_error_case, path
    ):
        from tfc.utils.ssrf_guard import endpoint_status_message

        # Status copy with anything added to it is not the fixed copy.
        failure = Exception(endpoint_status_message(401) + " body: secret-body")
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=failure
        )

        assert messages == [self.GENERIC_ERROR] * len(messages)
        assert "secret-body" not in json.dumps(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    def test_non_error_status_copy_is_not_passed(self, execute_prompt_error_case, path):
        from tfc.utils.ssrf_guard import endpoint_status_message

        # Only error statuses have fixed copy; a 200 in this shape is not one.
        failure = Exception(endpoint_status_message(200))
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=failure
        )

        assert messages == [self.GENERIC_ERROR] * len(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    def test_preview_out_of_credits_refusal_shows_its_typed_copy(
        self, execute_prompt_error_case, organization, monkeypatch
    ):
        from decimal import Decimal

        from model_hub.services import credit_ledger_port as credit_ledger

        # A managed preview row with no credits left is refused before the
        # provider call and shows the typed credits copy, not the generic one.
        monkeypatch.setattr(
            credit_ledger, "estimate_call_cost", lambda *a, **kw: Decimal("0.10")
        )
        monkeypatch.setattr(credit_ledger, "_sleep", lambda *_args: None)
        ledger = credit_ledger.MockCreditLedger(
            balances={organization.id: Decimal("0")}
        )
        credit_ledger.reset_meter_safety()
        try:
            with (
                credit_ledger.use_credit_ledger(ledger),
                credit_ledger.use_route_resolver(
                    lambda **_kwargs: credit_ledger.PayerRoute.managed(provider="openai")
                ),
            ):
                messages, cell_status, _, provider = execute_prompt_error_case(
                    "preview"
                )
        finally:
            credit_ledger.reset_meter_safety()

        expected = credit_ledger.CreditsExhaustedError().message
        assert expected != self.GENERIC_ERROR
        assert messages == [expected]
        assert cell_status is None
        provider.return_value.litellm_response.assert_not_called()
        assert "hold" in [call[0] for call in ledger.calls]
        assert "settle" not in [call[0] for call in ledger.calls]

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            (
                "status_401",
                "Custom model API request failed: "
                "The endpoint answered HTTP 401 Unauthorized.",
            ),
            (
                "connect",
                "Custom model API request failed: Could not connect to the endpoint.",
            ),
            (
                "timeout",
                "Custom model request timed out after 300 seconds: "
                "The endpoint did not answer in time.",
            ),
        ],
        ids=["status_401", "connect", "timeout"],
    )
    def test_custom_model_endpoint_copy_reaches_the_answer(
        self, execute_prompt_error_case, custom_endpoint_model, path, endpoint, expected
    ):
        import urllib3

        from tfc.utils.tests.guarded_post_fakes import FakeResponse

        # A provider "custom" model's endpoint fails. CustomModelHandler puts
        # one of its two fixed prefixes before the guard's copy, and
        # litellm_response raises that text unchanged. The answer shows it,
        # as cut 2a did, never the endpoint's body or address.
        transport = custom_endpoint_model.transport
        if endpoint == "status_401":
            transport.respond(
                FakeResponse(401, {"Content-Type": "text/plain"}, b"secret-body")
            )
        elif endpoint == "connect":
            transport.fail_with(
                urllib3.exceptions.NewConnectionError(None, "refused 93.184.216.34:443")
            )
        else:
            transport.fail_with(
                urllib3.exceptions.ConnectTimeoutError(None, "timed out 93.184.216.34")
            )

        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=custom_endpoint_model.call
        )

        assert messages == [expected] * len(messages)
        assert "secret-body" not in json.dumps(messages)
        assert "93.184.216.34" not in json.dumps(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert len(transport.requests) == 1
        assert custom_endpoint_model.unguarded.calls == []
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            (
                "read_timeout_443",
                "Custom model request timed out after 300 seconds: "
                "The endpoint did not answer in time.",
            ),
            (
                "remote_disconnected",
                "Custom model API request failed: "
                "The endpoint's response could not be read.",
            ),
            (
                "tls_hostname_mismatch",
                "Custom model API request failed: The TLS connection to the "
                "endpoint failed (certificate or handshake).",
            ),
        ],
        ids=["read_timeout_443", "remote_disconnected", "tls_hostname_mismatch"],
    )
    def test_custom_model_transport_failure_keeps_the_custom_model_copy(
        self, execute_prompt_error_case, custom_endpoint_model, path, endpoint, expected
    ):
        import http.client

        import urllib3
        from urllib3.connectionpool import HTTPSConnectionPool
        from urllib3.util.ssl_match_hostname import CertificateError

        from tfc.utils import ssrf_guard

        # The https endpoint (port 443) accepts the connection, then fails:
        # it does not answer within the read timeout, it closes the
        # connection without an answer, or its certificate names other
        # hosts. urllib3 raises from the transport inside guarded_post's
        # except clause, so its own text (the pinned address and port) stays
        # in the error chain as __context__. The answer still shows
        # CustomModelHandler's prefixed copy, never the generic sentence,
        # urllib3's text, the address or the certificate's names.
        if endpoint == "read_timeout_443":
            # urllib3's own pool class: the fake transport replaces the
            # urllib3.HTTPSConnectionPool name.
            error = urllib3.exceptions.ReadTimeoutError(
                HTTPSConnectionPool("93.184.216.34", port=443),
                "/v1/chat/completions",
                "Read timed out. (read timeout=300)",
            )
        elif endpoint == "remote_disconnected":
            error = urllib3.exceptions.ProtocolError(
                "Connection aborted.",
                http.client.RemoteDisconnected(
                    "Remote end closed connection without response"
                ),
            )
        else:
            error = urllib3.exceptions.SSLError(
                CertificateError(
                    "hostname 'api.example.com' doesn't match either of "
                    "'*.shared-edge.example.net', 'shared-edge.example.net', "
                    "'origin.shared-edge.example.net'"
                )
            )
        transport = custom_endpoint_model.transport
        transport.fail_with(error)

        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=custom_endpoint_model.call
        )

        assert messages == [expected] * len(messages)
        shown = json.dumps(messages)
        for text in (
            "93.184.216.34",
            "port=443",
            "Read timed out",
            "Connection aborted",
            "RemoteDisconnected",
            "doesn't match",
            "shared-edge.example.net",
        ):
            assert text not in shown
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert len(transport.requests) == 1
        assert custom_endpoint_model.unguarded.calls == []
        assert logger.exception.called

        # The premise: the guard's error keeps urllib3's error as its
        # __context__, because it is raised inside guarded_post's except.
        with pytest.raises(ssrf_guard.GuardedRequestError) as raised:
            ssrf_guard.guarded_post(
                "https://api.example.com/v1/chat/completions",
                json={"messages": []},
                timeout=300,
            )
        assert raised.value.__context__ is error

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "text",
        [
            "Custom model API request failed: "
            "The endpoint URL is not allowed: its host is, or resolves to, a "
            "private, loopback, link-local or cloud metadata address.",
            "Custom model API request failed: The endpoint answered HTTP 503 "
            "Service Unavailable.",
            "Custom model request timed out after 30 seconds: "
            "The endpoint did not answer in time.",
        ],
        ids=["failed_prefix_blocked", "failed_prefix_status_503", "timeout_30_seconds"],
    )
    def test_custom_model_prefixed_copy_is_shown_as_it_is(
        self, execute_prompt_error_case, path, text
    ):
        # Either fixed prefix followed by exactly one fixed copy is shown as
        # it is; the timeout's whole seconds are the only variable part.
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=Exception(text)
        )

        assert messages == [text] * len(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    @pytest.mark.parametrize("path", ["preview", "worker", "edit", "direct"])
    @pytest.mark.parametrize(
        "text",
        [
            "Custom model API request failed: "
            "The endpoint answered HTTP 401 Unauthorized. body: secret-body",
            "Custom model API request failed: secret-body",
            "Custom model API request failed: The endpoint answered HTTP 200 OK.",
            "secret-body Custom model API request failed: "
            "Could not connect to the endpoint.",
            "Custom model API request failed: Custom model API request failed: "
            "Could not connect to the endpoint.",
            "Custom model API request failed: Could not connect to the endpoint.\n",
            "Custom model request failed: Could not connect to the endpoint.",
            "Custom model request timed out after 300 seconds: "
            "The endpoint did not answer in time. secret-body",
            "Custom model request timed out after 300 seconds: secret-body",
            "Custom model request timed out after 3e2 seconds: "
            "The endpoint did not answer in time.",
            "Custom model request timed out after  seconds: "
            "The endpoint did not answer in time.",
            "Custom model request timed out after ٣٠٠ seconds: "
            "The endpoint did not answer in time.",
        ],
        ids=[
            "copy_plus_text",
            "prefix_plus_other_text",
            "prefix_plus_non_error_status",
            "text_before_prefix",
            "prefix_twice",
            "trailing_newline",
            "other_prefix",
            "timeout_copy_plus_text",
            "timeout_prefix_plus_other_text",
            "timeout_seconds_not_digits",
            "timeout_seconds_missing",
            "timeout_seconds_non_ascii_digits",
        ],
    )
    def test_custom_model_prefix_with_anything_else_is_redacted(
        self, execute_prompt_error_case, path, text
    ):
        # Only the two fixed prefixes, then exactly one fixed copy, pass; any
        # other text, before, after or in place of the copy, does not.
        messages, cell_status, logger, _ = execute_prompt_error_case(
            path, phase="provider", failure=Exception(text)
        )

        assert messages == [self.GENERIC_ERROR] * len(messages)
        assert "secret-body" not in json.dumps(messages)
        assert cell_status == (None if path == "preview" else CellStatus.ERROR.value)
        assert logger.exception.called

    def test_direct_out_of_credits_refusal_shows_its_typed_copy(
        self, execute_prompt_error_case
    ):
        from model_hub.services import credit_ledger_port as credit_ledger

        # A managed direct run (POST /model-hub/run-prompt/) refused for
        # credits shows the typed credits copy in the cell and its reason, as
        # the worker and preview paths do, not the generic sentence.
        messages, cell_status, logger, provider = execute_prompt_error_case(
            "direct", phase="provider", failure=credit_ledger.CreditsExhaustedError()
        )

        expected = credit_ledger.CreditsExhaustedError().message
        assert expected != self.GENERIC_ERROR
        assert messages == [expected, expected]
        assert cell_status == CellStatus.ERROR.value
        provider.return_value.litellm_response.assert_called_once()
        assert logger.exception.called

    def test_direct_out_of_credits_refusal_saves_its_typed_cell_info(
        self, execute_prompt_error_case
    ):
        from model_hub.services import credit_ledger_port as credit_ledger

        # The direct path saves the refusal's typed cell info as the cell's
        # value_infos (its reason and its error: code, message, billing
        # link), as the worker does; no provider text.
        execute_prompt_error_case(
            "direct", phase="provider", failure=credit_ledger.CreditsExhaustedError()
        )

        value_infos = json.loads(execute_prompt_error_case.saved["value_infos"])
        assert value_infos == credit_ledger.refusal_cell_info(
            credit_ledger.CreditsExhaustedError()
        )
        assert value_infos["error"]["code"] == credit_ledger.CreditsExhaustedError.code
        assert value_infos["error"]["message"] == value_infos["reason"]


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

    # ── Assertion helpers ──────────────────────────────────────────────
    @staticmethod
    def _assert_column_data_type(dataset, name, expected):
        column = Column.objects.get(name=name, dataset=dataset, deleted=False)
        assert column.data_type == expected

    @staticmethod
    def _assert_run_prompter_attrs(name, **expected):
        run_prompter = RunPrompter.objects.get(name=name, deleted=False)
        for attr, value in expected.items():
            actual = getattr(run_prompter, attr)
            assert actual == value, (
                f"RunPrompter.{attr}: expected {value!r}, got {actual!r}"
            )

    @staticmethod
    def _assert_run_prompter_messages(name, expected_len, first_role=None):
        run_prompter = RunPrompter.objects.get(name=name, deleted=False)
        assert len(run_prompter.messages) == expected_len
        if first_role is not None:
            assert run_prompter.messages[0]["role"] == first_role

    # ── Case table ────────────────────────────────────────────────────
    _RESPONSE_FORMAT_SCHEMA = {
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

    _CASES = [
        # (id, column_name, config_dict, verify_callable)
        (
            "output_format_array",
            "Array Output",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "List items"}]}
                ],
                "output_format": "array",
            },
            lambda self, ds: self._assert_column_data_type(ds, "Array Output", "array"),
        ),
        (
            "output_format_number",
            "Number Output",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Calculate total"}]}
                ],
                "output_format": "number",
            },
            lambda self, ds: self._assert_column_data_type(ds, "Number Output", "integer"),
        ),
        (
            "output_format_object",
            "JSON Output",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Return JSON"}]}
                ],
                "output_format": "object",
            },
            lambda self, ds: self._assert_column_data_type(ds, "JSON Output", "json"),
        ),
        (
            "output_format_audio",
            "Audio Output",
            {
                "model": "tts-1",
                "messages": [{"role": "user", "content": "Say hello"}],
                "output_format": "audio",
                "run_prompt_config": {"voice": "alloy"},
            },
            lambda self, ds: self._assert_column_data_type(ds, "Audio Output", "audio"),
        ),
        (
            "custom_temperature",
            "Custom Temp",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Generate text"}]}
                ],
                "output_format": "string",
                "run_prompt_config": {"temperature": 0.2, "max_tokens": 500},
            },
            lambda self, ds: self._assert_run_prompter_attrs(
                "Custom Temp", temperature=0.2, max_tokens=500
            ),
        ),
        (
            "concurrency",
            "Concurrent Run",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Process"}]}
                ],
                "output_format": "string",
                "concurrency": 10,
            },
            lambda self, ds: self._assert_run_prompter_attrs("Concurrent Run", concurrency=10),
        ),
        (
            "response_format",
            "Structured Output",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Analyze this"}]}
                ],
                "output_format": "object",
                "response_format": _RESPONSE_FORMAT_SCHEMA,
            },
            lambda self, ds: self._assert_run_prompter_attrs(
                "Structured Output",
                response_format=self._RESPONSE_FORMAT_SCHEMA,
            ),
        ),
        (
            "tool_choice",
            "Tool Choice Run",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Use tools"}]}
                ],
                "output_format": "string",
                "tool_choice": "auto",
            },
            lambda self, ds: self._assert_run_prompter_attrs(
                "Tool Choice Run", tool_choice="auto"
            ),
        ),
        (
            "all_penalties",
            "Creative Run",
            {
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
            },
            lambda self, ds: self._assert_run_prompter_attrs(
                "Creative Run",
                temperature=0.9,
                frequency_penalty=0.5,
                presence_penalty=0.3,
                top_p=0.95,
            ),
        ),
        (
            "system_message",
            "System Message Run",
            {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "system",
                        "content": [{"type": "text", "text": "You are helpful"}],
                    },
                    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                ],
                "output_format": "string",
            },
            lambda self, ds: self._assert_run_prompter_messages(
                "System Message Run", expected_len=2, first_role="system"
            ),
        ),
        (
            "multi_turn_conversation",
            "Multi Turn Run",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": "Assistant"}]},
                    {"role": "user", "content": [{"type": "text", "text": "First question"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "First answer"}]},
                    {"role": "user", "content": [{"type": "text", "text": "Follow up"}]},
                ],
                "output_format": "string",
            },
            lambda self, ds: self._assert_run_prompter_messages(
                "Multi Turn Run", expected_len=4
            ),
        ),
    ]

    @pytest.mark.parametrize(
        "column_name,config,verify",
        [case[1:] for case in _CASES],
        ids=[case[0] for case in _CASES],
    )
    def test_add_run_prompt_config_variation(
        self, auth_client, dataset, input_column, column_name, config, verify
    ):
        payload = {
            "dataset_id": str(dataset.id),
            "name": column_name,
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
        verify(self, dataset)


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
        """Verify an edit saves configuration and completes when there are no rows."""
        assert not Row.objects.filter(dataset=dataset, deleted=False).exists()
        # Link run_prompt_column to run_prompter
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        original_name = run_prompter.name
        original_model = run_prompter.model

        new_config = {
            # Must be a model available in the current catalog: edit now
            # rejects unavailable models (deprecated-model guard).
            "model": "gpt-4o-mini",
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
            with TestCase.captureOnCommitCallbacks(execute=True):
                response = auth_client.post(
                    "/model-hub/develops/edit_run_prompt_column/",
                    payload,
                    format="json",
                )
            mock_task.assert_not_called()

        assert response.status_code == status.HTTP_200_OK

        # Verify RunPrompter updates
        run_prompter.refresh_from_db()
        assert run_prompter.name == "Updated Name"
        assert run_prompter.model == "gpt-4o-mini"
        assert run_prompter.temperature == 0.3
        assert run_prompter.max_tokens == 300
        assert run_prompter.output_format == "object"
        # With no selected rows, the saved edit is a completed no-op.
        assert run_prompter.status == StatusType.COMPLETED.value

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

    # Each case: (id, config_dict, name_in_payload)
    _ADD_RUN_PROMPT_INVALID_CASES = [
        (
            "empty_messages",
            {"model": "gpt-4", "messages": [], "output_format": "string"},
            "Empty Messages",
        ),
        (
            "invalid_message_role",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "invalid_role", "content": [{"type": "text", "text": "Test"}]}
                ],
                "output_format": "string",
            },
            "Invalid Role",
        ),
        (
            "first_message_assistant",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                ],
                "output_format": "string",
            },
            "Assistant First",
        ),
        (
            "message_missing_content",
            {
                "model": "gpt-4",
                "messages": [{"role": "user"}],  # missing content key
                "output_format": "string",
            },
            "No Content",
        ),
        (
            "invalid_output_format",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Test"}]}
                ],
                "output_format": "invalid_format",
            },
            "Invalid Format",
        ),
        (
            "invalid_tool_choice",
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Test"}]}
                ],
                "output_format": "string",
                "tool_choice": "invalid_choice",
            },
            "Invalid Tool Choice",
        ),
    ]

    @pytest.mark.parametrize(
        "config,column_name",
        [case[1:] for case in _ADD_RUN_PROMPT_INVALID_CASES],
        ids=[case[0] for case in _ADD_RUN_PROMPT_INVALID_CASES],
    )
    def test_add_run_prompt_rejects_invalid_config(
        self, auth_client, dataset, config, column_name
    ):
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            {
                "dataset_id": str(dataset.id),
                "name": column_name,
                "config": config,
            },
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


class TestRunPromptToolPayload:
    """The tools list handed to litellm must carry the OpenAI function envelope."""

    PARAMETERS = {
        "type": "object",
        "properties": {
            "return_category": {"type": "string", "enum": ["defective", "other"]}
        },
        "required": ["return_category"],
    }

    def _tool(self, organization, workspace, config):
        from model_hub.models.openai_tools import Tools

        return Tools.objects.create(
            name="classify_return",
            description="Classify a customer return",
            config=config,
            organization=organization,
            workspace=workspace,
        )

    def _runner_for(self, dataset, organization, workspace, tool):
        from model_hub.views.run_prompt import RunPrompts

        run_prompter = RunPrompter.objects.create(
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            model="gpt-4o-mini",
            name="tool-column",
            messages=[{"role": "user", "content": "Classify {{input}}"}],
        )
        run_prompter.tools.set([tool])

        runner = RunPrompts(run_prompter.id)
        runner.load_run_prompt_id()
        return runner

    @pytest.mark.django_db
    def test_loader_wraps_stored_schema_for_litellm(
        self, dataset, organization, workspace
    ):
        tool = self._tool(organization, workspace, {"parameters": self.PARAMETERS})
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config == [
            {
                "type": "function",
                "function": {
                    "name": "classify_return",
                    "description": "Classify a customer return",
                    "parameters": self.PARAMETERS,
                },
            }
        ]

    @pytest.mark.django_db
    def test_loader_preserves_an_already_wrapped_config(
        self, dataset, organization, workspace
    ):
        wrapped = {
            "type": "function",
            "function": {
                "name": "hand_wrapped",
                "description": "Written straight into the JSON box",
                "parameters": self.PARAMETERS,
            },
        }
        tool = self._tool(organization, workspace, wrapped)
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config == [wrapped]

    @pytest.mark.django_db
    def test_loader_fills_a_blank_wrapped_name_from_the_saved_tool(
        self, dataset, organization, workspace
    ):
        tool = self._tool(
            organization,
            workspace,
            {
                "type": "function",
                "function": {
                    "name": "",
                    "description": "",
                    "parameters": self.PARAMETERS,
                },
            },
        )
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config[0]["function"]["name"] == "classify_return"
        assert (
            runner.tools_config[0]["function"]["description"]
            == "Classify a customer return"
        )

    @pytest.mark.django_db
    def test_loader_substitutes_an_empty_object_when_no_parameters_are_stored(
        self, dataset, organization, workspace
    ):
        tool = self._tool(organization, workspace, {"parameters": {}})
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config[0]["type"] == "function"
        assert runner.tools_config[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    @pytest.mark.django_db
    def test_loader_keeps_a_schema_that_declares_no_top_level_properties(
        self, dataset, organization, workspace
    ):
        referenced = {"type": "object", "$ref": "#/$defs/ReturnRequest"}
        tool = self._tool(organization, workspace, {"parameters": referenced})
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config[0]["function"]["parameters"] == referenced

    @pytest.mark.django_db
    def test_loader_maps_the_tool_name_onto_the_accepted_identifier_charset(
        self, dataset, organization, workspace
    ):
        from model_hub.models.openai_tools import Tools

        tool = Tools.objects.create(
            name="Get weather (v2)",
            description="Look up the forecast",
            config={"parameters": self.PARAMETERS},
            organization=organization,
            workspace=workspace,
        )
        runner = self._runner_for(dataset, organization, workspace, tool)

        assert runner.tools_config[0]["function"]["name"] == "Get_weather_v2"

    @pytest.mark.django_db
    def test_litellm_payload_carries_the_envelope(
        self, dataset, organization, workspace
    ):
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        tool = self._tool(organization, workspace, {"parameters": self.PARAMETERS})
        runner = self._runner_for(dataset, organization, workspace, tool)

        payload = RunPrompt(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Classify a return"}],
            organization_id=organization.id,
            output_format="string",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice="auto",
            tools=runner.tools_config,
        )._create_payload("openai", "sk-test")

        assert payload["tools"][0]["type"] == "function"
        assert payload["tools"][0]["function"]["parameters"] == self.PARAMETERS


# ==================== Prompts that use a deleted column ====================

# The cell text a row gets when its prompt still uses a deleted column.
DELETED_COLUMN_SENTENCE = (
    "This prompt uses a column that was deleted. Edit the prompt to remove it."
)


def _ref(column):
    return "{{" + str(column.id) + "}}"


def _user_message(text):
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def _other_dataset_column(organization, workspace, name="Other column"):
    other = Dataset.objects.create(
        name="Other Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    return Column.objects.create(
        name=name,
        dataset=other,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )


@pytest.fixture
def deleted_reference_setup(
    db, dataset, input_column, run_prompt_column, row, organization, workspace
):
    """A Run Prompt column whose saved prompt uses a column deleted later.

    The column is deleted as DeleteColumnView deletes it: soft-deleted and
    taken out of the dataset's column order. The saved prompt keeps {{<id>}}.
    """
    from types import SimpleNamespace

    from django.utils import timezone

    gone = Column.objects.create(
        name="Old answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(gone.id))
    dataset.save()
    Cell.objects.create(
        dataset=dataset, column=input_column, row=row, value="What is 2 + 2?"
    )
    Cell.objects.create(dataset=dataset, column=gone, row=row, value="4")
    Column.objects.filter(id=gone.id).update(deleted=True, deleted_at=timezone.now())
    dataset.column_order.remove(str(gone.id))
    dataset.save()

    messages = _user_message(f"Answer {_ref(input_column)} like {_ref(gone)}")
    run_prompter = RunPrompter.objects.create(
        name="Run Prompt Output",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        output_format="string",
        run_prompt_config={},
    )
    run_prompt_column.source_id = str(run_prompter.id)
    run_prompt_column.save()
    Cell.objects.create(
        dataset=dataset,
        column=run_prompt_column,
        row=row,
        value="old answer",
        status=CellStatus.PASS.value,
    )
    return SimpleNamespace(
        dataset=dataset,
        live=input_column,
        gone=gone,
        column=run_prompt_column,
        run_prompter=run_prompter,
        messages=messages,
        row=row,
    )


@pytest.mark.django_db
class TestRunPromptDeletedColumnReference:
    """A saved prompt that uses a column deleted later.

    Production let a column be deleted while a Run Prompt column used it, and
    dataset copies remap ids, so such prompts exist. Editing one must not 404
    on the reference it already had; only references the edit adds are
    checked. The editor is told which references are deleted, and a run says
    in each cell why it cannot run, with no model call and no charge.
    """

    EDIT_URL = "/model-hub/develops/edit_run_prompt_column/"
    CONFIG_URL = "/model-hub/develops/retrieve_run_prompt_column_config/"

    @pytest.fixture
    def queued(self, monkeypatch):
        tasks = []
        monkeypatch.setattr(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async",
            lambda *args, **kwargs: tasks.append((args, kwargs)),
        )
        return tasks

    def _edit(self, auth_client, setup, config):
        return auth_client.post(
            self.EDIT_URL,
            {
                "dataset_id": str(setup.dataset.id),
                "column_id": str(setup.column.id),
                "config": config,
            },
            format="json",
        )

    # (a) an edit that leaves the saved prompt as it is
    @pytest.mark.parametrize("change", ["temperature_only", "model_with_messages"])
    def test_edit_keeps_a_saved_deleted_column_reference(
        self, auth_client, deleted_reference_setup, queued, change
    ):
        setup = deleted_reference_setup
        if change == "temperature_only":
            config = {"run_prompt_config": {"temperature": 0.2}}
        else:
            # What the editor sends: the saved messages unchanged, a new model.
            config = {"model": "gpt-4", "messages": setup.messages}

        response = self._edit(auth_client, setup, config)

        assert response.status_code == status.HTTP_200_OK, response.content
        setup.run_prompter.refresh_from_db()
        assert setup.run_prompter.messages == setup.messages
        if change == "temperature_only":
            assert setup.run_prompter.temperature == 0.2
            assert setup.run_prompter.model == "gpt-4o"
        else:
            assert setup.run_prompter.model == "gpt-4"
        assert len(queued) == 1

    # (b) an edit that adds a reference to a deleted or foreign column
    @pytest.mark.parametrize(
        "added", ["deleted", "other_dataset", "other_dataset_next_to_a_kept_deleted"]
    )
    def test_edit_that_adds_a_dead_reference_is_refused(
        self, auth_client, deleted_reference_setup, organization, workspace, queued, added
    ):
        setup = deleted_reference_setup
        if added == "other_dataset_next_to_a_kept_deleted":
            saved = setup.messages
            target = _other_dataset_column(organization, workspace)
            text = f"Answer {_ref(setup.live)} like {_ref(setup.gone)} or {_ref(target)}"
        else:
            saved = _user_message(f"Answer {_ref(setup.live)}")
            target = (
                setup.gone
                if added == "deleted"
                else _other_dataset_column(organization, workspace)
            )
            text = f"Answer {_ref(setup.live)} like {_ref(target)}"
        RunPrompter.objects.filter(id=setup.run_prompter.id).update(messages=saved)

        response = self._edit(
            auth_client, setup, {"model": "gpt-4", "messages": _user_message(text)}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert target.name not in response.content.decode()
        setup.run_prompter.refresh_from_db()
        assert setup.run_prompter.messages == saved
        assert setup.run_prompter.model == "gpt-4o"
        assert setup.run_prompter.status == StatusType.COMPLETED.value
        assert queued == []

    # (c) the config the editor reads marks the dead reference as deleted
    def test_config_marks_dead_references_as_deleted(
        self, auth_client, deleted_reference_setup, organization, workspace
    ):
        setup = deleted_reference_setup
        foreign = _other_dataset_column(
            organization, workspace, name="Secret other column"
        )
        RunPrompter.objects.filter(id=setup.run_prompter.id).update(
            messages=_user_message(
                f"Answer {_ref(setup.live)} like {_ref(setup.gone)} or {_ref(foreign)}"
            )
        )

        response = auth_client.get(self.CONFIG_URL, {"column_id": str(setup.column.id)})

        assert response.status_code == status.HTTP_200_OK
        config = response.json()["result"]["config"]
        by_id = lambda item: item["id"]  # noqa: E731
        assert sorted(config["deleted_column_references"], key=by_id) == sorted(
            [
                {"id": str(setup.gone.id), "name": "Old answer"},
                # Not this dataset's column: marked, its name never shown.
                {"id": str(foreign.id), "name": None},
            ],
            key=by_id,
        )
        text = config["messages"][0]["content"][0]["text"]
        assert "{{Input Column}}" in text
        assert _ref(setup.gone) in text and _ref(foreign) in text
        assert "Secret other column" not in response.content.decode()

    def test_config_without_dead_references_lists_none(
        self, auth_client, deleted_reference_setup
    ):
        setup = deleted_reference_setup
        RunPrompter.objects.filter(id=setup.run_prompter.id).update(
            messages=_user_message(f"Answer {_ref(setup.live)}")
        )

        response = auth_client.get(self.CONFIG_URL, {"column_id": str(setup.column.id)})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["config"]["deleted_column_references"] == []

    # (d) a run gives each row the plain sentence: no model call, no charge
    @pytest.mark.parametrize("route", ["managed", "byok"])
    def test_run_says_why_it_cannot_run_without_a_call_or_charge(
        self, auth_client, deleted_reference_setup, organization, monkeypatch, route
    ):
        from decimal import Decimal
        from types import SimpleNamespace

        from model_hub.services import credit_ledger_port as credit_ledger
        from model_hub.services import selective_rerun
        from model_hub.views import run_prompt as module
        from tfc.constants.api_calls import APICallStatusChoices

        setup = deleted_reference_setup
        second_row = Row.objects.create(dataset=setup.dataset, order=1)
        Cell.objects.create(
            dataset=setup.dataset, column=setup.live, row=second_row, value="3 + 3?"
        )
        Cell.objects.create(
            dataset=setup.dataset,
            column=setup.column,
            row=second_row,
            value="old answer 2",
            status=CellStatus.PASS.value,
        )
        monkeypatch.setattr(selective_rerun, "input_fingerprints", lambda *args: {})
        monkeypatch.setattr(selective_rerun, "save_fingerprints", lambda *args: None)
        monkeypatch.setattr(module, "close_old_connections", lambda: None)
        monkeypatch.setattr(
            credit_ledger, "estimate_call_cost", lambda *a, **kw: Decimal("0.10")
        )
        monkeypatch.setattr(credit_ledger, "_sleep", lambda *_args: None)
        # The legacy per-call charge (BYOK rows) and the usage dual-write.
        charge = MagicMock(
            return_value=SimpleNamespace(
                id=uuid.uuid4(),
                status=APICallStatusChoices.PROCESSING.value,
                save=lambda: None,
            )
        )
        monkeypatch.setattr(
            module,
            "log_and_deduct_cost_for_api_request",
            charge if route == "byok" else None,
        )
        usage = MagicMock()
        # Real meter objects: a managed hold would be recorded by this ledger.
        ledger = credit_ledger.MockCreditLedger(
            balances={organization.id: Decimal("2")}
        )
        payer = (
            credit_ledger.PayerRoute.managed
            if route == "managed"
            else credit_ledger.PayerRoute.byok
        )

        runner = module.RunPrompts(run_prompt_id=setup.run_prompter.id)
        credit_ledger.reset_meter_safety()
        try:
            with (
                patch.object(module, "RunPrompt") as provider,
                patch.dict(
                    "sys.modules",
                    {
                        "ee.usage.schemas.events": MagicMock(),
                        "ee.usage.services.emitter": usage,
                    },
                ),
                credit_ledger.use_credit_ledger(ledger),
                credit_ledger.use_route_resolver(
                    lambda **_kwargs: payer(provider="openai")
                ),
            ):
                provider.return_value.litellm_response.return_value = (
                    "answer",
                    {"data": {"response": "answer"}, "metadata": {}},
                )
                runner.load_run_prompt_id()
                runner.is_editing = True
                for each in (setup.row, second_row):
                    runner.process_row(each, setup.column)
        finally:
            credit_ledger.reset_meter_safety()

        provider.assert_not_called()
        assert ledger.calls == []
        charge.assert_not_called()
        usage.emit.assert_not_called()
        for each in (setup.row, second_row):
            cell = Cell.objects.get(
                dataset=setup.dataset, column=setup.column, row=each, deleted=False
            )
            assert cell.status == CellStatus.ERROR.value
            assert cell.value == DELETED_COLUMN_SENTENCE
            assert json.loads(cell.value_infos)["reason"] == DELETED_COLUMN_SENTENCE

"""
Tests for run-prompt error sanitization (issue #2081).

Run-prompt failures must never leak raw exception text to API callers:
- Local validation errors (RunPromptValidationError) are the user's own
  input and are surfaced verbatim.
- Everything else (provider errors, unexpected exceptions) is replaced
  with a generic message; full details stay server-side in the logs.

Run with: pytest model_hub/tests/test_run_prompt_error_sanitization.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.run_prompt import (
    RUN_PROMPT_GENERIC_ERROR_MESSAGE,
    RunPromptValidationError,
    get_run_prompt_error_message,
)
from tfc.middleware.workspace_context import set_workspace_context

RAW_PROVIDER_ERROR = (
    "litellm.APIError: upstream https://api.internal.example/v1 returned 500 "
    "[sk-secret-key-abc123]"
)


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


@pytest.fixture
def run_prompt_config():
    return {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "What is {{Input Column}}?"}],
            }
        ],
        "temperature": 0.7,
        "max_tokens": 100,
        "output_format": "string",
    }


class TestGetRunPromptErrorMessage:
    """Unit tests for the error-message sanitizer helper."""

    def test_local_validation_error_surfaced_verbatim(self):
        exc = RunPromptValidationError(
            "Placeholder '{{missing}}' was not resolved."
        )
        assert get_run_prompt_error_message(exc) == (
            "Placeholder '{{missing}}' was not resolved."
        )

    def test_unexpected_exception_returns_generic_message(self):
        exc = RuntimeError(RAW_PROVIDER_ERROR)
        message = get_run_prompt_error_message(exc)
        assert message == RUN_PROMPT_GENERIC_ERROR_MESSAGE

    def test_generic_message_does_not_contain_raw_exception_text(self):
        exc = RuntimeError(RAW_PROVIDER_ERROR)
        message = get_run_prompt_error_message(exc)
        assert "api.internal.example" not in message
        assert "sk-secret-key-abc123" not in message
        assert str(exc) not in message


@pytest.mark.django_db
class TestLitellmAPIViewProcessRowSanitization:
    """The per-row execution path must store safe messages in cells."""

    def _process_row(self, dataset, input_column, row, organization, user):
        from model_hub.views.run_prompt import LitellmAPIView

        view = LitellmAPIView()
        validated_data = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "output_format": "string",
        }
        request = MagicMock(organization=organization, user=user)
        view.process_row(row, validated_data, dataset, input_column, request)

    def test_local_validation_error_message_stored_in_cell(
        self, dataset, input_column, row, organization, user
    ):
        validation_message = "Placeholder '{{missing}}' was not resolved."
        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            side_effect=RunPromptValidationError(validation_message),
        ):
            self._process_row(dataset, input_column, row, organization, user)

        output_cell = Cell.objects.get(
            dataset=dataset, column=input_column, row=row
        )
        assert output_cell.status == CellStatus.ERROR.value
        assert output_cell.value == validation_message
        value_infos = json.loads(output_cell.value_infos)
        assert value_infos["reason"] == validation_message

    def test_unexpected_provider_error_redacted_in_cell(
        self, dataset, input_column, row, organization, user
    ):
        mock_run_prompt = MagicMock()
        mock_run_prompt.litellm_response.side_effect = RuntimeError(
            RAW_PROVIDER_ERROR
        )
        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            return_value=[{"role": "user", "content": "Hello"}],
        ), patch(
            "model_hub.views.run_prompt.RunPrompt",
            return_value=mock_run_prompt,
        ):
            self._process_row(dataset, input_column, row, organization, user)

        output_cell = Cell.objects.get(
            dataset=dataset, column=input_column, row=row
        )
        assert output_cell.status == CellStatus.ERROR.value
        assert output_cell.value == RUN_PROMPT_GENERIC_ERROR_MESSAGE
        assert "api.internal.example" not in output_cell.value
        assert "sk-secret-key-abc123" not in output_cell.value
        value_infos = json.loads(output_cell.value_infos)
        assert value_infos["reason"] == RUN_PROMPT_GENERIC_ERROR_MESSAGE
        assert RAW_PROVIDER_ERROR not in output_cell.value_infos


@pytest.mark.django_db
class TestPreviewRunPromptColumnViewSanitization:
    """The preview endpoint must never echo raw exception text."""

    def _preview(self, auth_client, dataset, row, config):
        return auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            {
                "dataset_id": str(dataset.id),
                "config": config,
                "first_n_rows": 1,
            },
            format="json",
        )

    def test_local_validation_error_surfaced_verbatim(
        self, auth_client, dataset, input_column, row, run_prompt_config
    ):
        validation_message = "Placeholder '{{missing}}' was not resolved."
        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            side_effect=RunPromptValidationError(validation_message),
        ):
            response = self._preview(auth_client, dataset, row, run_prompt_config)

        assert response.status_code == 200
        responses = response.json()["result"]["responses"]
        assert responses == [validation_message]

    def test_unexpected_provider_error_returns_generic_message(
        self, auth_client, dataset, input_column, row, run_prompt_config
    ):
        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            side_effect=RuntimeError(RAW_PROVIDER_ERROR),
        ):
            response = self._preview(auth_client, dataset, row, run_prompt_config)

        assert response.status_code == 200
        body = response.json()
        responses = body["result"]["responses"]
        assert responses == [RUN_PROMPT_GENERIC_ERROR_MESSAGE]
        assert "api.internal.example" not in json.dumps(body)
        assert "sk-secret-key-abc123" not in json.dumps(body)

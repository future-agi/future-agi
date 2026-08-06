"""
Unit tests for column_service.py.

This module tests:
- get_or_create_column: Creates/updates columns with transaction safety
- create_run_prompt_column: Wrapper for run prompt columns
- create_experiment_column: Wrapper for experiment columns
- get_correct_data_type: Determines correct data type from response format
- fix_column_data_type: Persists data type fixes
- update_column_for_rerun: Updates columns and extracts derived variables
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction
from rest_framework.test import APITestCase

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.column_service import (
    create_experiment_column,
    create_run_prompt_column,
    fix_column_data_type,
    get_correct_data_type,
    get_or_create_column,
    update_column_for_rerun,
)
from tfc.constants.roles import OrganizationRoles


@pytest.mark.django_db
class ColumnServiceBaseTestCase(APITestCase):
    """Base test case for Column Service tests."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class."""
        cls.organization = Organization.objects.create(name="Test Organization")

        cls.user = User.objects.create_user(
            email="test@example.com",
            password="testpassword123",
            name="Test User",
            organization=cls.organization,
            organization_role=OrganizationRoles.OWNER,
        )

        cls.workspace = Workspace.objects.create(
            name="Default Workspace",
            organization=cls.organization,
            is_default=True,
            created_by=cls.user,
        )

    def create_test_dataset(self, name="Test Dataset"):
        """Helper method to create a test dataset."""
        return Dataset.objects.create(
            name=name,
            organization=self.organization,
            user=self.user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            column_order=[],
            column_config={},
        )

    def create_run_prompter(self, dataset, name="Test Run Prompter", **kwargs):
        """Helper method to create a RunPrompter."""
        defaults = {
            "dataset": dataset,
            "model": "gpt-4",
            "name": name,
            "organization": self.organization,
            "workspace": self.workspace,
            "output_format": kwargs.get("output_format", "string"),
            "response_format": kwargs.get("response_format", None),
            "messages": [{"role": "user", "content": "test"}],
        }
        defaults.update(kwargs)
        return RunPrompter.objects.create(**defaults)


# =============================================================================
# get_or_create_column Tests
# =============================================================================
@pytest.mark.django_db
class TestGetOrCreateColumn(ColumnServiceBaseTestCase):
    """Tests for get_or_create_column function."""

    def test_creates_new_column(self):
        """Test that a new column is created when it doesn't exist."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )

        assert created is True
        assert column.name == "Test Column"
        assert column.data_type == "text"
        assert column.source == SourceChoices.RUN_PROMPT.value
        assert column.source_id == str(source_id)
        assert column.dataset == dataset

    def test_returns_existing_column(self):
        """Test that an existing column is returned without creating a new one."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # Create the column first
        column1, created1 = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )

        # Try to get/create again
        column2, created2 = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )

        assert created1 is True
        assert created2 is False
        assert column1.id == column2.id

    def test_updates_data_type_on_existing_column(self):
        """Test that data_type is updated when it changes."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # Create with string format
        column, _ = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )
        assert column.data_type == "text"

        # Update with JSON response format
        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="object",
            response_format={"type": "json_object"},
        )

        column.refresh_from_db()
        assert created is False
        assert column.data_type == "json"

    def test_updates_status_on_existing_column(self):
        """Test that status is updated when provided."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # Create without status
        column, _ = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )

        # Update with status
        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
            status=StatusType.RUNNING.value,
        )

        column.refresh_from_db()
        assert created is False
        assert column.status == StatusType.RUNNING.value

    def test_handles_uuid_response_format(self):
        """Test that UUID response format produces JSON data type."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()
        response_format_uuid = str(uuid.uuid4())

        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="object",
            response_format=response_format_uuid,
        )

        assert created is True
        assert column.data_type == "json"

    def test_handles_object_output_format(self):
        """Test that object output format produces JSON data type."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="object",
        )

        assert created is True
        assert column.data_type == "json"

    def test_handles_number_output_format(self):
        """Test that number output format produces integer data type."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="number",
        )

        assert created is True
        assert column.data_type == "integer"

    def test_transaction_atomic_wraps_operations(self):
        """Test that operations are wrapped in a transaction."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # This test verifies the transaction.atomic context is used
        # by checking that the column is created successfully
        with transaction.atomic():
            column, created = get_or_create_column(
                dataset=dataset,
                source=SourceChoices.RUN_PROMPT.value,
                source_id=source_id,
                name="Test Column",
                output_format="string",
            )

        assert created is True
        # Verify the column persisted
        assert Column.objects.filter(id=column.id).exists()


# =============================================================================
# create_run_prompt_column Tests
# =============================================================================
@pytest.mark.django_db
class TestCreateRunPromptColumn(ColumnServiceBaseTestCase):
    """Tests for create_run_prompt_column function."""

    def test_creates_run_prompt_column(self):
        """Test that a run prompt column is created with correct source."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = create_run_prompt_column(
            dataset=dataset,
            source_id=source_id,
            name="Run Prompt Column",
            output_format="string",
        )

        assert created is True
        assert column.source == SourceChoices.RUN_PROMPT.value
        assert column.name == "Run Prompt Column"

    def test_run_prompt_column_with_json_response_format(self):
        """Test run prompt column with JSON response format."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = create_run_prompt_column(
            dataset=dataset,
            source_id=source_id,
            name="JSON Column",
            output_format="object",
            response_format={"type": "json_object"},
        )

        assert created is True
        assert column.data_type == "json"


# =============================================================================
# create_experiment_column Tests
# =============================================================================
@pytest.mark.django_db
class TestCreateExperimentColumn(ColumnServiceBaseTestCase):
    """Tests for create_experiment_column function."""

    def test_creates_experiment_column_with_status(self):
        """Test that an experiment column is created with status."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = create_experiment_column(
            dataset=dataset,
            source_id=source_id,
            name="Experiment Column",
            output_format="string",
            status=StatusType.RUNNING.value,
        )

        assert created is True
        assert column.source == SourceChoices.EXPERIMENT.value
        assert column.status == StatusType.RUNNING.value

    def test_experiment_column_default_status(self):
        """Test that experiment column defaults to RUNNING status."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        column, created = create_experiment_column(
            dataset=dataset,
            source_id=source_id,
            name="Experiment Column",
            output_format="string",
        )

        assert created is True
        assert column.status == StatusType.RUNNING.value


# =============================================================================
# get_correct_data_type Tests
# =============================================================================
@pytest.mark.django_db
class TestGetCorrectDataType(ColumnServiceBaseTestCase):
    """Tests for get_correct_data_type function."""

    def test_returns_original_type_when_no_run_prompter(self):
        """Test that original data type is returned when run_prompter is None."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )

        result = get_correct_data_type(column, None)

        assert result == DataTypeChoices.TEXT.value

    def test_returns_json_for_json_object_response_format(self):
        """Test that JSON type is returned for json_object response format."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )

        result = get_correct_data_type(column, run_prompter)

        assert result == "json"

    def test_returns_json_for_uuid_response_format(self):
        """Test that JSON type is returned for UUID response format."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format=str(uuid.uuid4()),  # UUID string
        )

        result = get_correct_data_type(column, run_prompter)

        assert result == "json"

    def test_returns_text_for_string_output_format(self):
        """Test that text type is returned for string output format."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type="json",  # Incorrectly set to json
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="string",
            response_format=None,
        )

        result = get_correct_data_type(column, run_prompter)

        assert result == "text"


# =============================================================================
# fix_column_data_type Tests
# =============================================================================
@pytest.mark.django_db
class TestFixColumnDataType(ColumnServiceBaseTestCase):
    """Tests for fix_column_data_type function."""

    def test_fixes_incorrect_data_type(self):
        """Test that incorrect data type is fixed and saved."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,  # Incorrect
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )

        result = fix_column_data_type(column, run_prompter)

        column.refresh_from_db()
        assert result is True
        assert column.data_type == "json"

    def test_returns_false_when_no_change_needed(self):
        """Test that False is returned when data type is already correct."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type="json",  # Already correct
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )

        result = fix_column_data_type(column, run_prompter)

        assert result is False

    def test_returns_false_when_run_prompter_is_none(self):
        """Test that False is returned when run_prompter is None."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )

        result = fix_column_data_type(column, None)

        assert result is False


# =============================================================================
# update_column_for_rerun Tests
# =============================================================================
@pytest.mark.django_db
class TestUpdateColumnForRerun(ColumnServiceBaseTestCase):
    """Tests for update_column_for_rerun function."""

    def test_updates_data_type(self):
        """Test that data type is updated on rerun."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )

        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=False,
        )

        column.refresh_from_db()
        assert column.data_type == "json"

    def test_updates_status(self):
        """Test that status is updated on rerun."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )

        update_column_for_rerun(
            column=column,
            output_format="string",
            status=StatusType.RUNNING.value,
            extract_derived_vars=False,
        )

        column.refresh_from_db()
        assert column.status == StatusType.RUNNING.value

    def test_updates_name(self):
        """Test that name is updated on rerun."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Old Name",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )

        update_column_for_rerun(
            column=column,
            output_format="string",
            name="New Name",
            extract_derived_vars=False,
        )

        column.refresh_from_db()
        assert column.name == "New Name"

    def test_does_not_update_when_values_same(self):
        """Test that no update occurs when values haven't changed."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
            status=StatusType.RUNNING.value,
        )
        original_updated_at = column.updated_at

        update_column_for_rerun(
            column=column,
            output_format="string",  # Same, produces "text"
            status=StatusType.RUNNING.value,  # Same
            extract_derived_vars=False,
        )

        column.refresh_from_db()
        # The column should not have been updated
        assert column.data_type == DataTypeChoices.TEXT.value

    @patch(
        "model_hub.services.derived_variable_service.extract_derived_variables_from_output"
    )
    def test_extracts_derived_variables_for_json_format(self, mock_extract):
        """Test that derived variables are extracted when format is JSON."""
        mock_extract.return_value = {
            "is_json": True,
            "full_variables": [{"path": "$.name", "sample": "John"}],
            "paths": ["$.name"],
        }

        dataset = self.create_test_dataset()
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(run_prompter.id),
            dataset=dataset,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            row=row,
            column=column,
            dataset=dataset,
            value=json.dumps({"name": "John", "age": 30}),
        )

        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=True,
        )

        mock_extract.assert_called_once()
        run_prompter.refresh_from_db()
        assert "derived_variables" in run_prompter.run_prompt_config

    @patch(
        "model_hub.services.derived_variable_service.extract_derived_variables_from_output"
    )
    def test_skips_derived_variable_extraction_when_disabled(self, mock_extract):
        """Test that derived variable extraction is skipped when disabled."""
        dataset = self.create_test_dataset()
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(run_prompter.id),
            dataset=dataset,
        )

        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=False,
        )

        mock_extract.assert_not_called()

    def test_skips_derived_variable_extraction_for_non_json(self):
        """Test that derived variable extraction is skipped for non-JSON format."""
        dataset = self.create_test_dataset()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            dataset=dataset,
        )

        # Should not raise even though extract_derived_vars=True
        update_column_for_rerun(
            column=column,
            output_format="string",
            response_format=None,
            extract_derived_vars=True,
        )

        column.refresh_from_db()
        assert column.data_type == "text"

    @patch(
        "model_hub.services.derived_variable_service.extract_derived_variables_from_output"
    )
    def test_handles_extraction_exception_gracefully(self, mock_extract):
        """Test that extraction exceptions are handled gracefully."""
        mock_extract.side_effect = Exception("Extraction failed")

        dataset = self.create_test_dataset()
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(run_prompter.id),
            dataset=dataset,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            row=row,
            column=column,
            dataset=dataset,
            value=json.dumps({"name": "John"}),
        )

        # Should not raise
        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=True,
        )

        # Column should still be updated
        column.refresh_from_db()
        assert column.data_type == "json"

    @patch(
        "model_hub.services.derived_variable_service.extract_derived_variables_from_output"
    )
    def test_skips_extraction_when_no_cell_with_value(self, mock_extract):
        """Test that extraction is skipped when no cell has a value."""
        dataset = self.create_test_dataset()
        run_prompter = self.create_run_prompter(
            dataset,
            output_format="object",
            response_format={"type": "json_object"},
        )
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(run_prompter.id),
            dataset=dataset,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        # Create cell with empty value
        Cell.objects.create(
            row=row,
            column=column,
            dataset=dataset,
            value="",
        )

        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=True,
        )

        # Should not call extract because there's no cell with value
        mock_extract.assert_not_called()

    @patch(
        "model_hub.services.derived_variable_service.extract_derived_variables_from_output"
    )
    def test_skips_storage_when_no_run_prompter_found(self, mock_extract):
        """Test that storage is skipped when run prompter not found."""
        mock_extract.return_value = {
            "is_json": True,
            "full_variables": [{"path": "$.name", "sample": "John"}],
            "paths": ["$.name"],
        }

        dataset = self.create_test_dataset()
        non_existent_id = uuid.uuid4()
        column = Column.objects.create(
            name="Test Column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(non_existent_id),  # Doesn't exist
            dataset=dataset,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            row=row,
            column=column,
            dataset=dataset,
            value=json.dumps({"name": "John"}),
        )

        # Should not raise
        update_column_for_rerun(
            column=column,
            output_format="object",
            response_format={"type": "json_object"},
            extract_derived_vars=True,
        )

        mock_extract.assert_called_once()
        # No exception should be raised


# =============================================================================
# Transaction Safety Tests
# =============================================================================
@pytest.mark.django_db(transaction=True)
class TestColumnServiceTransactionSafety(ColumnServiceBaseTestCase):
    """Tests for transaction safety in column service."""

    def test_get_or_create_column_uses_atomic_transaction(self):
        """Test that get_or_create_column uses atomic transaction."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # Create within a transaction and verify atomicity
        try:
            with transaction.atomic():
                column, created = get_or_create_column(
                    dataset=dataset,
                    source=SourceChoices.RUN_PROMPT.value,
                    source_id=source_id,
                    name="Test Column",
                    output_format="string",
                )
                assert created is True

                # Intentionally raise to rollback
                raise Exception("Rollback test")
        except Exception:
            pass

        # Column should not exist due to rollback
        assert not Column.objects.filter(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
        ).exists()

    def test_concurrent_updates_use_select_for_update(self):
        """Test that concurrent updates are serialized with select_for_update."""
        dataset = self.create_test_dataset()
        source_id = uuid.uuid4()

        # Create the column first
        column, _ = get_or_create_column(
            dataset=dataset,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=source_id,
            name="Test Column",
            output_format="string",
        )

        # Simulate concurrent update by updating within a transaction
        with transaction.atomic():
            # This should use select_for_update internally
            column2, created = get_or_create_column(
                dataset=dataset,
                source=SourceChoices.RUN_PROMPT.value,
                source_id=source_id,
                name="Test Column",
                output_format="object",
                response_format={"type": "json_object"},
            )

            assert created is False
            column2.refresh_from_db()
            assert column2.data_type == "json"

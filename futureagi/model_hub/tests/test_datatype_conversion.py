"""
Unit tests for datatype conversion functionality in develop_dataset.py.

Tests cover:
- DatatypeConverter class with all conversion methods
- Strict mode (all-or-nothing) validation
- Lenient mode (preserve original values on failure)
- Bulk update operations
- Error handling and reporting
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APITestCase

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    BooleanChoices,
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.types import ConversionResult
from model_hub.views.develop_dataset import DatatypeConverter
from tfc.constants.roles import OrganizationRoles


@pytest.mark.django_db
class TestDatatypeConverter(APITestCase):
    """Test cases for DatatypeConverter class"""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class"""
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

    def setUp(self):
        """Set up for each test method"""
        self.dataset = Dataset.objects.create(
            name="Test Dataset",
            organization=self.organization,
            user=self.user,
            source=DatasetSourceChoices.BUILD.value,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            column_order=[],
            column_config={},
        )
        self.column = Column.objects.create(
            dataset=self.dataset,
            name="test_column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        self.row = Row.objects.create(
            dataset=self.dataset,
            order=1,
        )

    def _create_cell(self, value, status=CellStatus.PASS.value):
        """Helper to create a cell"""
        return Cell.objects.create(
            column=self.column,
            row=self.row,
            value=value,
            status=status,
        )

    # ============= TEXT CONVERSION TESTS =============

    def test_convert_to_text_always_succeeds(self):
        """Text conversion should always succeed"""
        converter = DatatypeConverter(DataTypeChoices.TEXT.value)
        cell = self._create_cell("any value")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == "any value"
        assert result.status == CellStatus.PASS.value

    # ============= BOOLEAN CONVERSION TESTS =============

    def test_convert_to_boolean_true_values(self):
        """Test boolean conversion for true values"""
        converter = DatatypeConverter(DataTypeChoices.BOOLEAN.value)
        true_values = [
            "true",
            "True",
            "TRUE",
            "1",
            "yes",
            "Yes",
            "YES",
            "passed",
            "Passed",
        ]

        for value in true_values:
            cell = self._create_cell(value)
            result = converter._convert_single_cell(cell)

            assert result.success is True, f"Failed for value: {value}"
            assert result.new_value == BooleanChoices.TRUE.value
            assert result.status == CellStatus.PASS.value

    def test_convert_to_boolean_false_values(self):
        """Test boolean conversion for false values"""
        converter = DatatypeConverter(DataTypeChoices.BOOLEAN.value)
        false_values = [
            "false",
            "False",
            "FALSE",
            "0",
            "no",
            "No",
            "NO",
            "failed",
            "Failed",
        ]

        for value in false_values:
            cell = self._create_cell(value)
            result = converter._convert_single_cell(cell)

            assert result.success is True, f"Failed for value: {value}"
            assert result.new_value == BooleanChoices.FALSE.value
            assert result.status == CellStatus.PASS.value

    def test_convert_to_boolean_defaults_to_false(self):
        """Test boolean conversion defaults to false for non-matching values"""
        converter = DatatypeConverter(DataTypeChoices.BOOLEAN.value)
        cell = self._create_cell("random value")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == BooleanChoices.FALSE.value
        assert "Defaulted to false" in result.value_infos.get("note", "")

    def test_convert_to_boolean_empty_value(self):
        """Test boolean conversion handles empty values"""
        converter = DatatypeConverter(DataTypeChoices.BOOLEAN.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == BooleanChoices.FALSE.value

    # ============= INTEGER CONVERSION TESTS =============

    def test_convert_to_integer_valid_integers(self):
        """Test integer conversion with valid integer values"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        test_cases = [
            ("42", "42"),
            ("0", "0"),
            ("-100", "-100"),
            ("42.7", "42"),  # Should truncate decimals
            ("  123  ", "123"),  # Should handle whitespace
        ]

        for input_val, expected in test_cases:
            cell = self._create_cell(input_val)
            result = converter._convert_single_cell(cell)

            assert result.success is True, f"Failed for value: {input_val}"
            assert result.new_value == expected
            assert result.status == CellStatus.PASS.value

    def test_convert_to_integer_invalid_values(self):
        """Test integer conversion fails for invalid values"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        invalid_values = ["abc", "12.34.56", "hello", "12a"]

        for value in invalid_values:
            cell = self._create_cell(value)
            result = converter._convert_single_cell(cell)

            assert result.success is False, f"Should fail for value: {value}"
            assert result.status == CellStatus.ERROR.value
            assert "Cannot convert" in result.error_message

    def test_convert_to_integer_empty_value(self):
        """Test integer conversion fails for empty values"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Empty value" in result.error_message

    # ============= FLOAT CONVERSION TESTS =============

    def test_convert_to_float_valid_floats(self):
        """Test float conversion with valid float values"""
        converter = DatatypeConverter(DataTypeChoices.FLOAT.value)
        test_cases = [
            ("42.5", "42.5"),
            ("0.123", "0.123"),
            ("-100.99", "-100.99"),
            ("42", "42"),
            ("  3.14  ", "3.14"),
        ]

        for input_val, expected in test_cases:
            cell = self._create_cell(input_val)
            result = converter._convert_single_cell(cell)

            assert result.success is True, f"Failed for value: {input_val}"
            assert result.new_value == expected
            assert result.status == CellStatus.PASS.value

    def test_convert_to_float_invalid_values(self):
        """Test float conversion fails for invalid values"""
        converter = DatatypeConverter(DataTypeChoices.FLOAT.value)
        invalid_values = ["abc", "12.34.56", "hello"]

        for value in invalid_values:
            cell = self._create_cell(value)
            result = converter._convert_single_cell(cell)

            assert result.success is False, f"Should fail for value: {value}"
            assert result.status == CellStatus.ERROR.value

    def test_convert_to_float_empty_value(self):
        """Test float conversion fails for empty values"""
        converter = DatatypeConverter(DataTypeChoices.FLOAT.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Empty value" in result.error_message

    # ============= DATETIME CONVERSION TESTS =============

    def test_convert_to_datetime_empty_value(self):
        """Test datetime conversion fails for empty values"""
        converter = DatatypeConverter(DataTypeChoices.DATETIME.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Empty value" in result.error_message

    @patch("model_hub.views.develop_dataset.DateTimeFormatChoices")
    def test_convert_to_datetime_valid_format(self, mock_formats):
        """Test datetime conversion with valid datetime string"""
        mock_formats.OPTIONS.value = ["%Y-%m-%d"]
        converter = DatatypeConverter(DataTypeChoices.DATETIME.value)
        cell = self._create_cell("2025-01-12")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert "2025-01-12" in result.new_value

    def test_convert_to_datetime_unix_timestamp_seconds(self):
        """Test datetime conversion with Unix timestamp (seconds)"""
        converter = DatatypeConverter(DataTypeChoices.DATETIME.value)
        cell = self._create_cell("1704067200")  # 10-digit timestamp

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value is not None

    def test_convert_to_datetime_unix_timestamp_milliseconds(self):
        """Test datetime conversion with Unix timestamp (milliseconds)"""
        converter = DatatypeConverter(DataTypeChoices.DATETIME.value)
        cell = self._create_cell("1704067200000")  # 13-digit timestamp

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value is not None

    def test_convert_to_datetime_invalid_format(self):
        """Test datetime conversion fails for invalid format"""
        converter = DatatypeConverter(DataTypeChoices.DATETIME.value)
        cell = self._create_cell("not a date")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Cannot parse datetime" in result.error_message

    # ============= ARRAY CONVERSION TESTS =============

    def test_convert_to_array_valid_json_array(self):
        """Test array conversion with valid JSON array"""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell("[1, 2, 3]")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert json.loads(result.new_value) == [1, 2, 3]

    def test_convert_to_array_not_an_array(self):
        """Test array conversion fails when value is not an array"""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell('{"key": "value"}')

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "does not look like an array" in result.error_message

    def test_convert_to_array_invalid_json(self):
        """Test array conversion fails for invalid JSON"""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell("[1, 2, invalid]")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Invalid array format" in result.error_message

    def test_convert_to_array_empty_value(self):
        """Test array conversion converts empty values to empty array"""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        # Empty values are converted to empty array "[]"
        assert result.success is True
        assert result.new_value == "[]"
        assert result.status == CellStatus.PASS.value

    def test_convert_to_array_not_starting_with_bracket(self):
        """Test array conversion fails when value doesn't start with ["""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell("not an array")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "does not look like an array" in result.error_message

    # ============= JSON CONVERSION TESTS =============

    def test_convert_to_json_valid_json_object(self):
        """Test JSON conversion with valid JSON object"""
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell('{"key": "value"}')

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert json.loads(result.new_value) == {"key": "value"}

    def test_convert_to_json_array_accepted(self):
        """Test JSON conversion accepts arrays as valid JSON"""
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell("[1, 2, 3]")

        result = converter._convert_single_cell(cell)

        # Arrays are now accepted as valid JSON
        assert result.success is True
        assert json.loads(result.new_value) == [1, 2, 3]
        assert result.status == CellStatus.PASS.value

    def test_convert_to_json_python_dict(self):
        """Test JSON conversion with Python dict syntax"""
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell("{'key': 'value'}")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert json.loads(result.new_value) == {"key": "value"}

    def test_convert_to_json_invalid_format(self):
        """Test JSON conversion behavior for non-JSON text.

        Note: The converter uses json_repair which is intentionally lenient
        and may repair invalid input. This test verifies that behavior.
        """
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell("not json at all")

        result = converter._convert_single_cell(cell)

        # json_repair is lenient and may convert plain text to empty string
        # This is expected behavior - the converter tries to be helpful
        # If it succeeds, verify the result is valid JSON
        if result.success:
            import json

            # Should be parseable as JSON
            json.loads(result.new_value)
        else:
            # If it fails, verify error message
            assert "Cannot parse as valid JSON" in result.error_message

    def test_convert_to_json_empty_value(self):
        """Test JSON conversion converts empty values to empty object"""
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        # Empty values are converted to empty object "{}"
        assert result.success is True
        assert result.new_value == "{}"
        assert result.status == CellStatus.PASS.value

    # ============= IMAGE/AUDIO/DOCUMENT CONVERSION TESTS =============

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_image_success(self, mock_validate, mock_upload):
        """Test image conversion uploads to S3"""
        mock_validate.return_value = None  # Validation passes
        mock_upload.return_value = "https://s3.bucket/image.jpg"
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell("https://example.com/image.jpg")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == "https://s3.bucket/image.jpg"
        mock_validate.assert_called_once()
        mock_upload.assert_called_once()

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_image_upload_fails(self, mock_validate, mock_upload):
        """Test image conversion handles upload failure"""
        mock_validate.return_value = None  # Validation passes
        mock_upload.side_effect = Exception("Upload failed")
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell("https://example.com/image.jpg")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Failed to upload image" in result.error_message

    def test_convert_to_image_empty_value(self):
        """Test image conversion handles empty values"""
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value is None

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_image_skips_reupload_for_own_customer_bucket(
        self, mock_validate, mock_upload
    ):
        """A `fi-customer-data*` URL wrapped in a single-element JSON array
        must be linked, not re-downloaded/re-uploaded.
        """
        mock_validate.return_value = None
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        own_url = "https://fi-customer-data-dev.s3.amazonaws.com/images/uuid/uuid"
        cell = self._create_cell(json.dumps([own_url]))

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == own_url
        mock_upload.assert_not_called()

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_image_skips_reupload_for_own_content_dev_bucket(
        self, mock_validate, mock_upload
    ):
        """TH-5648 root cause #2: exported CSVs carry `fi-content-dev` URLs
        (not just `fi-customer-data`) — those must also be linked, not
        re-uploaded. This bucket was missed in earlier revisions of the fix.
        """
        mock_validate.return_value = None
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        own_url = (
            "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/uuid/uuid"
        )
        cell = self._create_cell(json.dumps([own_url]))

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == own_url
        mock_upload.assert_not_called()

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_image_reuploads_third_party_url_in_json_array(
        self, mock_validate, mock_upload
    ):
        """A third-party URL wrapped in the same single-element JSON array
        shape must still be downloaded and re-uploaded to our own bucket.
        """
        mock_validate.return_value = None
        mock_upload.return_value = "https://s3.bucket/image.jpg"
        converter = DatatypeConverter(
            DataTypeChoices.IMAGE.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell(json.dumps(["https://example.com/image.jpg"]))

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value == "https://s3.bucket/image.jpg"
        mock_upload.assert_called_once()

    # ============= STRICT MODE TESTS =============

    def test_strict_mode_all_succeed(self):
        """Test strict mode succeeds when all cells convert"""
        converter = DatatypeConverter(
            DataTypeChoices.INTEGER.value, allow_partial_failure=False
        )
        cells = [
            self._create_cell("1"),
            self._create_cell("2"),
            self._create_cell("3"),
        ]

        cells_queryset = Cell.objects.filter(id__in=[cell.id for cell in cells])

        # Should not raise exception
        converter.convert(cells_queryset)

        # Verify all cells were updated
        for cell in cells:
            cell.refresh_from_db()
            assert cell.status == CellStatus.PASS.value

    def test_strict_mode_any_fail_aborts(self):
        """Test strict mode aborts if any cell fails"""
        converter = DatatypeConverter(
            DataTypeChoices.INTEGER.value, allow_partial_failure=False
        )
        cells = [
            self._create_cell("1"),
            self._create_cell("invalid"),  # This will fail
            self._create_cell("3"),
        ]

        cells_queryset = Cell.objects.filter(id__in=[cell.id for cell in cells])

        # Should raise exception
        with pytest.raises(ValueError) as exc_info:
            converter.convert(cells_queryset)

        assert "Conversion failed" in str(exc_info.value)
        assert "No data was modified" in str(exc_info.value)

        # Verify NO cells were updated (all should still be PASS)
        for cell in cells:
            cell.refresh_from_db()
            assert cell.value in ["1", "invalid", "3"]  # Original values preserved

    # ============= LENIENT MODE TESTS =============

    def test_lenient_mode_preserves_failed_values(self):
        """Test lenient mode preserves original values for failed cells"""
        converter = DatatypeConverter(
            DataTypeChoices.INTEGER.value, allow_partial_failure=True
        )
        cells = [
            self._create_cell("1"),
            self._create_cell("invalid"),  # This will fail
            self._create_cell("3"),
        ]

        cells_queryset = Cell.objects.filter(id__in=[cell.id for cell in cells])

        # Should not raise exception
        converter.convert(cells_queryset)

        # Verify successful cells were updated
        cells[0].refresh_from_db()
        assert cells[0].value == "1"
        assert cells[0].status == CellStatus.PASS.value

        cells[2].refresh_from_db()
        assert cells[2].value == "3"
        assert cells[2].status == CellStatus.PASS.value

        # Verify failed cell kept original value
        cells[1].refresh_from_db()
        assert cells[1].value == "invalid"  # Original value preserved!
        assert cells[1].status == CellStatus.ERROR.value

    # ============= BULK UPDATE TESTS =============

    def test_bulk_update_called_once(self):
        """Test that bulk_update is called instead of individual saves"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        cells = [
            self._create_cell("1"),
            self._create_cell("2"),
            self._create_cell("3"),
        ]

        cells_queryset = Cell.objects.filter(id__in=[cell.id for cell in cells])

        with patch(
            "model_hub.views.develop_dataset.Cell.objects.bulk_update"
        ) as mock_bulk:
            cells_dict = {str(cell.id): cell for cell in cells}
            results = [
                ConversionResult(
                    cell_id=str(cell.id),
                    success=True,
                    new_value=cell.value,
                    status=CellStatus.PASS.value,
                    value_infos={},
                )
                for cell in cells
            ]
            converter._apply_conversions(results, cells_dict)

            # Verify bulk_update was called exactly once
            assert mock_bulk.call_count == 1
            # Verify it was called with 3 cells
            call_args = mock_bulk.call_args
            assert len(call_args[0][0]) == 3

    # ============= ERROR SUMMARY TESTS =============

    def test_error_summary_generation(self):
        """Test error summary is generated correctly"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        failed_results = [
            ConversionResult(
                cell_id="cell1",
                success=False,
                new_value=None,
                status=CellStatus.ERROR.value,
                value_infos={},
                error_message="Error 1",
            ),
            ConversionResult(
                cell_id="cell2",
                success=False,
                new_value=None,
                status=CellStatus.ERROR.value,
                value_infos={},
                error_message="Error 2",
            ),
        ]

        summary = converter._generate_error_summary(failed_results)

        assert "First 2 errors" in summary
        assert "cell1" in summary
        assert "Error 1" in summary
        assert "cell2" in summary
        assert "Error 2" in summary

    def test_error_summary_truncates_at_5(self):
        """Test error summary shows max 5 errors"""
        converter = DatatypeConverter(DataTypeChoices.INTEGER.value)
        failed_results = [
            ConversionResult(
                cell_id=f"cell{i}",
                success=False,
                new_value=None,
                status=CellStatus.ERROR.value,
                value_infos={},
                error_message=f"Error {i}",
            )
            for i in range(10)
        ]

        summary = converter._generate_error_summary(failed_results)

        assert "First 5 errors" in summary
        assert "and 5 more" in summary

    # ============= EDGE CASES =============

    def test_unsupported_datatype_raises_error(self):
        """Test conversion fails for unsupported datatype"""
        converter = DatatypeConverter("UNSUPPORTED_TYPE")
        cell = self._create_cell("value")

        result = converter._convert_single_cell(cell)

        assert result.success is False
        assert "Unsupported datatype" in result.error_message

    def test_convert_empty_queryset(self):
        """Test conversion handles empty queryset"""
        converter = DatatypeConverter(DataTypeChoices.TEXT.value)
        empty_queryset = Cell.objects.none()

        # Should not raise exception
        converter.convert(empty_queryset)

    def test_exception_handling_in_array_conversion(self):
        """Test exception handling catches unexpected errors in array conversion"""
        converter = DatatypeConverter(DataTypeChoices.ARRAY.value)
        cell = self._create_cell("[1, 2, 3]")

        # Mock json.loads to raise an unexpected error
        with patch("json.loads", side_effect=RuntimeError("Unexpected error")):
            result = converter._convert_single_cell(cell)

            assert result.success is False
            assert "Failed to convert to array" in result.error_message

    def test_exception_handling_in_json_conversion(self):
        """Test exception handling catches unexpected errors in JSON conversion"""
        converter = DatatypeConverter(DataTypeChoices.JSON.value)
        cell = self._create_cell('{"key": "value"}')

        # Mock json.loads to raise an unexpected error
        with patch("json.loads", side_effect=RuntimeError("Unexpected error")):
            result = converter._convert_single_cell(cell)

            assert result.success is False
            assert "Failed to convert to JSON" in result.error_message

    # ============= IMAGES CONVERSION TESTS =============

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_success(self, mock_validate, mock_upload):
        """Test IMAGES conversion uploads multiple images to S3"""
        mock_validate.return_value = None
        mock_upload.side_effect = [
            "https://s3.bucket/image1.jpg",
            "https://s3.bucket/image2.png",
        ]
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        # Input must be a JSON array string for multiple images
        cell = self._create_cell(
            '["https://example.com/image1.jpg", "https://example.com/image2.png"]'
        )

        result = converter._convert_single_cell(cell)

        assert result.success is True
        # Should be stored as JSON array
        uploaded_urls = json.loads(result.new_value)
        assert isinstance(uploaded_urls, list)
        assert len(uploaded_urls) == 2
        assert "https://s3.bucket/image1.jpg" in uploaded_urls
        assert "https://s3.bucket/image2.png" in uploaded_urls

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_partial_failure(self, mock_validate, mock_upload):
        """Test IMAGES conversion handles partial upload failures gracefully"""
        mock_validate.return_value = None
        # First upload succeeds, second fails
        mock_upload.side_effect = [
            "https://s3.bucket/image1.jpg",
            Exception("Upload failed"),
        ]
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        # Input must be a JSON array string for multiple images
        cell = self._create_cell(
            '["https://example.com/image1.jpg", "https://example.com/image2.png"]'
        )

        result = converter._convert_single_cell(cell)

        # Partial failure raises ValueError in current implementation
        # because the exception propagates
        assert result.success is False

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_all_fail(self, mock_validate, mock_upload):
        """Test IMAGES conversion fails when all uploads fail"""
        mock_validate.return_value = None
        mock_upload.side_effect = Exception("Upload failed")
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        # Input must be a JSON array string
        cell = self._create_cell(
            '["https://example.com/image1.jpg", "https://example.com/image2.png"]'
        )

        result = converter._convert_single_cell(cell)

        assert result.success is False

    def test_convert_to_images_empty_value(self):
        """Test IMAGES conversion handles empty values"""
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell("")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert result.new_value is None

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_three_images(self, mock_validate, mock_upload):
        """Test IMAGES conversion with three images"""
        mock_validate.return_value = None
        mock_upload.side_effect = [
            "https://s3.bucket/img1.jpg",
            "https://s3.bucket/img2.png",
            "https://s3.bucket/img3.gif",
        ]
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        # Input must be a JSON array string
        cell = self._create_cell(
            '["https://example.com/img1.jpg", "https://example.com/img2.png", "https://example.com/img3.gif"]'
        )

        result = converter._convert_single_cell(cell)

        assert result.success is True
        uploaded_urls = json.loads(result.new_value)
        assert len(uploaded_urls) == 3

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_single_url_becomes_array(self, mock_validate, mock_upload):
        """Test IMAGES conversion converts single URL to array"""
        mock_validate.return_value = None
        mock_upload.return_value = "https://s3.bucket/image1.jpg"
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        # Single image URL string (not JSON array)
        cell = self._create_cell("https://example.com/image1.jpg")

        result = converter._convert_single_cell(cell)

        assert result.success is True
        uploaded_urls = json.loads(result.new_value)
        assert isinstance(uploaded_urls, list)
        assert len(uploaded_urls) == 1

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_validates_every_url_in_array(
        self, mock_validate, mock_upload
    ):
        """TH-5648 follow-up: the array/"Images" path used to skip
        validate_file_url entirely, unlike the single-"Image" path -- making
        it an easy SSRF bypass (just wrap the target URL in a JSON array).
        Every element must now be validated before upload.
        """
        mock_validate.return_value = None
        mock_upload.side_effect = [
            "https://s3.bucket/image1.jpg",
            "https://s3.bucket/image2.png",
        ]
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell(
            '["https://example.com/image1.jpg", "https://example.com/image2.png"]'
        )

        result = converter._convert_single_cell(cell)

        assert result.success is True
        assert mock_validate.call_count == 2
        validated_urls = {call.args[0] for call in mock_validate.call_args_list}
        assert validated_urls == {
            "https://example.com/image1.jpg",
            "https://example.com/image2.png",
        }

    @patch("model_hub.views.develop_dataset.upload_image_to_s3")
    @patch("model_hub.views.develop_dataset.validate_file_url")
    def test_convert_to_images_rejects_ssrf_target_in_array(
        self, mock_validate, mock_upload
    ):
        """A malicious URL (e.g. the cloud metadata endpoint) hidden inside
        the array must be rejected, and no upload/fetch may be attempted for
        it or anything after it.
        """
        mock_validate.side_effect = ValueError(
            "Image URL resolves to a disallowed private/internal address"
        )
        converter = DatatypeConverter(
            DataTypeChoices.IMAGES.value, dataset_id=str(self.dataset.id)
        )
        cell = self._create_cell(
            '["http://169.254.169.254/latest/meta-data/", '
            '"https://example.com/image2.png"]'
        )

        result = converter._convert_single_cell(cell)

        assert result.success is False
        mock_upload.assert_not_called()

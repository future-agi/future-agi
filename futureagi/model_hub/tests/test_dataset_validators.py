"""
Unit tests for dataset_validators.py.

Tests the shared validation functions used by both backend views and MCP AI tools.
"""

import pytest

from model_hub.services.dataset_validators import (
    MAX_CELL_VALUE_LENGTH,
    MAX_DUPLICATE_COPIES,
    MAX_EMPTY_ROWS_PER_REQUEST,
    MAX_FILE_SIZE_BYTES,
    NON_EDITABLE_SOURCE_TYPES,
    cleanup_annotation_metadata,
    validate_and_convert_cell_value,
    validate_column_is_editable,
    validate_num_rows,
    validate_row_ids_or_select_all,
)


class TestValidateAndConvertCellValue:
    """Tests for validate_and_convert_cell_value()."""

    def test_empty_string_returns_none(self):
        result, error = validate_and_convert_cell_value("", "text")
        assert result is None
        assert error is None

    def test_whitespace_only_returns_none(self):
        result, error = validate_and_convert_cell_value("   ", "text")
        assert result is None
        assert error is None

    def test_none_value_returns_none(self):
        result, error = validate_and_convert_cell_value(None, "text")
        assert result is None
        assert error is None

    def test_text_type_passthrough(self):
        result, error = validate_and_convert_cell_value("hello world", "text")
        assert result == "hello world"
        assert error is None

    def test_max_length_exceeded(self):
        long_value = "x" * (MAX_CELL_VALUE_LENGTH + 1)
        result, error = validate_and_convert_cell_value(long_value, "text")
        assert result is None
        assert "maximum length" in error

    def test_max_length_at_boundary(self):
        value = "x" * MAX_CELL_VALUE_LENGTH
        result, error = validate_and_convert_cell_value(value, "text")
        assert result == value
        assert error is None

    def test_boolean_true(self):
        result, error = validate_and_convert_cell_value("true", "boolean")
        assert result == "true"
        assert error is None

    def test_boolean_false(self):
        result, error = validate_and_convert_cell_value("false", "boolean")
        assert result == "false"
        assert error is None

    def test_boolean_invalid(self):
        result, error = validate_and_convert_cell_value("maybe", "boolean")
        assert result is None
        assert "Invalid boolean" in error

    def test_integer_valid(self):
        result, error = validate_and_convert_cell_value("42", "integer")
        assert result == "42"
        assert error is None

    def test_integer_from_float(self):
        result, error = validate_and_convert_cell_value("42.7", "integer")
        assert result == "42"
        assert error is None

    def test_integer_invalid(self):
        result, error = validate_and_convert_cell_value("not_a_number", "integer")
        assert result is None
        assert "Invalid integer" in error

    def test_float_valid(self):
        result, error = validate_and_convert_cell_value("3.14", "float")
        assert result == "3.14"
        assert error is None

    def test_float_invalid(self):
        result, error = validate_and_convert_cell_value("abc", "float")
        assert result is None
        assert "Invalid float" in error

    def test_datetime_valid_iso(self):
        result, error = validate_and_convert_cell_value(
            "2024-01-15 10:30:00", "datetime"
        )
        assert error is None
        assert result is not None

    def test_datetime_invalid(self):
        result, error = validate_and_convert_cell_value("not-a-date", "datetime")
        assert result is None
        assert "Invalid datetime" in error

    def test_array_valid(self):
        result, error = validate_and_convert_cell_value("[1, 2, 3]", "array")
        assert error is None
        assert result == "[1, 2, 3]"

    def test_array_invalid_string(self):
        result, error = validate_and_convert_cell_value("not an array", "array")
        assert result is None
        assert "not valid JSON array" in error

    def test_array_invalid_json(self):
        result, error = validate_and_convert_cell_value("[1, 2,", "array")
        assert error is None
        assert result == "[1, 2]"

    def test_json_valid(self):
        result, error = validate_and_convert_cell_value('{"key": "value"}', "json")
        assert error is None
        assert '"key"' in result

    def test_json_invalid_string(self):
        result, error = validate_and_convert_cell_value("not json", "json")
        assert result is None
        assert "not valid JSON" in error

    def test_json_malformed_repaired(self):
        result, error = validate_and_convert_cell_value('{"key":', "json")
        assert error is None
        assert '"key"' in result

    def test_image_type_blocked(self):
        result, error = validate_and_convert_cell_value("some_url", "image")
        assert result is None
        assert "Cannot update" in error
        assert "dashboard UI" in error

    def test_audio_type_blocked(self):
        result, error = validate_and_convert_cell_value("some_url", "audio")
        assert result is None
        assert "Cannot update" in error

    def test_document_type_blocked(self):
        result, error = validate_and_convert_cell_value("some_url", "document")
        assert result is None
        assert "Cannot update" in error

    def test_unknown_type_passthrough(self):
        result, error = validate_and_convert_cell_value("some value", "unknown_type")
        assert result == "some value"
        assert error is None


class TestValidateColumnIsEditable:
    """Tests for validate_column_is_editable()."""

    def test_editable_column(self):
        class MockColumn:
            source = "others"
            name = "test_col"

        is_editable, error = validate_column_is_editable(MockColumn())
        assert is_editable is True
        assert error is None

    def test_non_editable_source_types(self):
        for source_type in NON_EDITABLE_SOURCE_TYPES:

            class MockColumn:
                source = source_type
                name = f"col_{source_type}"

            is_editable, error = validate_column_is_editable(MockColumn())
            assert is_editable is False, f"Expected {source_type} to be non-editable"
            assert "not directly editable" in error


class TestValidateNumRows:
    """Tests for validate_num_rows()."""

    def test_valid_num_rows(self):
        result, error = validate_num_rows(5)
        assert result == 5
        assert error is None

    def test_min_boundary(self):
        result, error = validate_num_rows(1)
        assert result == 1
        assert error is None

    def test_zero_invalid(self):
        result, error = validate_num_rows(0)
        assert result is None
        assert "at least 1" in error

    def test_negative_invalid(self):
        result, error = validate_num_rows(-5)
        assert result is None
        assert "at least 1" in error

    def test_exceeds_max(self):
        result, error = validate_num_rows(MAX_EMPTY_ROWS_PER_REQUEST + 1)
        assert result is None
        assert "cannot exceed" in error

    def test_max_boundary(self):
        result, error = validate_num_rows(
            MAX_EMPTY_ROWS_PER_REQUEST, max_allowed=MAX_EMPTY_ROWS_PER_REQUEST
        )
        assert result == MAX_EMPTY_ROWS_PER_REQUEST
        assert error is None

    def test_string_input(self):
        result, error = validate_num_rows("10")
        assert result == 10
        assert error is None

    def test_invalid_string(self):
        result, error = validate_num_rows("abc")
        assert result is None
        assert "valid integer" in error

    def test_custom_max(self):
        result, error = validate_num_rows(15, max_allowed=10)
        assert result is None
        assert "cannot exceed 10" in error


class TestValidateRowIdsOrSelectAll:
    """Tests for validate_row_ids_or_select_all()."""

    def test_valid_row_ids(self):
        is_valid, error = validate_row_ids_or_select_all(["id1", "id2"], False)
        assert is_valid is True
        assert error is None

    def test_valid_select_all(self):
        is_valid, error = validate_row_ids_or_select_all([], True)
        assert is_valid is True
        assert error is None

    def test_neither_provided(self):
        is_valid, error = validate_row_ids_or_select_all([], False)
        assert is_valid is False
        assert "must be provided" in error

    def test_both_provided(self):
        is_valid, error = validate_row_ids_or_select_all(["id1"], True)
        assert is_valid is True
        assert error is None


class TestConstants:
    """Test that constants have expected values."""

    def test_max_cell_value_length(self):
        assert MAX_CELL_VALUE_LENGTH == 100_000

    def test_max_file_size(self):
        assert MAX_FILE_SIZE_BYTES == 25 * 1024 * 1024

    def test_max_empty_rows(self):
        assert MAX_EMPTY_ROWS_PER_REQUEST == 100

    def test_max_duplicate_copies(self):
        assert MAX_DUPLICATE_COPIES == 100

    def test_non_editable_source_types_count(self):
        assert len(NON_EDITABLE_SOURCE_TYPES) == 12

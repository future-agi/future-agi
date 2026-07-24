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

    # ──────────────────────────────────────────────────────────────────
    # Cases where result == expected exactly (or is None) and error is
    # either None or contains a specific substring.
    #
    #   (value, cell_type, expected_result, expected_error_substring)
    #
    # expected_error_substring None → assert error is None
    # ──────────────────────────────────────────────────────────────────
    @pytest.mark.parametrize(
        "value,cell_type,expected_result,expected_error_substring",
        [
            # Empty / None passthrough
            ("",   "text", None, None),
            ("   ","text", None, None),
            (None, "text", None, None),
            # Text
            ("hello world", "text", "hello world", None),
            ("x" * MAX_CELL_VALUE_LENGTH, "text", "x" * MAX_CELL_VALUE_LENGTH, None),
            ("x" * (MAX_CELL_VALUE_LENGTH + 1), "text", None, "maximum length"),
            # Boolean
            ("true",  "boolean", "true",  None),
            ("false", "boolean", "false", None),
            ("maybe", "boolean", None, "Invalid boolean"),
            # Integer
            ("42",           "integer", "42", None),
            ("42.7",         "integer", "42", None),
            ("not_a_number", "integer", None, "Invalid integer"),
            # Float
            ("3.14", "float", "3.14", None),
            ("abc",  "float", None, "Invalid float"),
            # Datetime — invalid path only; valid variant kept separate below
            # because the return format is not a byte-for-byte string.
            ("not-a-date", "datetime", None, "Invalid datetime"),
            # Array
            ("[1, 2, 3]",   "array", "[1, 2, 3]", None),
            ("not an array","array", None, "not valid JSON array"),
            # Repair variant kept separate below — asserts a repaired result.
            # JSON invalid path
            ("not json", "json", None, "not valid JSON"),
            # Media types are read-only from the dashboard
            ("some_url", "image",    None, "Cannot update"),
            ("some_url", "audio",    None, "Cannot update"),
            ("some_url", "document", None, "Cannot update"),
            # Unknown type falls through
            ("some value", "unknown_type", "some value", None),
        ],
        ids=[
            "empty_string_returns_none",
            "whitespace_only_returns_none",
            "none_value_returns_none",
            "text_type_passthrough",
            "max_length_at_boundary",
            "max_length_exceeded",
            "boolean_true",
            "boolean_false",
            "boolean_invalid",
            "integer_valid",
            "integer_from_float",
            "integer_invalid",
            "float_valid",
            "float_invalid",
            "datetime_invalid",
            "array_valid",
            "array_invalid_string",
            "json_invalid_string",
            "image_type_blocked",
            "audio_type_blocked",
            "document_type_blocked",
            "unknown_type_passthrough",
        ],
    )
    def test_validate_and_convert(
        self, value, cell_type, expected_result, expected_error_substring
    ):
        result, error = validate_and_convert_cell_value(value, cell_type)
        assert result == expected_result
        if expected_error_substring is None:
            assert error is None
        else:
            assert expected_error_substring in (error or "")

    # ─── Tests kept separate because result isn't a fixed string ────────
    def test_datetime_valid_iso(self):
        result, error = validate_and_convert_cell_value(
            "2024-01-15 10:30:00", "datetime"
        )
        assert error is None
        assert result is not None

    def test_json_valid(self):
        result, error = validate_and_convert_cell_value('{"key": "value"}', "json")
        assert error is None
        assert '"key"' in result

    def test_array_invalid_json(self):
        # `parse_json_safely` invokes `json_repair` on structurally-broken
        # arrays like `"[1, 2,"` and returns the repaired list.
        result, error = validate_and_convert_cell_value("[1, 2,", "array")
        assert error is None
        assert result == "[1, 2]"

    def test_json_malformed_repaired(self):
        # `json_repair` also fixes malformed JSON objects missing a value
        # after the key.
        result, error = validate_and_convert_cell_value('{"key":', "json")
        assert error is None
        assert '"key"' in result

    def test_image_type_blocked_mentions_dashboard(self):
        # image case gets an extra substring check the shared harness above
        # doesn't cover.
        _, error = validate_and_convert_cell_value("some_url", "image")
        assert "dashboard UI" in error


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

    @pytest.mark.parametrize(
        "value,max_allowed,expected_result,expected_error_substring",
        [
            (5, None, 5, None),
            (1, None, 1, None),
            (0, None, None, "at least 1"),
            (-5, None, None, "at least 1"),
            (MAX_EMPTY_ROWS_PER_REQUEST + 1, None, None, "cannot exceed"),
            (
                MAX_EMPTY_ROWS_PER_REQUEST,
                MAX_EMPTY_ROWS_PER_REQUEST,
                MAX_EMPTY_ROWS_PER_REQUEST,
                None,
            ),
            ("10", None, 10, None),
            ("abc", None, None, "valid integer"),
            (15, 10, None, "cannot exceed 10"),
        ],
        ids=[
            "valid_num_rows",
            "min_boundary",
            "zero_invalid",
            "negative_invalid",
            "exceeds_max",
            "max_boundary",
            "string_input",
            "invalid_string",
            "custom_max",
        ],
    )
    def test_validate_num_rows(
        self, value, max_allowed, expected_result, expected_error_substring
    ):
        kwargs = {"max_allowed": max_allowed} if max_allowed is not None else {}
        result, error = validate_num_rows(value, **kwargs)
        assert result == expected_result
        if expected_error_substring is None:
            assert error is None
        else:
            assert expected_error_substring in error


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

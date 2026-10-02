"""Tests for agent_playground.serializers.dataset_link."""

import uuid

import pytest

from agent_playground.serializers.dataset_link import (
    CellUpdateSerializer,
    DeleteRowsSerializer,
    ExecuteRequestSerializer,
)

# =============================================================================
# CellUpdateSerializer
# =============================================================================


@pytest.mark.unit
class TestCellUpdateSerializer:
    """Tests for CellUpdateSerializer."""

    def test_valid_value(self):
        serializer = CellUpdateSerializer(data={"value": "hello"})
        assert serializer.is_valid()
        assert serializer.validated_data["value"] == "hello"

    def test_blank_value_allowed(self):
        serializer = CellUpdateSerializer(data={"value": ""})
        assert serializer.is_valid()
        assert serializer.validated_data["value"] == ""

    def test_null_value_allowed(self):
        serializer = CellUpdateSerializer(data={"value": None})
        assert serializer.is_valid()
        assert serializer.validated_data["value"] is None

    def test_empty_data_defaults_to_blank(self):
        serializer = CellUpdateSerializer(data={})
        assert serializer.is_valid()
        assert serializer.validated_data["value"] == ""


# =============================================================================
# DeleteRowsSerializer
# =============================================================================


@pytest.mark.unit
class TestDeleteRowsSerializer:
    """Tests for DeleteRowsSerializer."""

    def test_valid_with_row_ids(self):
        uid = uuid.uuid4()
        serializer = DeleteRowsSerializer(data={"row_ids": [str(uid)]})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["row_ids"] == [uid]

    def test_valid_with_select_all(self):
        serializer = DeleteRowsSerializer(data={"select_all": True})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["select_all"] is True

    def test_valid_select_all_with_exclude_ids(self):
        uid = uuid.uuid4()
        serializer = DeleteRowsSerializer(
            data={"select_all": True, "exclude_ids": [str(uid)]}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["exclude_ids"] == [uid]

    def test_invalid_both_select_all_and_row_ids(self):
        uid = uuid.uuid4()
        serializer = DeleteRowsSerializer(
            data={"select_all": True, "row_ids": [str(uid)]}
        )
        assert not serializer.is_valid()
        assert "Cannot provide both" in str(serializer.errors)

    def test_invalid_neither_select_all_nor_row_ids(self):
        serializer = DeleteRowsSerializer(data={})
        assert not serializer.is_valid()
        assert "A list of row IDs or select_all flag" in str(serializer.errors)

    def test_invalid_exclude_ids_without_select_all(self):
        uid = uuid.uuid4()
        serializer = DeleteRowsSerializer(
            data={"row_ids": [str(uid)], "exclude_ids": [str(uid)]}
        )
        assert not serializer.is_valid()
        assert "exclude_ids" in str(serializer.errors)

    def test_defaults_empty_lists_and_false(self):
        """Fields default to [] and False before validation."""
        serializer = DeleteRowsSerializer(data={"select_all": True})
        assert serializer.is_valid()
        assert serializer.validated_data["row_ids"] == []
        assert serializer.validated_data["exclude_ids"] == []


# =============================================================================
# ExecuteRequestSerializer
# =============================================================================


@pytest.mark.unit
class TestExecuteRequestSerializer:
    """Tests for ExecuteRequestSerializer."""

    def test_defaults(self):
        serializer = ExecuteRequestSerializer(data={})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["row_ids"] is None
        assert serializer.validated_data["task_queue"] == "tasks_l"

    def test_custom_values(self):
        uid = uuid.uuid4()
        serializer = ExecuteRequestSerializer(
            data={"row_ids": [str(uid)], "task_queue": "tasks_s"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["row_ids"] == [uid]
        assert serializer.validated_data["task_queue"] == "tasks_s"

    def test_invalid_uuid_in_row_ids(self):
        serializer = ExecuteRequestSerializer(data={"row_ids": ["not-a-uuid"]})
        assert not serializer.is_valid()
        assert "row_ids" in serializer.errors

    def test_invalid_empty_row_ids(self):
        serializer = ExecuteRequestSerializer(data={"row_ids": []})
        assert not serializer.is_valid()
        assert "row_ids" in serializer.errors

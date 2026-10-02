"""Unit tests for the annotation-queue REQUEST serializers.

Pure validation logic — NO DB, NO HTTP. Instantiate a serializer with a
payload, call ``is_valid()``, assert on ``errors`` / ``validated_data``.

Mirrors the Simulate serializer-unit pattern
(``simulate/tests/test_agent_definition_serializers.py``) and gives the
annotation feature the unit-layer coverage its endpoints (tested via
integration) don't provide — fast (ms), exhaustive on edge cases, and it
pinpoints exactly which field/rule broke.
"""

import uuid

import pytest

from model_hub.serializers.annotation_queues import (
    AddItemsSerializer,
    AddQueueItemSerializer,
    AnnotationQueueListQuerySerializer,
    BulkRemoveItemsSerializer,
    QueueDefaultRequestSerializer,
    QueueExportToDatasetRequestSerializer,
    QueueHardDeleteRequestSerializer,
    QueueLabelRequestSerializer,
    QueueStatusRequestSerializer,
)

pytestmark = pytest.mark.unit


def _uuid():
    return str(uuid.uuid4())


class TestQueueStatusRequestSerializer:
    def test_valid(self):
        s = QueueStatusRequestSerializer(data={"status": "active"})
        assert s.is_valid(), s.errors

    def test_missing_status(self):
        s = QueueStatusRequestSerializer(data={})
        assert not s.is_valid()
        assert "status" in s.errors

    def test_invalid_status_choice(self):
        s = QueueStatusRequestSerializer(data={"status": "bogus"})
        assert not s.is_valid()
        assert "status" in s.errors

    def test_rejects_unknown_key(self):
        s = QueueStatusRequestSerializer(data={"status": "active", "extra": 1})
        assert not s.is_valid()


class TestQueueHardDeleteRequestSerializer:
    def test_valid(self):
        s = QueueHardDeleteRequestSerializer(
            data={"force": True, "confirm_name": "Q"}
        )
        assert s.is_valid(), s.errors

    def test_missing_force(self):
        s = QueueHardDeleteRequestSerializer(data={"confirm_name": "Q"})
        assert not s.is_valid()
        assert "force" in s.errors

    def test_missing_confirm_name(self):
        s = QueueHardDeleteRequestSerializer(data={"force": True})
        assert not s.is_valid()
        assert "confirm_name" in s.errors


class TestQueueDefaultRequestSerializer:
    def test_valid_single_scope(self):
        s = QueueDefaultRequestSerializer(data={"project_id": _uuid()})
        assert s.is_valid(), s.errors

    def test_rejects_no_scope(self):
        s = QueueDefaultRequestSerializer(data={})
        assert not s.is_valid()

    def test_rejects_two_scopes(self):
        s = QueueDefaultRequestSerializer(
            data={"project_id": _uuid(), "dataset_id": _uuid()}
        )
        assert not s.is_valid()


class TestQueueLabelRequestSerializer:
    def test_valid_defaults_required_false(self):
        s = QueueLabelRequestSerializer(data={"label_id": _uuid()})
        assert s.is_valid(), s.errors
        assert s.validated_data["required"] is False

    def test_missing_label_id(self):
        s = QueueLabelRequestSerializer(data={})
        assert not s.is_valid()
        assert "label_id" in s.errors

    def test_invalid_label_uuid(self):
        s = QueueLabelRequestSerializer(data={"label_id": "not-a-uuid"})
        assert not s.is_valid()
        assert "label_id" in s.errors


class TestBulkRemoveItemsSerializer:
    def test_valid(self):
        s = BulkRemoveItemsSerializer(data={"item_ids": [_uuid()]})
        assert s.is_valid(), s.errors

    def test_rejects_empty_list(self):
        s = BulkRemoveItemsSerializer(data={"item_ids": []})
        assert not s.is_valid()
        assert "item_ids" in s.errors

    def test_missing_item_ids(self):
        s = BulkRemoveItemsSerializer(data={})
        assert not s.is_valid()


class TestAddQueueItemSerializer:
    def test_valid(self):
        s = AddQueueItemSerializer(
            data={"source_type": "dataset_row", "source_id": _uuid()}
        )
        assert s.is_valid(), s.errors

    def test_invalid_source_type(self):
        s = AddQueueItemSerializer(data={"source_type": "bogus", "source_id": "x"})
        assert not s.is_valid()
        assert "source_type" in s.errors

    def test_blank_source_id(self):
        s = AddQueueItemSerializer(
            data={"source_type": "dataset_row", "source_id": ""}
        )
        assert not s.is_valid()
        assert "source_id" in s.errors


class TestAddItemsSerializer:
    def test_valid_items(self):
        s = AddItemsSerializer(
            data={"items": [{"source_type": "dataset_row", "source_id": _uuid()}]}
        )
        assert s.is_valid(), s.errors

    def test_rejects_empty_items(self):
        s = AddItemsSerializer(data={"items": []})
        assert not s.is_valid()

    def test_rejects_neither_items_nor_selection(self):
        s = AddItemsSerializer(data={})
        assert not s.is_valid()

    def test_rejects_both_items_and_selection(self):
        s = AddItemsSerializer(
            data={
                "items": [{"source_type": "dataset_row", "source_id": _uuid()}],
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": _uuid(),
                },
            }
        )
        assert not s.is_valid()


class TestQueueExportToDatasetRequestSerializer:
    def test_valid_with_name(self):
        s = QueueExportToDatasetRequestSerializer(data={"dataset_name": "New DS"})
        assert s.is_valid(), s.errors

    def test_valid_with_id(self):
        s = QueueExportToDatasetRequestSerializer(data={"dataset_id": _uuid()})
        assert s.is_valid(), s.errors

    def test_rejects_neither_id_nor_name(self):
        s = QueueExportToDatasetRequestSerializer(data={})
        assert not s.is_valid()


class TestAnnotationQueueListQuerySerializer:
    def test_valid_empty(self):
        s = AnnotationQueueListQuerySerializer(data={})
        assert s.is_valid(), s.errors

    def test_rejects_page_size_below_min(self):
        s = AnnotationQueueListQuerySerializer(data={"page_size": 0})
        assert not s.is_valid()
        assert "page_size" in s.errors

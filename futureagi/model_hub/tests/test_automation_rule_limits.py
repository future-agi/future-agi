from types import SimpleNamespace
from unittest.mock import patch

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils import annotation_queue_helpers as helpers


class _BoundedRows:
    def __init__(self, ids):
        self.ids = ids
        self.requested_slice = None

    def order_by(self, *fields):
        assert fields == ("order", "id")
        return self

    def values_list(self, field, flat=False):
        assert field == "id"
        assert flat is True
        return self

    def __getitem__(self, item):
        self.requested_slice = item
        return self.ids[item]

    def count(self):
        raise AssertionError("dataset automation resolver must not issue COUNT(*)")


def _resolve_dataset_ids(ids, cap):
    rows = _BoundedRows(ids)
    rule = SimpleNamespace(organization=object())
    with (
        patch.object(Dataset.objects, "get", return_value=object()),
        patch.object(Row.objects, "filter", return_value=rows),
        patch.object(Column.objects, "filter", return_value=[]),
        patch.object(Cell.objects, "filter", return_value=object()),
    ):
        result = helpers._resolve_dataset_rule_ids(
            rule,
            filters=[],
            dataset_id="dataset-1",
            cap=cap,
        )
    return result, rows.requested_slice


def test_filter_mode_overflow_fails_before_queue_access():
    rule = SimpleNamespace(source_type="trace")

    with patch.object(helpers, "get_fk_field_name", return_value="trace"):
        result = helpers._add_source_ids_to_queue(
            rule,
            source_ids=["trace-1"],
            total_matching=helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        )

    assert result == {
        "matched": helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        "added": 0,
        "duplicates": 0,
        "truncated": True,
        "error": helpers.AUTOMATION_RULE_MATCH_LIMIT_ERROR,
    }


def test_filter_mode_overflow_preview_is_explicitly_truncated():
    rule = SimpleNamespace(source_type="trace")

    with patch.object(helpers, "get_fk_field_name", return_value="trace"):
        result = helpers._add_source_ids_to_queue(
            rule,
            source_ids=["trace-1"],
            total_matching=helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
            dry_run=True,
        )

    assert result == {
        "matched": helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        "added": 0,
        "duplicates": 0,
        "truncated": True,
    }


def test_dataset_resolver_returns_exact_total_without_count_at_or_below_cap():
    result, requested_slice = _resolve_dataset_ids(["row-1", "row-2"], cap=2)

    assert result == (2, ["row-1", "row-2"])
    assert requested_slice == slice(None, 3, None)


def test_dataset_resolver_uses_cap_plus_one_overflow_sentinel_without_count():
    result, requested_slice = _resolve_dataset_ids(
        ["row-1", "row-2", "row-3", "row-4"], cap=2
    )

    assert result == (3, ["row-1", "row-2"])
    assert requested_slice == slice(None, 3, None)

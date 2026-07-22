"""DB-free regression matrix for queue filter-mode resolver drift."""

from __future__ import annotations

import uuid

import pytest

from model_hub.services import bulk_selection
from model_hub.services.bulk_selection import (
    _apply_call_execution_filters,
    _filter_column_id,
)
from model_hub.utils import annotation_queue_helpers
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS
from tracer.utils.filters import apply_created_at_filters


class RecordingQuerySet:
    def __init__(self):
        self.calls = []

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    def exclude(self, *args, **kwargs):
        self.calls.append(("exclude", args, kwargs))
        return self

    def annotate(self, *args, **kwargs):
        self.calls.append(("annotate", args, kwargs))
        return self


def _filter(
    column_id,
    col_type,
    filter_type,
    filter_op,
    filter_value,
):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _value_for(filter_type, filter_op):
    if filter_op in {"is_null", "is_not_null"}:
        return None
    if filter_op in {"between", "not_between"}:
        return [5, 10]
    if filter_op in {"in", "not_in"}:
        return ["alpha", "beta"] if filter_type == "text" else [5, 10]
    if filter_type == "number":
        return 7
    return "alpha"


def _call_keys(qs):
    keys = []
    for method, _args, kwargs in qs.calls:
        keys.extend((method, key) for key in kwargs)
    return set(keys)


def test_filter_helpers_read_canonical_snake_filter_payloads_only():
    snake = _filter("label-1", "ANNOTATION", "text", "contains", "x")
    camel = {
        "columnId": "label-2",
        "filterConfig": {
            "colType": "ANNOTATION",
            "filterType": "text",
            "filterOp": "contains",
            "filterValue": "x",
        },
    }

    assert _filter_column_id(snake) == "label-1"
    assert _filter_column_id(camel) == ""


def test_rule_field_mapping_uses_canonical_snake_case_ids_only():
    for source_mapping in annotation_queue_helpers.FIELD_MAPPING.values():
        assert not any(
            any(char.isupper() for char in field_id)
            for field_id in source_mapping
        )


@pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["text"]))
def test_call_execution_text_filters_accept_contract_operators(filter_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [
            _filter(
                "status",
                "SYSTEM_METRIC",
                "text",
                filter_op,
                _value_for("text", filter_op),
            )
        ],
    )

    assert unsupported == []
    assert qs.calls
    keys = _call_keys(qs)
    if filter_op == "starts_with":
        assert ("filter", "status__istartswith") in keys
    elif filter_op == "ends_with":
        assert ("filter", "status__iendswith") in keys
    elif filter_op == "not_contains":
        assert ("exclude", "status__icontains") in keys


@pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
def test_call_execution_number_filters_accept_contract_operators(filter_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [
            _filter(
                "duration_seconds",
                "SYSTEM_METRIC",
                "number",
                filter_op,
                _value_for("number", filter_op),
            )
        ],
    )

    assert unsupported == []
    assert qs.calls
    keys = _call_keys(qs)
    if filter_op == "greater_than":
        assert ("filter", "duration_seconds__gt") in keys
    elif filter_op == "greater_than_or_equal":
        assert ("filter", "duration_seconds__gte") in keys
    elif filter_op == "less_than":
        assert ("filter", "duration_seconds__lt") in keys
    elif filter_op == "less_than_or_equal":
        assert ("filter", "duration_seconds__lte") in keys
    elif filter_op == "not_between":
        assert ("exclude", "duration_seconds__range") in keys


@pytest.mark.parametrize(
    "legacy_op",
    ["more_than", "more_than_or_equal", "gt", "gte", "not_in_between", "eq", "ne"],
)
def test_call_execution_rejects_legacy_numeric_operator_aliases(legacy_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [_filter("duration_seconds", "SYSTEM_METRIC", "number", legacy_op, 7)],
    )

    assert unsupported == ["duration_seconds"]
    assert not qs.calls


def test_apply_created_at_filters_accepts_canonical_filter_payload():
    qs = RecordingQuerySet()
    filtered_qs, remaining = apply_created_at_filters(
        qs,
        [
            _filter(
                "created_at",
                "SYSTEM_METRIC",
                "datetime",
                "greater_than",
                "2026-05-01T00:00:00Z",
            )
        ],
    )

    assert filtered_qs is qs
    assert remaining == []
    assert ("filter", "created_at__gt") in _call_keys(qs)


def test_call_execution_unknown_simulation_filter_fails_closed():
    _qs, unsupported = bulk_selection._apply_call_execution_filters(
        RecordingQuerySet(),
        [_filter("unmapped_simulation_metric", "SYSTEM_METRIC", "number", "equals", 1)],
    )

    assert unsupported == ["unmapped_simulation_metric"]

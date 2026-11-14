"""Raw attributes and identically named Users metrics remain distinct."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.unit
UID = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000002"


def wire(key, value, *, source="SPAN_ATTRIBUTE", kind="text"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": "equals",
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("key", ["user_id", "latency_ms", "total_tokens", "cost"])
@pytest.mark.parametrize("attribute_value", ["raw-match", "other", None])
def test_custom_and_native_filters_use_independent_values(key, attribute_value):
    native_value = "native-identity" if key == "user_id" else 99
    native_kind = "text" if key == "user_id" else "number"
    raw_filter = wire(key, "raw-match")
    native_filter = wire(key, native_value, source="SYSTEM_METRIC", kind=native_kind)
    assert not UserListQueryBuilderV2._is_output_filter(raw_filter)
    assert UserListQueryBuilderV2._is_output_filter(native_filter)
    manager = UsersListManager(
        organization_id="00000000-0000-4000-8000-000000000003",
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        filters=[raw_filter, native_filter],
    )
    assert manager.attribute_keys == (key,)
    native_key = UserListQueryBuilderV2.OUTPUT_FILTER_MAP[key]
    row = {"end_user_id": UID, key: native_value, native_key: native_value}
    data = (
        []
        if attribute_value is None
        else [
            {
                "end_user_id": UID,
                "attribute_key": key,
                "attribute_typed_values": [("string", json.dumps(attribute_value))],
            }
        ]
    )
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=data)
        attrs = manager._read_span_attributes([row], ReadDeadline.start(10_000))
        manager._apply_span_attributes([row], attrs)
        assert row[key] == native_value
        assert row[native_key] == native_value
        assert manager._row_matches_filters(row) is (attribute_value == "raw-match")

        # Absence in a later read must not reuse either old raw data or a
        # native field with the same spelling.
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        manager._read_span_attributes([row], ReadDeadline.start(10_000))
        assert not manager._row_matches_filters(row)

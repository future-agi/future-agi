"""Users answers native span-dimension filters from the span row.

``status``/``model``/``provider``/``name``/``observation_type`` are physical
``spans`` columns with no per-user aggregate and no key in the span attribute
maps. Reading them as custom attributes evaluates every user as NULL, so the
list answers ``is_null`` with a full page and every other operator with zero
rows while the users graph answers the identical leaf.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.unit

UID = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000002"
ORG = "00000000-0000-4000-8000-000000000003"


def leaf(column_id, operation, value=None, *, col_type="SYSTEM_METRIC"):
    config = {"col_type": col_type, "filter_type": "text", "filter_op": operation}
    if value is not None:
        config["filter_value"] = value
    return {
        "column_id": column_id,
        "property_id": f"system_attribute:traces:{column_id}",
        "filter_config": config,
    }


def manager_for(*filters):
    return UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        filters=list(filters),
    )


def test_native_dimensions_are_the_span_compilers_own_columns():
    for column_id, column in USER_NATIVE_SPAN_DIMENSIONS.items():
        assert ClickHouseFilterBuilder.SYSTEM_METRIC_MAP[column_id] == column
        assert column_id not in UserListQueryBuilderV2.OUTPUT_FILTER_MAP
        assert column in ClickHouseFilterBuilder._CASE_INSENSITIVE_COLUMNS


@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
def test_native_leaf_never_becomes_a_custom_attribute_key(column_id):
    manager = manager_for(leaf(column_id, "equals", "OK"))
    assert manager.attribute_keys == ()
    assert manager.native_dimension_filters == {
        column_id: USER_NATIVE_SPAN_DIMENSIONS[column_id]
    }
    assert manager.filters_need_enrichment is True


def test_raw_attribute_of_the_same_name_keeps_its_attribute_identity():
    raw = leaf("status", "equals", "OK", col_type="SPAN_ATTRIBUTE")
    manager = manager_for(raw)
    assert UserListQueryBuilderV2.native_span_dimension(raw) is None
    assert manager.attribute_keys == ("status",)
    assert manager.native_dimension_filters == {}


@pytest.mark.parametrize("column_id", ["status", "model", "name"])
def test_a_custom_attribute_identity_wins_without_a_col_type(column_id):
    # FilterItemField checks property_id against col_type only when col_type is
    # given, so a raw-attribute leaf can reach the manager declaring its
    # identity through property_id alone.
    raw = {
        "column_id": column_id,
        "property_id": f"custom_attribute:{column_id}",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "x",
        },
    }
    manager = manager_for(raw)
    assert UserListQueryBuilderV2.native_span_dimension(raw) is None
    assert manager.attribute_keys == (column_id,)
    assert manager.native_dimension_filters == {}


@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        ("is_null", None, False),
        ("is_not_null", None, True),
        ("equals", "OK", True),
        ("equals", "UNSET", False),
        ("not_equals", "OK", False),
        ("not_equals", "UNSET", True),
        ("in", ["ok"], True),
        ("in", ["UNSET"], False),
        ("not_in", ["OK", "ERROR"], False),
        ("contains", "R", True),
        ("not_contains", "R", False),
        ("starts_with", "O", True),
        ("ends_with", "R", True),
        ("ends_with", "Z", False),
    ],
)
def test_every_operator_is_answered_from_the_users_span_values(
    operation, value, expected
):
    item = leaf("status", operation, value)
    manager = manager_for(item)
    manager._native_dimension_values_by_user[UID] = {"status": ("OK", "ERROR")}
    assert manager._row_matches_filters({"end_user_id": UID}) is expected


@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        ("is_null", None, True),
        ("is_not_null", None, False),
        ("equals", "OK", False),
        ("not_equals", "OK", False),
    ],
)
def test_a_user_with_no_spans_in_the_window_has_no_value(operation, value, expected):
    manager = manager_for(leaf("status", operation, value))
    manager._native_dimension_values_by_user[UID] = {"status": ()}
    assert manager._row_matches_filters({"end_user_id": UID}) is expected


def test_the_dimension_query_projects_each_filtered_column():
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=[leaf("status", "equals", "OK")],
    )
    query, params = builder.build_native_span_dimension_query([UID], ("status",))
    assert "argMax(status, _version) AS latest_status" in query
    assert "tuple('status', toString(latest_status))" in query
    assert "ARRAY JOIN dimensions AS dimension" in query
    assert params["candidate_end_user_ids"] == (UID,)
    assert builder.build_native_span_dimension_query([UID], ()) == ("", {})
    assert builder.build_native_span_dimension_query([], ("status",)) == ("", {})


def test_the_page_read_caches_only_the_page_and_replaces_absence():
    manager = manager_for(leaf("status", "equals", "OK"))
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=manager.filters,
    )
    row = {"end_user_id": UID}
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[
                {
                    "end_user_id": UID,
                    "dimension_name": "status",
                    "dimension_values": ["OK"],
                }
            ]
        )
        manager._read_native_span_dimensions([row], builder, ReadDeadline.start(10_000))
        assert manager._row_matches_filters(row) is True

        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        manager._read_native_span_dimensions([row], builder, ReadDeadline.start(10_000))
        assert manager._row_matches_filters(row) is False


WINDOW = {
    "window_start": datetime(2026, 9, 1),
    "window_end": datetime(2026, 9, 8),
}


@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(
    ("operation", "value"), [("equals", "OK"), ("in", ["OK", "ERROR"])]
)
def test_native_leaf_never_narrows_acquisition_on_the_attribute_maps(
    column_id, operation, value
):
    # The native value lives in a spans column, not in the attribute maps: an
    # attribute-map witness would acquire only users carrying a same-named raw
    # attribute (nobody), so the page must be acquired without one.
    item = leaf(column_id, operation, value)
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    assert builder.matching_activity_witness() is None
    assert manager_for(item).matching_activity_walk_applies(builder) is False
    query, _params = builder.build_dimension_candidate_query(limit=26, **WINDOW)
    assert "span_attr_" not in query
    assert "attrs_" not in query


def test_a_raw_attribute_of_a_native_name_still_narrows_acquisition():
    item = leaf("status", "equals", "OK", col_type="SPAN_ATTRIBUTE")
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    witness = builder.matching_activity_witness()
    assert witness is not None and witness[0] == "status"
    query, _params = builder.build_dimension_candidate_query(limit=26, **WINDOW)
    assert "mapContains(attrs_string" in query

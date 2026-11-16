"""Supporting compiler checks; actual authorized PG/CH parity is tested separately."""

import pytest

from tracer.services.clickhouse.exact_graph_reads import compile_user_membership_leaf
from tracer.services.clickhouse.v2.effective_tags import EffectiveTagRelation
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_r2_tags_users import END, START, tag_leaf, window

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000002"
OTHER_PROJECT = "00000000-0000-4000-8000-000000000004"
ORG = "00000000-0000-4000-8000-000000000003"
UID = "00000000-0000-4000-8000-000000000001"


def manager_for(*leaves, attribute_keys=()):
    return UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        attribute_keys=list(attribute_keys),
        filters=window(*leaves),
    )


@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize("family", [None, "NORMAL", "SYSTEM_METRIC"])
def test_native_tag_has_no_raw_discovery_or_certification_dependency(column, family):
    item = tag_leaf(column=column, family=family)
    manager = manager_for(item)
    assert manager.attribute_keys == ()
    assert manager.native_dimension_leaves == ((1, item),)
    assert manager.attribute_exact_text_filters == {}
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=window(item)
    )
    assert list(builder._scalar_user_witnesses()) == []
    assert list(builder.matching_activity_witnesses()) == []
    assert manager._attribute_filters_match({"end_user_id": UID}) is True
    assert manager._row_matches_filters({"end_user_id": UID}) is False
    manager._native_dimension_matches_by_user[UID] = {1: True}
    assert manager._row_matches_filters({"end_user_id": UID}) is True


@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize("identity", ["SPAN_ATTRIBUTE", "property_id", "propertyId"])
def test_explicit_raw_tag_and_requested_display_attribute_remain_separate(
    column, identity
):
    native = tag_leaf(column=column)
    raw = tag_leaf(
        value="raw",
        column=column,
        family="SPAN_ATTRIBUTE" if identity == "SPAN_ATTRIBUTE" else None,
    )
    if identity != "SPAN_ATTRIBUTE":
        raw[identity] = f"custom_attribute:{column}"
    manager = manager_for(native, raw, attribute_keys=(column,))
    assert manager.attribute_keys == (column,)
    assert manager.native_dimension_leaves == ((1, native),)
    assert manager.attribute_exact_text_filters == {column: ("raw",)}
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=window(native, raw)
    )
    witnesses = list(builder.matching_activity_witnesses())
    assert len(witnesses) == 1
    assert witnesses[0].key == column
    assert manager._raw_walk_witness_accepts(witnesses[0])[0] is True


@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize("family", [None, "NORMAL", "SYSTEM_METRIC"])
@pytest.mark.parametrize(
    "operation",
    [
        "equals",
        "in",
        "contains",
        "not_equals",
        "not_in",
        "not_contains",
        "is_null",
        "is_not_null",
    ],
)
def test_native_tag_preserves_graph_leaf_semantics_and_full_project_scope(
    column, family, operation
):
    item = tag_leaf(column=column, family=family, operation=operation)
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT, OTHER_PROJECT], filters=window(item)
    )
    flags, condition, params, _ = builder.native_span_dimension_membership(
        item, index=7
    )
    graph_flags, graph_condition, graph_params, _ = compile_user_membership_leaf(
        item, project_id=PROJECT, namespace="native_leaf_7"
    )
    assert (flags, condition) == (graph_flags, graph_condition)
    assert params == {
        key: EffectiveTagRelation((PROJECT, OTHER_PROJECT), value.values)
        if isinstance(value, EffectiveTagRelation)
        else value
        for key, value in graph_params.items()
    }
    assert any(isinstance(value, EffectiveTagRelation) for value in params.values())


def test_combined_native_certification_reuses_finite_latest_replay_and_full_window():
    native = tag_leaf()
    dimension = {
        "column_id": "observation_type",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "span",
        },
    }
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=window(native, dimension)
    )
    query, params = builder.build_native_span_dimension_query(
        [UID], [(1, native), (2, dimension)], newest=2
    )
    assert params["candidate_end_user_ids"] == (UID,)
    assert (params["start_date"], params["end_date"]) == (
        START.replace(tzinfo=None),
        END.replace(tzinfo=None),
    )
    assert "latest_start_time AS start_time" in query
    assert "latest_start_time AS native_span_start_time" in query
    assert "argMax(start_time, _version) AS latest_start_time" in query
    assert "argMax(tag," not in query and "argMax(tags," not in query
    assert "argMax(project_id," not in query
    assert "latest_is_deleted = 0" in query
    assert "AS native_leaf_1" in query and "AS native_leaf_2" in query
    assert "AS native_leaf_2_newest" in query
    assert "GROUP BY end_user_id" in query

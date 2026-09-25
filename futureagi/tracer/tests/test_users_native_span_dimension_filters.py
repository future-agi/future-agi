"""Users answers native span-dimension filters from the span row.

``status``/``model``/``provider``/``name``/``observation_type`` are physical
``spans`` columns with no per-user aggregate and no key in the span attribute
maps. Reading them as custom attributes evaluates every user as NULL, so the
list answers ``is_null`` with a full page and every other operator with zero
rows while the users graph answers the identical leaf.

The list decides each native leaf with the users graph's own membership SQL:
a user matches when ANY of their latest live spans in the window satisfies the
span compiler's predicate (``''`` is null on these non-nullable columns,
negations mean "some span differs", comparisons are case-insensitive). The
live proof is ``test_users_native_span_dimension_parity_ch25.py``.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.exact_graph_reads import (
    _user_membership_having,
    compile_user_membership_leaf,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
    MatchingActivityWitness,
    UnsupportedBoundedUserListQuery,
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


OPERATIONS = [
    ("equals", "OK"),
    ("not_equals", "OK"),
    ("in", ["OK", "Error"]),
    ("not_in", ["OK"]),
    ("contains", "R"),
    ("not_contains", "R"),
    ("starts_with", "O"),
    ("ends_with", "K"),
    ("is_null", None),
    ("is_not_null", None),
]


@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(("operation", "value"), OPERATIONS)
def test_native_leaf_is_the_users_graphs_own_membership_sql(
    column_id, operation, value
):
    # The graph compiles this leaf with its default namespace; the list sends
    # the identical SQL under the leaf's own namespace, nothing else changed.
    item = leaf(column_id, operation, value)
    graph_flags, graph_condition, graph_params = _user_membership_having(
        [item], project_id=PROJECT
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    flags, condition, params = builder.native_span_dimension_membership(item, index=3)

    def renamed(text):
        return text.replace("user_member", "native_leaf_3")

    assert flags == tuple(renamed(flag) for flag in graph_flags)
    assert condition == renamed(graph_condition)
    assert params == {renamed(name): v for name, v in graph_params.items()}


@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(("operation", "value"), OPERATIONS)
def test_every_operator_is_one_any_span_condition(column_id, operation, value):
    # SYSTEM_METRIC leaves: a user matches when ANY latest live span satisfies
    # the span compiler's predicate - negations included ("some span differs").
    column = USER_NATIVE_SPAN_DIMENSIONS[column_id]
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[]
    )
    flags, condition, _params = builder.native_span_dimension_membership(
        leaf(column_id, operation, value), index=0
    )
    assert len(flags) == 1
    assert flags[0].endswith(" AS native_leaf_0_match_0")
    assert condition == "countIf(native_leaf_0_match_0) > 0"
    assert column in flags[0]
    if operation == "is_null":
        # Native text columns are non-nullable: '' is the missing value.
        assert f"{column} = ''" in flags[0]
    if operation == "is_not_null":
        assert f"{column} != ''" in flags[0]
    if operation not in {"is_null", "is_not_null"}:
        assert f"lowerUTF8(toString({column}))" in flags[0]


def test_each_native_leaf_is_decided_on_its_own_filter_index():
    # Two leaves on one column are two decisions, never one merged value set.
    first = leaf("status", "equals", "OK")
    second = leaf("status", "not_equals", "ERROR")
    manager = manager_for(first, second)
    assert manager.native_dimension_leaves == ((0, first), (1, second))
    row = {"end_user_id": UID}
    manager._native_dimension_matches_by_user[UID] = {0: True, 1: True}
    assert manager._row_matches_filters(row) is True
    manager._native_dimension_matches_by_user[UID] = {0: True, 1: False}
    assert manager._row_matches_filters(row) is False
    manager._native_dimension_matches_by_user[UID] = {0: False, 1: True}
    assert manager._row_matches_filters(row) is False


@pytest.mark.parametrize(("operation", "value"), OPERATIONS)
def test_a_user_with_no_spans_in_the_window_matches_no_leaf(operation, value):
    # The graph aggregates only users with a latest live span in the window;
    # a user the page statement returns no row for matches nothing, is_null
    # included.
    manager = manager_for(leaf("status", operation, value))
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    row = {"end_user_id": UID}
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        manager._read_native_span_dimensions([row], builder, None)
    assert manager._native_dimension_matches_by_user[UID] == {0: False}
    assert manager._row_matches_filters(row) is False


def test_the_dimension_query_decides_each_leaf_with_the_graph_sql():
    status = leaf("status", "equals", "OK")
    kind = leaf("node_type", "not_in", ["llm"])
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=[status, kind],
    )
    query, params = builder.build_native_span_dimension_query(
        [UID], [(1, status), (4, kind)]
    )
    flat = " ".join(query.split())
    assert "argMax(status, _version) AS latest_status" in query
    assert "latest_status AS status" in query
    # The replay identity already groups observation_type.
    assert "argMax(observation_type" not in query
    assert (
        "(lowerUTF8(toString(status)) = %(native_leaf_1_0_col_1)s)"
        " AS native_leaf_1_match_0" in flat
    )
    assert (
        "(lowerUTF8(toString(observation_type)) NOT IN %(native_leaf_4_0_col_1)s)"
        " AS native_leaf_4_match_0" in flat
    )
    assert "(countIf(native_leaf_1_match_0) > 0) AS native_leaf_1" in flat
    assert "(countIf(native_leaf_4_match_0) > 0) AS native_leaf_4" in flat
    assert "GROUP BY end_user_id" in query
    assert params["native_leaf_1_0_col_1"] == "ok"
    assert params["native_leaf_4_0_col_1"] == ("llm",)
    assert params["candidate_end_user_ids"] == (UID,)
    assert builder.build_native_span_dimension_query([UID], ()) == ("", {})
    assert builder.build_native_span_dimension_query([], [(1, status)]) == ("", {})


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
            data=[{"end_user_id": UID, "native_leaf_0": 1}]
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
    # attribute (nobody), so the seeded page is acquired without one. The
    # walk discovers on the leaf's own span flag instead.
    item = leaf(column_id, operation, value)
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    assert builder.matching_activity_witness() is None
    manager = manager_for(item)
    assert manager.matching_activity_walk_applies(builder) is True
    assert manager._walk_witness == builder.native_matching_activity_witness()
    assert manager._walk_witness.family == "native"
    assert "attrs_" not in manager._walk_witness.sql
    query, _params = builder.build_dimension_candidate_query(limit=26, **WINDOW)
    assert "span_attr_" not in query
    assert "attrs_" not in query


def test_a_raw_attribute_of_a_native_name_still_narrows_acquisition():
    item = leaf("status", "equals", "OK", col_type="SPAN_ATTRIBUTE")
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    witness = builder.matching_activity_witness()
    assert witness is not None and witness.key == "status"
    assert witness.family == "raw" and witness.index_pruned is True
    query, _params = builder.build_dimension_candidate_query(limit=26, **WINDOW)
    assert "mapContains(attrs_string" in query


def _raw_tag(value="gold"):
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("native_decision", [True, False])
def test_certification_skips_native_leaves_and_the_replay_decides_them(
    native_decision,
):
    # ``_attribute_filters_match`` decides the raw attribute leaves alone; a
    # native leaf has no attribute-map value, so deciding it there rejected
    # every user and published an empty page labelled exact. The native
    # decision comes from the native statement (read at the walk's
    # certification), and ``_row_matches_filters`` re-decides it.
    manager = manager_for(_raw_tag(), leaf("status", "equals", "ERROR"))
    assert "tag" in manager.attribute_exact_text_filters
    manager._attribute_values_by_user[UID] = {"tag": "gold"}
    row = {"end_user_id": UID}
    assert manager._attribute_filters_match(row) is True
    manager._native_dimension_matches_by_user[UID] = {1: native_decision}
    assert manager._row_matches_filters(row) is native_decision


def test_certification_still_rejects_a_raw_leaf_that_does_not_match():
    manager = manager_for(_raw_tag(), leaf("status", "equals", "ERROR"))
    manager._attribute_values_by_user[UID] = {"tag": "silver"}
    assert manager._attribute_filters_match({"end_user_id": UID}) is False


@pytest.mark.parametrize("enforce_on_server", [True, False])
def test_the_native_read_sends_the_statement_caps_its_siblings_send(
    enforce_on_server,
):
    manager = manager_for(leaf("status", "equals", "OK"))
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        manager._read_native_span_dimensions(
            [{"end_user_id": UID}],
            builder,
            ReadDeadline.start(10_000, enforce_on_server=enforce_on_server),
        )
    kwargs = service.return_value.execute_ch_query.call_args.kwargs
    assert 0 < kwargs["timeout_ms"] <= 10_000
    if enforce_on_server:
        assert kwargs["server_execution_cap_ms"] == kwargs["timeout_ms"]
    else:
        assert "server_execution_cap_ms" not in kwargs
    assert kwargs["settings"]["max_threads"] == 8
    assert kwargs["settings"]["max_result_rows"] == 1


def test_a_leaf_without_col_type_keeps_the_graphs_collection_shape():
    # FilterItemField checks property_id against col_type only when col_type
    # is sent, so a native leaf can arrive with no col_type. The graph compiles
    # that family with collection semantics (presence AND no forbidden value);
    # the list sends that same multi-flag SQL, never a forced SYSTEM_METRIC.
    item = {
        "column_id": "status",
        "property_id": "system_attribute:traces:status",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "not_equals",
            "filter_value": "OK",
        },
    }
    assert UserListQueryBuilderV2.native_span_dimension(item) == "status"
    graph_flags, graph_condition, graph_params = _user_membership_having(
        [item], project_id=PROJECT
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[item]
    )
    query, params = builder.build_native_span_dimension_query([UID], [(2, item)])
    flat = " ".join(query.split())

    def renamed(text):
        return text.replace("user_member", "native_leaf_2")

    assert len(graph_flags) == 2
    assert "= 0" in graph_condition
    for flag in graph_flags:
        assert " ".join(renamed(flag).split()) in flat
    assert f"({renamed(graph_condition)}) AS native_leaf_2" in flat
    for name, value in graph_params.items():
        assert params[renamed(name)] == value


# --------------------------------------------------------------------------
# A native leaf as the matching-activity walk's witness (design §2, §3).
# --------------------------------------------------------------------------

_NEGATIONS = {"not_equals", "not_in", "not_contains"}


def _builder(*filters):
    return UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=list(filters)
    )


@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", None], ids=["system", "none"])
@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(("operation", "value"), OPERATIONS)
def test_a_native_witness_is_the_leafs_existence_flag(
    column_id, operation, value, col_type
):
    """Every leaf whose graph condition has ``countIf(flag) > 0`` is eligible.

    The witness predicate is that flag's own SQL and parameters; a negation
    without a family witnesses on its presence flag (its forbidden term is
    decided at certification); ``is_null`` without a family has only an
    absence term and is never a witness.
    """
    item = leaf(column_id, operation, value, col_type=col_type)
    date = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-09-01T00:00:00", "2026-09-02T00:00:00"],
        },
    }
    builder = _builder(date, item)
    witness = builder.native_matching_activity_witness()
    flags, condition, params = compile_user_membership_leaf(
        item, project_id=PROJECT, namespace="native_leaf_1"
    )
    if col_type is None and operation == "is_null":
        assert condition == "countIf(native_leaf_1_match_0) = 0"
        assert witness is None
        return
    assert witness == MatchingActivityWitness(
        family="native",
        key=USER_NATIVE_SPAN_DIMENSIONS[column_id],
        kind="native",
        sql=flags[0].removesuffix(" AS native_leaf_1_match_0"),
        params=params,
        leaf_index=1,
        flag_alias="native_leaf_1_match_0",
        index_pruned=False,
    )
    if col_type is None and operation in _NEGATIONS:
        assert condition == (
            "countIf(native_leaf_1_match_0) > 0 AND countIf(native_leaf_1_match_1) = 0"
        )
    else:
        assert condition == "countIf(native_leaf_1_match_0) > 0"
    # The raw attribute-map witness never takes a native leaf.
    assert builder.matching_activity_witness() is None


def test_the_lowest_eligible_native_leaf_is_the_witness():
    date = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-09-01T00:00:00", "2026-09-02T00:00:00"],
        },
    }
    is_null = leaf("model", "is_null", col_type=None)
    status = leaf("status", "equals", "ERROR")
    model = leaf("model", "equals", "gpt-4o")
    witness = _builder(date, is_null, status, model).native_matching_activity_witness()
    assert (witness.leaf_index, witness.key) == (2, "status")
    witness = _builder(model, status).native_matching_activity_witness()
    assert (witness.leaf_index, witness.key) == (0, "model")
    assert _builder(date, is_null).native_matching_activity_witness() is None
    # A raw attribute of a native name is a raw leaf, never a native witness.
    raw = leaf("status", "equals", "OK", col_type="SPAN_ATTRIBUTE")
    assert _builder(raw).native_matching_activity_witness() is None
    assert _builder(raw).matching_activity_witness().family == "raw"


def test_the_walk_statements_carry_the_chosen_witness_and_never_recompute_it():
    item = leaf("status", "not_equals", "ok", col_type=None)
    builder = _builder(item)
    with pytest.raises(UnsupportedBoundedUserListQuery):
        builder.build_matching_activity_slice_query(
            slice_start=datetime(2026, 9, 1, tzinfo=UTC),
            slice_end=datetime(2026, 9, 2, tzinfo=UTC),
            limit=10,
        )
    builder.walk_witness = builder.native_matching_activity_witness()
    flags, _condition, params = compile_user_membership_leaf(
        item, project_id=PROJECT, namespace="native_leaf_0"
    )
    presence = flags[0].removesuffix(" AS native_leaf_0_match_0")
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 2, tzinfo=UTC)
    statements = [
        builder.build_matching_activity_slice_query(
            slice_start=start, slice_end=end, limit=10
        ),
        builder.build_matching_activity_instant_query(instant=start, limit=10),
        builder.build_matching_activity_existence_query(
            range_start=start, range_end=end
        ),
        builder.build_matching_activity_existence_estimate_query(
            range_start=start, range_end=end
        ),
    ]
    for query, statement_params in statements:
        flat = " ".join(query.split())
        assert f"WHERE {' '.join(presence.split())}" in flat
        # The forbidden term is certified over the window, never a row filter.
        assert "native_leaf_0_0_forbidden" not in flat
        for name, value in params.items():
            if f"%({name})s" in presence:
                assert statement_params[name] == value


def test_the_native_statement_projects_the_witness_leafs_order_key():
    status = leaf("status", "equals", "OK")
    negation = leaf("model", "not_equals", "gpt-4o", col_type=None)
    builder = _builder(status, negation)
    query, _params = builder.build_native_span_dimension_query(
        [UID], [(0, status), (1, negation)], newest=1
    )
    flat = " ".join(query.split())
    assert "latest_start_time AS native_span_start_time" in flat
    assert (
        "maxIf(native_span_start_time, native_leaf_1_match_0) AS native_leaf_1_newest"
        in flat
    )
    assert "native_leaf_0_newest" not in flat
    plain, _ = builder.build_native_span_dimension_query(
        [UID], [(0, status), (1, negation)]
    )
    assert "_newest" not in plain
    with pytest.raises(ValueError):
        builder.build_native_span_dimension_query(
            [UID], [(0, leaf("model", "is_null", col_type=None))], newest=0
        )


def test_the_native_read_caches_the_order_key_and_reads_the_epoch_as_none():
    manager = manager_for(leaf("status", "equals", "OK"))
    builder = _builder(*manager.filters)
    other = "00000000-0000-4000-8000-000000000009"
    newest = datetime(2026, 9, 1, 12, 30)
    rows = [{"end_user_id": UID}, {"end_user_id": other}]
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[
                {
                    "end_user_id": UID,
                    "native_leaf_0": 1,
                    "native_leaf_0_newest": newest,
                },
                {
                    "end_user_id": other,
                    "native_leaf_0": 0,
                    "native_leaf_0_newest": datetime(1970, 1, 1),
                },
            ]
        )
        manager._read_native_span_dimensions(rows, builder, None, newest=0)
    assert manager._native_matching_activity_by_user == {
        UID: {0: newest.replace(tzinfo=UTC)},
        other: {},
    }
    # An attribute key of the same name never shares this cache.
    assert manager._matching_activity_by_user == {}

"""Numbered Users pages retain attributes on every merged raw-user alias."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import (
    _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE,
    UsersListManager,
    _users_attr_enrichment_query,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import (
    engine as physical_engine,
)


def uid(value):
    return str(UUID(int=value))


@pytest.fixture
def engine(tmp_path):
    yield from physical_engine.__wrapped__(tmp_path)


SURVIVOR, ALIAS, NEW_ID, UNMAPPED = map(uid, (10, 40, 50, 70))
LONG_VALUE = "full alias attribute " * 400
KEYS = (
    "tag",
    "long_text",
    "flag",
    "json_null",
    "cleared",
    "deleted",
    "moved",
    "absent",
)


@pytest.mark.unit
@pytest.mark.parametrize("workspace", [False, True])
def test_preexpanded_cursor_aliases_keep_the_literal_map_path(workspace):
    scope = (
        {"project_ids": [PROJECT, OTHER_PROJECT]}
        if workspace
        else {"project_id": PROJECT}
    )
    sql, params = _users_attr_enrichment_query(
        **scope,
        attribute_keys=KEYS,
        start_date=START,
        end_date=START + timedelta(hours=1),
        candidate_end_user_id_map={SURVIVOR: SURVIVOR, ALIAS: SURVIVOR},
    )
    assert "end_user_id_remap" not in sql
    assert "OR end_user_id IN (SELECT any_id FROM eu_survivor_map)" not in sql
    assert "AND end_user_id IN %(eu_scan_ids)s" in sql
    assert params["candidate_remap_any_ids"] == [SURVIVOR, ALIAS]
    assert params["candidate_remap_survivor_ids"] == [SURVIVOR, SURVIVOR]
    assert params["requested_attribute_keys"] == list(KEYS)


@pytest.mark.integration
@pytest.mark.parametrize("workspace", [False, True], ids=["project", "workspace"])
def test_numbered_page_enriches_aliases_without_changing_page(engine, workspace):
    execute, insert = engine
    execute("""CREATE TABLE end_user_id_remap (
        old_id UUID, new_id UUID, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version) ORDER BY old_id""")
    for old, new in ((SURVIVOR, NEW_ID), (ALIAS, NEW_ID), (uid(80), uid(90))):
        execute(
            "INSERT INTO end_user_id_remap VALUES (%(old)s, %(new)s, 1)",
            {"old": old, "new": new},
        )

    def put(span_id, **changes):
        insert(**{"id": span_id, "end_user_id": ALIAS, "attrs_number": {}, **changes})

    put(
        "alias",
        attrs_number={"tag": 7},
        attrs_string={"long_text": LONG_VALUE},
        attrs_bool={"flag": 0},
        attributes_extra='{"json_null":null}',
    )
    put("new-id", end_user_id=NEW_ID, attrs_number={"tag": 0})
    put("same-id", project_id=OTHER_PROJECT, attrs_number={"tag": 8})
    put("foreign", project_id=uid(999), attrs_number={"tag": 999})
    put("other-remap-group", end_user_id=uid(80), attrs_number={"tag": 998})
    put("unmapped", end_user_id=UNMAPPED, attrs_number={"tag": 5})
    put("cleared", attrs_string={"cleared": "old"})
    put("cleared", _version=2)
    put("deleted", attrs_string={"deleted": "old"})
    put("deleted", attrs_string={"deleted": "old"}, is_deleted=1, _version=2)
    put("moved", attrs_string={"moved": "old"})
    put(
        "moved",
        attrs_string={"moved": "old"},
        start_time=START + timedelta(minutes=5),
        _version=2,
    )
    put("reassigned", attrs_number={"tag": 997})
    put("reassigned", end_user_id=uid(100), attrs_number={"tag": 996}, _version=2)

    projects = [PROJECT, OTHER_PROJECT] if workspace else [PROJECT]
    filters = [time_filter(START + timedelta(minutes=15), START + timedelta(hours=1))]
    manager = UsersListManager(
        organization_id=uid(1),
        allowed_project_ids=projects,
        project_id=None if workspace else PROJECT,
        filters=filters,
        requested_columns=[],
        attribute_keys=list(KEYS),
        sort_params=[{"column_id": "user_id", "direction": "asc"}],
    )
    builder = UserListQueryBuilderV2(
        organization_id=uid(1),
        project_ids=projects,
        filters=filters,
        sort_params=manager.sort_params,
    )
    page = [
        {"end_user_id": UNMAPPED, "user_id": "first"},
        {"end_user_id": SURVIVOR, "user_id": "second"},
    ]
    calls = []

    def native_transport(sql, params, **kwargs):
        calls.append((sql, params))
        return SimpleNamespace(data=execute(sql, params))

    # Page selection has separate coverage. This boundary freezes page N and
    # its count/order while all enrichment SQL executes on actual fixture rows.
    with patch.object(
        manager, "_fetch_rows", return_value=(deepcopy(page), 7, builder)
    ) as fetch:
        with patch(
            "tracer.services.users_list_manager.V2AnalyticsQueryService"
        ) as analytics:
            analytics.return_value.execute_ch_query.side_effect = native_transport
            payload = manager.list_payload(page_size=2, current_page=1)
    fetch.assert_called_once_with(limit=2, offset=2, deadline=None)
    expected_queries = (
        len(KEYS) + _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE - 1
    ) // _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE
    assert len(calls) == expected_queries, "alias expansion must not add a roundtrip"
    assert payload == {
        "table": [
            {**page[0], "tag": 5},
            {
                **page[1],
                "tag": [0, 7, 8] if workspace else [0, 7],
                "long_text": LONG_VALUE,
                "flag": "false",
                "json_null": None,
            },
        ],
        "total_count": 7,
        "total_pages": 4,
    }
    for key in ("cleared", "deleted", "moved", "absent"):
        assert key not in manager._attribute_values_by_user[SURVIVOR]
    assert manager._attribute_value_matches(
        row=payload["table"][1],
        key="tag",
        config={"filter_type": "number", "filter_op": "not_in", "filter_value": [999]},
    )

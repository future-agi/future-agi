"""Users typed hydration on constant fixtures, never server tables or DDL.

Execute the real builder and compare every result with independent physical-key
replacement. This is semantic evidence, not production latency qualification.
"""

import json
import math
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.server_readonly import without_query_settings
from tracer.services.users_list_manager import (
    UsersListManager,
    _users_attr_enrichment_query,
)
from tracer.tests.test_trace_root_physical_replay import values_where
from tracer.tests.test_user_latest_window_replay import cte

pytestmark = pytest.mark.unit
PROJECT, OTHER, FOREIGN, USER, ALIAS, NEW, UNMAPPED = (
    str(UUID(int=i)) for i in (101, 102, 103, 110, 140, 150, 170)
)
START = datetime(2026, 8, 1, 12, 15, 0, 123456)
REMAP = {USER: USER, ALIAS: USER, NEW: USER}
CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
LONG = "雪 %_\\' quoted text " * 400
KEYS = (
    "tag",
    "long",
    "flag",
    "zero",
    "empty",
    "null",
    "array",
    "object",
    "mixed",
    "cleared",
    "deleted",
    "reassigned",
    "outside",
    "absent",
)


def span(identity, **changes):
    return {
        "project_id": PROJECT,
        "observation_type": "SPAN",
        "service_name": "svc",
        "trace_id": "trace",
        "id": identity,
        "start_time": START,
        "_version": 1,
        "is_deleted": 0,
        "end_user_id": ALIAS,
        "attributes_extra": {},
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
        **changes,
    }


def cohort(days):
    end = START + timedelta(days=days)
    rows = [
        span(
            "live",
            attrs_string={"tag": "first", "long": LONG, "empty": ""},
            attrs_number={"zero": 0},
            attrs_bool={"flag": 0},
            attributes_extra={
                "null": None,
                "array": [1, "1", True],
                "object": {"a": "b"},
            },
        ),
        span("another", end_user_id=NEW, attrs_string={"tag": "second"}),
        span("unmapped", end_user_id=UNMAPPED, attrs_string={"tag": "unmapped"}),
        span("live", project_id=OTHER, attrs_string={"tag": "other-project"}),
        span("live", project_id=FOREIGN, attrs_string={"tag": "foreign"}),
        span("live", service_name="other", attrs_string={"tag": "other-service"}),
        span("live", observation_type="EVENT", attrs_string={"tag": "other-kind"}),
        span("live", trace_id="other", attrs_string={"tag": "other-trace"}),
        span(
            "live",
            start_time=START + timedelta(hours=1),
            attrs_string={"tag": "other-hour"},
        ),
        span("at-end", start_time=end, attrs_string={"outside": "exclusive-end"}),
        span(
            "near-end",
            start_time=end - timedelta(microseconds=1),
            attrs_string={"tag": "last-microsecond"},
        ),
        span("moved-in", start_time=START - timedelta(microseconds=1)),
        span("moved-in", _version=2, attrs_string={"tag": "moved-in"}),
        span("null-user", end_user_id=None, attrs_string={"tag": "no-user"}),
    ]
    for key, changes in (
        ("cleared", {"attrs_string": {}}),
        ("deleted", {"is_deleted": 1}),
        ("reassigned", {"end_user_id": str(UUID(int=999))}),
        ("outside", {"start_time": START - timedelta(microseconds=1)}),
    ):
        old = span(key, attrs_string={key: "obsolete"})
        rows.extend([old, {**old, "_version": 2, **changes}])
    for i, changes in enumerate(
        (
            {"attrs_string": {"mixed": "7"}},
            {"attrs_number": {"mixed": 7}},
            {"attrs_bool": {"mixed": 1}, "attrs_number": {"mixed": 9}},
            {"attributes_extra": {"mixed": [7]}, "attrs_bool": {"mixed": 1}},
            {"attributes_extra": {"mixed": None}, "attrs_string": {"mixed": "shadow"}},
            {"attrs_number": {"mixed": float("inf")}},
            {"attrs_number": {"mixed": float("nan")}},
        )
    ):
        rows.append(span(f"mixed-{i}", **changes))
    # Identical duplicate versions are safe; conflicting equal versions have
    # no uniquely defined physical winner and are deliberately not invented.
    rows.append(dict(rows[0]))
    return rows


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def expected(rows, keys, projects, days):
    latest = {}
    for row in rows:
        physical_key = tuple(
            row[k]
            for k in (
                "project_id",
                "observation_type",
                "service_name",
                "trace_id",
                "id",
            )
        ) + (row["start_time"].replace(minute=0, second=0, microsecond=0),)
        if (
            physical_key not in latest
            or row["_version"] > latest[physical_key]["_version"]
        ):
            latest[physical_key] = row
    result = {}
    for row in latest.values():
        user = REMAP.get(row["end_user_id"], row["end_user_id"])
        if (
            row["project_id"] not in projects
            or row["is_deleted"]
            or user not in (USER, UNMAPPED)
            or not START <= row["start_time"] < START + timedelta(days=days)
        ):
            continue
        for key in keys:
            for storage, kind in (
                ("attributes_extra", "json"),
                ("attrs_bool", "boolean"),
                ("attrs_number", "number"),
                ("attrs_string", "string"),
            ):
                values = row[storage] or {}
                if key not in values:
                    continue
                value = values[key]
                if kind == "boolean":
                    value = bool(value)
                elif kind == "number":
                    value = None if not math.isfinite(value) else value
                result.setdefault((user, key), set()).add((kind, canonical(value)))
                break
    return result


def execute(
    rows,
    keys,
    *,
    days=7,
    workspace=False,
    finite=True,
    join_use_nulls=0,
    query_factory=_users_attr_enrichment_query,
    compiled=None,
):
    engine = pytest.importorskip("chdb")
    sql, params = query_factory(
        **({"project_ids": [PROJECT, OTHER]} if workspace else {"project_id": PROJECT}),
        attribute_keys=keys,
        start_date=START,
        end_date=START + timedelta(days=days),
        candidate_end_user_id_map=REMAP if finite else None,
    )
    params.update(
        eu_ids=(USER, UNMAPPED),
        eu_scan_ids=(*REMAP, UNMAPPED) if finite else (USER, UNMAPPED),
    )
    if compiled is not None:
        sql, params = compiled
    columns = """project_id UUID, observation_type String, service_name String,
        trace_id String, id String, start_time DateTime64(6, 'UTC'), _version UInt64,
        is_deleted UInt8, end_user_id Nullable(UUID), attributes_extra Nullable(String),
        string_keys Array(String), string_value_indices Array(UInt32),
        number_keys Array(String), number_values Array(Float64),
        bool_keys Array(String), bool_values Array(UInt8)"""
    encoded = []
    # Encode repeated strings once, keeping the exact long values without a
    # multi-megabyte VALUES statement or an increased parser/memory limit.
    string_pool = list(
        dict.fromkeys(value for row in rows for value in row["attrs_string"].values())
    )
    string_indices = {value: index + 1 for index, value in enumerate(string_pool)}
    for row in rows:
        values = [
            row[k]
            for k in (
                "project_id",
                "observation_type",
                "service_name",
                "trace_id",
                "id",
            )
        ]
        values += [
            row["start_time"].isoformat(sep=" "),
            row["_version"],
            row["is_deleted"],
            row["end_user_id"],
            None
            if row["attributes_extra"] is None
            else canonical(row["attributes_extra"]),
        ]
        for storage in ("attrs_string", "attrs_number", "attrs_bool"):
            stored_values = list(row[storage].values())
            if storage == "attrs_string":
                stored_values = [string_indices[value] for value in stored_values]
            values.extend([list(row[storage]), stored_values])
        encoded.append(tuple(values))
    literals = escape_params(
        {
            "columns": columns,
            "rows": tuple(encoded),
            "remaps": ((USER, NEW), (ALIAS, NEW)),
            "string_pool": string_pool,
        },
        CONTEXT,
    )
    rendered = without_query_settings(sql) % escape_params(params, CONTEXT)
    # Dimension fixtures already contain their latest rows; only this fixed
    # fixture's FINAL is removed. Preserve every predicate when lowering PREWHERE.
    rendered = rendered.replace("end_user_id_remap FINAL", "end_user_id_remap")
    assert rendered.lstrip().startswith("WITH")
    rendered = f"""WITH {literals["string_pool"]} AS fixture_string_pool, spans AS (
        SELECT *, mapFromArrays(string_keys,
            arrayMap(i -> fixture_string_pool[i], string_value_indices)) AS attrs_string,
            mapFromArrays(number_keys, number_values) AS attrs_number,
            mapFromArrays(bool_keys, bool_values) AS attrs_bool
        FROM values({literals["columns"]}, {literals["rows"][1:-1]})
    ), end_user_id_remap AS (
        SELECT * FROM values('old_id UUID, new_id UUID', {literals["remaps"][1:-1]})
    ), {rendered.lstrip()[4:]}
    SETTINGS join_use_nulls={join_use_nulls}, max_threads=1,
        max_execution_time=5, max_memory_usage=268435456
    """
    try:
        result = engine.query(values_where(rendered), "JSONEachRow")
    except RuntimeError as exc:
        # Native client errors otherwise echo the entire long-string fixture.
        raise RuntimeError(str(exc).split("(query:", 1)[0][:1500]) from None
    return [json.loads(line) for line in str(result).splitlines() if line]


def decoded(rows):
    result = {
        (row["end_user_id"], row["attribute_key"]): {
            (kind, canonical(json.loads(value)))
            for kind, value in row["attribute_typed_values"]
        }
        for row in rows
    }
    assert len(result) == len(rows), "one output per user/key"
    return result


def test_requested_keys_keep_full_physical_replay_and_post_collapse_visibility():
    sql, params = _users_attr_enrichment_query(
        project_id=PROJECT, attribute_keys=["tag", "tag", "long"]
    )
    latest = cte(sql, "latest_candidate_attribute_values")
    group = latest.split("GROUP BY", 1)[1]
    for column in (
        "project_id",
        "observation_type",
        "service_name",
        "identity_hour",
        "trace_id",
        "id",
    ):
        assert column in group
    scan = latest.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "candidate_span_identities" in scan
    assert "is_deleted = 0" not in scan and "end_user_id IN" not in scan
    assert "latest_is_deleted = 0" in sql
    assert "GROUP BY end_user_id, attribute_key" in sql
    assert params["requested_attribute_keys"] == ["tag", "long"]


def test_duplicate_single_key_is_bound_once():
    sql, params = _users_attr_enrichment_query(
        project_id=PROJECT, attribute_keys=["long", "long"]
    )
    assert "%(requested_attribute_keys)s" in sql and "'long'" not in sql
    assert params["requested_attribute_keys"] == ["long"]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("key", KEYS)
def test_each_single_typed_key_replays_exactly(key, days):
    rows = cohort(days)
    assert decoded(execute(rows, [key], days=days)) == expected(
        rows, [key], [PROJECT], days
    )


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("finite", [False, True])
@pytest.mark.parametrize("join_use_nulls", [0, 1])
def test_typed_replay_matches_full_physical_truth(
    days, workspace, finite, join_use_nulls
):
    rows = cohort(days)
    actual = execute(
        rows,
        KEYS,
        days=days,
        workspace=workspace,
        finite=finite,
        join_use_nulls=join_use_nulls,
    )
    assert decoded(actual) == expected(
        rows, KEYS, [PROJECT, OTHER] if workspace else [PROJECT], days
    )


@pytest.mark.parametrize("key_count", [1, 4, 10])
def test_requested_key_width_keeps_sparse_and_removed_values(key_count):
    keys = [f"key-{i}" for i in range(key_count)]
    rows = [
        span("dense", attrs_string=dict.fromkeys(keys, LONG)),
        span("sparse", attrs_string={keys[-1]: "sparse"}),
        span("removed", attrs_string=dict.fromkeys(keys, "obsolete")),
        span("removed", _version=2),
        span("null-extra", attributes_extra=None, attrs_string={keys[0]: "fallback"}),
    ]
    assert decoded(execute(rows, keys)) == expected(rows, keys, [PROJECT], 7)


def test_actual_collector_preserves_mixed_values_and_missing_keys():
    rows = cohort(7)
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        attribute_keys=list(KEYS),
    )
    calls = []

    def read(sql, params, **kwargs):
        calls.append(params["requested_attribute_keys"])
        return SimpleNamespace(
            data=execute(
                rows, params["requested_attribute_keys"], compiled=(sql, params)
            )
        )

    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = read
        attrs = manager._read_span_attributes(
            [{"end_user_id": USER}],
            None,
            start_date=START,
            end_date=START + timedelta(days=7),
            candidate_scan_ids=list(REMAP),
            candidate_end_user_id_map=REMAP,
        )
    assert [key for batch in calls for key in batch] == list(KEYS)
    assert max(map(len, calls)) == 4  # Existing production batch size is unchanged.
    assert attrs[USER]["long"] == LONG and attrs[USER]["empty"] == ""
    assert attrs[USER]["zero"] == 0 and attrs[USER]["flag"] == "false"
    assert attrs[USER]["null"] is None
    assert {"cleared", "deleted", "reassigned", "outside", "absent"}.isdisjoint(
        attrs[USER]
    )


def test_actual_collector_reads_all_250_long_keys_in_existing_batches():
    keys = [f"key-{i}" for i in range(250)]
    rows = [
        span("dense", attrs_string=dict.fromkeys(keys, LONG)),
        span("sparse", attrs_string={keys[-1]: "sparse"}),
        span("removed", attrs_string=dict.fromkeys(keys, "obsolete")),
        span("removed", _version=2),
        span("null-extra", attributes_extra=None, attrs_string={keys[0]: "fallback"}),
    ]
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        attribute_keys=keys,
    )
    calls, actual = [], []

    def read(sql, params, **kwargs):
        calls.append(params["requested_attribute_keys"])
        result = execute(
            rows, params["requested_attribute_keys"], compiled=(sql, params)
        )
        actual.extend(result)
        return SimpleNamespace(data=result)

    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = read
        attrs = manager._read_span_attributes(
            [{"end_user_id": USER}],
            None,
            start_date=START,
            end_date=START + timedelta(days=7),
            candidate_scan_ids=list(REMAP),
            candidate_end_user_id_map=REMAP,
        )
    assert len(calls) == 63 and max(map(len, calls)) == 4
    assert [key for batch in calls for key in batch] == keys
    assert decoded(actual) == expected(rows, keys, [PROJECT], 7)
    assert set(attrs[USER]) == set(keys)
    assert attrs[USER][keys[1]] == LONG
    assert set(attrs[USER][keys[0]]) == {LONG, "fallback"}
    assert set(attrs[USER][keys[-1]]) == {LONG, "sparse"}

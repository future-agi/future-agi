"""Independent literal expectations over local UTC short-primary-key RMTs.

No application compiler/builders, network sockets, credentials or production
fixtures. Run with an explicit chdb Python path. The reduced CH25 chdb build
lacks lowerUTF8: run non-text cases there, and the unmodified full suite on a
full-function engine; report that distinction, never substitute lower().
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from clickhouse_driver.util.escape import escape_params

import observe_session_reference as reference
from replay_observe_filters import ReplayError


PROJECT = str(UUID(int=1))
OTHER_PROJECT = str(UUID(int=2))
START = datetime(2026, 8, 1, 12, 30, 0, 123456, tzinfo=timezone.utc)


def sid(number):
    return str(UUID(int=number))


def leaf(kind="number", op="equals", value=1, key="key"):
    return {"column_id": key, "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": kind,
        "filter_op": op, "filter_value": value,
    }}


def picker(values, types, *, op="in", kind="text", alias="attribute_value_types"):
    item = leaf(kind=kind, op=op, value=values, key="company_id")
    item["filter_config"][alias] = types
    return item


def request(**changes):
    return {"project_ids": [PROJECT], "authorized_project_ids": [PROJECT, OTHER_PROJECT],
            "start": START, "end": START + timedelta(days=7), "page_size": 2,
            "order_mode": "uuid_string", **changes}


def broad_pages(engine, monkeypatch, **options):
    # Test-only escape: parity with the immutable pre-integration compiler was
    # checked before release. No alternate SQL or production fallback switch.
    with monkeypatch.context() as patch:
        patch.setattr(reference, "_positive_witness_filter", lambda _: None)
        return reference.reference_session_pages(engine, **options)


def ids(result):
    return [[row["session_id"] for row in page] for page in result["pages"]]


class LocalRMT:
    def __init__(self, path):
        from chdb.session import Session
        self.session = Session(str(path))
        self.calls = []
        self.context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
        self.execute("SET output_format_json_quote_64bit_integers=0")
        self.version = self.execute("SELECT version() AS version")[0]["version"]
        self.execute("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String, parent_span_id Nullable(String),
            trace_session_id Nullable(UUID), end_time Nullable(DateTime64(6, 'UTC')),
            cost Float64, total_tokens Int64, attrs_string Map(String, String),
            attrs_number Map(String, Float64), attrs_bool Map(String, UInt8),
            is_deleted UInt8, _version UInt64
        ) ENGINE=ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
          ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
          SETTINGS index_granularity=8192""")
        self.execute("""CREATE TABLE trace_session_id_remap (
            old_id UUID, new_id UUID, version DateTime64(6, 'UTC')
        ) ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
        self.execute("SYSTEM STOP MERGES spans")
        self.execute("SYSTEM STOP MERGES trace_session_id_remap")

    def execute(self, sql, params=None, settings=None):
        if params:
            sql = sql % escape_params(params, self.context)
        if settings:
            sql += " SETTINGS " + ", ".join(f"{key}={value}" for key, value in settings.items())
        return [json.loads(line) for line in str(self.session.query(sql, "JSONEachRow")).splitlines() if line]

    def execute_ch_query(self, sql, params, *, settings):
        assert sql.lstrip().startswith(("WITH", "SELECT"))
        self.calls.append((sql, deepcopy(params), dict(settings)))
        return SimpleNamespace(data=self.execute(sql, params, settings))

    def insert(self, session, **changes):
        row = {
            "project_id": PROJECT, "observation_type": "SPAN", "service_name": "",
            "start_time": START + timedelta(minutes=1),
            "trace_id": f"trace-{session}", "id": f"root-{session}",
            "parent_span_id": None, "trace_session_id": sid(session),
            "end_time": START + timedelta(minutes=2), "cost": 1.25, "total_tokens": 10,
            "attrs_string": {}, "attrs_number": {}, "attrs_bool": {},
            "is_deleted": 0, "_version": 1, **changes,
        }
        values = []
        params = {}
        for index, (name, value) in enumerate(row.items()):
            param = f"v{index}"
            if isinstance(value, datetime):
                params[param] = reference._us(value)
                values.append(f"fromUnixTimestamp64Micro(%({param})s, 'UTC')")
            else:
                params[param] = value
                values.append(f"%({param})s")
        self.execute(f"INSERT INTO spans ({', '.join(row)}) VALUES ({', '.join(values)})", params)
        return row

    def remap(self, old, new, version=START):
        self.execute("""INSERT INTO trace_session_id_remap VALUES (
            %(old)s, %(new)s, fromUnixTimestamp64Micro(%(version)s, 'UTC'))""",
            {"old": sid(old), "new": sid(new), "version": reference._us(version)})


@pytest.fixture
def engine(tmp_path, record_property):
    pytest.importorskip("chdb")
    local = LocalRMT(tmp_path / "session-reference-rmt")
    record_property("clickhouse_version", local.version)
    try:
        yield local
    finally:
        local.session.close()


@pytest.mark.parametrize("days", [7, 30, 365])
def test_latest_tombstone_shifts_order_across_two_pages(engine, days):
    for session, minute in ((100, 1), (200, 20), (300, 10), (400, 5), (500, 2)):
        engine.insert(session, start_time=START + timedelta(minutes=minute))
    # An early raw root used to order session 100 last. Its latest tombstone
    # must shift first_seen to the other root, ahead of every other session.
    engine.insert(100, _version=2, is_deleted=1)
    engine.insert(100, id="late-root", trace_id="second-trace",
                  start_time=START + timedelta(minutes=25), _version=2)
    result = reference.reference_session_pages(engine, **request(end=START + timedelta(days=days)))
    assert ids(result) == [[sid(100), sid(200)], [sid(300), sid(400)]]
    assert result["total_sessions"] == 5
    assert result["has_more_after_two_pages"] is True
    assert result["population_exhausted_after_two_pages"] is False
    assert result["pages"][0][0]["first_root"][5:] == [
        "late-root", reference._us(START + timedelta(minutes=25)), 2,
    ]
    assert result["session_version"] is None
    assert result["first_root_version_is_session_version"] is False
    assert len(engine.calls) == 1


@pytest.mark.parametrize("replacement", ["tombstone", "before-start", "after-end", "child", "session-null"])
def test_physical_winner_before_mutable_membership(engine, replacement):
    end = START + timedelta(minutes=10)
    engine.insert(100, attrs_number={"key": 1})
    changes = {
        "tombstone": {"is_deleted": 1},
        "before-start": {"start_time": START - timedelta(microseconds=1)},
        "after-end": {"start_time": end},
        "child": {"parent_span_id": "parent"},
        "session-null": {"trace_session_id": None},
    }[replacement]
    engine.insert(100, _version=2, **changes)
    result = reference.reference_session_pages(engine, **request(end=end, filters=[leaf()]))
    assert ids(result) == [[], []]
    assert result["total_sessions"] == 0
    assert result["population_exhausted_after_two_pages"] is True
    assert result["metrics"] == []


@pytest.mark.parametrize("days", [7, 30, 365])
def test_exact_microseconds_and_all_physical_keys(engine, days):
    end = START + timedelta(days=days)
    engine.insert(100, start_time=START)
    engine.insert(200, start_time=START - timedelta(microseconds=1))
    engine.insert(300, start_time=end)
    engine.insert(400, start_time=end - timedelta(microseconds=1))
    engine.insert(100, project_id=OTHER_PROJECT, _version=99, is_deleted=1)
    # Same external trace/id, distinct service/type/hour are distinct roots.
    engine.insert(100, start_time=START, service_name="svc", _version=3)
    engine.insert(100, start_time=START, observation_type="GENERATION", _version=4)
    engine.insert(100, start_time=START + timedelta(hours=1), _version=5)
    result = reference.reference_session_pages(engine, **request(end=end))
    assert ids(result) == [[sid(400), sid(100)], []]
    row = next(r for r in result["metrics"] if r["session_id"] == sid(100))
    assert row["root_count"] == 4 and row["traces_count"] == 1
    assert row["total_tokens"] == 40 and row["total_cost"] == 5
    assert row["session_start_us"] == reference._us(START)


def test_latest_remap_consolidation_no_fanout_and_complete_group(engine):
    # The smallest old alias has no spans at all. Root-based/remap-page seeds
    # must not exclude it before selecting the group's canonical OLD ID.
    engine.remap(100, 900)
    engine.remap(200, 800)  # Superseded mapping; never split onto group 800.
    engine.remap(200, 900, START + timedelta(seconds=1))
    engine.remap(300, 900)
    engine.remap(900, 900)  # Identity row appears in both alias arms.
    engine.insert(200, total_tokens=11)
    engine.insert(300, total_tokens=13)
    engine.insert(900, total_tokens=17)
    result = reference.reference_session_pages(engine, **request())
    assert ids(result) == [[sid(100)], []]
    assert result["metrics"][0]["root_count"] == 3
    assert result["metrics"][0]["traces_count"] == 3
    assert result["metrics"][0]["total_tokens"] == 41


@pytest.mark.parametrize("order_mode,expected", [
    ("uuid", ["00000000-0000-0000-ffff-ffffffffffff", "ffffffff-ffff-ffff-0000-000000000001"]),
    ("uuid_string", ["ffffffff-ffff-ffff-0000-000000000001", "00000000-0000-0000-ffff-ffffffffffff"]),
])
def test_explicit_uuid_order_contract_crosses_page_boundary(engine, order_mode, expected):
    for index, session_id in enumerate(expected):
        engine.insert(100 + index, trace_session_id=session_id)
    result = reference.reference_session_pages(engine, **request(page_size=1, order_mode=order_mode))
    assert ids(result) == [[expected[0]], [expected[1]]]
    assert result["population_exhausted_after_two_pages"] is True


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 52])
def test_exact_two_visible_pages_and_exhaustion(engine, count):
    for index in range(count):
        engine.insert(100 + index, start_time=START + timedelta(microseconds=index))
    size = 25 if count == 52 else 2
    result = reference.reference_session_pages(engine, **request(page_size=size))
    expected = [sid(100 + i) for i in reversed(range(count))][:2 * size]
    assert ids(result) == [expected[:size], expected[size:]]
    assert result["total_sessions"] == count
    assert result["has_more_after_two_pages"] is (count > size * 2)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_session_and_leaves_can_have_different_children_traces_and_aliases(engine, days):
    engine.remap(100, 900)
    engine.remap(200, 900)
    engine.insert(100, total_tokens=10)
    engine.insert(200, total_tokens=20, cost=2.5)
    engine.insert(900, id="child-a", parent_span_id="parent", attrs_number={"a": 7}, total_tokens=999)
    engine.insert(200, id="child-b", parent_span_id="parent", attrs_bool={"b": 1}, total_tokens=999)
    filters = [leaf(value=7, key="a"), leaf(kind="boolean", value=True, key="b")]
    result = reference.reference_session_pages(engine, **request(filters=filters, end=START + timedelta(days=days)))
    assert ids(result) == [[sid(100)], []]
    assert result["metrics"][0]["total_tokens"] == 30
    assert result["metrics"][0]["total_cost"] == 3.75
    assert result["metrics"][0]["traces_count"] == 2


@pytest.mark.parametrize("operation,value,expected", [
    ("equals", 0, [400]), ("not_equals", 2, [400, 300, 200]),
    ("in", [0, 2], [400, 300, 100]), ("not_in", [0, 2], [300, 200]),
    ("greater_than", 2, [300, 200]), ("greater_than_or_equal", 2, [300, 200, 100]),
    ("less_than", 2, [400]), ("less_than_or_equal", 2, [400, 300, 100]),
    ("between", [0, 2], [400, 300, 100]), ("not_between", [0, 2], [300, 200]),
    ("is_null", None, [600, 500]), ("is_not_null", None, [400, 300, 200, 100]),
])
def test_numeric_leaf_truth_not_notexists_positive(engine, operation, value, expected):
    for session, numbers in ((100, {"key": 2}), (200, {"key": 5}),
                             (300, {"key": 2}), (400, {"key": 0}),
                             (500, {}), (600, {})):
        engine.insert(session, attrs_number=numbers)
    engine.insert(300, id="different-child", parent_span_id="parent", attrs_number={"key": 5})
    # Wrong storage domain must not turn missing numeric keys into presence.
    engine.insert(600, id="text-child", parent_span_id="parent", attrs_string={"key": "2"})
    result = reference.reference_session_pages(engine, **request(page_size=10, filters=[leaf(op=operation, value=value)]))
    assert ids(result) == [[sid(x) for x in expected], []]


@pytest.mark.parametrize("op,value,expected", [
    ("equals", False, [300, 200]), ("not_equals", True, [300, 200]),
    ("in", [True], [300, 100]), ("not_in", [True], [300, 200]),
    ("is_null", None, [400]), ("is_not_null", None, [300, 200, 100]),
])
def test_boolean_false_and_missing_are_distinct(engine, op, value, expected):
    for session, values in ((100, {"key": 1}), (200, {"key": 0}), (300, {"key": 1}), (400, {})):
        engine.insert(session, attrs_bool=values)
    engine.insert(300, id="false-child", parent_span_id="parent", attrs_bool={"key": 0})
    result = reference.reference_session_pages(engine, **request(page_size=10, filters=[leaf(kind="boolean", op=op, value=value)]))
    assert ids(result) == [[sid(x) for x in expected], []]


@pytest.mark.parametrize("change", ["removed", "deleted", "outside-window", "moved-session"])
def test_session_null_uses_all_latest_children_not_any_missing_child(engine, change):
    engine.remap(100, 900)
    engine.insert(100)
    child = {"id": "present-child", "parent_span_id": "parent", "attrs_number": {"key": 2}}
    engine.insert(900, **child)
    assert ids(reference.reference_session_pages(engine, **request(filters=[leaf(op="is_null", value=None)]))) == [[], []]
    changes = {"removed": {"attrs_number": {}}, "deleted": {"is_deleted": 1},
               "outside-window": {"start_time": START - timedelta(microseconds=1)},
               "moved-session": {"trace_session_id": sid(800)}}[change]
    engine.insert(900, **{**child, **changes, "_version": 2})
    result = reference.reference_session_pages(engine, **request(filters=[leaf(op="is_null", value=None)]))
    assert ids(result) == [[sid(100)], []]


@pytest.mark.parametrize("change", ["removed", "deleted", "outside-window", "moved-session"])
def test_negative_leaf_loses_its_last_differing_witness(engine, change):
    engine.insert(100, attrs_number={"key": 2})
    child = {"id": "different", "parent_span_id": "parent", "attrs_number": {"key": 5}}
    engine.insert(100, **child)
    changes = {"removed": {"attrs_number": {}}, "deleted": {"is_deleted": 1},
               "outside-window": {"start_time": START - timedelta(microseconds=1)},
               "moved-session": {"trace_session_id": sid(800)}}[change]
    engine.insert(100, **{**child, **changes, "_version": 2})
    result = reference.reference_session_pages(engine, **request(filters=[leaf(op="not_equals", value=2)]))
    assert ids(result) == [[], []]


def test_null_root_end_and_latest_root_metrics_are_not_stale_or_child_totals(engine):
    engine.insert(100, cost=7, total_tokens=70)
    engine.insert(100, _version=2, cost=2.5, total_tokens=25, end_time=None)
    engine.insert(100, id="child", parent_span_id="parent", total_tokens=900, cost=90,
                  end_time=START + timedelta(days=1))
    result = reference.reference_session_pages(engine, **request())
    metrics = result["metrics"][0]
    assert metrics["session_end_us"] is None and metrics["duration"] is None
    assert metrics["total_cost"] == 2.5 and metrics["total_tokens"] == 25
    assert metrics["root_count"] == metrics["traces_count"] == 1
    assert result["pages"][0][0]["first_root"][-1] == 2
    assert result["graph_metrics_verified"] is False


@pytest.mark.parametrize("width", [1, 2, 5, 10])
def test_independent_and_widths_with_raw_native_name_collision(engine, width):
    engine.insert(100)
    keys = ["created_at", "start_time", "user_id", "end_user_id", "session_id",
            "has_eval", "has_annotation", "total_tokens", "duration", "total_cost"][:width]
    for index, key in enumerate(keys):
        engine.insert(100, id=f"child-{index}", parent_span_id="parent", attrs_number={key: 1})
    result = reference.reference_session_pages(engine, **request(filters=[leaf(key=key) for key in keys]))
    assert ids(result) == [[sid(100)], []]


@pytest.mark.parametrize("op,value,expected", [
    ("equals", "abc%_\\", [300, 100]), ("not_equals", "abc%_\\", [400, 300, 200]),
    ("in", ["abc%_\\", "XYZ"], [300, 200, 100]),
    ("not_in", ["abc%_\\", "xyz"], [400]),
    ("contains", "%_\\", [300, 100]), ("not_contains", "%_\\", [400, 300, 200]),
    ("starts_with", "abc", [300, 100]), ("ends_with", "%_\\", [300, 100]),
])
def test_text_literal_and_negative_session_witnesses(engine, op, value, expected):
    for session, values in ((100, {"key": "AbC%_\\"}), (200, {"key": "XYZ"}),
                            (300, {"key": "AbC%_\\"}), (400, {"key": ""}), (500, {})):
        engine.insert(session, attrs_string=values)
    engine.insert(300, id="different-child", parent_span_id="parent", attrs_string={"key": "XYZ"})
    result = reference.reference_session_pages(engine, **request(page_size=10, filters=[leaf(kind="text", op=op, value=value)]))
    assert ids(result) == [[sid(x) for x in expected], []]


@pytest.mark.parametrize("change,code", [
    ({"project_ids": []}, "EXPLICIT_PROJECT_SCOPE"),
    ({"project_ids": [OTHER_PROJECT], "authorized_project_ids": [PROJECT]}, "NOT_AUTHORIZED"),
    ({"project_ids": [PROJECT, OTHER_PROJECT]}, "WORKSPACE_NOT_QUALIFIED"),
    ({"project_ids": [PROJECT, PROJECT]}, "NOT_AUTHORIZED"),
    ({"project_ids": ["not-a-uuid"]}, "INVALID_UUID"),
    ({"order_mode": "implicit"}, "EXPLICIT_ORDER"),
    ({"start": START.replace(tzinfo=None)}, "EXPLICIT_TIMEZONE"),
    ({"end": START}, "INVALID_WINDOW"),
    ({"page_size": True}, "INVALID_PAGE_SIZE"),
    ({"filters": [{"operator": "OR", "filters": [leaf()]}]}, "FLAT_AND"),
    ({"filters": [leaf(kind="map", value={"x": 1})]}, "UNSUPPORTED_TYPE"),
    ({"filters": [leaf(kind="array", value=[1])]}, "UNSUPPORTED_TYPE"),
    ({"filters": [leaf(op="equals", value=True)]}, "VALUE_TYPE_MISMATCH"),
    ({"filters": [leaf(op="between", value=[1])]}, "VALUE_TYPE_MISMATCH"),
    ({"filters": [leaf(op="not_in", value=[])]}, "VALUE_TYPE_MISMATCH"),
    ({"filters": [leaf(op="made_up")]}, "UNSUPPORTED_OPERATOR"),
    ({"filters": [leaf(key=f"key{i}") for i in range(11)]}, "TOO_MANY"),
])
def test_unsupported_scope_and_shapes_fail_before_reader(change, code):
    with pytest.raises(ReplayError, match=code):
        reference.reference_session_pages(object(), **request(**change))


@pytest.mark.parametrize("source", ["SYSTEM_METRIC", "EVAL_METRIC", "ANNOTATION", "NORMAL"])
def test_native_relational_and_implicit_sources_are_not_guessed(source):
    item = leaf(key="user_id")
    item["filter_config"]["col_type"] = source
    with pytest.raises(ReplayError, match="UNSUPPORTED_FILTER"):
        reference.reference_session_pages(object(), **request(filters=[item]))


@pytest.mark.parametrize("kind", ["text", "number", "boolean"])
@pytest.mark.parametrize("op", ["in", "not_in"])
def test_picker_provenance_and_complete_anchor_or(kind, op):
    for alias in ("attribute_value_types", "attributeValueTypes"):
        item = picker(["0012", 12, False, "K"], ["string", "number", "boolean", "string"],
                      kind=kind, op=op, alias=alias)
        original = deepcopy(item)
        sql, params = reference.build_session_reference_query(**request(filters=[item]))
        assert item == original
        assert params["ref_value_0_string"] == ["0012", "k"]
        assert type(params["ref_value_0_number"][0]) is int
        assert params["ref_value_0_boolean"][0] is False
        anchored = kind != "text" and op == "in"
        assert ("raw_positive_witnesses AS (" in sql) is anchored
        if anchored:
            raw = sql.split("raw_positive_witnesses AS (", 1)[1].split("), (", 1)[0]
            assert raw.count(" OR ") == 2 and raw.count(" AND has(") == 3
            assert "lowerUTF8(attrs_string" in raw and "mapContains(attrs_bool" in raw
            assert params["narrow_ref_value_0_string"] == ["0012", "k"]
            assert params["narrow_ref_value_0_boolean"][0] is False
        assert "countIf(" in sql and sql.count("LIMIT") == 1


@pytest.mark.parametrize("values,types,extra,code", [
    (["12"], ["number"], {}, "VALUE_TYPE_MISMATCH"),
    ([12], ["string"], {}, "VALUE_TYPE_MISMATCH"),
    ([False], ["number"], {}, "VALUE_TYPE_MISMATCH"),
    ([0], ["boolean"], {}, "VALUE_TYPE_MISMATCH"),
    ([float("nan")], ["number"], {}, "VALUE_TYPE_MISMATCH"),
    ([float("inf")], ["number"], {}, "VALUE_TYPE_MISMATCH"),
    ([None], ["string"], {}, "VALUE_TYPE_MISMATCH"),
    ([""], ["string"], {}, "VALUE_TYPE_MISMATCH"),
    (["x"], ["json"], {}, "PROVENANCE_TYPE"),
    (["x"], [], {}, "PROVENANCE_ALIGNMENT"),
    ([], [], {}, "PROVENANCE_ALIGNMENT"),
    (["x"], ["string"], {"attributeValueTypes": ["number"]}, "PROVENANCE_CONFLICT"),
    (["x"], ["string"], {"filter_op": "equals"}, "PROVENANCE_OPERATOR"),
])
def test_picker_invalid_provenance_fails_before_reader_and_anchor(values, types, extra, code):
    item = picker(values, types)
    item["filter_config"].update(extra)
    with pytest.raises(ReplayError, match=code):
        reference.reference_session_pages(object(), **request(filters=[leaf(), item]))


def test_query_independence_full_replacement_and_outer_only_limit():
    sql, params = reference.build_session_reference_query(**request(filters=[leaf(op="is_null", value=None)]))
    assert sql.count("LIMIT") == 1 and sql.rstrip().endswith("LIMIT %(ref_visible_limit)s")
    assert params["ref_visible_limit"] == 5
    physical = sql.split("FROM spans FINAL", 1)[1].split("), live_window_spans", 1)[0]
    assert "toStartOfHour(start_time)" in physical
    assert "is_deleted" not in physical and "parent_span_id" not in physical
    assert "attrs_" not in physical
    assert "candidate" not in sql and "spans_per_session" not in sql
    assert "FROM trace_session_id_remap FINAL" in sql
    assert "HAVING root_count > 0 AND (countIf(mapContains(attrs_number" in sql
    assert "sumIf(total_tokens, is_root)" in sql
    source = inspect.getsource(reference)
    assert "from tracer." not in source and "import tracer" not in source
    assert all(value == 0 for value in reference.SAFE_FINAL_SETTINGS.values())


def test_exact_native_window_must_equal_explicit_frozen_window():
    item = {"column_id": "created_at", "filter_config": {"col_type": "SYSTEM_METRIC",
        "filter_type": "datetime", "filter_op": "between",
        "filter_value": [START.isoformat(), (START + timedelta(days=7)).isoformat()]}}
    reference.build_session_reference_query(**request(filters=[item]))
    item["filter_config"]["filter_value"][1] = (START + timedelta(days=6)).isoformat()
    with pytest.raises(ReplayError, match="WINDOW_FILTER_MISMATCH"):
        reference.build_session_reference_query(**request(filters=[item]))


def test_reader_failure_never_becomes_empty_or_success():
    class Failing:
        def execute_ch_query(self, *args, **kwargs):
            raise RuntimeError("reference reader failed")
    with pytest.raises(RuntimeError, match="reference reader failed"):
        reference.reference_session_pages(Failing(), **request())


@pytest.mark.parametrize("damage", ["duplicate", "truncated", "wrong-project", "wrong-order", "no-version"])
def test_partial_or_malformed_reference_fails_closed(engine, damage):
    for session in (100, 200, 300, 400, 500):
        engine.insert(session)
    sql, params = reference.build_session_reference_query(**request())
    rows = engine.execute(sql, params, reference.SAFE_FINAL_SETTINGS)
    if damage == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif damage == "truncated":
        rows.pop()
    elif damage == "wrong-project":
        rows[0]["project_id"] = OTHER_PROJECT
    elif damage == "wrong-order":
        rows.reverse()
    else:
        rows[0]["first_root"].pop()
    class Damaged:
        def execute_ch_query(self, *args, **kwargs):
            return SimpleNamespace(data=rows)
    with pytest.raises(ReplayError, match="RESULT_INVALID_OR_INCOMPLETE"):
        reference.reference_session_pages(Damaged(), **request())


@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("kind,value,op", [("number", 1, "greater_than"), ("boolean", False, "equals")])
def test_raw_uuid_population_matches_broad_all_aliases_and_stale_move(engine, monkeypatch, width, kind, value, op):
    for old in (100, 200, 300):
        engine.remap(old, 900)
    engine.insert(200, start_time=START + timedelta(minutes=4), total_tokens=11)
    engine.insert(300, start_time=START + timedelta(minutes=1), total_tokens=13)
    column = "attrs_number" if kind == "number" else "attrs_bool"
    matching, different = (7, 0) if kind == "number" else (0, 1)
    engine.insert(900, id="anchor", parent_span_id="root", total_tokens=999,
                  **{column: {"anchor": matching}})
    filters = [leaf(kind=kind, value=value, op=op, key="anchor")]
    for index in range(1, width):
        missing = index % 2 == 0
        filters.append(leaf(key=f"extra{index}", op="is_null" if missing else "not_equals",
                            value=None if missing else 1))
        if not missing:
            engine.insert(300, id=f"child{index}", parent_span_id="root",
                          attrs_number={f"extra{index}": 0})
            engine.insert(400, id=f"child{index}", parent_span_id="root",
                          attrs_number={f"extra{index}": 0})
    engine.insert(400)
    engine.insert(500)
    child = dict(id="stale-anchor", parent_span_id="root")
    engine.insert(400, **child, **{column: {"anchor": matching}})
    # A -> B and matching -> nonmatching in the SAME full physical key. B is
    # absent from raw witnesses; pruning B before FINAL would resurrect A.
    engine.insert(400, **child, trace_session_id=sid(500), _version=2,
                  **{column: {"anchor": different}})
    for selected in (filters, filters[::-1]):
        options = request(filters=selected, order_mode="uuid" if width == 5 else "uuid_string")
        expected = broad_pages(engine, monkeypatch, **options)
        actual = reference.reference_session_pages(engine, **options)
        assert actual == expected
        assert ids(actual) == [[sid(100)], []]
        assert actual["metrics"][0]["total_tokens"] == 24
        assert actual["metrics"][0]["root_count"] == 2
        assert actual["metrics"][0]["session_start_us"] == reference._us(START + timedelta(minutes=1))


@pytest.mark.parametrize("filters,anchored", [
    ([leaf(op="greater_than", value=1)], True),
    ([leaf(kind="boolean", value=False)], True),
    ([leaf(op="not_equals", value=1)], False),
    ([leaf(op="is_null", value=None)], False),
    ([leaf(kind="text", value="Kelvin")], False),
])
def test_raw_uuid_scope_and_unanchored_compile_contract(monkeypatch, filters, anchored):
    options = request(filters=filters)
    sql, params = reference.build_session_reference_query(**options)
    with monkeypatch.context() as patch:
        patch.setattr(reference, "_positive_witness_filter", lambda _: None)
        broad, bindings = reference.build_session_reference_query(**options)
    if not anchored:
        assert (sql, params) == (broad, bindings)
        return
    raw = sql.split("raw_positive_witnesses AS (", 1)[1].split("), (", 1)[0]
    assert "mapContains(attrs_" in raw and "toStartOfHour(start_time)" in raw
    for forbidden in ("FINAL", "JOIN", "is_deleted", "parent_span_id", "LIMIT", "SAMPLE", "start_time >= fromUnix"):
        assert forbidden not in raw
    assert "trace_session_id IN (SELECT toUUIDOrNull(arrayJoin(reference_session_aliases)))" in sql
    assert "toString(trace_session_id) IN" not in sql
    assert sql.count("FROM spans FINAL") == 1 and sql.count("LIMIT") == 1
    assert all(params[name] == value for name, value in bindings.items())


@pytest.mark.parametrize("width", range(1, 11))
@pytest.mark.parametrize("op", ["in", "not_in"])
@pytest.mark.parametrize("with_text", [False, pytest.param(True, id="picker_text")])
def test_picker_session_widths_latest_alias_scope_and_presence(engine, monkeypatch, width, op, with_text):
    end = START + timedelta(minutes=20)
    selected = [12, False]
    types = ["number", "boolean"]
    if with_text:
        selected.insert(0, "0012")
        types.insert(0, "string")
    item = picker(selected, types, kind="number", op=op,
                  alias="attributeValueTypes" if width % 2 else "attribute_value_types")
    filters = [item]
    for index in range(1, width):
        operation = ("not_equals", "greater_than", "is_null")[index % 3]
        filters.append(leaf(key=f"extra{index}", op=operation,
                            value=None if operation == "is_null" else 0))
    positive = op == "in"
    good = {"attrs_number": {"company_id": 12 if positive else 7}}
    if with_text:
        good = {"attrs_string": {"company_id": "0012" if positive else "safe"}}
    bad = {"attrs_number": {"company_id": 0 if positive else 7},
           "attrs_bool": {"company_id": 1 if positive else 0}}
    if with_text:
        bad["attrs_string"] = {"company_id": "12" if positive else "safe"}

    # No physical root for canonical 100; all alias roots must supply metrics.
    engine.remap(102, 950, version=START - timedelta(days=1))
    for old in (100, 101, 102):
        engine.remap(old, 900)
    engine.insert(101, start_time=START + timedelta(minutes=1), total_tokens=11)
    engine.insert(102, start_time=START + timedelta(minutes=3), total_tokens=13)
    engine.insert(101, project_id=OTHER_PROJECT, _version=99, is_deleted=1)
    sessions = [900, 200, 300, 400, 500, 600, *range(700, 712)]
    for session in sessions:
        if session != 900:
            minute = {200: 2, 300: 3, 600: 4, 710: 8, 711: 9}.get(session, 6)
            engine.insert(session, start_time=START + timedelta(minutes=minute),
                          project_id=OTHER_PROJECT if session == 707 else PROJECT,
                          is_deleted=int(session == 709))
        # Siblings are independent latest spans/traces, not same-row ANDs.
        for index, sibling in enumerate(filters[1:], 1):
            missing = sibling["filter_config"]["filter_op"] == "is_null"
            if (missing and session != 710) or (session == 711 and index == 1):
                continue
            engine.insert(102 if session == 900 else session,
                          id=f"leaf-{index}", trace_id=f"leaf-{session}-{index}",
                          parent_span_id="root", attrs_number={f"extra{index}": 1})
        maps = ({200: {"attrs_bool": {"company_id": 0 if positive else 1}},
                 300: {"attrs_number": {"company_id": 12 if positive else 7}},
                 400: bad, 500: {}, 600: {"attrs_string": {"company_id": "12"}}}
                .get(session, good))
        fields = dict(id="picker", parent_span_id="root", total_tokens=999,
                      project_id=OTHER_PROJECT if session in (707, 708) else PROJECT,
                      start_time=START + timedelta(minutes=4), **maps)
        engine.insert(session, **fields)
        changes = {
            700: bad, 701: {"attrs_string": {}, "attrs_number": {}, "attrs_bool": {}},
            702: {"is_deleted": 1}, 703: {"start_time": START - timedelta(microseconds=1)},
            704: {"start_time": end}, 705: {"trace_session_id": None},
            706: {"trace_session_id": sid(899), **bad},
        }.get(session)
        if changes is not None:
            engine.insert(session, **{**fields, **changes, "_version": 2})
    if not positive:
        # A different span can satisfy NOT_IN despite an equal sibling. But
        # session 400's selected false on the SAME span blocks its safe number.
        engine.insert(300, id="equal-sibling", parent_span_id="root",
                      attrs_number={"company_id": 12})

    expected = ([711] if width == 1 else []) + ([710] if width < 3 else [])
    expected += ([600] if with_text and not positive else []) + [300, 200, 100]
    # Identical provenance with text UI type uses the unchanged broad route.
    # Number UI type may anchor, but must retain string AND false OR branches.
    for ui_type in ("number", "text"):
        item["filter_config"]["filter_type"] = ui_type
        options = request(filters=filters if width % 2 else filters[::-1], end=end,
                          page_size=10, order_mode="uuid" if width % 2 else "uuid_string")
        original = deepcopy(options)
        actual = reference.reference_session_pages(engine, **options)
        assert options == original
        assert actual == broad_pages(engine, monkeypatch, **options)
        assert ids(actual) == [[sid(s) for s in expected], []]
        assert actual["total_sessions"] == len(expected)
        metrics = next(row for row in actual["metrics"] if row["session_id"] == sid(100))
        assert metrics["root_count"] == metrics["traces_count"] == 2
        assert metrics["total_tokens"] == 24 and metrics["total_cost"] == 2.5
        assert metrics["session_start_us"] == reference._us(START + timedelta(minutes=1))
        assert actual["graph_metrics_verified"] is False


def test_picker_text_unicode_latest_literal_expectations(engine):
    for session, value in ((100, "0012"), (200, "12"), (300, "ÉCHO"), (400, "écho")):
        engine.insert(session, attrs_string={"company_id": value})
    item = picker(["0012", "ÉCHO"], ["string", "string"])
    actual = reference.reference_session_pages(engine, **request(filters=[item], page_size=10))
    assert ids(actual) == [[sid(400), sid(300), sid(100)], []]

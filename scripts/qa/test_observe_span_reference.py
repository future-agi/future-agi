"""Literal independent span expectations on isolated UTC CH25 short-key RMTs.

DDL/inserts are local chdb fixtures only; no Django, application builders,
network sockets, candidate results or production data. Reduced-engine missing
lowerUTF8 is explicitly skipped, never substituted with ASCII lower().
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import json
from types import SimpleNamespace

from clickhouse_driver.util.escape import escape_params
import pytest

import observe_span_reference as reference
from replay_observe_filters import ReplayError


PROJECT = "00000001-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
START = datetime(2026, 8, 1, 12, 30, 0, 123456, tzinfo=timezone.utc)


def leaf(kind="number", op="equals", value=1, key="key", types=None):
    cfg = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": kind,
        "filter_op": op,
        "filter_value": value,
    }
    if types is not None:
        cfg["attribute_value_types"] = types
    return {"column_id": key, "filter_config": cfg}


def case(*, days=7, target=25, page=0, end=None, surface="spans"):
    return {
        "surface": surface,
        "window": {
            "start": START.isoformat(),
            "end": (end or START + timedelta(days=days)).isoformat(),
        },
        "request": {
            "target_rows": target,
            "params": {"page_number": page, "page_size": target},
        },
    }


def scope():
    return {"project_id": PROJECT}


def public_ids(result):
    return [identity[2] for identity in result[0]]


def final_calls(engine):
    return [call for call in engine.calls if "FROM spans FINAL" in call[0]]


def full_window_truth(engine, request, selected, filters):
    """Small-fixture baseline: one unpruned FINAL, no interval traversal."""
    sql, params = reference.build_span_reference_query(
        request, selected, filters, raw_prefix=False
    )
    rows = engine.execute(sql, params, reference.SAFE_FINAL_SETTINGS)
    return [reference.span_identity(row) for row in rows]


class LocalRMT:
    def __init__(self, path):
        from chdb.session import Session

        self.session = Session(str(path))
        self.calls = []
        self.context = SimpleNamespace(
            server_info=SimpleNamespace(get_timezone=lambda: "UTC")
        )
        self.execute("SET output_format_json_quote_64bit_integers=0")
        self.version = self.execute("SELECT version() AS version")[0]["version"]
        self.execute("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String, parent_span_id Nullable(String),
            attrs_string Map(String, String), attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8), is_deleted UInt8, _version UInt64
        ) ENGINE=ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
          ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
          SETTINGS index_granularity=8192""")
        self.execute("SYSTEM STOP MERGES spans")

    def execute(self, sql, params=None, settings=None):
        if params:
            sql = sql % escape_params(params, self.context)
        if settings:
            sql += " SETTINGS " + ", ".join(
                f"{key}={value}"
                for key, value in escape_params(settings, self.context).items()
            )
        result = self.session.query(sql, "JSONEachRow")
        self.last_read_bytes = result.bytes_read()
        return [json.loads(line) for line in str(result).splitlines() if line]

    def execute_ch_query(self, sql, params, *, settings):
        assert sql.lstrip().startswith(("WITH", "SELECT"))
        self.calls.append((sql, deepcopy(params), dict(settings)))
        return SimpleNamespace(
            data=self.execute(sql, params, settings), read_bytes=self.last_read_bytes
        )

    def insert(self, span, **changes):
        row = {
            "project_id": PROJECT,
            "observation_type": "SPAN",
            "service_name": "",
            "trace_id": "trace",
            "id": span,
            "start_time": START + timedelta(minutes=1),
            "parent_span_id": None,
            "attrs_string": {},
            "attrs_number": {},
            "attrs_bool": {},
            "is_deleted": 0,
            "_version": 1,
            **changes,
        }
        values, params = [], {}
        for index, value in enumerate(row.values()):
            param = f"v{index}"
            if isinstance(value, datetime):
                params[param] = reference.unix_microseconds(value)
                values.append(f"fromUnixTimestamp64Micro(%({param})s, 'UTC')")
            elif isinstance(value, dict):
                pairs = []
                for entry, (key, scalar) in enumerate(value.items()):
                    params[f"{param}_k{entry}"] = key
                    params[f"{param}_v{entry}"] = scalar
                    pairs.extend((f"%({param}_k{entry})s", f"%({param}_v{entry})s"))
                values.append(f"map({', '.join(pairs)})")
            else:
                params[param] = value
                values.append(f"%({param})s")
        self.execute(
            f"INSERT INTO spans ({', '.join(row)}) VALUES ({', '.join(values)})", params
        )
        return row

    def require_unicode(self):
        available = self.execute(
            "SELECT count() AS n FROM system.functions WHERE name='lowerUTF8'"
        )[0]["n"]
        if not available:
            pytest.skip(
                f"CH {self.version} reduced engine lacks lowerUTF8; no ASCII substitution"
            )


@pytest.fixture
def engine(tmp_path, record_property):
    pytest.importorskip("chdb")
    local = LocalRMT(tmp_path / "span-reference-rmt")
    record_property("clickhouse_version", local.version)
    try:
        yield local
    finally:
        local.session.close()


@pytest.mark.parametrize(
    "kind,op,value,expected",
    [
        ("number", "equals", 1, ["one"]),
        ("number", "not_equals", 1, ["zero", "two"]),
        ("number", "in", [0, 1], ["zero", "one"]),
        ("number", "not_in", [0, 1], ["two"]),
        ("number", "greater_than", 1, ["two"]),
        ("number", "greater_than_or_equal", 1, ["two", "one"]),
        ("number", "less_than", 1, ["zero"]),
        ("number", "less_than_or_equal", 1, ["zero", "one"]),
        ("number", "between", [0, 1], ["zero", "one"]),
        ("number", "not_between", [0, 1], ["two"]),
        ("number", "between", [2, 0], []),
        ("number", "not_between", [2, 0], ["zero", "two", "one"]),
        ("number", "is_null", None, ["wrong-type", "missing"]),
        ("number", "is_not_null", None, ["zero", "two", "one"]),
        ("boolean", "equals", False, ["zero", "two"]),
        ("boolean", "not_equals", False, ["one"]),
        ("boolean", "in", [False], ["zero", "two"]),
        ("boolean", "not_in", [False], ["one"]),
        ("boolean", "is_null", None, ["wrong-type", "missing"]),
    ],
)
def test_scalar_truth_tables_on_one_latest_span(engine, kind, op, value, expected):
    for name, number, boolean in (
        ("zero", 0, False),
        ("one", 1, True),
        ("two", 2, False),
    ):
        engine.insert(name, attrs_number={"key": number}, attrs_bool={"key": boolean})
    engine.insert("missing")
    engine.insert("wrong-type", attrs_string={"key": "1"})
    result = reference.reference_span_ids(
        engine, case(), scope(), [leaf(kind, op, value)]
    )
    assert public_ids(result) == expected
    assert result[1]["total_matches"] == (None if expected else 0)
    assert result[1]["population_exhausted"] is True
    hourly = op in {"equals", "in"}
    assert len(engine.calls) == (9 if hourly else 8)
    assert result[1]["population_query_count"] == (8 if hourly else 0)
    assert result[1]["interval_coverage"][-1][
        "start_us"
    ] == reference.unix_microseconds(START)


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("equals", "Alpha", ["a"]),
        ("not_equals", "alpha", ["b"]),
        ("in", ["ALPHA"], ["a"]),
        ("not_in", ["alpha"], ["b"]),
        ("contains", "PH", ["a"]),
        ("not_contains", "ph", ["b"]),
        ("starts_with", "AL", ["a"]),
        ("ends_with", "HA", ["a"]),
        ("equals", "ÉCOLE", ["u"]),
    ],
)
def test_string_scalar_truth_and_unicode(engine, op, value, expected):
    engine.require_unicode()
    engine.insert("a", attrs_string={"key": "aLPHa"})
    engine.insert("b", attrs_string={"key": "Beta"})
    engine.insert("missing")
    if value == "ÉCOLE":
        engine.insert("u", attrs_string={"key": "école"})
    result = reference.reference_span_ids(
        engine, case(), scope(), [leaf("text", op, value)]
    )
    assert public_ids(result) == expected


@pytest.mark.parametrize(
    "types,values,op,expected",
    [
        (["number"], [0], "in", ["both", "conflict", "zero"]),
        (["number"], [0], "not_in", ["other"]),
        (["boolean"], [False], "in", ["both", "false"]),
        (["boolean"], [False], "not_in", ["conflict", "other"]),
        (
            ["number", "boolean"],
            [0, False],
            "in",
            ["both", "conflict", "false", "zero"],
        ),
        (["number", "boolean"], [0, False], "not_in", ["other"]),
        (
            ["string", "number", "boolean"],
            ["0012", 0, False],
            "in",
            ["both", "conflict", "false", "text", "zero"],
        ),
        (["string", "number", "boolean"], ["0012", 0, False], "not_in", ["other"]),
    ],
)
def test_typed_picker_presence_and_negation_on_same_row(
    engine, types, values, op, expected
):
    if "string" in types:
        engine.require_unicode()
    rows = {
        "zero": {"attrs_number": {"key": 0}},
        "false": {"attrs_bool": {"key": False}},
        "text": {"attrs_string": {"key": "0012"}},
        "other": {
            "attrs_number": {"key": 12},
            "attrs_bool": {"key": True},
            "attrs_string": {"key": "other"},
        },
        "both": {"attrs_number": {"key": 0}, "attrs_bool": {"key": False}},
        # A non-selected boolean on this row cannot override its selected zero.
        "conflict": {"attrs_number": {"key": 0}, "attrs_bool": {"key": True}},
        "missing": {},
    }
    for span, values_by_map in rows.items():
        engine.insert(span, **values_by_map)
    result = reference.reference_span_ids(
        engine, case(), scope(), [leaf("text", op, values, types=types)]
    )
    assert public_ids(result) == sorted(expected, reverse=True)


@pytest.mark.parametrize(
    "replacement", ["tombstone", "before-start", "at-end", "property", "missing"]
)
def test_latest_correction_never_resurrects_old_matching_version(engine, replacement):
    end = START + timedelta(minutes=10)
    engine.insert("span", attrs_number={"key": 1})
    changes = {
        "tombstone": {"is_deleted": 1},
        "before-start": {"start_time": START - timedelta(microseconds=1)},
        "at-end": {"start_time": end},
        "property": {"attrs_number": {"key": 2}},
        "missing": {"attrs_number": {}},
    }[replacement]
    engine.insert("span", _version=2, **{"attrs_number": {"key": 1}, **changes})
    result = reference.reference_span_ids(engine, case(end=end), scope(), [leaf()])
    assert result[0] == []
    assert result[1]["total_matches"] == 0


def test_new_winner_enters_window_and_preserves_large_version_and_microseconds(engine):
    engine.insert("span", start_time=START - timedelta(microseconds=1))
    row = engine.insert(
        "span",
        start_time=START,
        attrs_number={"key": 1},
        _version=2**64 - 1,
        observation_type="SPAN",
        service_name="",
    )
    result = reference.reference_span_ids(engine, case(), scope(), [leaf()])
    assert result[0] == [
        [
            PROJECT,
            "trace",
            "span",
            reference.unix_microseconds(
                START.replace(minute=0, second=0, microsecond=0)
            ),
            "SPAN",
            "",
            reference.unix_microseconds(START),
            str(2**64 - 1),
        ]
    ]
    assert row["_version"] == 2**64 - 1


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_all_leaves_require_one_span_and_full_unclipped_window(engine, days, count):
    predicates = [leaf(key=f"k{i}") for i in range(count)]
    engine.insert(
        "old-match", start_time=START, attrs_number={f"k{i}": 1 for i in range(count)}
    )
    engine.insert(
        "recent-empty",
        start_time=START + timedelta(days=days) - timedelta(microseconds=1),
    )
    if count > 1:
        for i in range(count):
            engine.insert(
                f"child-{i}", parent_span_id="root", attrs_number={f"k{i}": 1}
            )
    result = reference.reference_span_ids(engine, case(days=days), scope(), predicates)
    assert public_ids(result) == ["old-match"]
    assert len(final_calls(engine)) == 1  # Recent hour has no mandatory witness.
    assert all(
        params["ref_population_end_us"] - params["ref_population_start_us"]
        <= 7 * reference._DAY_US
        for _, params, _ in engine.calls
        if "ref_population_end_us" in params
    )
    coverage = result[1]["interval_coverage"]
    assert coverage[0]["end_us"] == result[1]["window_end_us"]
    assert coverage[-1]["start_us"] == result[1]["window_start_us"]
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert all(interval["exhausted"] for interval in coverage)
    assert (
        result[1]["window_end_us"] - result[1]["window_start_us"]
        == days * 86400 * 1_000_000
    )


def test_missing_count_if_zero_is_per_span_not_trace(engine):
    engine.insert("present", attrs_number={"key": 0})
    engine.insert("missing", parent_span_id="present")
    result = reference.reference_span_ids(
        engine, case(), scope(), [leaf(op="is_null", value=None)]
    )
    assert public_ids(result) == ["missing"]


def test_full_six_key_collisions_and_deterministic_numbered_pages(engine):
    instant = START + timedelta(minutes=1)
    variations = [
        {"project_id": OTHER},
        {"service_name": "service-z"},
        {"observation_type": "ZZ"},
        {},
        {"trace_id": "trace-z"},
        {"start_time": instant + timedelta(hours=1)},
    ]
    for index, variant in enumerate(variations):
        engine.insert("shared", attrs_number={"key": 1}, _version=index + 1, **variant)
    # A different immutable service is a separate deleted identity, not a
    # tombstone for any of the six live identities above.
    engine.insert("shared", service_name="deleted-service", _version=99, is_deleted=1)
    engine.insert("z-span", attrs_number={"key": 1})
    authorized = {
        "project_ids": [PROJECT, OTHER],
        "authorized_project_ids": [PROJECT, OTHER],
    }
    results = [
        reference.reference_span_ids(
            engine, case(target=2, page=page), authorized, [leaf()]
        )
        for page in range(4)
    ]
    physical = [identity for result in results for identity in result[0]]
    assert [(row[0], row[1], row[2], row[4], row[5], row[7]) for row in physical] == [
        (PROJECT, "trace", "shared", "SPAN", "", "6"),
        (PROJECT, "trace", "z-span", "SPAN", "", "1"),
        (PROJECT, "trace-z", "shared", "SPAN", "", "5"),
        (PROJECT, "trace", "shared", "ZZ", "", "3"),
        (PROJECT, "trace", "shared", "SPAN", "service-z", "2"),
        (PROJECT, "trace", "shared", "SPAN", "", "4"),
        (OTHER, "trace", "shared", "SPAN", "", "1"),
    ]
    assert len({tuple(row[:6]) for row in physical}) == 7
    assert [result[1]["has_more"] for result in results] == [True, True, True, False]
    for page in range(3):
        assert results[page][1]["lookahead_identity"] == results[page + 1][0][0]
    assert all(result[1]["total_matches"] is None for result in results)
    assert all(result[1]["same_transaction_snapshot"] is False for result in results)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_exact_inclusive_start_exclusive_end_and_project_fence(engine, days):
    end = START + timedelta(days=days)
    for span, instant in (
        ("start", START),
        ("before", START - timedelta(microseconds=1)),
        ("last", end - timedelta(microseconds=1)),
        ("end", end),
    ):
        engine.insert(span, start_time=instant, attrs_number={"key": 1})
    engine.insert("outside-project", project_id=OTHER, attrs_number={"key": 1})
    result = reference.reference_span_ids(engine, case(days=days), scope(), [leaf()])
    assert public_ids(result) == ["last", "start"]


def test_raw_date_named_attribute_is_not_consumed_as_window(engine):
    engine.insert("match", attrs_number={"created_at": 1})
    request = case()
    date = {
        "column_id": "start_time",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [request["window"]["start"], request["window"]["end"]],
        },
    }
    assert public_ids(
        reference.reference_span_ids(
            engine, request, scope(), [date, leaf(key="created_at")]
        )
    ) == ["match"]


@pytest.mark.parametrize(
    "surface,target", [("spans", 25), ("task_spans", 1), ("eval_spans", 50)]
)
def test_query_is_full_final_population_with_only_immutable_prewhere(surface, target):
    request = case(surface=surface, target=target)
    predicates = [leaf(op="is_null", value=None)]
    original = deepcopy((request, predicates))
    sql, params = reference.build_span_reference_query(request, scope(), predicates)
    before, after = sql.split("PREWHERE", 1)[1].split("), matching_spans AS", 1)
    assert (
        "is_deleted" not in before and "attrs_" not in before and "LIMIT" not in before
    )
    assert "toStartOfHour" in before and "ref_scan_end_us)s - 1" in before
    assert "FROM spans FINAL" in sql
    for forbidden in (
        "GROUP BY",
        "HAVING",
        "countIf(",
        "count()",
        " OVER ",
        "max(",
        "argMax(",
    ):
        assert forbidden not in sql
    assert "AND (NOT (mapContains(attrs_number, %(ref_key_0)s)))" in after
    assert "service_name, start_time, _version" in after
    assert sql.count("LIMIT") == 1 and sql.index("LIMIT") > sql.index(
        "WHERE is_deleted = 0"
    )
    assert params["ref_limit"] == target + 1
    assert (request, predicates) == original
    for setting in (
        "timeout_overflow_mode",
        "read_overflow_mode",
        "result_overflow_mode",
    ):
        assert reference.SAFE_FINAL_SETTINGS[setting] == "throw"
    assert reference.SAFE_FINAL_SETTINGS["max_execution_time"] == 60
    assert reference.SAFE_FINAL_SETTINGS["max_bytes_to_read"] == 8 * 1024**3
    assert reference.SAFE_FINAL_SETTINGS["max_memory_usage"] == 4 * 1024**3
    source = inspect.getsource(reference)
    assert "from tracer." not in source and "import django" not in source
    assert "candidate_ids" not in params


@pytest.mark.parametrize(
    "op,fragment",
    [
        (
            "equals",
            "lowerUTF8(attrs_string[%(ref_key_0)s]) = lowerUTF8(%(ref_value_0)s)",
        ),
        (
            "not_equals",
            "lowerUTF8(attrs_string[%(ref_key_0)s]) != lowerUTF8(%(ref_value_0)s)",
        ),
        (
            "in",
            "has(arrayMap(x -> lowerUTF8(x), %(ref_value_0)s), lowerUTF8(attrs_string[%(ref_key_0)s]))",
        ),
        (
            "not_in",
            "NOT (has(arrayMap(x -> lowerUTF8(x), %(ref_value_0)s), lowerUTF8(attrs_string[%(ref_key_0)s])))",
        ),
        (
            "contains",
            "position(lowerUTF8(attrs_string[%(ref_key_0)s]), lowerUTF8(%(ref_value_0)s)) > 0",
        ),
        (
            "not_contains",
            "position(lowerUTF8(attrs_string[%(ref_key_0)s]), lowerUTF8(%(ref_value_0)s)) = 0",
        ),
        (
            "starts_with",
            "startsWith(lowerUTF8(attrs_string[%(ref_key_0)s]), lowerUTF8(%(ref_value_0)s))",
        ),
        (
            "ends_with",
            "endsWith(lowerUTF8(attrs_string[%(ref_key_0)s]), lowerUTF8(%(ref_value_0)s))",
        ),
    ],
)
def test_string_sql_contract_without_reduced_engine_substitution(op, fragment):
    value = ["ÉCOLE"] if op in {"in", "not_in"} else "ÉCOLE"
    sql, params = reference.build_span_reference_query(
        case(), scope(), [leaf("text", op, value)]
    )
    assert f"AND ((mapContains(attrs_string, %(ref_key_0)s) AND ({fragment})))" in sql
    assert params["ref_value_0"] == value
    assert "ÉCOLE" not in sql


@pytest.mark.parametrize("op", ["in", "not_in"])
@pytest.mark.parametrize("alias", ["attribute_value_types", "attributeValueTypes"])
def test_typed_picker_sql_preserves_all_domains_on_single_winner(op, alias):
    item = leaf(
        "text",
        op,
        ["0012", "ÉCOLE", 0, False],
        types=["string", "string", "number", "boolean"],
    )
    cfg = item["filter_config"]
    cfg[alias] = cfg.pop("attribute_value_types")
    sql, params = reference.build_span_reference_query(case(), scope(), [item])
    having, _ = reference.membership_having([item], scalar_span=True)
    assert f"AND ({having})" in sql
    assert "countIf(" not in having
    assert having.count("NOT (") == int(op == "not_in")
    for column in ("attrs_string", "attrs_number", "attrs_bool"):
        assert f"mapContains({column}, %(ref_key_0)s)" in having
    assert params["ref_value_0_string"] == ["0012", "école"]
    assert params["ref_value_0_number"] == [0]
    assert type(params["ref_value_0_number"][0]) is int
    assert params["ref_value_0_boolean"] == [False]
    assert type(params["ref_value_0_boolean"][0]) is bool
    assert "toFloat" not in having and "toString" not in having


@pytest.mark.parametrize(
    "change",
    [
        "or",
        "group",
        "native",
        "array",
        "json",
        "empty",
        "eleven",
        "bad-picker",
        "bad-picker-after-anchor",
        "bad-window",
        "custom-sort",
        "scope",
        "cursor",
    ],
)
def test_unsupported_contracts_fail_before_any_read(change):
    request, selected, predicates = case(), scope(), [leaf()]
    if change == "or":
        predicates[0]["operator"] = "OR"
    elif change == "group":
        predicates = [{"filters": predicates}]
    elif change == "native":
        predicates[0]["filter_config"]["col_type"] = "SYSTEM_METRIC"
    elif change in {"array", "json"}:
        predicates[0]["filter_config"]["filter_type"] = change
    elif change == "empty":
        predicates = []
    elif change == "eleven":
        predicates *= 11
    elif change in {"bad-picker", "bad-picker-after-anchor"}:
        predicates = [leaf("text", "in", [1], types=["string"])]
        if change == "bad-picker-after-anchor":
            predicates.insert(0, leaf())
    elif change == "bad-window":
        request["window"]["start"] = START.replace(tzinfo=None).isoformat()
    elif change == "custom-sort":
        request["request"]["params"]["sort_by"] = "cost"
    elif change == "scope":
        selected = {
            "project_ids": [PROJECT, OTHER],
            "authorized_project_ids": [PROJECT],
        }
    elif change == "cursor":
        request["request"]["params"]["cursor"] = "opaque"
    reader = SimpleNamespace(
        execute_ch_query=lambda *_a, **_k: pytest.fail("unexpected read")
    )
    with pytest.raises(ReplayError):
        reference.reference_span_ids(reader, request, selected, predicates)


@pytest.mark.parametrize(
    "exception", [TimeoutError("diagnostic stopped"), RuntimeError("unknown column")]
)
def test_failed_diagnostic_never_becomes_empty_or_exhausted(exception):
    def fail(*_args, **_kwargs):
        raise exception

    with pytest.raises(type(exception), match=str(exception)):
        reference.reference_span_ids(
            SimpleNamespace(execute_ch_query=fail), case(), scope(), [leaf()]
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "incomplete",
        "truncated",
        "duplicate",
        "scope",
        "version",
        "hour",
        "missing-physical-key",
        "reordered",
    ],
)
def test_malformed_partial_or_drifted_result_is_unverified(engine, corruption):
    engine.insert("z", attrs_number={"key": 1})
    engine.insert("a", attrs_number={"key": 1})
    request = case(end=START + timedelta(hours=1))
    sql, params = reference.build_span_reference_query(request, scope(), [leaf()])
    result = engine.execute_ch_query(
        sql, params, settings=reference.SAFE_FINAL_SETTINGS
    )
    if corruption == "incomplete":
        result.complete = False
    elif corruption == "truncated":
        result.data.pop()
        # No global count is requested. The read boundary must attest failure
        # or throw, rather than label a truncated response as complete.
        result.complete = False
    elif corruption == "duplicate":
        result.data[1] = deepcopy(result.data[0])
    elif corruption == "scope":
        result.data[0]["project_id"] = OTHER
    elif corruption == "version":
        result.data[0]["_version"] = None
    elif corruption == "hour":
        result.data[0]["physical_hour_us"] += 1
    elif corruption == "missing-physical-key":
        result.data[0]["observation_type"] = None
    elif corruption == "reordered":
        result.data.reverse()

    def replay(sql, params, *, settings):
        if "ref_population_start_us" in params:
            return engine.execute_ch_query(sql, params, settings=settings)
        return result

    reader = SimpleNamespace(execute_ch_query=replay)
    with pytest.raises(ReplayError, match="INVALID_OR_INCOMPLETE"):
        reference.reference_span_ids(reader, request, scope(), [leaf()])


@pytest.mark.parametrize("target", [1, 25, 50])
def test_full_window_top_n_lookahead_after_membership_without_population_count(
    engine, target
):
    for index in range(55):
        engine.insert(
            f"match-{index:02}",
            start_time=START + timedelta(days=7, minutes=-1),
            attrs_number={"key": 1},
        )
    # Newer rows and superseded matches cannot consume the limited prefix.
    newest = START + timedelta(days=7, seconds=-1)
    engine.insert("newer-nonmatch", start_time=newest, attrs_number={"key": 2})
    engine.insert("newer-deleted", start_time=newest, attrs_number={"key": 1})
    engine.insert(
        "newer-deleted",
        start_time=newest,
        attrs_number={"key": 1},
        _version=2,
        is_deleted=1,
    )
    result = reference.reference_span_ids(
        engine, case(target=target), scope(), [leaf()]
    )
    assert public_ids(result) == [
        f"match-{index:02}" for index in range(54, 54 - target, -1)
    ]
    evidence = result[1]
    assert evidence["lookahead_identity"][2] == f"match-{54 - target:02}"
    assert evidence["total_matches"] is None
    assert evidence["has_more"] and evidence["ordered_prefix_complete"]
    assert (
        not evidence["population_exhausted"]
        and not evidence["complete_population_read"]
    )
    assert not evidence["http_e2e"] and not evidence["metrics_verified"]
    assert len(engine.calls) == 2 and len(final_calls(engine)) == 1
    assert all(
        "GROUP BY" not in sql and "count()" not in sql for sql, _, _ in engine.calls
    )


def test_later_empty_page_does_not_claim_globally_empty_population(engine):
    engine.insert("match", attrs_number={"key": 1})
    result = reference.reference_span_ids(
        engine, case(target=1, page=2), scope(), [leaf()]
    )
    assert result[0] == []
    assert result[1]["total_matches"] is None
    assert result[1]["population_exhausted"]
    assert not result[1]["complete_population_read"]
    assert result[1]["lookahead_identity"] is None


@pytest.mark.parametrize(
    "predicate",
    [
        leaf(op="equals", value=1),
        leaf(op="not_in", value=[0, 1]),
        leaf(op="is_null", value=None),
        leaf(op="not_between", value=[0, 1]),
        leaf("boolean", "equals", False),
        leaf("boolean", "is_null", None),
        leaf("text", "in", [0, False], types=["number", "boolean"]),
        leaf("text", "not_in", [0, False], types=["number", "boolean"]),
    ],
)
def test_scalar_final_predicate_matches_former_one_row_group_semantics(
    engine, predicate
):
    engine.insert("zero", attrs_number={"key": 0}, attrs_bool={"key": False})
    engine.insert("one", attrs_number={"key": 1}, attrs_bool={"key": True})
    engine.insert("other", attrs_number={"key": 2}, attrs_bool={"key": True})
    engine.insert("missing")
    engine.insert("corrected", attrs_number={"key": 0})
    engine.insert("corrected", _version=2)
    engine.insert("dead", attrs_number={"key": 2})
    engine.insert("dead", attrs_number={"key": 2}, _version=2, is_deleted=1)
    having, params = reference.membership_having([predicate])
    # Test-only historical oracle. All fixtures are in-window and project;
    # every aggregation key is the complete immutable replacement identity.
    grouped = engine.execute(
        f"""
        SELECT id FROM spans FINAL WHERE is_deleted=0
        GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id
        HAVING {having}
        ORDER BY max(start_time) DESC, id DESC
    """,
        params,
        reference.SAFE_FINAL_SETTINGS,
    )
    result = reference.reference_span_ids(engine, case(), scope(), [predicate])
    assert public_ids(result) == [row["id"] for row in grouped]
    assert result[1]["total_matches"] == (None if grouped else 0)


@pytest.mark.parametrize("target", [1, 50])
def test_raw_hour_discovery_finds_prefix_without_daily_value_scans(engine, target):
    end = START + timedelta(days=30)
    for index in range(target + 1):
        engine.insert(
            f"hit-{index:02}",
            start_time=end - timedelta(days=2),
            attrs_number={"key": 1},
        )
    result = reference.reference_span_ids(
        engine, case(days=30, target=target), scope(), [leaf()]
    )
    assert public_ids(result) == [f"hit-{index:02}" for index in range(target, 0, -1)]
    assert result[1]["lookahead_identity"][2] == "hit-00"
    coverage = result[1]["interval_coverage"]
    assert len(coverage) == 4
    assert all(part["kind"] == "numeric_witness_empty" for part in coverage[:3])
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert coverage[-1]["kind"] == "exact_hour_page"
    assert coverage[-1]["end_us"] - coverage[-1]["start_us"] == reference._HOUR_US
    assert len(engine.calls) == 4 and len(final_calls(engine)) == 1
    assert not result[1]["population_exhausted"]


@pytest.mark.parametrize("days", [7, 30, 365])
def test_repeated_hits_probe_only_remaining_current_utc_day(engine, days):
    end = START + timedelta(days=days)
    for hours in (1, 2, 3):
        engine.insert(
            f"hit-{hours}",
            start_time=end - timedelta(hours=hours),
            attrs_number={"key": 1},
        )
    request = case(end=end, target=2)
    result = reference.reference_span_ids(engine, request, scope(), [leaf()])
    truth = full_window_truth(engine, request, scope(), [leaf()])
    assert public_ids(result) == ["hit-1", "hit-2"]
    assert result[0] == truth[:2] and result[1]["lookahead_identity"] == truth[2]
    assert len(engine.calls) == 6 and len(final_calls(engine)) == 3
    day = reference.unix_microseconds(
        end.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    probes = [
        params for _, params, _ in engine.calls if "ref_population_end_us" in params
    ]
    assert [params["ref_population_start_us"] for params in probes] == [day] * 3
    assert [params["ref_population_end_us"] for params in probes] == [
        day + hours * reference._HOUR_US for hours in (13, 11, 10)
    ]
    assert result[1]["reference_route"].endswith(".v2")
    assert (
        result[1]["ordered_prefix_complete"] and not result[1]["population_exhausted"]
    )


@pytest.mark.parametrize("hit_state", ["match", "wrong-value", "tombstone"])
def test_weekly_population_hit_resets_to_days_even_when_final_rejects(
    engine, hit_state
):
    end = START + timedelta(days=30)
    hit_time = end - timedelta(days=10)
    engine.insert("week-hit", start_time=hit_time, attrs_number={"key": 1})
    if hit_state != "match":
        engine.insert(
            "week-hit",
            start_time=hit_time,
            attrs_number={"key": 0 if hit_state == "wrong-value" else 1},
            is_deleted=int(hit_state == "tombstone"),
            _version=2,
        )
    engine.insert(
        "older-match", start_time=end - timedelta(days=20), attrs_number={"key": 1}
    )
    request = case(end=end)
    result = reference.reference_span_ids(engine, request, scope(), [leaf()])
    assert public_ids(result) == (
        ["week-hit", "older-match"] if hit_state == "match" else ["older-match"]
    )
    assert result[0] == full_window_truth(engine, request, scope(), [leaf()])
    probes = [
        params for _, params, _ in engine.calls if "ref_population_end_us" in params
    ]
    day = reference.unix_microseconds(
        hit_time.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    # Partial newest day + seven whole empty days, then a weekly hit. A hit
    # resets both the width and the empty-day count regardless of membership.
    assert (
        probes[8]["ref_population_end_us"] - probes[8]["ref_population_start_us"]
        == 7 * reference._DAY_US
    )
    assert probes[9]["ref_population_start_us"] == day
    assert probes[9]["ref_population_end_us"] == day + 12 * reference._HOUR_US
    assert [
        (probe["ref_population_start_us"], probe["ref_population_end_us"])
        for probe in probes[10:17]
    ] == [
        (day - days * reference._DAY_US, day - (days - 1) * reference._DAY_US)
        for days in range(1, 8)
    ]
    assert (
        probes[17]["ref_population_end_us"] - probes[17]["ref_population_start_us"]
        == 7 * reference._DAY_US
    )
    exact = [
        part
        for part in result[1]["interval_coverage"]
        if part["kind"] == "exact_hour_page"
    ]
    assert [part["rows"] for part in exact] == [int(hit_state == "match"), 1]
    coverage = result[1]["interval_coverage"]
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert all(part["exhausted"] for part in coverage)
    assert coverage[-1]["start_us"] == reference.unix_microseconds(START)
    assert result[1]["population_exhausted"]


def test_global_offset_consumed_once_across_utc_intervals(engine):
    end = START + timedelta(days=3)
    for day in (1, 2, 3):
        for suffix in ("a", "z"):
            engine.insert(
                f"d{day}-{suffix}",
                start_time=START + timedelta(days=day, hours=-1),
                attrs_number={"key": 1},
            )
    result = reference.reference_span_ids(
        engine, case(end=end, target=2, page=1), scope(), [leaf()]
    )
    assert public_ids(result) == ["d2-z", "d2-a"]
    assert result[1]["lookahead_identity"][2] == "d1-z"
    assert result[1]["page_number"] == 1 and result[1]["total_matches"] is None
    assert all(params["ref_offset"] == 0 for _, params, _ in final_calls(engine))
    assert all(
        params["ref_start_us"] == reference.unix_microseconds(START)
        for _, params, _ in final_calls(engine)
    )
    assert result[1]["has_more"] and not result[1]["population_exhausted"]


def test_large_offset_keysets_oracle_own_full_tie_without_identity_collapse(engine):
    end = START + timedelta(hours=1)
    for index in range(125):
        engine.insert(
            "same-id", service_name=f"svc-{index:03}", attrs_number={"key": 1}
        )
    result = reference.reference_span_ids(
        engine, case(end=end, target=2, page=55), scope(), [leaf()]
    )
    assert [row[5] for row in result[0]] == ["svc-014", "svc-013"]
    assert result[1]["lookahead_identity"][5] == "svc-012"
    assert len(engine.calls) == 3
    assert "newest_raw_hour_us" in engine.calls[0][0]
    first, second = [params for _, params, _ in final_calls(engine)]
    assert first["ref_limit"] == 101 and second["ref_limit"] == 12
    assert first["ref_has_before"] == 0 and second["ref_has_before"] == 1
    assert second["ref_before_order"] == (
        reference.unix_microseconds(START + timedelta(minutes=1)),
        "same-id",
        "trace",
        PROJECT,
        "SPAN",
        "svc-024",
    )
    assert second["ref_scan_start_us"] == first["ref_scan_start_us"]
    assert second["ref_scan_end_us"] == first["ref_scan_end_us"]
    assert second["ref_offset"] == first["ref_offset"] == 0
    assert all(settings["max_result_rows"] == 101 for _, _, settings in engine.calls)


@pytest.mark.parametrize("numeric_anchor", [False, True])
def test_numeric_witness_skips_filter_empty_hours_but_no_anchor_still_classifies(
    engine,
    numeric_anchor,
):
    # No-anchor discovery retains the physical-population contract. Numeric
    # discovery may skip a physically populated hour only by its own proof.
    end = START + timedelta(days=30)
    engine.insert(
        "old-match",
        start_time=end - timedelta(days=10),
        attrs_number={"key": 1},
        attrs_bool={"flag": 0},
    )
    engine.insert(
        "recent-key-only",
        start_time=end - timedelta(days=1),
        attrs_number={"key": 0},
        attrs_bool={"flag": 1},
    )
    filters = (
        [leaf()] if numeric_anchor else [leaf("boolean", "equals", False, key="flag")]
    )
    result = reference.reference_span_ids(engine, case(end=end), scope(), filters)
    assert public_ids(result) == ["old-match"]
    coverage = result[1]["interval_coverage"]
    exact = [part for part in coverage if part["kind"] == "exact_hour_page"]
    assert [part["rows"] for part in exact] == ([1] if numeric_anchor else [0, 1])
    assert all(
        part["end_us"] - part["start_us"] == reference._HOUR_US for part in exact
    )
    assert len(final_calls(engine)) == (1 if numeric_anchor else 2)
    assert all(
        params["ref_population_end_us"] - params["ref_population_start_us"]
        <= 7 * reference._DAY_US
        for _, params, _ in engine.calls
        if "ref_population_end_us" in params
    )
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert coverage[-1]["start_us"] == reference.unix_microseconds(START)
    assert all(part["exhausted"] for part in coverage)
    assert result[1]["population_exhausted"] and result[1]["complete_population_read"]


def test_empty_year_requires_adjacent_full_history_not_recent_daily_absence(engine):
    result = reference.reference_span_ids(engine, case(days=365), scope(), [leaf()])
    assert result[0] == [] and result[1]["total_matches"] == 0
    coverage = result[1]["interval_coverage"]
    assert coverage[0]["end_us"] == reference.unix_microseconds(
        START + timedelta(days=365)
    )
    assert coverage[-1]["start_us"] == reference.unix_microseconds(START)
    assert (
        sum(part["end_us"] - part["start_us"] for part in coverage)
        == 365 * reference._DAY_US
    )
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert all(
        part["exhausted"] and part["numeric_witness_rows"] == 0 for part in coverage
    )
    assert len(coverage) == 60 and result[1]["complete_population_read"]
    assert all(
        part["kind"] == "numeric_witness_empty" and "rows" not in part
        for part in coverage
    )
    assert len(engine.calls) == 60 and not final_calls(engine)
    assert result[1]["population_query_count"] == 60
    assert "maxOrNull" in engine.calls[0][0]


class ProgressReader:
    """Same successful-byte ledger shape as the runner, with no DB/network."""

    def __init__(
        self,
        amounts=(),
        *,
        clock=None,
        elapsed=(),
        progress=True,
        outer_ms=None,
        results=None,
        population_empty=False,
    ):
        self.calls = []
        self.amounts, self.clock, self.elapsed = amounts, clock, elapsed
        self.progress, self.outer_ms = progress, outer_ms
        self.results = results
        self.population_empty = population_empty

    def remaining_read_ms(self):
        return self.outer_ms() if self.outer_ms else 60000

    def execute_ch_query(self, sql, params, *, settings):
        index = len(self.calls)
        record = {"sql": sql, "params": deepcopy(params), "limits": dict(settings)}
        if self.progress:
            record["read_bytes"] = (
                self.amounts[index] if index < len(self.amounts) else 0
            )
        self.calls.append(record)
        if self.clock is not None:
            self.clock[0] += self.elapsed[index] if index < len(self.elapsed) else 0
        if self.results is not None and index < len(self.results):
            return self.results[index]
        data = (
            [
                {
                    "newest_raw_hour_us": (
                        None
                        if self.population_empty
                        else params["ref_population_end_us"] - reference._HOUR_US
                    )
                }
            ]
            if "ref_population_end_us" in params
            else []
        )
        return SimpleNamespace(data=data)


@pytest.mark.parametrize("end_at_midnight", [False, True])
@pytest.mark.parametrize("numeric_anchor", [False, True])
def test_only_seven_completed_empty_proofs_allow_adjacent_week_probe(
    end_at_midnight, numeric_anchor
):
    end = START + timedelta(days=30)
    if end_at_midnight:
        end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    reader = ProgressReader(population_empty=True)
    rows, evidence = reference.reference_span_ids(
        reader,
        case(end=end),
        scope(),
        [leaf()] if numeric_anchor else [leaf("boolean", "equals", False)],
    )
    assert rows == [] and evidence["total_matches"] == 0
    probes = [call["params"] for call in reader.calls]
    first_week = 7 if end_at_midnight else 8
    assert all(
        params["ref_population_end_us"] - params["ref_population_start_us"]
        <= reference._DAY_US
        for params in probes[:first_week]
    )
    assert (
        probes[first_week]["ref_population_end_us"]
        - probes[first_week]["ref_population_start_us"]
        == 7 * reference._DAY_US
    )
    assert all(
        newer["ref_population_start_us"] == older["ref_population_end_us"]
        for newer, older in zip(probes, probes[1:])
    )
    coverage = evidence["interval_coverage"]
    assert coverage[0]["end_us"] == reference.unix_microseconds(end)
    assert coverage[-1]["start_us"] == reference.unix_microseconds(START)
    assert all(
        part["kind"]
        == ("numeric_witness_empty" if numeric_anchor else "raw_population_empty")
        and part["exhausted"]
        for part in coverage
    )
    assert sum(
        part["end_us"] - part["start_us"] for part in coverage
    ) == reference.unix_microseconds(end) - reference.unix_microseconds(START)
    assert evidence["population_exhausted"] and evidence["complete_population_read"]


def test_partial_day_null_at_total_byte_budget_cannot_claim_older_history_empty():
    reader = ProgressReader([8 * 1024**3], population_empty=True)
    with pytest.raises(ReplayError, match="TOTAL_READ_BYTES_EXCEEDED"):
        reference.reference_span_ids(reader, case(days=365), scope(), [leaf()])
    assert len(reader.calls) == 1
    params = reader.calls[0]["params"]
    assert params["ref_population_start_us"] > reference.unix_microseconds(START)
    assert (
        params["ref_population_end_us"] - params["ref_population_start_us"]
        < reference._DAY_US
    )


def test_empty_days_and_week_probes_share_wall_bytes_and_query_count(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(reference, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reference, "MAX_REFERENCE_QUERIES", 9)
    reader = ProgressReader(
        [1024**2] * 9, clock=clock, elapsed=[1] * 9, population_empty=True
    )
    with pytest.raises(ReplayError, match="TOTAL_QUERY_BUDGET_EXCEEDED"):
        reference.reference_span_ids(reader, case(days=365), scope(), [leaf()])
    assert len(reader.calls) == 9
    assert [call["limits"]["max_execution_time"] for call in reader.calls] == list(
        range(60, 51, -1)
    )
    assert [call["limits"]["max_bytes_to_read"] for call in reader.calls] == [
        8 * 1024**3 - index * 1024**2 for index in range(9)
    ]
    last = reader.calls[-1]["params"]
    assert (
        last["ref_population_end_us"] - last["ref_population_start_us"]
        == 7 * reference._DAY_US
    )


def test_read_byte_allowance_debits_runner_ledger_without_reset():
    gib = 1024**3
    reader = ProgressReader([5 * gib, 4 * gib])
    with pytest.raises(ReplayError, match="TOTAL_READ_BYTES_EXCEEDED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert [call["limits"]["max_bytes_to_read"] for call in reader.calls] == [
        8 * gib,
        3 * gib,
    ]


def test_exactly_consumed_byte_budget_cannot_start_next_interval():
    reader = ProgressReader([8 * 1024**3])
    with pytest.raises(ReplayError, match="TOTAL_READ_BYTES_EXCEEDED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert len(reader.calls) == 1


def test_missing_byte_evidence_fails_closed_before_next_read():
    reader = ProgressReader(progress=False)
    with pytest.raises(ReplayError, match="READ_PROGRESS_REQUIRED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert len(reader.calls) == 1


def test_shared_wall_does_not_refresh_and_rejects_late_empty_result(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(reference, "monotonic", lambda: clock[0])
    reader = ProgressReader(clock=clock, elapsed=[35, 26])
    with pytest.raises(ReplayError, match="TOTAL_WALL_EXCEEDED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert [call["limits"]["max_execution_time"] for call in reader.calls] == [60, 25]


@pytest.mark.parametrize("remaining,elapsed,queries", [(0, 0, 0), (20000, 21, 1)])
def test_outer_deadline_remains_tighter_than_local_budget(
    monkeypatch, remaining, elapsed, queries
):
    clock = [0.0]
    monkeypatch.setattr(reference, "monotonic", lambda: clock[0])
    reader = ProgressReader(
        clock=clock,
        elapsed=[elapsed],
        outer_ms=lambda: max(0, remaining - clock[0] * 1000),
    )
    with pytest.raises(ReplayError, match="TOTAL_WALL_EXCEEDED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert len(reader.calls) == queries
    if queries:
        assert reader.calls[0]["limits"]["max_execution_time"] == 20


def test_total_statement_guard_is_unverified_not_history_cutoff(monkeypatch):
    monkeypatch.setattr(reference, "MAX_REFERENCE_QUERIES", 2)
    reader = ProgressReader()
    with pytest.raises(ReplayError, match="TOTAL_QUERY_BUDGET_EXCEEDED"):
        reference.reference_span_ids(reader, case(days=365), scope(), [leaf()])
    assert len(reader.calls) == 2
    assert reader.calls[-1]["params"][
        "ref_scan_start_us"
    ] > reference.unix_microseconds(START)


def test_failure_after_newer_matches_does_not_return_partial_reference(engine):
    end = START + timedelta(days=7)
    engine.insert(
        "newer-match", start_time=end - timedelta(minutes=1), attrs_number={"key": 1}
    )

    class FailThirdRead:
        def __init__(self):
            self.reads = 0

        def execute_ch_query(self, sql, params, *, settings):
            self.reads += 1
            if self.reads == 3:
                raise TimeoutError("reference interval incomplete")
            return engine.execute_ch_query(sql, params, settings=settings)

    reader = FailThirdRead()
    with pytest.raises(TimeoutError, match="interval incomplete"):
        reference.reference_span_ids(reader, case(end=end), scope(), [leaf()])
    assert reader.reads == 3


@pytest.mark.parametrize(
    "filters",
    [
        [leaf(), leaf("boolean", "equals", False, key="flag")],
        [leaf(op="not_equals", value=1)],
        [leaf("text", "not_in", [1, False], types=["number", "boolean"])],
        [leaf(op="is_null", value=None), leaf(op="greater_than", value=0, key="other")],
        [leaf(op="is_null", value=None)],
    ],
)
def test_shape_dispatch_never_pushes_mutable_values_into_final(filters):
    sql, params = reference.build_span_reference_query(case(), scope(), filters)
    assert reference.SAFE_FINAL_SETTINGS["set_overflow_mode"] == "throw"
    assert reference.SAFE_FINAL_SETTINGS["distinct_overflow_mode"] == "throw"
    exact, _ = reference.membership_having(filters, scalar_span=True)
    assert f"AND ({exact})" in sql.split("matching_spans AS (", 1)[1]
    expected_hourly = filters[0]["filter_config"]["filter_op"] == "equals"
    assert params["ref_hourly_discovery"] is expected_hourly
    if expected_hourly:
        assert "raw_witness_prefixes" not in sql and "SELECT DISTINCT" not in sql
        assert sql.count("FROM spans") == 1
    else:
        raw_presence, raw_value, proof = reference._raw_prefix_proof(filters, exact)
        assert params["ref_prefix_proof"] == proof
        if raw_presence:
            raw = sql.split("physical_winners AS (", 1)[0]
            assert f"AND ({raw_presence})" in raw
            assert (
                (f"WHERE ({raw_value})" in raw)
                if raw_value
                else "ref_value_" not in raw
            )
            assert "LIMIT" not in raw and "ref_before" not in raw
        else:
            assert "raw_witness_prefixes" not in sql
    final_prewhere = (
        sql.split("physical_winners AS (", 1)[1]
        .split("PREWHERE", 1)[1]
        .split("), matching_spans AS", 1)[0]
    )
    assert "ref_scan_end_us)s - 1" in final_prewhere
    for forbidden in (
        "LIMIT",
        "OFFSET",
        "ref_before",
        "ref_start_us",
        "is_deleted",
        "_version",
        "SAMPLE",
        "attrs_",
        "ref_value_",
        "ref_key_",
    ):
        assert forbidden not in final_prewhere
    assert "ref_before_order" in params


@pytest.mark.parametrize(
    "filters,expected",
    [
        (
            [
                leaf(op="greater_than", value=0),
                leaf("boolean", "equals", False, key="flag"),
            ],
            ["a"],
        ),
        (
            [
                leaf(op="not_between", value=[1, 3]),
                leaf("boolean", "equals", False, key="flag"),
            ],
            ["stale", "b"],
        ),
        (
            [
                leaf("text", "not_in", [1, True], types=["number", "boolean"]),
                leaf("boolean", "equals", False, key="flag"),
            ],
            ["stale", "d", "b"],
        ),
        (
            [
                leaf(op="is_null", value=None),
                leaf("boolean", "is_not_null", None, key="flag"),
            ],
            ["wrong", "removed", "d"],
        ),
        ([leaf(op="is_null", value=None)], ["wrong", "removed", "missing", "d"]),
    ],
)
def test_hourly_conjunctions_equal_literal_and_full_window_latest_truth(
    engine, filters, expected
):
    engine.insert("a", attrs_number={"key": 1}, attrs_bool={"flag": False})
    engine.insert("b", attrs_number={"key": 0}, attrs_bool={"flag": False})
    engine.insert("c", attrs_number={"key": 1}, attrs_bool={"flag": True})
    engine.insert("d", attrs_bool={"key": False, "flag": False})
    engine.insert("missing")
    engine.insert("wrong", attrs_string={"key": "1"}, attrs_bool={"flag": False})
    for name in ("stale", "deleted", "removed"):
        engine.insert(name, attrs_number={"key": 1}, attrs_bool={"flag": False})
    engine.insert(
        "stale", attrs_number={"key": 0}, attrs_bool={"flag": False}, _version=2
    )
    engine.insert(
        "deleted",
        attrs_number={"key": 1},
        attrs_bool={"flag": False},
        _version=2,
        is_deleted=1,
    )
    engine.insert("removed", attrs_bool={"flag": False}, _version=2)
    request = case(end=START + timedelta(hours=1))
    actual = reference.reference_span_ids(engine, request, scope(), filters)
    assert public_ids(actual) == expected
    assert actual[1]["reference_route"].endswith(".v1")
    assert actual[0] == full_window_truth(engine, request, scope(), filters)
    assert actual[1]["population_exhausted"] and not actual[1]["has_more"]


def test_hourly_discovery_keeps_complete_hour_corrections_and_cross_project_collisions(
    engine,
):
    end = START + timedelta(minutes=10)
    for name in ("moved-out", "cleared", "dead"):
        engine.insert(name, attrs_number={"key": 1})
    engine.insert("moved-out", start_time=end, attrs_number={"key": 1}, _version=2)
    engine.insert("cleared", _version=2)
    engine.insert("dead", attrs_number={"key": 1}, is_deleted=1, _version=2)
    engine.insert(
        "moved-in",
        start_time=START - timedelta(microseconds=1),
        attrs_number={"key": 2},
    )
    engine.insert(
        "moved-in",
        start_time=START + timedelta(microseconds=1),
        attrs_number={"key": 1},
        _version=2,
    )
    for project, observation, service in (
        (PROJECT, "SPAN", ""),
        (OTHER, "SPAN", ""),
        (PROJECT, "OTHER", ""),
        (PROJECT, "SPAN", "other-service"),
    ):
        engine.insert(
            "same-id",
            project_id=project,
            observation_type=observation,
            service_name=service,
            attrs_number={"key": 1},
        )
    projects = {
        "project_ids": [PROJECT, OTHER],
        "authorized_project_ids": [PROJECT, OTHER],
    }
    request = case(end=end, target=2)
    actual = reference.reference_span_ids(engine, request, projects, [leaf()])
    assert len(actual[0]) == 2 and actual[1]["has_more"]
    assert public_ids(actual) == ["same-id", "same-id"]
    unpruned = full_window_truth(engine, request, projects, [leaf()])
    assert actual[0] == unpruned[:2]
    assert actual[1]["lookahead_identity"] == unpruned[2]
    remainder = reference.reference_span_ids(
        engine, case(end=end, target=2, page=2), projects, [leaf()]
    )
    assert public_ids(remainder) == ["moved-in"]
    assert remainder[0][0][-1] == "2"
    assert remainder[1]["population_exhausted"]


@pytest.mark.parametrize("ceiling", ["max_bytes_to_read", "max_memory_usage"])
def test_single_hot_hour_guard_throws_instead_of_proving_false_exhaustion(
    engine, ceiling
):
    for index in range(3):
        engine.insert(str(index), service_name=str(index), attrs_number={"key": 1})

    class TinyHourReader:
        def execute_ch_query(self, sql, params, *, settings):
            assert settings["set_overflow_mode"] == "throw"
            assert settings["distinct_overflow_mode"] == "throw"
            if "FROM spans FINAL" in sql:
                settings = {**settings, ceiling: 1}
                if ceiling == "max_memory_usage":
                    # CH buffers small allocations before charging its query
                    # tracker. Force immediate accounting for this tiny local
                    # failure fixture; production/reference settings stay fixed.
                    settings["max_untracked_memory"] = 1
            return engine.execute_ch_query(sql, params, settings=settings)

    with pytest.raises(RuntimeError, match="(?i)(limit|size|memory|bytes)"):
        reference.reference_span_ids(
            TinyHourReader(), case(end=START + timedelta(hours=1)), scope(), [leaf()]
        )


def test_sparse_hour_matches_full_window_and_records_embedded_byte_counters(
    engine, record_property
):
    # A wide numeric Map on a disjoint immutable prefix; no production data or
    # app index expressions are used. Record the embedded counters without
    # treating them as independent production scan-volume/performance proof.
    engine.execute(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id, attrs_number, _version)
        SELECT toUUID(%(project)s), 'SPAN', 'irrelevant',
               fromUnixTimestamp64Micro(%(time)s, 'UTC'), 'trace', toString(number),
               mapFromArrays(arrayMap(x -> concat('other-', toString(x)), range(64)),
                             arrayMap(x -> toFloat64(number + x), range(64))), 1
        FROM numbers(16384)""",
        {
            "project": PROJECT,
            "time": reference.unix_microseconds(START + timedelta(minutes=1)),
        },
    )
    engine.insert("wanted", service_name="wanted", attrs_number={"key": 1})
    request = case(end=START + timedelta(hours=1))
    actual = reference.reference_span_ids(engine, request, scope(), [leaf()])
    unpruned = full_window_truth(engine, request, scope(), [leaf()])
    assert public_ids(actual) == ["wanted"] and actual[0] == unpruned
    measured = {
        "hourly_bytes": actual[1]["reference_read_bytes"],
        "full_window_bytes": engine.last_read_bytes,
    }
    record_property("embedded_byte_counters_not_production_read_proof", measured)
    assert measured["hourly_bytes"] > 0 and measured["full_window_bytes"] > 0


def test_population_sql_uses_only_authorized_project_and_complete_raw_hours():
    reader = ProgressReader(population_empty=True)
    request = case(end=START + timedelta(hours=1))
    result = reference.reference_span_ids(
        reader, request, scope(), [leaf("boolean", "equals", False)]
    )
    assert result[0] == [] and result[1]["total_matches"] == 0
    assert len(reader.calls) == 1 and result[1]["population_exhausted"]
    call = reader.calls[0]
    sql, params = call["sql"], call["params"]
    assert "maxOrNull" in sql and "toStartOfHour(start_time)" in sql
    assert "project_id IN %(ref_project_ids)s" in sql
    assert set(params) == {
        "ref_project_ids",
        "ref_population_start_us",
        "ref_population_end_us",
    }
    assert params["ref_project_ids"] == (PROJECT,)
    start, end = (
        reference.unix_microseconds(datetime.fromisoformat(request["window"][key]))
        for key in ("start", "end")
    )
    assert (
        params["ref_population_start_us"]
        == start // reference._HOUR_US * reference._HOUR_US
    )
    assert (
        params["ref_population_end_us"]
        == ((end - 1) // reference._HOUR_US + 1) * reference._HOUR_US
    )
    for forbidden in (
        "FINAL",
        "attrs_",
        "ref_key_",
        "ref_value_",
        "is_deleted",
        "_version",
        "trace_id",
        " LIMIT ",
        "SAMPLE",
        "DISTINCT",
    ):
        assert forbidden not in sql
    assert result[1]["interval_coverage"] == [
        {
            "kind": "raw_population_empty",
            "start_us": start,
            "end_us": end,
            "rows": 0,
            "exhausted": True,
        }
    ]


@pytest.mark.parametrize(
    "data,complete",
    [
        ([], True),
        (None, True),
        ([{}], True),
        ([None], True),
        ([{"newest_raw_hour_us": None}, {"newest_raw_hour_us": None}], True),
        ([{"newest_raw_hour_us": None}], False),
        ([{"newest_raw_hour_us": True}], True),
        ([{"newest_raw_hour_us": "0"}], True),
        ([{"newest_raw_hour_us": 0.0}], True),
        ([{"newest_raw_hour_us": reference.unix_microseconds(START)}], True),
        (
            [
                {
                    "newest_raw_hour_us": reference.unix_microseconds(
                        START.replace(minute=0, second=0, microsecond=0)
                    )
                    - reference._HOUR_US
                }
            ],
            True,
        ),
        (
            [
                {
                    "newest_raw_hour_us": reference.unix_microseconds(
                        (START + timedelta(days=7, hours=1)).replace(
                            minute=0, second=0, microsecond=0
                        )
                    )
                }
            ],
            True,
        ),
    ],
)
def test_malformed_population_never_becomes_hour_or_empty_proof(data, complete):
    reader = ProgressReader(results=[SimpleNamespace(data=data, complete=complete)])
    with pytest.raises(ReplayError, match="POPULATION_INVALID_OR_INCOMPLETE"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert len(reader.calls) == 1


@pytest.mark.parametrize(
    "hour",
    [
        None,
        reference.unix_microseconds(START.replace(minute=0, second=0, microsecond=0)),
    ],
)
def test_late_population_null_or_hit_cannot_advance_coverage(monkeypatch, hour):
    clock = [0.0]
    monkeypatch.setattr(reference, "monotonic", lambda: clock[0])
    reader = ProgressReader(
        clock=clock,
        elapsed=[61],
        results=[SimpleNamespace(data=[{"newest_raw_hour_us": hour}])],
    )
    with pytest.raises(ReplayError, match="TOTAL_WALL_EXCEEDED"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert len(reader.calls) == 1


def test_complete_empty_proof_at_exact_total_byte_budget_needs_no_new_statement():
    reader = ProgressReader([8 * 1024**3], population_empty=True)
    rows, evidence = reference.reference_span_ids(
        reader, case(end=START + timedelta(hours=1)), scope(), [leaf()]
    )
    assert rows == [] and evidence["total_matches"] == 0
    assert evidence["reference_read_bytes"] == 8 * 1024**3
    assert evidence["reference_query_count"] == 1 and evidence["population_exhausted"]


def test_tombstone_only_hour_is_replayed_not_mistaken_for_physical_absence(engine):
    dead_time = START + timedelta(days=2)
    engine.insert("dead", start_time=dead_time, attrs_number={"key": 1})
    engine.insert(
        "dead", start_time=dead_time, attrs_number={"key": 1}, _version=2, is_deleted=1
    )
    engine.insert("old", attrs_number={"key": 1})
    result = reference.reference_span_ids(engine, case(), scope(), [leaf()])
    assert public_ids(result) == ["old"]
    exact = [
        part
        for part in result[1]["interval_coverage"]
        if part["kind"] == "exact_hour_page"
    ]
    assert [part["rows"] for part in exact] == [0, 1]
    assert len(final_calls(engine)) == 2
    assert all(part["exhausted"] for part in result[1]["interval_coverage"])


def test_utc_midnight_boundaries_replay_each_physical_hour_once(engine):
    midnight = START.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
    start, end = (
        midnight - timedelta(seconds=2, microseconds=345678),
        midnight + timedelta(seconds=3, microseconds=123456),
    )
    request = case(end=end)
    request["window"]["start"] = start.isoformat()
    engine.insert(
        "entered", start_time=start - timedelta(microseconds=1), attrs_number={"key": 0}
    )
    engine.insert("entered", start_time=start, attrs_number={"key": 1}, _version=2)
    engine.insert(
        "same", start_time=start + timedelta(microseconds=1), attrs_number={"key": 1}
    )
    engine.insert(
        "same", start_time=midnight + timedelta(microseconds=1), attrs_number={"key": 1}
    )
    engine.insert("left", start_time=midnight, attrs_number={"key": 1})
    engine.insert("left", start_time=end, attrs_number={"key": 1}, _version=2)
    engine.insert(
        "before", start_time=start - timedelta(microseconds=1), attrs_number={"key": 1}
    )
    engine.insert(
        "outside-project",
        project_id=OTHER,
        start_time=midnight,
        attrs_number={"key": 1},
    )
    result = reference.reference_span_ids(engine, request, scope(), [leaf()])
    assert public_ids(result) == ["same", "same", "entered"]
    assert result[0] == full_window_truth(engine, request, scope(), [leaf()])
    assert len(engine.calls) == 4 and len(final_calls(engine)) == 2
    assert result[1]["population_exhausted"]
    coverage = result[1]["interval_coverage"]
    assert [part["kind"] for part in coverage] == ["exact_hour_page", "exact_hour_page"]
    assert (
        coverage[0]["start_us"]
        == coverage[1]["end_us"]
        == reference.unix_microseconds(midnight)
    )
    assert coverage[0]["end_us"] == reference.unix_microseconds(end)
    assert coverage[1]["start_us"] == reference.unix_microseconds(start)
    assert engine.calls[2][1]["ref_population_end_us"] == reference.unix_microseconds(
        midnight
    )


def test_failed_older_hour_discards_already_found_newer_matches(engine):
    engine.insert("new", start_time=START + timedelta(days=2), attrs_number={"key": 1})
    engine.insert("old", attrs_number={"key": 1})

    class FailOlderHour:
        reads = 0
        final_reads = 0

        def execute_ch_query(self, sql, params, *, settings):
            self.reads += 1
            self.final_reads += int("FROM spans FINAL" in sql)
            if self.final_reads == 2:
                raise TimeoutError("older hour incomplete")
            return engine.execute_ch_query(sql, params, settings=settings)

    reader = FailOlderHour()
    with pytest.raises(TimeoutError, match="older hour incomplete"):
        reference.reference_span_ids(reader, case(), scope(), [leaf()])
    assert reader.final_reads == 2
    assert [part[1]["ref_scan_start_us"] for part in final_calls(engine)] == [
        reference.unix_microseconds(
            (START + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        )
    ]


@pytest.mark.parametrize(
    "operation,old_value,recent_value,proof",
    [
        ("between", 2, 0, "positive_same_row"),
        ("not_between", 5, 2, "necessary_typed_presence"),
    ],
)
def test_numeric_ranges_preserve_raw_prefix_daily_strategy(
    engine, operation, old_value, recent_value, proof
):
    end = START + timedelta(days=30)
    engine.insert(
        "old-match",
        start_time=end - timedelta(days=10),
        attrs_number={"key": old_value},
    )
    engine.insert(
        "recent-nonmatch",
        start_time=end - timedelta(days=1),
        attrs_number={"key": recent_value},
    )
    filters = [leaf(op=operation, value=[1, 3])]
    result = reference.reference_span_ids(engine, case(end=end), scope(), filters)
    assert public_ids(result) == ["old-match"]
    assert (
        result[1]["reference_route"]
        == "independent_adjacent_UTC_intervals_raw_prefix_FINAL_scalar_span_prefix.v1"
    )
    assert result[1]["reference_prefix_proof"] == proof
    assert result[1]["population_query_count"] == 0
    coverage = result[1]["interval_coverage"]
    assert all(part["kind"] == "exact_daily_page" for part in coverage)
    assert all(
        part["end_us"] - part["start_us"] <= reference._DAY_US for part in coverage[:7]
    )
    assert coverage[7]["end_us"] - coverage[7]["start_us"] == 7 * reference._DAY_US
    assert coverage[7]["rows"] == 1
    assert coverage[8]["end_us"] - coverage[8]["start_us"] == reference._DAY_US
    assert all(
        newer["start_us"] == older["end_us"]
        for newer, older in zip(coverage, coverage[1:])
    )
    assert coverage[-1]["start_us"] == reference.unix_microseconds(START)
    assert result[1]["population_exhausted"] and result[1]["complete_population_read"]
    exact, _ = reference.membership_having(filters, scalar_span=True)
    for sql, params, _ in engine.calls:
        assert params["ref_hourly_discovery"] is False
        assert "newest_raw_hour_us" not in sql and "raw_witness_prefixes" in sql
        raw, final = sql.split("physical_winners AS (", 1)
        assert "has(attrs_number.keys, %(ref_key_0)s)" in raw
        assert "LIMIT" not in raw and "is_deleted" not in raw
        assert (
            (f"WHERE ({exact})" in raw)
            if operation == "between"
            else "ref_value_" not in raw
        )
        assert f"AND ({exact})" in final
        assert "ref_scan_end_us)s - 1" in final


@pytest.mark.parametrize(
    "filters",
    [
        [leaf(op="is_null", value=None)],
        [leaf(op="not_equals", value=1)],
        [leaf(op="not_in", value=[1])],
        [leaf("text", "not_in", [1, False], types=["number", "boolean"])],
        [leaf(), leaf(op="not_between", value=[1, 3], key="range")],
        [leaf(), leaf(op="greater_than", value=0, key="range")],
    ],
)
def test_non_equality_or_mixed_conjunction_dispatch_keeps_daily_path(filters):
    reader = ProgressReader()
    result = reference.reference_span_ids(reader, case(), scope(), filters)
    assert result[1]["population_query_count"] == 0
    assert all("newest_raw_hour_us" not in call["sql"] for call in reader.calls)
    assert all(call["params"]["ref_hourly_discovery"] is False for call in reader.calls)


@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_equality_in_dispatch_is_generic_and_all_leaves_must_qualify(count):
    filters = [
        leaf("boolean", "in", [False, True], key=f"boolean-{i}")
        if i % 2
        else leaf("number", "equals", i, key=f"number-{i}")
        for i in range(count)
    ]
    sql, params = reference.build_span_reference_query(case(), scope(), filters)
    assert params["ref_hourly_discovery"] is True
    assert "raw_witness_prefixes" not in sql
    filters[-1] = leaf("number", "not_between", [1, 3], key="other-generic-key")
    sql, params = reference.build_span_reference_query(case(), scope(), filters)
    assert params["ref_hourly_discovery"] is False
    assert "raw_witness_prefixes" in sql


@pytest.mark.parametrize("op", ["between", "not_between"])
def test_daily_route_keeps_one_shared_byte_budget_without_fallback(op):
    reader = ProgressReader([5 * 1024**3, 4 * 1024**3])
    with pytest.raises(ReplayError, match="TOTAL_READ_BYTES_EXCEEDED"):
        reference.reference_span_ids(
            reader, case(), scope(), [leaf(op=op, value=[1, 3])]
        )
    assert [call["limits"]["max_bytes_to_read"] for call in reader.calls] == [
        8 * 1024**3,
        3 * 1024**3,
    ]
    assert all("raw_witness_prefixes" in call["sql"] for call in reader.calls)


@pytest.mark.parametrize(
    "days,filters",
    [
        (30, [leaf("boolean", "equals", False, key="flag"), leaf(value=17.5)]),
        (365, [leaf(value=17.5), leaf("text", "equals", "synthetic", key="text")]),
    ],
)
def test_171_numeric_witness_empty_skips_unrelated_full_hour_final(days, filters):
    # Shapes of 171's two independently-unverified cases, not customer values.
    # This reader proves the acquisition branch only, not an engine result.
    class NoNumericWitness(ProgressReader):
        def execute_ch_query(self, sql, params, *, settings):
            assert "FROM spans FINAL" not in sql, "unrelated full-hour FINAL read"
            self.population_empty = "mapContains(attrs_number" in sql
            return super().execute_ch_query(sql, params, settings=settings)

    request = case(days=days)
    date = {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": list(request["window"].values()),
        },
    }
    original = deepcopy((request, filters))
    reader = NoNumericWitness()
    rows, evidence = reference.reference_span_ids(
        reader, request, scope(), [date, *filters]
    )
    assert rows == [] and evidence["complete_population_read"]
    assert evidence["reference_population_proof"] == "mandatory_numeric_witness"
    for call in reader.calls:
        assert call["params"]["ref_key_0"] == "key"
        assert call["params"]["ref_value_0"] == 17.5
        assert "attrs_bool" not in call["sql"] and "attrs_string" not in call["sql"]
    assert all(
        part["kind"] == "numeric_witness_empty"
        and part["numeric_witness_rows"] == 0
        and "rows" not in part
        for part in evidence["interval_coverage"]
    )
    assert (request, filters) == original


@pytest.mark.parametrize("count", [2, 5, 10])
def test_native_numeric_hour_witness_latest_and_dense_control(engine, count):
    # One shared native fixture/oracle; no application builder or candidate IDs.
    filters = [leaf("boolean", "equals", False, key="flag"), leaf(value=17.5)]
    numbers, strings = {"key": 17.5}, {}
    if count > 2:
        filters += [
            leaf("text", "in", [7, False], key="typed", types=["number", "boolean"]),
            leaf("text", "equals", "synthetic", key="text"),
        ]
        numbers["typed"], strings["text"] = 7, "synthetic"
        engine.require_unicode()
    for index in range(len(filters), count):
        filters.append(leaf(value=index, key=f"n{index}"))
        numbers[f"n{index}"] = index
    winner = {
        "attrs_number": numbers,
        "attrs_string": strings,
        "attrs_bool": {"flag": 0},
    }
    request = case(end=START + timedelta(minutes=10), target=50)
    original = deepcopy((request, filters))
    for index in range(32):
        engine.insert(
            f"unrelated-{index:02}", **{**winner, "attrs_number": {**numbers, "key": 0}}
        )
    empty = reference.reference_span_ids(engine, request, scope(), filters)
    assert empty[0] == full_window_truth(engine, request, scope(), filters) == []
    assert not final_calls(engine) and empty[1]["complete_population_read"]
    assert empty[1]["interval_coverage"][0]["kind"] == "numeric_witness_empty"

    for name in ("stale", "removed", "dead", "left-window"):
        engine.insert(name, **winner)
        changes = {
            "stale": {"attrs_number": {**numbers, "key": 0}},
            "removed": {"attrs_number": {}},
            "dead": {"is_deleted": 1},
            "left-window": {"start_time": START + timedelta(minutes=10)},
        }[name]
        engine.insert(name, **{**winner, **changes, "_version": 2})
    engine.insert("entered", start_time=START - timedelta(microseconds=1), **winner)
    engine.insert("entered", start_time=START, _version=2, **winner)
    engine.insert("positive", start_time=START + timedelta(microseconds=1), **winner)
    engine.insert(
        "before-window", start_time=START - timedelta(microseconds=1), **winner
    )
    engine.insert("outside-project", project_id=OTHER, **winner)
    # Separate physical spans in one trace cannot supply a conjunction.
    for index, item in enumerate(filters):
        key = item["column_id"]
        engine.insert(
            f"child-{index}",
            parent_span_id="root",
            **{
                column: {key: mapping[key]}
                for column, mapping in winner.items()
                if key in mapping
            },
        )
    if count > 2:
        engine.insert(
            "wrong-typed",
            **{
                **winner,
                "attrs_number": {k: v for k, v in numbers.items() if k != "typed"},
                "attrs_string": {**strings, "typed": "7"},
            },
        )
    for index in range(32):
        engine.insert(
            f"dense-{index:02}", start_time=START + timedelta(minutes=5), **winner
        )
    expected = [f"dense-{index:02}" for index in reversed(range(32))] + [
        "positive",
        "entered",
    ]
    for negative_sibling in (False, True):
        selected = deepcopy(filters)
        if negative_sibling:
            selected[0] = leaf("boolean", "not_equals", True, key="flag")
        result = reference.reference_span_ids(engine, request, scope(), selected)
        assert public_ids(result) == expected
        assert result[0] == full_window_truth(engine, request, scope(), selected)
        assert result[1]["complete_population_read"] and not result[1]["has_more"]
        assert (
            result[1].get("reference_population_proof") == "mandatory_numeric_witness"
        ) is not negative_sibling
    # A missing-sibling predicate keeps the existing daily absence semantics.
    missing = deepcopy(filters)
    missing[0] = leaf("boolean", "is_null", None, key="flag")
    result = reference.reference_span_ids(engine, request, scope(), missing)
    assert public_ids(result) == (["child-1"] if count == 2 else [])
    assert result[0] == full_window_truth(engine, request, scope(), missing)
    assert result[1]["population_query_count"] == 0
    assert (request, filters) == original

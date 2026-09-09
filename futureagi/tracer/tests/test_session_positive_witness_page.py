"""Complete positive session candidates: native SQL and real list dispatch.

The independent QA oracle uses its own FINAL population and compiler. Native
fixtures are local RMTs; the reduced chdb engine cannot qualify Unicode text.
"""

import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT, OTHER, USER = (str(UUID(int=i)) for i in (1, 2, 3))
START = datetime(2026, 8, 1, 12, 30, 0, 123456, tzinfo=UTC)


def leaf(key="key", value=1, kind="number", op="equals", source="SPAN_ATTRIBUTE"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def builder(*filters, cls=SessionListQueryBuilderV2, org=False, days=7, size=2):
    return cls(
        **({"project_ids": [PROJECT, OTHER]} if org else {"project_id": PROJECT}),
        filters=[
            leaf(
                "created_at",
                [START, START + timedelta(days=days)],
                "datetime",
                "between",
                "SYSTEM_METRIC",
            ),
            *filters,
        ],
        page_size=size,
        bounded_internal_scan=True,
    )


def cte(sql, name):
    start = re.search(r"\b" + name + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize(
    "kind,value,op",
    [
        ("number", 1, "greater_than"),
        ("number", 0, "equals"),
        ("boolean", False, "equals"),
        ("text", "%_\\" + "x" * 700, "contains"),
    ],
)
def test_default_page_cursor_count_dispatch_share_complete_witness(
    cls, org, kind, value, op
):
    subject = builder(leaf(value=value, kind=kind, op=op), cls=cls, org=org)
    assert (
        subject.supports_candidate_first_page()
        and subject.supports_candidate_cursor_page()
    )
    for query in (
        subject.build_candidate_page_query,
        subject.build_candidate_cursor_page_query,
        subject.build_candidate_count_query,
    ):
        sql, params = query()
        witness = sql.split(
            "(SELECT groupUniqArray(assumeNotNull(trace_session_id))", 1
        )[1].split(") AS candidate_witness_session_ids,", 1)[0]
        assert "parent_span_id" not in witness and "LIMIT" not in witness
        assert "SAMPLE" not in sql and "filter_anchor_limit" not in params
        assert sql.count("AS candidate_witness_session_ids,") == 1
        assert "candidate_root_raw_session_ids AS" not in sql
        assert "candidate_witness_session_ids" in cte(sql, "candidate_filter_sessions")
        assert "candidate_filter_sessions" in cte(
            sql, "candidate_scalar_span_identities"
        )
        assert "candidate_filter_sessions" in cte(sql, "candidate_root_identities")
        assert "attrs_" not in cte(sql, "candidate_root_identities")
        assert "argMax" in cte(sql, "latest_candidate_scalar_spans")
        assert "min(start_time) AS session_start" in cte(sql, "sessions")
        assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
        if kind == "number" and op == "greater_than":
            assert "> %(latest_filter_param_0)s" in witness


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize(
    "first,second,typed,rank,chosen",
    [
        (leaf("first", False, "boolean"), leaf("second", 7), False, None, 1),
        (leaf("first", True, "boolean"), leaf("second", [7, 8], op="in"), False, None, 1),
        (leaf("first", "common", "string"), leaf("second", 7), False, None, 1),
        (leaf("first", "common", "text"), leaf("second", [7, 8], op="in"), False, None, 1),
        (leaf("first", "common", "text"), leaf("second", ["7", "8"], "text", "in"), True, None, 1),
        (leaf("first", "common", "text"), leaf("second", 0), False, None, 0),
        (leaf("first", False, "boolean"), leaf("second", [0, 7], op="in"), False, None, 0),
        (leaf("first", "common", "text"), leaf("second", ["0", "7"], "text", "in"), True, None, 0),
        (leaf("first", "common", "text"), leaf("second", 7), False, 10, 0),
        (leaf("first", 5), leaf("second", 7), False, None, 0),
        (leaf("first", 5), leaf("second", [7, 8], op="in"), False, None, 0),
        (leaf("first", "x", "text"), leaf("second", "y", "text"), False, None, 0),
        (leaf("first", True, "boolean"), leaf("second", False, "boolean"), False, None, 0),
    ],
    ids=["false-number", "true-numbers", "string-number", "text-numbers", "typed-numbers",
         "zero-default", "zero-in-default", "typed-zero-default", "rank-before-hint",
         "stable-number", "stable-numbers", "stable-text", "stable-boolean"],
)
def test_numeric_witness_preference_keeps_complete_membership(cls, org, first, second, typed, rank, chosen):
    if typed:
        second = {**second, "filter_config": {**second["filter_config"], "attribute_value_types": ["number", "number"]}}
    subject = builder(first, second, leaf("absent", None, op="is_null"), cls=cls, org=org)
    plans = subject._candidate_scalar_page_plans()
    if rank is not None:
        # Operator rank remains authoritative even if a numeric hint exists.
        plans = (plans[0], replace(plans[1], raw_witness_rank=rank), *plans[2:])
    with mock.patch.object(subject, "_candidate_scalar_page_plans", return_value=plans):
        sql, params = subject.build_candidate_cursor_page_query()
    witness = sql.split("(SELECT groupUniqArray(assumeNotNull(trace_session_id))", 1)[1].split(
        ") AS candidate_witness_session_ids,", 1
    )[0]
    assert set(re.findall(r"%\((latest_filter_key_\d+)\)s", witness)) == {f"latest_filter_key_{chosen}"}
    assert params[f"latest_filter_key_{chosen}"] == ("first" if chosen == 0 else "second")
    # The preferred seed is not same-span AND truth; retain both positive
    # memberships AND the inverse absence predicate after latest replay.
    having = " AND ".join(plan.grouped_match_predicate() for plan in plans)
    assert "HAVING " + " ".join(having.split()) in cte(sql, "matching_scalar_sessions")
    replay = cte(sql, "latest_candidate_scalar_spans")
    assert all(f"%(latest_filter_key_{index})s" in replay for index in range(3))
    assert "argMax" in replay and "GROUP BY" in replay
    assert "LIMIT" not in witness and "SAMPLE" not in sql
    assert "ORDER BY session_start DESC, session_id DESC" in sql


@pytest.mark.parametrize("shape", ["negative", "null", "map", "relation", "sort"])
def test_unsupported_and_negative_only_shapes_keep_fallback(shape):
    item = {
        "negative": leaf(op="not_equals"),
        "null": leaf(value=None, op="is_null"),
        "map": leaf(value={"a": 1}, kind="map", op="contains"),
        "relation": leaf(str(UUID(int=90)), source="ANNOTATION"),
        "sort": leaf(),
    }[shape]
    subject = builder(item)
    if shape == "sort":
        subject.sort_params = [{"column_id": "total_cost", "direction": "desc"}]
    assert not subject.supports_candidate_first_page()
    assert not subject.supports_candidate_cursor_page()


@pytest.mark.parametrize(
    "op", ["equals", "in", "not_equals", "not_in", "is_null", "is_not_null"]
)
def test_native_finite_session_filter_scopes_every_user_operator(op):
    subject = builder(
        leaf("session_id", [str(UUID(int=100))], "text", "in", "SYSTEM_METRIC"),
        leaf("end_user_id", [USER], "text", op, "SYSTEM_METRIC"),
    )
    sql, _ = subject.build_candidate_cursor_page_query()
    seed = cte(sql, "candidate_user_span_identities")
    assert "candidate_filter_sessions" in seed and "ts_survivor_map" in seed
    assert "end_user_id IN" not in seed
    assert "latest_is_deleted = 0" in cte(sql, "resolved_user_spans")


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("relational", [False, True])
def test_org_project_output_alias_cannot_shadow_input_membership(cls, relational):
    item = leaf(str(UUID(int=50)), source="ANNOTATION") if relational else leaf()
    subject = builder(item, cls=cls, org=True)
    queries = [subject.build_filter_match_query([str(UUID(int=100))])]
    if not relational:
        queries += [method() for method in (
            subject.build_candidate_page_query, subject.build_candidate_cursor_page_query,
            subject.build_candidate_count_query,
        )]
    for sql, _ in queries:
        sessions = cte(sql, "sessions")
        assert "any(resolved_root_sessions.project_id) AS project_id" in sessions
        assert "(resolved_root_sessions.project_id, session_id) IN (" in sessions
        assert "max(project_count) AS project_count" in sessions
        assert "GROUP BY session_id" in sessions
        counts = cte(sql, "candidate_session_project_counts")
        assert "uniqExact(project_id) AS project_count" in counts
        assert "FROM resolved_root_sessions GROUP BY session_id" in counts


@pytest.fixture
def engine(tmp_path, record_property):
    pytest.importorskip("chdb", reason="explicit isolated native engine required")
    qa_dir = Path(__file__).resolve().parents[3] / "scripts/qa"
    with mock.patch.object(sys, "path", [str(qa_dir), *sys.path]):
        import test_observe_session_reference as independent
    local = independent.LocalRMT(tmp_path / "positive-session-rmt")
    local.oracle = independent.reference.reference_session_pages
    local.execute(
        "ALTER TABLE spans ADD COLUMN end_user_id Nullable(UUID), ADD COLUMN input String DEFAULT ''"
    )
    local.execute("""CREATE TABLE end_user_id_remap (
        old_id UUID, new_id UUID, version DateTime64(6, 'UTC')
    ) ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
    record_property("clickhouse_version", local.version)
    try:
        yield local
    finally:
        local.session.close()


@pytest.mark.parametrize("collision", [False, True])
def test_native_org_scalar_user_page_preserves_project_and_collision_evidence(engine, collision):
    engine.insert(100, end_user_id=USER, attrs_number={"key": 1})
    engine.insert(200, project_id=OTHER, end_user_id=USER, attrs_number={"key": 1})
    if collision:
        # A nonmatching root in another project still makes UUID-only hydration unsafe.
        engine.insert(100, project_id=OTHER, end_user_id=USER, attrs_number={})
    subject = builder(leaf(), leaf("end_user_id", [USER], "text", "in", "SYSTEM_METRIC"), org=True)
    rows = engine.execute(*subject.build_candidate_page_query())
    assert [row["session_id"] for row in rows] == [str(UUID(int=200)), str(UUID(int=100))]
    assert {int(row["max_project_count"]) for row in rows} == {2 if collision else 1}
    assert rows[0]["project_id"] == OTHER and int(rows[0]["project_count"]) == 1
    assert int(rows[1]["project_count"]) == (2 if collision else 1)
    if not collision:
        assert rows[1]["project_id"] == PROJECT


def exact_pages(engine, filters, *, days=7, size=2):
    subject = builder(*filters, days=days, size=size)
    expected = engine.oracle(
        engine,
        project_ids=[PROJECT],
        authorized_project_ids=[PROJECT],
        start=START,
        end=START + timedelta(days=days),
        filters=filters,
        page_size=size,
        order_mode="uuid",
    )
    first = engine.execute(*subject.build_candidate_cursor_page_query())
    boundary = {}
    if first:
        last = first[:size][-1]
        boundary = {
            "before_start_time": datetime.fromisoformat(last["session_start"]),
            "before_session_id": last["session_id"],
        }
    second = (
        engine.execute(*subject.build_candidate_cursor_page_query(**boundary))
        if first
        else []
    )
    pages = [[row["session_id"] for row in page[:size]] for page in (first, second)]
    assert pages == [[r["session_id"] for r in page] for page in expected["pages"]]
    numbered = engine.execute(*subject.build_candidate_page_query())
    assert [r["session_id"] for r in numbered[:size]] == pages[0]
    count = engine.execute(*subject.build_candidate_count_query())[0]["total"]
    assert int(count) == expected["total_sessions"]
    # Previous-page navigation replays its original boundary (no inverse UUID order).
    assert engine.execute(*subject.build_candidate_cursor_page_query()) == first
    assert not (set(pages[0]) & set(pages[1]))
    return pages, expected


@pytest.mark.parametrize("kind", ["number", "boolean"])
@pytest.mark.parametrize(
    "replacement", ["clear", "tombstone", "session-null", "move", "before", "after"]
)
def test_native_latest_winner_removes_stale_positive_witness(engine, kind, replacement):
    column, value = ("attrs_number", 7) if kind == "number" else ("attrs_bool", 0)
    predicate = leaf(value=value if kind == "number" else False, kind=kind)
    engine.insert(100)  # The matching attribute may live only on a child.
    child = dict(id="child", parent_span_id="root", **{column: {"key": value}})
    engine.insert(100, **child)
    changes = {
        "clear": {column: {}},
        "tombstone": {"is_deleted": 1},
        "session-null": {"trace_session_id": None},
        "move": {"trace_session_id": str(UUID(int=900))},
        "before": {"start_time": START - timedelta(microseconds=1)},
        "after": {"start_time": START + timedelta(minutes=20)},
    }[replacement]
    # before/after corrections stay in the SAME replacement hour as the old child.
    if replacement == "after":
        changes["start_time"] = START + timedelta(days=7)
        child["start_time"] = START + timedelta(days=7) - timedelta(microseconds=1)
        engine.insert(100, **child)
        # Remove the earlier child identity so only the end-boundary one remains.
        engine.insert(100, id="child", parent_span_id="root", _version=2, is_deleted=1)
    engine.insert(100, **{**child, **changes, "_version": 3})
    pages, _ = exact_pages(engine, [predicate])
    assert pages == [[], []]


@pytest.mark.parametrize("width", [2, 5, 10])
def test_native_independent_leaves_all_aliases_and_root_metrics(engine, width):
    engine.remap(100, 900)  # Canonical ID has no physical spans.
    engine.remap(200, 900)
    engine.remap(300, 900)
    engine.insert(200, total_tokens=11, start_time=START + timedelta(minutes=2))
    engine.insert(300, total_tokens=13, start_time=START + timedelta(minutes=1))
    keys = [
        "created_at",
        "start_time",
        "end_user_id",
        "session_id",
        "duration",
        "total_tokens",
        "total_cost",
        "first_message",
        "custom8",
        "custom9",
    ][:width]
    filters = []
    for i, key in enumerate(keys):
        is_bool = i % 2 == 1
        filters.append(
            leaf(key, False if is_bool else 7, "boolean" if is_bool else "number")
        )
        engine.insert(
            900,
            id=f"child-{i}",
            trace_id=f"independent-trace-{i}",
            parent_span_id="root",
            **{"attrs_bool" if is_bool else "attrs_number": {key: 0 if is_bool else 7}},
            total_tokens=999,
            start_time=START + timedelta(minutes=10 + i),
        )
    pages, reference = exact_pages(engine, filters)
    assert pages == [[str(UUID(int=100))], []]
    sql, params = builder(*filters).build_page_metrics_query([str(UUID(int=100))])
    metrics = engine.execute(sql, params)[0]
    assert metrics["total_tokens"] == reference["metrics"][0]["total_tokens"] == 24
    assert datetime.fromisoformat(metrics["session_start"]).replace(
        tzinfo=UTC
    ) == START + timedelta(minutes=1)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_native_root_minimum_uuid_ties_and_two_pages(engine, days):
    tricky = [
        "00000000-0000-0000-ffff-ffffffffffff",
        "ffffffff-ffff-ffff-0000-000000000001",
    ]
    for i, sid in enumerate(tricky):
        engine.insert(100 + i, trace_session_id=sid, attrs_number={"key": 7})
    # A late witness does not make its session newer than its earlier root.
    engine.insert(200, start_time=START)
    engine.insert(
        200,
        id="late-child",
        parent_span_id="root",
        attrs_number={"key": 7},
        start_time=START + timedelta(days=days) - timedelta(microseconds=1),
    )
    # Full physical keys: neither foreign project nor another service/type can erase it.
    engine.insert(200, project_id=OTHER, _version=9, is_deleted=1)
    engine.insert(
        200,
        service_name="collision",
        observation_type="GENERATION",
        _version=8,
        is_deleted=1,
    )
    pages, _ = exact_pages(
        engine, [leaf(value=1, op="greater_than")], days=days, size=1
    )
    assert pages == [[tricky[0]], [tricky[1]]]


def test_native_population_exceeds_retired_anchor_sentinel(engine):
    for i in range(70):
        engine.insert(100 + i, attrs_number={"key": 7})
    pages, reference = exact_pages(engine, [leaf(value=1, op="greater_than")], size=25)
    assert reference["total_sessions"] == 70 and len(pages[0]) == len(pages[1]) == 25


@pytest.mark.parametrize("kind,value", [("number", 0), ("boolean", False)])
def test_native_zero_false_missing_and_mixed_null(engine, kind, value):
    engine.insert(100, attrs_number={"key": 0, "anchor": 1}, attrs_bool={"key": 0})
    engine.insert(200, attrs_number={"anchor": 1}, attrs_string={"key": "0"})
    engine.insert(300, attrs_number={"key": 2, "anchor": 1}, attrs_bool={"key": 1})
    pages, _ = exact_pages(engine, [leaf(value=value, kind=kind)])
    assert pages == [[str(UUID(int=100))], []]
    pages, _ = exact_pages(
        engine, [leaf("anchor"), leaf(kind=kind, value=None, op="is_null")]
    )
    assert pages == [[str(UUID(int=200))], []]


def test_native_unicode_text_requires_full_function_engine(engine):
    available = engine.execute(
        "SELECT count() AS present FROM system.functions WHERE name = 'lowerUTF8'"
    )
    if not available[0]["present"]:
        pytest.skip(
            "reduced chdb lacks lowerUTF8; no ASCII substitution or text qualification"
        )
    value = "K Straße %_\\" + "long" * 150
    engine.insert(100, attrs_string={"key": value})
    engine.insert(200, attrs_string={"key": "different"})
    pages, _ = exact_pages(engine, [leaf(value=value, kind="text")])
    assert pages == [[str(UUID(int=100))], []]


@pytest.mark.parametrize("cursor", [False, True])
@pytest.mark.parametrize("kind,value", [("number", 1), ("boolean", False)])
def test_real_list_entrypoint_uses_primary_witness_without_bounded_root_walk(
    cursor, kind, value
):
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    calls = []
    newest, older = START + timedelta(minutes=2), START + timedelta(minutes=1)

    def execute(sql, params, **kwargs):
        calls.append((sql, params))
        if "AS candidate_witness_session_ids," in sql:
            assert "filter_anchor_limit" not in params
            first = "cursor_before_start_us" not in params
            data = [(100, newest), (200, older)] if first else [(200, older)]
            return SimpleNamespace(
                data=[
                    {
                        "session_id": str(UUID(int=sid)),
                        "session_start": time,
                        "remaining_count": len(data),
                        "total_count": 2,
                    }
                    for sid, time in data
                ]
            )
        if "sum(cost) AS total_cost" in sql:
            return SimpleNamespace(
                data=[
                    {
                        "session_id": sid,
                        "session_start": newest,
                        "session_end": newest,
                        "duration": 0,
                        "total_cost": 1,
                        "total_tokens": 1,
                        "traces_count": 1,
                    }
                    for sid in params["candidate_session_ids"]
                ]
            )
        return SimpleNamespace(data=[])

    view._fetch_session_names = mock.Mock(return_value={})
    view._fetch_end_user_info = mock.Mock(return_value={})
    data = {
        "filters": builder(
            leaf(
                value=value,
                kind=kind,
                op="greater_than" if kind == "number" else "equals",
            )
        ).filters,
        "sort_params": [],
        "page_number": 0,
        "page_size": 1,
        "cursor_mode": cursor,
    }
    with (
        mock.patch("tracer.views.trace_session.read_bounded_filter_page") as bounded,
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        kwargs = {
            "project_id": PROJECT,
            "project": None,
            "analytics": SimpleNamespace(execute_ch_query=execute),
        }
        status, first = TraceSessionView._list_sessions_clickhouse(
            view, request, validated_data=data, **kwargs
        )
        assert status == "ok" and first["table"][0]["session_id"] == str(UUID(int=100))
        if cursor:
            token = first["metadata"]["next_cursor"]
            assert token
            status, second = TraceSessionView._list_sessions_clickhouse(
                view, request, validated_data={**data, "cursor": token}, **kwargs
            )
            assert status == "ok" and second["table"][0]["session_id"] == str(
                UUID(int=200)
            )
            assert second["metadata"]["next_cursor"] is None
        bounded.assert_not_called()
    assert any("AS candidate_witness_session_ids," in sql for sql, _ in calls)


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize("filters,preferred", [
    ([leaf("company", ["alpha"], "text", "in")], True),
    ([leaf("flag", False, "boolean"), leaf("company", "alpha", "text")], True),
    ([leaf("flag", True, "boolean")], False),
    ([leaf("company", "alpha", "text"), leaf("number", 7)], False),
    ([leaf("company", "alpha", "text"), leaf("zero", 0)], False),
    ([leaf("typed", ["7"], "text", "in")], False),
    ([leaf("typed", ["0"], "text", "in")], False),
    ([leaf("company", "alpha", "text"), leaf("missing", None, op="is_null")], False),
    ([leaf("company", "alpha", "text", "not_equals")], False),
    ([leaf("company", "alpha", "text"), leaf("session_id", [USER], "text", "in", "SYSTEM_METRIC")], False),
    ([leaf("company", "alpha", "text"), leaf("end_user_id", [USER], "text", "in", "SYSTEM_METRIC")], False),
])
def test_string_page_policy_and_finite_single_replay(cls, org, filters, preferred):
    for item in filters:
        if item["column_id"] == "typed":
            item["filter_config"]["attribute_value_types"] = ["number"]
    subject = builder(*filters, cls=cls, org=org)
    assert subject.prefers_bounded_filter_page() is preferred
    assert not preferred or subject.supports_candidate_cursor_page()  # Policy is not capability.
    assert subject.recommended_filter_cursor_seed_batch_size() is None
    assert subject.recommended_filter_classify_batch_size() == 50
    assert subject.recommended_filter_max_slice_width() is None
    sql, params = subject.build_filter_match_query_from_seed_rows([{"session_id": USER}])
    combined = preferred and not org
    assert ("candidate_root_identities AS" not in sql) is combined
    assert "candidate_scalar_coordinates" not in sql
    if combined:
        assert sql.count("FROM spans") == 2 and sql.count("FROM resolved_candidate_scalar_spans") == 1
        replay, sessions = cte(sql, "latest_candidate_scalar_spans"), cte(sql, "sessions")
        version = "_version" if cls is SessionListQueryBuilderV2 else "_peerdb_version"
        assert f"argMax(tuple(parent_span_id), {version}).1 AS latest_parent_span_id" in replay
        assert "GROUP BY " + subject._physical_group_by_sql() in replay
        assert "minIf(latest_start_time, is_root) AS session_start" in sessions and "countIf(is_root) > 0" in sessions
        assert "session_id IN (SELECT session_id FROM candidate_filter_sessions)" in sessions
        for plan in subject._bounded_span_filter_parts()[0]:
            assert " ".join(plan.grouped_match_predicate().split()) in sessions
        with mock.patch.object(subject, "prefers_bounded_filter_page", return_value=False):
            original, original_params = subject.build_filter_match_query_from_seed_rows([{"session_id": USER}])
        assert original.count("FROM spans") == 4 and params == original_params
        full, _ = subject.build_filter_match_query([USER], candidate_full_state=True)
        assert "candidate_root_identities AS" in full


@pytest.mark.parametrize("minutes,expected", [(4, None), (5, 5), (60, 60), (2880, 1440)])
def test_string_page_initial_width_preserves_minimum_and_maximum(minutes, expected):
    subject = builder(leaf("company", "alpha", "text"), days=minutes / 1440)
    assert subject.recommended_filter_initial_slice_width() == (timedelta(minutes=expected) if expected else None)
    assert subject.recommended_filter_max_slice_width() is None

@pytest.mark.parametrize('old_parent,new_parent', [(None, 'root'), ('root', None), ('root', '')],
                         ids=['root-to-child', 'child-to-null-root', 'child-to-empty-root'])
def test_native_parent_winner_controls_root_only_order(engine, old_parent, new_parent, record_property):
    from datetime import UTC

    from observe_session_reference import _us
    start = START
    filters = [leaf('company', ['alpha'], 'text', 'in'),
               leaf('flag', False, 'boolean')]
    filters[0]['filter_config']['attribute_value_types'] = ['string']
    for sid, minute in ((100, 10), (200, 5)):
        engine.insert(sid, start_time=start + timedelta(minutes=minute))  # Nonmatching roots set order.
    for sid in (100, 200, 300):  # 300 has witnesses but no live root.
        engine.insert(sid, id=f'string-{sid}', parent_span_id='root',
                      attrs_string={'company': 'alpha'}, start_time=start + timedelta(minutes=20))
        engine.insert(sid, id=f'bool-{sid}', parent_span_id='root',
                      attrs_bool={'flag': 0}, start_time=start + timedelta(minutes=21))
    old = engine.insert(100, id='changing-parent', parent_span_id=old_parent,
                        start_time=start + timedelta(minutes=1))
    engine.insert(100, **{**old, 'parent_span_id': new_parent, '_version': 2})
    retained = engine.execute("SELECT count() AS n, uniqExact(_version) AS versions FROM spans "
                              "WHERE id = 'changing-parent'")[0]
    assert (int(retained['n']), int(retained['versions'])) == (2, 2)
    reference = engine.oracle(engine, project_ids=[PROJECT],
        authorized_project_ids=[PROJECT], start=start, end=start + timedelta(days=7),
        filters=filters, page_size=3, order_mode='uuid_string')
    expected = [(row['session_id'], row['session_start_us']) for page in reference['pages'] for row in page]
    truth = [(100, 10), (200, 5)] if new_parent == 'root' else [(200, 5), (100, 1)]
    assert reference['population_exhausted_after_two_pages'] and reference['total_sessions'] == 2
    assert expected == [(str(UUID(int=sid)), _us(start + timedelta(minutes=minute))) for sid, minute in truth]
    def emitted():
        return builder(*filters).build_filter_match_query_from_seed_rows(
            [{'session_id': str(UUID(int=sid))} for sid in (100, 200, 300)])
    def signature(rows):
        return [(row['session_id'], _us(datetime.fromisoformat(str(row['start_time'])).replace(tzinfo=UTC)))
                for row in rows]
    sql, params = emitted()
    assert sql.count('FROM spans') == 2
    assert signature(engine.execute(sql, params)) == expected
    target = 'argMax(tuple(parent_span_id), _version)'
    assert sql.count(target) == 1
    mutant = sql.replace(target, 'argMin(tuple(parent_span_id), _version)')
    assert signature(engine.execute(mutant, params)) != expected  # RED: stale parent changes order.
    record_property('parent_versions_retained', 2)
    record_property('candidate_span_reads', 2)

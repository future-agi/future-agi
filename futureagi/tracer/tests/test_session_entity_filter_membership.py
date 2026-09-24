"""Offline session routing/entity-grain regressions; no live SQL execution."""

import re
from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
OTHER_PROJECT = "00000000-0000-4000-8000-000000000002"
SESSION = "00000000-0000-4000-8000-000000000003"
LABEL = "00000000-0000-4000-8000-000000000004"
END = datetime(2026, 9, 5, 7)


def _filter(
    key, value=None, *, kind="text", operation="equals", source="SPAN_ATTRIBUTE"
):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _builder(*filters, org=False):
    return SessionListQueryBuilderV2(
        **(
            {"project_ids": [PROJECT, OTHER_PROJECT]}
            if org
            else {"project_id": PROJECT}
        ),
        filters=[
            _filter(
                "created_at",
                [(END - timedelta(days=7)).isoformat(), END.isoformat()],
                kind="datetime",
                operation="between",
                source="SYSTEM_METRIC",
            ),
            *filters,
        ],
        annotation_label_ids=[LABEL],
        annotation_label_ids_by_project={PROJECT: [LABEL], OTHER_PROJECT: []},
        eval_config_ids=[],
        bounded_internal_scan=True,
    )


def _cte(sql, name):
    # The single-project positive path fuses scalar membership and roots into
    # sessions. Assert the same entity-grain predicates in that actual stage.
    if (
        name == "matching_scalar_sessions"
        and "matching_scalar_sessions AS (" not in sql
    ):
        name = "sessions"
    start = re.search(r"\b" + re.escape(name) + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if not depth:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


@pytest.mark.parametrize(
    "key",
    [
        "duration",
        "total_cost",
        "total_tokens",
        "traces_count",
        "total_traces_count",
        "first_message",
        "last_message",
        "session",
        "session_id",
        "trace_session_id",
        "user",
        "end_user_id",
        "created_at",
        "start_time",
        "end_time",
    ],
)
@pytest.mark.parametrize("org", [False, True])
def test_explicit_raw_reserved_keys_do_not_become_native_session_filters(key, org):
    item = _filter(key, "raw-value")
    builder = _builder(item, org=org)
    assert item in builder._bounded_scalar_span_filters()
    assert item in builder._extract_span_filters()
    assert builder._build_having_clauses() == ""
    assert not builder.has_having_filters()
    assert not builder._has_message_filters()
    assert builder.supports_candidate_first_page()
    assert builder.supports_candidate_cursor_page()
    assert not builder.supports_filter_candidate_seed_page()
    assert builder.supports_bounded_filter_scan()
    sql, params = builder.build_filter_match_query([SESSION])
    assert key in params.values()
    assert "raw-value" in params.values()
    assert "mapContains(attrs_string" in _cte(sql, "latest_candidate_scalar_spans")
    assert "matching_user_sessions AS" not in sql
    assert not any(key.startswith("candidate_sess_") for key in params)
    assert "latest_input" not in sql
    assert "HAVING countIf(" in _cte(sql, "matching_scalar_sessions")
    # Page attribute hydration must not reinterpret a raw timestamp key as
    # another request-window constraint in the V2 subclass either.
    builder.build_span_attributes_query([SESSION])


@pytest.mark.parametrize(
    "key", ["duration", "total_cost", "total_tokens", "traces_count"]
)
@pytest.mark.parametrize("source", [None, "SYSTEM_METRIC"])
def test_native_session_aggregate_contract_is_unchanged(key, source):
    item = _filter(key, 5, kind="number", operation="greater_than", source=source)
    if source is None:
        item["filter_config"].pop("col_type")
    builder = _builder(item)
    assert builder.supports_candidate_first_page()
    assert builder._bounded_scalar_span_filters() == []
    sql, params = builder.build_candidate_page_query()
    assert f"HAVING {key} >" in _cte(sql, "sessions")
    assert "latest_candidate_scalar_spans AS" not in sql
    assert 5 in params.values()


@pytest.mark.parametrize("org", [False, True])
def test_scalar_leaves_intersect_at_project_session_grain_before_limit(org):
    builder = _builder(
        _filter("company_id", "company"), _filter("prompt_slug", "summary"), org=org
    )
    sql, params = builder.build_filter_match_query([SESSION])
    membership = _cte(sql, "matching_scalar_sessions")
    assert "FROM resolved_candidate_scalar_spans" in membership
    assert "GROUP BY project_id, session_id HAVING" in membership
    assert "GROUP BY project_id, session_id, trace_id" not in membership
    assert "countIf(latest_attr_exists_0 AND" in membership
    assert "countIf(latest_attr_exists_1 AND" in membership
    assert membership.count("> 0") == (2 if org else 3)
    if not org:
        assert "AND countIf(is_root) > 0" in membership
    assert "matching_scalar_traces AS" not in sql
    assert "ORDER BY start_time DESC, toString(session_id) DESC" in sql
    assert params["bounded_match_limit"] == 1
    assert params["candidate_filter_session_id_array"] == [SESSION]
    if org:
        assert "(resolved_root_sessions.project_id, session_id) IN" in _cte(
            sql, "sessions"
        )
    stage = "matching_scalar_sessions" if org else "sessions"
    assert sql.index(f"{stage} AS") < sql.rindex("LIMIT")


@pytest.mark.parametrize("kind", ["text", "number", "boolean", "array", "map"])
def test_grouped_null_requires_no_live_typed_key_anywhere_in_session(kind):
    builder = _builder(
        _filter("optional", kind=kind, operation="is_null"),
        _filter("company_id", "company"),
    )
    plans, residual = builder._bounded_span_filter_parts()
    assert not residual and plans[0].exclude_group_matches
    sql, _ = builder.build_filter_match_query([SESSION])
    membership = _cte(sql, "matching_scalar_sessions")
    assert plans[0].grouped_match_predicate() in membership
    assert "= 0" in membership and "> 0" in membership
    assert (
        "FROM resolved_candidate_scalar_spans GROUP BY project_id, session_id"
        in membership
    )


def test_latest_physical_replay_remaps_and_all_root_metrics_are_preserved():
    builder = _builder(
        _filter("company_id", "company"),
        _filter(
            "total_cost",
            5,
            kind="number",
            operation="greater_than",
            source="SYSTEM_METRIC",
        ),
    )
    sql, _ = builder.build_filter_match_query([SESSION])
    seed = _cte(sql, "candidate_scalar_span_identities")
    latest = _cte(sql, "latest_candidate_scalar_spans")
    live = _cte(sql, "resolved_candidate_scalar_spans")
    assert "attrs_string" not in seed
    assert "parent_span_id" not in seed  # Child spans may witness membership.
    assert (
        "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in latest
    )
    assert "argMax(is_deleted, _version)" in latest
    assert "argMax(tuple(trace_session_id), _version)" in latest
    assert "latest_is_deleted = 0" in live
    assert "LEFT JOIN ts_survivor_map AS scalar_ts_remap" in live
    sessions = _cte(sql, "sessions")
    assert "sum(cost) AS total_cost" in sessions
    assert "FROM resolved_root_sessions" in sessions
    assert "latest_attr_value" not in sessions
    assert "matching_scalar_sessions" in sessions
    assert "HAVING total_cost >" in sessions


@pytest.mark.parametrize("org", [False, True])
def test_relational_leaves_have_independent_session_membership_and_keep_all_roots(org):
    builder = _builder(
        _filter("has_annotation", True, kind="boolean", source="SYSTEM_METRIC"),
        _filter(LABEL, "helpful", source="ANNOTATION"),
        _filter(
            "total_tokens",
            5,
            kind="number",
            operation="greater_than",
            source="SYSTEM_METRIC",
        ),
        org=org,
    )
    sql, _ = builder.build_filter_match_query([SESSION])
    membership = _cte(sql, "matching_relational_sessions")
    assert (
        "FROM resolved_root_sessions GROUP BY project_id, session_id HAVING"
        in membership
    )
    assert membership.count("countIf(") == 2
    assert membership.count("> 0") == 2
    sessions = _cte(sql, "sessions")
    assert "FROM resolved_root_sessions" in sessions
    assert "sum(total_tokens) AS total_tokens" in sessions
    assert "(resolved_root_sessions.project_id, session_id) IN" in sessions
    assert "trace_id IN" not in sessions
    assert "%(session_relational_trace_ids)s" not in sql
    assert "candidate_relational_trace_ids AS" in sql


@pytest.mark.parametrize("has_second_leaf", [True, False])
def test_fixture_cross_trace_intersection_uses_the_generated_session_grain(
    has_second_leaf,
):
    """Execute a tiny grouping model, not a ClickHouse interpreter.

    This catches trace-grain regressions with two sibling trace witnesses and
    a tenant-isolation counterexample. Latest replay is asserted separately.
    """
    sql, _ = _builder(_filter("a", "x"), _filter("b", "y")).build_filter_match_query(
        [SESSION]
    )
    membership = _cte(sql, "matching_scalar_sessions")
    grain = re.search(r"GROUP BY (.*?) HAVING", membership).group(1).split(", ")
    rows = [
        {
            "project_id": PROJECT,
            "session_id": SESSION,
            "trace_id": "a",
            "a": True,
            "b": False,
        },
        {
            "project_id": PROJECT,
            "session_id": SESSION,
            "trace_id": "b",
            "a": False,
            "b": has_second_leaf,
        },
        {
            "project_id": OTHER_PROJECT,
            "session_id": SESSION,
            "trace_id": "b",
            "a": False,
            "b": True,
        },
    ]
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[key] for key in grain), []).append(row)
    matches = {
        key
        for key, values in groups.items()
        if all(any(row[leaf] for row in values) for leaf in ("a", "b"))
    }
    assert ((PROJECT, SESSION) in matches) is has_second_leaf
    assert (OTHER_PROJECT, SESSION) not in matches


@pytest.mark.parametrize(
    ("last_state", "expected"),
    [
        ("live", False),
        ("removed", True),
        ("deleted", True),
        ("reassigned", True),
        ("other-project", True),
        ("wrong-type", True),
    ],
)
def test_null_fixture_ignores_only_nonlive_or_out_of_domain_witnesses(
    last_state, expected
):
    """Model physical latest replay plus the generated session null reducer."""
    builder = _builder(
        _filter("optional", operation="is_null"), _filter("company_id", "company")
    )
    plans, _ = builder._bounded_span_filter_parts()
    sql, _ = builder.build_filter_match_query([SESSION])
    membership = _cte(sql, "matching_scalar_sessions")
    assert plans[0].grouped_match_predicate() in membership
    grain = re.search(r"GROUP BY (.*?) HAVING", membership).group(1).split(", ")
    old_alias, new_alias = "old-session", "new-session"
    survivor = {old_alias: SESSION, new_alias: SESSION}
    rows = [
        {
            "project_id": PROJECT,
            "session_id": old_alias,
            "trace_id": "trace-a",
            "id": "root",
            "start": 1,
            "version": 1,
            "deleted": False,
            "optional": False,
            "company": True,
        },
        {
            "project_id": PROJECT,
            "session_id": new_alias,
            "trace_id": "trace-b",
            "id": "child",
            "start": 2,
            "version": 1,
            "deleted": False,
            "optional": True,
            "company": False,
        },
    ]
    update = {**rows[1], "version": 2}
    if last_state in {"removed", "wrong-type"}:
        update["optional"] = False  # No live key in the selected text domain.
    elif last_state == "deleted":
        update["deleted"] = True
    elif last_state == "reassigned":
        update["session_id"] = "different-session"
    elif last_state == "other-project":
        rows[1]["project_id"] = OTHER_PROJECT
        update["project_id"] = OTHER_PROJECT
    rows.append(update)
    latest = {}
    for row in rows:
        physical = tuple(row[key] for key in ("project_id", "trace_id", "id", "start"))
        if physical not in latest or latest[physical]["version"] < row["version"]:
            latest[physical] = row
    groups = {}
    for row in latest.values():
        if row["deleted"]:
            continue
        row = {**row, "session_id": survivor.get(row["session_id"], row["session_id"])}
        groups.setdefault(tuple(row[key] for key in grain), []).append(row)
    matching = {
        key
        for key, values in groups.items()
        if not any(row["optional"] for row in values)
        and any(row["company"] for row in values)
    }
    assert ((PROJECT, SESSION) in matching) is expected


def test_native_user_membership_and_raw_metric_intersect_without_value_collision():
    builder = _builder(
        _filter("end_user_id", [SESSION], operation="in", source="SYSTEM_METRIC"),
        _filter("total_tokens", "raw-token-value"),
        _filter(
            "total_tokens",
            5,
            kind="number",
            operation="greater_than",
            source="SYSTEM_METRIC",
        ),
    )
    sql, params = builder.build_filter_match_query([SESSION])
    assert "raw-token-value" in params.values()
    assert "matching_user_sessions AS" in sql
    assert "mapContains(attrs_string" in _cte(sql, "latest_candidate_scalar_spans")
    assert "sum(total_tokens) AS total_tokens" in _cte(sql, "sessions")
    assert "HAVING total_tokens >" in _cte(sql, "sessions")
    assert builder.supports_candidate_cursor_page()


@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
def test_raw_identity_null_does_not_invoke_native_presence_routing(operation):
    builder = _builder(_filter("end_user_id", operation=operation))
    sql, _ = builder.build_filter_match_query([SESSION])
    assert builder._user_null_filter_op() is None
    assert "matching_user_sessions AS" not in sql
    membership = _cte(sql, "matching_scalar_sessions")
    assert (
        f"countIf(latest_attr_exists_0) {'= 0' if operation == 'is_null' else '> 0'}"
        in membership
    )

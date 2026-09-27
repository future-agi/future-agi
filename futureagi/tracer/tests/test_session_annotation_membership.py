"""Offline contracts for session-only Score membership in the real V2 compiler.

These tests compile SQL; they do not execute ClickHouse, create ORM Scores, or
qualify UI behavior. They follow test_session_entity_filter_membership's bounded
classifier entry point. The categorical wire is TraceFilterPanel's ANNOTATION /
categorical / is / scalar contract, not an invented session-specific filter.

Runtime qualification still needs isolated CH25 spans, trace_session_id_remap,
and the versioned PeerDB model_hub_score table. Seed two root traces per session,
a session-only Score (trace/span references NULL), corrections of that SAME Score
id, application/CDC tombstones, old/new session aliases, and a foreign project's
colliding session/trace ids. Supply only authorized project ids to the builder;
authentication/PG label visibility are separate contracts. Assert actual result
ids before/after every mutation, then separately drive the real session drawer.
"""

import re
import socket
from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
SIBLING = "00000000-0000-4000-8000-000000000002"
SESSION = "00000000-0000-4000-8000-000000000003"
LABEL = "00000000-0000-4000-8000-000000000004"
SECOND_LABEL = "00000000-0000-4000-8000-000000000005"
FOREIGN_PROJECT = "00000000-0000-4000-8000-000000000006"
SESSION_CANDIDATES = "candidate_relational_session_traces"
END = datetime(2026, 9, 8)


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("session annotation compiler tests cannot use sockets")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _annotation(value="kept", *, operation="is"):
    return {
        "column_id": LABEL,
        "property_id": f"annotation:{LABEL}",
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "categorical",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _builder(*filters, projects=(PROJECT,), org=False):
    return SessionListQueryBuilderV2(
        **({"project_ids": list(projects)} if org else {"project_id": projects[0]}),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        (END - timedelta(days=7)).isoformat(),
                        END.isoformat(),
                    ],
                },
            },
            *filters,
        ],
        # Projectless labels need not occur in the legacy trace/span-only label
        # inventory. A per-label predicate must still reach its session Scores.
        annotation_label_ids=[],
        annotation_label_ids_by_project={p: [] for p in projects},
        eval_config_ids=[],
        bounded_internal_scan=True,
    )


def _groups(sql):
    """Balanced SQL groups, ignoring parentheses inside string literals."""
    stack = []
    for token in re.finditer(r"'(?:''|\\.|[^'\\])*'|[()]", sql):
        if token.group() == "(":
            stack.append(token.end())
        elif token.group() == ")":
            assert stack, "unbalanced generated SQL"
            yield " ".join(sql[stack.pop() : token.start()].split())
    assert not stack, "unbalanced generated SQL"


def _cte(sql, name):
    marker = re.search(rf"\b{re.escape(name)}\s+AS\s*\(", sql)
    assert marker, f"missing bounded session mapping CTE: {name}"
    return _enclosed(sql, marker.end() - 1)


def _enclosed(sql, opening):
    # Reuse the same quote-aware tokenization but stop at this group's closing
    # parenthesis; _groups on a suffix could include unrelated closing groups.
    depth = 0
    for token in re.finditer(r"'(?:''|\\.|[^'\\])*'|[()]", sql[opening:]):
        depth += (token.group() == "(") - (token.group() == ")")
        if token.group() == ")" and depth == 0:
            return " ".join(sql[opening + 1 : opening + token.start()].split())
    raise AssertionError("unclosed generated CTE")


def _scalar(sql, name):
    """Read a parenthesized scalar expression preceding ``AS name``."""
    stack = []
    for token in re.finditer(r"'(?:''|\\.|[^'\\])*'|[()]", sql):
        if token.group() == "(":
            stack.append(token.start())
        elif token.group() == ")":
            opening = stack.pop()
            if re.match(rf"\s+AS\s+{re.escape(name)}\b", sql[token.end() :]):
                return _enclosed(sql, opening)
    raise AssertionError(f"missing materialized scalar: {name}")


def _session_score_reads(sql):
    reads = []
    for group in _groups(sql):
        # Match a physical Score SELECT, not a containing session CTE that
        # happens to mention spans.trace_session_id elsewhere in its subtree.
        match = re.match(
            r"SELECT\b(?P<projection>.*?)\bFROM model_hub_score AS (?P<alias>\w+)\b",
            group,
        )
        if not match or re.search(r"\bFROM\b", match["projection"]):
            continue
        alias = match["alias"]
        if re.search(rf"\b{alias}\.trace_session_id\b", group):
            reads.append((group, alias))
    assert reads, (
        "session-only Score lookup is missing: generated Score SELECTs never "
        "read their own trace_session_id (a spans/session CTE is not sufficient)"
    )
    return reads


@pytest.mark.parametrize("org", [False, True], ids=["project", "organization"])
def test_session_only_annotation_has_native_score_lookup(org):
    builder = _builder(_annotation(), projects=(PROJECT, SIBLING), org=org)
    sql, params = builder.build_filter_match_query([SESSION])
    _session_score_reads(sql)
    assert builder._FILTER_BUILDER_CLS is ClickHouseFilterBuilderV2
    assert params["candidate_filter_session_id_array"] == [SESSION]
    assert params["bounded_match_limit"] == 1
    assert LABEL in params.values() and "kept" in params.values()
    assert "GROUP BY project_id, session_id" in _cte(
        sql, "matching_relational_sessions"
    )


@pytest.mark.parametrize("value", ["old-choice", "corrected-choice"])
def test_session_annotation_corrections_filter_latest_score_not_history(value):
    sql, params = _builder(_annotation(value)).build_filter_match_query([SESSION])
    for read, alias in _session_score_reads(sql):
        assert f"FROM model_hub_score AS {alias} FINAL" in read
        assert f"JSONExtract({alias}.value, 'selected', 'Array(String)')" in read
        assert "value_history" not in read
        # The relation's age is not the root/session time window. Old live
        # Scores must not disappear merely because their creation predates it.
        assert f"{alias}.created_at" not in read
        value_params = {name for name, bound in params.items() if bound == value}
        assert any(f"%({name})s" in read for name in value_params)
        assert value not in sql  # exact values remain bound data


@pytest.mark.parametrize("operation", ["is", "is_not_null", "is_null"])
def test_session_annotation_soft_delete_and_cdc_tombstone_follow_latest(operation):
    value = "kept" if operation == "is" else None
    sql, _ = _builder(_annotation(value, operation=operation)).build_filter_match_query(
        [SESSION]
    )
    for read, alias in _session_score_reads(sql):
        assert f"FROM model_hub_score AS {alias} FINAL" in read
        assert f"{alias}.deleted = false" in read
        assert f"{alias}._peerdb_is_deleted = 0" in read
        assert f"{alias}.is_deleted" not in read  # Score is NOT migrated spans


@pytest.mark.parametrize(
    "projects",
    [(PROJECT, SIBLING), (FOREIGN_PROJECT,)],
    ids=["tenant-a", "tenant-b"],
)
def test_session_score_join_is_project_correlated_and_candidate_bounded(projects):
    sql, params = _builder(
        _annotation(), projects=projects, org=True
    ).build_filter_match_query([SESSION])
    seen_projects = set()
    for read, alias in _session_score_reads(sql):
        join = re.search(
            rf"LEFT JOIN {SESSION_CANDIDATES}(?: AS)? (\w+) ON (.*?) WHERE", read
        )
        assert join, "direct session scores must use the finite candidate mapping"
        joined, on_clause = join.groups()
        for field in (
            f"{alias}.trace_session_id",
            f"{alias}.tracer_project_id",
            f"{joined}.project_id",
        ):
            assert field in on_clause
        # The same session UUID in a different project must not cross-match.
        guards = re.findall(
            rf"{alias}\.tracer_project_id = toUUID\(%\(([^)]+)\)s\)", read
        )
        assert guards, "every Score arm needs its authenticated project fence"
        seen_projects.update(params[name] for name in guards)
        assert "candidate_relational_trace_ids" in read
    assert seen_projects == set(projects)
    assert "(resolved_root_sessions.project_id, session_id) IN" in sql
    assert params["candidate_filter_session_id_array"] == [SESSION]


def test_session_score_candidates_include_survivor_and_remapped_any_ids():
    sql, params = _builder(_annotation()).build_filter_match_query([SESSION])
    mapping = _cte(sql, SESSION_CANDIDATES)
    assert "resolved_root_sessions" in mapping
    assert all(field in mapping for field in ("project_id", "trace_id", "session_id"))
    # Roots are survivor-keyed. Scores can still reference another old id or
    # the shared new id; mapping only the survivor loses those annotations.
    assert "any_id" in mapping and "survivor_id" in mapping
    assert "ts_survivor_map" in mapping
    # Deduplicate the whole mapping, including an alias equal to the survivor
    # already emitted by the first arm; DISTINCT within just one arm is weaker.
    assert "UNION DISTINCT" in mapping
    survivor_map = _cte(sql, "ts_survivor_map")
    assert "arrayJoin(candidate_session_pairs)" in survivor_map
    assert "trace_session_id_remap" not in survivor_map
    assert "trace_session_id_remap" not in mapping
    # The expensive remap reads belong to one scalar, not each table-CTE use.
    # Follow the real candidate IDs -> targeted groups -> scalar -> map chain.
    pairs = _scalar(sql, "candidate_session_pairs")
    assert "SELECT groupArray(tuple(any_id, survivor_id))" in pairs
    ids = _cte(pairs, "candidate_filter_ids")
    assert "%(candidate_filter_session_id_array)s" in ids
    assert params["candidate_filter_session_id_array"] == [SESSION]
    targets = _cte(pairs, "candidate_target_new_ids")
    assert "old_id IN ( SELECT candidate_id FROM candidate_filter_ids )" in targets
    assert "SELECT candidate_id AS new_id FROM candidate_filter_ids" in targets
    groups = _cte(pairs, "candidate_remap_groups")
    assert "SELECT new_id FROM candidate_target_new_ids" in groups
    assert "arrayDistinct" in groups
    assert "argMin(old_id, toString(old_id)) AS survivor_id" in groups
    assert "SELECT arrayJoin(group_ids) AS any_id, survivor_id" in pairs


def test_session_annotation_completeness_keeps_one_score_relation():
    filter_builder = ClickHouseFilterBuilderV2(
        table="spans",
        project_id=PROJECT,
        query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_TRACE,
        annotation_label_ids=[LABEL, SECOND_LABEL],
        annotation_label_set_known=True,
        candidate_ids_param="session_relational_trace_ids",
        score_date_scope=False,
        resolved_candidate_sessions_table=SESSION_CANDIDATES,
    )
    predicate, params = filter_builder.translate(
        [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
    )
    reads = _session_score_reads(predicate)
    assert len(reads) == 1
    read, alias = reads[0]
    assert "UNION" not in read
    assert f"GROUP BY entity_id HAVING uniqExact({alias}.label_id) >= 2" in read
    assert LABEL in params.values() and SECOND_LABEL in params.values()


@pytest.mark.parametrize("mode", ["trace", "span"])
def test_non_session_score_callers_do_not_opt_in(mode):
    builder = ClickHouseFilterBuilderV2(
        table="spans", project_id=PROJECT, query_mode=mode
    )
    sql, params = builder.translate([_annotation()])
    assert "model_hub_score" in sql
    assert "observation_span_id" in sql
    assert "s.trace_session_id" not in sql
    assert SESSION_CANDIDATES not in sql
    explicit_none = ClickHouseFilterBuilderV2(
        table="spans",
        project_id=PROJECT,
        query_mode=mode,
        resolved_candidate_sessions_table=None,
    )
    assert explicit_none.translate([_annotation()]) == (sql, params)


@pytest.mark.parametrize(
    "table", ["x; DROP TABLE spans", "x JOIN spans", "(SELECT 1)", "x.y"]
)
def test_session_candidate_table_rejects_request_sql(table):
    with pytest.raises(ValueError):
        ClickHouseFilterBuilderV2(
            table="spans",
            project_id=PROJECT,
            query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_TRACE,
            candidate_ids_param="session_relational_trace_ids",
            resolved_candidate_sessions_table=table,
        )


@pytest.mark.parametrize(
    "mode,candidate_param",
    [("span", "session_relational_trace_ids"), ("trace", None)],
    ids=["wrong-row-mode", "missing-finite-candidates"],
)
def test_session_candidate_opt_in_requires_trace_mode_and_finite_candidates(
    mode, candidate_param
):
    with pytest.raises(ValueError, match="bounded internal trace relation"):
        ClickHouseFilterBuilderV2(
            table="spans",
            project_id=PROJECT,
            query_mode=mode,
            candidate_ids_param=candidate_param,
            resolved_candidate_sessions_table=SESSION_CANDIDATES,
        )

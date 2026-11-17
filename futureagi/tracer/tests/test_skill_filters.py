"""SKILL correlation contracts. Execute only on the cloud test stack.

The integration cases use an isolated owned database and the production adapter,
not a Python approximation of the SQL's row or anti-membership semantics.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from rest_framework.exceptions import ValidationError

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import (
    EvalFilterMetadata,
    resolve_failed_skill_names,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_exact_graph_filter_predicates,
    partition_span_filter_plans,
    partition_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.filters import ClickHouseFilterBuilderV2
from tracer.services.clickhouse.v2.query_builders.trace_list import TraceListQueryBuilderV2
from tracer.tests._ch_seed import seed_ch_spans

START = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
SKILL = "22222222-2222-4222-8222-222222222222"
VERSION = "11111111-1111-4111-8111-111111111111"
OTHER_VERSION = "33333333-3333-4333-8333-333333333333"
PROJECT = "44444444-4444-4444-8444-444444444444"
DIGEST = "sha256:" + "a" * 64


def skill(column, value, op="equals", group=0):
    return {
        "column_id": "skill." + column,
        "group": group,
        "filter_config": {
            "col_type": "SKILL",
            "filter_type": "number" if column == "version" else "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def compile_filters(items, *, project=PROJECT, mode="trace", **kwargs):
    return ClickHouseFilterBuilderV2(
        project_id=project, query_mode=mode, **kwargs,
    ).translate(items)


@pytest.mark.parametrize("items", [
    [skill("version_id", VERSION)],
    [skill("id", SKILL), skill("version", 3)],
    [skill("name", "refund"), skill("version", [2, 3], "in")],
    [skill("id", SKILL), skill("version", 3, "gte")],
    [skill("load_result", "failed"), skill("name", "refund")],
    [skill("agent", "")],
    [skill("evidence", ["delivered", "agent_read", "declared"], "in")],
    [skill("binding_id", SKILL), skill("root_status", "error")],
])
def test_serializer_preserves_canonical_items_and_saved_view_round_trip(items):
    field = FilterListField()
    validated = field.run_validation(items)
    assert field.run_validation(json.loads(json.dumps(validated))) == items


@pytest.mark.parametrize("column,value", [
    ("version_id", VERSION), ("version", 3), ("digest", DIGEST),
    ("evidence", ["delivered"]), ("in_scope", VERSION), ("binding_id", SKILL),
])
def test_failed_groups_reject_identity_not_present_on_failures(column, value):
    items = [skill("load_result", "failed"), skill("name", "refund"),
             skill(column, value, "in" if column == "evidence" else "equals")]
    with pytest.raises(ValidationError):
        FilterListField().run_validation(items)
    with pytest.raises(UnsupportedFilterShapeError):
        compile_filters(items)


@pytest.mark.parametrize("items", [
    [skill("version", 3)],
    [skill("id", SKILL, group=1), skill("version", 3)],
    [skill("id", SKILL), skill("version", True)],
    [skill("id", SKILL), skill("version", "3")],
    [skill("id", SKILL), skill("version", 0)],
    [skill("id", SKILL), skill("version", 4294967296)],
    [skill("version_id", [], "in")],
    [skill("version_id", VERSION, group=True)],
    [skill("version_id", VERSION, group=-1)],
    [skill("version_id", VERSION, group=1.5)],
    [skill("load_result", "unknown")],
    [skill("load_result", "failed"), skill("load_result", "loaded")],
    [skill("root_status", "error")],
    [skill("evidence", ["used"], "in")],
    [skill("name", "x", "not_contains")],
    [skill("version_id); DROP TABLE spans", VERSION)],
])
def test_invalid_skill_shapes_are_rejected(items):
    with pytest.raises(ValidationError):
        FilterListField().run_validation(items)


def test_same_group_is_one_source_and_separate_groups_are_separate_sources():
    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    sql, params = compile_filters(items)
    assert sql.count("FROM skill_trace_links FINAL") == 1
    assert "skill_id = " in sql and "skill_version = " in sql and "agent_name = " in sql
    assert SKILL in params.values() and 3 in params.values() and "A" in params.values()
    sql, _ = compile_filters(items + [skill("name", "tone", group=1)])
    assert sql.count("FROM skill_trace_links FINAL") == 2


def test_negative_means_absence_and_values_are_bound():
    value = "x' OR 1 = 1 --"
    sql, params = compile_filters([skill("version_id", [value], "not_in")])
    assert "(toUUID(%(project_id)s), trace_id) NOT IN" in sql
    assert value not in sql and (value,) in params.values()
    assert "argMax(is_deleted, _version) = 0" in sql
    assert "span_id, _version) IN" in sql
    assert "start_time < %(end_date)s" in sql


def test_latest_state_partitions_keep_complete_group_as_residual():
    items = [skill("id", SKILL), skill("version", 3), skill("root_status", "error")]
    assert partition_trace_filter_plans(items) == ([], items)
    assert partition_span_filter_plans(items) == ([], items)
    with pytest.raises(UnsupportedFilterShapeError):
        partition_trace_filter_plans([skill("in_scope", VERSION)])


def test_in_scope_is_span_only_and_does_not_need_projected_array():
    with pytest.raises(UnsupportedFilterShapeError):
        compile_filters([skill("in_scope", VERSION)])
    sql, params = compile_filters([skill("in_scope", VERSION)], mode="span")
    assert "(toUUID(%(project_id)s), trace_id, id) IN" in sql
    assert "argMax(tuple(skill_version_ids, is_deleted), _version)" in sql
    assert "hasAny(scope_state.1" in sql
    assert (VERSION,) in params.values()


def test_exact_graph_does_not_compile_skill_leaves_independently():
    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    plan = compile_exact_graph_row_predicates(items, project_id=PROJECT, observe_type="trace")
    assert len(plan.predicates) == 1
    assert plan.predicates[0].count("FROM skill_trace_links FINAL") == 1
    assert plan.match_condition_groups == (((0, True),),)


def test_session_classifier_compiles_all_skill_leaves_in_one_trace_requirement():
    from tracer.services.clickhouse.v2.query_builders.session_list import SessionListQueryBuilderV2

    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    builder = SessionListQueryBuilderV2(project_id=PROJECT, filters=items)
    ctes, predicates, params = builder._bounded_relational_membership_plan(
        items, scope_to_request_window=True,
        available_params={"project_id": PROJECT, "start_date": START, "end_date": END},
    )
    assert len(predicates) == 1
    assert predicates[0].count("FROM skill_trace_links FINAL") == 1
    assert "candidate_relational_trace_ids" in ctes
    assert SKILL in params.values()


def test_project_scope_is_mandatory_and_org_membership_uses_composite_key():
    with pytest.raises(UnsupportedFilterShapeError):
        ClickHouseFilterBuilderV2().translate([skill("id", SKILL)])
    sql, _ = ClickHouseFilterBuilderV2(project_ids=[PROJECT]).translate([skill("id", SKILL)])
    assert "(project_id, trace_id) IN" in sql
    assert "project_id IN %(project_ids)s" in sql


def test_failed_id_resolution_retains_project_and_name_pairs(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.filters.resolve_failed_skill_names",
        lambda ids, projects: [(PROJECT, "refund")],
    )
    sql, params = compile_filters([skill("id", SKILL), skill("load_result", "failed")])
    assert "FROM skill_load_failures FINAL" in sql
    assert "(toString(project_id), skill_name) IN" in sql
    assert ((PROJECT, "refund"),) in params.values()


@pytest.mark.django_db
def test_failed_skill_names_are_resolved_only_in_project_workspace(project, workspace, organization):
    from accounts.models.workspace import Workspace
    from model_hub.models.skills_hub import HubSkill

    other_workspace = Workspace.objects.create(
        organization=organization, created_by=workspace.created_by, name="other-skill-filter-workspace",
    )
    own = HubSkill.no_workspace_objects.create(
        organization=organization, workspace=workspace, name="same-name",
    )
    foreign = HubSkill.no_workspace_objects.create(
        organization=organization, workspace=other_workspace, name="same-name",
    )
    assert resolve_failed_skill_names([str(foreign.id)], [str(project.id)]) == []
    assert resolve_failed_skill_names(["invalid", str(own.id)], [str(project.id)]) == [
        (str(project.id), "same-name"),
    ]


def test_eval_failure_filter_combines_without_changing_skill_group():
    eval_id, config_id = str(uuid4()), str(uuid4())
    eval_filter = {"column_id": eval_id, "filter_config": {
        "col_type": "EVAL_METRIC", "filter_type": "categorical",
        "filter_op": "equals", "filter_value": "FAIL",
    }}
    sql, params = compile_filters(
        [skill("id", SKILL), skill("version", 3), eval_filter],
        eval_filter_metadata={eval_id: EvalFilterMetadata((config_id,), "PASS_FAIL")},
    )
    assert sql.count("FROM skill_trace_links FINAL") == 1
    assert "eval" in sql and params
    # The pre-existing latency key is already root latency in trace lists.
    assert TraceListQueryBuilderV2.SORT_FIELD_MAP["latency"] == "latency_ms"


def test_users_fail_explicitly_until_group_preserving_reader_exists():
    from tracer.services.clickhouse.v2.query_builders.user_list import UserListQueryBuilderV2

    with pytest.raises(UnsupportedFilterShapeError, match="Users"):
        UserListQueryBuilderV2(organization_id=str(uuid4()), project_id=PROJECT, filters=[skill("id", SKILL)])


@pytest.fixture(scope="module")
def skill_query_client():
    from conftest import _ch_test_apply_v2_schema, _ch_test_owned_database, _open_ch_test_http_client

    with _ch_test_owned_database("test_skill_filters_") as database:
        _ch_test_apply_v2_schema(database)
        client = _open_ch_test_http_client(database=database)
        try:
            yield client
        finally:
            client.close()


def trace_id(label):
    # Migration 015 derives trace_name through a UUID-keyed dictionary.
    # Keep labels readable in tests while inserting production-shaped IDs.
    return str(uuid5(NAMESPACE_URL, "skill-filter-test:" + label))


def trace_ids(*labels):
    return {trace_id(label) for label in labels}


def span_row(project, trace, *, version=VERSION, agent="A", **overrides):
    return {
        "project_id": project, "trace_id": trace_id(trace), "id": uuid4().hex[:16],
        "parent_span_id": "root", "name": "skill.load", "status": "OK",
        "observation_type": "unknown", "service_name": "support",
        "start_time": START, "end_time": START + timedelta(milliseconds=5),
        "created_at": START, "updated_at": START,
        "span_attributes": {
            "fi.skill.id": SKILL, "fi.skill.version_id": version,
            "fi.skill.version": 3 if version == VERSION else 2,
            "fi.skill.name": "refund", "fi.skill.digest": DIGEST,
            "fi.skill.load.evidence": "delivered", "gen_ai.agent.name": agent,
            "fi.context.binding_id": SKILL, "fi.skills.version_ids": [version],
        }, **overrides,
    }


def insert_rows(client, rows):
    seed_ch_spans(rows, client=client, version_from_created_at=True)


def matching_traces(client, project, filters):
    predicate, params = compile_filters(filters, project=project)
    result = client.query(
        "SELECT DISTINCT trace_id FROM spans FINAL WHERE project_id = %(project_id)s "
        "AND is_deleted = 0 AND start_time >= %(start_date)s AND start_time < %(end_date)s "
        "AND (" + predicate + ")",
        parameters={**params, "project_id": project, "start_date": START, "end_date": END},
    )
    return {row[0] for row in result.result_rows}


@pytest.mark.integration
def test_live_correlation_negative_groups_failed_and_root_status(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    failure = span_row(project, "mixed", name="skill.load", status="ERROR", span_attributes={
        "fi.skill.name": "refund", "error.type": "integrity", "gen_ai.agent.name": "A",
    })
    rows = [
        span_row(project, "cross", version=OTHER_VERSION, agent="A"),
        span_row(project, "cross", agent="B"),
        span_row(project, "match"), span_row(project, "mixed"), failure,
        span_row(project, "unrecorded", name="llm.chat", span_attributes={}),
        span_row(project, "match", name="request", parent_span_id="", status="ERROR", span_attributes={}),
        span_row(project, "cross", name="request", parent_span_id="", status="OK", span_attributes={}),
        span_row(str(uuid4()), "unrecorded"),  # same id, another project
    ]
    insert_rows(client, rows)
    selected = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    assert matching_traces(client, project, selected) == trace_ids("match", "mixed")
    assert matching_traces(client, project, selected + [skill("root_status", "error")]) == trace_ids("match")
    assert matching_traces(client, project, [skill("version_id", [VERSION], "not_in")]) == trace_ids("unrecorded")
    assert matching_traces(client, project, [skill("version_id", VERSION), skill("version_id", OTHER_VERSION, group=1)]) == trace_ids("cross")
    assert matching_traces(client, project, selected + [skill("load_result", "failed", group=1), skill("name", "refund", group=1)]) == trace_ids("mixed")
    assert matching_traces(client, project, [skill("binding_id", SKILL)]) == trace_ids("cross", "match", "mixed")


@pytest.mark.integration
def test_live_bounded_trace_and_span_readers_keep_group_correlation(skill_query_client):
    from tracer.services.clickhouse.v2.query_builders.span_list import SpanListQueryBuilderV2

    client = skill_query_client
    project = str(uuid4())
    matching = span_row(project, "matched")
    cross_a = span_row(project, "cross", version=OTHER_VERSION)
    cross_b = span_row(project, "cross", agent="B")
    root_match = span_row(project, "matched", name="request", parent_span_id="", span_attributes={})
    root_cross = span_row(project, "cross", name="request", parent_span_id="", span_attributes={})
    insert_rows(client, [matching, cross_a, cross_b, root_match, root_cross])
    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A"), {
        "column_id": "created_at", "filter_config": {
            "filter_type": "datetime", "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }]
    for builder, candidates in (
        (TraceListQueryBuilderV2(project_id=project, filters=items), sorted(trace_ids("matched", "cross"))),
        (TraceListQueryBuilderV2(project_id=project, filters=items, bounded_identity_only=True), sorted(trace_ids("matched", "cross"))),
        (SpanListQueryBuilderV2(project_id=project, filters=items), [matching["id"], cross_b["id"]]),
    ):
        sql, params = builder.build_filter_match_query(candidates)
        result = list(client.query(sql, parameters=params).named_results())
        assert len(result) == 1
        assert str(result[0]["trace_id"]) == trace_id("matched")


@pytest.mark.integration
def test_live_replacement_and_key_only_attribute_tombstone(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    row = span_row(project, "replayed")
    insert_rows(client, [row, row])
    assert matching_traces(client, project, [skill("version_id", VERSION)]) == trace_ids("replayed")
    correction = {**row, "created_at": START + timedelta(seconds=1), "span_attributes": {
        **row["span_attributes"], "fi.skill.version_id": OTHER_VERSION, "fi.skill.version": 2,
    }}
    insert_rows(client, [correction])
    assert matching_traces(client, project, [skill("version_id", VERSION)]) == set()
    assert matching_traces(client, project, [skill("version_id", OTHER_VERSION)]) == trace_ids("replayed")
    # No load attributes: neither MV receives a tombstone. The reader's live
    # span-version fence must still remove the stale narrow-table records.
    insert_rows(client, [{**correction, "created_at": START + timedelta(seconds=2),
                          "deleted": True, "span_attributes": {}}])
    assert matching_traces(client, project, [skill("version_id", OTHER_VERSION)]) == set()


@pytest.mark.integration
def test_live_span_scope_and_legacy_array_filter(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    stamped = span_row(project, "scope", name="llm.chat")
    early = span_row(project, "scope", name="early", span_attributes={})
    root = span_row(project, "scope", name="request", parent_span_id="", span_attributes={})
    insert_rows(client, [stamped, early, root])
    raw_array = {"column_id": "fi.skills.version_ids", "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": "array",
        "filter_op": "contains", "filter_value": [VERSION],
    }}
    assert FilterListField().run_validation([raw_array]) == [raw_array]
    for items in ([skill("in_scope", VERSION)], [raw_array]):
        predicate, params = compile_exact_graph_filter_predicates(
            items, project_id=project, observe_type="span",
        )
        result = client.query(
            "SELECT id FROM spans FINAL WHERE project_id = %(project_id)s AND is_deleted = 0 AND ("
            + predicate + ")",
            parameters={**params, "project_id": project, "start_date": START, "end_date": END},
        )
        assert {row[0] for row in result.result_rows} == {stamped["id"]}
    # V10 specifically requires the serializer -> trace-list path, not just
    # the span predicate used by the generic exact graph compiler.
    trace_filters = FilterListField().run_validation([raw_array, {
        "column_id": "created_at", "filter_config": {
            "filter_type": "datetime", "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }])
    sql, params = TraceListQueryBuilderV2(
        project_id=project, filters=trace_filters,
    ).build_filter_match_query([trace_id("scope")])
    rows = list(client.query(sql, parameters=params).named_results())
    assert len(rows) == 1 and str(rows[0]["trace_id"]) == trace_id("scope")

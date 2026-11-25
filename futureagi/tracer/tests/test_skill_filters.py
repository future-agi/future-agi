"""SKILL correlation contracts. Execute only on the cloud test stack.

The integration cases use an isolated owned database and the production adapter,
not a Python approximation of the SQL's row or anti-membership semantics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from rest_framework.exceptions import ValidationError

from tracer.serializers.filters import (
    FilterListField,
    filter_list_field,
    skill_filter_list_field,
)
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import (
    SKILL_VERSION_IDS_SQL,
    EvalFilterMetadata,
    SkillEvidenceWindow,
    resolve_failed_skill_names,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_exact_graph_filter_predicates,
    partition_span_filter_plans,
    partition_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests._ch_seed import seed_ch_spans

START = datetime(2026, 11, 1, 12, tzinfo=UTC)
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


def test_shared_field_requires_opt_in_for_skill_groups():
    plain = filter_list_field()
    extended = skill_filter_list_field()
    payload = [skill("version_id", VERSION)]
    assert "group" not in plain.Meta.swagger_schema_fields["items"]["properties"]
    with pytest.raises(ValidationError, match="Unknown filter item keys: group"):
        plain.run_validation(payload)
    assert "group" in extended.Meta.swagger_schema_fields["items"]["properties"]
    assert extended.run_validation(payload) == payload


def compile_filters(items, *, project=PROJECT, mode="trace", **kwargs):
    return ClickHouseFilterBuilderV2(
        project_id=project,
        query_mode=mode,
        **kwargs,
    ).translate(items)


def assert_skill_span_source(sql, *, groups=1, failed=False):
    sql = " ".join(sql.split())
    assert sql.count("skill_load_keys AS (") == groups
    assert sql.count("AND name = 'skill.load'") == groups
    assert "argMax(attrs_string['fi.skill.name'], _version) AS skill_name" in sql
    assert "argMax(is_deleted, _version) AS latest_deleted" in sql
    assert "WHERE latest_deleted = 0 AND latest_name = 'skill.load'" in sql
    outcome = (
        "status = 'ERROR' AND skill_version_id = ''"
        if failed
        else "skill_version_id != '' AND status != 'ERROR'"
    )
    assert outcome in sql


@pytest.mark.parametrize(
    "items",
    [
        [skill("version_id", VERSION)],
        [skill("id", SKILL), skill("version", 3)],
        [skill("name", "refund"), skill("version", [2, 3], "in")],
        [skill("id", SKILL), skill("version", 3, "gte")],
        [skill("load_result", "failed"), skill("name", "refund")],
        [skill("agent", "")],
        [skill("evidence", ["delivered", "agent_read", "declared"], "in")],
        [skill("binding_id", SKILL), skill("root_status", "error")],
    ],
)
def test_serializer_preserves_canonical_items_and_saved_view_round_trip(items):
    field = FilterListField()
    validated = field.run_validation(items)
    assert field.run_validation(json.loads(json.dumps(validated))) == items


@pytest.mark.parametrize(
    "column,value",
    [
        ("version_id", VERSION),
        ("version", 3),
        ("digest", DIGEST),
        ("evidence", ["delivered"]),
        ("in_scope", VERSION),
        ("binding_id", SKILL),
    ],
)
def test_failed_groups_reject_identity_not_present_on_failures(column, value):
    items = [
        skill("load_result", "failed"),
        skill("name", "refund"),
        skill(column, value, "in" if column == "evidence" else "equals"),
    ]
    with pytest.raises(ValidationError):
        FilterListField().run_validation(items)
    with pytest.raises(UnsupportedFilterShapeError):
        compile_filters(items)


@pytest.mark.parametrize(
    "items",
    [
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
    ],
)
def test_invalid_skill_shapes_are_rejected(items):
    with pytest.raises(ValidationError):
        FilterListField().run_validation(items)


def test_same_group_is_one_source_and_separate_groups_are_separate_sources():
    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    sql, params = compile_filters(items)
    assert_skill_span_source(sql)
    assert "skill_id = " in sql and "skill_version = " in sql and "agent_name = " in sql
    assert SKILL in params.values() and 3 in params.values() and "A" in params.values()
    sql, _ = compile_filters(items + [skill("name", "tone", group=1)])
    assert_skill_span_source(sql, groups=2)


def test_negative_means_absence_and_values_are_bound():
    value = "x' OR 1 = 1 --"
    sql, params = compile_filters([skill("version_id", [value], "not_in")])
    assert "(toUUID(%(project_id)s), trace_id) NOT IN" in sql
    assert value not in sql and (value,) in params.values()
    assert_skill_span_source(sql)
    assert "(project_id, trace_id, id) IN" in sql
    assert "start_time < %(end_date)s" in sql


def test_latest_state_partitions_keep_complete_group_as_residual():
    items = [skill("id", SKILL), skill("version", 3), skill("root_status", "error")]
    assert partition_trace_filter_plans(items) == ([], items)
    assert partition_span_filter_plans(items) == ([], items)
    with pytest.raises(UnsupportedFilterShapeError):
        partition_trace_filter_plans([skill("in_scope", VERSION)])


def test_skill_filters_with_a_metric_identity_are_rejected_by_the_compiler():
    # Direct compiler callers must not route a metric identity around the
    # metric family's own validation by labelling it SKILL.
    for key in ("property_id", "column_id"):
        item = skill("name", "refund")
        item[key] = "metric_field:11111111-1111-4111-8111-111111111111"
        with pytest.raises(UnsupportedFilterShapeError, match="metric field identity"):
            compile_filters([item])


def test_only_sampled_skill_filters_reference_sampling_source():
    sampled, _ = compile_filters([skill("name", "refund")], table="sampling_source")
    live, _ = compile_filters([skill("name", "refund")])
    assert "(project_id, trace_id, span_id, live_version) IN" in sampled
    assert "SELECT project_id, trace_id, id, _version FROM sampling_source" in sampled
    assert "FROM dependency_source" in sampled
    assert "FROM sampling_source" not in live and "FROM dependency_source" not in live


def test_in_scope_is_span_only_and_does_not_need_projected_array():
    with pytest.raises(UnsupportedFilterShapeError):
        compile_filters([skill("in_scope", VERSION)])
    sql, params = compile_filters([skill("in_scope", VERSION)], mode="span")
    assert "(toUUID(%(project_id)s), trace_id, id) IN" in sql
    assert (
        f"argMax(tuple({SKILL_VERSION_IDS_SQL}, is_deleted, rev.start_time), _version)"
        in sql
    )
    assert "skill_version_ids" not in sql
    assert "hasAny(scope_state.1" in sql
    assert [VERSION] in params.values()
    _, membership = compile_filters(
        [skill("in_scope", [VERSION, OTHER_VERSION], op="in")], mode="span"
    )
    assert [VERSION, OTHER_VERSION] in membership.values()


def test_exact_graph_does_not_compile_skill_leaves_independently():
    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    plan = compile_exact_graph_row_predicates(
        items, project_id=PROJECT, observe_type="trace"
    )
    assert len(plan.predicates) == 1
    assert_skill_span_source(plan.predicates[0])
    assert plan.match_condition_groups == (((0, True),),)


def test_session_classifier_compiles_all_skill_leaves_in_one_trace_requirement():
    from tracer.services.clickhouse.v2.query_builders.session_list import (
        SessionListQueryBuilderV2,
    )

    items = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    builder = SessionListQueryBuilderV2(project_id=PROJECT, filters=items)
    ctes, predicates, params = builder._bounded_relational_membership_plan(
        items,
        scope_to_request_window=True,
        available_params={"project_id": PROJECT, "start_date": START, "end_date": END},
    )
    assert len(predicates) == 1
    assert_skill_span_source(predicates[0])
    assert "candidate_relational_trace_ids" in ctes
    assert SKILL in params.values()


def test_project_scope_is_mandatory_and_org_membership_uses_composite_key():
    with pytest.raises(UnsupportedFilterShapeError):
        ClickHouseFilterBuilderV2().translate([skill("id", SKILL)])
    sql, _ = ClickHouseFilterBuilderV2(project_ids=[PROJECT]).translate(
        [skill("id", SKILL)]
    )
    assert "(project_id, trace_id) IN" in sql
    assert "project_id IN %(project_ids)s" in sql


def test_failed_id_resolution_retains_project_and_name_pairs(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.filters.resolve_failed_skill_names",
        lambda ids, projects: [(PROJECT, "refund")],
    )
    sql, params = compile_filters([skill("id", SKILL), skill("load_result", "failed")])
    assert_skill_span_source(sql, failed=True)
    assert "(toString(project_id), skill_name) IN" in sql
    assert ((PROJECT, "refund"),) in params.values()


@pytest.mark.django_db
def test_failed_skill_names_are_resolved_only_in_project_workspace(
    project, workspace, organization
):
    from accounts.models.workspace import Workspace
    from model_hub.models.skills_hub import HubSkill

    other_workspace = Workspace.objects.create(
        organization=organization,
        created_by=workspace.created_by,
        name="other-skill-filter-workspace",
    )
    own = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="same-name",
    )
    foreign = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=other_workspace,
        name="same-name",
    )
    assert resolve_failed_skill_names([str(foreign.id)], [str(project.id)]) == []
    assert resolve_failed_skill_names(["invalid", str(own.id)], [str(project.id)]) == [
        (str(project.id), "same-name"),
    ]


@pytest.mark.parametrize("include_attribute", [False, True])
def test_eval_and_skill_filters_compile_with_optional_span_attribute(include_attribute):
    eval_id, config_id = str(uuid4()), str(uuid4())
    eval_filter = {
        "column_id": eval_id,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "categorical",
            "filter_op": "equals",
            "filter_value": "FAIL",
        },
    }
    attr_filter = {
        "column_id": "fi.skill.name",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "refund",
        },
    }
    items = [skill("id", SKILL), skill("version", 3), eval_filter]
    if include_attribute:
        items.append(attr_filter)
    sql, params = compile_filters(
        items,
        eval_filter_metadata={eval_id: EvalFilterMetadata((config_id,), "PASS_FAIL")},
    )
    assert_skill_span_source(sql)
    assert "output_bool IN" in sql and (config_id,) in params.values()
    assert (0,) in params.values()
    if include_attribute:
        (key,) = [p for p, value in params.items() if value == "fi.skill.name"]
        assert f"mapContains(attrs_string, %({key})s)" in sql
        assert "refund" in params.values()
    assert "skill_id = " in sql and "skill_version = " in sql
    # The pre-existing latency key is already root latency in trace lists.
    assert TraceListQueryBuilderV2.SORT_FIELD_MAP["latency"] == "latency_ms"


def test_users_fail_explicitly_until_group_preserving_reader_exists():
    from tracer.services.clickhouse.v2.query_builders.user_list import (
        UserListQueryBuilderV2,
    )

    with pytest.raises(UnsupportedFilterShapeError, match="Users"):
        UserListQueryBuilderV2(
            organization_id=str(uuid4()),
            project_id=PROJECT,
            filters=[skill("id", SKILL)],
        )


@pytest.fixture(scope="module")
def skill_query_client():
    from conftest import (
        _ch_test_apply_v2_schema,
        _ch_test_owned_database,
        _open_ch_test_http_client,
    )

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
        "project_id": project,
        "trace_id": trace_id(trace),
        "id": uuid4().hex[:16],
        "parent_span_id": "root",
        "name": "skill.load",
        "status": "OK",
        "observation_type": "unknown",
        "service_name": "support",
        "start_time": START,
        "end_time": START + timedelta(milliseconds=5),
        "created_at": START,
        "updated_at": START,
        "span_attributes": {
            "fi.skill.id": SKILL,
            "fi.skill.version_id": version,
            "fi.skill.version": 3 if version == VERSION else 2,
            "fi.skill.name": "refund",
            "fi.skill.digest": DIGEST,
            "fi.skill.load.evidence": "delivered",
            "gen_ai.agent.name": agent,
            "fi.context.binding_id": SKILL,
            "fi.skills.version_ids": [version],
        },
        **overrides,
    }


def insert_rows(client, rows):
    seed_ch_spans(rows, client=client, version_from_created_at=True)


def matching_traces(client, project, filters):
    predicate, params = compile_filters(filters, project=project)
    result = client.query(
        "SELECT DISTINCT trace_id FROM spans FINAL WHERE project_id = %(project_id)s "
        "AND is_deleted = 0 AND start_time >= %(start_date)s AND start_time < %(end_date)s "
        "AND (" + predicate + ")",
        parameters={
            **params,
            "project_id": project,
            "start_date": START,
            "end_date": END,
        },
    )
    return {row[0] for row in result.result_rows}


@pytest.mark.integration
def test_closed_snapshot_excludes_late_skill_revisions_and_new_loads(
    skill_query_client,
):
    from tracer.models.eval_task import EvalTask
    from tracer.services.eval_tasks.sampling.policy import validate_policy
    from tracer.services.eval_tasks.sampling.query import READ_SETTINGS, SamplingQuery

    project = str(uuid4())
    root, late_root = (
        span_row(project, label, name="request", parent_span_id="", span_attributes={})
        for label in ("revised", "late-child")
    )
    admitted = span_row(
        project,
        "revised",
        parent_span_id=root["id"],
        resource_attributes={"service.name": "original"},
    )
    dead_root = span_row(
        project, "deleted", name="root", parent_span_id="", span_attributes={}
    )
    deleted = span_row(project, "deleted", parent_span_id=dead_root["id"])
    # Tombstones are not in the live sampling_source. The election must still
    # see a deletion *before* the cutoff, even under another sorting key.
    tombstone = {
        **deleted,
        "name": "",
        "span_attributes": {},
        "deleted": True,
        "resource_attributes": {"service.name": "delete-writer"},
        "created_at": START + timedelta(seconds=1),
    }
    insert_rows(
        skill_query_client, [root, late_root, admitted, dead_root, deleted, tombstone]
    )
    policy = validate_policy(
        {
            "version": 1,
            "mode": "closed",
            "sampling_unit": "trace",
            "window": {"start": START.isoformat(), "end": END.isoformat()},
        },
        row_type="traces",
        now=END,
    )

    def closed_matches(version):
        task = EvalTask(
            project_id=project,
            row_type="traces",
            filters={"filters": [skill("version_id", version)]},
        )
        sql, params = SamplingQuery(task, policy, "v1", END).rows()
        result = skill_query_client.query(
            sql, parameters=params, settings=READ_SETTINGS
        )
        return {row[0] for row in result.result_rows}

    assert closed_matches(VERSION) == trace_ids("revised")
    late = END + timedelta(seconds=1)
    # A service change leaves the admitted revision under its original sorting key.
    correction = {
        **admitted,
        "resource_attributes": {"service.name": "corrected"},
        "created_at": late,
        "span_attributes": {
            **admitted["span_attributes"],
            "fi.skill.version_id": OTHER_VERSION,
        },
    }
    new_child = span_row(
        project,
        "late-child",
        parent_span_id=late_root["id"],
        version=OTHER_VERSION,
        created_at=late,
    )
    insert_rows(skill_query_client, [correction, new_child])
    assert skill_query_client.query(
        "SELECT service_name FROM spans FINAL WHERE project_id = %(project)s "
        "AND trace_id = %(trace)s AND id = %(id)s ORDER BY _version",
        parameters={
            "project": project,
            "trace": admitted["trace_id"],
            "id": admitted["id"],
        },
    ).result_rows == [("original",), ("corrected",)]
    assert matching_traces(
        skill_query_client, project, [skill("version_id", OTHER_VERSION)]
    ) == trace_ids("revised", "late-child")
    assert closed_matches(OTHER_VERSION) == set()
    assert closed_matches(VERSION) == trace_ids("revised")


@pytest.mark.integration
def test_live_correlation_negative_groups_failed_and_root_status(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    failure = span_row(
        project,
        "mixed",
        name="skill.load",
        status="ERROR",
        span_attributes={
            "fi.skill.name": "refund",
            "error.type": "integrity",
            "gen_ai.agent.name": "A",
        },
    )
    rows = [
        span_row(project, "cross", version=OTHER_VERSION, agent="A"),
        span_row(project, "cross", agent="B"),
        span_row(project, "match"),
        span_row(project, "mixed"),
        failure,
        span_row(project, "unrecorded", name="llm.chat", span_attributes={}),
        span_row(
            project,
            "match",
            name="request",
            parent_span_id="",
            status="ERROR",
            span_attributes={},
        ),
        span_row(
            project,
            "cross",
            name="request",
            parent_span_id="",
            status="OK",
            span_attributes={},
        ),
        span_row(str(uuid4()), "unrecorded"),  # same id, another project
    ]
    insert_rows(client, rows)
    selected = [skill("id", SKILL), skill("version", 3), skill("agent", "A")]
    assert matching_traces(client, project, selected) == trace_ids("match", "mixed")
    assert matching_traces(
        client, project, selected + [skill("root_status", "error")]
    ) == trace_ids("match")
    assert matching_traces(
        client, project, [skill("version_id", [VERSION], "not_in")]
    ) == trace_ids("unrecorded")
    assert matching_traces(
        client,
        project,
        [skill("version_id", VERSION), skill("version_id", OTHER_VERSION, group=1)],
    ) == trace_ids("cross")
    assert matching_traces(
        client,
        project,
        selected
        + [skill("load_result", "failed", group=1), skill("name", "refund", group=1)],
    ) == trace_ids("mixed")
    assert matching_traces(client, project, [skill("binding_id", SKILL)]) == trace_ids(
        "cross", "match", "mixed"
    )


@pytest.mark.integration
def test_summary_requires_catalog_identity_only_for_loaded_groups(skill_query_client):
    from tracer.services.clickhouse.v2.query_builders.trace_skills import (
        TraceSkillsQueryBuilder,
    )

    project = str(uuid4())
    rows = [span_row(project, label) for label in ("verified", "both", "wrong-digest")]
    rows[-1]["span_attributes"]["fi.skill.digest"] = "unknown"
    for label in ("both", "wrong-digest", "failed-only"):
        rows.append(
            span_row(
                project,
                label,
                status="ERROR",
                span_attributes={"fi.skill.name": "refund", "error.type": "integrity"},
            )
        )
    insert_rows(skill_query_client, rows)
    query = TraceSkillsQueryBuilder(project_ids=[project], start=START, end=END)
    verified = [(SKILL, VERSION, DIGEST)]
    loaded = [skill("version_id", VERSION)]
    failed = [skill("load_result", "failed", group=1), skill("name", "refund", group=1)]

    def trace_count(filters, identities):
        sql, params = query.build_summary(
            filters=filters, verified_identities=identities
        )
        (row,) = skill_query_client.query(sql, parameters=params).named_results()
        return row["traces"]

    assert trace_count(loaded, verified) == 2
    assert trace_count(loaded + failed, verified) == 1
    assert trace_count(failed, []) == 3


@pytest.mark.integration
def test_root_status_matches_runtime_canonical_root_across_window_and_replays(
    skill_query_client,
):
    from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
        SkillRuntimeQueryBuilder,
    )

    client = skill_query_client
    project = str(uuid4())
    early = START - timedelta(minutes=5)
    labels = (
        "early-error",
        "early-ok",
        "tie",
        "deleted-root",
        "corrected-root",
        "missing-root",
    )

    def root(trace, status, **kwargs):
        return span_row(
            project,
            trace,
            name="request",
            parent_span_id="",
            status=status,
            span_attributes={},
            **kwargs,
        )

    deleted = root("deleted-root", "ERROR", start_time=early)
    corrected = root("corrected-root", "ERROR", start_time=early)
    rows = [span_row(project, label) for label in labels] + [
        root("early-error", "ERROR", start_time=early),
        root("early-error", "OK"),
        root("early-ok", "OK", start_time=early),
        root("early-ok", "ERROR"),
        root("tie", "OK", id="0000000000000001"),
        root("tie", "ERROR", id="0000000000000002"),
        deleted,
        {**deleted, "deleted": True, "created_at": END, "span_attributes": {}},
        root("deleted-root", "OK"),
        corrected,
        {**corrected, "status": "OK", "created_at": END},
        # A foreign root for the missing-root trace must not complete it.
        {**root("missing-root", "ERROR"), "project_id": str(uuid4())},
    ]
    insert_rows(client, rows)
    selected = [skill("version_id", VERSION)]
    failed = matching_traces(
        client, project, selected + [skill("root_status", "error")]
    )
    ok = matching_traces(client, project, selected + [skill("root_status", "ok")])
    assert failed == trace_ids("early-error")
    assert ok == trace_ids("early-ok", "tie", "deleted-root", "corrected-root")
    sql, params = SkillRuntimeQueryBuilder(
        project_ids=[project], start=START, end=END
    ).build_cohort_metrics(
        skill_id=SKILL,
        version_id=VERSION,
        digest=DIGEST,
    )
    (metrics,) = client.query(sql, parameters=params).named_results()
    assert metrics["traces"] == len(labels)
    assert metrics["incomplete"] == 1
    assert metrics["failed_count"] == len(failed)
    assert metrics["failed_rate"] == len(failed) / (len(failed) + len(ok))


@pytest.mark.integration
def test_live_bounded_trace_and_span_readers_keep_group_correlation(skill_query_client):
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    client = skill_query_client
    project = str(uuid4())
    matching = span_row(project, "matched")
    cross_a = span_row(project, "cross", version=OTHER_VERSION)
    cross_b = span_row(project, "cross", agent="B")
    root_match = span_row(
        project, "matched", name="request", parent_span_id="", span_attributes={}
    )
    root_cross = span_row(
        project, "cross", name="request", parent_span_id="", span_attributes={}
    )
    for root in (root_match, root_cross):
        root["resource_attributes"] = {"service.name": root["service_name"]}
    insert_rows(client, [matching, cross_a, cross_b, root_match, root_cross])
    items = [
        skill("id", SKILL),
        skill("version", 3),
        skill("agent", "A"),
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        },
    ]
    for builder, candidates in (
        (
            TraceListQueryBuilderV2(project_id=project, filters=items),
            sorted(trace_ids("matched", "cross")),
        ),
        (
            TraceListQueryBuilderV2(
                project_id=project, filters=items, bounded_identity_only=True
            ),
            sorted(trace_ids("matched", "cross")),
        ),
        (
            SpanListQueryBuilderV2(project_id=project, filters=items),
            [matching["id"], cross_b["id"]],
        ),
        (
            SpanListQueryBuilderV2(project_id=project, filters=items),
            [root_match["id"], root_cross["id"]],
        ),
    ):
        sql, params = builder.build_filter_match_query(candidates)
        result = list(client.query(sql, parameters=params).named_results())
        assert len(result) == 1
        assert str(result[0]["trace_id"]) == trace_id("matched")
        if isinstance(builder, SpanListQueryBuilderV2):
            assert result[0]["id"] == candidates[0]

    # Production uses the seed-row path, not just plain span IDs. The inner
    # skill keys must be trace-limited even though the candidates are roots.
    sql, params = SpanListQueryBuilderV2(
        project_id=project, filters=items
    ).build_filter_match_query_from_seed_rows([root_match, root_cross])
    keys = sql.split("skill_load_keys AS (", 1)[1].split("), skill_load_days AS", 1)[0]
    assert "FROM resolved_annotation_candidates" in keys
    assert "(toString(trace_id), toString(id)) IN %(candidate_span_entities)s" in keys
    assert set(params["candidate_span_entities"]) == {
        (root["trace_id"], root["id"]) for root in (root_match, root_cross)
    }
    assert {
        (row["trace_id"], row["id"])
        for row in client.query(sql, parameters=params).named_results()
    } == {(root_match["trace_id"], root_match["id"])}


@pytest.mark.integration
def test_live_replacement_and_key_only_attribute_tombstone(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    row = span_row(project, "replayed")
    # Keep a live candidate even after the load is deleted.
    root = span_row(
        project, "replayed", name="request", parent_span_id="", span_attributes={}
    )
    insert_rows(client, [root, row, row])
    assert matching_traces(
        client, project, [skill("version_id", VERSION)]
    ) == trace_ids("replayed")
    correction = {
        **row,
        "created_at": START + timedelta(seconds=1),
        "span_attributes": {
            **row["span_attributes"],
            "fi.skill.version_id": OTHER_VERSION,
            "fi.skill.version": 2,
        },
    }
    insert_rows(client, [correction])
    assert matching_traces(client, project, [skill("version_id", VERSION)]) == set()
    assert matching_traces(
        client, project, [skill("version_id", OTHER_VERSION)]
    ) == trace_ids("replayed")
    insert_rows(
        client,
        [
            {
                **correction,
                "created_at": START + timedelta(seconds=2),
                "deleted": True,
                "name": "",
                "span_attributes": {},
            }
        ],
    )
    assert (
        matching_traces(client, project, [skill("version_id", OTHER_VERSION)]) == set()
    )


@pytest.mark.integration
def test_live_span_scope_and_legacy_array_filter(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    stamped = span_row(project, "scope", name="llm.chat")
    early = span_row(project, "scope", name="early", span_attributes={})
    root = span_row(
        project, "scope", name="request", parent_span_id="", span_attributes={}
    )
    insert_rows(client, [stamped, early, root])
    raw_array = {
        "column_id": "fi.skills.version_ids",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "array",
            "filter_op": "contains",
            "filter_value": [VERSION],
        },
    }
    assert FilterListField().run_validation([raw_array]) == [raw_array]
    for items in (
        [skill("in_scope", VERSION)],
        [skill("in_scope", [VERSION, OTHER_VERSION], op="in")],
        [raw_array],
    ):
        predicate, params = compile_exact_graph_filter_predicates(
            items,
            project_id=project,
            observe_type="span",
        )
        result = client.query(
            "SELECT id FROM spans FINAL WHERE project_id = %(project_id)s AND is_deleted = 0 AND ("
            + predicate
            + ")",
            parameters={
                **params,
                "project_id": project,
                "start_date": START,
                "end_date": END,
            },
        )
        assert {row[0] for row in result.result_rows} == {stamped["id"]}
    # V10 specifically requires the serializer -> trace-list path, not just
    # the span predicate used by the generic exact graph compiler.
    trace_filters = FilterListField().run_validation(
        [
            raw_array,
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [START.isoformat(), END.isoformat()],
                },
            },
        ]
    )
    sql, params = TraceListQueryBuilderV2(
        project_id=project,
        filters=trace_filters,
    ).build_filter_match_query([trace_id("scope")])
    rows = list(client.query(sql, parameters=params).named_results())
    assert len(rows) == 1 and str(rows[0]["trace_id"]) == trace_id("scope")


@pytest.mark.integration
def test_column_offer_uses_current_successful_sources(skill_query_client):
    from tracer.tests.test_bounded_trace_filter_reads import END as page_end
    from tracer.tests.test_bounded_trace_filter_reads import (
        PROJECT_ID,
        _check_page_enrichments,
        _short_time_filter,
    )

    client = skill_query_client

    def probe(sql, params):
        # The native driver renders Python datetimes at second precision.
        params = {
            k: v.replace(microsecond=0) if isinstance(v, datetime) else v
            for k, v in params.items()
        }
        return list(client.query(sql, parameters=params).named_results())

    # A normal root remains visible throughout; a failed-only load must not
    # trigger the offer. Unverified successes are shown (not linked) by hydration.
    start = (page_end - timedelta(minutes=1)).replace(tzinfo=UTC)
    ordinary = span_row(
        PROJECT_ID,
        "column-offer",
        name="request",
        parent_span_id="",
        start_time=start,
        span_attributes={},
    )
    failed = span_row(
        PROJECT_ID,
        "column-offer",
        start_time=start,
        status="ERROR",
        span_attributes={"fi.skill.name": "refund", "error.type": "integrity"},
    )
    error_with_version = span_row(
        PROJECT_ID, "column-offer", start_time=start, status="ERROR"
    )
    insert_rows(client, [ordinary, failed, error_with_version])
    _check_page_enrichments(1, expect_offer=False, offer_query=probe)
    exact = start + timedelta(microseconds=123456)
    load = span_row(PROJECT_ID, "column-offer", start_time=exact)
    load["span_attributes"]["fi.skill.digest"] = "unverified"
    insert_rows(client, [load])
    _check_page_enrichments(1, expect_offer=True, offer_query=probe)
    _check_page_enrichments(
        1,
        expect_offer=True,
        offer_query=probe,
        time_filters=[_date_filter("equals", exact.isoformat())],
    )
    # The ordinary root remains allowed, but no successful load does.
    _check_page_enrichments(
        1,
        expect_offer=False,
        offer_query=probe,
        time_filters=[
            _short_time_filter(),
            _date_filter("not_equals", exact.isoformat()),
        ],
    )
    insert_rows(
        client,
        [
            {
                **load,
                "created_at": load["created_at"] + timedelta(seconds=1),
                "deleted": True,
                "name": "",
                "span_attributes": {},
            }
        ],
    )
    assert client.query(
        "SELECT count() FROM spans FINAL WHERE id = %(id)s AND is_deleted = 0",
        parameters={"id": ordinary["id"]},
    ).result_rows == [(1,)]
    _check_page_enrichments(1, expect_offer=False, offer_query=probe)


def _date_filter(op, value):
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.integration
def test_mixed_eval_attribute_and_skill_membership_by_grain(
    skill_query_client, settings
):
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    project, config_id, eval_id = str(uuid4()), str(uuid4()), str(uuid4())
    roots, rows, evaluations = {}, [], []
    for label in ("all", "no-skill", "no-attr", "no-eval", "siblings"):
        root = span_row(
            project,
            label,
            name="root",
            parent_span_id="",
            span_attributes={} if label == "no-attr" else {"review.result": "rejected"},
        )
        roots[label] = root
        rows.append(root)
        if label != "no-skill":
            rows.append(span_row(project, label, parent_span_id=root["id"]))
        if label != "no-eval":
            scored = root
            if label == "siblings":
                scored = span_row(
                    project,
                    label,
                    name="scored",
                    parent_span_id=root["id"],
                    span_attributes={},
                )
                rows.append(scored)
            evaluations.append(
                (uuid4(), config_id, scored["trace_id"], scored["id"], 0, START, START)
            )
    insert_rows(skill_query_client, rows)
    skill_query_client.insert(
        "tracer_eval_logger_v2",
        evaluations,
        column_names=[
            "id",
            "custom_eval_config_id",
            "trace_id",
            "observation_span_id",
            "output_bool",
            "created_at",
            "updated_at",
        ],
    )
    options = {
        "project_id": project,
        "filters": [
            _date_filter("between", [START.isoformat(), END.isoformat()]),
            skill("version_id", VERSION),
            {
                "column_id": "review.result",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "rejected",
                },
            },
            {
                "column_id": eval_id,
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "categorical",
                    "filter_op": "equals",
                    "filter_value": "FAIL",
                },
            },
        ],
        "eval_filter_metadata": {
            eval_id: EvalFilterMetadata((config_id,), "PASS_FAIL")
        },
    }
    sql, params = TraceListQueryBuilderV2(**options).build_filter_match_query(
        [row["trace_id"] for row in roots.values()]
    )
    assert {
        row["trace_id"]
        for row in skill_query_client.query(sql, parameters=params).named_results()
    } == trace_ids("all", "siblings")
    sql, params = SpanListQueryBuilderV2(**options).build_filter_match_query(
        [row["id"] for row in rows]
    )
    assert {
        row["id"]
        for row in skill_query_client.query(sql, parameters=params).named_results()
    } == {roots["all"]["id"]}


def test_only_successful_load_key_discovery_uses_map_key_index():
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT)
    success, failure = (compiler.skill_live_rows_sql(failed=f) for f in (False, True))
    witness = "has(mapKeys(attrs_string), 'fi.skill.version_id')"
    assert f"indexHint({witness})" in success
    keys, replay = success.split("), skill_load_days AS", 1)
    assert f"indexHint({witness})" in keys
    assert "fi.skill.name" not in keys
    assert witness not in replay and witness not in failure


def test_revision_partition_discovery_precedes_attribute_replay():
    sql = ClickHouseFilterBuilderV2(project_id=PROJECT).skill_live_rows_sql()
    assert "skill_load_days AS (" in sql
    days, winner = sql.split("skill_load_days AS (", 1)[1].split("AS revision_day", 1)
    assert "SELECT DISTINCT toDate(start_time)" in days
    discovery, replay = winner.split("SELECT project_id, trace_id, id AS span_id", 1)
    guards = (
        "trace_id IN (SELECT trace_id FROM skill_load_keys)",
        "id IN (SELECT id FROM skill_load_keys)",
        "(project_id, trace_id, id) IN (SELECT DISTINCT project_id, trace_id, id FROM skill_load_keys)",
    )
    assert "FROM spans PREWHERE" in discovery
    assert all(guard in discovery for guard in guards)
    assert "attrs_" not in discovery and "%(start_date)s" not in discovery
    replay = replay.split("GROUP BY project_id, trace_id, id", 1)[0]
    assert "PREWHERE" in replay and all(guard in replay for guard in guards)
    assert (
        "toDate(rev.start_time) IN (SELECT revision_day FROM skill_load_days)" in replay
    )
    assert "%(start_date)s" not in replay


@pytest.mark.integration
def test_out_of_window_key_only_tombstone_on_other_day_wins(skill_query_client):
    project = str(uuid4())
    row = span_row(project, "cross-day")
    insert_rows(skill_query_client, [row])
    selected = [skill("version_id", VERSION)]
    assert matching_traces(skill_query_client, project, selected) == {row["trace_id"]}
    next_day = START + timedelta(days=1)
    insert_rows(
        skill_query_client,
        [
            {
                **row,
                "start_time": next_day,
                "created_at": next_day,
                "deleted": True,
                "name": "",
                "span_attributes": {},
            }
        ],
    )
    assert matching_traces(skill_query_client, project, selected) == set()


def test_canonical_root_replay_starts_from_this_trace_batch_or_load_cohort():
    filters = [skill("version_id", VERSION), skill("root_status", "error")]
    for kwargs in ({"candidate_ids_param": "candidate_trace_ids"}, {}):
        sql, _ = compile_filters(filters, **kwargs)
        assert "skill_root_keys AS (" in sql
        seed, replay = sql.split("skill_root_keys AS (", 1)[1].split(
            "), skill_root_days AS", 1
        )
        if kwargs:
            assert "trace_id IN %(candidate_trace_ids)s" in seed
        else:
            assert "skill_version_id = %(skill_1)s" in seed
        assert "trace_id IN (SELECT trace_id FROM skill_root_keys)" in replay
        assert (
            "toDate(rev.start_time) IN (SELECT revision_day FROM skill_root_days)"
            in replay
        )


@pytest.mark.integration
@pytest.mark.parametrize("org_scope", [False, True])
def test_list_load_witness_respects_datetime_complement(skill_query_client, org_scope):
    from tracer.services.clickhouse.v2.query_builders.session_list import (
        SessionListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    project = str(uuid4())
    rows = []
    roots = []
    for label, minute in (("included", 30), ("excluded", 20)):
        root = span_row(
            project,
            label,
            name="request",
            parent_span_id="",
            span_attributes={},
            trace_session_id=str(uuid4()),
        )
        roots.append(root)
        rows.extend(
            [
                root,
                span_row(
                    project,
                    label,
                    parent_span_id=root["id"],
                    start_time=START + timedelta(minutes=minute),
                ),
            ]
        )
    insert_rows(skill_query_client, rows)
    filters = [
        _date_filter("between", [START.isoformat(), END.isoformat()]),
        _date_filter(
            "not_between",
            [(START + timedelta(minutes=m)).isoformat() for m in (15, 30)],
        ),
        skill("version_id", VERSION),
    ]
    scope = {"project_ids": [project]} if org_scope else {"project_id": project}
    for builder, candidate, column in (
        (TraceListQueryBuilderV2, "trace_id", "trace_id"),
        (SpanListQueryBuilderV2, "id", "id"),
        (SessionListQueryBuilderV2, "trace_session_id", "session_id"),
    ):
        sql, params = builder(**scope, filters=filters).build_filter_match_query(
            [root[candidate] for root in roots]
        )
        assert {
            str(row[column])
            for row in skill_query_client.query(sql, parameters=params).named_results()
        } == {roots[0][candidate]}


@pytest.mark.integration
@pytest.mark.parametrize("org_scope", [False, True])
def test_fractional_equals_bounds_survive_native_datetime_formatting(
    skill_query_client,
    org_scope,
):
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    project = str(uuid4())
    roots = []
    start = START + timedelta(microseconds=123456)
    for label, us in (("inside", 0), ("before", -1), ("after", 1)):
        roots.append(
            span_row(
                project,
                label,
                parent_span_id="",
                start_time=start + timedelta(microseconds=us),
            )
        )
    insert_rows(skill_query_client, roots)
    filters = [_date_filter("equals", start.isoformat()), skill("version_id", VERSION)]
    predicate, params = compile_filters(filters, project=project)
    # Mirror the native driver's second-only datetime escaping, even when the
    # cloud fixture executes over HTTP. Only integer skill bounds survive it.
    legacy_binds = {
        "start_date": start.replace(microsecond=0),
        "end_date": start.replace(microsecond=0),
    }
    sql = (
        "SELECT DISTINCT trace_id FROM spans WHERE project_id = %(project_id)s AND "
        + predicate
    )
    assert {
        row[0]
        for row in skill_query_client.query(
            sql, parameters={**legacy_binds, **params, "project_id": project}
        ).result_rows
    } == trace_ids("inside")

    # The list classifiers partition time leaves away from the residual SKILL
    # group. They must still pass the exact request window to that group.
    scope = {"project_ids": [project]} if org_scope else {"project_id": project}
    for builder, column in (
        (TraceListQueryBuilderV2, "trace_id"),
        (SpanListQueryBuilderV2, "id"),
    ):
        sql, params = builder(**scope, filters=filters).build_filter_match_query(
            [root[column] for root in roots]
        )
        assert {
            row[column]
            for row in skill_query_client.query(
                sql, parameters={**params, **legacy_binds}
            ).named_results()
        } == {roots[0][column]}


def _closed_row_ids(client, project, items, *, row_type):
    from tracer.models.eval_task import EvalTask
    from tracer.services.eval_tasks.sampling.policy import validate_policy
    from tracer.services.eval_tasks.sampling.query import READ_SETTINGS, SamplingQuery

    task = EvalTask(project_id=project, row_type=row_type, filters={"filters": items})
    policy = validate_policy(
        {
            "version": 1,
            "mode": "closed",
            "sampling_unit": {"spans": "span", "traces": "trace"}[row_type],
            "window": {"start": START.isoformat(), "end": END.isoformat()},
        },
        row_type=row_type,
        now=END,
    )
    sql, params = SamplingQuery(task, policy, "v1", END).rows()
    return {
        row[0]
        for row in client.query(
            sql, parameters=params, settings=READ_SETTINGS
        ).result_rows
    }


@pytest.mark.integration
def test_closed_span_rows_replay_out_of_hour_tombstone_and_root(skill_query_client):
    project = str(uuid4())
    early = START - timedelta(minutes=5)
    child = span_row(
        project, "out-of-hour-root", start_time=START + timedelta(minutes=5)
    )
    root = span_row(
        project,
        "out-of-hour-root",
        id=child["parent_span_id"],
        parent_span_id="",
        name="request",
        status="ERROR",
        start_time=early,
        span_attributes={},
    )
    deleted = span_row(
        project, "out-of-hour-delete", start_time=START + timedelta(minutes=5)
    )
    tombstone = {
        **deleted,
        "start_time": early,
        "name": "",
        "span_attributes": {},
        "deleted": True,
        "created_at": START + timedelta(minutes=1),
    }
    insert_rows(skill_query_client, [root, child, deleted, tombstone])
    items = [skill("version_id", VERSION)]
    assert _closed_row_ids(skill_query_client, project, items, row_type="spans") == {
        child["id"]
    }
    assert _closed_row_ids(
        skill_query_client,
        project,
        [*items, skill("root_status", "error")],
        row_type="spans",
    ) == {child["id"]}


@pytest.mark.integration
def test_closed_watermark_precedes_physical_winner_election(skill_query_client):
    client = skill_query_client
    project = str(uuid4())
    original = span_row(
        project,
        "three-revisions",
        parent_span_id="",
        resource_attributes={"service.name": "X"},
    )
    corrected = {
        **original,
        "resource_attributes": {"service.name": "Y"},
        "created_at": START + timedelta(seconds=1),
        "span_attributes": {
            **original["span_attributes"],
            "fi.skill.version_id": OTHER_VERSION,
        },
    }
    # C displaces B under the *same* physical key. Keep the input revisions
    # available: after compaction historical membership needs persisted data.
    late = {
        **corrected,
        "created_at": END + timedelta(seconds=1),
        "span_attributes": {},
    }
    client.command("SYSTEM STOP MERGES spans")
    try:
        insert_rows(client, [original, corrected])
        for after_cutoff in (False, True):
            if after_cutoff:
                insert_rows(client, [late])
            assert _closed_row_ids(
                client, project, [skill("version_id", OTHER_VERSION)], row_type="traces"
            ) == {original["trace_id"]}
            assert (
                _closed_row_ids(
                    client, project, [skill("version_id", VERSION)], row_type="traces"
                )
                == set()
            )
    finally:
        client.command("SYSTEM START MERGES spans")


@pytest.mark.integration
def test_in_scope_elects_corrected_time_and_closed_scope(skill_query_client):
    project = str(uuid4())
    old = span_row(
        project, "moved", name="llm.chat", resource_attributes={"service.name": "X"}
    )
    other = span_row(
        project,
        "closed-scope",
        name="llm.chat",
        resource_attributes={"service.name": "X"},
    )
    insert_rows(skill_query_client, [old, other])
    moved = {
        **old,
        "start_time": START - timedelta(days=1),
        "span_attributes": {},
        "created_at": START + timedelta(minutes=5),
    }
    changed = {
        **other,
        "resource_attributes": {"service.name": "Y"},
        "created_at": END + timedelta(seconds=1),
        "span_attributes": {"fi.skills.version_ids": [OTHER_VERSION]},
    }
    insert_rows(skill_query_client, [moved, changed])
    items = [
        _date_filter("between", [START.isoformat(), END.isoformat()]),
        skill("in_scope", VERSION),
    ]
    sql, params = compile_filters(items, project=project, mode="span")
    assert (
        skill_query_client.query(
            "SELECT id FROM spans WHERE project_id = %(project_id)s AND " + sql,
            parameters={**params, "project_id": project},
        ).result_rows
        == []
    )
    assert _closed_row_ids(skill_query_client, project, items, row_type="spans") == {
        other["id"]
    }
    assert (
        _closed_row_ids(
            skill_query_client,
            project,
            [skill("in_scope", OTHER_VERSION)],
            row_type="spans",
        )
        == set()
    )


@pytest.mark.integration
def test_paged_replay_checks_later_candidates_and_never_truncates(
    skill_query_client, monkeypatch
):
    from clickhouse_connect.driver.exceptions import DatabaseError

    import tracer.services.clickhouse.query_builders.filters as compiler_module

    project = str(uuid4())
    # All three candidate identities share one trace so the order is by ID.
    stale, first, second = [
        span_row(project, "paged", id=f"{i:016x}") for i in range(3)
    ]
    insert_rows(skill_query_client, [stale, first, second])
    insert_rows(
        skill_query_client,
        [
            {
                **stale,
                "name": "",
                "span_attributes": {},
                "deleted": True,
                "created_at": END,
                "start_time": START - timedelta(days=1),
            }
        ],
    )
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    window = SkillEvidenceWindow.from_bounds(START, END)
    monkeypatch.setattr(compiler_module, "SKILL_REPLAY_BATCH_SIZE", 1)

    def read_keys(sql, params):
        return skill_query_client.query(sql, parameters=params).named_results()

    counts, winners = [], []
    for sql, params in compiler.iter_skill_load_replays(
        read_keys, skill_window=window, batch_size=1
    ):
        counts.append(len(params["skill_replay_keys"]))
        winners.append([row["span_id"] for row in read_keys(sql, params)])
    assert counts == [1, 1, 1]
    assert winners == [[], [first["id"]], [second["id"]]]
    # An unpaged aggregate must never publish the first page as an exact count.
    sql = compiler.skill_live_rows_sql(skill_window=window)
    with pytest.raises(DatabaseError, match="Skill load replay key budget exceeded"):
        skill_query_client.query(
            f"SELECT count() FROM ({sql})",
            parameters={"project_id": project, **window.params},
        )


@pytest.mark.integration
def test_fractional_window_in_raw_runtime_and_verified_summary(skill_query_client):
    from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
        SkillRuntimeQueryBuilder,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_skills import (
        TraceSkillsQueryBuilder,
    )

    project = str(uuid4())
    start = START + timedelta(microseconds=123456)
    end = start + timedelta(microseconds=1)
    inside = span_row(project, "window-hit", start_time=start, parent_span_id="")
    outside = span_row(project, "exclusive-end", start_time=end, parent_span_id="")
    claimed = span_row(project, "unverified", start_time=start, parent_span_id="")
    claimed["span_attributes"]["fi.skill.digest"] = "unverified"
    insert_rows(skill_query_client, [inside, outside, claimed])
    kwargs = {"project_ids": [project], "start": start, "end": end}
    identities = [(SKILL, VERSION, DIGEST)]

    def native_rows(statement):
        sql, params = statement
        params = {
            k: v.replace(microsecond=0) if isinstance(v, datetime) else v
            for k, v in params.items()
        }
        return list(skill_query_client.query(sql, parameters=params).named_results())

    usage = SkillRuntimeQueryBuilder(**kwargs).build_usage_loads(
        identities=identities, skill_names=["refund"]
    )
    assert [row["span_id"] for row in native_rows(usage)] == [inside["id"]]
    summary = TraceSkillsQueryBuilder(**kwargs).build_summary(
        filters=[skill("version_id", VERSION)], verified_identities=identities
    )
    assert native_rows(summary)[0]["traces"] == 1

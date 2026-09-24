"""Root-only Score membership on complete, latest CH25 span coordinates."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    builder,
    row,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine

LABEL = "44444444-4444-4444-4444-444444444444"
SECOND_LABEL = "77777777-7777-7777-7777-777777777777"
AUTHOR = "88888888-8888-8888-8888-888888888888"
OTHER_AUTHOR = "99999999-9999-9999-9999-999999999999"
TRACE = "55555555-5555-5555-5555-555555555555"
OTHER_TRACE = "66666666-6666-6666-6666-666666666666"
NIL_TRACE = "00000000-0000-0000-0000-000000000000"


def annotation(operation="equals", value=5):
    return {
        "column_id": LABEL,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "number",
            "filter_op": operation,
            "filter_value": value,
        },
    }


@pytest.fixture
def scored_engine(request):
    execute, insert_span = request.getfixturevalue("engine")
    trace_type = getattr(request, "param", "Nullable(String)")
    execute(f"""CREATE TABLE model_hub_score (
        id String, trace_id {trace_type}, observation_span_id Nullable(String),
        tracer_project_id UUID, label_id UUID, annotator_id Nullable(UUID),
        created_at DateTime64(6, 'UTC'), value String,
        deleted UInt8, _peerdb_is_deleted UInt8, _peerdb_version UInt64
    ) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY id""")
    execute("SYSTEM STOP MERGES model_hub_score")

    def insert_score(**changes):
        values = {
            "id": "score",
            "trace_id": "trace",
            "observation_span_id": None,
            "tracer_project_id": PROJECT,
            "label_id": LABEL,
            "annotator_id": None,
            "created_at": START,
            "value": '{"rating":5.0}',
            "deleted": 0,
            "_peerdb_is_deleted": 0,
            "_peerdb_version": 1,
            **changes,
        }
        execute(
            "INSERT INTO model_hub_score ("
            + ", ".join(values)
            + ") VALUES ("
            + ", ".join(f"%({key})s" for key in values)
            + ")",
            values,
        )

    return execute, insert_span, insert_score


def match(execute, filters, candidates=None):
    target = builder(filters=[time_filter(), *filters], annotation_label_ids=[LABEL])
    return execute(
        *target.build_filter_match_query_from_seed_rows(
            candidates
            or [
                row(service_name="root-service"),
                row(service_name="child-service"),
            ]
        )
    )


@pytest.mark.integration
def test_root_score_does_not_leak_to_child_with_same_external_id_and_time(
    scored_engine,
):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score()
    assert [value["service_name"] for value in match(execute, [annotation()])] == [
        "root-service"
    ]


@pytest.mark.unit
def test_legacy_key_and_unresolved_v2_probe_contracts_are_unchanged():
    options = {
        "project_id": PROJECT,
        "query_mode": ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        "resolved_candidate_spans_table": "latest_candidates",
    }
    legacy = ClickHouseFilterBuilder(**options)
    v2 = ClickHouseFilterBuilderV2(**options)
    assert (
        legacy._score_entity_column()
        == "tuple(trace_id, id, toUnixTimestamp64Micro(start_time))"
    )
    assert legacy._score_resolved_span_columns_sql() == "id, trace_id, start_time"
    assert "root_sp.service_name" not in legacy._score_span_select()
    legacy_joins = (
        "scored_sp.id = s.observation_span_id",
        "root_sp.trace_id = toString(s.trace_id)",
    )
    assert legacy._score_span_join_conditions_sql() == legacy_joins
    scored_join, root_join = v2._score_span_join_conditions_sql()
    assert "scored_sp.project_id = s.tracer_project_id" in scored_join
    assert "scored_sp.trace_id = toString(s.trace_id)" in scored_join
    assert "root_sp.project_id = s.tracer_project_id" in root_join
    assert "toStartOfHour(start_time)" in v2._score_entity_column()
    assert "toString(project_id)" in v2._score_entity_column()
    assert (
        "toString(observation_type), toString(service_name)"
        in v2._score_entity_column()
    )
    assert "root_sp.service_name" in v2._score_span_select()
    for compiler in (ClickHouseFilterBuilder, ClickHouseFilterBuilderV2):
        probe = compiler(project_id=PROJECT, query_mode=compiler.QUERY_MODE_SPAN)
        assert probe._score_entity_column() == "tuple(trace_id, id)"
        assert probe._score_span_join_conditions_sql() == legacy_joins
        trace = compiler(project_id=PROJECT, query_mode=compiler.QUERY_MODE_TRACE)
        assert trace._score_entity_column() == "trace_id"


@pytest.mark.integration
def test_root_score_does_not_cross_observation_type(scored_engine):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="same-service", observation_type="span", parent_span_id="")
    insert_span(
        service_name="same-service", observation_type="tool", parent_span_id="parent"
    )
    insert_score()
    rows = match(
        execute,
        [annotation()],
        [
            row(service_name="same-service", observation_type="span"),
            row(service_name="same-service", observation_type="tool"),
        ],
    )
    assert [value["observation_type"] for value in rows] == ["span"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation,expected",
    [
        ("not_equals", ["root-service"]),
        ("is_null", ["child-service"]),
        ("is_not_null", ["root-service"]),
    ],
)
def test_annotation_negative_and_absence_keep_physical_grain(
    scored_engine, operation, expected
):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score()
    assert [
        value["service_name"] for value in match(execute, [annotation(operation, 6)])
    ] == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    "present,expected", [(True, ["root-service"]), (False, ["child-service"])]
)
def test_has_annotation_uses_same_physical_key(scored_engine, present, expected):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score()
    leaf = {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": present,
        },
    }
    assert [value["service_name"] for value in match(execute, [leaf])] == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    "key", [LABEL, "has_annotation", "service_name", "observation_type", "_version"]
)
def test_explicit_typed_attribute_collision_does_not_change_score_identity(
    scored_engine, key
):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None, attrs_number={key: 9})
    insert_span(
        service_name="child-service", parent_span_id="parent", attrs_number={key: 9}
    )
    insert_score()
    attribute = {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": 9,
        },
    }
    assert [
        value["service_name"] for value in match(execute, [annotation(), attribute])
    ] == ["root-service"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"parent_span_id": "new-parent"},
        {"is_deleted": 1},
    ],
)
def test_historical_root_role_does_not_survive_latest_replacement(
    scored_engine, replacement
):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(
        service_name="root-service",
        _version=2,
        start_time=START + timedelta(minutes=10),
        **replacement,
    )
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score()
    assert match(execute, [annotation()]) == []


@pytest.mark.integration
@pytest.mark.parametrize("replacement", [{"deleted": 1}, {"_peerdb_is_deleted": 1}])
def test_latest_score_tombstones_remain_authoritative(scored_engine, replacement):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score()
    insert_score(_peerdb_version=2, **replacement)
    assert match(execute, [annotation()]) == []


@pytest.mark.integration
def test_span_backed_score_still_resolves_matching_live_span_candidates(scored_engine):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score(trace_id=None, observation_span_id="span")
    # A span-backed Score has logical span identity, not a root-only target.
    assert {value["service_name"] for value in match(execute, [annotation()])} == {
        "root-service",
        "child-service",
    }


@pytest.mark.integration
def test_score_from_other_project_with_same_external_ids_does_not_match(scored_engine):
    execute, insert_span, insert_score = scored_engine
    insert_span(service_name="root-service", parent_span_id=None)
    insert_span(service_name="child-service", parent_span_id="parent")
    insert_score(tracer_project_id=OTHER_PROJECT)
    assert match(execute, [annotation()]) == []


class JoinedScoreFilterBuilderV2(ClickHouseFilterBuilderV2):
    """Previous six-part/project-fenced join shape, for local parity checks."""

    _score_membership_condition = ClickHouseFilterBuilder._score_membership_condition
    _score_label_completeness_condition = (
        ClickHouseFilterBuilder._score_label_completeness_condition
    )


def org_match(
    execute,
    candidates,
    *,
    route,
    filters=None,
    label_ids=None,
    labels_by_project=None,
    joined=False,
):
    filters = [annotation()] if filters is None else filters
    label_ids = [LABEL] if label_ids is None else label_ids
    compiler_cls = JoinedScoreFilterBuilderV2 if joined else ClickHouseFilterBuilderV2
    if route == "builder":
        target = builder(
            project_id=None,
            project_ids=[PROJECT, OTHER_PROJECT],
            filters=[time_filter(), *filters],
            annotation_label_ids=label_ids,
            annotation_label_ids_by_project=labels_by_project,
        )
        target._FILTER_BUILDER_CLS = compiler_cls
        return execute(*target.build_filter_match_query_from_seed_rows(candidates))
    # Exercise the compiler's multi-project contract directly as well as the
    # public builder, whose per-project branches already fence that caller.
    compiler = compiler_cls(
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        project_ids=[PROJECT, OTHER_PROJECT],
        annotation_label_ids=label_ids,
        score_date_scope=False,
        resolved_candidate_spans_table="latest_candidates",
    )
    predicate, params = compiler.translate(filters)
    return execute(
        "WITH latest_candidates AS ("
        "SELECT * FROM (SELECT * FROM spans FINAL "
        "WHERE project_id IN %(project_ids)s) WHERE is_deleted = 0) "
        "SELECT project_id, trace_id, id, service_name FROM latest_candidates "
        f"WHERE {predicate}",
        {**params, "project_ids": (PROJECT, OTHER_PROJECT)},
    )


@pytest.mark.integration
@pytest.mark.parametrize("route", ["compiler", "builder"])
@pytest.mark.parametrize(
    "score_kind", ["root", "span", "span_without_trace", "span_nil_trace"]
)
@pytest.mark.parametrize(
    "scored_engine", ["Nullable(String)", "Nullable(UUID)"], indirect=True
)
def test_score_cannot_cross_two_authorized_projects_with_same_external_ids(
    scored_engine, route, score_kind
):
    execute, insert_span, insert_score = scored_engine
    candidates = [
        row(project_id=project, trace_id=TRACE) for project in (PROJECT, OTHER_PROJECT)
    ]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id=None)
    insert_score(
        trace_id={"span_without_trace": None, "span_nil_trace": NIL_TRACE}.get(
            score_kind, TRACE
        ),
        observation_span_id=None if score_kind == "root" else "span",
    )
    assert [
        value["project_id"] for value in org_match(execute, candidates, route=route)
    ] == [PROJECT]


@pytest.mark.integration
@pytest.mark.parametrize("route", ["compiler", "builder"])
@pytest.mark.parametrize(
    "scored_engine", ["Nullable(String)", "Nullable(UUID)"], indirect=True
)
def test_populated_score_trace_fences_same_span_id_in_another_trace(
    scored_engine, route
):
    execute, insert_span, insert_score = scored_engine
    candidates = [row(trace_id=trace) for trace in (TRACE, OTHER_TRACE)]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id="parent")
    insert_score(trace_id=TRACE, observation_span_id="span")
    assert [
        value["trace_id"] for value in org_match(execute, candidates, route=route)
    ] == [TRACE]


@pytest.mark.integration
@pytest.mark.parametrize("route", ["compiler", "builder"])
def test_empty_score_trace_resolves_span_within_its_project(scored_engine, route):
    execute, insert_span, insert_score = scored_engine
    candidates = [
        row(project_id=project, trace_id=TRACE) for project in (PROJECT, OTHER_PROJECT)
    ]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id="parent")
    insert_score(trace_id="", observation_span_id="span")
    assert [
        value["project_id"] for value in org_match(execute, candidates, route=route)
    ] == [PROJECT]


@pytest.mark.integration
@pytest.mark.parametrize("observation_span_id", [None, "span"])
@pytest.mark.parametrize(
    "operation,value,expected_project",
    [
        ("not_equals", 6, PROJECT),
        ("is_null", None, OTHER_PROJECT),
        ("is_not_null", None, PROJECT),
    ],
)
def test_org_annotation_negative_and_absence_do_not_borrow_another_project_score(
    scored_engine, observation_span_id, operation, value, expected_project
):
    execute, insert_span, insert_score = scored_engine
    candidates = [
        row(project_id=project, trace_id=TRACE) for project in (PROJECT, OTHER_PROJECT)
    ]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id=None)
    insert_score(trace_id=TRACE, observation_span_id=observation_span_id)
    assert [
        value["project_id"]
        for value in org_match(
            execute,
            candidates,
            route="compiler",
            filters=[annotation(operation, value)],
        )
    ] == [expected_project]


def meta_filter(column, value, operation="equals", **config):
    return {
        "column_id": column,
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": operation,
            "filter_value": value,
            **config,
        },
    }


def assert_org_membership(execute, candidates, expected, **options):
    for joined in (False, True):
        values = org_match(execute, candidates, joined=joined, **options)
        assert {
            (value["project_id"], value["id"], value["service_name"])
            for value in values
        } == expected


@pytest.mark.integration
@pytest.mark.parametrize("route", ["compiler", "builder"])
@pytest.mark.parametrize("span_score_trace", [None, TRACE, NIL_TRACE])
@pytest.mark.parametrize("complete", [True, False])
def test_completeness_combines_labels_across_score_arms_without_crossing_scope(
    scored_engine, route, span_score_trace, complete
):
    execute, insert_span, insert_score = scored_engine
    candidates = []
    for project in (PROJECT, OTHER_PROJECT):
        for service, parent in (("root", None), ("child", "parent")):
            candidate = row(project_id=project, trace_id=TRACE, service_name=service)
            candidates.append(candidate)
            insert_span(**candidate, parent_span_id=parent)
        insert_score(id=f"root-{project}", trace_id=TRACE, tracer_project_id=project)
    # The second label exists only in A and is span-backed. A's root is fully
    # annotated by the union of two arms; neither child inherits root label 1.
    insert_score(
        id="second",
        trace_id=span_score_trace,
        observation_span_id="span",
        label_id=SECOND_LABEL,
    )
    # Duplicates of label 1 must not substitute for missing label 2 in B.
    insert_score(id="duplicate", trace_id=TRACE, tracer_project_id=OTHER_PROJECT)
    population = {
        (p, "span", s) for p in (PROJECT, OTHER_PROJECT) for s in ("root", "child")
    }
    positive = {(PROJECT, "span", "root")}
    assert_org_membership(
        execute,
        candidates,
        positive if complete else population - positive,
        route=route,
        filters=[meta_filter("has_annotation", complete)],
        label_ids=[LABEL, SECOND_LABEL],
        labels_by_project={p: [LABEL, SECOND_LABEL] for p in (PROJECT, OTHER_PROJECT)},
    )


@pytest.mark.integration
@pytest.mark.parametrize("other_labels", [[LABEL], []])
@pytest.mark.parametrize("complete", [True, False])
def test_completeness_keeps_per_project_label_sets_and_known_empty_policy(
    scored_engine, other_labels, complete
):
    execute, insert_span, insert_score = scored_engine
    candidates = [row(project_id=p, trace_id=TRACE) for p in (PROJECT, OTHER_PROJECT)]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id=None)
    insert_score(trace_id=TRACE, tracer_project_id=OTHER_PROJECT)
    expected_project = OTHER_PROJECT if complete else PROJECT
    assert_org_membership(
        execute,
        candidates,
        {(expected_project, "span", "service-a")},
        route="builder",
        filters=[meta_filter("has_annotation", complete)],
        label_ids=[LABEL, SECOND_LABEL],
        labels_by_project={PROJECT: [LABEL, SECOND_LABEL], OTHER_PROJECT: other_labels},
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "leaf,expected",
    [
        (
            meta_filter("annotator", [AUTHOR], "not_in"),
            {"a-child", "b-root", "b-child"},
        ),
        (
            meta_filter("annotator", AUTHOR, "not_equals"),
            {"a-child", "b-root", "b-child"},
        ),
        (
            meta_filter(
                LABEL + "**annotator",
                [AUTHOR],
                "not_in",
                col_type="ANNOTATION",
                filter_type="annotator",
            ),
            {"a-child", "b-root"},
        ),
        (
            meta_filter(
                LABEL + "**annotator",
                AUTHOR,
                "not_equals",
                col_type="ANNOTATION",
                filter_type="annotator",
            ),
            {"a-child", "b-root"},
        ),
        (
            meta_filter("my_annotations", False, user_id=AUTHOR),
            {"a-child", "b-root", "b-child", "empty"},
        ),
        (meta_filter("my_annotations", True, user_id=AUTHOR), {"a-root"}),
        (meta_filter("my_annotations", False), set()),
        (meta_filter("annotator", None, "is_null"), {"empty"}),
        (
            meta_filter("annotator", None, "is_not_null"),
            {"a-root", "a-child", "b-root", "b-child"},
        ),
    ],
)
def test_negative_author_requires_presence_but_my_annotations_false_does_not(
    scored_engine, leaf, expected
):
    execute, insert_span, insert_score = scored_engine
    candidates = []
    for project, prefix in ((PROJECT, "a"), (OTHER_PROJECT, "b")):
        for role, parent in (("root", None), ("child", "parent")):
            candidate = row(
                project_id=project, trace_id=TRACE, service_name=f"{prefix}-{role}"
            )
            candidates.append(candidate)
            insert_span(**candidate, parent_span_id=parent)
    candidates.append(row(trace_id=TRACE, id="empty", service_name="empty"))
    insert_span(**candidates[-1], parent_span_id="parent")
    insert_score(id="a-root", trace_id=TRACE, annotator_id=AUTHOR)
    insert_score(
        id="a-span",
        trace_id=None,
        observation_span_id="span",
        annotator_id=OTHER_AUTHOR,
    )
    insert_score(
        id="b-root",
        trace_id=TRACE,
        tracer_project_id=OTHER_PROJECT,
        annotator_id=OTHER_AUTHOR,
    )
    insert_score(
        id="b-span-other-label",
        trace_id=TRACE,
        observation_span_id="span",
        tracer_project_id=OTHER_PROJECT,
        label_id=SECOND_LABEL,
        annotator_id=OTHER_AUTHOR,
    )
    expected_keys = {
        (c["project_id"], c["id"], c["service_name"])
        for c in candidates
        if c["service_name"] in expected
    }
    assert_org_membership(
        execute, candidates, expected_keys, route="builder", filters=[leaf]
    )


@pytest.mark.integration
@pytest.mark.parametrize("operation,value", [("not_equals", 6), ("not_in", [6])])
def test_negative_values_remain_existential_not_an_anti_match(
    scored_engine, operation, value
):
    execute, insert_span, insert_score = scored_engine
    candidates = [
        row(trace_id=TRACE, service_name=service) for service in ("root", "child")
    ]
    for candidate in candidates:
        insert_span(
            **candidate,
            parent_span_id=None if candidate["service_name"] == "root" else "parent",
        )
    insert_score(trace_id=TRACE, value='{"rating":5}')
    insert_score(
        id="six", trace_id=TRACE, observation_span_id="span", value='{"rating":6}'
    )
    assert_org_membership(
        execute,
        candidates,
        {(PROJECT, "span", "root")},
        route="builder",
        filters=[annotation(operation, value)],
    )


@pytest.mark.integration
def test_latest_score_reassignment_does_not_leave_old_project_membership(scored_engine):
    execute, insert_span, insert_score = scored_engine
    candidates = [row(project_id=p, trace_id=TRACE) for p in (PROJECT, OTHER_PROJECT)]
    for candidate in candidates:
        insert_span(**candidate, parent_span_id=None)
    insert_score(trace_id=TRACE)
    insert_score(trace_id=TRACE, tracer_project_id=OTHER_PROJECT, _peerdb_version=2)
    assert_org_membership(
        execute, candidates, {(OTHER_PROJECT, "span", "service-a")}, route="builder"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "leaf",
    [
        annotation(),
        annotation("not_equals", 6),
        annotation("is_null", None),
        meta_filter("has_annotation", True),
        meta_filter("has_annotation", False),
        meta_filter("annotator", AUTHOR, "not_equals"),
        meta_filter("my_annotations", False, user_id=AUTHOR),
    ],
)
def test_resolved_score_membership_prunes_each_branch_to_physical_candidates(leaf):
    target = builder(
        filters=[time_filter(), leaf], annotation_label_ids=[LABEL, SECOND_LABEL]
    )
    sql, _ = target.build_filter_match_query_from_seed_rows([row()])
    assert sql.count("FROM spans FINAL") == 1
    # Each score-shape branch prunes against the same scoped latest source.
    # CTE text references are not a measured database scan count.
    assert sql.count("FROM resolved_annotation_candidates") in (4, 7)
    assert "root_sp." not in sql and "scored_sp." not in sql
    assert "JOIN" not in sql
    reads = sql.count("FROM model_hub_score AS s FINAL")
    assert reads >= 3
    assert sql.count("s.deleted = false") == reads
    assert sql.count("s._peerdb_is_deleted = 0") == reads

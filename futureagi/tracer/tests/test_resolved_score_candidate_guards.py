"""Finite Score arms reuse the resolved physical candidate domain."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests.test_relational_filter_native import (
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- disable external integration DDL
    _ensure_test_score_tenant_column,  # noqa: F401
    offline_only,  # noqa: F401
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    row,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_score_physical_identity import annotation
from tracer.tests.test_span_score_physical_identity import (
    scored_engine as scored_engine,
)

pytestmark = pytest.mark.unit
RESOLVED = "resolved_score_candidates"


def compiler(**kwargs):
    return ClickHouseFilterBuilderV2(
        project_ids=[PROJECT, OTHER_PROJECT],
        query_mode="span",
        resolved_candidate_spans_table=RESOLVED,
        **kwargs,
    )


@pytest.mark.parametrize("scope", [False, True])
@pytest.mark.parametrize(
    "candidates,guard",
    [
        ({"candidate_entities_table": "finite_pairs"}, "FROM finite_pairs"),
        ({"candidate_entities_param": "finite_pairs"}, "IN %(finite_pairs)s"),
        ({"candidate_ids_param": "finite_ids"}, "IN %(finite_ids)s"),
        ({}, None),
    ],
)
def test_each_score_arm_is_project_correlated_and_finitely_guarded(
    candidates, guard, scope
):
    sql, _ = compiler(score_date_scope=scope, **candidates).translate([annotation()])
    project = "toString(s.tracer_project_id)"
    trace = "ifNull(toString(s.trace_id), '')"
    span = "ifNull(toString(s.observation_span_id), '')"
    for score_columns, candidate_columns in (
        (trace, "toString(trace_id)"),
        (span, "toString(id)"),
        (f"{trace}, {span}", "toString(trace_id), toString(id)"),
    ):
        assert (
            f"tuple({project}, {score_columns}) IN "
            f"(SELECT tuple(toString(project_id), {candidate_columns}) FROM {RESOLVED}"
        ) in sql
    assert sql.count(f"FROM {RESOLVED}") == 3
    assert sql.count("ifNull(parent_span_id, '') = ''") == 2  # candidate and outer root
    assert "scored_sp." not in sql and "root_sp." not in sql
    assert "FROM spans" not in sql
    assert ("s.created_at >=" in sql) is scope
    assert sql.count("s._peerdb_is_deleted = 0") == 3
    if guard:
        assert sql.count(guard) == 3


def trace_id(name):
    return str(uuid5(NAMESPACE_URL, "resolved-score-guard-" + name))


@pytest.mark.integration
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
@pytest.mark.parametrize("candidate_source", ["table", "parameter"])
@pytest.mark.parametrize("score_arm", ["trace", "span", "pair"])
@pytest.mark.parametrize(
    "operation", ["equals", "not_equals", "is_null", "is_not_null"]
)
def test_native_finite_score_arms_keep_latest_physical_and_tenant_truth(
    scored_engine,  # noqa: F811 -- fixture injection
    candidate_source,
    score_arm,
    operation,
):
    execute, insert_span, insert_score = scored_engine
    names = (
        "live",
        "changed",
        "hard",
        "soft",
        "absent",
        "unselected",
        "role",
        "rooted",
        "tenant_only",
        "foreign_only",
        "collision",
        "outside",
        "gone",
        "moved",
    )
    for name in names:
        candidate = row(id=name, trace_id=trace_id(name))
        insert_span(
            **{
                **candidate,
                "start_time": START - timedelta(days=1)
                if name == "outside"
                else candidate["start_time"],
            },
            parent_span_id="parent" if name == "rooted" else None,
        )
        correction = {
            "live": {"start_time": START + timedelta(minutes=25)},
            "role": {"parent_span_id": "parent"},
            "rooted": {"parent_span_id": None},
            "gone": {"is_deleted": 1},
            "moved": {"start_time": START + timedelta(minutes=10)},
        }.get(name)
        if correction:
            insert_span(**{**candidate, "_version": 2, **correction})
        if name == "absent":
            continue
        score = {
            "id": name,
            "trace_id": None if score_arm == "span" else trace_id(name),
            "observation_span_id": None if score_arm == "trace" else name,
            "created_at": START - timedelta(days=400),
            "tracer_project_id": (
                OTHER_PROJECT
                if name == "tenant_only"
                else str(uuid5(NAMESPACE_URL, "unauthorized-score-project"))
                if name == "foreign_only"
                else PROJECT
            ),
        }
        insert_score(**score)
        if name in {"changed", "hard", "soft"}:
            insert_score(
                **score,
                _peerdb_version=2,
                value='{"rating":7}' if name == "changed" else '{"rating":5}',
                _peerdb_is_deleted=int(name == "hard"),
                deleted=int(name == "soft"),
            )

    # Equal public IDs are distinct physical rows; root-only scores must not
    # attach to either child discriminator or the other authorized tenant.
    for extra in (
        {"service_name": "service-b", "parent_span_id": "parent"},
        {"observation_type": "tool", "parent_span_id": "parent"},
        {"project_id": OTHER_PROJECT, "parent_span_id": None},
    ):
        insert_span(id="collision", trace_id=trace_id("collision"), **extra)

    options = (
        {"candidate_entities_table": "finite_pairs"}
        if candidate_source == "table"
        else {"candidate_entities_param": "finite_pairs"}
    )
    sql, params = compiler(score_date_scope=False, **options).translate(
        [annotation(operation)]
    )
    params.update(
        project_ids=(PROJECT, OTHER_PROJECT),
        finite_pairs=tuple(
            (trace_id(name), name) for name in names if name != "unselected"
        ),
        window_start=START + timedelta(minutes=15),
        window_end=START + timedelta(hours=1),
    )
    # This compiler consumes already-resolved physical rows. Materialize that
    # fixture boundary before adding mutable predicates: CH25.3 can otherwise
    # prune a corrected row before FINAL, even with PREWHERE movement disabled.
    # Call-site latest-state construction is exercised by the Users/Span tests.
    execute(
        "CREATE TABLE score_fixture_winners ENGINE = Memory "
        "AS SELECT * FROM spans FINAL"
    )
    actual = execute(
        f"WITH {RESOLVED} AS ("
        "SELECT * FROM score_fixture_winners WHERE project_id IN %(project_ids)s AND is_deleted = 0 "
        "AND start_time >= %(window_start)s AND start_time < %(window_end)s), "
        # Deliberately narrower than the outer relation. Previously the table
        # guard was dropped in every Score arm, reviving 'unselected'.
        f"finite_pairs AS (SELECT DISTINCT trace_id, id FROM {RESOLVED} WHERE id != 'unselected') "
        f"SELECT project_id, trace_id, id, observation_type, service_name FROM {RESOLVED} "
        f"WHERE {sql}",
        params,
    )

    def key(name, *, project=PROJECT, observation="span", service="service-a"):
        return project, trace_id(name), name, observation, service

    population = {
        key(name) for name in names if name not in {"outside", "gone", "moved"}
    }
    siblings = {
        key("collision", service="service-b"),
        key("collision", observation="tool"),
    }
    population |= siblings | {key("collision", project=OTHER_PROJECT)}
    selected = {key(name) for name in ("live", "rooted", "collision")}
    if score_arm != "trace":
        selected |= siblings | {key("role")}
    expected = {
        "equals": selected,
        "not_equals": {key("changed")},
        "is_not_null": selected | {key("changed")},
        "is_null": population - selected - {key("changed")},
    }[operation]
    assert {
        tuple(
            value[column]
            for column in (
                "project_id",
                "trace_id",
                "id",
                "observation_type",
                "service_name",
            )
        )
        for value in actual
    } == expected

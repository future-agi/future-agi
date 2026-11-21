"""Core runtime reads; cloud-only Postgres/ClickHouse validation.

Integrate ST5 migration 039 and ST6's live-row compiler before this suite.
These tests do not certify the not-yet-wired HTTP projections or eval reads.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import (
    ProjectScope,
    core_metrics,
    read_runtime_statement,
)
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeDependencyUnavailable,
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.tests._ch_seed import seed_ch_spans

START = datetime(2026, 11, 1, 12, tzinfo=UTC)
END = START + timedelta(hours=1)
SKILL = "22222222-2222-4222-8222-222222222222"
VERSION = "11111111-1111-4111-8111-111111111111"
DIGEST = "sha256:" + "a" * 64


def builder(project):
    return SkillRuntimeQueryBuilder(project_ids=[project], start=START, end=END)


def test_project_scope_caps_only_readable_ids_and_exposes_no_names():
    ids = [str(uuid4()) for _ in range(63)]
    scope = ProjectScope.select(workspace_ids=ids, readable_ids=ids[:61])
    assert len(scope.project_ids) == 50 and scope.truncated is True
    assert scope.unreadable_projects == 2
    assert set(scope.project_ids).issubset(ids[:61])
    assert not hasattr(scope, "unreadable_names")


@pytest.mark.parametrize("requested", [["private"], ["foreign"]])
def test_explicit_unreadable_and_foreign_project_have_same_error(requested):
    with pytest.raises(SkillsHubError) as error:
        ProjectScope.select(
            workspace_ids=["public", "private"],
            readable_ids=["public"],
            requested_ids=requested,
        )
    assert error.value.code == "project_not_readable" and error.value.status == 403


def test_empty_authorized_scope_cannot_become_unscoped_sql():
    with pytest.raises(ValueError):
        SkillRuntimeQueryBuilder(project_ids=[], start=START, end=END)


def test_missing_dependency_fails_closed_instead_of_reimplementing_live_semantics():
    query = builder(str(uuid4()))
    query.compiler = object()
    with pytest.raises(RuntimeDependencyUnavailable):
        query.build_library_counts(verified_identities=[])


def test_library_sql_is_parameterized_and_scoped_on_both_narrow_and_span_sources():
    project = str(uuid4())
    sql, params = builder(project).build_library_counts(
        verified_identities=[(SKILL, VERSION, DIGEST)]
    )
    assert "skill_trace_links FINAL" in sql
    assert sql.count("project_id IN %(project_ids)s") >= 2
    assert "argMax(is_deleted, _version) = 0" in sql
    assert "uniqExact(tuple(project_id, trace_id))" in sql
    assert "SETTINGS" in sql
    assert params["project_ids"] == (project,)
    assert DIGEST not in sql and params["verified_identities"] == (
        (SKILL, VERSION, DIGEST),
    )


@pytest.mark.parametrize(
    "table,guard",
    [
        ("tracer_eval_logger", "raw_eval_logger._peerdb_is_deleted"),
        ("tracer_eval_logger_v2", "raw_eval_logger.is_deleted"),
    ],
)
def test_eval_coverage_reads_configured_live_results(settings, table, guard):
    settings.CH25_EVAL_LOGGER_TABLE = table
    project, trace, config = (str(uuid4()) for _ in range(3))
    sql, params = builder(project).build_usage_evals(
        config_ids=[config], members=[(project, trace)]
    )
    assert f"FROM {table} AS raw_eval_logger FINAL" in sql
    assert guard + " = 0" in sql
    assert params["config_ids"] == (config,) and params["trace_ids"] == (trace,)


def test_agent_filter_rejects_foreign_project_and_binds_names():
    project = str(uuid4())
    query = builder(project)
    with pytest.raises(ValueError):
        query.build_cohort_metrics(
            skill_id=SKILL,
            version_id=VERSION,
            digest=DIGEST,
            agent=(str(uuid4()), "", ""),
        )
    sql, params = query.build_cohort_metrics(
        skill_id=SKILL,
        version_id=VERSION,
        digest=DIGEST,
        agent=(project, "service'", "agent'"),
    )
    assert "agent'" not in sql and "service'" not in sql
    assert params["agent"] == "agent'"


def test_one_deadline_is_shared_and_sent_to_the_server():
    class Service:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, *args, **kwargs):
            self.calls.append(kwargs)
            return "result"

    service = Service()
    deadline = ReadDeadline.start(1000)
    session = RuntimeReadSession(service, deadline=deadline)
    assert session.execute(("SELECT 1", {})) == "result"
    assert session.execute(("SELECT 2", {})) == "result"
    assert all(0 < call["server_execution_cap_ms"] <= 1000 for call in service.calls)
    assert (
        service.calls[1]["server_execution_cap_ms"]
        <= service.calls[0]["server_execution_cap_ms"]
    )


def test_deadline_and_sentinel_are_typed_failures_not_empty_successes():
    class Expired:
        def execute(self, statement):
            raise ReadDeadlineExceeded("private diagnostic")

    with pytest.raises(SkillsHubError) as error:
        read_runtime_statement(Expired(), ("query", {}))
    assert error.value.code == "runtime_read_budget_exceeded"
    assert "private" not in error.value.message
    oversized = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(data=[{}], row_count=10001)
    )
    with pytest.raises(SkillsHubError) as error:
        read_runtime_statement(oversized, ("query", {}), max_rows=10000)
    assert error.value.code == "runtime_read_too_large"


def test_programming_error_is_not_disguised_as_no_loads():
    class Broken:
        def execute(self, statement):
            raise ValueError("bad builder")

    with pytest.raises(ValueError, match="bad builder"):
        read_runtime_statement(Broken(), ("query", {}))


@pytest.fixture(scope="module")
def runtime_ch():
    from conftest import (
        _ch_test_apply_v2_schema,
        _ch_test_owned_database,
        _open_ch_test_http_client,
    )

    with _ch_test_owned_database("test_skill_runtime_") as database:
        _ch_test_apply_v2_schema(database)
        client = _open_ch_test_http_client(database=database)
        try:
            yield client
        finally:
            client.close()


def span(project, trace, **changes):
    return {
        "project_id": project,
        "trace_id": trace,
        "id": uuid4().hex[:16],
        "parent_span_id": "root",
        "name": "skill.load",
        "status": "OK",
        "observation_type": "unknown",
        "service_name": "support",
        "latency_ms": 7,
        "start_time": START,
        "end_time": START + timedelta(milliseconds=7),
        "created_at": START,
        "updated_at": START,
        "cost": 0.1,
        "total_tokens": 1,
        "span_attributes": {
            "fi.skill.id": SKILL,
            "fi.skill.version_id": VERSION,
            "fi.skill.version": 1,
            "fi.skill.name": "support",
            "fi.skill.digest": DIGEST,
            "fi.skill.load.evidence": "delivered",
            "gen_ai.agent.name": "worker",
        },
        **changes,
    }


def execute(client, statement):
    sql, params = statement
    return list(client.query(sql, parameters=params).named_results())


@pytest.mark.integration
def test_filter_values_are_live_verified_keyset_pages(runtime_ch):
    project, foreign = str(uuid4()), str(uuid4())
    identities = [(SKILL, VERSION, DIGEST)]
    first, second, wrong = (span(project, uuid4().hex) for _ in range(3))
    first["span_attributes"]["gen_ai.agent.name"] = "a"
    second["span_attributes"]["gen_ai.agent.name"] = "b"
    wrong["span_attributes"].update(
        {"gen_ai.agent.name": "c", "fi.skill.digest": "wrong"}
    )
    seed_ch_spans(
        [first, first, second, wrong, span(foreign, uuid4().hex)],
        client=runtime_ch,
        version_from_created_at=True,
    )
    query = builder(project)
    statement = query.build_filter_values(
        field="skill.agent", verified_identities=identities, limit=1
    )
    assert execute(runtime_ch, statement) == [{"value": "a"}, {"value": "b"}]
    assert execute(
        runtime_ch,
        query.build_filter_values(
            field="skill.agent", verified_identities=identities, after="a", limit=1
        ),
    ) == [{"value": "b"}]
    assert execute(
        runtime_ch,
        query.build_filter_values(field="skill.name", verified_identities=identities),
    ) == [{"value": SKILL}]
    assert execute(
        runtime_ch,
        query.build_filter_values(
            field="skill.version_id", verified_identities=identities
        ),
    ) == [{"value": VERSION}]
    assert (
        execute(
            runtime_ch,
            query.build_filter_values(field="skill.service", verified_identities=[]),
        )
        == []
    )
    seed_ch_spans(
        [{**first, "deleted": True, "created_at": END, "span_attributes": {}}],
        client=runtime_ch,
        version_from_created_at=True,
    )
    assert execute(runtime_ch, statement) == [{"value": "b"}]


@pytest.mark.integration
def test_failed_filter_values_have_no_version_identity(runtime_ch):
    project = str(uuid4())
    failed = span(
        project,
        uuid4().hex,
        status="ERROR",
        span_attributes={
            "fi.skill.name": "unpublished",
            "fi.skill.load.via": "sdk.read_file",
            "error.type": "not_found",
            "gen_ai.agent.name": "worker",
        },
    )
    seed_ch_spans([failed], client=runtime_ch, version_from_created_at=True)
    query = builder(project)
    assert execute(
        runtime_ch,
        query.build_filter_values(
            field="skill.name",
            verified_identities=[],
            failed=True,
        ),
    ) == [{"value": "unpublished"}]
    assert (
        execute(
            runtime_ch,
            query.build_filter_values(
                field="skill.agent",
                verified_identities=[],
                failed=True,
                skill_name="different",
            ),
        )
        == []
    )
    with pytest.raises(ValueError, match="Unsupported"):
        query.build_filter_values(
            field="skill.version_id", verified_identities=[], failed=True
        )


@pytest.mark.integration
def test_real_cohort_counts_roots_whole_trace_cost_and_missing_roots(runtime_ch):
    project = str(uuid4())
    t1, t2, t3 = (str(uuid4()) for _ in range(3))
    first = span(project, t1, latency_ms=5)
    first["span_attributes"].update(
        {"fi.skills.version_ids": [VERSION], "fi.skill.load.cached": True}
    )
    rows = [
        first,
        span(
            project,
            t1,
            name="request",
            parent_span_id="",
            status="ERROR",
            latency_ms=100,
            cost=1,
            total_tokens=2,
            start_time=START - timedelta(minutes=10),
            span_attributes={},
        ),
        span(project, t2, latency_ms=9),
        span(
            project,
            t2,
            name="request",
            parent_span_id="",
            latency_ms=200,
            cost=2,
            total_tokens=20,
            span_attributes={},
        ),
        span(
            project,
            t2,
            name="llm.chat",
            status="ERROR",
            cost=3,
            total_tokens=30,
            span_attributes={"fi.skills.version_ids": [VERSION]},
        ),
        span(project, t3),
    ]
    seed_ch_spans(rows, client=runtime_ch, version_from_created_at=True)
    result = core_metrics(
        execute(
            runtime_ch,
            builder(project).build_cohort_metrics(
                skill_id=SKILL, version_id=VERSION, digest=DIGEST
            ),
        )[0]
    )
    assert result["traces"] == 3 and result["loads"] == 3
    assert result["incomplete"] == 1 and result["failed_requests"] == {
        "count": 1,
        "rate": 0.5,
    }
    assert result["errors_any_span"] == 2
    assert result["latency_ms"]["p95"] == 200
    assert result["cost"]["total"] == pytest.approx(6.3)
    assert result["cost"]["per_trace_mean"] == pytest.approx(2.1)
    assert result["tokens"]["total"] == 55
    assert result["in_scope_cost"]["coverage"] == pytest.approx(2 / 3)
    assert result["in_scope_cost"]["total"] == pytest.approx(3.1)
    assert result["load_overhead_ms_p50"] == 7
    assert result["cached_share"] == pytest.approx(1 / 3)
    assert result["low_sample"] is True


@pytest.mark.integration
def test_real_replay_digest_mismatch_project_collision_and_tombstone(runtime_ch):
    project, foreign = str(uuid4()), str(uuid4())
    trace = str(uuid4())
    good = span(project, trace)
    wrong = span(project, str(uuid4()))
    wrong["span_attributes"]["fi.skill.digest"] = "sha256:wrong"
    seed_ch_spans(
        [good, good, wrong, span(foreign, trace)],
        client=runtime_ch,
        version_from_created_at=True,
    )
    query = builder(project)
    identities = [(SKILL, VERSION, DIGEST)]
    assert execute(
        runtime_ch, query.build_library_counts(verified_identities=identities)
    ) == [{"skill_id": SKILL, "traces": 1}]
    assert execute(
        runtime_ch,
        query.build_mismatch_counts(skill_id=SKILL, verified_identities=identities),
    ) == [{"loads": 1, "traces": 1}]
    seed_ch_spans(
        [
            {
                **good,
                "deleted": True,
                "created_at": START + timedelta(seconds=1),
                "span_attributes": {},
            }
        ],
        client=runtime_ch,
        version_from_created_at=True,
    )
    assert (
        execute(runtime_ch, query.build_library_counts(verified_identities=identities))
        == []
    )


@pytest.mark.integration
def test_library_deduplicates_overlapping_versions_and_window_is_half_open(runtime_ch):
    project, trace = str(uuid4()), str(uuid4())
    other_version = str(uuid4())
    first = span(project, trace)
    other = span(project, trace)
    other["span_attributes"]["fi.skill.version_id"] = other_version
    boundary = span(project, str(uuid4()), start_time=END, created_at=END)
    seed_ch_spans(
        [first, other, boundary], client=runtime_ch, version_from_created_at=True
    )
    assert execute(
        runtime_ch,
        builder(project).build_library_counts(
            verified_identities=[
                (SKILL, VERSION, DIGEST),
                (SKILL, other_version, DIGEST),
            ]
        ),
    ) == [{"skill_id": SKILL, "traces": 1}]


@pytest.mark.integration
def test_empty_cohort_returns_null_rates_not_nan(runtime_ch):
    project = str(uuid4())
    row = core_metrics(
        execute(
            runtime_ch,
            builder(project).build_cohort_metrics(
                skill_id=SKILL, version_id=VERSION, digest=DIGEST
            ),
        )[0]
    )
    assert row["traces"] == row["loads"] == 0
    assert row["failed_requests"] == {"count": 0, "rate": None}
    assert row["latency_ms"] == {"p50": None, "p95": None}
    assert row["cached_share"] is None and row["in_scope_cost"] is None


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["skill", "agent_context"])
def test_latest_load_replays_corrections_and_ignores_stamped_children(runtime_ch, kind):
    project, foreign = str(uuid4()), str(uuid4())
    prefix = "fi.skill" if kind == "skill" else "fi.context"
    name = "skill.load" if kind == "skill" else "context.load"
    attrs = {f"{prefix}.id": SKILL, f"{prefix}.version_id": VERSION}
    load = span(project, uuid4().hex, name=name, span_attributes=attrs)
    newer_child = span(
        project,
        uuid4().hex,
        name="llm.chat",
        span_attributes=attrs,
        start_time=START + timedelta(minutes=1),
    )
    foreign_load = span(
        foreign,
        uuid4().hex,
        name=name,
        span_attributes=attrs,
        start_time=START + timedelta(minutes=2),
    )
    unknown_version = span(
        project,
        uuid4().hex,
        name=name,
        span_attributes={**attrs, f"{prefix}.version_id": str(uuid4())},
        start_time=START + timedelta(minutes=3),
    )
    seed_ch_spans(
        [load, newer_child, foreign_load, unknown_version],
        client=runtime_ch,
        version_from_created_at=True,
    )
    statement = builder(project).build_latest_load(
        resource_id=SKILL, kind=kind, version_ids=[VERSION]
    )
    rows = execute(runtime_ch, statement)
    assert len(rows) == 1 and rows[0]["version_id"] == VERSION
    # clickhouse-connect returns UTC DateTime64 values naive when its server
    # timezone is UTC; compare the instant rather than the driver's tzinfo.
    assert rows[0]["start_time"].replace(tzinfo=UTC) == START
    seed_ch_spans(
        [
            {
                **load,
                "deleted": True,
                "created_at": END + timedelta(seconds=1),
                "span_attributes": {},
            }
        ],
        client=runtime_ch,
        version_from_created_at=True,
    )
    assert execute(runtime_ch, statement) == []

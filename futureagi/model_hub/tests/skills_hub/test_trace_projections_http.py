"""Mounted projection HTTP reads over an isolated real CH25 database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from tracer.models.project import Project
from tracer.services import trace_skills
from tracer.tests._ch_seed import seed_ch_spans
from tracer.tests.test_skill_filters import skill

pytestmark = pytest.mark.django_db
URL = "/tracer/trace/skill-projection/"
START = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def ch():
    from conftest import (
        _ch_test_apply_v2_schema,
        _ch_test_owned_database,
        _open_ch_test_http_client,
    )

    with _ch_test_owned_database("test_trace_projections_") as database:
        _ch_test_apply_v2_schema(database)
        client = _open_ch_test_http_client(database=database)
        try:
            yield client
        finally:
            client.close()


@pytest.fixture
def project(organization, workspace):
    return Project.objects.create(
        organization=organization,
        workspace=workspace,
        name="Visible",
        trace_type="observe",
        model_type="GenerativeLLM",
    )


@pytest.fixture
def http(user, organization, workspace, project, ch, monkeypatch):
    class Service:
        def execute_ch_query(self, sql, params, **kwargs):
            assert 0 < kwargs["server_execution_cap_ms"] <= 3000
            data = list(ch.query(sql, parameters=params).named_results())
            return SimpleNamespace(row_count=len(data), data=data)

    monkeypatch.setattr(trace_skills, "V2AnalyticsQueryService", Service)

    from tracer.views import trace as trace_view

    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", Service)

    def post(_url=URL, **body):
        request = APIRequestFactory().post(
            _url, {"project_id": str(project.pk), **body}, format="json"
        )
        force_authenticate(request, user=user)
        request.organization, request.workspace = organization, workspace
        return resolve(_url).func(request)

    return post


@pytest.fixture
def seeded(ch, project, hub_skill, publish_skill):
    version = publish_skill(hub_skill).version
    trace = str(uuid4())
    attrs = {
        "fi.skill.id": str(hub_skill.pk),
        "fi.skill.version_id": str(version.pk),
        "fi.skill.name": "untrusted-rename",
        "fi.skill.version": version.number,
        "fi.skill.digest": version.digest,
        "fi.skill.load.evidence": "delivered",
        "gen_ai.agent.name": "worker",
        "fi.skill.requested_label": "production",
    }

    def span(id, **kwargs):
        return {
            "project_id": str(project.pk),
            "trace_id": trace,
            "id": id,
            "parent_span_id": "root",
            "name": "tool",
            "status": "OK",
            "observation_type": "unknown",
            "service_name": "svc",
            "start_time": START,
            "end_time": START + timedelta(milliseconds=50),
            "created_at": START,
            "updated_at": START,
            **kwargs,
        }

    rows = [
        span(
            "root",
            parent_span_id="",
            status="OK",
            cost=2,
            latency_ms=123,
            start_time=START - timedelta(minutes=1),
        ),
        span("later-root", parent_span_id="", status="ERROR", cost=3),
        span("load", name="skill.load", span_attributes=attrs, cost=1),
        span(
            "after",
            status="ERROR",
            cost=7,
            start_time=START + timedelta(hours=2),
            span_attributes={"fi.skills.version_ids": [str(version.pk)]},
        ),
    ]
    seed_ch_spans(rows, client=ch, version_from_created_at=True)
    return SimpleNamespace(
        trace=trace, version=version, skill=hub_skill, span=span, attrs=attrs
    )


def test_first_day_page_and_detail(http):
    trace = str(uuid4())
    result = http(projection="page", trace_ids=[trace])
    assert result.status_code == 200, result.data
    assert result.data["result"]["traces"][trace] == {
        "skills": [],
        "failed": [],
        "more": 0,
    }
    detail = http(projection="detail", trace_id=trace).data["result"]
    assert detail["skills"] == detail["failed"] == detail["contexts"] == []
    assert detail["coverage"] == {
        "spans": 0,
        "stamped_spans": 0,
        "rate": None,
        "span_ids_before_first_load": [],
    }


def test_page_detail_authoritative_identity_and_whole_trace_coverage(http, seeded):
    result = http(projection="page", trace_ids=[seeded.trace])
    assert result.status_code == 200, result.data
    row = result.data["result"]["traces"][seeded.trace]["skills"][0]
    assert row["name"] == seeded.skill.name and row["version_id"] == str(
        seeded.version.pk
    )
    assert (
        row["agent"] == "worker" and row["span_ids"] == ["load"] and row["loads"] == 1
    )
    detail = http(projection="detail", trace_id=seeded.trace).data["result"]
    assert detail["skills"] == [row]
    assert detail["coverage"]["span_ids_before_first_load"] == ["root"]
    assert detail["coverage"]["rate"] == 0.25


def test_cohort_uses_canonical_root_and_whole_trace_cost(http, seeded):
    response = http(
        projection="cohort",
        filters=[skill("version_id", str(seeded.version.pk))],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.data
    summary = response.data["result"]["summary"]
    assert summary["traces"] == 1 and summary["incomplete"] == 0
    assert summary["failed_requests"] == {"count": 0, "rate": 0.0}
    assert summary["cost"]["per_trace_mean"] == 13


def test_cohort_respects_every_filter_and_empty_state(http, seeded):
    response = http(
        projection="cohort",
        filters=[
            skill("version_id", str(seeded.version.pk)),
            skill("root_status", "error"),
        ],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["summary"] == {
        "traces": 0,
        "incomplete": 0,
        "failed_requests": {"count": 0, "rate": None},
        "latency_ms": {"p95": None},
        "cost": {"per_trace_mean": None},
    }


def test_failed_load_and_missing_root_are_not_success(http, seeded, ch):
    trace = str(uuid4())
    seed_ch_spans(
        [
            seeded.span(
                "failed",
                trace_id=trace,
                name="skill.load",
                status="ERROR",
                span_attributes={
                    "fi.skill.name": "reported-name",
                    "error.type": "integrity",
                },
            )
        ],
        client=ch,
        version_from_created_at=True,
    )
    result = http(projection="detail", trace_id=trace).data["result"]
    assert (
        result["skills"] == [] and result["failed"][0]["skill_name"] == "reported-name"
    )
    response = http(
        projection="cohort",
        filters=[skill("name", "reported-name"), skill("load_result", "failed")],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.data
    summary = response.data["result"]["summary"]
    assert summary["traces"] == summary["incomplete"] == 1
    assert summary["failed_requests"] == {"count": 0, "rate": None}


def test_replay_tombstone_removes_load(http, seeded, ch):
    seed_ch_spans(
        [
            seeded.span(
                "load",
                name="skill.load",
                deleted=True,
                created_at=START + timedelta(minutes=2),
            )
        ],
        client=ch,
        version_from_created_at=True,
    )
    result = http(projection="detail", trace_id=seeded.trace)
    assert result.status_code == 200, result.data
    assert result.data["result"]["skills"] == []


def test_cursor_is_query_bound_and_pages_detail(http, seeded, ch):
    seed_ch_spans(
        [
            seeded.span(
                "load2",
                name="skill.load",
                span_attributes={**seeded.attrs, "gen_ai.agent.name": "second"},
            )
        ],
        client=ch,
        version_from_created_at=True,
    )
    first = http(projection="detail", trace_id=seeded.trace, limit=1).data["result"]
    assert len(first["skills"]) == 1 and first["next_cursor"]
    second = http(
        projection="detail", trace_id=seeded.trace, limit=1, cursor=first["next_cursor"]
    ).data["result"]
    assert len(second["skills"]) == 1 and second["next_cursor"] is None
    assert second["skills"][0]["agent"] != first["skills"][0]["agent"]
    changed = http(
        projection="detail", trace_id=str(uuid4()), limit=1, cursor=first["next_cursor"]
    )
    assert (
        changed.status_code == 400 and changed.data["code"] == "invalid_runtime_query"
    )


@pytest.mark.parametrize(
    "body",
    [
        {"projection": "unknown"},
        {"projection": "page", "trace_ids": []},
        {"projection": "page", "trace_ids": ["x"] * 101},
        {"projection": "detail"},
        {"projection": "detail", "trace_id": "x", "cursor": "invalid"},
        {"projection": "cohort", "filters": []},
        {"projection": "page", "trace_ids": ["x"], "extra": 1},
    ],
)
def test_invalid_request_is_typed(http, body):
    response = http(**body)
    assert response.status_code == 400, response.data
    assert response.data["code"] == "invalid_runtime_query"


def test_foreign_project_denied_before_read(
    http, project, organization, user, monkeypatch
):
    project.workspace = Workspace.objects.create(
        organization=organization, name="Other", created_by=user
    )
    project.save(update_fields=["workspace"])
    monkeypatch.setattr(
        trace_skills,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("Unauthorized read"),
    )
    result = http(projection="page", trace_ids=[str(uuid4())])
    assert result.status_code == 403 and result.data["code"] == "project_not_readable"


def graph_body(seeded, metric="cost"):
    return {
        "split_by": "skill_version",
        "interval": "hour",
        "req_data_config": {"type": "SYSTEM_METRIC", "id": metric},
        "filters": [
            skill("version_id", str(seeded.version.pk)),
            {
                "column_id": "start_time",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        START.isoformat(),
                        (START + timedelta(hours=1)).isoformat(),
                    ],
                },
            },
        ],
    }


@pytest.mark.parametrize(
    "metric,expected",
    [("cost", 13), ("traffic", 1), ("error_rate", 0), ("latency", 123)],
)
def test_graph_split_http_real_clickhouse(http, seeded, metric, expected):
    from tracer.serializers.filters import ObserveGraphDataResponseSerializer

    result = http(_url="/tracer/trace/get_graph_methods/", **graph_body(seeded, metric))
    assert result.status_code == 200, result.data
    graph = result.data["result"]
    assert graph["data"] == [] and graph["split_by"] == "skill_version"
    assert graph["series"][0]["version_id"] == str(seeded.version.pk)
    assert graph["series"][0]["data"][0]["value"] == expected
    assert (
        graph["query_exact"] and graph["totals_note"] == "cohorts overlap; not additive"
    )
    serializer = ObserveGraphDataResponseSerializer(data=result.data)
    assert serializer.is_valid(), serializer.errors


def test_graph_split_first_day_and_validation(http, seeded):
    body = graph_body(seeded)
    body["filters"][0] = skill("version_id", str(uuid4()))
    empty = http(_url="/tracer/trace/get_graph_methods/", **body)
    assert empty.status_code == 200, empty.data
    assert empty.data["result"]["series"] == []
    for change in (
        {"split_by": "unknown"},
        {"observe_type": "voice"},
        {"filters": []},
        {"req_data_config": {"type": "SYSTEM_METRIC", "id": "prompt_tokens"}},
    ):
        invalid = http(_url="/tracer/trace/get_graph_methods/", **{**body, **change})
        assert invalid.status_code == 400, invalid.data


def test_graph_cache_identity_is_tenant_project_query_and_metric_bound():
    from tracer.services.clickhouse.skill_graph import skill_graph_cache_key

    args = {
        "project_id": "p",
        "organization_id": "o",
        "workspace_id": "w",
        "filters": [],
        "interval": "hour",
        "metric_id": "cost",
    }
    key = skill_graph_cache_key(**args)
    for field in args:
        changed = [skill("agent", "changed")] if field == "filters" else "changed"
        assert skill_graph_cache_key(**{**args, field: changed}) != key


def test_detail_context_closure_is_available_not_loaded(
    http, seeded, ch, organization, workspace, hub_actor, publish_skill
):
    from model_hub.services.skills_hub.versions import create_skill

    context = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="context-test",
        description="Context fixture",
        tags=[],
    )
    context_version = publish_skill(context).version
    context.kind = "agent_context"
    context.save(update_fields=["kind"])
    trace = str(uuid4())
    seed_ch_spans(
        [
            seeded.span(
                "context",
                trace_id=trace,
                name="context.load",
                parent_span_id="",
                span_attributes={
                    "fi.context.id": str(context.pk),
                    "fi.context.version_id": str(context_version.pk),
                    "fi.context.digest": context_version.digest,
                    "fi.context.linked_skill_version_ids": [
                        str(seeded.version.pk),
                        str(uuid4()),
                    ],
                },
            )
        ],
        client=ch,
        version_from_created_at=True,
    )
    result = http(projection="detail", trace_id=trace)
    assert result.status_code == 200, result.data
    detail = result.data["result"]
    assert detail["skills"] == []
    assert detail["contexts"] == [
        {
            "context_id": str(context.pk),
            "name": context.name,
            "version": context_version.number,
        }
    ]
    assert detail["linked_not_loaded"] == [seeded.skill.name]


def test_detail_distinguishes_another_loaded_version_and_stamped_root(
    http,
    seeded,
    ch,
    organization,
    workspace,
    hub_actor,
    publish_skill,
    make_skill_files,
):
    from model_hub.services.skills_hub.versions import create_skill

    linked = seeded.version
    actual = publish_skill(
        seeded.skill,
        files=make_skill_files(seeded.skill.name, body="A new set of instructions."),
    ).version
    context = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="linked-context",
        description="A versioned context",
        tags=[],
    )
    ctx_version = publish_skill(context).version
    context.kind = "agent_context"
    context.save(update_fields=["kind"])
    trace = str(uuid4())
    rows = [
        seeded.span(
            "stamped-root",
            trace_id=trace,
            parent_span_id="",
            start_time=START - timedelta(seconds=1),
            span_attributes={"fi.skills.version_ids": [str(actual.pk)]},
        ),
        seeded.span(
            "context",
            trace_id=trace,
            name="context.load",
            span_attributes={
                "fi.context.id": str(context.pk),
                "fi.context.version_id": str(ctx_version.pk),
                "fi.context.digest": ctx_version.digest,
                "fi.context.linked_skill_version_ids": [str(linked.pk)],
            },
        ),
        seeded.span(
            "v2",
            trace_id=trace,
            name="skill.load",
            span_attributes={
                **seeded.attrs,
                "fi.skill.version": actual.number,
                "fi.skill.version_id": str(actual.pk),
                "fi.skill.digest": actual.digest,
            },
        ),
        seeded.span(
            "wrong-v1",
            trace_id=trace,
            name="skill.load",
            span_attributes={**seeded.attrs, "fi.skill.digest": "wrong"},
        ),
    ]
    seed_ch_spans(rows, client=ch, version_from_created_at=True)
    response = http(projection="detail", trace_id=trace)
    assert response.status_code == 200, response.data
    detail = response.data["result"]
    assert detail["linked_not_loaded"] == []
    assert detail["linked_version_differences"] == [
        {
            "skill_name": seeded.skill.name,
            "context_name": context.name,
            "context_version": ctx_version.number,
            "linked_version": linked.number,
            "loaded_versions": [actual.number],
        }
    ]
    assert "stamped-root" not in detail["coverage"]["span_ids_before_first_load"]


def test_mismatch_and_unknown_versions_never_become_verified(http, seeded, ch):
    seed_ch_spans(
        [
            seeded.span(
                "mismatch",
                name="skill.load",
                span_attributes={**seeded.attrs, "fi.skill.digest": "wrong"},
            ),
            seeded.span(
                "foreign",
                name="skill.load",
                span_attributes={**seeded.attrs, "fi.skill.version_id": str(uuid4())},
            ),
            seeded.span(
                "failed-with-id",
                name="skill.load",
                status="ERROR",
                span_attributes=seeded.attrs,
            ),
        ],
        client=ch,
        version_from_created_at=True,
    )
    result = http(projection="detail", trace_id=seeded.trace).data["result"]
    assert len(result["skills"]) == 3
    unknown = next(row for row in result["skills"] if row["name"] == "Unknown skill")
    assert unknown["skill_id"] is None and unknown["version_id"] is None
    assert {row["digest_status"] for row in result["skills"]} == {
        "verified",
        "mismatch",
    }


def test_page_cursor_preserves_requested_trace_set(http):
    ids = [str(uuid4()), str(uuid4())]
    first = http(projection="page", trace_ids=ids, limit=1).data["result"]
    assert list(first["traces"]) == ids[:1] and first["next_cursor"]
    second = http(
        projection="page", trace_ids=ids, limit=1, cursor=first["next_cursor"]
    ).data["result"]
    assert list(second["traces"]) == ids[1:] and second["next_cursor"] is None


def test_timeout_is_typed_not_empty(http, monkeypatch):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    class Slow:
        def execute_ch_query(self, *args, **kwargs):
            raise ReadDeadlineExceeded("private diagnostic")

    monkeypatch.setattr(trace_skills, "V2AnalyticsQueryService", Slow)
    response = http(projection="page", trace_ids=[str(uuid4())])
    assert (
        response.status_code == 503
        and response.data["code"] == "runtime_read_budget_exceeded"
    )
    assert "private diagnostic" not in str(response.data)


def test_cohort_negative_skill_filter_includes_unloaded_traces(http, seeded, ch):
    unloaded = str(uuid4())
    seed_ch_spans(
        [seeded.span("unloaded-root", trace_id=unloaded, parent_span_id="", cost=5)],
        client=ch,
        version_from_created_at=True,
    )
    response = http(
        projection="cohort",
        filters=[skill("version_id", str(seeded.version.pk), "not_equals")],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["summary"]["traces"] == 1
    assert response.data["result"]["summary"]["cost"]["per_trace_mean"] == 5


def test_cohort_excludes_unverified_performance(http, seeded, ch):
    trace = str(uuid4())
    seed_ch_spans(
        [
            seeded.span(
                "unverified-root",
                trace_id=trace,
                parent_span_id="",
                status="ERROR",
                cost=999,
            ),
            seeded.span(
                "unverified-load",
                trace_id=trace,
                name="skill.load",
                span_attributes={**seeded.attrs, "fi.skill.digest": "mismatch"},
            ),
        ],
        client=ch,
        version_from_created_at=True,
    )
    result = http(
        projection="cohort",
        filters=[skill("version_id", str(seeded.version.pk))],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert result.status_code == 200, result.data
    summary = result.data["result"]["summary"]
    assert summary["traces"] == 1 and summary["failed_requests"]["count"] == 0
    assert summary["cost"]["per_trace_mean"] == 13


def test_graph_failed_only_group_never_borrows_a_sibling_version(http, seeded, ch):
    seed_ch_spans(
        [
            seeded.span(
                "failure",
                name="skill.load",
                status="ERROR",
                span_attributes={
                    "fi.skill.name": seeded.skill.name,
                    "error.type": "integrity",
                },
            )
        ],
        client=ch,
        version_from_created_at=True,
    )
    body = graph_body(seeded)
    body["filters"] = [
        *body["filters"][1:],
        skill("name", seeded.skill.name),
        skill("load_result", "failed"),
    ]
    response = http(_url="/tracer/trace/get_graph_methods/", **body)
    assert response.status_code == 200, response.data
    assert response.data["result"]["series"] == []


def test_cohort_applies_non_skill_filters(http, seeded):
    filters = [
        skill("version_id", str(seeded.version.pk)),
        {
            "column_id": "name",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "not-this-request",
            },
        },
    ]
    response = http(
        projection="cohort",
        filters=filters,
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["summary"]["traces"] == 0


def test_cohort_accepts_observe_end_of_day_and_ninety_day_windows(http):
    end = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    for days in (1, 90):
        response = http(
            projection="cohort",
            filters=[skill("version_id", str(uuid4()))],
            **{"from": (end - timedelta(days=days)).isoformat(), "to": end.isoformat()},
        )
        assert response.status_code == 200, response.data
        assert response.data["result"]["summary"]["traces"] == 0
    invalid = http(
        projection="cohort",
        filters=[skill("version_id", str(uuid4()))],
        **{"from": (end - timedelta(days=91)).isoformat(), "to": end.isoformat()},
    )
    assert (
        invalid.status_code == 400 and invalid.data["code"] == "invalid_runtime_query"
    )

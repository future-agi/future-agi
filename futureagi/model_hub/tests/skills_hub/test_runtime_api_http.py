"""Shared usage and comparison HTTP contracts against real ClickHouse."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.skills_hub import HubSkillLabelEvent
from model_hub.services.skills_hub import runtime_api
from model_hub.tests.skills_hub.test_runtime_usage import START, END, runtime_ch, span  # noqa: F401 - shared CH fixture
from tracer.models.project import Project
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def project(organization, workspace):
    return Project.objects.create(organization=organization, workspace=workspace, name="Runtime QA", trace_type="observe", model_type="GenerativeLLM")


@pytest.fixture
def live_service(runtime_ch, monkeypatch):
    class Service:
        def execute_ch_query(self, sql, params, **kwargs):
            data = list(runtime_ch.query(sql, parameters=params).named_results())
            return SimpleNamespace(data=data, row_count=len(data))
    monkeypatch.setattr(runtime_api, "V2AnalyticsQueryService", Service)
    return runtime_ch


def get(user, organization, workspace, query, route="usage"):
    url = f"/model-hub/skills/{route}/"
    request = APIRequestFactory().get(url, query)
    force_authenticate(request, user=user)
    request.organization, request.workspace = organization, workspace
    return resolve(url).func(request)


def params(skill, **changes):
    return {"resource_id": str(skill.pk), "from": START.isoformat(), "to": END.isoformat(), **changes}


def load(project, version, trace=None, **changes):
    row = span(str(project.pk), trace or str(uuid4()), **changes)
    row["resource_attributes"] = {"service.name": row["service_name"]}
    row["span_attributes"] = {**row["span_attributes"], "fi.skill.id": str(version.skill_id),
                              "fi.skill.version_id": str(version.pk), "fi.skill.digest": version.digest,
                              "fi.skill.name": version.skill.name, "fi.skill.version": version.number}
    return row


@pytest.mark.parametrize("group_by", ["skill", "version", "agent", "context"])
def test_empty_usage(user, organization, workspace, hub_skill, project, live_service, group_by):
    response = get(user, organization, workspace, params(hub_skill, group_by=group_by))
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["summary"]["traces"] == 0
    if group_by == "skill":
        assert result["rows"][0]["runtime"]["loaded_7d"] is None
        assert result["rows"][0]["runtime"]["can_record"] is False
    else:
        assert result["rows"] == []
    assert result["summary"]["failed_requests"] == {"count": 0, "rate": None, "denominator": 0}
    assert result["diagnosis"]["state"] == "never_fetched"


def test_live_usage_dedup_roots_and_costs(user, organization, workspace, hub_skill, publish_skill, make_skill_files, project, live_service):
    version = publish_skill(hub_skill).version
    one = load(project, version)
    root = span(str(project.pk), one["trace_id"], id="root", parent_span_id="", status="ERROR", latency_ms=90, cost=.7, total_tokens=4, span_attributes={})
    missing = load(project, version)
    seed_ch_spans([one, one, root, missing], client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(hub_skill))
    assert response.status_code == 200, response.data
    row = response.data["result"]["rows"][0]
    assert (row["traces"], row["loads"], row["incomplete"]) == (2, 2, 1)
    assert row["failed_requests"] == {"count": 1, "rate": 1., "denominator": 1}
    assert row["latency_ms"] == {"p50": 90, "p95": 90}
    assert row["cost"]["total"] == pytest.approx(.9)
    assert row["tokens"]["total"] == 6
    assert row["low_sample"] is True
    assert row["name"] == hub_skill.name
    assert row["agent_rows"][0]["agent"]["name"] == "worker"


def test_cursor_bound_to_window_and_tenant(user, organization, workspace, hub_skill, publish_skill, make_skill_files, project, live_service):
    v1 = publish_skill(hub_skill).version
    v2 = publish_skill(hub_skill, files=make_skill_files(hub_skill.name, body="Updated instructions for the second version.")).version
    seed_ch_spans([load(project, v1), load(project, v2)], client=live_service, version_from_created_at=True)
    first = get(user, organization, workspace, params(hub_skill, limit=1))
    assert first.status_code == 200, first.data
    cursor = first.data["result"]["next_cursor"]
    assert cursor
    second = get(user, organization, workspace, params(hub_skill, limit=1, cursor=cursor))
    assert second.status_code == 200, second.data
    assert first.data["result"]["rows"][0]["version_id"] != second.data["result"]["rows"][0]["version_id"]
    assert second.data["result"]["next_cursor"] is None
    invalid = get(user, organization, workspace, params(hub_skill, limit=1, cursor=cursor, group_by="agent"))
    assert invalid.status_code == 400 and invalid.data["code"] == "invalid_runtime_query"


@pytest.mark.parametrize("extra", [{"group_by": "sql"}, {"from": "bad"}, {"limit": 0}, {"cursor": "bad"}, {"project_ids": "bad"}, {"unknown": "bad"}])
def test_invalid_usage(user, organization, workspace, hub_skill, extra):
    response = get(user, organization, workspace, params(hub_skill, **extra))
    assert response.status_code == 400 and response.data["code"] == "invalid_runtime_query"


def test_foreign_project_and_resource(user, organization, workspace, hub_skill):
    denied = get(user, organization, workspace, params(hub_skill, project_ids=str(uuid4())))
    assert denied.status_code == 403 and denied.data["code"] == "project_not_readable"
    missing = get(user, organization, workspace, params(hub_skill, resource_id=str(uuid4())))
    assert missing.status_code == 404


def test_explicit_comparison_is_pooled_not_sum(user, organization, workspace, hub_skill, publish_skill, make_skill_files, project, live_service):
    v1 = publish_skill(hub_skill).version
    v2 = publish_skill(hub_skill, files=make_skill_files(hub_skill.name, body="Updated instructions for the second version.")).version
    same_trace = str(uuid4())
    seed_ch_spans([load(project, v1, same_trace), load(project, v2, same_trace)], client=live_service, version_from_created_at=True)
    query = {"resource_id": str(hub_skill.pk), "before_from": (START - timedelta(hours=1)).isoformat(), "before_to": START.isoformat(),
             "after_from": START.isoformat(), "after_to": END.isoformat()}
    response = get(user, organization, workspace, query, "comparison")
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["verdict"] == "not_enough_data"
    traces = next(row for row in result["all_agents"]["metrics"] if row["metric"] == "traces")
    assert traces["after"] == 1 and traces["delta"] is None


def test_move_comparison_same_agent_adoption(user, organization, workspace, hub_skill, publish_skill, make_skill_files, project, live_service):
    v1 = publish_skill(hub_skill).version
    v2 = publish_skill(hub_skill, files=make_skill_files(hub_skill.name, body="Updated instructions for the second version.")).version
    event = HubSkillLabelEvent.objects.create(skill=hub_skill, label="production", from_version=1, to_version=2)
    HubSkillLabelEvent.objects.filter(pk=event.pk).update(created_at=START)
    rows = [load(project, v1, start_time=START - timedelta(minutes=5)), load(project, v2)]
    seed_ch_spans(rows, client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, {"resource_id": str(hub_skill.pk), "event_id": str(event.pk), "window": "1h"}, "comparison")
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["same_agents"]["agents"] == 1
    assert result["adoption"][0]["not_moved_reason"] == "got_new"
    assert result["verdict"] == "not_enough_data"
    assert all(row["delta"] is None for row in result["same_agents"]["metrics"])


def test_comparison_rejects_foreign_event_and_bad_windows(user, organization, workspace, hub_skill):
    base = {"resource_id": str(hub_skill.pk)}
    response = get(user, organization, workspace, {**base, "event_id": str(uuid4())}, "comparison")
    assert response.status_code == 404
    for extra in ({"window": "8d"}, {"before_from": timezone.now().isoformat()}, {"window": "0h"}):
        response = get(user, organization, workspace, {**base, **extra}, "comparison")
        assert response.status_code == 400 and response.data["code"] == "invalid_runtime_query"


def test_eval_any_live_span_has_trace_weight_and_coverage(user, organization, workspace, hub_skill, publish_skill, project, live_service):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    template = EvalTemplate.objects.create(name="runtime-quality", organization=organization, workspace=workspace, config={})
    config = CustomEvalConfig.objects.create(name="Quality", project=project, eval_template=template)
    version = publish_skill(hub_skill).version
    one, two = load(project, version), load(project, version)
    child = span(str(project.pk), one["trace_id"], name="child", span_attributes={})
    seed_ch_spans([one, two, child], client=live_service, version_from_created_at=True)
    live_service.insert("tracer_eval_logger_v2", [
        [uuid4(), one["trace_id"], one["id"], config.pk, 1., START, START],
        [uuid4(), one["trace_id"], child["id"], config.pk, .5, START, START],
    ], column_names=["id", "trace_id", "observation_span_id", "custom_eval_config_id", "output_float", "created_at", "updated_at"])
    response = get(user, organization, workspace, params(hub_skill))
    assert response.status_code == 200, response.data
    evaluation = response.data["result"]["rows"][0]["evals"][0]
    assert evaluation == {"config_id": str(config.pk), "name": "Quality", "mean": .75, "pass_rate": 0., "evaluated": 1, "coverage": .5}


def test_label_and_agent_filters_do_not_count_other_loads(user, organization, workspace, hub_skill, publish_skill, project, live_service):
    version = publish_skill(hub_skill).version
    selected, other = load(project, version), load(project, version)
    selected["span_attributes"]["fi.skill.requested_label"] = "production"
    other["span_attributes"]["fi.skill.requested_label"] = "staging"
    seed_ch_spans([selected, other], client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(hub_skill, label="production", project_id=str(project.pk), service="support", agent="worker", group_by="agent"))
    assert response.status_code == 200, response.data
    assert response.data["result"]["summary"]["loads"] == 1
    assert response.data["result"]["rows"][0]["requested_labels"] == {"production": 1}


def test_mismatch_and_failed_loads_stay_outside_cohort(user, organization, workspace, hub_skill, publish_skill, project, live_service):
    version = publish_skill(hub_skill).version
    mismatch = load(project, version)
    mismatch["span_attributes"]["fi.skill.digest"] = "sha256:" + "f" * 64
    failure = span(str(project.pk), str(uuid4()), status="ERROR", span_attributes={"fi.skill.name": hub_skill.name, "error.type": "integrity"})
    seed_ch_spans([mismatch, failure], client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(hub_skill))
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["summary"]["loads"] == 0
    assert result["mismatch"]["loads"] == 1
    assert result["failed_loads"]["total"] == 1
    assert result["failed_loads"]["links"][str(project.pk)]["all"]["project_id"] == str(project.pk)


@pytest.mark.parametrize("route", ["usage", "comparison"])
def test_unavailable_read_is_typed_not_zero(user, organization, workspace, hub_skill, project, monkeypatch, route):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    class Unavailable:
        def execute_ch_query(self, *args, **kwargs):
            raise ReadDeadlineExceeded("internal diagnostic")
    monkeypatch.setattr(runtime_api, "V2AnalyticsQueryService", Unavailable)
    query = params(hub_skill) if route == "usage" else {"resource_id": str(hub_skill.pk)}
    response = get(user, organization, workspace, query, route)
    assert response.status_code == 503, response.data
    assert response.data["code"] == "runtime_read_budget_exceeded"
    assert "internal diagnostic" not in str(response.data)


def test_completed_move_flags_same_agent_regression(user, organization, workspace, hub_skill, publish_skill, make_skill_files, project, live_service):
    v1 = publish_skill(hub_skill).version
    v2 = publish_skill(hub_skill, files=make_skill_files(hub_skill.name, body="Second published instructions.")).version
    event = HubSkillLabelEvent.objects.create(skill=hub_skill, label="production", from_version=1, to_version=2)
    HubSkillLabelEvent.objects.filter(pk=event.pk).update(created_at=START)
    rows = []
    for version, at in ((v1, START - timedelta(minutes=30)), (v2, START + timedelta(minutes=30))):
        for index in range(30):
            row = load(project, version, start_time=at)
            rows.extend([row, span(str(project.pk), row["trace_id"], id="root", parent_span_id="", name="request", span_attributes={},
                                   start_time=at - timedelta(seconds=1), status="ERROR" if version == v2 and index < 3 else "OK", latency_ms=100)])
    seed_ch_spans(rows, client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, {"resource_id": str(hub_skill.pk), "event_id": str(event.pk), "window": "1h"}, "comparison")
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["verdict"] == "worse" and result["trigger"]["metric"] == "failed_requests"
    assert result["trigger"]["agents"] == 1 and result["primary_action"] == "review_failed"
    assert result["same_agents"]["metrics"][0]["delta"] == pytest.approx(.1)
    assert result["links"]["after"]["failed_requests"]["source"]["event_id"] == str(event.pk)
    library_query = {"from": (END - timedelta(days=7)).isoformat(), "to": END.isoformat(), "group_by": "skill", "skill_ids": str(hub_skill.pk)}
    library = get(user, organization, workspace, library_query)
    assert library.status_code == 200, library.data
    runtime = library.data["result"]["rows"][0]["runtime"]
    assert runtime["loaded_7d"] == 60 and runtime["agents"] == 1
    assert runtime["regression"] is True and runtime["move"]["verdict"] == "worse_so_far"


def test_context_usage_and_closure_comparison(user, organization, workspace, hub_skill, publish_skill, project, live_service):
    from model_hub.models.skills_hub import HubSkill, HubSkillVersion
    from model_hub.models.skills_hub_v12 import HubSkillContextDependency

    leaf = publish_skill(hub_skill).version
    context = HubSkill.objects.create(organization=organization, workspace=workspace, name="qa-context", kind="agent_context")
    one, two = [HubSkillVersion.objects.create(skill=context, number=n, digest="sha256:" + str(n) * 64, file_count=1, total_bytes=1, frontmatter={}, checks={}) for n in (1, 2)]
    HubSkillContextDependency.objects.create(organization=organization, workspace=workspace, parent_version=two,
                                            child_version=leaf, child_skill=hub_skill, mount="skills/help-desk", requested_selector={"version": 1})
    row = load(project, leaf)
    context_attrs = {"fi.context.id": str(context.pk), "fi.context.version_id": str(two.pk), "fi.context.digest": two.digest}
    row["span_attributes"].update(context_attrs)
    context_only = span(str(project.pk), str(uuid4()), name="context.load", span_attributes=context_attrs)
    seed_ch_spans([row, context_only], client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(context, kind="agent_context", context_version_id=str(two.pk)))
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["rows"][0]["skill_id"] == str(hub_skill.pk)
    assert result["links_summary"][0]["loaded_versions"] == [1]
    old = get(user, organization, workspace, params(context, kind="agent_context", context_version_id=str(one.pk)))
    assert old.status_code == 200 and old.data["result"]["rows"] == []
    event = HubSkillLabelEvent.objects.create(skill=context, label="production", from_version=1, to_version=2)
    HubSkillLabelEvent.objects.filter(pk=event.pk).update(created_at=START)
    compared = get(user, organization, workspace, {"resource_id": str(context.pk), "kind": "agent_context", "event_id": str(event.pk), "window": "1h"}, "comparison")
    assert compared.status_code == 200, compared.data
    traces = next(row for row in compared.data["result"]["all_agents"]["metrics"] if row["metric"] == "traces")
    assert traces["after"] == 2  # context.load without any leaf load is a cohort member
    assert compared.data["result"]["changed_skills"] == [{"skill_id": str(hub_skill.pk), "name": hub_skill.name, "from_version": None, "to_version": 1, "change": "added"}]

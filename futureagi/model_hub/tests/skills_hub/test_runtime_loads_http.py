"""Mounted shared-load reads preserve tenant boundaries and fetch/load evidence."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub import runtime_loads
from model_hub.views.skills_hub_runtime import SkillLoadsView
from tracer.models.project import Project

pytestmark = pytest.mark.django_db
URL = "/model-hub/skills/loads/"


def get(user, organization, workspace, resource, **params):
    request = APIRequestFactory().get(URL, {
        "resource_id": str(resource.pk), "kind": resource.kind,
        "since": (timezone.now() - timedelta(minutes=5)).isoformat(), **params,
    })
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = workspace
    return resolve(URL).func(request)


def test_mounted_shared_route():
    assert resolve(URL).func.view_class is SkillLoadsView


@pytest.mark.parametrize("kind", ["skill", "agent_context"])
@pytest.mark.parametrize("language,version_number,capable", [
    ("python", "0.8.0", True), ("python", "0.7.9", False),
    ("typescript", "0.3.0", True), ("typescript", "0.2.9", False),
    (None, "0.9.0", False),
])
def test_fetch_is_not_a_load(user, organization, workspace, hub_skill, publish_skill, kind, monkeypatch,
                             language, version_number, capable):
    version = publish_skill(hub_skill).version
    hub_skill.kind = kind
    hub_skill.save(update_fields=["kind"])
    HubSkillUsageDaily.objects.create(
        skill=hub_skill, version=version, day=timezone.now().date(), channel="sdk",
        consumer_key="credential:private", consumer_label="private credential",
        sdk_version=version_number, sdk_language=language,
        tracing_mode="on", count=1, last_at=timezone.now(),
    )
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", lambda: pytest.fail("no project should query CH"))
    response = get(user, organization, workspace, hub_skill)
    assert response.status_code == 200, response.data
    assert response.data["result"]["load"] is None
    assert response.data["result"]["fetch"]["sdk_version"] == version_number
    assert response.data["result"]["fetch"]["can_record"] is capable
    assert "private" not in str(response.data)


def test_foreign_resource_denied_before_telemetry(user, organization, workspace, hub_skill, monkeypatch):
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", lambda: pytest.fail("denied request queried CH"))
    response = get(user, organization, workspace, hub_skill, resource_id=str(uuid4()))
    assert response.status_code == 404
    assert response.data["code"] == "resource_not_found"


def test_missing_workspace_is_denied(user, organization, hub_skill):
    assert get(user, organization, None, hub_skill).status_code == 403


def test_other_workspace_resource_is_not_visible(user, organization, workspace, hub_skill, monkeypatch):
    other = Workspace.objects.create(organization=organization, name="Other", created_by=user)
    hub_skill.workspace = other
    hub_skill.save(update_fields=["workspace"])
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", lambda: pytest.fail("foreign resource queried CH"))
    response = get(user, organization, workspace, hub_skill)
    assert response.status_code == 404
    assert response.data["code"] == "resource_not_found"


@pytest.mark.parametrize("params", [
    {"kind": "other"}, {"since": "bad"}, {"project_id": str(uuid4())},
    {"since": "2000-01-01T00:00:00Z"},
    {"since": "2999-01-01T00:00:00Z"},
])
def test_invalid_queries_never_read_telemetry(user, organization, workspace, hub_skill, monkeypatch, params):
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", lambda: pytest.fail("invalid query reached CH"))
    assert get(user, organization, workspace, hub_skill, **params).status_code == 400


@pytest.mark.parametrize("kind", ["skill", "agent_context"])
def test_load_enrichment_and_project_scope(user, organization, workspace, hub_skill, publish_skill, monkeypatch, kind):
    version = publish_skill(hub_skill).version
    hub_skill.kind = kind
    hub_skill.save(update_fields=["kind"])
    project = Project.objects.create(
        organization=organization, workspace=workspace, name="Visible", trace_type="observe", model_type="GenerativeLLM",
    )
    other = Workspace.objects.create(organization=organization, name="Other", created_by=user)
    foreign_project = Project.objects.create(
        organization=organization, workspace=other, name="Private", trace_type="observe", model_type="GenerativeLLM",
    )
    statements = []

    class Service:
        def execute_ch_query(self, sql, params, **kwargs):
            statements.append((sql, params, kwargs))
            return SimpleNamespace(row_count=1, data=[{
                "project_id": str(project.pk), "version_id": str(version.pk),
                "start_time": timezone.now(), "service_name": "", "agent_name": "",
            }])

    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", Service)
    response = get(user, organization, workspace, hub_skill)
    assert response.status_code == 200, response.data
    load = response.data["result"]["load"]
    assert load["version"] == version.number
    assert load["agent"]["name"] == "Unnamed agent in Visible"
    assert load["project"] == {"id": str(project.pk), "name": "Visible"}
    assert statements[0][1]["project_ids"] == (str(project.pk),)
    assert str(foreign_project.pk) not in str(statements)
    assert statements[0][1]["version_ids"] == (str(version.pk),)
    assert 0 < statements[0][2]["server_execution_cap_ms"] <= 3000


def test_project_limit_never_returns_a_partial_latest_load(user, organization, workspace, hub_skill, monkeypatch):
    Project.objects.bulk_create([
        Project(organization=organization, workspace=workspace, name=f"Project {index}", trace_type="observe", model_type="GenerativeLLM")
        for index in range(51)
    ])
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", lambda: pytest.fail("over-limit request queried CH"))
    response = get(user, organization, workspace, hub_skill)
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_too_large"


def test_timeout_is_not_no_loads(user, organization, workspace, hub_skill, publish_skill, monkeypatch):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    publish_skill(hub_skill)
    Project.objects.create(organization=organization, workspace=workspace, name="Visible", trace_type="observe", model_type="GenerativeLLM")

    class Service:
        def execute_ch_query(self, *args, **kwargs):
            raise ReadDeadlineExceeded("private diagnostic")

    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", Service)
    response = get(user, organization, workspace, hub_skill)
    assert response.status_code == 503, response.data
    assert response.data["code"] == "runtime_read_budget_exceeded"
    assert "private diagnostic" not in str(response.data)

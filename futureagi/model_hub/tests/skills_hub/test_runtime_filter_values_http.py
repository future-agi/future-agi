"""Mounted picker reads: authorization, enrichment, bounded paging and errors."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.services.skills_hub import runtime_reads
from model_hub.views.skills_hub_runtime import SkillFilterValuesView
from tracer.models.project import Project

pytestmark = pytest.mark.django_db
URL = "/model-hub/skills/filter-values/"


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
def query(project):
    end = timezone.now()
    return {
        "project_id": str(project.pk),
        "field": "skill.agent",
        "from": (end - timedelta(days=7)).isoformat(),
        "to": end.isoformat(),
    }


def get(user, organization, workspace, query):
    request = APIRequestFactory().get(URL, query)
    force_authenticate(request, user=user)
    request.organization, request.workspace = organization, workspace
    return resolve(URL).func(request)


def service(monkeypatch, rows):
    statements = []

    class Service:
        def execute_ch_query(self, sql, params, **kwargs):
            statements.append((sql, params, kwargs))
            data = rows(params) if callable(rows) else rows
            return SimpleNamespace(row_count=len(data), data=data)

    monkeypatch.setattr(runtime_reads, "V2AnalyticsQueryService", Service)
    return statements


def test_route_is_shared():
    assert resolve(URL).func.view_class is SkillFilterValuesView


@pytest.mark.parametrize(
    "field",
    [
        "skill.name",
        "skill.version_id",
        "skill.agent",
        "skill.service",
        "skill.requested_label",
        "skill.context_id",
    ],
)
def test_first_day_empty(user, organization, workspace, query, monkeypatch, field):
    service(monkeypatch, [])
    response = get(user, organization, workspace, {**query, "field": field})
    assert response.status_code == 200, response.data
    assert response.data["result"] == {
        "values": [],
        "next_cursor": None,
        "truncated": False,
    }


def test_blank_agent_is_a_valid_value(
    user, organization, workspace, query, monkeypatch
):
    service(monkeypatch, [{"value": ""}])
    response = get(user, organization, workspace, query)
    assert response.data["result"]["values"] == [{"value": "", "label": ""}]


@pytest.mark.parametrize(
    "params",
    [
        {"field": "sql"},
        {"limit": 0},
        {"limit": 101},
        {"from": "bad"},
        {"from": "2000-01-01T00:00:00Z"},
        {"to": "2999-01-01T00:00:00Z"},
        {"field": "skill.version_id", "load_result": "failed"},
        {"unknown": "x"},
        {"cursor": "not-a-cursor"},
    ],
)
def test_invalid_queries_are_typed_before_ch(
    user, organization, workspace, query, monkeypatch, params
):
    monkeypatch.setattr(
        runtime_reads,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("invalid read reached CH"),
    )
    response = get(user, organization, workspace, {**query, **params})
    assert response.status_code == 400, response.data
    assert response.data["code"] == "invalid_runtime_query"


def test_foreign_project_denied(
    user, organization, workspace, query, project, monkeypatch
):
    other = Workspace.objects.create(
        organization=organization, name="Other", created_by=user
    )
    project.workspace = other
    project.save(update_fields=["workspace"])
    monkeypatch.setattr(
        runtime_reads,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("foreign read reached CH"),
    )
    response = get(user, organization, workspace, query)
    assert response.status_code == 403
    assert response.data["code"] == "project_not_readable"


def test_foreign_skill_denied(user, organization, workspace, query, monkeypatch):
    monkeypatch.setattr(
        runtime_reads,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("foreign read reached CH"),
    )
    response = get(user, organization, workspace, {**query, "skill_id": str(uuid4())})
    assert response.status_code == 404


def test_version_enrichment_and_identity_fence(
    user, organization, workspace, query, hub_skill, publish_skill, monkeypatch
):
    version = publish_skill(hub_skill).version
    statements = service(monkeypatch, [{"value": str(version.pk)}])
    response = get(
        user, organization, workspace, {**query, "field": "skill.version_id"}
    )
    assert response.status_code == 200, response.data
    item = response.data["result"]["values"][0]
    assert item["skill_name"] == hub_skill.name and item["version"] == version.number
    assert item["version_id"] == str(version.pk)
    assert statements[0][1]["verified_identities"] == (
        (str(hub_skill.pk), str(version.pk), version.digest),
    )
    assert statements[0][1]["project_ids"] == (query["project_id"],)
    assert 0 < statements[0][2]["server_execution_cap_ms"] <= 3000


def test_exact_version_label_requires_a_load_in_the_selected_project(
    user, organization, workspace, query, hub_skill, publish_skill, monkeypatch
):
    version = publish_skill(hub_skill).version
    selected = {**query, "field": "skill.version_id", "q": str(version.pk), "limit": 1}
    statements = service(monkeypatch, [])
    assert get(user, organization, workspace, selected).data["result"]["values"] == []
    assert statements[0][1]["project_ids"] == (query["project_id"],)
    assert statements[0][1]["verified_identities"] == (
        (str(hub_skill.pk), str(version.pk), version.digest),
    )
    service(monkeypatch, [{"value": str(version.pk)}])
    [row] = get(user, organization, workspace, selected).data["result"]["values"]
    assert row["label"] == f"{hub_skill.name} v{version.number}"


def test_cursor_is_bound_to_the_exact_read(
    user, organization, workspace, query, monkeypatch
):
    def rows(params):
        return (
            [{"value": "a"}, {"value": "b"}]
            if params["after"] is None
            else [{"value": "b"}]
        )

    statements = service(monkeypatch, rows)
    query = {**query, "limit": 1}
    first = get(user, organization, workspace, query).data["result"]
    assert first["values"] == [{"value": "a", "label": "a"}]
    cursor = first["next_cursor"]
    second = get(user, organization, workspace, {**query, "cursor": cursor}).data[
        "result"
    ]
    assert second["values"] == [{"value": "b", "label": "b"}]
    assert second["next_cursor"] is None and statements[1][1]["after"] == "a"
    for changed in [
        {"field": "skill.service"},
        {"q": "x"},
        {"limit": 2},
        {"load_result": "failed"},
    ]:
        response = get(
            user, organization, workspace, {**query, "cursor": cursor, **changed}
        )
        assert response.status_code == 400, response.data
    assert len(statements) == 2


def test_timeout_is_not_an_empty_picker(
    user, organization, workspace, query, monkeypatch
):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    def timeout(params):
        raise ReadDeadlineExceeded("private diagnostic")

    service(monkeypatch, timeout)
    response = get(user, organization, workspace, query)
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_budget_exceeded"
    assert "private diagnostic" not in str(response.data)


def test_current_skill_name_and_labels_are_authoritative(
    user, organization, workspace, query, hub_skill, publish_skill, monkeypatch
):
    from model_hub.models.skills_hub import HubSkillLabel

    version = publish_skill(hub_skill).version
    HubSkillLabel.objects.update_or_create(
        skill=hub_skill, name="production", defaults={"version": version}
    )
    hub_skill.name = "renamed-skill"
    hub_skill.save(update_fields=["name"])
    service(monkeypatch, [{"value": str(hub_skill.pk)}])
    response = get(
        user, organization, workspace, {**query, "field": "skill.name", "q": "RENAMED"}
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["values"] == [
        {
            "value": str(hub_skill.pk),
            "skill_id": str(hub_skill.pk),
            "label": "renamed-skill",
            "name": "renamed-skill",
            "production_version": version.number,
        }
    ]
    service(monkeypatch, [{"value": str(version.pk)}])
    response = get(
        user,
        organization,
        workspace,
        {**query, "field": "skill.version_id", "q": "production"},
    )
    assert response.data["result"]["values"][0]["labels_now"] == ["production"]


def test_context_names_come_from_workspace_catalog(
    user, organization, workspace, query, hub_skill, monkeypatch
):
    hub_skill.kind = "agent_context"
    hub_skill.save(update_fields=["kind"])
    statements = service(monkeypatch, [{"value": str(hub_skill.pk)}])
    response = get(
        user, organization, workspace, {**query, "field": "skill.context_id"}
    )
    assert response.status_code == 200
    assert response.data["result"]["values"] == [
        {"value": str(hub_skill.pk), "label": hub_skill.name}
    ]
    assert statements[0][1]["context_ids"] == (str(hub_skill.pk),)


def test_foreign_catalog_identity_is_never_enriched(
    user, organization, workspace, query, monkeypatch
):
    service(monkeypatch, [{"value": str(uuid4())}])
    response = get(
        user, organization, workspace, {**query, "field": "skill.version_id"}
    )
    assert response.status_code == 503
    assert response.data["code"] == "runtime_scope_mismatch"


def test_missing_workspace_is_denied(user, organization, query):
    assert get(user, organization, None, query).status_code == 403


def test_unavailable_read_is_typed(user, organization, workspace, query, monkeypatch):
    def unavailable(params):
        raise EOFError("private connection details")

    service(monkeypatch, unavailable)
    response = get(user, organization, workspace, query)
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_unavailable"
    assert "private connection details" not in str(response.data)


def test_catalog_limit_fails_before_telemetry(
    user, organization, workspace, query, hub_skill, publish_skill, monkeypatch
):
    publish_skill(hub_skill)
    monkeypatch.setattr(runtime_reads, "CATALOG_LIMIT", 0)
    monkeypatch.setattr(
        runtime_reads,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("oversized catalog reached CH"),
    )
    response = get(user, organization, workspace, query)
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_too_large"


def test_oversized_cursor_is_not_returned(
    user, organization, workspace, query, monkeypatch
):
    monkeypatch.setattr(runtime_reads, "MAX_CURSOR_LENGTH", 1)
    service(monkeypatch, [{"value": "a"}, {"value": "b"}])
    response = get(user, organization, workspace, {**query, "limit": 1})
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_too_large"


def test_oversized_read_is_not_empty_success(
    user, organization, workspace, query, monkeypatch
):
    service(monkeypatch, [{"value": "a"}] * 102)
    response = get(user, organization, workspace, query)
    assert response.status_code == 503
    assert response.data["code"] == "runtime_read_too_large"


def test_cursor_expiry_is_typed(user, organization, workspace, query, monkeypatch):
    from django.core import signing

    def expired(*args, **kwargs):
        raise signing.SignatureExpired("expired")

    monkeypatch.setattr(runtime_reads.signing, "loads", expired)
    monkeypatch.setattr(
        runtime_reads,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("expired cursor reached CH"),
    )
    response = get(user, organization, workspace, {**query, "cursor": "expired"})
    assert response.status_code == 400
    assert response.data["code"] == "invalid_runtime_query"


def test_failed_names_are_unverified(user, organization, workspace, query, monkeypatch):
    statements = service(monkeypatch, [{"value": "not-published"}])
    response = get(
        user,
        organization,
        workspace,
        {**query, "field": "skill.name", "load_result": "failed"},
    )
    assert response.data["result"]["values"] == [
        {"value": "not-published", "label": "not-published"}
    ]
    assert "skill_load_failures" in statements[0][0]

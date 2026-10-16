"""Insights report API: creating runs, joining a run in flight, gating,
tenant isolation and reading a run's units."""

import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from model_hub.models.ai_model import AIModel
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.models.project import Project
from tracer.views import insights as views

RUNS = "/tracer/insights/runs/"
CONFIGS = "/tracer/insights/configs/"


@pytest.fixture(autouse=True)
def insight_enabled(monkeypatch):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(allowed=True, feature_id=feature_id),
    )
    # Runs are started on Temporal; record the start instead.
    started = []
    monkeypatch.setattr(views, "_start", lambda run: started.append(run.id))
    monkeypatch.setattr(views, "_spend", lambda *a: True)
    return started


def population(n=30):
    return [
        {"id": str(uuid.uuid4()), "time": f"2026-10-{1 + i % 28:02d}T10:00:00Z", "user_id": f"u{i}"}
        for i in range(n)
    ]


def new_run_body(project, n=30, sample_size=10):
    return {
        "project_id": str(project.id),
        "config": {
            "name": "Where is the agent failing?",
            "unit": "sessions",
            "lens": "failures",
            "question": "Where is the agent failing?",
            "attributes": [{"key": "asked_for_human", "type": "boolean"}],
        },
        "scope": {"filters": [], "label": "Past 7 days", "preset": "7d"},
        "population": population(n),
        "sample_size": sample_size,
    }


@pytest.mark.django_db
def test_a_new_report_saves_its_config_and_samples_the_population(
    auth_client, observe_project, insight_enabled
):
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    run = InsightRun.objects.get(id=result["run_id"])
    config = InsightConfig.objects.get(id=result["config_id"])
    assert config.unit == "sessions" and config.lens == "failures"
    assert config.scope["preset"] == "7d"
    assert run.population_size == 30
    assert run.sample_size == 10
    assert InsightUnit.objects.filter(run=run).count() == 10
    assert run.taxonomy_mode == "discover"
    assert insight_enabled == [run.id]


@pytest.mark.django_db
def test_run_again_while_a_run_is_in_flight_joins_it(
    auth_client, observe_project, insight_enabled
):
    first = auth_client.post(RUNS, new_run_body(observe_project), format="json").json()["result"]
    again = auth_client.post(RUNS, {
        "project_id": str(observe_project.id),
        "config_id": first["config_id"],
        "population": population(5),
    }, format="json").json()["result"]
    assert again["run_id"] == first["run_id"]
    assert InsightRun.objects.filter(config_id=first["config_id"]).count() == 1

    InsightRun.objects.filter(id=first["run_id"]).update(status="completed")
    third = auth_client.post(RUNS, {
        "project_id": str(observe_project.id),
        "config_id": first["config_id"],
        "population": population(5),
    }, format="json").json()["result"]
    assert third["run_id"] != first["run_id"]


@pytest.mark.django_db
def test_reports_need_the_turing_capability(auth_client, observe_project, monkeypatch):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(allowed=False, feature_id=feature_id),
    )
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert not InsightConfig.objects.exists()


@pytest.mark.django_db
def test_other_organizations_projects_and_runs_are_not_found(auth_client, observe_project):
    other = Organization.objects.create(name="Other org")
    project = getattr(Project, "no_workspace_objects", Project.objects).create(
        name="Elsewhere",
        organization=other,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    assert auth_client.post(RUNS, new_run_body(project), format="json").status_code == 404
    assert auth_client.get(CONFIGS, {"project_id": str(project.id)}).status_code == 404
    assert auth_client.get(f"{RUNS}{uuid.uuid4()}/").status_code == 404


@pytest.mark.django_db
def test_run_units_filter_by_category_and_skip_left_out_units(auth_client, observe_project):
    created = auth_client.post(RUNS, new_run_body(observe_project), format="json").json()["result"]
    units = list(InsightUnit.objects.filter(run_id=created["run_id"]).order_by("id"))
    for i, u in enumerate(units):
        u.status = "done"
        u.included = i != 0
        u.category_id = "top-a" if i < 6 else "top-b"
        u.sub_category_id = "sub-a1" if i < 3 else "sub-x"
        u.summary = f"summary {i}"
    InsightUnit.objects.bulk_update(
        units, ["status", "included", "category_id", "sub_category_id", "summary"]
    )
    base = f"{RUNS}{created['run_id']}/units/"
    by_top = auth_client.get(base, {"category_id": "top-a"}).json()["result"]
    assert by_top["total"] == 5  # unit 0 was left out by the lens
    by_sub = auth_client.get(base, {"category_id": "sub-a1"}).json()["result"]
    assert by_sub["total"] == 2
    wanted = units[4].unit_id
    picked = auth_client.get(base, {"unit_ids": wanted}).json()["result"]
    assert [u["unit_id"] for u in picked["units"]] == [wanted]


@pytest.mark.django_db
def test_saved_reports_list_their_latest_run(auth_client, observe_project):
    created = auth_client.post(RUNS, new_run_body(observe_project), format="json").json()["result"]
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()["result"]["configs"]
    assert [c["id"] for c in configs] == [created["config_id"]]
    assert configs[0]["latest_run"]["id"] == created["run_id"]
    deleted = auth_client.delete(f"{CONFIGS}{created['config_id']}/")
    assert deleted.status_code == 200
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()["result"]["configs"]
    assert configs == []

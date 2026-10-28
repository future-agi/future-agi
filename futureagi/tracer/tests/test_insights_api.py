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
# The real start, which the autouse stub below replaces with a recorder.
REAL_START = views._start


@pytest.fixture(autouse=True)
def insight_enabled(monkeypatch):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    # Insights is Enterprise-only: run it as a licensed install does.
    monkeypatch.setattr(capability_service, "_configured", True)
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
        {
            "id": str(uuid.uuid4()),
            "time": f"2026-10-{1 + i % 28:02d}T10:00:00Z",
            "user_id": f"u{i}",
        }
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
    # Insights' earlier "7d" is stored as Observe's "7D".
    assert config.scope["preset"] == "7D"
    assert run.population_size == 30
    assert run.sample_size == 10
    assert InsightUnit.objects.filter(run=run).count() == 10
    assert run.taxonomy_mode == "discover"
    assert insight_enabled == [run.id]


@pytest.mark.django_db
def test_run_again_while_a_run_is_in_flight_joins_it(
    auth_client, observe_project, insight_enabled
):
    first = auth_client.post(RUNS, new_run_body(observe_project), format="json").json()[
        "result"
    ]
    again = auth_client.post(
        RUNS,
        {
            "project_id": str(observe_project.id),
            "config_id": first["config_id"],
            "population": population(5),
        },
        format="json",
    ).json()["result"]
    assert again["run_id"] == first["run_id"]
    assert InsightRun.objects.filter(config_id=first["config_id"]).count() == 1

    InsightRun.objects.filter(id=first["run_id"]).update(status="completed")
    third = auth_client.post(
        RUNS,
        {
            "project_id": str(observe_project.id),
            "config_id": first["config_id"],
            "population": population(5),
        },
        format="json",
    ).json()["result"]
    assert third["run_id"] != first["run_id"]


@pytest.mark.django_db
def test_reports_need_the_insights_capability(
    auth_client, observe_project, monkeypatch
):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(
            allowed=feature_id != "insights", feature_id=feature_id
        ),
    )
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.json()["error"]["detail"]["feature"] == "insights"
    assert not InsightConfig.objects.exists()


@pytest.mark.django_db
def test_reports_on_turing_models_need_the_turing_capability(
    auth_client, observe_project, monkeypatch
):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision
    from tfc.ee_gates import insights_turing_denied_message

    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(
            allowed=feature_id != "turing_models", feature_id=feature_id
        ),
    )
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.json()["message"] == insights_turing_denied_message()
    assert not InsightConfig.objects.exists()


@pytest.mark.django_db
def test_other_organizations_projects_and_runs_are_not_found(
    auth_client, observe_project
):
    other = Organization.objects.create(name="Other org")
    project = getattr(Project, "no_workspace_objects", Project.objects).create(
        name="Elsewhere",
        organization=other,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    assert (
        auth_client.post(RUNS, new_run_body(project), format="json").status_code == 404
    )
    assert auth_client.get(CONFIGS, {"project_id": str(project.id)}).status_code == 404
    assert auth_client.get(f"{RUNS}{uuid.uuid4()}/").status_code == 404


@pytest.mark.django_db
def test_run_units_filter_by_category_and_skip_left_out_units(
    auth_client, observe_project
):
    created = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
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
    # No page starts past the most units a run can read.
    assert auth_client.get(base, {"offset": views.MAX_SAMPLE}).status_code == 200
    assert auth_client.get(base, {"offset": views.MAX_SAMPLE + 1}).status_code == 400


@pytest.mark.django_db
def test_run_units_give_v2_evidence_as_the_list_the_report_page_reads(
    auth_client, observe_project
):
    created = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
    unit = InsightUnit.objects.filter(run_id=created["run_id"]).order_by("id").first()
    receipt = {
        "kind": "text",
        "evidence_id": f"ev2|{unit.pk}|s3|output|10|15",
        "span": "s3",
        "field": "output",
        "quote": "refund=declined",
        "supports": "failure",
    }
    image = {
        "kind": "media",
        "evidence_id": f"ev2m|{unit.pk}|m1|full",
        "media": "m1",
        "observation": "A crushed box.",
    }
    unit.status, unit.included = "done", True
    unit.facets = {
        "summary": "Refund refused",
        "evidence": [receipt, image],
        "evidence_meta": {"dropped": 1, "coverage_note": "Read the refund span."},
    }
    unit.metrics = {"coverage": {"chars_total": 182_000, "chars_read": 21_000}}
    unit.save(update_fields=["status", "included", "facets", "metrics"])

    result = auth_client.get(f"{RUNS}{created['run_id']}/units/").json()["result"]

    [shown] = result["units"]
    # The citation card and the example panel call list methods on it.
    assert shown["facets"]["evidence"] == [receipt, image]
    assert shown["facets"]["evidence_meta"]["dropped"] == 1
    assert shown["metrics"]["coverage"]["chars_total"] == 182_000


@pytest.mark.django_db
def test_saved_reports_list_their_latest_run(auth_client, observe_project):
    created = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()[
        "result"
    ]["configs"]
    assert [c["id"] for c in configs] == [created["config_id"]]
    assert configs[0]["latest_run"]["id"] == created["run_id"]
    deleted = auth_client.delete(f"{CONFIGS}{created['config_id']}/")
    assert deleted.status_code == 200
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()[
        "result"
    ]["configs"]
    assert configs == []
    # Its runs are soft-deleted like any row: flagged and stamped.
    run = InsightRun.all_objects.get(id=created["run_id"])
    assert run.deleted and run.deleted_at is not None


@pytest.mark.django_db
def test_without_the_pipeline_edit_and_ask_answer_403_like_their_siblings(
    auth_client, observe_project, monkeypatch
):
    """A build without the Insights pipeline refuses every model step with
    the same 403, never an ImportError 500."""
    import builtins

    config = InsightConfig.objects.create(
        project=observe_project,
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        name="Where is the agent failing?",
        unit="sessions",
        lens="failures",
        question="Where is the agent failing?",
    )
    run = InsightRun.objects.create(
        config=config,
        project=observe_project,
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        unit="sessions",
        status="completed",
        result={"categories": [{"id": "a"}]},
    )
    real_import = builtins.__import__

    def no_insights(name, *args, **kwargs):
        if name.startswith("ee.agenthub.insights"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_insights)
    edited = auth_client.patch(
        f"{CONFIGS}{config.id}/", {"name": "Renamed"}, format="json"
    )
    asked = auth_client.post(
        f"{RUNS}{run.id}/ask/", {"question": "Why?"}, format="json"
    )
    for response in (edited, asked):
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert response.json()["code"] == "feature_unavailable"
    config.refresh_from_db()
    assert config.name == "Where is the agent failing?"


SAVED_TAXONOMY = {
    "tops": [{"id": "t1", "name": "Order lookups"}],
    "subs": [{"id": "s1", "parent_id": "t1", "name": "Re-asks the order number"}],
}


@pytest.mark.django_db
def test_rediscover_forgets_the_saved_categories_only_once_its_run_starts(
    auth_client, observe_project, insight_enabled, monkeypatch
):
    """A refused run, a run that can't be created and one that can't start
    leave the saved categories as they were."""
    from ee.agenthub.insights import pipeline

    created = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
    InsightRun.objects.filter(id=created["run_id"]).update(status="completed")
    config = InsightConfig.objects.get(id=created["config_id"])
    config.taxonomy = SAVED_TAXONOMY
    config.save(update_fields=["taxonomy"])
    again = {
        "project_id": str(observe_project.id),
        "config_id": created["config_id"],
        "population": population(5),
        "reset_taxonomy": True,
    }

    def saved():
        config.refresh_from_db()
        return config.taxonomy

    with monkeypatch.context() as patch:
        patch.setattr(views, "_spend", lambda *a: False)
        refused = auth_client.post(RUNS, again, format="json")
    assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert saved() == SAVED_TAXONOMY

    def no_run(**kwargs):
        raise RuntimeError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "create_run", no_run)
        with pytest.raises(RuntimeError):
            auth_client.post(RUNS, again, format="json")
    assert saved() == SAVED_TAXONOMY

    def no_temporal(run_id, task_queue="tasks_xl"):
        raise RuntimeError("Temporal is down")

    with monkeypatch.context() as patch:
        patch.setattr(views, "_start", REAL_START)
        patch.setattr("tfc.temporal.insights.client.start_insight_run", no_temporal)
        unstarted = auth_client.post(RUNS, again, format="json").json()["result"]
    assert InsightRun.objects.get(id=unstarted["run_id"]).status == "failed"
    assert saved() == SAVED_TAXONOMY

    started = auth_client.post(RUNS, again, format="json").json()["result"]
    run = InsightRun.objects.get(id=started["run_id"])
    assert run.taxonomy_mode == "discover"
    assert saved() == {}

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
def test_a_cited_image_is_served_to_its_own_organization_only(
    auth_client, observe_project, monkeypatch
):
    """The example panel shows the screenshot a finding rests on: rebuilt
    from the unit as the run read it, for this organization's kept unit and
    the media its evidence names, and nothing else."""
    import base64
    import io

    from PIL import Image

    from ee.agenthub.insights import broker

    buffer = io.BytesIO()
    Image.new("RGB", (2400, 1200), "blue").save(buffer, "PNG")
    entry = {
        "alias": "m1",
        "span": "s2",
        "field": "input",
        "kind": "image",
        "mime": "image/png",
        "accessible": True,
        "reason": None,
    }
    source = ["data_uri", base64.b64encode(buffer.getvalue()).decode(), "image/png"]
    built = []

    def build(run, row):
        built.append(row.unit_id)
        manifest = {"unit": {"key": str(row.pk)}, "spans": [], "media": [entry]}
        return broker.Cached({"manifest": manifest, "fields": {}}, {"m1": source})

    monkeypatch.setattr(broker, "build", build)
    created = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
    InsightRun.objects.filter(id=created["run_id"]).update(status="completed")
    kept, left_out = InsightUnit.objects.filter(run_id=created["run_id"]).order_by(
        "id"
    )[:2]
    image = {
        "kind": "media",
        "evidence_id": f"ev2m|{kept.pk}|m1|full",
        "media": "m1",
        "span": "s2",
        "observation": "Card declined, code 51.",
    }
    for unit, included in ((kept, True), (left_out, False)):
        unit.status, unit.included = "done", included
        unit.facets = {"evidence": [image]}
        unit.save(update_fields=["status", "included", "facets"])
    url = f"{RUNS}{created['run_id']}/media/"

    response = auth_client.get(url, {"unit_id": kept.unit_id, "media": "m1"})

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"].startswith("private")
    with Image.open(io.BytesIO(response.content)) as shown:
        assert max(shown.size) == broker.MAX_IMAGE_SIDE
    assert built == [kept.unit_id]

    # Media the evidence doesn't name, a unit the report left out, and a
    # malformed alias are refused before anything is built.
    for params, code in (
        ({"unit_id": kept.unit_id, "media": "m2"}, 404),
        ({"unit_id": left_out.unit_id, "media": "m1"}, 404),
        ({"unit_id": kept.unit_id, "media": "../m1"}, 400),
    ):
        assert auth_client.get(url, params).status_code == code
    assert built == [kept.unit_id]

    # Past the organization's hourly bound nothing more is rebuilt.
    monkeypatch.setattr(views, "_spend", lambda kind, *a: kind != "media")
    limited = auth_client.get(url, {"unit_id": kept.unit_id, "media": "m1"})
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert (
        limited.json()["message"]
        == "Too many images opened this hour; try again later."
    )
    assert built == [kept.unit_id]
    monkeypatch.setattr(views, "_spend", lambda *a: True)

    # Another organization's run is not found, whatever it cites.
    other = Organization.objects.create(name="Other org")
    InsightRun.objects.filter(id=created["run_id"]).update(organization=other)
    assert (
        auth_client.get(url, {"unit_id": kept.unit_id, "media": "m1"}).status_code
        == 404
    )
    assert built == [kept.unit_id]


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


# ─── Stop ────────────────────────────────────────────────────────────────────
@pytest.fixture
def cancelled(monkeypatch):
    """Workflow ids Temporal was asked to cancel."""
    from tfc.temporal.common import client

    asked = []
    monkeypatch.setattr(
        client, "cancel_workflow_sync", lambda workflow_id: asked.append(workflow_id)
    )
    return asked


def running_run(client, project, **values):
    run_id = client.post(RUNS, new_run_body(project), format="json").json()["result"][
        "run_id"
    ]
    InsightRun.objects.filter(id=run_id).update(
        status="running", workflow_id="insight-run-wf", **values
    )
    return run_id


@pytest.mark.django_db
def test_stop_fails_a_run_in_flight_once_and_cancels_its_workflow(
    auth_client, observe_project, user, cancelled
):
    run_id = running_run(auth_client, observe_project)
    stopped = f"Stopped by {user.name or user.email}."

    response = auth_client.post(f"{RUNS}{run_id}/stop/")
    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == {
        "id": run_id,
        "status": "failed",
        "error": stopped,
        "error_code": "stopped",
    }
    assert cancelled == ["insight-run-wf"]
    run = InsightRun.objects.get(id=run_id)
    assert (run.status, run.error) == ("failed", stopped)
    assert run.finished_at is not None
    detail = auth_client.get(f"{RUNS}{run_id}/").json()["result"]
    assert (detail["error"], detail["error_code"]) == (stopped, "stopped")
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()[
        "result"
    ]["configs"]
    assert configs[0]["latest_run"]["error_code"] == "stopped"

    # Again: the same answer, and nothing more is cancelled.
    again = auth_client.post(f"{RUNS}{run_id}/stop/")
    assert again.status_code == status.HTTP_200_OK
    assert again.json()["result"]["error"] == stopped
    assert cancelled == ["insight-run-wf"]


@pytest.mark.django_db
def test_stop_still_stops_the_run_when_temporal_cannot_be_reached(
    auth_client, observe_project, monkeypatch
):
    from tfc.temporal.common import client

    def unreachable(workflow_id):
        raise ConnectionError("temporal down")

    monkeypatch.setattr(client, "cancel_workflow_sync", unreachable)
    run_id = running_run(auth_client, observe_project)
    response = auth_client.post(f"{RUNS}{run_id}/stop/")
    assert response.status_code == status.HTTP_200_OK
    assert InsightRun.objects.get(id=run_id).status == "failed"


@pytest.mark.django_db
@pytest.mark.parametrize("ended", ["completed", "failed"])
def test_stop_refuses_a_run_that_already_ended(
    auth_client, observe_project, cancelled, ended
):
    run_id = running_run(auth_client, observe_project)
    InsightRun.objects.filter(id=run_id).update(status=ended, error="lost")
    response = auth_client.post(f"{RUNS}{run_id}/stop/")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["message"] == "This run has already finished."
    run = InsightRun.objects.get(id=run_id)
    assert (run.status, run.error) == (ended, "lost")
    assert cancelled == []


@pytest.mark.django_db
def test_stop_never_reaches_another_organizations_run(
    auth_client, observe_project, cancelled
):
    other = Organization.objects.create(name="Other org")
    project = getattr(Project, "no_workspace_objects", Project.objects).create(
        name="Elsewhere",
        organization=other,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    config = InsightConfig.objects.create(
        project=project,
        organization=other,
        workspace=project.workspace,
        name="Theirs",
        unit="sessions",
        lens="failures",
        question="Theirs?",
    )
    theirs = getattr(InsightRun, "no_workspace_objects", InsightRun.objects).create(
        config=config,
        project=project,
        organization=other,
        workspace=project.workspace,
        unit="sessions",
        status="running",
    )
    assert auth_client.post(f"{RUNS}{theirs.id}/stop/").status_code == 404
    assert auth_client.post(f"{RUNS}{uuid.uuid4()}/stop/").status_code == 404
    theirs.refresh_from_db()
    assert theirs.status == "running"
    assert cancelled == []


@pytest.mark.django_db
def test_a_stopped_runs_batches_under_way_are_listed_for_two_minutes_at_most(
    auth_client, observe_project, cancelled
):
    # Stopped while a batch was being read: its page waits for that batch's
    # units, but not for a worker lost meanwhile, and the server's clock,
    # not the browser's, says when it has waited long enough.
    from datetime import timedelta

    from django.utils import timezone

    batch = {"7": "2026-10-28T08:00:00+00:00"}
    run_id = running_run(
        auth_client, observe_project, progress={"stage": "reading", "in_flight": batch}
    )
    assert auth_client.post(f"{RUNS}{run_id}/stop/").status_code == 200
    detail = auth_client.get(f"{RUNS}{run_id}/").json()["result"]
    assert detail["progress"]["in_flight"] == batch

    InsightRun.objects.filter(id=run_id).update(
        finished_at=timezone.now() - timedelta(minutes=2)
    )
    detail = auth_client.get(f"{RUNS}{run_id}/").json()["result"]
    assert detail["progress"] == {"stage": "reading", "error_code": "stopped"}
    # Only the answer: the run still holds what the worker wrote.
    assert InsightRun.objects.get(id=run_id).progress["in_flight"] == batch


@pytest.mark.django_db
def test_a_run_failed_on_its_model_setup_says_so(auth_client, observe_project):
    run_id = running_run(auth_client, observe_project)
    InsightRun.objects.filter(id=run_id).update(
        status="failed", error="licence", progress={"error_code": "license"}
    )
    assert auth_client.get(f"{RUNS}{run_id}/").json()["result"]["error_code"] == (
        "license"
    )
    # A run still in flight has no error code, whatever its progress holds.
    InsightRun.objects.filter(id=run_id).update(status="running")
    assert auth_client.get(f"{RUNS}{run_id}/").json()["result"]["error_code"] == ""


@pytest.mark.django_db
def test_draft_and_preview_name_a_licence_that_cannot_activate(
    auth_client, observe_project, monkeypatch
):
    """Draft with AI and Preview make the run's model calls: a licence the
    managed service won't activate fails them at once, with the same reason
    and fix as the run."""
    from types import SimpleNamespace

    from ee.agenthub.insights import agent, draft, llm
    from ee.licensing.activation_client import ManagedServiceError

    calls = []

    def refuse(*_):
        calls.append(1)
        raise ManagedServiceError("ACTIVATION_FAILED", "Could not obtain service token")

    unit = SimpleNamespace(
        unit_id="s1",
        content="User: cancel my plan",
        label="s1",
        excerpt="cancel",
        user_id="u1",
    )
    monkeypatch.setattr(draft, "load_units", lambda *a, **k: {"s1": unit})
    monkeypatch.setattr(agent, "enabled", lambda: False)
    monkeypatch.setattr(llm, "_complete", refuse)
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    drafted = auth_client.post(
        "/tracer/insights/draft/",
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "lens": "failures",
            "sample_ids": ["s1"],
        },
        format="json",
    )
    previewed = auth_client.post(
        "/tracer/insights/preview/",
        {
            "project_id": str(observe_project.id),
            "config": new_run_body(observe_project)["config"],
            "unit_ids": ["s1"],
        },
        format="json",
    )
    for response in (drafted, previewed):
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        body = response.json()
        assert body["code"] == "insight_unavailable"
        assert body["message"] == llm._MANAGED_REFUSALS["ACTIVATION_FAILED"]
    # One call each: none retried.
    assert len(calls) == 2

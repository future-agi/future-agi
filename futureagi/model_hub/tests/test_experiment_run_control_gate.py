"""F06 selected-run control stays OFF independently of the classic whole stop.

OFF (EXPERIMENT_RUN_CONTROL_ENABLED unset or anything but True): resume,
run-control status and a stop that names run_ids answer 404
``run_control_not_enabled`` before tenant lookups, Temporal traffic or writes.
The empty-body whole-experiment stop keeps its cancel-and-cleanup contract, and
the generated schema documents /resume/ and /run-control/ even while OFF.
Their descriptions explicitly disclose the operator opt-in. Workflow, worker and
run-state modules are not gated: their binding and completion writes are
ordinary-run bookkeeping. The ON arm of the original F06 assertions lives in
test_experiment_run_control.py.
"""

import json
from uuid import uuid4

import pytest
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiment_run_control import (
    ExperimentResumeV2View,
    ExperimentRunControlView,
)
from model_hub.views.experiments import ExperimentStopV2View

SETTING = "EXPERIMENT_RUN_CONTROL_ENABLED"
CODE = "run_control_not_enabled"
CONTROL = "model_hub.views.experiment_run_control.run_control"
TEMPORAL = "model_hub.services.experiment_run_control.get_client"
CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"


@pytest.fixture
def off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


@pytest.fixture
def no_control(monkeypatch):
    """Every F06 effect fails the test: control path, Temporal, whole cancel."""

    def fail(name):
        def refuse(*args, **kwargs):
            pytest.fail(f"OFF reached {name}")

        return refuse

    monkeypatch.setattr(CONTROL, fail("run_control"))
    monkeypatch.setattr(TEMPORAL, fail("Temporal"))
    monkeypatch.setattr(CANCEL, fail("whole-experiment cancellation"))


def state(experiment):
    experiment.refresh_from_db()
    return (
        experiment.status,
        sorted(
            ExperimentRunControl.objects.filter(
                experiment_dataset__experiment=experiment
            ).values_list("id", "phase", "workflow_run_id")
        ),
        sorted(
            Cell.objects.filter(dataset=experiment.dataset).values_list(
                "id", "status", "value"
            )
        ),
    )


def bind(variant, phase="RUNNING"):
    run, config, _ = variant
    return ExperimentRunControl.objects.create(
        experiment_dataset=run,
        config_id=config.id,
        workflow_id=f"control-{config.id}",
        workflow_run_id=uuid4(),
        phase=phase,
    )


def assert_not_enabled(response):
    assert response.status_code == 404, response.data
    assert response.data["code"] == CODE, response.data
    assert "Run control is not available." in json.dumps(response.data)


def entries(variants):
    selected = {"run_ids": [str(variants[0][0].id)]}
    return [
        (ExperimentResumeV2View, "post", selected),
        (ExperimentResumeV2View, "post", {}),
        (ExperimentRunControlView, "get", None),
        (ExperimentStopV2View, "post", selected),
    ]


def test_setting_defaults_off(settings):
    # The qualification job must leave EXPERIMENT_RUN_CONTROL_ENABLED unset.
    assert getattr(settings, SETTING, None) is False


@pytest.mark.django_db
@pytest.mark.parametrize("entry", range(4))
@pytest.mark.parametrize("phase", ["RUNNING", "STOPPED"])
def test_off_refuses_selected_control_before_any_effect(
    user, workspace, organization, off, no_control, entry, phase
):
    experiment, _, variants = seed(organization, workspace, user)
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    # A run paused while ON stays paused; OFF never resumes or rewrites it.
    bind(variants[0], phase)
    view, method, data = entries(variants)[entry]
    before = state(experiment)
    assert_not_enabled(call(view, method, experiment.id, user, workspace, data))
    assert state(experiment) == before


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["missing", None, "true", 1, "True"])
def test_only_an_explicit_true_setting_opens_selected_control(
    user, workspace, organization, no_control, settings, value
):
    experiment, _, variants = seed(organization, workspace, user)
    # Only the settings fixture changes the setting, so teardown restores it.
    if value == "missing":
        del settings.EXPERIMENT_RUN_CONTROL_ENABLED
    else:
        settings.EXPERIMENT_RUN_CONTROL_ENABLED = value
    for view, method, data in entries(variants):
        assert_not_enabled(call(view, method, experiment.id, user, workspace, data))


@pytest.mark.django_db
def test_off_refusal_does_not_reveal_another_tenant(
    user, workspace, organization, off, no_control
):
    from accounts.models import Workspace

    other = Workspace.objects.create(
        name="Other scope", organization=organization, created_by=user, is_default=False
    )
    experiment, _, variants = seed(organization, other, user)
    for view, method, data in entries(variants):
        assert_not_enabled(call(view, method, experiment.id, user, workspace, data))
    for view, method, data in entries(variants):
        assert_not_enabled(call(view, method, uuid4(), user, workspace, data))


@pytest.mark.django_db
@pytest.mark.parametrize("status", [StatusType.RUNNING.value, StatusType.QUEUED.value])
def test_off_classic_whole_stop_still_cancels_and_is_idempotent(
    user, workspace, organization, off, monkeypatch, status
):
    experiment, _, variants = seed(organization, workspace, user)
    experiment.status = status
    experiment.save(update_fields=["status"])
    bind(variants[0])
    cancelled = []

    def cancel(experiment_id):
        cancelled.append(experiment_id)
        return {"main_cancelled": True, "rerun_cancelled": False}

    monkeypatch.setattr(CANCEL, cancel)
    monkeypatch.setattr(TEMPORAL, lambda: pytest.fail("cooperative control"))
    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": False,
    }
    assert cancelled == [str(experiment.id)]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value
    again = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert again.status_code == 200, again.data
    assert cancelled == [str(experiment.id)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data",
    [{"run_ids": []}, {"run_ids": None}, {"run_ids": ["not-a-uuid"]}, {"x": 1}],
)
@pytest.mark.parametrize("view", [ExperimentStopV2View, ExperimentResumeV2View])
def test_off_invalid_selection_is_still_a_plain_400(
    user, workspace, organization, off, no_control, view, data
):
    # Request validation (pure, no lookup) runs before the in-view gate, as
    # before; an invalid selection never becomes a whole-experiment stop.
    experiment, _, _ = seed(organization, workspace, user)
    before = state(experiment)
    response = call(view, "post", experiment.id, user, workspace, data)
    assert response.status_code == 400, response.data
    assert state(experiment) == before


@pytest.mark.django_db
def test_on_opens_each_selected_entry_to_the_unchanged_control_path(
    user, workspace, organization, settings, monkeypatch
):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True
    experiment, _, variants = seed(organization, workspace, user)
    calls = []

    def control(request, experiment_id, run_ids=None, *, stopped=None):
        calls.append((str(experiment_id), run_ids, stopped))
        return {"experiment_id": str(experiment_id), "runs": []}

    monkeypatch.setattr(CONTROL, control)
    monkeypatch.setattr(CANCEL, lambda *a: pytest.fail("whole cancel"))
    run_id = variants[0][0].id
    expected = [
        (202, (str(experiment.id), [run_id], False)),
        (202, (str(experiment.id), None, False)),
        (200, (str(experiment.id), None, None)),
        (202, (str(experiment.id), [run_id], True)),
    ]
    for (view, method, data), (code, args) in zip(
        entries(variants), expected, strict=True
    ):
        response = call(view, method, experiment.id, user, workspace, data)
        assert response.status_code == code, response.data
        assert calls[-1] == args


@pytest.mark.django_db
@pytest.mark.parametrize("method,suffix", [("post", "resume"), ("get", "run-control")])
def test_off_http_routes_return_the_gated_refusal(
    auth_client, user, workspace, organization, off, no_control, method, suffix
):
    experiment, _, variants = seed(organization, workspace, user)
    bind(variants[0], "STOPPED")
    before = state(experiment)
    url = f"/model-hub/experiments/v2/{experiment.id}/{suffix}/"
    response = getattr(auth_client, method)(url, {}, format="json")
    assert_not_enabled(response)
    assert state(experiment) == before


def schema_document():
    from model_hub import urls

    names = {
        "experiments-v2-create",
        "experiments-v2-stop",
        "experiments-v2-resume",
        "experiments-v2-run-control",
    }
    patterns = [p for p in urls.urlpatterns if getattr(p, "name", None) in names]
    assert {p.name for p in patterns} == names
    generator = OpenAPISchemaGenerator(
        openapi.Info(title="Run control boundary", default_version="v1"),
        patterns=[path("model-hub/", include(patterns))],
    )
    return json.loads(
        OpenAPICodecJson(validators=[]).encode(
            generator.get_schema(request=None, public=True)
        )
    )


def route(paths, suffix):
    found = [methods for key, methods in paths.items() if key.endswith(suffix)]
    assert len(found) <= 1, paths
    return found[0] if found else None


@pytest.mark.parametrize("enabled", [False, True])
def test_schema_always_documents_control_and_keeps_the_classic_stop(settings, enabled):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = enabled
    paths = schema_document()["paths"]
    assert "post" in route(paths, "/{experiment_id}/stop/")
    for method, suffix in (("post", "resume"), ("get", "run-control")):
        operation = route(paths, f"/{{experiment_id}}/{suffix}/")
        assert operation is not None, suffix
        assert method in operation, suffix
        description = operation[method]["description"]
        assert (
            "DISABLED unless the operator enables EXPERIMENT_RUN_CONTROL_ENABLED"
            in description
        )
        assert "404" in description
        assert CODE in description


def test_schema_is_identical_with_run_control_off_and_on(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False
    disabled = schema_document()
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True
    assert schema_document() == disabled

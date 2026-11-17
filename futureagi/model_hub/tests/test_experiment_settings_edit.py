"""An edit saves an experiment's run settings for its next run: its metadata,
its error policy and its time limit. Finished runs keep what they ran with,
the rows an experiment runs on stay as created, and a server that turns run
settings off refuses them with a sentence a person can act on."""

# Imported fixtures are injected by pytest.
# ruff: noqa: F811

from copy import deepcopy
from unittest.mock import patch

import pytest
from django.utils import timezone

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.serializers.experiments import ExperimentUpdateV2Serializer
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.experiment_scope_gate import (
    SCOPE_OFF_MESSAGE,
    UNAVAILABLE_ROW_LIMITS,
    UNAVAILABLE_ROW_LIMITS_MESSAGE,
)
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
)
from model_hub.tests.test_experiment_scope_gate import schema
from model_hub.tests.test_experiment_version_params import (
    create_run,
    version_world,  # noqa: F401
)
from model_hub.tests.test_http_experiment_source import tenants  # noqa: F401
from model_hub.tests.test_http_mutations import finished  # noqa: F401
from model_hub.tests.test_http_mutations import put as http_put

pytestmark = pytest.mark.django_db(transaction=True)
START = "tfc.temporal.experiments.start_experiment_v2_workflow"
EDITABLE = {"metadata", "error_policy", "limits"}
# The words a person reads when an edit tries to sample other rows.
SAMPLE_FIXED = (
    "An experiment's rows are chosen when it is created, so its sample can't "
    "change. Create a new experiment to sample other rows."
)


def idle(experiment):
    """Every run of the experiment is over, as after its worker completes."""
    ExperimentRun.no_workspace_objects.filter(
        native_binding__snapshot__experiment=experiment
    ).update(status="completed", completed_at=timezone.now())
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.COMPLETED.value
    )
    experiment.refresh_from_db()
    return experiment


def created(auth_client, world, **settings):
    record = create_run(auth_client, request_body(world, **settings))
    idle(record.experiment_dataset.experiment)
    return record


def edit(auth_client, experiment, body):
    with patch(START, return_value="settings-edit") as start:
        response = auth_client.put(f"{CREATE_URL}{experiment.id}/", body, format="json")
    return response, start


def changed_prompt(world, record):
    """The run's entry with new model parameters: an edit that runs it again."""
    return {
        **request_body(world)["prompt_config"][0],
        "id": str(record.id),
        "model_params": {"temperature": 0.9, "maxTokens": 96},
    }


def runs_of(experiment):
    return list(
        ExperimentRun.no_workspace_objects.filter(
            native_binding__snapshot__experiment=experiment
        ).order_by("ordinal")
    )


def test_edit_saves_run_settings_for_the_next_run(auth_client, scope_world):
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    identity = deepcopy(experiment.run_metadata["native_snapshot"])
    response, start = edit(
        auth_client,
        experiment,
        {
            "metadata": {"purpose": "edited"},
            "error_policy": "zero",
            "limits": {"timeout_s": 45},
        },
    )
    assert response.status_code == 200, response.content
    # Saving settings starts nothing.
    start.assert_not_called()
    result = response.json()["result"]
    assert result["metadata"] == {"purpose": "edited"}
    assert result["error_policy"] == "zero"
    assert result["limits"] == {"timeout_s": 45}
    experiment.refresh_from_db()
    assert experiment.run_policy == {
        "error_policy": "zero",
        "limits": {"timeout_s": 45},
    }
    # The server's snapshot identity stays beside the person's metadata.
    assert experiment.run_metadata == {
        "purpose": "edited",
        "native_snapshot": identity,
    }
    assert detail_of(auth_client, experiment)["error_policy"] == "zero"


def detail_of(auth_client, experiment):
    response = auth_client.get(f"{CREATE_URL}{experiment.id}/")
    assert response.status_code == 200, response.content
    return response.json()["result"]


def test_edit_returns_run_settings_to_the_default(auth_client, scope_world):
    record = created(
        auth_client,
        scope_world,
        metadata={"purpose": "first"},
        error_policy="exclude",
        limits={"timeout_s": 30},
    )
    experiment = record.experiment_dataset.experiment
    response, start = edit(
        auth_client,
        experiment,
        {"metadata": {}, "error_policy": None, "limits": {}},
    )
    assert response.status_code == 200, response.content
    start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_policy == {}
    assert set(experiment.run_metadata) == {"native_snapshot"}
    result = response.json()["result"]
    assert not {"error_policy", "limits"}.intersection(result)
    assert result.get("metadata", {}) == {}


def test_finished_run_keeps_its_policy_and_the_next_run_takes_the_edit(
    auth_client, scope_world
):
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    before = runs_of(experiment)
    assert len(before) == 1
    pinned = before[0].metadata["native_identity"]["policy_hash"]
    assert pinned == _digest({})
    response, start = edit(
        auth_client,
        experiment,
        {
            "prompt_config": [changed_prompt(scope_world, record)],
            "error_policy": "zero",
        },
    )
    assert response.status_code == 200, response.content
    assert start.call_count == 1
    after = runs_of(experiment)
    assert len(after) == 2
    assert after[0].pk == before[0].pk
    # The finished run still names the policy it ran with ...
    assert after[0].metadata["native_identity"]["policy_hash"] == pinned
    # ... and the run the edit started is admitted with the edited one.
    assert after[1].metadata["native_identity"]["policy_hash"] == _digest(
        {"error_policy": "zero"}
    )


def test_resending_an_earlier_edit_after_a_settings_change_applies_it(
    auth_client, scope_world
):
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    earlier = {
        "prompt_config": [changed_prompt(scope_world, record)],
        "error_policy": "exclude",
    }
    response, start = edit(auth_client, experiment, earlier)
    assert response.status_code == 200, response.content
    assert start.call_count == 1
    idle(experiment)
    response, _ = edit(auth_client, experiment, {"error_policy": "zero"})
    assert response.status_code == 200, response.content
    experiment.refresh_from_db()
    assert experiment.run_policy == {"error_policy": "zero"}
    # The same body again is a new request, not an answer from before.
    response, _ = edit(auth_client, experiment, earlier)
    assert response.status_code == 200, response.content
    experiment.refresh_from_db()
    assert experiment.run_policy == {"error_policy": "exclude"}


def test_run_settings_wait_for_the_current_run(auth_client, scope_world):
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    response, start = edit(auth_client, experiment, {"error_policy": "zero"})
    assert response.status_code == 400, response.content
    assert (
        "Wait for the current execution to finish before changing its run settings."
        in response.content.decode()
    )
    start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_policy == {}


def test_edit_refuses_the_limits_create_refuses(auth_client, scope_world):
    record = created(auth_client, scope_world, limits={"timeout_s": 30})
    experiment = record.experiment_dataset.experiment
    values = {"tokens": 500, "messages": 2}
    for key in UNAVAILABLE_ROW_LIMITS:
        response, start = edit(
            auth_client,
            experiment,
            {"limits": {key: values.get(key, 0.05), "timeout_s": 60}},
        )
        assert response.status_code == 400, (key, response.content)
        body = response.json()
        assert body["message"] == UNAVAILABLE_ROW_LIMITS_MESSAGE
        assert body["details"]["limits"] == [UNAVAILABLE_ROW_LIMITS_MESSAGE]
        start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_policy == {"limits": {"timeout_s": 30}}


def test_edit_keeps_the_rows_and_says_why(auth_client, scope_world):
    record = created(auth_client, scope_world, sample={"size": 2, "seed": 17})
    experiment = record.experiment_dataset.experiment
    rows = snapshot_orders(experiment)
    scope = deepcopy(experiment.run_scope)
    response, start = edit(auth_client, experiment, {"sample": {"size": 3, "seed": 17}})
    assert response.status_code == 400, response.content
    assert "Unknown field." not in response.content.decode()
    body = response.json()
    assert body["message"] == SAMPLE_FIXED
    assert body["details"]["sample"] == [SAMPLE_FIXED]
    start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_scope == scope
    assert snapshot_orders(experiment) == rows


def test_run_settings_are_saved_apart_from_adding_a_run(auth_client, scope_world):
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    entry = request_body(scope_world)["prompt_config"][0]
    response, start = edit(
        auth_client,
        experiment,
        {
            "append_only": True,
            "prompt_config": [{**entry, "name": "Additional variant"}],
            "error_policy": "zero",
        },
    )
    assert response.status_code == 400, response.content
    assert (
        "Save run settings separately from adding rows or runs."
        in response.content.decode()
    )
    start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_policy == {}
    assert len(runs_of(experiment)) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"metadata": {"purpose": "off"}},
        {"error_policy": "zero"},
        {"error_policy": None},
        {"limits": {"timeout_s": 30}},
        {"limits": {}},
        {"sample": {"size": 1, "seed": 17}},
        {"metadata": {"purpose": "mixed"}, "error_policy": "exclude"},
    ],
)
def test_off_refuses_edited_run_settings_before_any_write(
    auth_client, scope_world, settings, extra
):
    record = created(auth_client, scope_world, metadata={"purpose": "kept"})
    experiment = record.experiment_dataset.experiment
    before = (deepcopy(experiment.run_metadata), deepcopy(experiment.run_policy))
    settings.EXPERIMENT_SCOPE_ENABLED = False
    response, start = edit(auth_client, experiment, extra)
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["message"] == SCOPE_OFF_MESSAGE
    assert "Unknown field." not in response.content.decode()
    for field in extra:
        assert field in body["details"]
    start.assert_not_called()
    experiment.refresh_from_db()
    assert (experiment.run_metadata, experiment.run_policy) == before


def test_off_edit_with_empty_metadata_changes_nothing(
    auth_client, scope_world, settings
):
    record = created(auth_client, scope_world, metadata={"purpose": "kept"})
    experiment = record.experiment_dataset.experiment
    before = deepcopy(experiment.run_metadata)
    settings.EXPERIMENT_SCOPE_ENABLED = False
    response, start = edit(auth_client, experiment, {"metadata": {}})
    assert response.status_code == 200, response.content
    start.assert_not_called()
    experiment.refresh_from_db()
    assert experiment.run_metadata == before


@pytest.mark.parametrize("enabled", [False, True])
def test_update_schema_tracks_setting_and_keeps_append_rows(settings, enabled):
    settings.EXPERIMENT_SCOPE_ENABLED = enabled
    fields = set(ExperimentUpdateV2Serializer().fields)
    assert EDITABLE.intersection(fields) == (EDITABLE if enabled else set())
    # row_ids here adds rows to an endpoint experiment, in both states.
    assert "row_ids" in fields
    assert "sample" not in fields
    update = schema()["definitions"]["ExperimentUpdateV2"]["properties"]
    assert EDITABLE.intersection(update) == (EDITABLE if enabled else set())
    assert "row_ids" in update
    assert "sample" not in update


@pytest.mark.django_db
def test_endpoint_experiment_edit_saves_policy_and_refuses_a_time_limit(finished):
    actor, experiment, _source, _plan = finished
    response = http_put(actor, experiment, {"limits": {"timeout_s": 30}})
    assert response.status_code == 400, response.content
    assert "HTTP sources use timeout_seconds" in response.content.decode()
    experiment.refresh_from_db()
    assert experiment.run_policy == {}
    response = http_put(
        actor,
        experiment,
        {"error_policy": "zero", "metadata": {"purpose": "endpoint"}},
    )
    assert response.status_code == 200, response.content
    experiment.refresh_from_db()
    assert experiment.run_policy == {"error_policy": "zero"}
    assert experiment.run_metadata.get("purpose") == "endpoint"

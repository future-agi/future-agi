import copy
import csv
import io
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    CallExecutionSnapshot,
    CallTranscript,
    TestExecution as SimulationTestExecution,
)
from simulate.serializers.test_execution import CallExecutionDetailSerializer
from simulate.views import run_test as run_test_views


@pytest.fixture
def simulation_tree(db, organization, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name="Transcript Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for transcript read API tests.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator_agent = SimulatorAgent.objects.create(
        name="Transcript Simulator",
        prompt="Simulate a customer.",
        organization=organization,
        workspace=workspace,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4o-mini",
    )
    scenario = Scenarios.objects.create(
        name="Transcript Scenario",
        description="Scenario for transcript read API tests.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    run_test = RunTest.objects.create(
        name="Transcript Run",
        description="Run for transcript read API tests.",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    call_execution = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15551234567",
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={},
    )
    CallTranscript.objects.create(
        call_execution=call_execution,
        speaker_role=CallTranscript.SpeakerRole.USER,
        content="I need help scheduling an interview.",
        start_time_ms=0,
        end_time_ms=1000,
    )
    CallTranscript.objects.create(
        call_execution=call_execution,
        speaker_role=CallTranscript.SpeakerRole.ASSISTANT,
        content="I can help with that.",
        start_time_ms=1000,
        end_time_ms=2000,
    )
    return {
        "run_test": run_test,
        "test_execution": test_execution,
        "call_execution": call_execution,
    }


def _result(response):
    return response.data.get("result", response.data)


@pytest.mark.django_db
def test_call_transcript_views_traverse_run_test_organization(
    auth_client, simulation_tree
):
    call_execution = simulation_tree["call_execution"]
    test_execution = simulation_tree["test_execution"]

    response = auth_client.get(
        f"/simulate/call-executions/{call_execution.id}/transcripts/"
    )

    assert response.status_code == 200
    assert response.data["call_execution_id"] == str(call_execution.id)
    assert response.data["total_transcripts"] == 2
    assert [row["speaker_role"] for row in response.data["transcripts"]] == [
        "user",
        "assistant",
    ]

    response = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/transcripts/"
    )

    assert response.status_code == 200
    assert response.data["test_execution_id"] == test_execution.id
    assert response.data["total_calls"] == 1
    assert response.data["total_transcripts"] == 2
    assert response.data["calls"][0]["call_execution_id"] == str(call_execution.id)


@pytest.mark.django_db
def test_chat_sdk_code_uses_placeholders_without_leaking_org_keys(
    auth_client, organization, user, simulation_tree
):
    OrgApiKey.objects.create(
        organization=organization,
        user=user,
        type="user",
        api_key="liveapikey1234567890",
        secret_key="livesecret1234567890",
    )
    run_test = simulation_tree["run_test"]

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/sdk-code/")

    assert response.status_code == 200
    payload = _result(response)
    sdk_code = payload["sdk_code"]
    assert payload["run_test_id"] == str(run_test.id)
    assert str(run_test.id) in sdk_code
    assert run_test.name not in sdk_code
    assert "liveapikey1234567890" not in sdk_code
    assert "livesecret1234567890" not in sdk_code
    assert 'FI_API_KEY="<YOUR_FI_API_KEY>"' in sdk_code
    assert 'FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"' in sdk_code


@pytest.fixture
def eval_configs(db, simulation_tree, organization):
    template = EvalTemplate.objects.create(
        name="Read Surface Eval Template",
        config={},
        organization=organization,
    )
    run_test = simulation_tree["run_test"]
    live = SimulateEvalConfig.objects.create(
        name="Live Eval",
        eval_template=template,
        run_test=run_test,
    )
    deleted = SimulateEvalConfig.objects.create(
        name="Deleted Eval",
        eval_template=template,
        run_test=run_test,
    )
    deleted.delete()
    return {"live": live, "deleted": deleted}


def _eval_outputs_for(live, deleted):
    return {
        str(live.id): {
            "name": "Live Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
        str(deleted.id): {
            "name": "Deleted Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
    }


@pytest.mark.django_db
def test_get_eval_outputs_skips_missing_config(simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    )
    outputs = serializer.get_eval_outputs(call_execution)

    assert str(live.id) in outputs
    assert str(deleted.id) not in outputs


@pytest.mark.django_db
def test_get_eval_outputs_keeps_external_harness_results_without_config(
    simulation_tree, eval_configs
):
    live, _ = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    harness_id = str(uuid.uuid4())
    call_execution.eval_outputs = {
        harness_id: {
            "name": "database_state_verified",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
            "source": "harness",
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    outputs = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    ).get_eval_outputs(call_execution)

    assert outputs[harness_id]["name"] == "database_state_verified"
    assert outputs[harness_id]["value"] == "Passed"


@pytest.mark.django_db
def test_hosted_recording_artifacts_surface_in_detail_recording_shape(simulation_tree):
    call_execution = simulation_tree["call_execution"]
    agent_definition = call_execution.test_execution.run_test.agent_definition
    agent_definition.agent_type = AgentDefinition.AgentTypeChoices.VOICE
    agent_definition.save(update_fields=["agent_type"])
    call_execution.simulation_call_type = CallExecution.SimulationCallType.VOICE
    call_execution.call_metadata = {
        "hosted_harness_artifacts": {
            "recording_combined": {"url": "https://media.example/combined.wav"},
            "recording_stereo": {"url": "https://media.example/stereo.wav"},
            "recording_customer": {"url": "https://media.example/customer.wav"},
            "recording_assistant": {"url": "https://media.example/assistant.wav"},
        }
    }

    recordings = CallExecutionDetailSerializer(
        context={"detail_mode": True}
    ).get_recordings(call_execution)

    assert recordings == {
        "combined": "https://media.example/combined.wav",
        "stereo": "https://media.example/stereo.wav",
        "customer": "https://media.example/customer.wav",
        "assistant": "https://media.example/assistant.wav",
    }


@pytest.mark.django_db
def test_explicit_voice_call_type_wins_over_stale_text_agent_definition(simulation_tree):
    call_execution = simulation_tree["call_execution"]
    agent_definition = call_execution.test_execution.run_test.agent_definition
    agent_definition.agent_type = AgentDefinition.AgentTypeChoices.TEXT
    agent_definition.save(update_fields=["agent_type"])
    call_execution.simulation_call_type = CallExecution.SimulationCallType.VOICE
    call_execution.duration_seconds = 42
    call_execution.recording_url = "https://media.example/combined.wav"

    serializer = CallExecutionDetailSerializer(
        call_execution, context={"detail_mode": True}
    )

    assert serializer.data["duration"] == 42
    assert serializer.data["recordings"] == {
        "combined": "https://media.example/combined.wav"
    }


@pytest.mark.django_db
def test_get_eval_metrics_skips_missing_config(simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    )
    metrics = serializer.get_eval_metrics(call_execution)

    assert str(live.id) in metrics
    assert str(deleted.id) not in metrics


@pytest.mark.django_db
def test_eval_serializers_extract_choice_labels_from_scored_output(
    simulation_tree, eval_configs
):
    live = eval_configs["live"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        str(live.id): {
            "name": "Live Eval",
            "output": {"score": 0.5, "choices": ["Helpful", "Polite"]},
            "output_type": "choices",
            "status": "completed",
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    )

    assert serializer.get_eval_outputs(call_execution)[str(live.id)]["value"] == [
        "Helpful",
        "Polite",
    ]
    assert serializer.get_eval_metrics(call_execution)[str(live.id)]["value"] == [
        "Helpful",
        "Polite",
    ]


@pytest.mark.django_db
def test_get_eval_outputs_surfaces_all_when_context_absent(
    simulation_tree, eval_configs
):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer()
    outputs = serializer.get_eval_outputs(call_execution)

    assert str(live.id) in outputs
    assert str(deleted.id) in outputs


@pytest.mark.django_db
def test_call_details_marks_removed(auth_client, simulation_tree, eval_configs):
    """A removed eval's verdict is still returned by call details, marked
    ``removed: true`` in both projections; a live eval's verdict carries no
    such key; the stored value is never rewritten.

    Contract api_contracts/harness/eval-offer-backend-frontend.md v1.4 P14/P23;
    design §7; lld-5 state Removed; lld-1 Verdict.removed.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    # An independent copy: if the read path ever mutated the dict in place, it
    # would mutate this too, and the final `== stored` assertion would keep
    # passing regardless. `refresh_from_db()` below rebinds the attribute to a
    # freshly deserialised dict either way, but that guard is only real if
    # `stored` cannot be the same object as what got mutated.
    stored = copy.deepcopy(_eval_outputs_for(live, deleted))
    call_execution.eval_outputs = copy.deepcopy(stored)
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/call-executions/{call_execution.id}/")

    assert response.status_code == status.HTTP_200_OK
    outputs = response.data["eval_outputs"]
    metrics = response.data["eval_metrics"]

    # The removed eval's verdict is present and marked, in both places.
    assert outputs[str(deleted.id)]["removed"] is True
    assert metrics[str(deleted.id)]["removed"] is True
    # ... and still carries its real value, not a blanked one.
    assert outputs[str(deleted.id)]["value"] == "Passed"
    assert metrics[str(deleted.id)]["value"] == "Passed"

    # The live eval's verdict carries NO "removed" key (P23: not `false`).
    assert "removed" not in outputs[str(live.id)]
    assert "removed" not in metrics[str(live.id)]

    # Reading never rewrites what is stored.
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs == stored


@pytest.mark.django_db
def test_run_test_call_execution_list_marks_removed_evals(
    auth_client, simulation_tree, eval_configs
):
    """The run's call list (``GET /simulate/run-tests/{id}/call-executions/``,
    ``RunTestCallExecutionsView``) marks a removed eval's verdict the same
    way call details does -- mirrors ``test_call_details_marks_removed``.

    Contract v1.5 P23 now names this endpoint too, alongside call details, as
    the only read surfaces that show removed verdicts (whole-change review
    round 2, M1): the view used to build ``CallExecutionDetailSerializer``
    with no context at all, so ``get_eval_outputs``/``get_eval_metrics`` took
    the unfiltered branch and a removed eval's verdict came back
    indistinguishable from a live one here.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    stored = copy.deepcopy(_eval_outputs_for(live, deleted))
    call_execution.eval_outputs = copy.deepcopy(stored)
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    results = response.data["results"]
    row = next(r for r in results if str(r["id"]) == str(call_execution.id))
    outputs = row["eval_outputs"]
    metrics = row["eval_metrics"]

    # The removed eval's verdict is present and marked, in both places.
    assert outputs[str(deleted.id)]["removed"] is True
    assert metrics[str(deleted.id)]["removed"] is True
    # ... and still carries its real value, not a blanked one.
    assert outputs[str(deleted.id)]["value"] == "Passed"
    assert metrics[str(deleted.id)]["value"] == "Passed"

    # The live eval's verdict carries NO "removed" key (P23: not `false`).
    assert "removed" not in outputs[str(live.id)]
    assert "removed" not in metrics[str(live.id)]

    # Reading never rewrites what is stored.
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs == stored


@pytest.mark.django_db
def test_run_test_call_execution_list_name_less_row_falls_back_to_empty_string(
    auth_client, simulation_tree, eval_configs
):
    """L3 (whole-change review round 5): round 4's L1 fix added an ``""``
    ``name`` fallback under ``mark_removed_only`` in ``get_eval_metrics`` so
    a status-less row with no stored ``name`` does not pick up the live
    config's name on this surface (that would break the "byte-identical to
    the no-context branch except for the added `removed` keys" claim).
    Nothing planted a name-less row here before, so the fallback was
    revertible while green -- this pins it.

    Removal proof: revert the fallback from ``"" if mark_removed_only else
    (eval_config.name if eval_config else "")`` back to
    ``eval_config.name if eval_config else ""`` and this test goes red
    (``name`` would read ``"Deleted Eval"`` instead of ``""``).
    """
    deleted = eval_configs["deleted"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        str(deleted.id): {
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    row = next(
        r for r in response.data["results"] if str(r["id"]) == str(call_execution.id)
    )
    assert row["eval_metrics"][str(deleted.id)]["name"] == ""


@pytest.mark.django_db
def test_serializer_does_not_mutate_stored_eval_outputs(simulation_tree, eval_configs):
    """Calling the getters directly, on one instance, never leaves a trace on
    that instance's ``eval_outputs`` -- no ``removed`` key leaks into the
    model's dict, and the stored value is unaffected by whatever the
    serializer builds for the API. Unlike ``test_call_details_marks_removed``,
    this never goes through ``refresh_from_db()``, so it would actually catch
    an in-place mutation."""
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    expected = copy.deepcopy(_eval_outputs_for(live, deleted))
    call_execution.eval_outputs = copy.deepcopy(expected)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live, str(deleted.id): deleted}}
    )
    outputs = serializer.get_eval_outputs(call_execution)
    metrics = serializer.get_eval_metrics(call_execution)

    # The serializer output does carry `removed` for the deleted config...
    assert outputs[str(deleted.id)]["removed"] is True
    assert metrics[str(deleted.id)]["removed"] is True
    # ...but the model instance's own eval_outputs is untouched, same object.
    assert call_execution.eval_outputs == expected
    assert "removed" not in call_execution.eval_outputs[str(deleted.id)]
    assert "removed" not in call_execution.eval_outputs[str(live.id)]


@pytest.mark.django_db
def test_call_details_config_map_includes_removed_configs(
    simulation_tree, eval_configs
):
    """``build_eval_configs_map`` is the single hinge: it reads
    ``all_objects``, so ``iter_live_eval_outputs`` no longer drops the removed
    eval's row on this one surface."""
    from simulate.services.test_executor import build_eval_configs_map

    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    config_map = build_eval_configs_map(call_execution)

    assert set(config_map) == {str(live.id), str(deleted.id)}
    assert config_map[str(deleted.id)].deleted is True
    assert config_map[str(live.id)].deleted is False


@pytest.mark.django_db
def test_run_test_call_execution_list_builds_the_eval_config_map_once_per_request(
    auth_client, simulation_tree, eval_configs
):
    """Before this fix, ``build_eval_configs_map`` was called once per row
    (whole-change review round 3, M2) -- once per call-execution row and
    once per snapshot row -- so its query cost grew with the number of rows
    on a page: a default page (``limit=10``) was +10 queries, and an
    uncapped ``?limit=`` scaled further. It must not: the map is built
    exactly once per request now, from the union of every row's
    ``eval_outputs`` keys, and the same dict is reused for every row.

    Asserted by spying on the real ``build_eval_configs_map`` (wrapped, not
    replaced, so its behaviour -- and every other assertion on the response
    shape -- is unaffected) and counting calls, rather than the endpoint's
    total query count: this view's serializer has other, pre-existing
    per-row queries unrelated to eval configs (e.g. chat-metrics and
    scenario-graph lookups), so the total count already scales with the
    number of rows for reasons this ticket does not touch, and asserting
    total-count invariance would conflate that unrelated, pre-existing cost
    with the one this fix removes.

    Round 3's M2 fix note was "once per call-execution row **and once per
    snapshot row**", but this test used to plant zero
    ``CallExecutionSnapshot`` rows -- so a regressed per-snapshot
    ``build_eval_configs_map`` call inside the snapshot branch would still
    keep ``map_spy.assert_called_once()`` green (nothing on the snapshot
    branch was ever spied on). The one snapshot row below closes that gap
    (whole-change review round 4, L4): it would also have caught the
    discarded-map bug round 3 filed separately as M4.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    for i in range(2):
        CallExecution.objects.create(
            test_execution=call_execution.test_execution,
            scenario=call_execution.scenario,
            phone_number=f"+1555999000{i}",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={},
            eval_outputs=_eval_outputs_for(live, deleted),
        )
    CallExecutionSnapshot.objects.create(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        status=call_execution.status,
        eval_outputs=_eval_outputs_for(live, deleted),
    )

    with patch(
        "simulate.views.run_test.build_eval_configs_map",
        wraps=run_test_views.build_eval_configs_map,
    ) as map_spy:
        response = auth_client.get(
            f"/simulate/run-tests/{run_test.id}/call-executions/"
        )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data["results"]) == 4
    map_spy.assert_called_once()
    for row in response.data["results"]:
        assert row["eval_outputs"][str(deleted.id)]["removed"] is True


@pytest.mark.django_db
def test_run_test_call_execution_list_omits_error_localizer_payload(
    auth_client, simulation_tree, eval_configs, organization, workspace
):
    """The run's call list must not fire a per-row error-localizer lookup or
    embed its payload (``input_data`` can hold a full transcript) in a
    paginated response. The context this view now passes
    (``mark_removed_only``) keeps ``error_localizer`` false for every entry
    regardless of the config's own flag, so ``get_eval_metrics``'s EL merge
    block never runs here -- ``template_type`` stays ``None`` too, exactly
    the shape this surface had with no context at all before this ticket
    (whole-change review round 3, M3).
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    live.error_localizer = True
    live.save(update_fields=["error_localizer"])
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])
    ErrorLocalizerTask.objects.create(
        source=ErrorLocalizerSource.SIMULATE,
        status=ErrorLocalizerStatus.COMPLETED,
        input_data={"conversation": "a very long transcript ..."},
        input_keys=["conversation"],
        input_types={"conversation": "text"},
        error_analysis={"root_cause": "prompt too vague"},
        selected_input_key="conversation",
        organization=organization,
        workspace=workspace,
        metadata={
            "call_execution_id": str(call_execution.id),
            "eval_config_id": str(live.id),
        },
    )

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    row = next(
        r for r in response.data["results"] if str(r["id"]) == str(call_execution.id)
    )
    metric = row["eval_metrics"][str(live.id)]
    assert metric["error_localizer"] is False
    assert metric["template_type"] is None
    assert "error_analysis" not in metric
    assert "input_data" not in metric


@pytest.mark.django_db
def test_call_details_keeps_error_localizer_payload(
    auth_client, simulation_tree, eval_configs, organization, workspace
):
    """The mirror of ``test_run_test_call_execution_list_omits_error_localizer_payload``,
    on call details instead of the run's call list: ``mark_removed_only`` is
    a context key that ``CallExecutionDetailView`` never sets
    (``run_test.py:3730``, no ``mark_removed_only`` in its context dict), so
    ``template_type`` and the error-localizer merge (``error_localizer``,
    ``error_analysis``) must stay live there -- the drawer's error-analysis
    tab depends on them. Nothing before this test pinned that call details
    keeps this on; a copy-paste of ``mark_removed_only: True`` into this
    view's context would silently blank the drawer's error analysis with the
    whole suite still green (whole-change review round 4, L5).
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    live.error_localizer = True
    live.save(update_fields=["error_localizer"])
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])
    ErrorLocalizerTask.objects.create(
        source=ErrorLocalizerSource.SIMULATE,
        status=ErrorLocalizerStatus.COMPLETED,
        input_data={"conversation": "a very long transcript ..."},
        input_keys=["conversation"],
        input_types={"conversation": "text"},
        error_analysis={"root_cause": "prompt too vague"},
        selected_input_key="conversation",
        organization=organization,
        workspace=workspace,
        metadata={
            "call_execution_id": str(call_execution.id),
            "eval_config_id": str(live.id),
        },
    )

    response = auth_client.get(f"/simulate/call-executions/{call_execution.id}/")

    assert response.status_code == status.HTTP_200_OK
    metric = response.data["eval_metrics"][str(live.id)]
    assert metric["error_localizer"] is True
    assert metric["template_type"] == "single"
    assert metric["error_analysis"] == {"root_cause": "prompt too vague"}


@pytest.mark.django_db
def test_run_test_call_execution_list_keeps_key_with_no_config_row(
    auth_client, simulation_tree, eval_configs
):
    """A stored ``eval_outputs`` key with no matching config row at all (not
    even a removed one) -- theoretical today, since every writer keys by a
    real config id or a harness ``uuid5`` -- must not be silently dropped
    from this surface by ``iter_live_eval_outputs``'s filtering (whole-change
    review round 3, M3's fourth flip): the context this view now passes
    (``mark_removed_only``) iterates ``eval_outputs`` directly, same as the
    no-context branch, so nothing is dropped for lacking a config row.
    """
    live = eval_configs["live"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    orphan_id = str(uuid.uuid4())
    call_execution.eval_outputs = {
        str(live.id): {
            "name": "Live Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
        orphan_id: {
            "name": "Orphan Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
    }
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    row = next(
        r for r in response.data["results"] if str(r["id"]) == str(call_execution.id)
    )
    assert orphan_id in row["eval_outputs"]
    assert orphan_id in row["eval_metrics"]


@pytest.mark.django_db
def test_run_test_call_execution_list_survives_a_non_uuid_eval_outputs_key(
    auth_client, simulation_tree, eval_configs
):
    """A raw, non-UUID key in one row's stored ``eval_outputs`` (a corrupted
    or hand-edited row -- ``orphan_id`` above is still a well-formed UUID,
    just one with no matching config) must not 500 the whole page.

    ``build_eval_configs_map`` now builds exactly one map for every row on
    the page from the union of their ``eval_outputs`` keys (whole-change
    review round 3, M2); before that, only the one call's own detail
    request broke on a bad key. Passing a raw non-UUID string straight into
    ``SimulateEvalConfig.all_objects.filter(id__in=...)`` raises
    ``ValidationError``, which this view's blanket ``except Exception``
    turns into a 500 for every row sharing the single page-level map, not
    just the row that holds the bad key (whole-change review round 4, L8).
    The fix filters non-UUID keys out of the map query; the bad key's own
    entry simply has no matching config (same as any other id the query
    finds nothing for), while every other row's ``removed`` marking is
    unaffected.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        "not-a-uuid": {
            "name": "Corrupted Row",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
        str(live.id): {
            "name": "Live Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
    }
    call_execution.save(update_fields=["eval_outputs"])
    other_call = CallExecution.objects.create(
        test_execution=call_execution.test_execution,
        scenario=call_execution.scenario,
        phone_number="+15559990099",
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={},
        eval_outputs=_eval_outputs_for(live, deleted),
    )

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    results = response.data["results"]
    corrupted_row = next(r for r in results if str(r["id"]) == str(call_execution.id))
    assert "not-a-uuid" in corrupted_row["eval_outputs"]
    assert "removed" not in corrupted_row["eval_outputs"]["not-a-uuid"]

    other_row = next(r for r in results if str(r["id"]) == str(other_call.id))
    assert other_row["eval_outputs"][str(deleted.id)]["removed"] is True
    assert "removed" not in other_row["eval_outputs"][str(live.id)]


@pytest.mark.django_db
def test_call_details_survives_a_non_uuid_eval_outputs_key(
    auth_client, simulation_tree, eval_configs
):
    """L4 (whole-change review round 5): unlike the run's call list (see
    ``test_run_test_call_execution_list_survives_a_non_uuid_eval_outputs_key``
    above, which iterates ``eval_outputs`` directly under
    ``mark_removed_only`` and keeps the bad key), call details feeds the
    filtered map straight to ``iter_live_eval_outputs``. A non-UUID key is
    never in that map and has no ``"source": "harness"`` marker, so
    ``iter_live_eval_outputs`` drops the row -- round 4's L8 fix turned a
    500 on this surface into a silent 200 that omits the row, and nothing
    pinned that until now. This test only pins the behaviour (200, the
    other row still rendered, the bad key omitted from both projections);
    it does not assert that omission is the right call -- the PR body
    names it as a known, deliberate trade-off (never 500 the page).
    """
    live = eval_configs["live"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        "not-a-uuid": {
            "name": "Corrupted Row",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
        str(live.id): {
            "name": "Live Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
    }
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/call-executions/{call_execution.id}/")

    assert response.status_code == status.HTTP_200_OK
    assert "not-a-uuid" not in response.data["eval_outputs"]
    assert "not-a-uuid" not in response.data["eval_metrics"]
    assert response.data["eval_outputs"][str(live.id)]["value"] == "Passed"
    assert response.data["eval_metrics"][str(live.id)]["value"] == "Passed"


@pytest.mark.django_db
def test_run_test_call_execution_list_snapshot_row_marks_removed_evals(
    auth_client, simulation_tree, eval_configs
):
    """A snapshot row's own ``eval_outputs`` (returned raw, not through the
    serializer -- a pre-existing shape mismatch with a live call execution's
    row for the same field name, contract P23) gets the removed marker too,
    from the same page-level config map (whole-change review round 3, M4):
    the map built for the snapshot branch used to be thrown away entirely
    (its output was never used), so a snapshot row's removed verdict came
    back unmarked here even after round 2's M1 fix.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    run_test = simulation_tree["run_test"]
    call_execution = simulation_tree["call_execution"]
    snapshot = CallExecutionSnapshot.objects.create(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        status=call_execution.status,
        eval_outputs=_eval_outputs_for(live, deleted),
    )

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/call-executions/")

    assert response.status_code == status.HTTP_200_OK
    row = next(r for r in response.data["results"] if str(r["id"]) == str(snapshot.id))
    assert row["is_snapshot"] is True
    outputs = row["eval_outputs"]

    assert outputs[str(deleted.id)]["removed"] is True
    assert "removed" not in outputs[str(live.id)]
    # Raw stored shape, not the serializer's structured one (pre-existing).
    assert outputs[str(deleted.id)]["output"] == "Passed"

    # Reading never rewrites what is stored on the snapshot. NOTE: this
    # specific check is not itself a mutation guard -- nothing in the
    # request above ever calls ``snapshot.save()``, so
    # ``refresh_from_db()`` re-reads the same row ``_mark_removed_evals``
    # was handed in the first place, and it would pass identically even if
    # ``_mark_removed_evals`` mutated its input dict in place (whole-change
    # review round 4, L6). The real non-mutation guard is
    # ``test_mark_removed_evals_does_not_mutate_input`` below, which calls
    # ``_mark_removed_evals`` directly and diffs its input against a
    # deep copy taken before the call.
    snapshot.refresh_from_db()
    assert "removed" not in snapshot.eval_outputs[str(deleted.id)]


@pytest.mark.django_db
def test_mark_removed_evals_does_not_mutate_input(simulation_tree, eval_configs):
    """``RunTestCallExecutionsView._mark_removed_evals`` returns a stamped
    copy and never touches the dict it was handed -- called directly here,
    with the input diffed against a ``copy.deepcopy`` taken before the call,
    so an in-place mutation would actually be caught (unlike
    ``test_run_test_call_execution_list_snapshot_row_marks_removed_evals``'s
    ``refresh_from_db()`` check, which never saves the snapshot and so
    cannot observe a mutation -- whole-change review round 4, L6). Mirrors
    ``test_serializer_does_not_mutate_stored_eval_outputs``'s approach for
    ``CallExecutionDetailSerializer.get_eval_outputs``, applied to this
    view's own ``eval_outputs`` marker for snapshot rows.

    Round 4's version of this test only ever planted flat rows
    (``_eval_outputs_for``'s shape), so its "every row dict nested inside it
    is byte-for-byte unchanged" claim was never actually exercised: a
    shallow ``dict(eval_data)`` copy leaves any nested value shared with the
    input, and a flat row has no nested value for that sharing to matter
    (whole-change review round 5, L6). This version plants a nested
    ``details`` dict on the live row and mutates it through the *returned*
    ``marked`` copy -- proof by removal: revert the helper's
    ``copy.deepcopy(eval_data)`` back to ``dict(eval_data)`` and the last
    assertion below goes red, because the mutation below would then reach
    back into ``eval_outputs`` through the shared nested dict.
    """
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    eval_outputs = _eval_outputs_for(live, deleted)
    eval_outputs[str(live.id)]["details"] = {"choices": ["a", "b"]}
    before = copy.deepcopy(eval_outputs)
    eval_configs_map = {str(live.id): live, str(deleted.id): deleted}

    marked = run_test_views.RunTestCallExecutionsView._mark_removed_evals(
        eval_outputs, eval_configs_map
    )

    # The stamp landed on the returned copy...
    assert marked[str(deleted.id)]["removed"] is True
    assert "removed" not in marked[str(live.id)]
    # ...but the caller's own dict, and every row dict nested inside it, is
    # byte-for-byte unchanged.
    assert eval_outputs == before
    assert "removed" not in eval_outputs[str(deleted.id)]
    assert "removed" not in eval_outputs[str(live.id)]

    # Mutate the returned copy's nested value: if the helper only shallow-
    # copied each row, this reaches back into the caller's own dict through
    # the shared nested object.
    marked[str(live.id)]["details"]["choices"].append("c")
    assert eval_outputs[str(live.id)]["details"]["choices"] == ["a", "b"]


@pytest.mark.django_db
def test_column_order_drops_deleted_eval_columns(
    auth_client, simulation_tree, eval_configs
):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    test_execution = simulation_tree["test_execution"]
    test_execution.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "type": "scenario_dataset_column",
                "id": "scenario_col",
                "column_name": "Scenario",
            },
            {"type": "evaluation", "id": str(live.id), "column_name": "Live Eval"},
            {
                "type": "evaluation",
                "id": str(deleted.id),
                "column_name": "Deleted Eval",
            },
        ],
    }
    test_execution.save(update_fields=["execution_metadata"])

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(col.get("id"))
        for col in response.data["column_order"]
        if col.get("type") == "evaluation"
    }
    assert str(live.id) in eval_col_ids
    assert str(deleted.id) not in eval_col_ids


@pytest.fixture
def three_eval_configs(db, simulation_tree, organization):
    """Two evals present at TE creation, one added later on the run_test."""
    template = EvalTemplate.objects.create(
        name="Reconcile Late-Add Template", config={}, organization=organization
    )
    run_test = simulation_tree["run_test"]
    test_execution = simulation_tree["test_execution"]
    orig_a = SimulateEvalConfig.objects.create(
        name="Task", eval_template=template, run_test=run_test
    )
    orig_b = SimulateEvalConfig.objects.create(
        name="Prompt", eval_template=template, run_test=run_test
    )
    test_execution.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "type": "scenario_dataset_column",
                "id": "scenario_col",
                "column_name": "Scenario",
            },
            {"type": "evaluation", "id": str(orig_a.id), "column_name": "Task"},
            {"type": "evaluation", "id": str(orig_b.id), "column_name": "Prompt"},
        ],
    }
    test_execution.save(update_fields=["execution_metadata"])
    late_add = SimulateEvalConfig.objects.create(
        name="Toxicity", eval_template=template, run_test=run_test
    )
    return {"orig_a": orig_a, "orig_b": orig_b, "late_add": late_add}


@pytest.mark.django_db
def test_late_added_eval_column_appears_only_after_it_has_been_evaluated(
    auth_client, simulation_tree, three_eval_configs
):
    """Adding a 3rd eval on the run_test alone must not add a phantom
    column on TE#1; only after it has run against TE#1's calls does the
    column land."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    orig_a = three_eval_configs["orig_a"]
    orig_b = three_eval_configs["orig_b"]
    late_add = three_eval_configs["late_add"]

    response_before = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/"
    )
    assert response_before.status_code == 200
    eval_cols_before = [
        c
        for c in response_before.data["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in eval_cols_before] == [
        str(orig_a.id),
        str(orig_b.id),
    ]

    call_execution.eval_outputs = {
        str(orig_a.id): {"status": "completed", "output": "Passed"},
        str(orig_b.id): {"status": "completed", "output": "Passed"},
        str(late_add.id): {"status": "completed", "output": "Passed"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    response_after = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/"
    )
    assert response_after.status_code == 200
    eval_cols_after = [
        c
        for c in response_after.data["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in eval_cols_after] == [
        str(orig_a.id),
        str(orig_b.id),
        str(late_add.id),
    ]

    test_execution.refresh_from_db()
    persisted = [
        c
        for c in test_execution.execution_metadata["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in persisted] == [
        str(orig_a.id),
        str(orig_b.id),
        str(late_add.id),
    ]


@pytest.mark.django_db
def test_errored_eval_output_still_surfaces_the_column(
    auth_client, simulation_tree, three_eval_configs
):
    """An eval that ran but errored is still 'attempted' - the column
    must surface so the user can see the failure."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    late_add = three_eval_configs["late_add"]
    call_execution.eval_outputs = {
        str(late_add.id): {"status": "error", "error": "boom"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(c["id"])
        for c in response.data["column_order"]
        if c.get("type") == "evaluation"
    }
    assert str(late_add.id) in eval_col_ids


@pytest.mark.django_db
def test_corrupted_eval_outputs_does_not_crash_the_view(
    auth_client, simulation_tree, three_eval_configs
):
    """Legacy / bad-actor rows with a non-dict `eval_outputs` (string,
    list, etc.) must not 500 the detail GET. The reconciler skips them."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    # Bypass model-level validation to persist a legacy-shape payload.
    CallExecution.objects.filter(id=call_execution.id).update(
        eval_outputs="not-a-dict-corrupted-legacy"
    )

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(c["id"])
        for c in response.data["column_order"]
        if c.get("type") == "evaluation"
    }
    # Only the two originals that were already in column_order survive;
    # the late-add is not appended because the corrupted row contributes
    # nothing to evaluated_eval_ids.
    orig_a = three_eval_configs["orig_a"]
    orig_b = three_eval_configs["orig_b"]
    assert eval_col_ids == {str(orig_a.id), str(orig_b.id)}


@pytest.mark.django_db
def test_second_get_is_a_noop_no_repeated_writes(
    auth_client, simulation_tree, three_eval_configs
):
    """Once column_order matches the live state, subsequent GETs must
    not re-save. Guards against a subtle write-loop regression."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    late_add = three_eval_configs["late_add"]
    call_execution.eval_outputs = {
        str(late_add.id): {"status": "completed", "output": "Passed"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    # First GET reconciles; second GET must land the same column_order
    # with no additional persistence.
    auth_client.get(f"/simulate/test-executions/{test_execution.id}/")
    test_execution.refresh_from_db()
    updated_at_after_first = test_execution.updated_at

    auth_client.get(f"/simulate/test-executions/{test_execution.id}/")
    test_execution.refresh_from_db()

    assert test_execution.updated_at == updated_at_after_first


@pytest.mark.django_db
def test_csv_export_excludes_deleted_evals(auth_client, simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        str(live.id): {
            "name": "Live Eval Column",
            "output": "Passed",
            "output_type": "Pass/Fail",
        },
        str(deleted.id): {
            "name": "Deleted Eval Column",
            "output": "Passed",
            "output_type": "Pass/Fail",
        },
    }
    call_execution.save(update_fields=["eval_outputs"])
    test_execution = simulation_tree["test_execution"]

    response = auth_client.get(
        f"/simulate/export/{test_execution.id}/?type=testexecution"
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Live Eval Column" in body
    assert "Deleted Eval Column" not in body


# ============================================================================
# Phase 4 polish: GET /simulate/export/<uuid:item_id>/
# ============================================================================


def _parse_csv_response(response):
    """Parse a CSV HttpResponse body into (header_row, list_of_dicts)."""
    body = response.content.decode()
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    assert rows, "CSV response is empty"
    header = rows[0]
    data_rows = [dict(zip(header, r)) for r in rows[1:]]
    return header, data_rows


def _seed_export_stack(organization, workspace, run_test_name="Export Run"):
    """Seed a RunTest + Scenarios + TestExecution + CallExecutions suitable
    for exercising the CSV export view."""
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Export Agent {uuid.uuid4().hex[:6]}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for export tests.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator_agent = SimulatorAgent.objects.create(
        name=f"Sim {uuid.uuid4().hex[:6]}",
        prompt="Simulate.",
        organization=organization,
        workspace=workspace,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4o-mini",
    )
    scenario = Scenarios.objects.create(
        name=f"Scenario {uuid.uuid4().hex[:6]}",
        description="Export scenario.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    run_test = RunTest.objects.create(
        name=run_test_name,
        description="Export run.",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    return {
        "agent_definition": agent_definition,
        "simulator_agent": simulator_agent,
        "scenario": scenario,
        "run_test": run_test,
        "test_execution": test_execution,
    }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCSVExportRowFormat:
    URL = "/simulate/export/{item_id}/"

    def test_csv_export_row_format_matches_seeded_data(
        self, auth_client, simulation_tree, eval_configs
    ):
        live = eval_configs["live"]
        run_test = simulation_tree["run_test"]
        # A scenario name with a comma and a double-quote must round-trip
        # through csv.reader, which validates that the writer is quoting
        # the field.
        tricky_scenario = Scenarios.objects.create(
            name='Comma, and "quote" scenario',
            description="Scenario stressing CSV escaping.",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=run_test.organization,
            workspace=run_test.workspace,
            agent_definition=simulation_tree["run_test"].agent_definition,
            simulator_agent=simulation_tree["run_test"].simulator_agent,
        )
        run_test.scenarios.add(tricky_scenario)
        tricky_call = CallExecution.objects.create(
            test_execution=simulation_tree["test_execution"],
            scenario=tricky_scenario,
            phone_number="+15550000000",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={},
            duration_seconds=42,
            overall_score=0.87,
            response_time_ms=1500,
            eval_outputs={
                str(live.id): {
                    "name": "Live Eval",
                    "output": "Passed",
                    "output_type": "Pass/Fail",
                    "reason": "clean, tidy",
                },
            },
        )
        # Also populate an eval output on the original call execution so the
        # header advertises the eval column.
        original_call = simulation_tree["call_execution"]
        original_call.eval_outputs = {
            str(live.id): {
                "name": "Live Eval",
                "output": "Failed",
                "output_type": "Pass/Fail",
                "reason": "boom",
            },
        }
        original_call.overall_score = 0.5
        original_call.duration_seconds = 10
        original_call.save(
            update_fields=["eval_outputs", "overall_score", "duration_seconds"]
        )

        response = auth_client.get(
            self.URL.format(item_id=run_test.id) + "?type=runtest"
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        header, data_rows = _parse_csv_response(response)

        expected_base_columns = [
            "ID",
            "Timestamp",
            "Call Type",
            "Status",
            "Duration",
            "Scenario",
            "Overall Score",
            "Response Time",
            "Audio URL",
            "Provider call ID",
        ]
        for col in expected_base_columns:
            assert col in header, f"Missing base column {col!r} in {header!r}"
        # Dynamic eval column pair.
        assert "Live Eval" in header
        assert "Live Eval_reason" in header

        # Every seeded call must appear exactly once.
        ids_in_csv = {row["ID"] for row in data_rows}
        assert str(tricky_call.id) in ids_in_csv
        assert str(original_call.id) in ids_in_csv

        tricky_row = next(row for row in data_rows if row["ID"] == str(tricky_call.id))
        # Escaping check: the raw scenario contained a comma and quotes;
        # csv.reader gives us back the un-escaped value.
        assert tricky_row["Scenario"] == 'Comma, and "quote" scenario'
        # Numeric fields round-trip as their string form.
        assert tricky_row["Duration"] == "42"
        assert tricky_row["Overall Score"] == "0.87"
        # response_time_ms 1500 -> 1.5s.
        assert tricky_row["Response Time"] == "1.5"
        assert tricky_row["Live Eval"] == "Passed"
        assert tricky_row["Live Eval_reason"] == "clean, tidy"

    def test_csv_export_masks_sensitive_fields(
        self, auth_client, simulation_tree
    ):
        call_execution = simulation_tree["call_execution"]
        call_execution.call_metadata = {
            "api_key": "sk-should-not-leak-1234",
            "credentials": {"password": "pw-should-not-leak"},
            "bearer_token": "bearer-should-not-leak",
        }
        call_execution.save(update_fields=["call_metadata"])
        run_test = simulation_tree["run_test"]

        response = auth_client.get(
            self.URL.format(item_id=run_test.id) + "?type=runtest"
        )

        assert response.status_code == 200
        header, _rows = _parse_csv_response(response)
        for banned_col in ("api_key", "credentials", "password", "bearer_token"):
            assert banned_col not in header, (
                f"Sensitive column {banned_col!r} leaked into export header"
            )
        body = response.content.decode()
        for banned_value in (
            "sk-should-not-leak-1234",
            "pw-should-not-leak",
            "bearer-should-not-leak",
        ):
            assert banned_value not in body, (
                f"Sensitive value {banned_value!r} leaked into export body"
            )

    def test_csv_export_other_workspace_returns_404(
        self, auth_client, user
    ):
        other_org = Organization.objects.create(name="Other Org For Export")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Org Default Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other = _seed_export_stack(other_org, other_workspace)
        other_run_test = other["run_test"]

        response = auth_client.get(
            self.URL.format(item_id=other_run_test.id) + "?type=runtest"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        # And nothing was written back to the foreign row.
        other_run_test.refresh_from_db()
        assert other_run_test.deleted is False

    def test_csv_export_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.get(
            self.URL.format(item_id=uuid.uuid4()) + "?type=runtest"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_csv_export_unauthenticated_is_rejected(self, api_client):
        response = api_client.get(
            self.URL.format(item_id=uuid.uuid4()) + "?type=runtest"
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ============================================================================
# Phase 4 polish: PATCH /simulate/run-tests/<uuid:run_test_id>/components/
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRunTestComponentsPatch:
    URL = "/simulate/run-tests/{run_test_id}/components/"

    def test_components_patch_persists_updates(
        self, auth_client, simulation_tree, organization, workspace
    ):
        run_test = simulation_tree["run_test"]
        new_simulator_agent = SimulatorAgent.objects.create(
            name="Replacement Simulator",
            prompt="Simulate a new customer.",
            organization=organization,
            workspace=workspace,
            voice_provider="openai",
            voice_name="alloy",
            model="gpt-4o-mini",
        )
        new_scenario = Scenarios.objects.create(
            name="Replacement Scenario",
            description="Scenario added via components PATCH.",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            agent_definition=run_test.agent_definition,
            simulator_agent=new_simulator_agent,
        )

        payload = {
            "simulator_agent_id": str(new_simulator_agent.id),
            "scenarios": [str(new_scenario.id)],
        }
        response = auth_client.patch(
            self.URL.format(run_test_id=run_test.id),
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        run_test.refresh_from_db()
        assert run_test.simulator_agent_id == new_simulator_agent.id
        scenario_ids_on_run = list(
            run_test.scenarios.values_list("id", flat=True)
        )
        # The view uses .set() which replaces the m2m contents.
        assert scenario_ids_on_run == [new_scenario.id]

    def test_components_patch_other_workspace_returns_404(
        self, auth_client, user
    ):
        """PATCH against a sibling-organization RunTest must 404 and must not mutate the foreign row."""
        other_org = Organization.objects.create(name="Other Org For Components")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Org Default Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other = _seed_export_stack(other_org, other_workspace)
        other_run_test = other["run_test"]
        original_simulator_id = other_run_test.simulator_agent_id
        # Count the m2m through table directly: the default `scenarios`
        # manager is workspace-filtered, and auth_client has set the caller's
        # workspace on the thread-local, which hides foreign rows and would
        # give false confidence.
        RunTestScenariosThrough = RunTest.scenarios.through
        original_through_count = RunTestScenariosThrough.objects.filter(
            runtest_id=other_run_test.id
        ).count()
        assert original_through_count > 0, (
            "seed produced no scenarios; assertion below would be vacuous"
        )

        # Payload uses a real UUID for simulator_agent_id so validation
        # passes; the request must still 404 on the outer object lookup.
        response = auth_client.patch(
            self.URL.format(run_test_id=other_run_test.id),
            {"simulator_agent_id": str(uuid.uuid4())},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_run_test.refresh_from_db()
        assert other_run_test.simulator_agent_id == original_simulator_id
        assert (
            RunTestScenariosThrough.objects.filter(
                runtest_id=other_run_test.id
            ).count()
            == original_through_count
        )

    def test_components_patch_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.patch(
            self.URL.format(run_test_id=uuid.uuid4()),
            {"scenarios": []},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_components_patch_unauthenticated_is_rejected(self, api_client):
        response = api_client.patch(
            self.URL.format(run_test_id=uuid.uuid4()),
            {"scenarios": []},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ============================================================================
# GET /simulate/run-tests/<uuid:run_test_id>/sdk-code/
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSDKCodeContent:
    URL = "/simulate/run-tests/{run_test_id}/sdk-code/"

    def test_sdk_code_returns_expected_content_shape(
        self, auth_client, simulation_tree
    ):
        run_test = simulation_tree["run_test"]

        response = auth_client.get(self.URL.format(run_test_id=run_test.id))

        assert response.status_code == status.HTTP_200_OK
        payload = _result(response)
        assert "sdk_code" in payload
        assert isinstance(payload["sdk_code"], str)
        assert payload["sdk_code"], "sdk_code snippet is empty"
        # A downstream user must be able to distinguish snippets between runs:
        # either the run name or its id should appear in the response.
        assert payload.get("run_test_id") == str(run_test.id)
        assert payload.get("run_test_name") == run_test.name
        assert str(run_test.id) in payload["sdk_code"]

    def test_sdk_code_other_workspace_returns_404(self, auth_client, user):
        other_org = Organization.objects.create(name="Other Org For SDK Code")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Org Default Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other = _seed_export_stack(
            other_org, other_workspace, run_test_name="Hidden Cross Tenant Run"
        )
        other_run_test = other["run_test"]

        response = auth_client.get(
            self.URL.format(run_test_id=other_run_test.id)
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.content.decode()
        assert str(other_run_test.id) not in body
        assert other_run_test.name not in body

    def test_sdk_code_not_found_returns_404(self, auth_client):
        response = auth_client.get(self.URL.format(run_test_id=uuid.uuid4()))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_sdk_code_unauthenticated_returns_401(self, api_client):
        response = api_client.get(self.URL.format(run_test_id=uuid.uuid4()))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

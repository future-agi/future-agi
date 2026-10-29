"""Sub-actors on the platform: the environment's list, each scenario's, and who spoke."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import QueryDict
from rest_framework.test import APIClient

from simulate.models import CallExecution, HostedHarnessScenario
from simulate.services.harness_scenarios import (
    apply_filters,
    field_catalogue,
    index_scenarios,
    scenario_row,
    sub_actor_choice_labels,
)
from simulate.services.hosted_harness import create_hosted_job, register_attempt
from simulate.services.hosted_harness_ingestion import _ingest_hosted_transcript
from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload

_FRIEND = {
    "id": "friend",
    "instruction": "You are with the caller and want a second item.",
    "trigger": {"kind": "mid_journey", "turn": 2},
}
_LIST = {
    "sub_actors": [
        {
            "id": "friend",
            "name": "Rowan",
            "role": "a friend with the caller",
            "channel": "audio_mix",
            "persona": {"gender": "female"},
        },
        {
            "id": "partner",
            "name": "Sam",
            "role": "the caller's partner",
            "channel": "audio_handoff",
            "persona": {"gender": "male"},
        },
    ]
}


def _docs():
    return [
        {"scenario_key": "alone", "name": "alone", "instruction": "Book it."},
        {
            "scenario_key": "with-friend",
            "name": "with-friend",
            "instruction": "Book it.",
            "sub_actors": [_FRIEND, "not-an-entry"],
        },
    ]


def _keys(queryset):
    return sorted(queryset.values_list("scenario_key", flat=True))


@pytest.mark.django_db
def test_a_suite_indexes_each_scenarios_sub_actors_and_filters_by_them(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=2),
        idempotency_key="sub-actor-index",
        workspace=workspace,
    )
    index_scenarios(job, _docs())
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=job)

    assert scenario_row(rows.get(scenario_key="with-friend"))["sub_actors"] == [_FRIEND]
    assert rows.get(scenario_key="alone").sub_actors is None
    assert scenario_row(rows.get(scenario_key="alone"))["sub_actors"] == []

    def filtered(query: str):
        return _keys(apply_filters(rows, QueryDict(query)))

    assert filtered("sub_actors=friend") == ["with-friend"]
    assert filtered("sub_actors=none") == ["alone"]
    assert filtered("sub_actors_not=friend") == ["alone"]
    field = next(one for one in field_catalogue(rows) if one["value"] == "sub_actors")
    assert field["counts"] == {"friend": 1, "none": 1}
    # The panel names each choice: the sub-actor by name, and none as the simulated user alone.
    named = next(
        one
        for one in field_catalogue(
            rows, sub_actor_labels=sub_actor_choice_labels(_LIST)
        )
        if one["value"] == "sub_actors"
    )
    assert named["choice_labels"] == {
        "none": "Simulated user only",
        "friend": "Rowan",
        "partner": "Sam",
    }
    assert field["choice_labels"] == {"none": "Simulated user only"}


@pytest.mark.django_db
def test_a_suite_with_no_sub_actors_offers_no_sub_actor_filter(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=1),
        idempotency_key="no-sub-actor-index",
        workspace=workspace,
    )
    index_scenarios(job, _docs()[:1])
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=job)
    assert not any(one["value"] == "sub_actors" for one in field_catalogue(rows))


def _ingest(organization, workspace, key, call_type, transcript, metadata=None):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=key, workspace=workspace
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": key,
            "modality": "text",
            "personas": [
                {
                    "scenario_key": key,
                    "name": "Customer",
                    "situation": "Book it",
                    "outcome": "Booked",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provision.json()["result"]["run_test_id"],
            "scenario_keys": [key],
        },
        format="json",
        **_headers(capability),
    )
    call = CallExecution.objects.get(hosted_registration__scenario_key=key)
    call.simulation_call_type = call_type
    call.started_at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    call.save(update_fields=["simulation_call_type", "started_at"])
    response = MagicMock()
    response.read.return_value = json.dumps(transcript).encode()
    storage = MagicMock()
    storage.get_object.return_value = response
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        _ingest_hosted_transcript(
            call, SimpleNamespace(object_key="transcript.json"), metadata
        )
    return call


@pytest.mark.django_db
def test_a_sub_actor_turn_is_stored_with_its_name_and_the_call_keeps_who_took_part(
    organization, workspace
):
    at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC).timestamp()
    record = {
        "resolved": [{"id": "friend", "name": "Rowan"}],
        "summary": [{"sub_actor": "friend", "triggered": True, "delivered": 1}],
        "events": [{"sub_actor": "friend", "event": "delivered"}],
    }
    metadata = {"harness_sub_actors": {"stale": True}}
    call = _ingest(
        organization,
        workspace,
        "voice-one",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "Where to?",
                    "started_speaking_at": at,
                    "stopped_speaking_at": at + 1,
                },
                {
                    "role": "sub_actor",
                    "name": "Rowan",
                    "content": "Add a stop.",
                    "started_speaking_at": at + 2,
                    "stopped_speaking_at": at + 3,
                },
                {
                    "role": "user",
                    "content": "Just the airport.",
                    "started_speaking_at": at + 4,
                    "stopped_speaking_at": at + 5,
                },
            ],
            "sub_actors": record,
        },
        metadata,
    )

    rows = list(call.transcripts.order_by("start_time_ms"))
    assert [(row.speaker_role, row.speaker_name) for row in rows] == [
        ("assistant", ""),
        ("sub_actor", "Rowan"),
        ("user", ""),
    ]
    assert metadata["harness_sub_actors"] == record
    from simulate.tasks.alk_sim import _build_transcript_text
    from simulate.temporal.activities.xl import _build_transcript_data

    assert _build_transcript_text(call).splitlines() == [
        "Agent: Where to?",
        "Rowan (not the customer): Add a stop.",
        "Customer: Just the airport.",
    ]
    assert "Rowan (not the customer): Add a stop." in (
        _build_transcript_data(call)["transcript"]
    )


@pytest.mark.django_db
def test_a_rerun_without_sub_actors_clears_the_record(organization, workspace):
    metadata = {"harness_sub_actors": {"resolved": [{"id": "friend"}]}}
    _ingest(
        organization,
        workspace,
        "voice-rerun",
        CallExecution.SimulationCallType.VOICE,
        {"messages": [{"role": "user", "content": "Hello."}]},
        metadata,
    )
    assert "harness_sub_actors" not in metadata


def test_the_environment_s_list_is_a_stage_output_and_an_emptied_list_replaces_it():
    from simulate.services.hosted_harness_gateway import authoring_stage_outputs

    [listed] = [
        one
        for one in authoring_stage_outputs(None, None, None, sub_actors=_LIST)
        if one["kind"] == "sub_actors"
    ]
    assert (listed["summary"], listed["data"]) == ("2 sub-actors", _LIST)
    [emptied] = [
        one
        for one in authoring_stage_outputs(
            None, None, None, sub_actors={"sub_actors": []}
        )
        if one["kind"] == "sub_actors"
    ]
    assert emptied["summary"] == "0 sub-actors"
    assert not any(
        one["kind"] == "sub_actors"
        for one in authoring_stage_outputs(None, None, None, sub_actors=None)
    )


def test_the_contract_lists_each_sub_actor_with_how_many_scenarios_include_it():
    from simulate.services.harness_environment import _sub_actor_list

    listed = _sub_actor_list(_LIST, _docs())
    assert [(one["id"], one["scenarios_count"]) for one in listed] == [
        ("friend", 1),
        ("partner", 0),
    ]
    assert listed[0]["name"] == "Rowan"
    assert _sub_actor_list(None, _docs()) == []


def test_a_sub_actor_s_turns_are_shown_in_the_transcript_view():
    from simulate.utils.stored_transcript_roles import get_displayable_transcript_roles

    assert "sub_actor" in get_displayable_transcript_roles()


@pytest.mark.django_db
def test_a_transcript_written_without_a_speaker_name_is_stored_with_none(
    organization, workspace
):
    from django.db import connection

    from simulate.models.test_execution import CallTranscript

    call_execution = _ingest(
        organization,
        workspace,
        "old-release",
        CallExecution.SimulationCallType.VOICE,
        {"messages": []},
    )
    # A process still on the previous release writes transcript rows without the column.
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {CallTranscript._meta.db_table} "
            "(id, created_at, updated_at, deleted, call_execution_id, speaker_role, "
            "content, start_time_ms, end_time_ms, confidence_score) "
            "VALUES (gen_random_uuid(), now(), now(), false, %s, 'user', 'Hi', 0, 0, 1)",
            [call_execution.id],
        )
    assert CallTranscript.objects.get(call_execution=call_execution).speaker_name == ""


@pytest.mark.django_db
def test_run_results_filter_and_count_calls_by_the_sub_actor_that_entered_them(
    organization, workspace
):
    from simulate.services.run_results_v3_queries import (
        _sub_actor_facet,
        apply_run_call_query,
    )

    seen = {
        "entered": [
            {"sub_actor": "friend", "name": "Rowan", "triggered": True, "delivered": 1}
        ],
        # Included but never came in: the call is not one the friend entered.
        "absent": [
            {"sub_actor": "friend", "name": "Rowan", "triggered": False, "delivered": 0}
        ],
        "plain": None,
    }
    ids = {}
    for key, summary in seen.items():
        call = _ingest(
            organization,
            workspace,
            f"results-{key}",
            CallExecution.SimulationCallType.TEXT,
            {"messages": []},
            {},
        )
        if summary is not None:
            call.call_metadata = {
                **(call.call_metadata or {}),
                "harness_sub_actors": {"summary": summary},
            }
            call.save(update_fields=["call_metadata"])
        ids[call.id] = key
    calls = CallExecution.objects.filter(id__in=ids)

    def filtered(**filters):
        return sorted(
            ids[one]
            for one in apply_run_call_query(calls, {"filters": filters}).values_list(
                "id", flat=True
            )
        )

    assert filtered(sub_actor=["friend"]) == ["entered"]
    assert filtered(sub_actor=["none"]) == ["absent", "plain"]
    assert filtered(sub_actor_not=["friend"]) == ["absent", "plain"]
    assert filtered(sub_actor=["friend", "none"]) == ["absent", "entered", "plain"]
    assert _sub_actor_facet(calls) == [
        {"value": "none", "count": 2, "label": "Simulated user only"},
        {"value": "friend", "count": 1, "label": "Rowan"},
    ]
    # A run where no sub-actor entered any call offers no sub-actor filter.
    assert (
        _sub_actor_facet(
            calls.exclude(id__in=[k for k, v in ids.items() if v == "entered"])
        )
        == []
    )


def test_a_pinned_suite_version_keeps_each_scenario_s_sub_actors():
    from simulate.services.harness_versioning.versions import _member_content

    included = [{"id": "near", "instruction": "x", "trigger": {"kind": "always"}}]
    assert (
        _member_content({"name": "a", "sub_actors": included})["sub_actors"] == included
    )
    assert "sub_actors" not in _member_content({"name": "a"})

"""Actors on the platform: the environment's list, each scenario's, and who spoke."""

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
    call_actor_choice_labels,
    field_catalogue,
    index_scenarios,
    scenario_row,
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
    "call_actors": [
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
            "actors": [_FRIEND, "not-an-entry"],
        },
    ]


def _keys(queryset):
    return sorted(queryset.values_list("scenario_key", flat=True))


@pytest.mark.django_db
def test_a_suite_indexes_each_scenarios_call_actors_and_filters_by_them(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=2),
        idempotency_key="actor-index",
        workspace=workspace,
    )
    index_scenarios(job, _docs())
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=job)

    assert scenario_row(rows.get(scenario_key="with-friend"))["actors"] == [_FRIEND]
    assert rows.get(scenario_key="alone").actors is None
    assert scenario_row(rows.get(scenario_key="alone"))["actors"] == []

    def filtered(query: str):
        return _keys(apply_filters(rows, QueryDict(query)))

    assert filtered("actors=friend") == ["with-friend"]
    assert filtered("actors=none") == ["alone"]
    assert filtered("actors_not=friend") == ["alone"]
    field = next(one for one in field_catalogue(rows) if one["value"] == "actors")
    assert field["counts"] == {"friend": 1, "none": 1}
    # The panel names each choice: the actor by name, and none as the simulated user alone.
    named = next(
        one
        for one in field_catalogue(
            rows, call_actor_labels=call_actor_choice_labels(_LIST)
        )
        if one["value"] == "actors"
    )
    assert named["choice_labels"] == {
        "none": "Simulated user only",
        "friend": "Rowan",
        "partner": "Sam",
    }
    assert field["choice_labels"] == {"none": "Simulated user only"}


@pytest.mark.django_db
def test_a_suite_with_no_call_actors_offers_no_call_actor_filter(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=1),
        idempotency_key="no-actor-index",
        workspace=workspace,
    )
    index_scenarios(job, _docs()[:1])
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=job)
    assert not any(one["value"] == "actors" for one in field_catalogue(rows))


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
def test_a_call_actor_turn_is_stored_with_its_name_and_the_call_keeps_who_took_part(
    organization, workspace
):
    at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC).timestamp()
    record = {
        "resolved": [{"id": "friend", "name": "Rowan"}],
        "summary": [{"actor": "friend", "triggered": True, "delivered": 1}],
        "events": [{"actor": "friend", "event": "delivered"}],
    }
    metadata = {"harness_call_actors": {"stale": True}}
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
                    "role": "actor",
                    "actor": "friend",
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
            "call_actors": record,
        },
        metadata,
    )

    rows = list(call.transcripts.order_by("start_time_ms"))
    assert [(row.speaker_role, row.speaker_name, row.actor_id) for row in rows] == [
        ("assistant", "", None),
        ("actor", "Rowan", "friend"),
        ("user", "", None),
    ]
    assert metadata["harness_call_actors"] == record
    from simulate.tasks.alk_sim import _build_transcript_text
    from simulate.temporal.activities.xl import _build_transcript_data

    assert _build_transcript_text(call).splitlines() == [
        "Agent: Where to?",
        "Rowan (not the person being served): Add a stop.",
        "Customer: Just the airport.",
    ]
    assert "Rowan (not the person being served): Add a stop." in (
        _build_transcript_data(call)["transcript"]
    )


@pytest.mark.django_db
def test_a_rerun_without_call_actors_clears_the_record(organization, workspace):
    metadata = {"harness_call_actors": {"resolved": [{"id": "friend"}]}}
    _ingest(
        organization,
        workspace,
        "voice-rerun",
        CallExecution.SimulationCallType.VOICE,
        {"messages": [{"role": "user", "content": "Hello."}]},
        metadata,
    )
    assert "harness_call_actors" not in metadata


def test_the_environment_s_list_is_a_stage_output_and_an_emptied_list_replaces_it():
    from simulate.services.hosted_harness_gateway import authoring_stage_outputs

    [listed] = [
        one
        for one in authoring_stage_outputs(None, None, None, call_actors=_LIST)
        if one["kind"] == "call_actors"
    ]
    assert (listed["summary"], listed["data"]) == ("2 actors", _LIST)
    [emptied] = [
        one
        for one in authoring_stage_outputs(
            None, None, None, call_actors={"call_actors": []}
        )
        if one["kind"] == "call_actors"
    ]
    assert emptied["summary"] == "0 actors"
    assert not any(
        one["kind"] == "call_actors"
        for one in authoring_stage_outputs(None, None, None, call_actors=None)
    )


def test_the_contract_lists_each_call_actor_with_how_many_scenarios_include_it():
    from simulate.services.harness_environment import _call_actor_list

    listed = _call_actor_list(_LIST, _docs())
    assert [(one["id"], one["scenarios_count"]) for one in listed] == [
        ("friend", 1),
        ("partner", 0),
    ]
    assert listed[0]["name"] == "Rowan"
    assert _call_actor_list(None, _docs()) == []


def test_a_call_actor_s_turns_are_shown_in_the_transcript_view():
    from simulate.utils.stored_transcript_roles import get_displayable_transcript_roles

    assert "actor" in get_displayable_transcript_roles()


def test_a_scenario_s_own_task_turn_is_shown_in_the_transcript_view():
    from simulate.utils.stored_transcript_roles import get_displayable_transcript_roles

    assert "task" in get_displayable_transcript_roles()


_ACTOR_CALL = {
    "messages": [
        {"role": "assistant", "content": "Where to?"},
        {"role": "actor", "actor": "friend", "name": "Rowan", "content": "Add a stop."},
        {"role": "user", "content": "Just the airport."},
        {
            "role": "actor",
            "actor": "partner",
            "name": "Sam",
            "content": "And a coffee.",
        },
    ]
}


@pytest.mark.django_db
def test_who_spoke_an_actor_turn_is_kept_beside_the_transcript_only_for_actor_turns(
    organization, workspace
):
    from simulate.models import CallTranscriptActor

    call = _ingest(
        organization,
        workspace,
        "actor-turns-table",
        CallExecution.SimulationCallType.VOICE,
        _ACTOR_CALL,
        {},
    )
    stored = CallTranscriptActor.objects.filter(transcript__call_execution=call)
    assert sorted(stored.values_list("actor_id", "speaker_name")) == [
        ("friend", "Rowan"),
        ("partner", "Sam"),
    ]
    assert {turn.transcript.speaker_role for turn in stored} == {"actor"}


@pytest.mark.django_db
def test_a_transcript_without_actors_stores_no_actor_turns(organization, workspace):
    from simulate.models import CallTranscriptActor

    call = _ingest(
        organization,
        workspace,
        "no-actor-turns",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [
                {"role": "assistant", "content": "Hi."},
                {"role": "user", "content": "Hello."},
            ]
        },
        {},
    )
    assert call.transcripts.count() == 2
    assert not CallTranscriptActor.objects.filter(
        transcript__call_execution=call
    ).exists()
    assert [(row.speaker_name, row.actor_id) for row in call.transcripts.all()] == [
        ("", None),
        ("", None),
    ]


@pytest.mark.django_db
def test_ingesting_a_call_again_replaces_its_actor_turns(organization, workspace):
    from simulate.models import CallTranscriptActor
    from simulate.services.hosted_harness_ingestion import _ingest_hosted_transcript

    call = _ingest(
        organization,
        workspace,
        "actor-turns-again",
        CallExecution.SimulationCallType.VOICE,
        _ACTOR_CALL,
        {},
    )
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"messages": [{"role": "user", "content": "Hello."}]}
    ).encode()
    storage = MagicMock()
    storage.get_object.return_value = response
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        _ingest_hosted_transcript(
            call, SimpleNamespace(object_key="transcript.json"), {}
        )
    # The earlier turns' rows go with them, rather than being left behind.
    assert not CallTranscriptActor.objects.filter(
        actor_id__in=["friend", "partner"]
    ).exists()


@pytest.mark.django_db
def test_a_call_s_transcript_is_read_with_who_spoke_in_one_query(
    organization, workspace
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from simulate.tasks.alk_sim import _build_transcript_text

    call = _ingest(
        organization,
        workspace,
        "actor-turns-queries",
        CallExecution.SimulationCallType.VOICE,
        _ACTOR_CALL,
        {},
    )
    with CaptureQueriesContext(connection) as queries:
        text = _build_transcript_text(call)
    assert len(queries) == 1
    assert text.splitlines() == [
        "Agent: Where to?",
        "Rowan (not the person being served): Add a stop.",
        "Customer: Just the airport.",
        "Sam (not the person being served): And a coffee.",
    ]


def test_adding_actors_changes_no_column_of_the_transcript_table():
    import importlib

    from django.db import migrations

    actors = importlib.import_module("simulate.migrations.0104_actors")
    on_transcripts = [
        operation
        for operation in actors.Migration.operations
        if getattr(operation, "model_name", None) == "calltranscript"
    ]
    # Only the speaker_role choices change on the transcript table, which writes no SQL.
    assert [(type(op), op.name) for op in on_transcripts] == [
        (migrations.AlterField, "speaker_role")
    ]


@pytest.mark.django_db
def test_run_results_filter_and_count_calls_by_each_call_actor_planned_for_them(
    organization, workspace
):
    from simulate.services.run_results_v3_queries import (
        _call_actor_facet,
        apply_run_call_query,
    )

    def took_part(**part):
        return [{"actor": "friend", "name": "Rowan", **part}]

    seen = {
        "entered": took_part(triggered=True, delivered=1, drawn=True),
        # Planned, but their trigger never fired.
        "absent": took_part(triggered=False, delivered=0, drawn=True),
        # Triggered, but the call ended before their line was said: dropped, not absent.
        "unsaid": took_part(triggered=True, delivered=0, drawn=True, dropped=["x"]),
        # This call's draw left them out: absent by design.
        "undrawn": took_part(triggered=False, delivered=0, drawn=False),
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
                "harness_call_actors": {"summary": summary},
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

    assert filtered(actor=["friend"]) == ["absent", "entered", "unsaid"]
    assert filtered(actor=["none"]) == ["plain", "undrawn"]
    assert filtered(actor_not=["friend"]) == ["plain", "undrawn"]
    assert filtered(actor=["friend", "none"]) == sorted(seen)
    assert _call_actor_facet(calls) == [
        {
            "value": "friend",
            "count": 3,
            "label": "Rowan",
            "delivered": 1,
            "dropped": 1,
            "not_triggered": 1,
        },
        {
            "value": "none",
            "count": 2,
            "label": "Simulated user only",
            "delivered": 0,
            "dropped": 0,
            "not_triggered": 0,
        },
    ]
    # A run where no call had an actor planned offers no actor filter.
    assert (
        _call_actor_facet(
            calls.filter(
                id__in=[k for k, v in ids.items() if v in ("plain", "undrawn")]
            )
        )
        == []
    )


def test_a_pinned_suite_version_keeps_each_scenario_s_call_actors():
    from simulate.services.harness_versioning.versions import _member_content

    included = [{"id": "near", "instruction": "x", "trigger": {"kind": "always"}}]
    assert _member_content({"name": "a", "actors": included})["actors"] == included
    assert "actors" not in _member_content({"name": "a"})


def test_a_pinned_suite_version_keeps_what_starts_a_scenario_and_what_happened_before_it():
    from simulate.services.harness_versioning.versions import _member_content

    document = {
        "name": "a",
        "pre_run": [
            {
                "actors": ["manager"],
                "where": {"action": "chat.post_message"},
                "about": "deal 8",
            }
        ],
        "starts_with": {"actor": "manager", "text": "Apply the agreed discount"},
        "faults": [{"action": "crm.apply_discount", "mode": "timeout_after_commit"}],
        "limits": {"max_messages": 4},
    }
    kept = _member_content(document)
    for field in ("pre_run", "starts_with", "faults", "limits"):
        assert kept[field] == document[field]
    # An edit to one of them alone reads as a change to the scenario.
    assert _member_content({**document, "faults": []}) != kept


@pytest.mark.django_db
def test_a_hostile_call_actor_filter_value_matches_nothing_and_never_errors(
    organization, workspace
):
    from simulate.services.run_results_v3_queries import apply_run_call_query

    call = _ingest(
        organization,
        workspace,
        "results-hostile",
        CallExecution.SimulationCallType.TEXT,
        {"messages": []},
        {},
    )
    call.call_metadata = {
        **(call.call_metadata or {}),
        "harness_call_actors": {
            "summary": [{"actor": "friend", "name": "Rowan", "delivered": 1}]
        },
    }
    call.save(update_fields=["call_metadata"])
    calls = CallExecution.objects.filter(id=call.id)

    for hostile in (
        'friend" || @.delivered > 0 || @.call_actor == "x',
        "friend\\",
        "$who",
    ):
        assert not apply_run_call_query(
            calls, {"filters": {"actor": [hostile]}}
        ).exists()
        assert apply_run_call_query(
            calls, {"filters": {"actor_not": [hostile]}}
        ).exists()
    assert apply_run_call_query(calls, {"filters": {"actor": ["friend"]}}).exists()


def test_a_filter_value_the_database_cannot_hold_is_refused():
    from simulate.views.run_results_v3 import RunCallFiltersSerializer

    for value in ("\x00", "\ud800"):
        query = RunCallFiltersSerializer(data={"filters": {"actor": [value]}})
        assert not query.is_valid()
    assert RunCallFiltersSerializer(
        data={"filters": {"actor": ["friend", "none"]}}
    ).is_valid()


@pytest.mark.django_db
def test_a_built_bundle_s_call_actor_list_is_stored_once_and_an_empty_one_is_not(
    organization, workspace
):
    from simulate.models import HostedHarnessStageOutput
    from simulate.services.hosted_harness_gateway import _persist_bundle_stage_outputs

    one = {"call_actors": _LIST["call_actors"][:1]}

    def persisted(key, listing):
        job, _ = create_hosted_job(
            organization, _payload(), idempotency_key=key, workspace=workspace
        )
        files = {"actors.json": listing}
        with (
            patch(
                "simulate.services.hosted_harness_gateway._load_bundle_file",
                side_effect=lambda manifest, path, job: files.get(path),
            ),
            patch(
                "simulate.services.hosted_harness_gateway._load_bundle_scenarios",
                return_value=[],
            ),
        ):
            _persist_bundle_stage_outputs(job, {})
            _persist_bundle_stage_outputs(job, {})
        return list(
            HostedHarnessStageOutput.no_workspace_objects.filter(
                job=job, kind="call_actors"
            ).values_list("title", "summary", "data")
        )

    # The bundle has one actors.json; the stage output keeps the actors in it who speak in the conversation.
    world_only = {
        "id": "manager",
        "name": "Mia",
        "permitted_actions": ["deals.approve"],
    }
    assert persisted("bundle-one", {"actors": [*one["call_actors"], world_only]}) == [
        ("Actors", "1 actor", one)
    ]
    assert persisted("bundle-empty", {"actors": [world_only]}) == []
    assert persisted("bundle-none", None) == []


def test_a_pinned_version_s_call_actor_list_replaces_the_run_s_own():
    from simulate.services.hosted_harness import pinned_stage_outputs

    listed = {"kind": "call_actors", "data": _LIST}
    version = SimpleNamespace(documents={"stage_outputs": [listed]})
    assert pinned_stage_outputs(version, SimpleNamespace(documents={})) == {
        "call_actors": listed
    }


@pytest.mark.django_db
def test_the_scenario_filter_names_the_environment_s_own_call_actors(
    organization, workspace
):
    from simulate.models import HostedHarnessStageOutput
    from simulate.services.harness_environment import call_actor_labels

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="labels", workspace=workspace
    )
    assert call_actor_labels(job) == {"none": "Simulated user only"}
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=job, title="Actors", summary="", kind="call_actors", data=_LIST
    )
    assert call_actor_labels(job) == {
        "none": "Simulated user only",
        "friend": "Rowan",
        "partner": "Sam",
    }


def test_stored_call_actor_shapes_keep_only_well_formed_entries():
    from simulate.services.harness_scenarios import call_actor_entries
    from simulate.services.hosted_harness_ingestion import _call_actor_record

    # A trigger that is not a record stays missing, so the actor's own activation applies.
    assert call_actor_entries([{"id": "friend", "trigger": "soon"}, "x"]) == [
        {"id": "friend", "instruction": ""}
    ]
    assert call_actor_entries({"id": "friend"}) is None
    assert _call_actor_record(
        {"summary": [{"actor": "friend"}, "x"], "events": "x", "extra": 1}
    ) == {"summary": [{"actor": "friend"}]}
    assert _call_actor_record(["x"]) is None


def test_the_call_detail_serves_who_each_call_actor_is_and_an_older_call_what_it_kept():
    from simulate.views.run_results_v3 import _call_call_actors

    resolved = [
        {
            "id": "friend",
            "name": "Rowan",
            "role": "roommate",
            "channel": "audio_mix",
            "persona": {},
        },
        {"id": "kid", "name": "Sam"},
        "x",
    ]
    assert _call_call_actors({"harness_call_actors": {"resolved": resolved}}) == [
        {"id": "friend", "name": "Rowan", "role": "roommate", "channel": "audio_mix"},
        {"id": "kid", "name": "Sam"},
    ]
    summary = [
        {"actor": "friend", "triggered": True, "delivered": 0},
        {"actor": "kid", "triggered": False, "delivered": 0, "drawn": False},
    ]
    assert _call_call_actors(
        {"harness_call_actors": {"resolved": resolved, "summary": summary}}
    ) == [
        {
            "id": "friend",
            "name": "Rowan",
            "role": "roommate",
            "channel": "audio_mix",
            "delivery": "dropped",
        },
        {"id": "kid", "name": "Sam", "delivery": "not_drawn"},
    ]
    assert _call_call_actors({"harness_call_actors": {"summary": []}}) == []
    assert _call_call_actors(None) == []


@pytest.mark.django_db
def test_a_rerun_snapshot_keeps_who_spoke_each_turn(organization, workspace):
    from simulate.models.test_execution import CallExecutionSnapshot
    from simulate.services.call_rerun import clear_call_for_rerun

    call = _ingest(
        organization,
        workspace,
        "rerun-snapshot",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [
                {
                    "role": "actor",
                    "actor": "friend",
                    "name": "Rowan",
                    "content": "Add a stop.",
                }
            ]
        },
        {},
    )
    clear_call_for_rerun(call)

    [turn] = CallExecutionSnapshot.objects.get(call_execution=call).transcripts
    assert (turn["speaker_role"], turn["speaker_name"], turn["actor_id"]) == (
        "actor",
        "Rowan",
        "friend",
    )


def test_a_member_stored_before_the_new_fields_reads_unchanged_against_the_same_scenario_now():
    from simulate.services.harness_versioning.diffs import suite_diff
    from simulate.services.harness_versioning.versions import _member_content

    old = {"scenario_key": "a", "name": "a", "instruction": "Cancel it."}
    now = {
        **old,
        "pre_run": [],
        "faults": [],
        "starts_with": None,
        "limits": {},
        "history": [],
    }
    # The stored member carries none of the empty ones.
    assert not {"pre_run", "faults", "starts_with", "limits", "history"} & set(
        _member_content(now)
    )
    assert _member_content(now) == _member_content(old)
    assert suite_diff([old], [now])["changed"] == {}
    assert suite_diff(
        [old],
        [{**now, "faults": [{"action": "crm.apply", "mode": "timeout_after_commit"}]}],
    )["changed"] == {"a": ["faults"]}


def test_a_sandbox_list_adds_at_most_the_cap_to_a_call():
    from simulate.services.hosted_harness_ingestion import (
        MAX_RECORDED_ENTRIES,
        _call_actor_record,
    )

    many = [{"actor": "a", "n": n} for n in range(MAX_RECORDED_ENTRIES + 50)]
    record = _call_actor_record({"events": [*many, "not a dict"]})

    assert len(record["events"]) == MAX_RECORDED_ENTRIES
    assert record["events"][0]["n"] == 0


def test_a_pre_run_message_keeps_its_actor_s_name_in_the_served_trial_state():
    from simulate.views.run_results_v3 import TrialStateSerializer

    served = TrialStateSerializer(
        {
            "decisions": [
                {
                    "actor": "lead",
                    "name": "Sam Lee",
                    "play": "history",
                    "action": "chat.post_message",
                },
                {"actor": "manager", "play": "history", "action": "chat.post_message"},
            ]
        }
    ).data
    assert [(one["actor"], one.get("name")) for one in served["decisions"]] == [
        ("lead", "Sam Lee"),
        ("manager", None),
    ]

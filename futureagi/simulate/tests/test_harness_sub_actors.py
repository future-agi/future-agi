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
)
from simulate.services.hosted_harness import create_hosted_job, register_attempt
from simulate.services.hosted_harness_ingestion import _ingest_hosted_transcript
from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload


def _docs():
    return [
        {"scenario_key": "alone", "name": "alone", "instruction": "Book it."},
        {
            "scenario_key": "with-bystander",
            "name": "with-bystander",
            "instruction": "Book it.",
            "sub_actors": [
                {
                    "id": "subactor_bystander",
                    "trigger": {"kind": "mid_journey", "turn": 2},
                }
            ],
        },
        {
            "scenario_key": "with-passenger",
            "name": "with-passenger",
            "instruction": "Book it.",
            "sub_actors": ["subactor_passenger"],
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
        _payload(scenario_count=3),
        idempotency_key="sub-actor-index",
        workspace=workspace,
    )
    index_scenarios(job, _docs())
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=job)

    bound = rows.get(scenario_key="with-bystander")
    assert scenario_row(bound)["sub_actors"] == [
        {"id": "subactor_bystander", "trigger": {"kind": "mid_journey", "turn": 2}}
    ]
    assert rows.get(scenario_key="with-passenger").sub_actors == [
        {"id": "subactor_passenger", "trigger": {}}
    ]
    assert rows.get(scenario_key="alone").sub_actors is None

    def filtered(query: str):
        return _keys(apply_filters(rows, QueryDict(query)))

    assert filtered("sub_actors=subactor_bystander") == ["with-bystander"]
    assert filtered("sub_actors=none") == ["alone"]
    assert filtered("sub_actors_not=subactor_passenger") == ["alone", "with-bystander"]

    field = next(one for one in field_catalogue(rows) if one["value"] == "sub_actors")
    assert field["counts"] == {
        "subactor_bystander": 1,
        "subactor_passenger": 1,
        "none": 1,
    }


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


def _ingest(organization, workspace, key, call_type, messages):
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
    response.read.return_value = json.dumps({"messages": messages}).encode()
    storage = MagicMock()
    storage.get_object.return_value = response
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        _ingest_hosted_transcript(call, SimpleNamespace(object_key="transcript.json"))
    return call


@pytest.mark.django_db
def test_a_sub_actor_turn_is_stored_with_its_name(organization, workspace):
    at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC).timestamp()
    call = _ingest(
        organization,
        workspace,
        "voice-one",
        CallExecution.SimulationCallType.VOICE,
        [
            {
                "role": "assistant",
                "content": "Where to?",
                "started_speaking_at": at,
                "stopped_speaking_at": at + 1,
            },
            {
                "role": "sub_actor",
                "name": "Bystander",
                "sub_actor": "subactor_bystander",
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
    )

    rows = list(call.transcripts.order_by("start_time_ms"))
    assert [(row.speaker_role, row.speaker_name) for row in rows] == [
        ("assistant", ""),
        ("sub_actor", "Bystander"),
        ("user", ""),
    ]
    from simulate.tasks.alk_sim import _build_transcript_text

    assert _build_transcript_text(call).splitlines() == [
        "Agent: Where to?",
        "Bystander (not the customer): Add a stop.",
        "Customer: Just the airport.",
    ]


@pytest.mark.django_db
def test_a_second_chat_participant_is_its_own_message_with_its_name(
    organization, workspace
):
    from simulate.models.chat_message import ChatMessageModel

    call = _ingest(
        organization,
        workspace,
        "chat-one",
        CallExecution.SimulationCallType.TEXT,
        [
            {"role": "user", "content": "One item please."},
            {"role": "tool_call_result", "name": "lookup", "content": "In stock."},
            {"role": "assistant", "content": "Added."},
            {
                "role": "sub_actor",
                "name": "Friend",
                "sub_actor": "subactor_friend",
                "content": "Add one more.",
            },
            {"role": "user", "content": "Just the one."},
            {"role": "assistant", "content": "Done."},
        ],
    )

    rows = list(
        ChatMessageModel.objects.filter(call_execution=call).order_by(
            "created_at", "role"
        )
    )
    assert [row.role for row in rows] == [
        "user",
        "assistant",
        "sub_actor",
        "user",
        "assistant",
    ]
    friend = next(row for row in rows if row.role == "sub_actor")
    assert friend.content == [
        {"role": "sub_actor", "content": "Add one more.", "name": "Friend"}
    ]
    assert not any(
        "name" in item
        for row in rows
        if row.role != "sub_actor"
        for item in row.content
    )
    from simulate.tasks.alk_sim import _build_transcript_text

    assert "Friend (not the customer): Add one more." in _build_transcript_text(call)
    assert "Assistant: Add one more." not in _build_transcript_text(call)


def test_the_sandboxes_are_told_about_sub_actors_only_when_the_setting_is_on(
    settings, monkeypatch
):
    from simulate.services.hosted_harness_gateway import _platform_simulator_material

    monkeypatch.setenv("ALK_HOSTED_AGENTCC_BASE_URL", "https://gateway.futureagi.test")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-key")
    settings.ALK_SUB_ACTORS = False
    assert "HARNESS_SUB_ACTORS" not in _platform_simulator_material()
    settings.ALK_SUB_ACTORS = True
    assert _platform_simulator_material()["HARNESS_SUB_ACTORS"] == "1"


def test_the_environments_list_says_whether_sub_actors_are_on(
    settings, user, workspace
):
    client = APIClient()
    client.force_authenticate(user=user)
    for enabled in (False, True):
        settings.ALK_SUB_ACTORS = enabled
        response = client.get(
            "/simulate/api/harness-environments/", HTTP_X_WORKSPACE_ID=str(workspace.id)
        )
        assert response.status_code == 200
        assert response.json()["sub_actors"] is enabled

"""Revising or adding scenarios through the environment's builder agent."""

import uuid

import pytest
from rest_framework.test import APIClient

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.hosted_harness_conversation import pending_commands
from simulate.tests.test_harness_amend_archive import _run_environment

BASE = "/simulate/api/harness-environments"


@pytest.fixture
def environment(user, workspace):
    return _run_environment(user, workspace, "scenario-changes")


@pytest.fixture
def client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults["HTTP_X_WORKSPACE_ID"] = str(workspace.id)
    return client


@pytest.fixture
def builder(settings, monkeypatch):
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
    scheduled = []
    monkeypatch.setattr(
        "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime",
        lambda conversation_id, base_url: scheduled.append(conversation_id),
    )
    return scheduled


def _change(client, environment, body, **headers):
    return client.post(
        f"{BASE}/{environment.id}/scenarios/changes/", body, format="json", **headers
    )


def _commands(environment):
    conversation = HostedHarnessConversation.no_workspace_objects.get(job=environment)
    return pending_commands(conversation, after=0)["commands"]


@pytest.mark.django_db
def test_a_revision_is_handed_to_the_builder_naming_the_selected_scenarios(
    client, environment, builder
):
    row = HostedHarnessScenario.no_workspace_objects.filter(job=environment).first()
    body = {
        "kind": "revise",
        "scenario_ids": [str(row.id)],
        "instruction": "the agent must read the booking back before confirming",
    }

    first = _change(client, environment, body, HTTP_IDEMPOTENCY_KEY="revise-once")
    again = _change(client, environment, body, HTTP_IDEMPOTENCY_KEY="revise-once")

    assert first.status_code == 202, first.content
    assert again.status_code == 202, again.content
    commands = _commands(environment)
    assert len(commands) == 1
    content = commands[0]["payload"]["content"]
    assert "re-prove" in content and "read the booking back" in content
    assert f"{row.name or row.scenario_key} ({row.scenario_key})" in content


@pytest.mark.django_db
def test_adding_asks_the_builder_for_that_many_new_scenarios(
    client, environment, builder
):
    response = _change(
        client,
        environment,
        {"kind": "add", "count": 3, "instruction": "callers booking for a friend"},
    )

    assert response.status_code == 202, response.content
    content = _commands(environment)[0]["payload"]["content"]
    assert content.startswith("Add exactly 3 new scenarios to this suite.")
    assert "Keep every existing scenario as it is" in content
    assert builder


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"kind": "revise", "instruction": "change it"},
        {"kind": "revise", "scenario_ids": [str(uuid.uuid4())]},
        {"kind": "add"},
    ],
)
def test_an_incomplete_change_is_refused(client, environment, builder, body):
    response = _change(client, environment, body)

    assert response.status_code == 400, response.content
    assert builder == []


@pytest.mark.django_db
def test_changes_wait_for_the_build_and_name_known_scenarios(
    client, environment, builder
):
    unknown = _change(
        client,
        environment,
        {"kind": "revise", "scenario_ids": [str(uuid.uuid4())], "instruction": "x"},
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    building = _change(client, environment, {"kind": "add", "count": 1})

    assert (unknown.status_code, unknown.json()["code"]) == (404, "scenario_not_found")
    assert (building.status_code, building.json()["code"]) == (
        409,
        "environment_not_ready",
    )
    assert builder == []


@pytest.mark.django_db
def test_adding_past_the_suite_limit_is_refused(client, environment, builder):
    HostedHarnessScenario.no_workspace_objects.bulk_create(
        HostedHarnessScenario(job=environment, scenario_key=f"extra-{index}")
        for index in range(197)
    )

    response = _change(client, environment, {"kind": "add", "count": 2})

    assert (response.status_code, response.json()["code"]) == (400, "suite_too_large")
    assert builder == []

"""An agent's env values changed on its own page: a value removed, a value the test
environment makes itself refused at save and before any build or run, a missing
required value named, and the environments following production moved with a
values-only change."""

from unittest.mock import patch

import pytest
from rest_framework import status

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
)
from simulate.services.harness_agent_versions import (
    _side_row,
    credential_set,
    values_gate,
)
from simulate.services.harness_versioning import hashing
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_versions_api import AGENTS, NUDGE, _change
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import (
    _connect,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def production(agent):
    version = _connect(agent)
    with patch(NUDGE):
        _preflight(version)
    version.refresh_from_db()
    return version


CONTRACT = "simulate.services.agent_contracts.stored_version_contract"
# The support agent's login, as its reading stored it on the test stack.
LOCAL_AUTH = {
    "runtime": {
        "interface": {
            "local_auth": {
                "server_environment": "AGENT_API_TOKEN",
                "template_variable": "agent_api_token",
            }
        }
    }
}


def _set(client, agent, **body):
    response = _change(client, agent, kind="credentials", **body)
    assert response.status_code in (200, 202), response.content
    return response.json()


def _names(version_id):
    return credential_set(_side_row(version_id, blob=True)).names()


def _passed(version_id):
    with patch(NUDGE):
        _preflight(AgentVersion.objects.get(id=version_id))


# --- removal ---------------------------------------------------------------------


def test_a_value_can_be_removed(auth_client, agent, production):
    first = _set(auth_client, agent, secret_values={"DB_URL": "pg://x", "KEEP": "k"})
    _passed(first["version"]["id"])

    second = _set(auth_client, agent, remove_names=["DB_URL"])

    assert second["created"] is True
    assert _names(second["version"]["id"]) == ["KEEP"]
    runtime = HarnessAgentVersionRuntime.objects.get(
        agent_version_id=second["version"]["id"]
    )
    assert runtime.diff["variables"]["removed"] == ["DB_URL"]
    assert runtime.trigger == HarnessAgentVersionRuntime.Trigger.CREDENTIALS


def test_removing_and_setting_in_one_change(auth_client, agent, production):
    first = _set(auth_client, agent, secret_values={"OLD": "o"})
    _passed(first["version"]["id"])

    second = _set(auth_client, agent, secret_values={"NEW": "n"}, remove_names=["OLD"])

    assert _names(second["version"]["id"]) == ["NEW"]


def test_removing_a_name_that_is_not_there_makes_no_version(
    auth_client, agent, production
):
    body = _set(auth_client, agent, remove_names=["NEVER_SET"])

    assert body["created"] is False
    assert body["version"]["id"] == str(production.id)


@pytest.mark.parametrize(
    "body",
    [
        {"remove_names": []},
        {"remove_names": ["1BAD"]},
        {"secret_values": {"A": "x"}, "remove_names": ["A"]},
    ],
)
def test_a_removal_the_form_cannot_take_is_refused(
    auth_client, agent, production, body
):
    response = _change(auth_client, agent, kind="credentials", **body)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert AgentVersion.objects.filter(agent_definition=agent).count() == 1


def test_names_to_remove_belong_to_a_values_change_only(auth_client, agent, production):
    response = _change(auth_client, agent, kind="sync", remove_names=["A"])

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_a_provider_key_is_not_removed_here(auth_client, agent, production):
    alias = sorted(hashing.PROVIDER_CREDENTIAL_ALIASES)[0]

    response = _change(auth_client, agent, kind="credentials", remove_names=[alias])

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "provider_key_not_removable"


# --- a value the test environment makes itself -------------------------------------


@pytest.mark.parametrize("name", ["AGENT_API_TOKEN", "agent_api_token"])
def test_a_value_the_environment_makes_is_refused_at_save(
    auth_client, agent, production, name
):
    with patch(CONTRACT, return_value=LOCAL_AUTH):
        response = _change(
            auth_client, agent, kind="credentials", secret_values={name: "mine"}
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    body = response.json()
    assert body["code"] == "value_made_by_environment"
    assert body["details"]["names"] == [name]
    assert "mine" not in response.content.decode()
    assert AgentVersion.objects.filter(agent_definition=agent).count() == 1


def test_a_value_the_environment_makes_can_always_be_removed(
    auth_client, agent, production
):
    # Saved before this rule, as on the test stack.
    first = _set(auth_client, agent, secret_values={"AGENT_API_TOKEN": "old"})
    _passed(first["version"]["id"])

    with patch(CONTRACT, return_value=LOCAL_AUTH):
        second = _set(auth_client, agent, remove_names=["AGENT_API_TOKEN"])

    # The same set as the first version: that one stands for it again, and
    # becomes production in place of the version with the token.
    assert second["version"]["id"] == str(production.id)
    production.refresh_from_db()
    assert production.status == "active"
    assert "AGENT_API_TOKEN" not in _names(production.id)


# --- a value the reading lists as required --------------------------------------------


def test_removing_a_required_value_is_allowed_and_named(auth_client, agent, production):
    first = _set(auth_client, agent, secret_values={"DB_URL": "pg://x", "KEEP": "k"})
    _passed(first["version"]["id"])
    HarnessAgentVersionRuntime.objects.filter(
        agent_version_id=first["version"]["id"]
    ).update(required_variable_names=["DB_URL"])

    second = _set(auth_client, agent, remove_names=["DB_URL"])

    assert second["created"] is True
    assert second["missing_required_names"] == ["DB_URL"]


def test_a_change_with_every_required_value_names_none(auth_client, agent, production):
    body = _set(auth_client, agent, secret_values={"DB_URL": "pg://x"})

    assert body["missing_required_names"] == []


# --- the gate before a build, rebuild or run -------------------------------------------


def _runtime(version, **fields):
    HarnessAgentVersionRuntime.objects.filter(agent_version=version).update(**fields)
    return HarnessAgentVersionRuntime.objects.get(agent_version=version)


def test_the_gate_refuses_a_value_the_environment_makes(agent, production):
    runtime = _runtime(production, credential_names=["AGENT_API_TOKEN", "DB_URL"])

    with patch(CONTRACT, return_value=LOCAL_AUTH):
        with pytest.raises(HostedHarnessError) as caught:
            values_gate(runtime)

    assert caught.value.code == "value_made_by_environment"
    assert caught.value.details == {"names": ["AGENT_API_TOKEN"]}


def test_the_gate_counts_values_the_request_brings(agent, production):
    runtime = _runtime(production, credential_names=["DB_URL"])

    with patch(CONTRACT, return_value=LOCAL_AUTH):
        with pytest.raises(HostedHarnessError) as caught:
            values_gate(runtime, extra_names=["agent_api_token"])

    assert caught.value.code == "value_made_by_environment"


def test_the_gate_refuses_a_missing_required_value(agent, production):
    runtime = _runtime(
        production,
        credential_names=["DB_URL"],
        required_variable_names=["DB_URL", "KEY"],
    )

    with pytest.raises(HostedHarnessError) as caught:
        values_gate(runtime)

    assert caught.value.code == "required_values_missing"
    assert caught.value.details == {"names": ["KEY"]}
    # A value the request brings meets it.
    values_gate(runtime, extra_names=["KEY"])


def test_the_gate_lets_through_what_it_cannot_judge(agent, production):
    # No reading's list, no stored contract: the kit stays the last line.
    values_gate(_runtime(production, required_variable_names=None))
    values_gate(None)


# --- the agent page's list of names ------------------------------------------------


def test_the_page_lists_names_never_values(auth_client, agent, production):
    body = _set(
        auth_client,
        agent,
        secret_values={"AGENT_API_TOKEN": "secret-one", "DB_URL": "secret-two"},
    )
    HarnessAgentVersionRuntime.objects.filter(
        agent_version_id=body["version"]["id"]
    ).update(required_variable_names=["DB_URL", "KEY"])

    with patch(CONTRACT, return_value=LOCAL_AUTH):
        response = auth_client.get(
            f"{AGENTS}{agent.id}/versions/{body['version']['id']}/values/"
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert "secret-" not in response.content.decode()
    assert response.json() == {
        "names": [
            {"name": "AGENT_API_TOKEN", "required": False, "made_by_environment": True},
            {"name": "DB_URL", "required": True, "made_by_environment": False},
        ],
        "missing_required_names": ["KEY"],
        "made_by_environment": ["AGENT_API_TOKEN", "agent_api_token"],
    }


def test_another_agents_version_is_not_found(auth_client, agent, production):
    response = auth_client.get(
        f"{AGENTS}{agent.id}/versions/00000000-0000-0000-0000-000000000000/values/"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND

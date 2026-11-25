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
    HostedHarnessJob,
)
from simulate.services.harness_agent_versions import (
    _side_row,
    credential_set,
    values_gate,
)
from simulate.services.harness_versioning import hashing
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_agent_versions_api import AGENTS, NUDGE, _change
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import (
    _completed,
    _connect,
    _following,
    _pinned,
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
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


# --- environments follow a values-only change -------------------------------------------


def _store_reading(version):
    from simulate.services.agent_setup import store_agent
    from simulate.tests.test_agent_setup_service import source_agent

    assert store_agent(
        agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3"
    )


def test_a_values_only_change_becomes_production_and_moves_followers(
    auth_client, agent, production
):
    _store_reading(production)
    following = _following(_completed(_environment(agent, 1), production))
    pinned = _pinned(_completed(_environment(agent, 2), production))

    body = _set(auth_client, agent, secret_values={"DB_URL": "pg://x"})
    _store_reading(AgentVersion.objects.get(id=body["version"]["id"]))
    with patch(NUDGE) as nudge:
        _passed(body["version"]["id"])

    version = AgentVersion.objects.get(id=body["version"]["id"])
    production.refresh_from_db()
    following.refresh_from_db()
    pinned.refresh_from_db()
    assert version.status == "active"
    assert production.status == "archived"
    assert following.agent_version_id == version.id
    assert pinned.agent_version_id == production.id
    # No rebuild card, neither for the environment that moved nor the pinned one.
    carded = {str(one.args[0]) for one in nudge.call_args_list}
    assert str(following.id) not in carded
    assert str(pinned.id) not in carded


def test_a_follower_busy_at_the_move_gets_no_card_once_moved(
    auth_client, agent, production
):
    from simulate.services.harness_versioning.rebuild import (
        retry_deferred_activations,
    )

    _store_reading(production)
    following = _following(_completed(_environment(agent, 1), production))
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    body = _set(auth_client, agent, secret_values={"DB_URL": "pg://x"})
    _store_reading(AgentVersion.objects.get(id=body["version"]["id"]))
    _passed(body["version"]["id"])
    following.refresh_from_db()
    assert following.agent_version_id == production.id

    _completed(following, production)
    with patch(NUDGE) as nudge:
        outcome = retry_deferred_activations(body["version"]["id"])

    assert outcome == {str(following.id): "activated"}
    following.refresh_from_db()
    assert str(following.agent_version_id) == body["version"]["id"]
    assert nudge.call_args_list == []


def test_made_production_by_hand_later_the_cards_come_back(
    auth_client, agent, production
):
    from simulate.services.harness_agent_versions import quiet_production

    _store_reading(production)
    _following(_completed(_environment(agent, 1), production))
    body = _set(auth_client, agent, secret_values={"DB_URL": "pg://x"})
    _store_reading(AgentVersion.objects.get(id=body["version"]["id"]))
    _passed(body["version"]["id"])
    assert quiet_production(body["version"]["id"])

    # Production moved back by hand, then forward by hand: a plain move.
    from simulate.services.harness_agent_versions import promote_to_production

    with patch(NUDGE):
        promote_to_production(production)
        promote_to_production(AgentVersion.objects.get(id=body["version"]["id"]))

    assert not quiet_production(body["version"]["id"])


def test_the_next_build_after_a_failed_one_takes_the_new_values(
    auth_client, agent, production
):
    # The rehearsal's case: a build failed on a value the agent then lost. The
    # failed environment is left as it is; pressing Build again builds the new
    # production version.
    from simulate.services.harness_provider import _connected_build_runtime

    _store_reading(production)
    environment = _following(_environment(agent, 1))
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.FAILED, agent_version=production
    )

    body = _set(auth_client, agent, secret_values={"DB_URL": "pg://x"})
    _store_reading(AgentVersion.objects.get(id=body["version"]["id"]))
    _passed(body["version"]["id"])

    runtime = _connected_build_runtime(
        agent.organization_id,
        agent.workspace_id,
        {"metadata": {"agent_definition_id": str(agent.id)}},
    )
    assert str(runtime.agent_version_id) == body["version"]["id"]
    environment.refresh_from_db()
    assert environment.agent_version_id == production.id


def test_two_values_changes_in_a_row_keep_the_first(auth_client, agent, production):
    _store_reading(production)
    first = _set(auth_client, agent, secret_values={"A": "1"})
    _passed(first["version"]["id"])

    second = _set(auth_client, agent, secret_values={"B": "2"})

    assert _names(second["version"]["id"]) == ["A", "B"]


def test_a_code_change_still_waits_to_be_made_production(
    auth_client, agent, production
):
    from simulate.services.harness_agent_changes import HeadResult

    _store_reading(production)
    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        body = _change(auth_client, agent, kind="sync").json()
    version = AgentVersion.objects.get(id=body["version"]["id"])
    _store_reading(version)
    _passed(version.id)

    version.refresh_from_db()
    production.refresh_from_db()
    assert version.status == "draft"
    assert production.status == "active"


def test_a_values_change_on_an_old_production_is_only_offered(
    auth_client, agent, production
):
    _store_reading(production)
    body = _set(auth_client, agent, secret_values={"A": "1"})
    # Production moved meanwhile, to another version made from the same one.
    other = _set(auth_client, agent, secret_values={"Z": "9"})
    _passed(other["version"]["id"])

    _passed(body["version"]["id"])

    assert AgentVersion.objects.get(id=body["version"]["id"]).status != "active"
    assert AgentVersion.objects.get(id=other["version"]["id"]).status == "active"


# --- the gate at each entry point ------------------------------------------------------


def test_a_build_with_a_value_the_environment_makes_is_refused_before_any_job(
    auth_client, agent, production, workspace
):
    from simulate.tests.test_harness_provider import _v1_payload

    _runtime(production, credential_names=["AGENT_API_TOKEN", "DB_URL"])
    payload = _v1_payload()
    payload["metadata"] = {"agent_definition_id": str(agent.id)}
    before = HostedHarnessJob.no_workspace_objects.count()

    with (
        patch(CONTRACT, return_value=LOCAL_AUTH),
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start,
        patch("simulate.services.harness_usage.require_harness_run_usage") as usage,
    ):
        response = auth_client.post(
            "/simulate/api/harness-jobs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="values-gate-build",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

    assert response.status_code == 422, response.content
    assert response.json()["error"] == "value_made_by_environment"
    assert HostedHarnessJob.no_workspace_objects.count() == before
    start.assert_not_called()
    usage.assert_not_called()


def test_a_run_with_a_value_the_environment_makes_is_refused_and_reads_once(
    user, workspace, storage
):
    from simulate.services.hosted_harness import create_selected_harness_run
    from simulate.tests.test_harness_amend_archive import NAMES, _key
    from simulate.tests.test_harness_versioning_api import _versioned

    environment = _versioned(user, workspace, "values-gate-run", storage)
    # An environment from before versioning: its own references are the names its
    # runs carry, and one of them is the name the environment makes itself.
    payload = dict(environment.payload)
    refs = dict(payload["agent"]["secret_refs"])
    refs["AGENT_API_TOKEN"] = next(iter(refs.values()))
    payload["agent"] = {**payload["agent"], "secret_refs": refs}
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload=payload
    )
    environment.refresh_from_db()
    before = HostedHarnessJob.no_workspace_objects.count()

    with patch(CONTRACT, return_value=LOCAL_AUTH) as contract:
        with pytest.raises(HostedHarnessError) as caught:
            create_selected_harness_run(
                environment,
                scenario_keys=[_key(NAMES[0])],
                trials=1,
                idempotency_key="values-gate-run",
            )

    assert caught.value.code == "value_made_by_environment"
    assert HostedHarnessJob.no_workspace_objects.count() == before
    # Read once, before the environment was locked; not again under the lock.
    assert contract.call_count == 1


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
            {
                "name": "AGENT_API_TOKEN",
                "required": False,
                "made_by_environment": True,
                "provider_key": False,
            },
            {
                "name": "DB_URL",
                "required": True,
                "made_by_environment": False,
                "provider_key": False,
            },
        ],
        "missing_required_names": ["KEY"],
        "made_by_environment": ["AGENT_API_TOKEN", "agent_api_token"],
    }


def test_the_page_marks_a_provider_key(auth_client, agent, production):
    alias = sorted(hashing.PROVIDER_CREDENTIAL_ALIASES)[0]
    HarnessAgentVersionRuntime.objects.filter(agent_version=production).update(
        credential_names=[alias, "DB_URL"]
    )

    response = auth_client.get(f"{AGENTS}{agent.id}/versions/{production.id}/values/")

    flags = {one["name"]: one["provider_key"] for one in response.json()["names"]}
    assert flags == {alias: True, "DB_URL": False}


def test_another_agents_version_is_not_found(auth_client, agent, production):
    response = auth_client.get(
        f"{AGENTS}{agent.id}/versions/00000000-0000-0000-0000-000000000000/values/"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND

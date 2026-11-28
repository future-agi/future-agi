import pytest
from rest_framework import status

from simulate.models import (
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
    HostedHarnessStageOutput,
)
from simulate.tests.test_agent_overview_api import (
    AGENTS,
    ENVIRONMENTS,
    _agent,
    _environment,
    _environment_version,
    _job,
    _run_test,
)

State = HostedHarnessJob.State


def _get(auth_client, workspace, **params):
    response = auth_client.get(
        ENVIRONMENTS, params, HTTP_X_WORKSPACE_ID=str(workspace.id)
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def _ids(body):
    return {row["id"] for row in body["results"]}


def _set(job, **fields):
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(**fields)
    job.refresh_from_db()
    return job


def _payload(job, **parts):
    return _set(job, payload={**job.payload, **parts})


def _contract(job, data):
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=job, title="Contract", summary="", kind="contract", data=data
    )


@pytest.mark.django_db
def test_status_filter_matches_the_status_each_row_reports(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    by_state = {
        state: _set(_environment(agent, minute), state=state)
        for minute, state in enumerate(
            (
                State.QUEUED,
                State.PROVISIONING,
                State.RUNNING,
                State.FINALIZING,
                State.CLEANING_UP,
                State.COMPLETED,
                State.FAILED,
                State.CANCELED,
            )
        )
    }
    expected = {
        "building": {State.QUEUED, State.PROVISIONING},
        "running": {State.RUNNING, State.FINALIZING, State.CLEANING_UP},
        "completed": {State.COMPLETED},
        "failed": {State.FAILED, State.CANCELED},
    }

    for wanted, states in expected.items():
        body = _get(auth_client, workspace, status=wanted)
        assert _ids(body) == {str(by_state[s].id) for s in states}
        assert {row["status"] for row in body["results"]} == {wanted}

    counts = _get(auth_client, workspace, status="failed")["status_counts"]
    assert counts == {
        "awaiting_approval": 0,
        "building": 2,
        "running": 3,
        "completed": 1,
        "failed": 2,
    }


@pytest.mark.django_db
def test_agent_type_filter_matches_the_type_each_row_reports(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    vapi = _payload(_environment(agent, 1), agent={"connector": " Vapi "})
    http = _payload(_environment(agent, 2), agent={"connector": "http"})
    auto_voice = _payload(_environment(agent, 3), agent={"connector": "auto"})
    _contract(auto_voice, {"modality": "Voice"})
    legacy_voice = _set(
        _payload(_environment(agent, 4), agent={"connector": "auto"}),
        stage_outputs=[{"kind": "contract", "data": {"modality": "voice"}}],
    )
    shadowed = _set(
        _environment(agent, 5),
        stage_outputs=[{"kind": "contract", "data": {"modality": "voice"}}],
    )
    _contract(shadowed, {"modality": "chat"})
    bare = _environment(agent, 6)

    voice = _get(auth_client, workspace, agent_type="voice")
    chat = _get(auth_client, workspace, agent_type="chat")

    assert _ids(voice) == {str(vapi.id), str(auto_voice.id), str(legacy_voice.id)}
    assert _ids(chat) == {str(http.id), str(shadowed.id), str(bare.id)}
    assert {row["agent_type"] for row in voice["results"]} == {"voice"}
    assert {row["agent_type"] for row in chat["results"]} == {"chat"}


@pytest.mark.django_db
def test_search_matches_the_shown_name_or_the_repository(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    renamed = _set(_environment(agent, 1), name="Refund Desk")
    from_metadata = _payload(
        _set(_environment(agent, 2), name=""),
        metadata={"agent_name": "Billing Line"},
    )
    from_repo = _payload(
        _set(_environment(agent, 3), name=""),
        source={"kind": "github", "repository": "acme/returns-bot"},
    )
    _set(_environment(agent, 4), name="Unrelated")

    assert _ids(_get(auth_client, workspace, search="refund")) == {str(renamed.id)}
    assert _ids(_get(auth_client, workspace, search="BILLING")) == {
        str(from_metadata.id)
    }
    assert _ids(_get(auth_client, workspace, search="acme/")) == {str(from_repo.id)}
    assert len(_ids(_get(auth_client, workspace, search="   "))) == 4


@pytest.mark.django_db
def test_filters_combine_and_reset_nothing_in_the_counts(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    match = _payload(
        _set(_environment(agent, 1), name="Refund voice", state=State.COMPLETED),
        agent={"connector": "retell"},
    )
    _set(_environment(agent, 2), name="Refund chat", state=State.COMPLETED)
    _payload(
        _set(_environment(agent, 3), name="Refund voice", state=State.FAILED),
        agent={"connector": "retell"},
    )

    body = _get(
        auth_client, workspace, search="refund", status="completed", agent_type="voice"
    )

    assert _ids(body) == {str(match.id)}
    assert body["count"] == 1
    assert sum(body["status_counts"].values()) == 3


@pytest.mark.django_db
def test_each_row_names_its_agent_and_default_environment_version(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    linked = _environment(agent, 1)
    version = _environment_version(linked, 3)
    _set(linked, environment_version=version)
    unlinked = _job(_run_test(agent), minutes=2)
    _set(unlinked, run_test=None)

    rows = {row["id"]: row for row in _get(auth_client, workspace)["results"]}

    assert rows[str(linked.id)]["agent"] == {"id": str(agent.id), "name": "Northwind"}
    assert rows[str(linked.id)]["environment_version"] == {
        "id": str(version.id),
        "version_number": 3,
    }
    assert rows[str(unlinked.id)]["agent"] is None
    assert rows[str(unlinked.id)]["environment_version"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [{"status": "ready"}, {"agent_type": "robotics"}, {"search": "x" * 256}],
)
def test_unknown_filter_values_are_refused(auth_client, workspace, params):
    response = auth_client.get(
        ENVIRONMENTS, params, HTTP_X_WORKSPACE_ID=str(workspace.id)
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content


@pytest.mark.django_db
def test_an_upload_agent_without_a_stored_setup_reports_its_source(
    auth_client, organization, workspace
):
    upload = _agent(organization, workspace, "Folder Test", provider="")
    version = upload.create_version(
        description="", commit_message="v1", status="active"
    )
    HarnessAgentVersionRuntime.objects.create(
        agent_version=version,
        agent_definition=upload,
        organization=organization,
        hash="sha256:" + "1" * 64,
        source_kind="archive",
        trigger="creation",
    )
    hosted = _agent(organization, workspace, "Northwind")

    response = auth_client.get(AGENTS, {"limit": 50})

    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {row["agent_name"]: row for row in response.json()["results"]}
    assert rows["Folder Test"]["agent_kind"] is None
    assert rows["Folder Test"]["source"] == "archive"
    assert rows[hosted.agent_name]["source"] == "vapi"

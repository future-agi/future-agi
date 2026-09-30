import uuid

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessScenario
from simulate.services.harness_scenarios import index_scenarios
from simulate.services.hosted_harness import create_selected_harness_run
from simulate.tests.test_harness_amend_archive import NAMES, _key, _run_environment
from simulate.tests.test_hosted_harness_conversation import storage  # noqa: F401

BASE = "/simulate/api/harness-environments"
LANGUAGES = ["English", "Spanish", "Hindi"]


@pytest.fixture
def environment(user, workspace):
    environment = _run_environment(user, workspace, "environment-scenarios")
    index_scenarios(
        environment,
        [
            {
                "name": name,
                "scenario_key": _key(name),
                "use_case": "Bookings",
                "branch": f"branch of {name}",
                "persona": {"name": name, "languages": LANGUAGES, "multilingual": True},
            }
            for name in NAMES[:2]
        ],
    )
    return environment


@pytest.fixture
def client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults["HTTP_X_WORKSPACE_ID"] = str(workspace.id)
    return client


@pytest.mark.django_db
def test_scenarios_are_listed_by_row_id_with_every_language(client, environment):
    response = client.get(f"{BASE}/{environment.id}/scenarios/", {"group_by": ""})

    assert response.status_code == 200, response.content
    body = response.json()
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    assert {one["id"] for one in body["results"]} == {str(row.id) for row in rows}
    assert {one["scenario_key"] for one in body["results"]} == {
        _key(name) for name in NAMES[:2]
    }
    for one in body["results"]:
        assert one["persona"]["languages"] == LANGUAGES
        assert one["persona"]["multilingual"] is True
    assert body["count"] == 2
    assert {"groups", "fields", "scenario_editing", "groupings", "level_labels"} <= set(
        body
    )


@pytest.mark.django_db
def test_the_environment_list_matches_the_older_route(client, environment):
    params = {"group_by": "goal", "ordering": "-number"}
    new = client.get(f"{BASE}/{environment.id}/scenarios/", params).json()
    old = client.get(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/", params
    ).json()

    assert new == old


@pytest.mark.django_db
def test_coverage_is_served_for_the_environment(client, environment):
    response = client.get(f"{BASE}/{environment.id}/scenarios/coverage/")

    assert response.status_code == 200, response.content
    assert {"per_axis", "rows", "columns", "cells", "axes"} <= set(response.json())


@pytest.mark.django_db
def test_one_scenario_is_read_by_its_row_id(client, environment):
    row = HostedHarnessScenario.no_workspace_objects.filter(job=environment).first()

    response = client.get(f"{BASE}/{environment.id}/scenarios/{row.id}/")

    assert response.status_code == 200, response.content
    assert response.json()["id"] == str(row.id)
    assert response.json()["persona"]["languages"] == LANGUAGES


@pytest.mark.django_db
def test_unknown_scenarios_and_run_ids_are_not_found(client, environment):
    row = HostedHarnessScenario.no_workspace_objects.filter(job=environment).first()
    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=[row.scenario_key],
        trials=1,
        idempotency_key="scenario-detail-run",
    )

    assert (
        client.get(f"{BASE}/{environment.id}/scenarios/{uuid.uuid4()}/").status_code
        == 404
    )
    assert client.get(f"{BASE}/{run.id}/scenarios/").status_code == 404
    assert client.get(f"{BASE}/{run.id}/scenarios/{row.id}/").status_code == 404


@pytest.fixture
def editable(environment, storage):  # noqa: F811
    from simulate.models import HostedHarnessJob, HostedHarnessStageOutput
    from simulate.tests.test_harness_amend_archive import _archive

    HostedHarnessStageOutput.no_workspace_objects.create(
        job=environment,
        title="Scenarios",
        summary="2 pre-authored scenarios",
        kind="scenarios",
        data=[
            {"name": name, "scenario_key": _key(name), "tests": "t", "persona": {}}
            for name in NAMES[:2]
        ],
    )
    storage.objects[environment.payload["metadata"]["authoring_object_key"]] = _archive(
        names=NAMES[:2]
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )
    environment.refresh_from_db()
    return environment


def _contract(client, environment):
    return client.get(f"{BASE}/{environment.id}/scenarios/").json()["scenario_editing"]


def _row(environment, name=NAMES[0]):
    return HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=_key(name)
    )


@pytest.mark.django_db
def test_a_direct_edit_keeps_every_language_and_reaches_the_next_run(client, editable):
    languages = _contract(client, editable)["persona_choices"]["languages"][:3]
    before = editable.payload["metadata"]["authoring_object_key"]
    row = _row(editable)

    response = client.patch(
        f"{BASE}/{editable.id}/scenarios/{row.id}/",
        {"persona": {"languages": languages}},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["scenario"]["persona"]["languages"] == languages
    editable.refresh_from_db()
    assert editable.payload["metadata"]["authoring_object_key"] != before
    run, _ = create_selected_harness_run(
        editable,
        scenario_keys=[row.scenario_key],
        trials=1,
        idempotency_key="after-edit",
    )
    edits = run.payload["metadata"]["scenario_edits"][row.scenario_key]
    assert edits["persona"]["languages"] == languages


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"persona": {"languages": "English"}},
        {"persona": {"languages": ["Klingon"]}},
        {"persona": {"name": "Somebody"}},
        {"background_noise": "a jet engine"},
    ],
)
def test_values_outside_the_editing_contract_are_refused(client, editable, body):
    row = _row(editable)

    response = client.patch(
        f"{BASE}/{editable.id}/scenarios/{row.id}/", body, format="json"
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] in {"field_not_editable", "value_not_allowed"}


@pytest.mark.django_db
def test_the_passes_when_line_is_not_a_direct_edit(client, editable):
    row = _row(editable)

    response = client.patch(
        f"{BASE}/{editable.id}/scenarios/{row.id}/",
        {"tests": "anything"},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_a_stale_revision_or_an_unbuilt_environment_is_refused(client, editable):
    from simulate.models import HostedHarnessJob

    row = _row(editable)
    stale = client.patch(
        f"{BASE}/{editable.id}/scenarios/{row.id}/",
        {"keywords": ["refund"], "expected_revision": "sha256:not-current"},
        format="json",
    )
    HostedHarnessJob.no_workspace_objects.filter(id=editable.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    building = client.patch(
        f"{BASE}/{editable.id}/scenarios/{row.id}/",
        {"keywords": ["refund"]},
        format="json",
    )

    assert (stale.status_code, stale.json()["error"]) == (409, "scenario_suite_changed")
    assert (building.status_code, building.json()["error"]) == (
        409,
        "environment_not_ready",
    )


@pytest.mark.django_db
def test_deleting_hides_scenarios_and_keeps_their_rows(client, editable):
    first, second = _row(editable, NAMES[0]), _row(editable, NAMES[1])

    one = client.delete(f"{BASE}/{editable.id}/scenarios/{first.id}/")
    missing = client.post(
        f"{BASE}/{editable.id}/scenarios/delete/",
        {"scenario_ids": [str(second.id), str(uuid.uuid4())]},
        format="json",
    )

    assert one.status_code == 200, one.content
    listed = client.get(f"{BASE}/{editable.id}/scenarios/").json()["results"]
    assert [row["id"] for row in listed] == [str(second.id)]
    assert HostedHarnessScenario.all_objects.get(id=first.id).deleted is True
    assert missing.status_code == 404


@pytest.mark.django_db
def test_changes_address_environments_only(client, editable):
    row = _row(editable)
    run, _ = create_selected_harness_run(
        editable, scenario_keys=[row.scenario_key], trials=1, idempotency_key="run-only"
    )

    response = client.patch(
        f"{BASE}/{run.id}/scenarios/{row.id}/", {"keywords": ["refund"]}, format="json"
    )

    assert response.status_code == 404


@pytest.fixture
def builder(settings, monkeypatch):
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
    scheduled = []
    monkeypatch.setattr(
        "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime",
        lambda conversation_id, base_url: scheduled.append(conversation_id),
    )
    return scheduled


@pytest.mark.django_db
def test_a_revision_is_handed_to_the_builder_naming_the_selected_scenarios(
    client, editable, builder
):
    from simulate.services.hosted_harness_conversation import pending_commands

    row = _row(editable)
    response = client.post(
        f"{BASE}/{editable.id}/scenarios/changes/",
        {
            "kind": "revise",
            "scenario_ids": [str(row.id)],
            "instruction": "the agent must read the booking back before confirming",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="revise-once",
    )
    again = client.post(
        f"{BASE}/{editable.id}/scenarios/changes/",
        {
            "kind": "revise",
            "scenario_ids": [str(row.id)],
            "instruction": "the agent must read the booking back before confirming",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="revise-once",
    )

    assert response.status_code == 202, response.content
    assert again.status_code == 202
    assert len(builder) == 2
    from simulate.models import HostedHarnessConversation

    conversation = HostedHarnessConversation.no_workspace_objects.get(job=editable)
    commands = pending_commands(conversation, after=0)["commands"]
    assert len(commands) == 1
    content = commands[0]["payload"]["content"]
    assert "re-prove" in content and "read the booking back" in content
    assert f"{row.name} ({row.scenario_key})" in content


@pytest.mark.django_db
def test_adding_asks_the_builder_for_that_many_new_scenarios(client, editable, builder):
    from simulate.models import HostedHarnessConversation
    from simulate.services.hosted_harness_conversation import pending_commands

    response = client.post(
        f"{BASE}/{editable.id}/scenarios/changes/",
        {"kind": "add", "count": 3, "instruction": "callers booking for a friend"},
        format="json",
    )

    assert response.status_code == 202, response.content
    conversation = HostedHarnessConversation.no_workspace_objects.get(job=editable)
    content = pending_commands(conversation, after=0)["commands"][0]["payload"]["content"]
    assert content.startswith("Add exactly 3 new scenarios to this suite.")
    assert "Keep every existing scenario as it is" in content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"kind": "revise", "instruction": "change it"},
        {"kind": "revise", "scenario_ids": ["00000000-0000-0000-0000-000000000000"]},
        {"kind": "add"},
    ],
)
def test_an_incomplete_change_is_refused(client, editable, builder, body):
    response = client.post(f"{BASE}/{editable.id}/scenarios/changes/", body, format="json")

    assert response.status_code == 400, response.content
    assert builder == []


@pytest.mark.django_db
def test_changes_wait_for_the_build_and_known_scenarios(client, editable, builder):
    from simulate.models import HostedHarnessJob

    unknown = client.post(
        f"{BASE}/{editable.id}/scenarios/changes/",
        {"kind": "revise", "scenario_ids": [str(uuid.uuid4())], "instruction": "x"},
        format="json",
    )
    HostedHarnessJob.no_workspace_objects.filter(id=editable.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    building = client.post(
        f"{BASE}/{editable.id}/scenarios/changes/", {"kind": "add", "count": 1}, format="json"
    )

    assert (unknown.status_code, unknown.json()["error"]) == (404, "scenario_not_found")
    assert (building.status_code, building.json()["error"]) == (409, "environment_not_ready")
    assert builder == []

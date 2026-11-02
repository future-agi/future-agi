"""An environment's actors, edited in its draft: a saved version is unchanged until the draft is saved."""

import json

import pytest

from simulate.services.harness_versioning import archives
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_rollback import _built

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
MANAGER = {
    "id": "manager",
    "name": "Priya Raman",
    "role": "sales manager who approves discounts above 10%",
    "goal": "Approve only discounts she can justify",
    "constraints": {"may_agree": ["Discounts up to 25%"], "private": ["The quarter's discount budget"]},
    "permitted_actions": ["crm.approve_discount", "crm.reject_discount"],
    "activation": {"trigger": {"kind": "on_tool", "tool": "crm.request_approval"}, "fallback": "decline"},
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _saved_actors(environment):
    environment.refresh_from_db()
    files = archives.read_archive(archives.get_archive(environment.environment_version.archive_object_key))
    raw = files.get("actors.json")
    return json.loads(raw)["actors"] if raw else []


def _add(auth_client, environment, actor, revision=None):
    return auth_client.post(
        f"{BASE}/{environment.id}/actors/",
        {"actor": actor, **({"expected_revision": revision} if revision is not None else {})},
        format="json",
    )


def test_a_manager_added_to_the_draft_reads_back_and_the_saved_version_waits_for_save(
    user, workspace, storage, auth_client, settings
):
    # With an agent runtime, so the save can prepare the environment's bundle.
    environment = _versioned(user, workspace, "actors-done-when", storage)
    saved_before = environment.environment_version_id

    added = _add(auth_client, environment, {**MANAGER, "provenance": {"origin": "harness"}}, revision=0)
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/")
    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/")

    assert added.status_code == 200, added.content
    body = listed.json()
    assert (body["source"], body["revision"]) == ("draft", 1)
    [manager] = body["actors"]
    assert manager["permitted_actions"] == MANAGER["permitted_actions"]
    # Who set it is stamped by the server, whatever the client sent.
    assert manager["provenance"]["origin"] == "editor" and manager["provenance"]["by"] == str(user.id)
    assert view.status_code == 200, view.content
    seen = {one["input"]: one["value"] for one in view.json()["sees"]}
    assert view.json()["permitted_actions"] == MANAGER["permitted_actions"]
    assert seen["actor.constraints"]["private"] == ["The quarter's discount budget"]
    assert "scenario.solution" in view.json()["never_sees"]
    # The saved version is untouched until the draft is saved.
    environment.refresh_from_db()
    assert environment.environment_version_id == saved_before and _saved_actors(environment) == []

    environment_draft = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()["environment"]
    # An environment change goes live through bundle preparation, as every environment edit does.
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    saved = auth_client.post(
        f"{BASE}/{environment.id}/drafts/save/",
        {
            "drafts": [
                {
                    "piece": "environment",
                    "expected_parent_id": environment_draft["parent_id"],
                    "expected_revision": environment_draft["revision"],
                }
            ],
            "idempotency_key": "save-actors",
        },
        format="json",
    )
    assert saved.status_code == 200, saved.content
    assert [one["id"] for one in _saved_actors(environment)] == ["manager"]
    assert environment.environment_version_id != saved_before


def test_edits_are_refused_when_stale_repeated_or_structurally_wrong(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "actors-refused")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200

    stale = _add(auth_client, environment, {**MANAGER, "id": "coworker", "name": "Sam"}, revision=0)
    again = _add(auth_client, environment, MANAGER)
    orphan = _add(auth_client, environment, {"id": "coworker", "name": "Sam", "parent": "nobody"})
    agent = _add(auth_client, environment, {"id": "agent", "name": "Agent"})

    assert (stale.status_code, stale.json()["error"]) == (409, "draft_changed")
    assert (again.status_code, again.json()["error"]) == (409, "actor_exists")
    assert (orphan.status_code, orphan.json()["error"]) == (400, "actor_invalid")
    assert "not another listed actor" in orphan.json()["message"]
    assert (agent.status_code, agent.json()["error"]) == (400, "actor_invalid")
    assert [one["id"] for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]] == ["manager"]


def test_an_actor_is_replaced_and_removed_in_the_draft_and_its_id_never_changes(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "actors-replace")
    _add(auth_client, environment, MANAGER)
    url = f"{BASE}/{environment.id}/actors/manager/"

    renamed = auth_client.put(url, {"actor": {**MANAGER, "id": "boss"}}, format="json")
    replaced = auth_client.put(url, {"actor": {**MANAGER, "goal": "Approve fast"}}, format="json")
    removed = auth_client.delete(url)
    missing = auth_client.delete(url)

    assert (renamed.status_code, renamed.json()["error"]) == (400, "actor_id_mismatch")
    assert replaced.status_code == 200 and replaced.json()["actors"][0]["goal"] == "Approve fast"
    assert removed.status_code == 200 and removed.json()["actors"] == []
    assert (missing.status_code, missing.json()["error"]) == (404, "actor_not_found")


def test_a_view_hides_another_actor_s_private_material(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "actors-view")
    _add(auth_client, environment, MANAGER)
    _add(auth_client, environment, {"id": "coworker", "name": "Sam", "goal": "CANARY-OTHER-GOAL", "prompt": "CANARY-P"})

    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/").json()

    assert "CANARY" not in json.dumps(view["sees"])
    assert {"coworker.goal", "coworker.prompt", "coworker.instruction"} <= set(view["never_sees"])


def test_an_actor_in_a_scenario_cannot_be_removed():
    from simulate.services.harness_actors import _scenarios_using

    suite = {
        "scenarios.json": json.dumps(
            [{"scenario_key": "discount-approved", "actors": [{"id": "manager", "instruction": "Approve it"}]}]
        ).encode()
    }
    assert _scenarios_using(suite, "manager") == ["discount-approved"]
    assert _scenarios_using(suite, "coworker") == []


def test_the_actor_routes_need_versioning(user, workspace, storage, auth_client, settings):
    environment = _built(user, workspace, "actors-off")
    settings.HARNESS_VERSIONING_ENABLED = False

    response = auth_client.get(f"{BASE}/{environment.id}/actors/")

    assert (response.status_code, response.json()["error"]) == (404, "versioning_disabled")


def test_an_actor_a_scenario_includes_is_not_removed(user, workspace, storage, auth_client):
    from unittest.mock import patch

    environment = _built(user, workspace, "actors-in-use")
    _add(auth_client, environment, MANAGER)

    with patch("simulate.services.harness_actors._scenarios_using", return_value=["discount-approved"]):
        refused = auth_client.delete(f"{BASE}/{environment.id}/actors/manager/")

    assert (refused.status_code, refused.json()["error"]) == (409, "actor_in_use")
    assert "discount-approved" in refused.json()["message"]
    assert [one["id"] for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]] == ["manager"]


def test_in_a_scenario_others_see_the_caller_s_situation_and_the_caller_does_not_get_it_twice(
    user, workspace, storage, auth_client
):
    from simulate.tests.test_harness_amend_archive import NAMES, _key

    environment = _built(user, workspace, "actors-scenario-view")
    _add(auth_client, environment, MANAGER)
    _add(auth_client, environment, {"id": "caller", "name": "Caller", "side": "caller", "surface": "call.person"})
    key = _key(NAMES[0])

    manager = auth_client.get(f"{BASE}/{environment.id}/actors/manager/", {"scenario_key": key}).json()
    caller = auth_client.get(f"{BASE}/{environment.id}/actors/caller/", {"scenario_key": key}).json()

    assert "caller.situation" in {one["input"] for one in manager["sees"]}
    assert "caller.situation" not in {one["input"] for one in caller["sees"]}


def test_an_actor_edit_shows_in_review_as_that_actor_added_changed_or_removed(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.drafts import fresh_draft

    environment = _built(user, workspace, "actors-diff")
    _add(auth_client, environment, MANAGER)
    added = fresh_draft(environment, "environment")
    auth_client.put(f"{BASE}/{environment.id}/actors/manager/", {"actor": {**MANAGER, "goal": "Approve fast"}}, format="json")
    changed = fresh_draft(environment, "environment")

    assert added.diff["actors"] == {"added": ["manager"], "removed": [], "changed": []}
    assert "1 actor added" in added.summary
    assert changed.diff["actors"]["added"] == ["manager"] and changed.diff["actors"]["changed"] == []
    # Against the saved version the actor is still new; only a saved actor can read as changed.
    from simulate.services.harness_versioning import hashing

    before = hashing.actor_digests({"actors.json": json.dumps({"actors": [MANAGER]}).encode()})
    after = hashing.actor_digests({"actors.json": json.dumps({"actors": [{**MANAGER, "goal": "Approve fast"}]}).encode()})
    restamped = hashing.actor_digests(
        {"actors.json": json.dumps({"actors": [{**MANAGER, "provenance": {"origin": "chat"}}]}).encode()}
    )
    assert before != after and before == restamped

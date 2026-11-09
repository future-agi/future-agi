"""An environment's actors, edited in its draft: a saved version is unchanged until the draft is saved."""

import json

import pytest

from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_backfill import (
    _legacy,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring
from simulate.tests.test_harness_versioning_rollback import _rows

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
MANAGER = {
    "id": "manager",
    "name": "Priya Raman",
    "role": "sales manager who approves discounts above 10%",
    "goal": "Approve only discounts she can justify",
    "constraints": {
        "may_agree": ["Discounts up to 25%"],
        "private": ["The quarter's discount budget"],
    },
    "permitted_actions": ["crm.approve_discount", "crm.reject_discount"],
    "activation": {
        "trigger": {"kind": "on_tool", "tool": "crm.request_approval"},
        "fallback": "decline",
    },
}


# A world with a CRM service, as a discount-approval environment has: each tool calls a route the world names as an
# action an actor can be given.
CRM_CONTRACT = {
    "agent": "sales",
    "tools": [
        {"name": name, "description": f"{name.replace('_', ' ')} on a deal"}
        for name in ("request_approval", "approve_discount", "reject_discount")
    ],
    "tool_entrypoints": [
        {"tool": name, "service": "tools-api", "endpoint": f"crm/{name}"}
        for name in ("request_approval", "approve_discount", "reject_discount")
    ],
}


def _built(user, workspace, key, contract=CRM_CONTRACT):
    """Built to E1/S1 (two scenarios) by a build that sealed its bundle; `contract=None` is a world whose tools all
    run inside the agent."""
    environment = record_tests._environment(user, workspace, key)
    record_authoring(environment, _authoring(contract=contract), origin="authoring")
    environment.refresh_from_db()
    _rows(environment)
    return environment


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _saved_actors(environment):
    environment.refresh_from_db()
    files = archives.read_archive(
        archives.get_archive(environment.environment_version.archive_object_key)
    )
    raw = files.get("actors.json")
    return json.loads(raw)["actors"] if raw else []


def _add(auth_client, environment, actor, revision=None):
    return auth_client.post(
        f"{BASE}/{environment.id}/actors/",
        {
            "actor": actor,
            **({"expected_revision": revision} if revision is not None else {}),
        },
        format="json",
    )


def test_a_manager_added_to_the_draft_reads_back_and_the_saved_version_waits_for_save(
    user, workspace, storage, auth_client, settings
):
    # With an agent runtime, so the save can prepare the environment's bundle.
    environment = _versioned(user, workspace, "actors-done-when", storage)
    record_authoring(
        environment,
        _authoring(names=NAMES[:1], contract=CRM_CONTRACT),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
    saved_before = environment.environment_version_id

    added = _add(
        auth_client,
        environment,
        {**MANAGER, "provenance": {"origin": "harness"}},
        revision=0,
    )
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/")
    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/")

    assert added.status_code == 200, added.content
    body = listed.json()
    assert (body["source"], body["revision"]) == ("draft", 1)
    [manager] = body["actors"]
    assert manager["permitted_actions"] == MANAGER["permitted_actions"]
    # Who set it is stamped by the server, whatever the client sent.
    assert manager["provenance"]["origin"] == "editor" and manager["provenance"][
        "by"
    ] == str(user.id)
    assert view.status_code == 200, view.content
    seen = {one["input"]: one["value"] for one in view.json()["sees"]}
    assert view.json()["permitted_actions"] == MANAGER["permitted_actions"]
    assert seen["actor.constraints"]["private"] == ["The quarter's discount budget"]
    assert "solution" in view.json()["never_sees"]["scenario_fields"]
    # The saved version is untouched until the draft is saved.
    environment.refresh_from_db()
    assert (
        environment.environment_version_id == saved_before
        and _saved_actors(environment) == []
    )

    environment_draft = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()[
        "environment"
    ]
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


def test_edits_are_refused_when_stale_repeated_or_structurally_wrong(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-refused")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200

    stale = _add(
        auth_client,
        environment,
        {**MANAGER, "id": "coworker", "name": "Sam"},
        revision=0,
    )
    again = _add(auth_client, environment, MANAGER)
    orphan = _add(
        auth_client, environment, {"id": "coworker", "name": "Sam", "parent": "nobody"}
    )
    agent = _add(auth_client, environment, {"id": "agent", "name": "Agent"})
    bad_id = _add(auth_client, environment, {"id": "Bad Id", "name": "X"})

    assert (stale.status_code, stale.json()["error"]) == (409, "draft_changed")
    assert (again.status_code, again.json()["error"]) == (409, "actor_exists")
    assert "is already used by" in again.json()["message"]
    assert (orphan.status_code, orphan.json()["error"]) == (400, "actor_invalid")
    assert "not another listed actor" in orphan.json()["message"]
    assert (agent.status_code, agent.json()["error"]) == (400, "actor_invalid")
    assert (
        "The id 'Bad Id' is not valid; use 1 to 64 lowercase letters, digits or underscores."
        in bad_id.json()["message"]
    )
    assert [
        one["id"]
        for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    ] == ["manager"]


def test_an_actor_is_replaced_and_removed_in_the_draft_and_its_id_never_changes(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-replace")
    _add(auth_client, environment, MANAGER)
    url = f"{BASE}/{environment.id}/actors/manager/"

    renamed = auth_client.put(url, {"actor": {**MANAGER, "id": "boss"}}, format="json")
    replaced = auth_client.put(
        url, {"actor": {**MANAGER, "goal": "Approve fast"}}, format="json"
    )
    # A replace without an id keeps the one in the address; a create without one is refused.
    unnamed = {key: value for key, value in MANAGER.items() if key != "id"}
    kept = auth_client.put(url, {"actor": unnamed}, format="json")
    anonymous = auth_client.post(
        f"{BASE}/{environment.id}/actors/", {"actor": unnamed}, format="json"
    )
    removed = auth_client.delete(url)
    missing = auth_client.delete(url)

    assert (renamed.status_code, renamed.json()["error"]) == (400, "actor_id_mismatch")
    assert (
        replaced.status_code == 200
        and replaced.json()["actors"][0]["goal"] == "Approve fast"
    )
    assert kept.status_code == 200
    assert (anonymous.status_code, anonymous.json()["error"]) == (
        400,
        "actor_id_missing",
    )
    assert removed.status_code == 200 and removed.json()["actors"] == []
    assert (missing.status_code, missing.json()["error"]) == (404, "actor_not_found")
    # Removing the only actor leaves nothing to review: no empty actors.json, so no draft.
    from simulate.services.harness_versioning.drafts import fresh_draft

    assert fresh_draft(environment, "environment") is None


def test_a_view_hides_another_actor_s_private_material(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-view")
    _add(auth_client, environment, MANAGER)
    _add(
        auth_client,
        environment,
        {
            "id": "coworker",
            "name": "Sam",
            "goal": "CANARY-OTHER-GOAL",
            "prompt": "CANARY-P",
        },
    )

    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/").json()

    assert "CANARY" not in json.dumps(view["sees"])
    assert {"Sam.goal", "Sam.prompt", "Sam.instruction"} <= set(
        view["never_sees"]["other_actors"]
    )


def test_the_actor_routes_need_versioning(
    user, workspace, storage, auth_client, settings
):
    environment = _built(user, workspace, "actors-off")
    settings.HARNESS_VERSIONING_ENABLED = False

    response = auth_client.get(f"{BASE}/{environment.id}/actors/")

    assert (response.status_code, response.json()["error"]) == (
        404,
        "versioning_disabled",
    )


@pytest.mark.parametrize("authored", [True, False])
def test_an_environment_built_before_versioning_reads_its_actors(
    user, workspace, storage, auth_client, authored
):
    environment = _legacy(
        user,
        workspace,
        f"actors-legacy-{authored}",
        storage,
        body=_authoring(contract=CRM_CONTRACT),
    )
    if not authored:
        environment.payload["metadata"].pop("authoring_object_key")
        environment.save(update_fields=["payload"])

    response = auth_client.get(f"{BASE}/{environment.id}/actors/")

    assert response.status_code == 200
    # Read from its authored files when it has them, so the world's actions show; nothing when it has none.
    assert bool(response.json()["actions"]) is authored


def test_an_actor_a_scenario_includes_is_not_removed(
    user, workspace, storage, auth_client
):
    from simulate.tests.test_harness_versioning_record import _scenario

    documents = [
        _scenario(NAMES[0], actors=[{"id": "manager", "instruction": "Approve it"}]),
        _scenario(NAMES[1], actors=[{"id": "colleague"}]),
    ]
    environment = record_tests._environment(user, workspace, "actors-in-use")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "scenarios.json": json.dumps(documents),
                **{
                    f"scenarios/{one['name']}/scenario.json": json.dumps(one)
                    for one in documents
                },
            },
        ),
        origin="authoring",
    )
    _add(auth_client, environment, MANAGER)
    _add(
        auth_client,
        environment,
        {
            "id": "colleague",
            "name": "Jordan",
            "role": "colleague",
            "channel": "audio_mix",
        },
    )
    _add(auth_client, environment, {"id": "coworker", "name": "Sam"})

    counts = {
        one["id"]: one["scenarios"]
        for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    }
    manager = auth_client.delete(f"{BASE}/{environment.id}/actors/manager/")
    colleague = auth_client.delete(f"{BASE}/{environment.id}/actors/colleague/")
    coworker = auth_client.delete(f"{BASE}/{environment.id}/actors/coworker/")

    assert counts == {"manager": 1, "colleague": 1, "coworker": 0}
    assert (manager.status_code, manager.json()["error"]) == (409, "actor_in_use")
    assert (
        "Priya Raman is in 1 scenario (book-ride-airport)" in manager.json()["message"]
    )
    assert (colleague.status_code, colleague.json()["error"]) == (409, "actor_in_use")
    assert "cancel-active-ride" in colleague.json()["message"]
    assert coworker.status_code == 200, coworker.content
    assert sorted(one["id"] for one in coworker.json()["actors"]) == [
        "colleague",
        "manager",
    ]


def test_an_actor_counts_every_scenario_it_takes_part_in_however_it_comes_in():
    from simulate.services.harness_actors import _scenarios_using

    documents = [
        {"scenario_key": "asks", "actors": [{"id": "manager"}]},
        {
            "scenario_key": "meeting-ended",
            "starts_with": {"actor": "calendar", "text": "The call ended"},
        },
        {"scenario_key": "thread", "pre_run": [{"actors": ["coworker", "manager"]}]},
        {"scenario_key": "summarize", "starts_with": {"text": "Summarize this thread"}},
    ]

    # The caller is the person in every scenario that a person starts, and in none started by a task or event.
    assert _scenarios_using(documents, "caller") == ["asks", "thread"]
    assert _scenarios_using(documents, "manager") == ["asks", "thread"]
    assert _scenarios_using(documents, "calendar") == ["meeting-ended"]
    assert _scenarios_using(documents, "coworker") == ["thread"]


def test_in_a_scenario_others_see_the_caller_s_situation_and_the_caller_does_not_get_it_twice(
    user, workspace, storage, auth_client
):
    from simulate.tests.test_harness_amend_archive import NAMES, _key

    environment = _built(user, workspace, "actors-scenario-view")
    _add(auth_client, environment, MANAGER)
    _add(
        auth_client,
        environment,
        {"id": "caller", "name": "Caller", "side": "caller", "surface": "call.person"},
    )
    key = _key(NAMES[0])

    manager = auth_client.get(
        f"{BASE}/{environment.id}/actors/manager/", {"scenario_key": key}
    ).json()
    caller = auth_client.get(
        f"{BASE}/{environment.id}/actors/caller/", {"scenario_key": key}
    ).json()

    assert "caller.situation" in {one["input"] for one in manager["sees"]}
    assert "caller.situation" not in {one["input"] for one in caller["sees"]}


def test_an_actor_edit_shows_in_review_as_that_actor_added_changed_or_removed(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.drafts import fresh_draft

    environment = _built(user, workspace, "actors-diff")
    _add(auth_client, environment, MANAGER)
    added = fresh_draft(environment, "environment")
    auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**MANAGER, "goal": "Approve fast"}},
        format="json",
    )
    changed = fresh_draft(environment, "environment")

    assert added.diff["actors"] == {
        "added": [{"id": "manager", "name": MANAGER["name"]}],
        "removed": [],
        "changed": [],
    }
    assert "1 actor added" in added.summary
    # One actor edit is one change: `actors.json` is not counted again as a world file.
    assert "world file" not in added.summary and "actors.json" not in (
        added.diff["files"] or {}
    ).get("changed", [])
    assert (
        changed.diff["actors"]["added"] == [{"id": "manager", "name": MANAGER["name"]}]
        and changed.diff["actors"]["changed"] == []
    )
    # The save bar counts the actor change.
    listed = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()
    listed = listed.get("result", listed)
    assert listed["environment"]["changes"]["actors"] == 1
    # Against the saved version the actor is still new; only a saved actor can read as changed.
    from simulate.services.harness_versioning import hashing

    before = hashing.actor_digests(
        {"actors.json": json.dumps({"actors": [MANAGER]}).encode()}
    )
    after = hashing.actor_digests(
        {
            "actors.json": json.dumps(
                {"actors": [{**MANAGER, "goal": "Approve fast"}]}
            ).encode()
        }
    )
    restamped = hashing.actor_digests(
        {
            "actors.json": json.dumps(
                {"actors": [{**MANAGER, "provenance": {"origin": "chat"}}]}
            ).encode()
        }
    )
    assert before != after and before == restamped


def test_an_actor_who_speaks_takes_the_whole_contract_in_the_one_actor_list(
    user, workspace, storage, auth_client
):
    """TH-8927 done-when with the one actor type: a Manager who joins the call is read back with its permissions and
    its view, and no field is refused because it speaks."""
    from simulate.services.harness_versioning.drafts import edit_sides, fresh_draft

    environment = _built(user, workspace, "actors-actor")
    manager_on_call = {
        **MANAGER,
        "role": "the caller's manager",
        "channel": "audio_handoff",
        "goal": "Approve only discounts she can justify",
        "knowledge": [{"kind": "facts", "text": "The limit is 15%."}],
    }
    colleague = {
        "id": "colleague",
        "name": "Jordan Taylor",
        "role": "the caller's colleague",
        "channel": "audio_mix",
        "loudness_ratio": 0.5,
    }
    added = _add(auth_client, environment, manager_on_call)
    assert added.status_code == 200, added.content
    _add(auth_client, environment, colleague)
    files = edit_sides(environment)["environment"]

    # One file, one shape: no second actor file.
    assert set(files) & {"sub_actors.json", "call_actors.json"} == set()
    stored = {one["id"]: one for one in json.loads(files["actors.json"])["actors"]}
    assert stored["manager"]["permitted_actions"] == MANAGER["permitted_actions"]
    assert stored["manager"]["channel"] == "audio_handoff"
    assert "side" not in stored["colleague"]
    assert fresh_draft(environment, "environment").diff["actors"]["added"] == [
        {"id": "colleague", "name": "Jordan Taylor"},
        {"id": "manager", "name": MANAGER["name"]},
    ]

    # A goal on an actor who speaks is kept, never refused.
    goal = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**colleague, "goal": "Get a bigger discount"}},
        format="json",
    )
    assert goal.status_code == 200, goal.content
    from simulate.tests.test_harness_amend_archive import NAMES, _key

    view = auth_client.get(
        f"{BASE}/{environment.id}/actors/manager/", {"scenario_key": _key(NAMES[0])}
    ).json()
    assert view["permitted_actions"] == MANAGER["permitted_actions"]
    assert view["played_from_view"] is True and view["unused_reason"] is None
    # Named by name, apart from the grader's material.
    assert "Jordan Taylor.goal" in view["never_sees"]["other_actors"]
    assert "solution" in view["never_sees"]["scenario_fields"]
    seen = {one["input"] for one in view["sees"]}
    assert {"actor.goal", "actor.knowledge", "caller.situation"} <= seen

    removed = auth_client.delete(f"{BASE}/{environment.id}/actors/colleague/")
    assert removed.status_code == 200, removed.content
    assert [one["id"] for one in removed.json()["actors"]] == ["manager"]


def test_an_actor_who_only_acts_in_the_world_is_told_its_view_is_not_played(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-world-only")
    _add(auth_client, environment, MANAGER)
    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/").json()
    assert view["played_from_view"] is False and view["unused_reason"]
    auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**MANAGER, "play": "live"}},
        format="json",
    )
    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/").json()
    assert view["played_from_view"] is True and view["unused_reason"] is None


def test_the_checks_and_the_view_follow_the_rules_the_environment_was_sealed_with(
    user, workspace, storage, auth_client
):
    from simulate.services import harness_actors
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests import test_harness_versioning_record as record_tests
    from simulate.tests.test_harness_versioning_record import _authoring

    rules = json.loads(harness_actors._RULES_BEFORE_DECLARED.read_text())
    rules["evaluator_only"]["scenario_fields"].append("rubric")
    rules["records"]["actors.json"]["properties"]["mood"] = {"type": "string"}
    rules["records"]["actors.json"]["properties"]["loudness_ratio"] = {
        "type": "number",
        "maximum": 0.5,
    }
    later = record_tests._environment(user, workspace, "actors-later-release")
    record_authoring(
        later,
        _authoring(contract=CRM_CONTRACT, **{"actor_rules.json": json.dumps(rules)}),
        origin="authoring",
    )
    earlier = _built(user, workspace, "actors-earlier-release")

    added = _add(auth_client, later, {**MANAGER, "mood": "patient"})
    loud = _add(
        auth_client, later, {"id": "coworker", "name": "Sam", "loudness_ratio": 0.8}
    )
    view = auth_client.get(f"{BASE}/{later.id}/actors/manager/").json()
    listed = auth_client.get(f"{BASE}/{later.id}/actors/").json()

    # A field the sealing release declares is kept, its limits apply, and what it hides is never seen.
    assert added.status_code == 200, added.content
    assert (
        listed["actors"][0]["mood"] == "patient"
        and "mood" in listed["record_schemas"]["actors.json"]["properties"]
    )
    assert (loud.status_code, loud.json()["error"]) == (400, "actor_invalid")
    assert (
        "coworker's loudness_ratio: 0.8 is greater than the maximum of 0.5."
        in loud.json()["message"]
    )
    assert "rubric" in view["never_sees"]["scenario_fields"]
    # A bundle sealed before any release declared its rules is checked by the first declared ones.
    refused = _add(auth_client, earlier, {**MANAGER, "mood": "patient"})
    assert (refused.status_code, refused.json()["error"]) == (400, "actor_invalid")
    assert "manager has no mood in actors.json" in refused.json()["message"]
    # An optional field's limit is named, not "not valid under any of the given schemas".
    too_loud = _add(
        auth_client, earlier, {"id": "coworker", "name": "Sam", "loudness_ratio": 5}
    )
    assert (
        "coworker's loudness_ratio: 5 is greater than the maximum of 1."
        in too_loud.json()["message"]
    )
    assert _add(auth_client, earlier, MANAGER).status_code == 200
    earlier_view = auth_client.get(f"{BASE}/{earlier.id}/actors/manager/").json()
    assert "solution" in earlier_view["never_sees"]["scenario_fields"]
    assert "rubric" not in earlier_view["never_sees"]["scenario_fields"]


def test_an_actor_is_given_only_actions_the_world_has_and_is_told_which(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests import test_harness_versioning_record as record_tests
    from simulate.tests.test_harness_versioning_record import _authoring

    contract = {
        "agent": "sales",
        "tools": [
            {
                "name": "request_approval",
                "description": "Ask a manager to approve a discount",
            },
            {"name": "approve_discount", "description": "Approve a pending discount"},
            {"name": "read_notes", "description": "Read the agent's own notes"},
        ],
        "tool_entrypoints": [
            {"tool": "request_approval", "endpoint": "crm/request_approval"},
            {"tool": "approve_discount", "endpoint": "/crm/approve_discount/"},
            {"tool": "read_notes", "mode": "import", "endpoint": ""},
        ],
    }
    world = record_tests._environment(user, workspace, "actors-world-actions")
    record_authoring(world, _authoring(contract=contract), origin="authoring")
    in_process = _built(user, workspace, "actors-no-world-actions", contract=None)

    listed = auth_client.get(f"{BASE}/{world.id}/actors/").json()
    listed = listed.get("result", listed)
    manager = {
        "id": "manager",
        "name": "Priya",
        "permitted_actions": ["crm.approve_discount"],
        "activation": {"trigger": {"kind": "on_tool", "tool": "crm.request_approval"}},
    }
    added = _add(auth_client, world, manager)
    wrong = auth_client.put(
        f"{BASE}/{world.id}/actors/manager/",
        {"actor": {**manager, "permitted_actions": ["refunds.approve"]}},
        format="json",
    )
    nowhere = _add(auth_client, in_process, {**manager, "activation": None})

    # A tool with no route runs inside the agent and is no world action.
    assert listed["actions"] == [
        {
            "action": "crm.approve_discount",
            "tool": "approve_discount",
            "description": "Approve a pending discount",
        },
        {
            "action": "crm.request_approval",
            "tool": "request_approval",
            "description": "Ask a manager to approve a discount",
        },
    ]
    assert set(listed["channels"]) == {
        "audio_mix",
        "audio_handoff",
        "ivr",
        "chat_message",
    }
    assert added.status_code == 200, added.content
    assert (wrong.status_code, wrong.json()["error"]) == (400, "actor_invalid")
    assert (
        "refunds.approve is not an action of this environment; choose from crm.approve_discount, crm.request_approval."
        in wrong.json()["message"]
    )
    assert (
        "this environment has no world actions, because its tools run inside the agent"
        in nowhere.json()["message"]
    )


def test_values_written_elsewhere_do_not_block_an_edit_and_the_scenario_count_is_never_stored(
    user, workspace, storage, auth_client
):
    from simulate.services import harness_actors
    from simulate.services.harness_versioning.drafts import edit_sides

    # As the builder chat might have written it: actions a world whose tools run inside the agent does not have.
    in_process = _built(user, workspace, "actors-kept-values-world", contract=None)
    files = edit_sides(in_process)["environment"]
    rules = harness_actors.actor_rules(files)
    renamed = {**MANAGER, "name": "Priya R."}
    assert harness_actors._new_choices(MANAGER, renamed, files, rules) == []
    assert harness_actors._new_choices(None, MANAGER, files, rules) != []

    environment = _built(user, workspace, "actors-kept-values")
    call_actor = {
        "id": "colleague",
        "name": "Jordan",
        "role": "colleague",
        "channel": "audio_mix",
    }
    added = _add(auth_client, environment, call_actor)
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    listed = listed.get("result", listed)
    [colleague] = [one for one in listed["actors"] if one["id"] == "colleague"]
    resent = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": colleague},
        format="json",
    )
    bad_channel = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**call_actor, "channel": "smoke_signal"}},
        format="json",
    )

    assert added.status_code == 200, added.content
    assert colleague["scenarios"] == 0
    assert resent.status_code == 200, resent.content
    stored = json.loads(edit_sides(environment)["environment"]["actors.json"])["actors"]
    assert "scenarios" not in stored[0]
    assert (bad_channel.status_code, bad_channel.json()["error"]) == (
        400,
        "actor_invalid",
    )
    assert (
        "colleague's channel: 'smoke_signal' is not one of: audio_handoff, audio_mix, chat_message, ivr."
        in bad_channel.json()["message"]
    )


def test_an_actor_a_version_saved_before_actor_digests_already_had_reads_as_changed_not_added(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.drafts import fresh_draft

    colleague = {"id": "colleague", "name": "Jordan", "channel": "audio_mix"}
    environment = record_tests._environment(user, workspace, "actors-old-version")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{"actors.json": json.dumps({"actors": [colleague]})},
        ),
        origin="authoring",
    )
    environment.refresh_from_db()
    # As every version saved before actor digests were recorded.
    version = environment.environment_version
    version.documents = {
        key: value
        for key, value in (version.documents or {}).items()
        if key != "actor_digests"
    }
    version.save(update_fields=["documents"])

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**colleague, "role": "the caller's colleague"}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    draft = fresh_draft(environment, "environment")
    assert draft.diff["actors"] == {
        "added": [],
        "changed": [{"id": "colleague", "name": "Jordan"}],
        "removed": [],
    }
    assert draft.summary == "1 actor changed"


def test_a_call_actor_never_takes_the_name_of_a_caller_it_is_with():
    from simulate.services import harness_actors

    rules = json.loads(harness_actors._RULES_BEFORE_DECLARED.read_text())
    suite = {
        "scenarios/one/scenario.json": json.dumps(
            {
                "scenario_key": "one",
                "persona": {"name": "Mei Lin"},
                "actors": [{"id": "colleague"}],
            }
        ).encode(),
        "scenarios/two/scenario.json": json.dumps(
            {"scenario_key": "two", "persona": {"name": "Jordan Taylor"}}
        ).encode(),
    }
    colleague = {"id": "colleague", "name": "Jordan Taylor", "channel": "audio_mix"}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "simulate.services.harness_actors.suite_documents",
            lambda files: [json.loads(raw) for raw in files.values()],
        )
        # The caller of a scenario that does not include the actor may share its name.
        assert harness_actors._new_choices(None, colleague, {}, rules, suite) == []
        renamed = {**colleague, "name": "mei lin"}
        problems = harness_actors._new_choices(colleague, renamed, {}, rules, suite)
    assert problems == [
        "colleague's name is the caller's in one (Mei Lin); choose another."
    ]


def test_world_actions_keep_only_routes_an_actor_can_send_to_when_the_rules_say_how():
    from simulate.services.harness_actors import world_actions

    contract = {
        "tool_entrypoints": [
            {"tool": "approve", "endpoint": "crm/approve"},
            {"tool": "read", "endpoint": "crm/read", "method": "get"},
            {"tool": "cancel", "endpoint": "orders/{order_id}/cancel"},
        ]
    }
    files = {"contract.json": json.dumps(contract).encode()}
    naming = {
        "from": "tool_entrypoints",
        "route": "endpoint",
        "tool": "tool",
        "separator": ".",
    }
    sealed = {**naming, "method": "method", "methods": ["POST"], "placeholder": "{"}

    def names(rules):
        return [one["action"] for one in world_actions(files, {"world_actions": rules})]

    assert names(sealed) == ["crm.approve"]
    # Rules sealed before methods and placeholders were named keep every route.
    assert names(naming) == ["crm.approve", "crm.read", "orders.{order_id}.cancel"]


def test_record_changes_on_the_world_s_own_collections_are_actions_when_the_release_seals_the_rule():
    from simulate.services.harness_actors import world_actions

    files = {
        "contract.json": json.dumps(
            {"tool_entrypoints": [{"tool": "approve", "endpoint": "crm/approve"}]}
        ).encode(),
        "generic-harness/source-model.json": json.dumps(
            {
                "tables": [
                    {"name": "approvals"},
                    {"name": "harness_seed_sentinel"},
                    {"name": "_alk_tool_trace"},
                ]
            }
        ).encode(),
    }
    naming = {
        "from": "tool_entrypoints",
        "route": "endpoint",
        "tool": "tool",
        "separator": ".",
    }
    changes = {
        "from": "generic-harness/source-model.json",
        "tables": "tables",
        "name": "name",
        "verbs": ["put", "change"],
        "never_prefix": "_alk_",
        "never": ["harness_seed_sentinel"],
    }
    sealed = world_actions(files, {"world_actions": naming, "record_changes": changes})
    assert [one["action"] for one in sealed] == [
        "approvals.change",
        "approvals.put",
        "crm.approve",
    ]
    # A release that seals no such rule offers its routes alone.
    assert [
        one["action"] for one in world_actions(files, {"world_actions": naming})
    ] == ["crm.approve"]


def test_a_fallback_may_be_one_of_the_actor_s_own_actions_but_not_someone_else_s():
    from simulate.services.harness_actors import _broken_references

    rules = {"references": {"activation.fallback": ["silent", "decline"]}}
    dana = {
        "id": "dana",
        "name": "Dana",
        "permitted_actions": ["approvals.change"],
        "activation": {"fallback": "approvals.change"},
    }
    assert _broken_references(dana, {"dana"}, rules) == []
    stranger = {**dana, "activation": {"fallback": "orders.cancel"}}
    assert _broken_references(stranger, {"dana"}, rules) == [
        ("activation.fallback", "orders.cancel")
    ]
    # A release that declares no such field keeps every fallback to another actor.
    assert _broken_references(dana, {"dana"}, {**rules, "own_actions": []}) == [
        ("activation.fallback", "approvals.change")
    ]


def test_every_actor_has_an_id_its_routes_can_reach_and_a_name(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-id-rule")
    slash = _add(
        auth_client, environment, {"id": "a/b", "name": "", "channel": "audio_mix"}
    )
    long = _add(
        auth_client,
        environment,
        {"id": "a" * 100, "name": "Jordan", "channel": "audio_mix"},
    )
    unnamed = _add(auth_client, environment, {"id": "sam", "name": ""})

    assert (slash.status_code, slash.json()["error"]) == (400, "actor_invalid")
    assert (
        "The id 'a/b' is not valid; use 1 to 64 lowercase letters, digits or underscores."
        in slash.json()["message"]
    )
    assert "a/b's name: it cannot be empty." in slash.json()["message"]
    assert (long.status_code, long.json()["error"]) == (400, "actor_invalid")
    assert "The id 'aaaa" in long.json()["message"]
    assert (unnamed.status_code, unnamed.json()["error"]) == (400, "actor_invalid")
    assert "sam's name: it cannot be empty." in unnamed.json()["message"]
    assert auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"] == []


def test_an_actor_s_fields_are_bounded_on_the_server(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-bounded")
    long_name = _add(auth_client, environment, {**MANAGER, "name": "N" * 81})
    long_role = _add(
        auth_client,
        environment,
        {
            "id": "colleague",
            "name": "Jordan",
            "role": "R" * 161,
            "channel": "audio_mix",
        },
    )
    huge = _add(auth_client, environment, {**MANAGER, "goal": "G" * 70_000})
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()

    assert "manager's name: use at most 80 characters." in long_name.json()["message"]
    assert (
        "colleague's role: use at most 160 characters." in long_role.json()["message"]
    )
    assert huge.status_code == 400
    assert "Keep an actor under 65536 characters." in huge.content.decode()
    # The editor reads its limits from the schemas the cast is sent with.
    for file in ("actors.json", "actors.json"):
        properties = listed["record_schemas"][file]["properties"]
        assert (properties["name"]["maxLength"], properties["role"]["maxLength"]) == (
            80,
            160,
        )
    assert listed["actors"] == []


def test_a_bad_actor_written_elsewhere_never_blocks_an_edit_to_another(
    user, workspace, storage, auth_client
):
    environment = record_tests._environment(user, workspace, "actors-bad-elsewhere")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{"actors.json": json.dumps({"actors": [{"id": "Old Id", "name": ""}]})},
        ),
        origin="authoring",
    )

    added = _add(auth_client, environment, MANAGER)
    edited_bad = auth_client.put(
        f"{BASE}/{environment.id}/actors/Old Id/",
        {"actor": {"name": ""}},
        format="json",
    )

    assert added.status_code == 200, added.content
    assert [one["id"] for one in added.json()["actors"]] == ["Old Id", "manager"]
    # Its id is kept as it is; only the empty name it was sent with is refused.
    assert (edited_bad.status_code, edited_bad.json()["error"]) == (
        400,
        "actor_invalid",
    )
    assert edited_bad.json()["message"] == "Old Id's name: it cannot be empty."
    renamed = auth_client.put(
        f"{BASE}/{environment.id}/actors/Old Id/",
        {"actor": {"name": "Pat"}},
        format="json",
    )
    assert renamed.status_code == 200, renamed.content


@pytest.mark.parametrize(
    "actors_json",
    [
        "{not json",
        json.dumps({"actors": ["manager"]}),
        json.dumps({"actors": "manager"}),
    ],
)
def test_an_unreadable_actors_file_is_a_plain_conflict(
    actors_json, user, workspace, storage, auth_client
):
    environment = record_tests._environment(user, workspace, "actors-unreadable")
    record_authoring(
        environment,
        _authoring(contract=CRM_CONTRACT, **{"actors.json": actors_json}),
        origin="authoring",
    )

    listed = auth_client.get(f"{BASE}/{environment.id}/actors/")
    added = _add(auth_client, environment, MANAGER)

    for response in (listed, added):
        assert (response.status_code, response.json()["error"]) == (
            409,
            "actors_unreadable",
        )
        assert response.json()["message"] == (
            "This environment's actors.json cannot be read; ask the builder chat to fix it"
        )


def test_an_environment_in_another_workspace_has_no_actors_here(
    user, workspace, storage, auth_client
):
    from accounts.models.workspace import Workspace

    other = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    environment = _built(user, other, "actors-other-workspace")
    url = f"{BASE}/{environment.id}/actors/"

    responses = [
        auth_client.get(url),
        _add(auth_client, environment, MANAGER),
        auth_client.get(f"{url}manager/"),
        auth_client.put(f"{url}manager/", {"actor": MANAGER}, format="json"),
        auth_client.delete(f"{url}manager/"),
    ]

    assert [one.status_code for one in responses] == [404] * 5
    assert fresh_draft_of(environment) is None


def fresh_draft_of(environment):
    from simulate.services.harness_versioning.drafts import fresh_draft

    return fresh_draft(environment, "environment")


def test_unknown_actors_and_scenarios_and_a_stale_removal_are_refused(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-unknown")
    _add(auth_client, environment, MANAGER)
    url = f"{BASE}/{environment.id}/actors/"

    unknown = auth_client.get(f"{url}nobody/")
    no_scenario = auth_client.get(
        f"{url}manager/", {"scenario_key": "no-such-scenario"}
    )
    stale = auth_client.delete(f"{url}manager/?expected_revision=0")

    assert (unknown.status_code, unknown.json()["error"]) == (404, "actor_not_found")
    assert (no_scenario.status_code, no_scenario.json()["error"]) == (
        404,
        "scenario_not_found",
    )
    assert (stale.status_code, stale.json()["error"]) == (409, "draft_changed")
    assert [one["id"] for one in auth_client.get(url).json()["actors"]] == ["manager"]


def test_a_cycle_of_parents_and_a_removal_that_leaves_a_parent_dangling_are_refused(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-cycle")
    url = f"{BASE}/{environment.id}/actors/"
    _add(auth_client, environment, {"id": "boss", "name": "Ana"})
    _add(auth_client, environment, {"id": "lead", "name": "Bo", "parent": "boss"})

    cycle = auth_client.put(
        f"{url}boss/", {"actor": {"name": "Ana", "parent": "lead"}}, format="json"
    )
    dangling = auth_client.delete(f"{url}boss/")

    assert (cycle.status_code, cycle.json()["error"]) == (400, "actor_invalid")
    assert "boss is in a cycle of parent links; break it." in cycle.json()["message"]
    assert (dangling.status_code, dangling.json()["error"]) == (400, "actor_invalid")
    assert "lead's parent is boss; change it first." in dangling.json()["message"]


@pytest.mark.parametrize("failure", ["gone", "unreadable", "invalid_actors_json"])
def test_an_unknown_cast_on_either_side_shows_no_actor_diff_rather_than_all_added_or_removed(
    failure, user, workspace, storage
):
    from unittest.mock import patch

    from minio.error import S3Error

    from simulate.services.harness_versioning import hashing, versions

    environment = _built(user, workspace, f"actors-unknown-{failure}")
    base = environment.environment_version
    known = {"manager": "digest-1", "colleague": "digest-2"}
    # As a version saved before actor digests were recorded.
    base.documents = {
        k: v for k, v in (base.documents or {}).items() if k != "actor_digests"
    }
    raised = {
        "gone": {
            "side_effect": S3Error("NoSuchKey", "gone", "key", "request", "host", None)
        },
        "unreadable": {"return_value": b"not an archive"},
    }.get(failure)
    if failure == "invalid_actors_json":
        base.documents["actor_digests"] = hashing.actor_digests(
            {"actors.json": b"{not json"}
        )

    with patch.object(
        versions.archives,
        "get_archive",
        **(raised or {"wraps": versions.archives.get_archive}),
    ):
        diff, summary = versions.environment_change(base, {}, {"actor_digests": known})
    # And the other way: a cast that cannot be read now never reads as every actor removed.
    later, later_summary = versions.environment_change(
        type(base)(documents={"actor_digests": known}),
        {},
        {"actor_digests": hashing.actor_digests({"actors.json": b"{not json"})},
    )

    assert diff["actors"] is None and "actor" not in summary
    assert later["actors"] is None and "actor" not in later_summary


def test_any_actor_can_start_or_stop_speaking_unless_a_scenario_includes_it_as_it_joins_now(
    user, workspace, storage, auth_client
):
    from simulate.tests.test_harness_versioning_record import _scenario

    documents = [_scenario(NAMES[0], actors=[{"id": "colleague"}])]
    environment = record_tests._environment(user, workspace, "actors-joins")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "scenarios.json": json.dumps(documents),
                **{
                    f"scenarios/{one['name']}/scenario.json": json.dumps(one)
                    for one in documents
                },
            },
        ),
        origin="authoring",
    )
    colleague = {
        "id": "colleague",
        "name": "Jordan",
        "role": "colleague",
        "channel": "audio_mix",
    }
    _add(auth_client, environment, colleague)
    _add(auth_client, environment, {"id": "coworker", "name": "Sam"})
    url = f"{BASE}/{environment.id}/actors/"

    onto_call = auth_client.put(
        f"{url}coworker/",
        {"actor": {"name": "Sam", "role": "a coworker", "channel": "audio_mix"}},
        format="json",
    )
    moved = auth_client.put(
        f"{url}colleague/",
        {"actor": {**colleague, "channel": "audio_handoff"}},
        format="json",
    )

    # One actor type: a world actor starts speaking with no remove and re-add.
    assert onto_call.status_code == 200, onto_call.content
    # A scenario's trigger and level were written for how the actor joins it now.
    assert (moved.status_code, moved.json()["error"]) == (409, "actor_in_use")
    cast = {one["id"]: one for one in auth_client.get(url).json()["actors"]}
    assert cast["coworker"]["channel"] == "audio_mix"
    assert cast["colleague"]["channel"] == "audio_mix"


def test_an_edit_landing_while_another_reads_the_draft_is_never_overwritten(
    user, workspace, storage
):
    from unittest.mock import patch

    from simulate.services import harness_actors
    from simulate.services.hosted_harness import HostedHarnessError

    environment = _built(user, workspace, "actors-race")
    harness_actors.write_actor_edit(
        environment,
        op="create",
        actor_id="manager",
        body=MANAGER,
        user=user,
        expected_revision=None,
    )
    real = harness_actors.drafts.edit_sides
    raced = []

    def edit_lands_meanwhile(env):
        sides = real(env)
        if not raced:
            raced.append(True)
            harness_actors.write_actor_edit(
                env,
                op="create",
                actor_id="coworker",
                body={"name": "Sam"},
                user=user,
                expected_revision=None,
            )
        return sides

    with patch.object(
        harness_actors.drafts, "edit_sides", side_effect=edit_lands_meanwhile
    ):
        with pytest.raises(HostedHarnessError) as refused:
            harness_actors.write_actor_edit(
                environment,
                op="replace",
                actor_id="manager",
                body={**MANAGER, "goal": "Approve fast"},
                user=user,
                expected_revision=None,
            )

    assert refused.value.code == "draft_changed"
    assert [one["id"] for one in harness_actors.read_actors(environment)["actors"]] == [
        "manager",
        "coworker",
    ]


def test_an_edit_stamps_only_the_fields_it_changed(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.drafts import edit_sides

    chat = {"origin": "chat", "fields": {"name": "chat", "goal": "chat"}}
    environment = record_tests._environment(user, workspace, "actors-stamped")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "actors.json": json.dumps(
                    {
                        "actors": [
                            {
                                "id": "manager",
                                "name": "Priya",
                                "goal": "Old",
                                "provenance": chat,
                            }
                        ]
                    }
                )
            },
        ),
        origin="authoring",
    )

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {"name": "Priya", "goal": "New", "provenance": {"origin": "client"}}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    [stored] = json.loads(edit_sides(environment)["environment"]["actors.json"])[
        "actors"
    ]
    assert stored["provenance"]["origin"] == "editor"
    assert stored["provenance"]["fields"] == {"name": "chat", "goal": "editor"}


def test_a_world_actor_s_actions_come_from_the_transcript_and_never_join_the_agent_s_calls(
    organization, workspace
):
    from datetime import UTC, datetime

    from simulate.models import CallExecution
    from simulate.services.hosted_harness_ingestion import WORLD_ACTIONS_KEY
    from simulate.services.run_results_v3 import function_calls
    from simulate.tests.test_harness_call_actors import _ingest

    at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC).timestamp()
    approve = {
        "name": "crm.approve_discount",
        "arguments": {"deal": 7},
        "ok": False,
        "error": "not permitted",
        "refused": True,
        "at": at + 3,
        "action_id": "a-1",
        "actor_id": "manager",
        "actor_name": "Priya",
        "outcome": "refused",
    }
    metadata = {WORLD_ACTIONS_KEY: [{"stale": True}]}
    call = _ingest(
        organization,
        workspace,
        "actors-world-actions-transcript",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "Hello",
                    "started_speaking_at": at + 1,
                    "stopped_speaking_at": at + 2,
                }
            ],
            "world_actors": {"actions": [approve, "not a row"]},
        },
        metadata,
    )
    call.call_metadata = metadata
    call.provider_call_data = {
        "livekit": {
            "tool_calls": [
                {"name": "lookup", "action_id": "a-0", "outcome": "committed"}
            ]
        }
    }

    [stored] = metadata[WORLD_ACTIONS_KEY]
    calls = function_calls(call)

    assert stored["start_time_ms"] == 3000 and stored["actor_id"] == "manager"
    assert [
        one["name"] for one in call.provider_call_data["livekit"]["tool_calls"]
    ] == ["lookup"]
    assert [(one["name"], one.get("actor_id")) for one in calls] == [
        ("lookup", None),
        ("crm.approve_discount", "manager"),
    ]


def test_a_call_stored_with_actor_rows_in_its_tool_calls_still_lists_them():
    from simulate.services.run_results_v3 import function_calls

    row = {
        "name": "crm.approve_discount",
        "actor_id": "manager",
        "actor_name": "Priya",
        "refused": True,
    }
    call = type(
        "Call",
        (),
        {
            "provider_call_data": {"livekit": {"tool_calls": [row]}},
            "call_metadata": None,
        },
    )()

    assert function_calls(call) == [row]


def test_a_call_actor_follows_the_sealed_persona_role_and_channel_rules(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-sealed-persona")
    chat = record_tests._environment(user, workspace, "actors-sealed-chat")
    record_authoring(
        chat,
        _authoring(contract={**CRM_CONTRACT, "modality": "chat"}),
        origin="authoring",
    )
    colleague = {
        "id": "colleague",
        "name": "Jordan",
        "role": "the caller's colleague",
        "channel": "audio_mix",
    }

    bad_persona = _add(
        auth_client, environment, {**colleague, "persona": {"age_group": 30}}
    )
    other_name = _add(
        auth_client, environment, {**colleague, "persona": {"name": "Sam"}}
    )
    no_role = _add(auth_client, environment, {**colleague, "role": ""})
    on_chat = _add(auth_client, chat, colleague)

    assert (bad_persona.status_code, bad_persona.json()["error"]) == (
        400,
        "actor_invalid",
    )
    assert "colleague's persona.age_group:" in bad_persona.json()["message"]
    assert (
        "colleague's persona.name is 'Sam' but its name is 'Jordan'; make them the same."
        in other_name.json()["message"]
    )
    assert "colleague's role: it cannot be empty." in no_role.json()["message"]
    assert (
        "colleague cannot reach a chat agent through audio_mix; choose another channel."
        in on_chat.json()["message"]
    )
    assert (
        _add(
            auth_client,
            environment,
            {**colleague, "persona": {"name": "Jordan", "age_group": "adult"}},
        ).status_code
        == 200
    )


def test_a_call_keeps_its_trial_s_state_changes_and_actor_decisions_and_serves_them(
    organization, workspace
):
    from simulate.models import CallExecution
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.tests.test_harness_call_actors import _ingest
    from simulate.views.run_results_v3 import TrialStateSerializer

    changes = [
        {
            "who": "agent",
            "name": "the agent",
            "attempted": 2,
            "committed": 1,
            "refused": 1,
            "faulted": 0,
            "duplicate": 0,
            "committed_actions": ["deals.request_approval"],
        },
        {
            "who": "manager",
            "name": "Mia",
            "attempted": 1,
            "committed": 1,
            "refused": 0,
            "faulted": 0,
            "duplicate": 0,
            "committed_actions": ["deals.approve"],
        },
    ]
    decisions = [
        {
            "actor": "manager",
            "play": "live",
            "action": "deals.approve",
            "why": "12% is within my limit",
            "status": 200,
        },
        {
            "actor": "lead",
            "play": "waiting",
            "timeout_seconds": 30.0,
            "fallback": "decline",
            "parent": "a1",
        },
        {
            "actor": "lead",
            "play": "timeout",
            "fallback": "decline",
            "outcome": "declined",
            "timeout_seconds": 30.0,
        },
    ]
    metadata = {TRIAL_STATE_KEY: {"stale": True}}
    _ingest(
        organization,
        workspace,
        "actors-trial-state",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [],
            "world_actors": {
                "actions": [],
                "state_changes": changes,
                "decisions": [*decisions, "x"],
            },
        },
        metadata,
    )
    assert metadata[TRIAL_STATE_KEY] == {
        "state_changes": changes,
        "decisions": decisions,
    }
    assert TrialStateSerializer(data=metadata[TRIAL_STATE_KEY]).is_valid()

    cleared = {TRIAL_STATE_KEY: {"stale": True}}
    _ingest(
        organization,
        workspace,
        "actors-trial-state-none",
        CallExecution.SimulationCallType.VOICE,
        {"messages": []},
        cleared,
    )
    assert TRIAL_STATE_KEY not in cleared


def test_a_fault_that_did_not_take_effect_is_kept_on_the_trial_for_the_person(
    organization, workspace
):
    from simulate.models import CallExecution
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.tests.test_harness_call_actors import _ingest
    from simulate.views.run_results_v3 import TrialStateSerializer

    notice = {
        "code": "fault_not_applied",
        "message": "The timeout fault on call 1 of crm.apply_discount could not be applied: the agent made that call "
        "outside the world's door (in its own process), so nothing held it.",
        "action": "crm.apply_discount",
        "on_call": 1,
        "mode": "timeout",
        "internal": "dropped",
    }
    metadata: dict = {}
    _ingest(
        organization,
        workspace,
        "fault-notice",
        CallExecution.SimulationCallType.TEXT,
        {"messages": [], "notices": [notice, "x", {"code": "no_message"}]},
        metadata,
    )
    expected = {key: value for key, value in notice.items() if key != "internal"}
    assert metadata[TRIAL_STATE_KEY] == {"notices": [expected]}
    assert TrialStateSerializer(data=metadata[TRIAL_STATE_KEY]).is_valid()


def test_an_actor_is_offered_and_given_only_the_environment_s_own_files_and_records(
    user, workspace, storage, auth_client
):
    from simulate.models import HostedHarnessStageOutput

    environment = record_tests._environment(user, workspace, "actors-knowledge")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "docs/discount-policy.md": "Up to 15% needs the manager.",
                "sub_goals.json": "{}",
            },
        ),
        origin="authoring",
    )
    environment.refresh_from_db()
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=environment,
        title="Stores",
        summary="",
        kind="stores",
        data=[
            {
                "capability": "crm",
                "engine": "postgres",
                "tables": [{"name": "deals", "rows": 3}],
            }
        ],
    )
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    sources = listed["knowledge_sources"]
    assert "docs/discount-policy.md" in sources["files"]
    assert (
        "sub_goals.json" not in sources["files"]
        and "contract.json" not in sources["files"]
    )
    assert sources["records"] == [{"store": "crm", "collection": "deals"}]

    reads = {
        **MANAGER,
        "knowledge": [
            {"kind": "files", "paths": ["docs/discount-policy.md"]},
            {"kind": "records", "store": "crm", "collection": "deals"},
        ],
    }
    assert _add(auth_client, environment, reads).status_code == 200
    for bad, said in (
        (
            {"kind": "files", "paths": ["sub_goals.json"]},
            "may never read sub_goals.json",
        ),
        (
            {"kind": "files", "paths": ["docs/missing.md"]},
            "docs/missing.md is not a file of this environment",
        ),
        (
            {"kind": "records", "store": "crm", "collection": "salaries"},
            "crm.salaries is not a record collection",
        ),
    ):
        refused = auth_client.put(
            f"{BASE}/{environment.id}/actors/manager/",
            {"actor": {**reads, "knowledge": [*reads["knowledge"], bad]}},
            format="json",
        )
        assert (
            refused.status_code == 400 and said in refused.json()["message"]
        ), refused.content


def test_giving_an_actor_an_action_only_one_other_actor_holds_shows_the_conflict_before_saving(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-permission-conflict")
    customer = {
        "id": "customer",
        "name": "Lena Ortiz",
        "permitted_actions": ["crm.request_approval"],
    }
    assert _add(auth_client, environment, customer).status_code == 200
    manager = {
        "id": "manager",
        "name": "Priya Raman",
        "permitted_actions": ["crm.approve_discount", "crm.request_approval"],
    }

    refused = _add(auth_client, environment, manager)
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    listed = listed.get("result", listed)
    confirmed = auth_client.post(
        f"{BASE}/{environment.id}/actors/",
        {"actor": manager, "share_actions": ["crm.request_approval"]},
        format="json",
    )

    # The manager is given the customer's own action: refused before anything is saved, naming who holds it.
    assert refused.status_code == 409, refused.content
    body = refused.json()
    assert body["error"] == "permission_conflict"
    assert body["conflicts"] == [
        {
            "actor": "manager",
            "action": "crm.request_approval",
            "holder": "customer",
            "holder_name": "Lena Ortiz",
        }
    ]
    assert "held only by Lena Ortiz" in body["message"]
    assert [one["id"] for one in listed["actors"]] == ["customer"]
    # Confirmed as shared, the edit goes through.
    assert confirmed.status_code == 200, confirmed.content


def test_no_conflict_for_an_action_the_actor_already_had_none_holds_or_two_already_share(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actors-permission-no-conflict")
    lead = {"id": "lead", "name": "Arjun", "permitted_actions": ["crm.reject_discount"]}
    coworker = {
        "id": "coworker",
        "name": "Meera",
        "permitted_actions": ["crm.reject_discount"],
    }
    assert _add(auth_client, environment, lead).status_code == 200
    assert (
        auth_client.post(
            f"{BASE}/{environment.id}/actors/",
            {"actor": coworker, "share_actions": ["crm.reject_discount"]},
            format="json",
        ).status_code
        == 200
    )
    manager = {
        "id": "manager",
        "name": "Priya",
        "permitted_actions": ["crm.approve_discount", "crm.reject_discount"],
    }

    # Nobody holds approve_discount; two already share reject_discount, so it is shared, not reserved.
    added = _add(auth_client, environment, manager)
    # Editing the lead keeps the action it already had: never a conflict with the others who share it.
    kept = auth_client.put(
        f"{BASE}/{environment.id}/actors/lead/",
        {"actor": {**lead, "name": "Arjun Mehta"}},
        format="json",
    )

    assert added.status_code == 200, added.content
    assert kept.status_code == 200, kept.content

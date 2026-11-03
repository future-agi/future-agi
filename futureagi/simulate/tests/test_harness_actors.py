"""An environment's actors, edited in its draft: a saved version is unchanged until the draft is saved."""

import json

import pytest

from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_api import _versioned
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
    assert "scenario.solution" in view.json()["never_sees"]
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
    anonymous = auth_client.post(f"{BASE}/{environment.id}/actors/", {"actor": unnamed}, format="json")
    removed = auth_client.delete(url)
    missing = auth_client.delete(url)

    assert (renamed.status_code, renamed.json()["error"]) == (400, "actor_id_mismatch")
    assert (
        replaced.status_code == 200
        and replaced.json()["actors"][0]["goal"] == "Approve fast"
    )
    assert kept.status_code == 200
    assert (anonymous.status_code, anonymous.json()["error"]) == (400, "actor_id_missing")
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
    assert {"coworker.goal", "coworker.prompt", "coworker.instruction"} <= set(
        view["never_sees"]
    )


def test_an_actor_in_a_scenario_cannot_be_removed():
    from simulate.services.harness_actors import _scenarios_using

    suite = [
        {
            "scenario_key": "discount-approved",
            "actors": [{"id": "manager", "instruction": "Approve it"}],
        }
    ]
    assert _scenarios_using(suite, "manager") == ["discount-approved"]
    assert _scenarios_using(suite, "coworker") == []


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


def test_an_actor_a_scenario_includes_is_not_removed(
    user, workspace, storage, auth_client
):
    from unittest.mock import patch

    environment = _built(user, workspace, "actors-in-use")
    _add(auth_client, environment, MANAGER)

    with patch(
        "simulate.services.harness_actors._scenarios_using",
        return_value=["discount-approved"],
    ):
        refused = auth_client.delete(f"{BASE}/{environment.id}/actors/manager/")

    assert (refused.status_code, refused.json()["error"]) == (409, "actor_in_use")
    assert "discount-approved" in refused.json()["message"]
    assert [
        one["id"]
        for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    ] == ["manager"]


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

    assert added.diff["actors"] == {"added": ["manager"], "removed": [], "changed": []}
    assert "1 actor added" in added.summary
    # One actor edit is one change: `actors.json` is not counted again as a world file.
    assert "world file" not in added.summary and "actors.json" not in (
        added.diff["files"] or {}
    ).get("changed", [])
    assert (
        changed.diff["actors"]["added"] == ["manager"]
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


def test_a_sub_actor_is_in_the_cast_and_edited_where_the_call_reads_it(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.drafts import edit_sides, fresh_draft

    environment = _built(user, workspace, "actors-sub-actor")
    colleague = {
        "id": "colleague",
        "name": "Jordan Taylor",
        "role": "the caller's colleague",
        "side": "caller",
        "channel": "audio_mix",
        "loudness_ratio": 0.5,
    }
    added = _add(auth_client, environment, colleague)
    _add(auth_client, environment, MANAGER)
    files = edit_sides(environment)["environment"]

    assert added.status_code == 200, added.content
    assert json.loads(files["sub_actors.json"])["sub_actors"] == [
        {
            "id": "colleague",
            "name": "Jordan Taylor",
            "role": "the caller's colleague",
            "channel": "audio_mix",
            "loudness_ratio": 0.5,
            "provenance": json.loads(files["sub_actors.json"])["sub_actors"][0][
                "provenance"
            ],
        }
    ]
    assert [one["id"] for one in json.loads(files["actors.json"])["actors"]] == [
        "manager"
    ]
    cast = {
        one["id"]: one
        for one in auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    }
    assert (cast["colleague"]["side"], cast["colleague"]["channel"]) == (
        "caller",
        "audio_mix",
    )
    assert fresh_draft(environment, "environment").diff["actors"]["added"] == [
        "colleague",
        "manager",
    ]

    replaced = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**colleague, "channel": "audio_handoff"}},
        format="json",
    )
    assert replaced.status_code == 200, replaced.content
    assert json.loads(edit_sides(environment)["environment"]["sub_actors.json"])[
        "sub_actors"
    ][0]["channel"] == ("audio_handoff")
    from simulate.tests.test_harness_amend_archive import NAMES, _key

    view = auth_client.get(
        f"{BASE}/{environment.id}/actors/colleague/", {"scenario_key": _key(NAMES[0])}
    ).json()
    assert view["permitted_actions"] == [] and "manager.goal" in view["never_sees"]
    # As the call gives a sub-actor the caller's situation, so does its view.
    assert "caller.situation" in {one["input"] for one in view["sees"]}

    refused = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**colleague, "goal": "Get a bigger discount"}},
        format="json",
    )
    assert (refused.status_code, refused.json()["error"]) == (400, "actor_invalid")
    assert (
        "colleague has no goal in sub_actors.json; remove it."
        in refused.json()["message"]
    )
    loud = auth_client.put(
        f"{BASE}/{environment.id}/actors/colleague/",
        {"actor": {**colleague, "side": "world"}},
        format="json",
    )
    assert (loud.status_code, loud.json()["error"]) == (400, "actor_invalid")

    removed = auth_client.delete(f"{BASE}/{environment.id}/actors/colleague/")
    assert removed.status_code == 200, removed.content
    assert (
        json.loads(edit_sides(environment)["environment"]["sub_actors.json"])[
            "sub_actors"
        ]
        == []
    )
    assert [one["id"] for one in removed.json()["actors"]] == ["manager"]


def test_a_sub_actor_a_scenario_includes_counts_as_in_use():
    from simulate.services import harness_actors

    suite = {
        "scenarios/one/scenario.json": json.dumps(
            {
                "scenario_key": "one",
                "instruction": "I need my plan renewed. Your details: account 7",
                "sub_actors": [
                    {"id": "colleague", "instruction": "Ask for a bigger discount"}
                ],
            }
        ).encode()
    }
    documents = [json.loads(raw) for raw in suite.values()]
    assert harness_actors._scenarios_using(documents, "colleague") == ["one"]


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
    assert "scenario.rubric" in view["never_sees"]
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
    assert "scenario.solution" in earlier_view["never_sees"]
    assert "scenario.rubric" not in earlier_view["never_sees"]


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
    assert set(listed["channels"]) == {"audio_mix", "audio_handoff", "ivr"}
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
    sub_actor = {
        "id": "colleague",
        "name": "Jordan",
        "side": "caller",
        "channel": "audio_mix",
    }
    added = _add(auth_client, environment, sub_actor)
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
        {"actor": {**sub_actor, "channel": "smoke_signal"}},
        format="json",
    )

    assert added.status_code == 200, added.content
    assert colleague["scenarios"] == 0
    assert resent.status_code == 200, resent.content
    stored = json.loads(edit_sides(environment)["environment"]["sub_actors.json"])[
        "sub_actors"
    ]
    assert "scenarios" not in stored[0]
    assert (bad_channel.status_code, bad_channel.json()["error"]) == (
        400,
        "actor_invalid",
    )
    assert (
        "colleague's channel 'smoke_signal' is not one of: audio_mix, audio_handoff, ivr."
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
            **{"sub_actors.json": json.dumps({"sub_actors": [colleague]})},
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
        {"actor": {**colleague, "side": "caller", "role": "the caller's colleague"}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    draft = fresh_draft(environment, "environment")
    assert draft.diff["actors"] == {
        "added": [],
        "changed": ["colleague"],
        "removed": [],
    }
    assert draft.summary == "1 actor changed"


def test_a_sub_actor_never_takes_the_name_of_a_caller_it_is_with():
    from simulate.services import harness_actors

    rules = json.loads(harness_actors._RULES_BEFORE_DECLARED.read_text())
    suite = {
        "scenarios/one/scenario.json": json.dumps(
            {
                "scenario_key": "one",
                "persona": {"name": "Mei Lin"},
                "sub_actors": [{"id": "colleague"}],
            }
        ).encode(),
        "scenarios/two/scenario.json": json.dumps(
            {"scenario_key": "two", "persona": {"name": "Jordan Taylor"}}
        ).encode(),
    }
    colleague = {"id": "colleague", "name": "Jordan Taylor", "channel": "audio_mix"}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "simulate.services.harness_versioning.versions.suite_documents",
            lambda files: [json.loads(raw) for raw in files.values()],
        )
        # The caller of a scenario that does not include the sub-actor may share its name.
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
    naming = {"from": "tool_entrypoints", "route": "endpoint", "tool": "tool", "separator": "."}
    sealed = {**naming, "method": "method", "methods": ["POST"], "placeholder": "{"}

    def names(rules):
        return [one["action"] for one in world_actions(files, {"world_actions": rules})]

    assert names(sealed) == ["crm.approve"]
    # Rules sealed before methods and placeholders were named keep every route.
    assert names(naming) == ["crm.approve", "crm.read", "orders.{order_id}.cancel"]

"""What the actor editor picks from and how its edits are answered, all the server's: the options each field offers,
the cap a way of joining counts, the limits' defaults, when a scenario's actions are taken, empty optional numbers,
problems in the editor's words, names no two actors share, records written elsewhere and documents never replaced."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from simulate.services import harness_actor_labels as labels
from simulate.services.harness_actor_merge import id_from, merged_record
from simulate.services.harness_actor_options import limit_fields
from simulate.services.harness_actor_rules import _RULES_BEFORE_DECLARED
from simulate.services.harness_actor_view import seen_text
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_actors import BASE, CRM_CONTRACT, MANAGER, _add, _built
from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
from simulate.tests.test_harness_versioning_record import _authoring
from tfc.utils import storage_client

RULES = json.loads(_RULES_BEFORE_DECLARED.read_text())


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def storage(monkeypatch):
    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _stored(environment):
    raw = drafts.edit_sides(environment)["environment"].get("actors.json")
    return {one["id"]: one for one in json.loads(raw)["actors"]} if raw else {}


def _with_actors(user, workspace, key, actors, rules=None):
    """An environment whose builder wrote these actor records as they are, sealed with these rules when given."""
    environment = record_tests._environment(user, workspace, key)
    sealed = {"actor_rules.json": json.dumps(rules)} if rules is not None else {}
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{"actors.json": json.dumps({"actors": actors}), **sealed},
        ),
        origin="authoring",
    )
    return environment


def _rules_with_caps():
    """Rules of a release that declares an actor's own caps."""
    rules = json.loads(json.dumps(RULES))
    cap = {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
    rules["records"]["actors.json"]["properties"].update(max_turns=cap, max_lines=cap)
    return rules


def _check(auth_client, environment, **body):
    return auth_client.post(
        f"{BASE}/{environment.id}/actor-validation/", body, format="json"
    )


# --- The cap a way of joining counts --------------------------------------------------------------------------------


def test_each_way_of_joining_names_its_cap_and_a_change_of_channel_moves_the_stored_cap(
    user, workspace, storage, auth_client
):
    environment = _with_actors(
        user, workspace, "actor-rules-cap", [], rules=_rules_with_caps()
    )
    url = f"{BASE}/{environment.id}/actors/"
    created = auth_client.post(
        url,
        {"changes": {"name": "Sam", "role": "a clerk", "max_turns": 3}},
        format="json",
    )
    assert created.status_code == 200, created.content
    options = {
        one["channel"]: one["cap_field"] for one in created.json()["channel_options"]
    }

    joined = auth_client.put(
        f"{url}sam/", {"changes": {"channel": "audio_mix"}}, format="json"
    )
    speaking = dict(_stored(environment)["sam"])
    left = auth_client.put(f"{url}sam/", {"changes": {"channel": None}}, format="json")

    assert options == {
        "audio_mix": "max_lines",
        "audio_handoff": "max_lines",
        "ivr": "max_lines",
        "chat_message": "max_lines",
        None: "max_turns",
    }
    assert joined.status_code == 200 and left.status_code == 200, joined.content
    # The cap moves to the field the new way of joining counts, never left where nothing shows it.
    assert (speaking.get("max_lines"), "max_turns" in speaking) == (3, False)
    stored = _stored(environment)["sam"]
    assert (stored.get("max_turns"), "max_lines" in stored) == (3, False)


def test_a_cap_sent_with_the_change_of_channel_is_the_edit_s_and_the_old_one_goes():
    stored = {"id": "sam", "name": "Sam", "max_turns": 3}

    merged = merged_record(
        stored, {"channel": "audio_mix", "max_lines": 5}, loud_channels=[]
    )
    unchanged = merged_record(stored, {"name": "Sam Lee"}, loud_channels=[])

    assert (merged.get("max_lines"), "max_turns" in merged) == (5, False)
    assert unchanged.get("max_turns") == 3 and "max_lines" not in unchanged


# --- The options each field offers, and writes checked against them -------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [("age_group", "18-31"), ("gender", "robot"), ("accent", "Martian"), ("kind", "x")],
)
def test_a_picked_value_the_field_does_not_offer_is_a_problem_on_that_field(
    user, workspace, storage, auth_client, field, value
):
    environment = _built(user, workspace, f"actor-rules-pick-{field}")
    _add(auth_client, environment, MANAGER)

    check = _check(
        auth_client,
        environment,
        op="replace",
        actor_id="manager",
        changes={field: value},
    )
    write = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {field: value}},
        format="json",
    )

    assert check.status_code == 200, check.content
    assert [one["field"] for one in check.json()["problems"]] == [field]
    assert (write.status_code, write.json()["error"]) == (400, "actor_invalid")
    assert [one["field"] for one in write.json()["problems"]] == [field]


@pytest.mark.parametrize(
    "changes", [{}, {"name": ""}, {"name": "   "}], ids=["untouched", "empty", "blank"]
)
def test_a_new_actor_without_a_name_is_asked_for_one_on_the_name_field(
    user, workspace, storage, auth_client, changes
):
    environment = _built(user, workspace, "actor-rules-unnamed")

    check = _check(auth_client, environment, op="create", changes=changes)

    assert check.status_code == 200, check.content
    assert check.json()["problems"] == [
        {"field": "name", "message": "Give them a name."}
    ]


def test_a_new_actor_s_too_long_id_is_a_problem_on_its_id_field(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-long-id")

    check = _check(
        auth_client,
        environment,
        op="create",
        changes={"name": "Priya Raman"},
        id="a" * 70,
    )

    assert check.status_code == 200, check.content
    assert check.json()["problems"] == [
        {"field": "id", "message": "This must be at most 64 characters."}
    ]


def test_a_value_stored_before_that_the_field_does_not_offer_is_kept_on_another_edit(
    user, workspace, storage, auth_client
):
    environment = _with_actors(
        user,
        workspace,
        "actor-rules-kept-pick",
        [{**MANAGER, "kind": "vendor", "persona": {"accent": "Irish"}}],
    )

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {"goal": "Hold the price", "gender": "female"}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    stored = _stored(environment)["manager"]
    assert (stored["kind"], stored["persona"]["accent"]) == ("vendor", "Irish")


# --- The limits' defaults -------------------------------------------------------------------------------------------


# As ALK seals them: the chat value, and the voice value where a voice call differs.
SEALED_DEFAULTS = {
    "max_active": {"default": None, "by_modality": {"voice": 2}},
    "actor_seconds_per_call": {"default": None, "by_modality": {"voice": 60.0}},
    "max_messages": {"default": 20, "by_modality": {}},
    "model": {"default": None, "by_modality": {}},
}


def _defaults(fields):
    return {one["key"]: (one["default"], one["default_label"]) for one in fields}


@pytest.mark.parametrize(
    "modality, said",
    [
        (
            "voice",
            {
                "max_active": (2, "Default: 2"),
                "actor_seconds_per_call": (60.0, "Default: 60 s"),
                "max_messages": (20, "Default: 20"),
            },
        ),
        (
            "chat",
            {
                "max_active": (None, "Default: no limit"),
                "actor_seconds_per_call": (None, "Default: no limit"),
                "max_messages": (20, "Default: 20"),
            },
        ),
        # Before authoring reports the agent's modality, the shared default.
        (
            None,
            {
                "max_active": (None, "Default: no limit"),
                "actor_seconds_per_call": (None, "Default: no limit"),
                "max_messages": (20, "Default: 20"),
            },
        ),
    ],
)
def test_a_limit_s_default_is_the_sealed_one_for_the_agent_s_modality(modality, said):
    defaults = _defaults(
        limit_fields({**RULES, "limit_defaults": SEALED_DEFAULTS}, modality)
    )

    assert {key: defaults[key] for key in said} == said
    assert defaults["model"] == (None, "Default: the caller's model")
    # A limit the release seals nothing for says nothing.
    assert defaults["live_turns_per_trial"] == (None, None)


def test_a_bundle_that_seals_no_defaults_says_none():
    assert all(
        value == (None, None)
        for value in _defaults(limit_fields(RULES, "voice")).values()
    )


def test_the_cast_reads_the_defaults_for_its_agent_and_a_bundle_with_one_model(
    user, workspace, storage, auth_client
):
    rules = {**RULES, "limit_defaults": SEALED_DEFAULTS, "actor_models": ["only-model"]}
    environment = record_tests._environment(user, workspace, "actor-rules-defaults")
    record_authoring(
        environment,
        _authoring(
            contract={**CRM_CONTRACT, "modality": "voice"},
            **{"actor_rules.json": json.dumps(rules)},
        ),
        origin="authoring",
    )

    cast = auth_client.get(f"{BASE}/{environment.id}/actors/")
    limited = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/",
        {"limits": {"model": "only-model"}},
        format="json",
    )

    assert cast.status_code == 200, cast.content
    assert _defaults(cast.json()["limit_fields"])["max_active"] == (2, "Default: 2")
    assert cast.json()["actor_models"] == ["only-model"]
    assert limited.status_code == 200, limited.content


# --- When a scenario's actions are taken ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger, said",
    [
        (None, "At the start"),
        ({"kind": "always"}, "At the start"),
        ({"kind": "after_seconds", "seconds": 30}, "30 s after the start"),
        (
            {"kind": "on_tool", "tool": "crm.approve"},
            "When the agent calls crm.approve",
        ),
        (
            {"kind": "on_tool", "tool": "crm.approve", "on_call": 2},
            "On call 2 of crm.approve",
        ),
        ({"kind": "contacted"}, "When the agent contacts them"),
        ({"kind": "probability", "p": 0.25}, "In about 25% of calls, at the start"),
        ({"kind": "mid_journey", "turn": 3}, "After the agent's turn 3"),
        ({"kind": "on_keyword", "words": ["refund"]}, "When the agent says refund"),
        (
            {
                "kind": "conditional",
                "collection": "orders",
                "where": {"status": "late"},
            },
            'Once a record in orders with status "late" exists',
        ),
    ],
)
def test_each_scenario_action_says_when_the_world_takes_it(trigger, said):
    action = {
        "action": "crm.approve_discount",
        **({"trigger": trigger} if trigger else {}),
    }

    [resolved] = labels.resolved_scenario_actors(
        [{"id": "manager", "instruction": "", "actions": [action]}], [MANAGER]
    )

    assert resolved["actions"] == [{**action, "when_label": said}]


def test_a_scenario_actor_without_actions_gets_none_added():
    [resolved] = labels.resolved_scenario_actors(
        [{"id": "manager", "instruction": ""}], [MANAGER]
    )

    assert "actions" not in resolved


# --- What an actor sees, as a line ----------------------------------------------------------------------------------


def test_the_actor_view_gives_each_seen_value_its_line(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-seen")
    _add(auth_client, environment, MANAGER)

    view = auth_client.get(f"{BASE}/{environment.id}/actors/manager/").json()

    goal = next(one for one in view["sees"] if one["input"] == "actor.goal")
    assert goal["text"] == "Approve only discounts she can justify"
    # A list or a set of fields reads as its line, never as its raw form.
    shaped = [one for one in view["sees"] if isinstance(one["value"], (list, dict))]
    assert shaped
    assert all(
        one["text"] == seen_text(one["input"].split(".", 1)[1], one["value"])
        and one["text"] != str(one["value"])
        for one in shaped
    )


# --- Empty optional numbers, and problems in the editor's words -----------------------------------------------------


def test_an_emptied_optional_number_is_no_value_of_its_own(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-empty")
    _add(auth_client, environment, MANAGER)
    changes = {
        "timeout_seconds": None,
        "response_timing": {"kind": "delayed", "seconds": None},
    }

    check = _check(
        auth_client, environment, op="replace", actor_id="manager", changes=changes
    )
    write = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/", {"changes": changes}, format="json"
    )

    assert check.status_code == 200 and check.json()["problems"] == [], check.content
    assert write.status_code == 200, write.content
    stored = _stored(environment)["manager"]
    assert stored["activation"]["timeout_seconds"] == 0.0
    assert stored["behaviour"]["response_timing"] == {"kind": "immediate", "seconds": 0}


@pytest.mark.parametrize(
    "changes, field, message",
    [
        (
            {"trigger": {"kind": "mid_journey", "turn": None}},
            "trigger",
            "The agent's turn needs a value.",
        ),
        (
            {"trigger": {"kind": "mid_journey", "turn": 1.5}},
            "trigger",
            "The agent's turn must be a whole number.",
        ),
        ({"max_turns": 2.5}, "max_turns", "This must be a whole number."),
        ({"max_turns": 0}, "max_turns", "This must be 1 or more."),
        ({"timeout_seconds": -1}, "timeout_seconds", "This must be 0 or more."),
        ({"name": "x" * 501}, "name", "This must be at most 500 characters."),
        (
            {"response_timing": {"kind": "delayed", "seconds": "soon"}},
            "response_timing",
            "The seconds must be a number.",
        ),
        ({"facts": ["ok", 7, None]}, "facts", "Entry 3 needs a value."),
    ],
)
def test_a_change_its_serializer_refuses_is_a_problem_on_the_editor_s_field_never_an_api_path(
    user, workspace, storage, auth_client, changes, field, message
):
    environment = _built(user, workspace, "actor-rules-words")
    _add(auth_client, environment, MANAGER)

    check = _check(
        auth_client, environment, op="replace", actor_id="manager", changes=changes
    )
    write = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/", {"changes": changes}, format="json"
    )

    assert check.status_code == 200, check.content
    assert {"field": field, "message": message} in check.json()["problems"]
    assert all(
        one["field"] == field and "changes" not in one["message"]
        for one in check.json()["problems"]
    )
    assert (write.status_code, write.json()["error"]) == (400, "actor_invalid")
    assert {"field": field, "message": message} in write.json()["problems"]
    assert "changes" not in write.json()["message"]


def test_a_check_wrong_outside_its_changes_is_still_refused(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-wrong-op")

    check = _check(auth_client, environment, op="rename", changes={"name": "Sam"})

    assert check.status_code == 400


@pytest.mark.parametrize(
    "limits, message",
    [
        ({"max_active": "two"}, "This must be a whole number."),
        ({"max_active": 0}, "This must be 1 or more."),
        ({"max_active": 0.5}, "This must be a whole number."),
        (
            {"actor_seconds_per_call": 0},
            "Enter a number above 0, or leave it empty for no limit.",
        ),
    ],
)
def test_a_limit_refused_says_which_limit_in_the_editor_s_words(
    user, workspace, storage, auth_client, limits, message
):
    environment = _built(user, workspace, "actor-rules-limits")

    put = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/", {"limits": limits}, format="json"
    )

    assert (put.status_code, put.json()["error"]) == (400, "limits_invalid")
    assert put.json()["problems"] == [{"field": next(iter(limits)), "message": message}]
    assert "limits." not in put.json()["message"] and "_" not in put.json()["message"]


# --- Names no two actors share --------------------------------------------------------------------------------------


def test_a_name_another_actor_has_in_any_case_and_spacing_is_a_name_problem(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-names")
    _add(auth_client, environment, MANAGER)
    url = f"{BASE}/{environment.id}/actors/"

    check = _check(
        auth_client, environment, op="create", changes={"name": " priya RAMAN "}
    )
    write = auth_client.post(url, {"changes": {"name": "Priya Raman"}}, format="json")
    other = auth_client.post(url, {"changes": {"name": "Sam Lee"}}, format="json")
    kept = auth_client.put(
        f"{url}manager/", {"changes": {"goal": "Hold the price"}}, format="json"
    )

    assert check.status_code == 200, check.content
    assert [one["field"] for one in check.json()["problems"]] == ["name"]
    assert (write.status_code, write.json()["error"]) == (400, "actor_invalid")
    assert other.status_code == 200 and kept.status_code == 200, kept.content
    assert set(_stored(environment)) == {"manager", "sam_lee"}


def test_a_name_in_another_script_gets_a_stable_readable_id_never_a_bare_actor():
    first = id_from("Иван Петров", set())

    assert first.startswith("actor_") and first != "actor"
    assert id_from("Иван Петров", set()) == first
    assert id_from("李明", set()) not in (first, "actor", "actor_2")
    assert id_from("", set()) == "actor"


# --- A record written elsewhere -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        {"activation": {"trigger": "always"}},
        {"persona": None},
    ],
    ids=["trigger-a-word", "persona-null"],
)
def test_a_record_invalid_in_a_field_the_edit_does_not_touch_can_still_be_edited(
    user, workspace, storage, auth_client, stored
):
    manager = {**MANAGER, **stored}
    environment = _with_actors(
        user, workspace, f"actor-rules-old-{next(iter(stored))}", [manager]
    )

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {"goal": "Hold the price"}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    assert _stored(environment)["manager"]["goal"] == "Hold the price"


def test_an_edit_that_breaks_a_field_is_still_refused_on_a_record_written_elsewhere(
    user, workspace, storage, auth_client
):
    environment = _with_actors(
        user, workspace, "actor-rules-old-broken", [{**MANAGER, "persona": None}]
    )

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {"loudness_ratio": 2, "channel": "audio_mix", "role": "a manager"}},
        format="json",
    )

    assert (edited.status_code, edited.json()["error"]) == (400, "actor_invalid")
    assert "loudness_ratio" in [one["field"] for one in edited.json()["problems"]]


# --- Documents never replaced ---------------------------------------------------------------------------------------


def test_an_upload_of_a_name_the_draft_holds_is_refused_naming_its_readers(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-upload-taken")
    url = f"{BASE}/{environment.id}/files/"
    first = auth_client.post(
        url,
        {"file": SimpleUploadedFile("policy.md", b"Old policy"), "kind": "knowledge"},
        format="multipart",
    )
    _add(
        auth_client,
        environment,
        {**MANAGER, "knowledge": [{"kind": "files", "paths": ["knowledge/policy.md"]}]},
    )

    again = auth_client.post(
        url,
        {"file": SimpleUploadedFile("policy.md", b"New policy"), "kind": "knowledge"},
        format="multipart",
    )

    assert first.status_code == 200, first.content
    assert (again.status_code, again.json()["error"]) == (409, "upload_exists")
    assert again.json()["readers"] == ["Priya Raman"]
    files = drafts.edit_sides(environment)["environment"]
    assert files["knowledge/policy.md"] == b"Old policy"


# --- What a later release keeps beside the cast ---------------------------------------------------------------------


def test_a_top_level_key_a_later_release_writes_beside_the_cast_survives_every_write(
    user, workspace, storage, auth_client
):
    share = {"min": 0.15, "max": 0.25}
    environment = record_tests._environment(user, workspace, "actor-rules-share")
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "actors.json": json.dumps(
                    {
                        "actors": [MANAGER],
                        "unresolved": [
                            {"policy": "Refunds over 500", "why": "no record"}
                        ],
                        "actor_share": share,
                    }
                )
            },
        ),
        origin="authoring",
    )
    url = f"{BASE}/{environment.id}"

    def actor_share():
        raw = drafts.edit_sides(environment)["environment"]["actors.json"]
        return json.loads(raw).get("actor_share")

    edited = auth_client.put(
        f"{url}/actors/manager/", {"changes": {"goal": "Hold the price"}}, format="json"
    )
    after_edit = actor_share()
    limited = auth_client.put(
        f"{url}/actor-limits/", {"limits": {"max_active": 3}}, format="json"
    )
    after_limits = actor_share()
    accepted = auth_client.post(
        f"{url}/actor-unresolved/accept/", {"policy": "Refunds over 500"}, format="json"
    )

    assert (edited.status_code, limited.status_code, accepted.status_code) == (
        200,
        200,
        200,
    ), accepted.content
    assert after_edit == share and after_limits == share and actor_share() == share


# --- What a new actor starts as, and what each way of joining and trigger kind is called ---------------------------


def test_a_new_actor_is_served_as_the_create_writes_it_and_a_create_from_a_name_matches(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-new")
    url = f"{BASE}/{environment.id}/actors/"

    new = auth_client.get(url).json()["new_actor"]
    created = auth_client.post(url, {"changes": {"name": "Sam"}}, format="json")
    [sam] = [one for one in created.json()["actors"] if one["id"] == "sam"]

    assert (new["id"], new["name"], new["side"], new["persona"]) == (
        "",
        "",
        "world",
        None,
    )
    assert (new["kind"], new["play"]) == ("person", "mocked")
    assert new["activation"] == {
        "trigger": {"kind": "always"},
        "timeout_seconds": 0.0,
        "fallback": "silent",
    }
    assert new["behaviour"]["response_timing"] == {"kind": "immediate", "seconds": 0.0}
    assert new["fallback"] == {
        "kind": "silent",
        "value": "silent",
        "label": "Nothing happens",
    }
    assert new["editable_trigger"] is True
    # The values served are the ones a create writes for every field left as it is.
    compared = (
        "kind",
        "play",
        "activation",
        "behaviour",
        "constraints",
        "facts",
        "files",
        "records",
    )
    assert {key: new[key] for key in compared} == {key: sam[key] for key in compared}


def test_each_way_of_joining_offers_its_trigger_kinds_labelled_in_the_same_order(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-labels")

    cast = auth_client.get(f"{BASE}/{environment.id}/actors/").json()

    for one in cast["channel_options"]:
        assert [option["value"] for option in one["trigger_options"]] == one[
            "trigger_kinds"
        ]
        # Only the way of acting in the world alone has no label of its own.
        assert (one["label"] is None) == (one["channel"] is None)
    assert set(cast["trigger_fields"]) >= {
        kind for one in cast["channel_options"] for kind in one["trigger_kinds"]
    }


def test_an_actor_s_aim_is_labelled_as_its_aim_and_an_empty_one_stays_empty(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-goal")
    url = f"{BASE}/{environment.id}/actors/"
    _add(auth_client, environment, MANAGER)
    auth_client.post(url, {"changes": {"name": "Sam"}}, format="json")

    new = auth_client.get(url).json()["new_actor"]
    aimed = auth_client.get(f"{url}manager/").json()["sees"]
    aimless = auth_client.get(f"{url}sam/").json()["sees"]

    [goal] = [one for one in aimed if one["input"] == "actor.goal"]
    assert goal["label"] == "Aim in this conversation"
    assert goal["text"] == MANAGER["goal"]
    # Optional: nothing is written for an actor given none, and it is not shown as seen.
    assert new["goal"] == "" and _stored(environment)["sam"]["goal"] == ""
    assert "actor.goal" not in [one["input"] for one in aimless]


def test_an_id_over_its_length_is_a_problem_on_the_id_in_the_editor_s_words(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-rules-long-id")
    body = {"changes": {"name": "Sam"}, "id": "x" * 65}

    check = _check(auth_client, environment, op="create", **body)
    write = auth_client.post(f"{BASE}/{environment.id}/actors/", body, format="json")

    assert check.status_code == 200, check.content
    assert check.json()["problems"] == [
        {"field": "id", "message": "This must be at most 64 characters."}
    ]
    assert (write.status_code, write.json()["error"]) == (400, "actor_invalid")
    assert [one["field"] for one in write.json()["problems"]] == ["id"]


@pytest.mark.parametrize(
    "changes",
    [{"timeout_seconds": 5}, {"fallback": "silent"}],
    ids=["timeout", "fallback"],
)
def test_an_activation_edit_keeps_working_beside_a_trigger_written_elsewhere(
    user, workspace, storage, auth_client, changes
):
    environment = _with_actors(
        user,
        workspace,
        f"actor-rules-old-trigger-{next(iter(changes))}",
        [{**MANAGER, "activation": {"trigger": "always", "fallback": "decline"}}],
    )

    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/", {"changes": changes}, format="json"
    )
    broken = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {"trigger": {"kind": "on_tool"}, "timeout_seconds": -1}},
        format="json",
    )

    assert edited.status_code == 200, edited.content
    # What the edit writes is still checked.
    assert broken.status_code == 400

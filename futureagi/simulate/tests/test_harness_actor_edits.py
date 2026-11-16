"""The rules the actor editor used to apply by hand, now the server's: the cast's derived fields and sentences, the
field-level patch merged into the stored record, the id made from a name, and a dry run that writes nothing."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from simulate.services import harness_actor_labels as labels
from simulate.services.harness_actor_merge import id_from, merged_record
from simulate.services.harness_actor_rules import _RULES_BEFORE_DECLARED, typed_limits
from simulate.services.harness_actor_view import never_sees
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_actors import (
    BASE,
    CRM_CONTRACT,
    MANAGER,
    _add,
    _built,
)
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring, _scenario

RULES = json.loads(_RULES_BEFORE_DECLARED.read_text())
LOUD = ["audio_mix"]


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _stored(environment):
    raw = drafts.edit_sides(environment)["environment"].get("actors.json")
    return {one["id"]: one for one in json.loads(raw)["actors"]} if raw else {}


def _with_manager_in_a_scenario(user, workspace, key, actors=None):
    documents = [
        _scenario(NAMES[0], actors=[{"id": "manager", "instruction": "Approve it"}]),
        _scenario(NAMES[1]),
    ]
    environment = record_tests._environment(user, workspace, key)
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
                **({"actors.json": json.dumps({"actors": actors})} if actors else {}),
            },
        ),
        origin="authoring",
    )
    return environment


# --- Sentences: when an actor comes in, how long it waits, who answers for it ---------------------------------------


@pytest.mark.parametrize(
    "trigger, channel, said",
    [
        ({"kind": "always"}, None, "Present from the start"),
        # Someone who speaks comes in at the first moment the runtime lets them: after the agent's first turn.
        ({}, "audio_mix", "After the agent's first turn"),
        (
            {"kind": "always"},
            "audio_handoff",
            "After the agent's second turn",
        ),
        ({"kind": "always"}, "ivr", "Answers the call"),
        # Someone who takes the phone never does so before the agent's second turn.
        (
            {"kind": "mid_journey", "turn": 1},
            "audio_handoff",
            "After the agent's turn 2",
        ),
        ({"kind": "mid_journey", "turn": 1}, "audio_mix", "After the agent's turn 1"),
        (
            {"kind": "on_tool", "tool": "crm.approve"},
            None,
            "When the agent calls crm.approve",
        ),
        (
            {"kind": "on_tool", "tool": "crm.approve", "on_call": 3},
            None,
            "On call 3 of crm.approve",
        ),
        (
            {"kind": "on_keyword", "words": ["refund", "manager"]},
            "chat_message",
            "When the agent says refund, manager",
        ),
        ({"kind": "contacted"}, None, "When the agent contacts them"),
        ({"kind": "after_seconds", "seconds": 2.5}, None, "2.5 s after the start"),
        ({"kind": "after_seconds", "seconds": 30.0}, None, "30 s after the start"),
        (
            {
                "kind": "conditional",
                "collection": "orders",
                "where": {"status": "late", "count": 2},
            },
            None,
            'Once a record in orders with status "late", count 2 exists',
        ),
        ({"kind": "conditional"}, None, "Once a record exists"),
        (
            {"kind": "probability", "p": 0.125},
            "audio_mix",
            "In about 13% of calls, after the agent's first turn",
        ),
        (
            {"kind": "probability"},
            None,
            "In about 100% of calls, present from the start",
        ),
        # A kind a later release adds reads by its own name, never as something it is not.
        ({"kind": "on_silence"}, None, "On silence"),
    ],
)
def test_when_label_says_when_the_runtime_brings_the_actor_in(trigger, channel, said):
    assert labels.when_label(trigger, channel) == said


@pytest.mark.parametrize(
    "activation, said, kind",
    [
        ({"timeout_seconds": 5}, "Waits 5 s, then nothing is sent", "silent"),
        (
            {"timeout_seconds": 5, "fallback": "decline"},
            "Waits 5 s, then it declines",
            "decline",
        ),
        (
            {"timeout_seconds": 1.5, "fallback": "sam"},
            "Waits 1.5 s, then Sam Lee answers instead",
            "actor",
        ),
        (
            {"timeout_seconds": 5, "fallback": "crm.reject"},
            "Waits 5 s, then crm.reject is taken for it",
            "action",
        ),
        ({"timeout_seconds": 0, "fallback": "decline"}, None, "decline"),
        ({}, None, "silent"),
    ],
)
def test_timeout_label_and_fallback_name_what_happens_when_the_actor_does_not_answer(
    activation, said, kind
):
    actor = {
        "id": "priya",
        "permitted_actions": ["crm.reject"],
        "activation": activation,
    }
    names = {"priya": "Priya", "sam": "Sam Lee"}

    assert labels.timeout_label(actor, names) == said
    assert labels.fallback_of(actor, names)["kind"] == kind


def test_a_fallback_that_is_one_of_the_actor_s_actions_is_an_action_even_when_an_actor_has_that_id():
    actor = {
        "id": "priya",
        "permitted_actions": ["sam"],
        "activation": {"fallback": "sam"},
    }

    assert labels.fallback_of(actor, {"sam": "Sam"}) == {
        "kind": "action",
        "value": "sam",
        "label": "sam is taken for them",
    }


def test_a_scenario_s_actor_is_resolved_against_the_cast_and_its_own_trigger_wins():
    cast = [
        {
            "id": "sam",
            "name": "Sam",
            "role": "brother",
            "channel": "audio_handoff",
            "goal": "Get off the phone",
            "activation": {
                "trigger": {"kind": "mid_journey", "turn": 1},
                "timeout_seconds": 4,
            },
        },
        {"id": "caller", "name": "Ana"},
    ]
    entries = [
        {"id": "sam", "instruction": ""},
        {
            "id": "sam",
            "instruction": "Ask about the bill",
            "trigger": {"kind": "mid_journey", "turn": 5},
        },
        {"id": "caller", "instruction": ""},
        {"id": "ghost", "instruction": "x"},
    ]

    own, overridden, caller, ghost = labels.resolved_scenario_actors(entries, cast)

    assert own == {
        "id": "sam",
        "name": "Sam",
        "role": "brother",
        "channel": "audio_handoff",
        "side": "world",
        "effective_trigger": {"kind": "mid_journey", "turn": 1},
        "when_label": "After the agent's turn 2",
        "timeout_label": "Waits 4 s, then nothing is sent",
        # The scenario's instruction as stored, even when it gives none.
        "instruction": "",
    }
    assert overridden["effective_trigger"] == {"kind": "mid_journey", "turn": 5}
    assert overridden["when_label"] == "After the agent's turn 5"
    assert overridden["instruction"] == "Ask about the bill"
    assert (caller["side"], caller["channel"], caller["when_label"]) == (
        "caller",
        None,
        "Present from the start",
    )
    assert (ghost["name"], ghost["timeout_label"], ghost["effective_trigger"]) == (
        "ghost",
        None,
        {"kind": "always"},
    )


def test_trigger_kinds_follow_the_runtime_and_the_sealed_schema_and_editable_trigger_follows_them():
    newer = json.loads(json.dumps(RULES))
    newer["records"]["actors.json"]["$defs"]["Trigger"]["properties"]["kind"][
        "enum"
    ].append("conditional")

    # The first sealed schema has no `conditional` kind, so it is never offered there.
    assert labels.trigger_kinds(RULES, "audio_mix") == [
        "always",
        "on_tool",
        "on_keyword",
        "mid_journey",
    ]
    assert labels.trigger_kinds(newer, None) == [
        "always",
        "on_tool",
        "contacted",
        "after_seconds",
        "conditional",
    ]
    kinds = labels.trigger_kinds(newer, None)
    assert labels.editable_trigger({"kind": "on_tool", "tool": "a"}, None, kinds)
    assert not labels.editable_trigger(
        {"kind": "on_tool", "tool": "a", "on_call": 2}, None, kinds
    )
    assert not labels.editable_trigger({"kind": "probability", "p": 0.5}, None, kinds)
    assert not labels.editable_trigger(
        {"kind": "on_keyword", "words": ["x"]}, None, kinds
    )


# --- The id made from a name -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, taken, made",
    [
        ("Sam Lee", set(), "sam_lee"),
        ("Sam Lee", {"sam_lee"}, "sam_lee_2"),
        ("Sam Lee", {"sam_lee", "sam_lee_2"}, "sam_lee_3"),
        ("  José  Ñúñez!! ", set(), "jose_nunez"),
        ("", set(), "actor"),
        ("x" * 80, set(), "x" * 60),
    ],
)
def test_an_id_is_made_from_the_name_unique_and_not_reserved(name, taken, made):
    assert id_from(name, taken) == made


# --- The patch merged into the stored record ------------------------------------------------------------------------


def test_a_patch_changes_only_what_it_names_and_keeps_every_other_field_as_stored():
    stored = {
        **MANAGER,
        "mood": "patient",
        "identities": {"crm": "priya@acme.test"},
        "activation": {**MANAGER["activation"], "timeout_seconds": 5, "note": "kept"},
    }

    after = merged_record(stored, {"goal": "Hold the price"}, loud_channels=LOUD)

    assert after == {**stored, "goal": "Hold the price"}
    # The stored record is never changed in place.
    assert stored["goal"] == MANAGER["goal"]


def test_the_persona_name_follows_the_name_only_when_the_persona_has_one():
    named = {"id": "sam", "name": "Sam", "persona": {"name": "Sam", "accent": "Irish"}}
    unnamed = {"id": "sam", "name": "Sam", "persona": {"accent": "Irish"}}

    assert merged_record(named, {"name": " Sam Lee "}, loud_channels=LOUD)[
        "persona"
    ] == {
        "name": "Sam Lee",
        "accent": "Irish",
    }
    assert merged_record(unnamed, {"name": "Sam Lee"}, loud_channels=LOUD)[
        "persona"
    ] == {"accent": "Irish"}


def test_a_voice_field_sent_empty_is_removed_and_the_others_stay():
    stored = {
        "id": "sam",
        "name": "Sam",
        "persona": {"gender": "male", "accent": "Irish"},
    }

    after = merged_record(
        stored, {"accent": "", "age_group": "senior"}, loud_channels=LOUD
    )

    assert after["persona"] == {"gender": "male", "age_group": "senior"}


def test_loudness_is_kept_only_on_a_channel_that_has_one():
    loud = {"id": "sam", "name": "Sam", "channel": "audio_mix", "loudness_ratio": 0.4}

    moved = merged_record(loud, {"channel": "audio_handoff"}, loud_channels=LOUD)
    set_on_mix = merged_record(loud, {"loudness_ratio": 0.7}, loud_channels=LOUD)
    cleared = merged_record(loud, {"channel": None}, loud_channels=LOUD)

    assert moved["loudness_ratio"] is None and moved["channel"] == "audio_handoff"
    assert set_on_mix["loudness_ratio"] == 0.7
    assert "channel" not in cleared and cleared["loudness_ratio"] is None


def test_a_new_fallback_drops_the_old_one_s_arguments_unless_they_are_sent_too():
    stored = {
        **MANAGER,
        "activation": {
            "fallback": "crm.reject_discount",
            "fallback_arguments": {"reason": "late"},
            "trigger": {"kind": "always"},
        },
    }

    new = merged_record(stored, {"fallback": "decline"}, loud_channels=LOUD)[
        "activation"
    ]
    same = merged_record(
        stored, {"fallback": "crm.reject_discount"}, loud_channels=LOUD
    )["activation"]
    sent = merged_record(
        stored,
        {"fallback": "crm.approve_discount", "fallback_arguments": {"percent": 10}},
        loud_channels=LOUD,
    )["activation"]
    cleared = merged_record(stored, {"fallback_arguments": None}, loud_channels=LOUD)[
        "activation"
    ]

    assert new == {"fallback": "decline", "trigger": {"kind": "always"}}
    assert same["fallback_arguments"] == {"reason": "late"}
    assert sent["fallback_arguments"] == {"percent": 10}
    assert "fallback_arguments" not in cleared


def test_facts_still_written_keep_their_entry_and_place_and_other_knowledge_keeps_its_order():
    knowledge = [
        {"kind": "facts", "text": "Prices rise in May.", "source": "policy.md"},
        {"kind": "files", "paths": ["knowledge/policy.md"]},
        {"kind": "facts", "text": "The budget is $10k.\nOnly she knows it."},
        {
            "kind": "records",
            "store": "db",
            "collection": "orders",
            "where": {"status": "open"},
        },
        {"kind": "facts", "text": "Dropped later."},
    ]
    stored = {**MANAGER, "knowledge": knowledge}

    after = merged_record(
        stored,
        {
            "facts": [
                "New first fact.",
                "The budget is $10k.\nOnly she knows it.",
                "Prices rise in May.",
            ]
        },
        loud_channels=LOUD,
    )["knowledge"]

    assert after == [
        {"kind": "facts", "text": "New first fact."},
        knowledge[1],
        knowledge[2],
        knowledge[3],
        # A fact still there keeps its other keys, in the place a fact held.
        knowledge[0],
    ]


def test_a_record_collection_still_picked_keeps_its_where_and_files_are_one_entry():
    knowledge = [
        {"kind": "facts", "text": "A fact."},
        {
            "kind": "records",
            "store": "db",
            "collection": "orders",
            "where": {"status": "open"},
        },
        {"kind": "files", "paths": ["knowledge/a.md"], "note": "kept"},
    ]
    stored = {**MANAGER, "knowledge": knowledge}

    after = merged_record(
        stored,
        {
            "records": [
                {"store": "db", "collection": "orders"},
                {"store": "db", "collection": "refunds"},
            ],
            "files": ["knowledge/a.md", "knowledge/b.md"],
        },
        loud_channels=LOUD,
    )["knowledge"]
    only_files = merged_record(stored, {"files": []}, loud_channels=LOUD)["knowledge"]

    assert after == [
        knowledge[0],
        # The one files entry keeps the stored entry's other keys.
        {
            "kind": "files",
            "paths": ["knowledge/a.md", "knowledge/b.md"],
            "note": "kept",
        },
        knowledge[1],
        {"kind": "records", "store": "db", "collection": "refunds"},
    ]
    assert only_files == [knowledge[0], knowledge[1]]


@pytest.mark.parametrize("field", ["max_turns", "max_lines", "model", "parent"])
def test_null_clears_a_limit_the_model_and_the_parent(field):
    stored = {
        **MANAGER,
        "max_turns": 4,
        "max_lines": 3,
        "model": "gemini-3.8-flash",
        "parent": "sam",
    }

    after = merged_record(stored, {field: None}, loud_channels=LOUD)

    kept = {key: value for key, value in stored.items() if key != field}
    assert after == ({**stored, field: None} if field == "parent" else kept)


def test_constraints_response_timing_and_trigger_are_written_as_the_form_wrote_them():
    stored = {**MANAGER, "behaviour": {"style": "brisk"}}

    after = merged_record(
        stored,
        {
            "may_agree": ["  Up to 30% ", "", "Free shipping"],
            "response_timing": {"kind": "delayed", "seconds": 3},
            "trigger": {"kind": "on_keyword", "words": ["discount"]},
            "timeout_seconds": 8,
        },
        loud_channels=LOUD,
    )

    assert after["constraints"] == {
        "may_agree": ["Up to 30%", "Free shipping"],
        "private": ["The quarter's discount budget"],
    }
    assert after["behaviour"] == {
        "style": "brisk",
        "response_timing": {"kind": "delayed", "seconds": 3.0},
    }
    assert after["activation"] == {
        "trigger": {"kind": "on_keyword", "words": ["discount"]},
        "fallback": "decline",
        "timeout_seconds": 8.0,
    }


# --- What an actor never sees, grouped to read ----------------------------------------------------------------------


def test_never_sees_groups_the_grader_s_material_and_other_actors_private_fields():
    seen = never_sees(
        ["sub_goals", "setup_code"],
        ["sub_goals.json", "runtime-validation.json"],
        ["Sam.goal", "Sam.prompt", "Mei Lin.goal", "Mei Lin.prompt", "Dr. Ruiz.goal"],
    )

    assert seen["grader"] == ["sub goals", "setup code", "runtime validation"]
    assert seen["others"] == [
        {"fields": ["goal", "prompt"], "count": 2},
        {"fields": ["goal"], "count": 1},
    ]
    assert seen["other_actors"][0] == "Sam.goal"


# --- Through the API ------------------------------------------------------------------------------------------------


def test_the_cast_carries_what_the_server_decides_about_each_actor(
    user, workspace, storage, auth_client
):
    # As the builder chat might have written them: one action this world no longer has.
    environment = _with_manager_in_a_scenario(
        user,
        workspace,
        "actor-edits-derived",
        actors=[
            {
                **MANAGER,
                "identities": {"crm": "priya@acme.test"},
                "permitted_actions": ["crm.approve_discount", "gone.x"],
            },
            {
                "id": "rep",
                "name": "Sam",
                "role": "sales rep",
                "channel": "audio_mix",
                "permitted_actions": ["crm.request_approval"],
                "activation": {"fallback": "manager", "timeout_seconds": 6},
            },
        ],
    )

    cast = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    actors = {one["id"]: one for one in cast["actors"]}
    manager, rep = actors["manager"], actors["rep"]

    assert (manager["removable"], manager["scenarios"]) == (False, 1)
    assert manager["removal_blocked_by"][0].startswith("Priya Raman is in 1 scenario")
    assert (
        "Sam has Priya Raman answer instead when Sam does not"
        in manager["removal_blocked_by"][1]
    )
    assert [one["field"] for one in manager["locked_fields"]] == ["channel"]
    assert (rep["removable"], rep["locked_fields"], rep["removal_blocked_by"]) == (
        True,
        [],
        [],
    )
    assert rep["reaches"] == ["manager"] and manager["reaches"] == []
    assert manager["permissions"] == [
        {"action": "crm.approve_discount", "in_world": True},
        {"action": "gone.x", "in_world": False},
    ]
    assert rep["fallback"] == {
        "kind": "actor",
        "value": "manager",
        "label": "Priya Raman answers instead",
    }
    assert rep["timeout_label"] == "Waits 6 s, then Priya Raman answers instead"
    assert manager["when_label"] == "When the agent calls crm.request_approval"
    assert (rep["side"], rep["channel"], manager["channel"]) == (
        "world",
        "audio_mix",
        None,
    )
    assert manager["editable_trigger"] is True
    # Typed: the record's knowledge, constraints and activation as fields, defaults filled where it set none.
    assert manager["constraints"]["may_agree"] == ["Discounts up to 25%"]
    assert (
        manager["activation"]["timeout_seconds"] == 0.0 and manager["play"] == "mocked"
    )
    assert cast["has_caller"] is False and cast["record_max_chars"] == 65_536
    options = {one["channel"]: one for one in cast["channel_options"]}
    # A speaking actor needs a role, as the sealed schema's conditional rule says; one in the world does not.
    assert options["audio_mix"]["required_fields"] == ["name", "role"]
    assert options[None]["required_fields"] == ["name"]
    assert options["audio_mix"]["loudness"] and not options["ivr"]["loudness"]
    assert options[None]["trigger_kinds"] == [
        "always",
        "on_tool",
        "contacted",
        "after_seconds",
    ]
    assert cast["id_hint"] == "1 to 64 lowercase letters, digits or underscores"
    kinds = {one["key"]: one["kind"] for one in cast["limit_fields"]}
    assert (kinds["actor_seconds_per_call"], kinds["max_active"]) == (
        "seconds",
        "whole",
    )


def test_a_create_from_changes_gets_an_id_from_its_name_unique_and_not_reserved(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-id")
    url = f"{BASE}/{environment.id}/actors/"

    first = auth_client.post(url, {"changes": {"name": "Agent"}}, format="json")
    # Another name that makes the same id: two actors never share a name.
    second = auth_client.post(url, {"changes": {"name": "Agent!"}}, format="json")
    given = auth_client.post(
        url, {"changes": {"name": "Sam"}, "id": "rep_1"}, format="json"
    )

    assert first.status_code == 200, first.content
    assert set(_stored(environment)) == {"agent_2", "agent_3", "rep_1"}
    assert second.status_code == 200 and given.status_code == 200


def test_a_patch_through_the_api_keeps_a_field_a_later_release_declared(
    user, workspace, storage, auth_client
):
    rules = json.loads(json.dumps(RULES))
    rules["records"]["actors.json"]["properties"]["mood"] = {"type": "string"}
    environment = record_tests._environment(user, workspace, "actor-edits-mood")
    record_authoring(
        environment,
        _authoring(contract=CRM_CONTRACT, **{"actor_rules.json": json.dumps(rules)}),
        origin="authoring",
    )
    assert (
        _add(auth_client, environment, {**MANAGER, "mood": "patient"}).status_code
        == 200
    )

    patched = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"changes": {"goal": "Hold the price"}},
        format="json",
    )

    assert patched.status_code == 200, patched.content
    stored = _stored(environment)["manager"]
    assert (stored["mood"], stored["goal"]) == ("patient", "Hold the price")
    assert stored["permitted_actions"] == MANAGER["permitted_actions"]
    # Who set it is stamped per changed field.
    assert stored["provenance"]["fields"]["goal"] == "editor"


def test_a_whole_record_and_changes_cannot_both_be_sent_and_one_is_needed(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-one-of")
    url = f"{BASE}/{environment.id}/actors/"

    both = auth_client.post(
        url, {"actor": MANAGER, "changes": {"name": "X"}}, format="json"
    )
    neither = auth_client.post(url, {"expected_revision": 0}, format="json")
    whole = auth_client.post(url, {"actor": MANAGER}, format="json")

    assert both.status_code == 400 and neither.status_code == 400
    assert whole.status_code == 200, whole.content
    assert _stored(environment)["manager"]["goal"] == MANAGER["goal"]


def test_validation_answers_as_the_write_would_and_writes_nothing(
    user, workspace, storage, auth_client, django_capture_on_commit_callbacks
):
    environment = _with_manager_in_a_scenario(user, workspace, "actor-edits-dry")
    assert _add(auth_client, environment, MANAGER).status_code == 200
    url = f"{BASE}/{environment.id}/actor-validation/"
    before = drafts.fresh_draft(environment, "environment").revision

    def check(**edit):
        return auth_client.post(url, edit, format="json")

    with django_capture_on_commit_callbacks() as retired:
        create = check(
            op="create",
            changes={
                "name": "Rep",
                "permitted_actions": ["crm.approve_discount"],
                "parent": "nobody",
            },
        )
        shared = check(
            op="create",
            changes={"name": "Rep", "permitted_actions": ["crm.approve_discount"]},
            share_actions=["crm.approve_discount"],
        )
        huge = check(op="create", changes={"name": "Rep", "goal": "x" * 70_000})
        moved = check(
            op="replace", actor_id="manager", changes={"channel": "audio_mix"}
        )
        removed = check(op="delete", actor_id="manager")
        unnamed = check(op="replace", changes={"name": "X"})
        missing = check(op="delete", actor_id="nobody")

    assert create.status_code == 200, create.content
    assert create.json() == {
        "problems": [
            {
                "field": "parent",
                "message": "rep's parent 'nobody' is not another listed actor.",
            }
        ],
        "conflicts": [
            {
                "actor": "rep",
                "action": "crm.approve_discount",
                "holder": "manager",
                "holder_name": "Priya Raman",
            }
        ],
        "suggested_id": "rep",
        "over_by": 0,
    }
    assert shared.json()["conflicts"] == [] and shared.json()["problems"] == []
    assert huge.json()["over_by"] > 0
    assert (
        huge.json()["problems"][-1]["message"]
        == "Keep an actor under 65536 characters."
    )
    assert moved.json()["problems"][0]["field"] == "channel"
    assert moved.json()["suggested_id"] is None
    assert removed.status_code == 200, removed.content
    assert removed.json()["problems"][0]["message"].startswith(
        "Priya Raman is in 1 scenario"
    )
    assert unnamed.status_code == 400
    assert (missing.status_code, missing.json()["error"]) == (404, "actor_not_found")
    # Nothing written, and the builder chat's runtime is never retired.
    assert drafts.fresh_draft(environment, "environment").revision == before
    assert set(_stored(environment)) == {"manager"}
    assert retired == []


def test_a_write_from_changes_retires_the_chat_runtime_as_any_write_does(
    user, workspace, storage, auth_client, django_capture_on_commit_callbacks
):
    environment = _built(user, workspace, "actor-edits-retire")

    with django_capture_on_commit_callbacks() as retired:
        written = auth_client.post(
            f"{BASE}/{environment.id}/actors/",
            {"changes": {"name": "Rep"}},
            format="json",
        )

    assert written.status_code == 200, written.content
    assert len(retired) == 1


def test_a_write_from_changes_is_refused_as_a_whole_record_would_be(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-refused")
    assert _add(auth_client, environment, MANAGER).status_code == 200
    url = f"{BASE}/{environment.id}/actors/"

    taken = auth_client.post(
        url, {"changes": {"name": "X"}, "id": "manager"}, format="json"
    )
    shared = auth_client.post(
        url,
        {"changes": {"name": "Rep", "permitted_actions": ["crm.approve_discount"]}},
        format="json",
    )
    huge = auth_client.post(
        url, {"changes": {"name": "Rep", "goal": "x" * 70_000}}, format="json"
    )
    renamed = auth_client.put(
        f"{url}manager/", {"changes": {"name": "X"}, "id": "other"}, format="json"
    )

    assert (taken.status_code, taken.json()["error"]) == (409, "actor_exists")
    assert (shared.status_code, shared.json()["error"]) == (409, "permission_conflict")
    assert (huge.status_code, huge.json()["error"]) == (400, "actor_invalid")
    assert (renamed.status_code, renamed.json()["error"]) == (400, "actor_id_mismatch")
    assert set(_stored(environment)) == {"manager"}


def test_limits_are_typed_and_a_wrong_type_is_still_refused_as_limits_invalid(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-limits")
    url = f"{BASE}/{environment.id}/actor-limits/"

    put = auth_client.put(
        url,
        {"limits": {"max_active": 3.0, "actor_seconds_per_call": 2.5, "model": None}},
        format="json",
    )
    wrong = auth_client.put(url, {"limits": {"max_active": "two"}}, format="json")

    assert put.status_code == 200, put.content
    assert put.json()["limits"] == {
        "max_active": 3,
        "actor_seconds_per_call": 2.5,
        "model": None,
    }
    assert (wrong.status_code, wrong.json()["error"]) == (400, "limits_invalid")
    # Said on the limit's own field, in the editor's words, never as the request's path.
    assert wrong.json()["problems"] == [
        {"field": "max_active", "message": "This must be a whole number."}
    ]
    assert "limits." not in wrong.json()["message"]


def test_limits_written_elsewhere_read_as_their_types_and_a_wrong_one_is_left_out():
    typed = typed_limits(
        {
            "max_active": 3.0,
            "actor_seconds_per_call": 2,
            "model": 5,
            "loop_guard": 1,
            "max_messages": None,
        }
    )

    assert typed == {
        "max_active": 3,
        "actor_seconds_per_call": 2.0,
        "max_messages": None,
    }
    assert (
        type(typed["max_active"]) is int
        and type(typed["actor_seconds_per_call"]) is float
    )


def test_a_create_from_changes_holds_the_defaults_and_the_cast_lists_the_caller_s_side_first(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-defaults")
    url = f"{BASE}/{environment.id}/actors/"
    assert _add(auth_client, environment, MANAGER).status_code == 200

    added = auth_client.post(
        url,
        {
            "changes": {
                "name": "Pat",
                "role": "spouse",
                "side": "caller",
                "channel": "audio_mix",
            }
        },
        format="json",
    )

    assert added.status_code == 200, added.content
    pat = _stored(environment)["pat"]
    assert {key: value for key, value in pat.items() if key != "provenance"} == {
        "id": "pat",
        "name": "Pat",
        "role": "spouse",
        "side": "caller",
        "channel": "audio_mix",
        # Automatic on a channel with a loudness.
        "loudness_ratio": None,
        "kind": "person",
        "play": "mocked",
        "goal": "",
        "constraints": {
            "may_agree": [],
            "private": [],
            "escalate_when": [],
            "stop_when": [],
        },
        "knowledge": [],
        "behaviour": {
            "style": "",
            "response_timing": {"kind": "immediate", "seconds": 0},
        },
        "permitted_actions": [],
        "activation": {
            "trigger": {"kind": "always"},
            "timeout_seconds": 0,
            "fallback": "silent",
        },
    }
    assert [one["id"] for one in added.json()["actors"]] == ["pat", "manager"]


def _with_stored_manager(user, workspace, key):
    """A manager as the builder chat wrote it, with what a read never shows: persona metadata, its knowledge and a
    field a later release declares."""
    rules = json.loads(json.dumps(RULES))
    rules["records"]["actors.json"]["properties"]["mood"] = {"type": "string"}
    stored = {
        **MANAGER,
        "mood": "patient",
        "persona": {
            "name": "Priya Raman",
            "accent": "Irish",
            "metadata": {"crm_id": 7},
        },
        "knowledge": [
            {"kind": "facts", "text": "The budget is $10k.", "source": "policy.md"},
            {
                "kind": "files",
                "paths": ["knowledge/policy.md"],
                "note": "the regional policy",
            },
            {
                "kind": "records",
                "store": "db",
                "collection": "deals",
                "where": {"stage": "open"},
            },
        ],
    }
    environment = record_tests._environment(user, workspace, key)
    record_authoring(
        environment,
        _authoring(
            contract=CRM_CONTRACT,
            **{
                "actor_rules.json": json.dumps(rules),
                "actors.json": json.dumps({"actors": [stored]}),
            },
        ),
        origin="authoring",
    )
    return environment, stored


def test_a_read_entry_sent_back_whole_with_one_edit_keeps_everything_a_read_never_shows(
    user, workspace, storage, auth_client
):
    environment, stored = _with_stored_manager(user, workspace, "actor-edits-roundtrip")
    [entry] = auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    entry["goal"] = "Hold the price"
    entry["persona"] = {**entry["persona"], "accent": "Indian"}

    resent = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/", {"actor": entry}, format="json"
    )

    assert resent.status_code == 200, resent.content
    after = _stored(environment)["manager"]
    assert {key: value for key, value in after.items() if key != "provenance"} == {
        **stored,
        "goal": "Hold the price",
        "persona": {
            "name": "Priya Raman",
            "accent": "Indian",
            "metadata": {"crm_id": 7},
        },
    }


def test_a_raw_record_with_its_own_knowledge_still_replaces_it(
    user, workspace, storage, auth_client
):
    environment, stored = _with_stored_manager(
        user, workspace, "actor-edits-raw-replace"
    )
    knowledge = [{"kind": "facts", "text": "Prices rise in May."}]

    replaced = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**stored, "knowledge": knowledge, "goal": "Hold the price"}},
        format="json",
    )

    assert replaced.status_code == 200, replaced.content
    after = _stored(environment)["manager"]
    assert (after["knowledge"], after["goal"], after["persona"]) == (
        knowledge,
        "Hold the price",
        stored["persona"],
    )
    # A record that leaves its knowledge out keeps the stored knowledge.
    without = {key: value for key, value in stored.items() if key != "knowledge"}
    resent = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**without, "goal": "Hold firm"}},
        format="json",
    )
    assert resent.status_code == 200, resent.content
    assert _stored(environment)["manager"]["knowledge"] == knowledge


@pytest.mark.parametrize(
    "trigger",
    [
        {"kind": "on_tool", "tool": "crm.x", "on_call": None},
        {"kind": "mid_journey", "turn": "2"},
        {"kind": "probability", "p": None},
        {"kind": "after_seconds", "seconds": "5"},
        {"kind": "on_keyword", "words": "refund"},
        {"kind": "on_keyword", "words": [1, None]},
    ],
)
def test_a_stored_trigger_of_the_wrong_types_reads_by_its_kind_and_is_not_editable(
    trigger,
):
    kinds = list(labels.SPEAKING_TRIGGERS) + list(labels.WORLD_TRIGGERS)

    assert (
        labels.when_label(trigger, "audio_handoff")
        == trigger["kind"].replace("_", " ").capitalize()
    )
    assert labels.editable_trigger(trigger, "audio_handoff", kinds) is False


def test_an_upload_refused_by_its_request_keeps_its_error_codes(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-edits-upload-codes")
    url = f"{BASE}/{environment.id}/files/"

    unknown = auth_client.post(
        url, {"file": SimpleUploadedFile("a.md", b"x"), "kind": "recording"}
    )
    missing = auth_client.post(url, {"kind": "knowledge"})

    assert (unknown.status_code, unknown.json()["error"]) == (
        400,
        "upload_kind_unknown",
    )
    assert (missing.status_code, missing.json()["error"]) == (400, "upload_invalid")
    assert set(missing.json()) == {"error", "message", "retryable"}


def test_a_fact_edited_as_an_earlier_client_does_keeps_the_other_knowledge(
    user, workspace, storage, auth_client
):
    environment, stored = _with_stored_manager(
        user, workspace, "actor-edits-old-fact-edit"
    )
    [entry] = auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"]
    # As an earlier editor did: the record as read, its knowledge with the one fact rewritten and the rest as read.
    entry["knowledge"] = [
        {"kind": "facts", "text": "The budget is $12k."}
        if one["kind"] == "facts"
        else one
        for one in entry["knowledge"]
    ]

    resent = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/", {"actor": entry}, format="json"
    )

    assert resent.status_code == 200, resent.content
    assert _stored(environment)["manager"]["knowledge"] == [
        {"kind": "facts", "text": "The budget is $12k."},
        stored["knowledge"][1],
        stored["knowledge"][2],
    ]


@pytest.mark.parametrize(
    "modality, channel", [("chat", "chat_message"), ("video", None)]
)
def test_someone_new_on_the_caller_s_side_speaks_on_the_first_way_the_agent_is_reached(
    modality, channel, user, workspace, storage, auth_client
):
    environment = _built(
        user,
        workspace,
        f"actor-edits-side-{modality}",
        contract={**CRM_CONTRACT, "modality": modality},
    )

    cast = auth_client.get(f"{BASE}/{environment.id}/actors/").json()

    assert cast["side_defaults"] == {
        "caller": {"channel": channel},
        "world": {"channel": None},
    }

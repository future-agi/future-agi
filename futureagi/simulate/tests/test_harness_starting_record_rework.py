"""Starting a scenario from another seeded record: the request filed with the environment's builder, refused before
anything is sent when the world can't hold the choice, and served as the state the builder's turn left."""

from __future__ import annotations

import json
from datetime import UTC, timedelta

import pytest
from django.utils import timezone

from accounts.models import Workspace
from simulate.models import (
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.tests.test_harness_starting_records import (
    FIXTURE,
    KEY,
    _author,
    _rides,
    _url,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

PRIYA = {"path": ["identity", "rider_id"], "table": "users", "key": "rdr_priya"}
Message = HostedHarnessConversationMessage
Event = HostedHarnessConversationEvent


@pytest.fixture(autouse=True)
def builder_reachable(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.test"


def _rework(auth_client, environment, body=PRIYA):
    return auth_client.post(f"{_url(environment)}rework/", body, format="json")


def _requests(environment):
    return Message.no_workspace_objects.filter(
        conversation__job=environment, payload___origin="starting_record"
    )


def test_a_rework_request_names_the_scenario_and_the_chosen_record_to_the_builder(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "rework-filed")

    response = _rework(auth_client, environment)

    assert response.status_code == 202, response.content
    assert (response.json()["state"], response.json()["key"]) == (
        "pending",
        "rdr_priya",
    )
    [message] = _requests(environment)
    # What the builder reads names the scenario and the record exactly, and what the platform reads back does too.
    assert (
        KEY in message.content and "users record rider_id=rdr_priya" in message.content
    )
    assert message.payload["_record"] == {
        **PRIYA,
        "key_field": "rider_id",
        "fields": {
            "rider_id": "rdr_priya",
            "phone": "+14155550102",
            "first_name": "Priya",
        },
    }
    # The builder reads the record exactly as seeded.
    assert '"first_name": "Priya"' in message.content
    assert message.state == Message.State.QUEUED


@pytest.mark.parametrize(
    "body",
    [
        {**PRIYA, "key": "rdr_nobody"},
        {**PRIYA, "table": "wallets"},
        {**PRIYA, "path": ["pickup"]},
        {**PRIYA, "key": "rdr_arjun"},
        {"path": ["identity", "rider_id"], "table": "users"},
    ],
    ids=["not seeded", "other table", "not a record", "already its record", "no key"],
)
def test_a_choice_the_world_cannot_hold_is_refused_and_never_sent(
    user, workspace, storage, auth_client, body
):
    environment = _rides(user, workspace, storage, "rework-refused")

    response = _rework(auth_client, environment, body)

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "starting_record_invalid"
    assert not _requests(environment).exists()


def _land(
    environment, user, *, starts_from, holds=True, proved_at=None, first_name="Priya"
):
    """The builder's turn saving the reworked scenario and its proof."""
    proof = {
        "holds": holds,
        "why": "" if holds else "the check never passes",
        "proved_at": proved_at,
    }
    _author(
        environment,
        user,
        fixture={
            **FIXTURE,
            "identity": {"rider_id": starts_from, "first_name": first_name},
        },
        extra={"proofs.json": json.dumps({KEY: proof})},
    )


def _turn_ends(environment, message, *, outcome="success", published=True):
    """The builder's turn ending as the chat records it: the checkpoint it committed, then its end, and once its save
    lands, that checkpoint published on the environment."""
    now = timezone.now()
    for sequence, kind, payload in (
        (1, "checkpoint_committed", {"digest": "sha256:" + "a" * 64}),
        (
            2,
            "turn_completed",
            {"command_message_id": str(message.id), "outcome": outcome},
        ),
    ):
        Event.no_workspace_objects.create(
            event_id=f"{message.id}-{sequence}",
            conversation_id=message.conversation_id,
            sequence=sequence,
            kind=kind,
            payload=payload,
            digest="sha256:" + "b" * 64,
            emitted_at=now,
        )
    Message.no_workspace_objects.filter(id=message.id).update(
        state=Message.State.FAILED if outcome != "success" else Message.State.COMPLETED
    )
    if published:
        environment.refresh_from_db()
        payload = dict(environment.payload or {})
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            "conversation_checkpoints": {str(message.conversation_id): {"sequence": 1}},
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )


@pytest.mark.parametrize(
    "turn, landed, served",
    [
        (None, None, "pending"),
        ({"outcome": "failure"}, None, "refused"),
        # The turn has ended but its save has not landed yet: still the builder's.
        ({"published": False}, None, "pending"),
        ({}, {"starts_from": "rdr_priya"}, "proven"),
        ({}, {"starts_from": "rdr_priya", "holds": False}, "refused"),
        # A proof made before the request is not a proof of the rework.
        ({}, {"starts_from": "rdr_priya", "stale": True}, "refused"),
        ({}, {"starts_from": "rdr_arjun"}, "refused"),
        # A later, unrelated request whose turn fails never reaches back to this rework.
        ({"later_failure": True}, {"starts_from": "rdr_priya"}, "proven"),
        # The person answers the builder's question, then the rework's save is refused: still this rework's turn.
        ({"answered_failure": True}, {"starts_from": "rdr_priya"}, "refused"),
        # A fact about the record that is not its seeded value: invented, never the record.
        ({}, {"starts_from": "rdr_priya", "first_name": "Priyanka"}, "refused"),
        # The builder found the scenario cannot hold with that record and changed nothing: its reason is served.
        (
            {
                "reply": "### Ruling value\n- **`completed_rides: 0`**: the refund can't apply."
            },
            None,
            "refused",
        ),
    ],
    ids=[
        "running",
        "failed",
        "save not landed",
        "proven",
        "proof fails",
        "unproven",
        "unchanged",
        "later unrelated failure",
        "answered then save refused",
        "invented",
        "cannot hold",
    ],
)
def test_the_served_state_follows_the_builder_s_turn(
    user, workspace, storage, auth_client, turn, landed, served
):
    environment = _rides(user, workspace, storage, "rework-state")
    assert _rework(auth_client, environment).status_code == 202
    message = _requests(environment).get()
    if landed is not None:
        stale = landed.pop("stale", False)
        made = message.created_at + (
            timedelta(hours=-1) if stale else timedelta(seconds=1)
        )
        _land(environment, user, **landed, proved_at=made.isoformat())
    turn = dict(turn) if turn is not None else None
    reply = turn.pop("reply", None) if turn is not None else None
    if turn is not None:
        later_failure = turn.pop("later_failure", False)
        answered_failure = turn.pop("answered_failure", False)
        _turn_ends(environment, message, **turn)
        if reply is not None:
            Message.no_workspace_objects.create(
                conversation_id=message.conversation_id,
                role=Message.Role.ASSISTANT,
                state=Message.State.COMPLETED,
                sequence=message.sequence + 5,
                content=reply,
            )
        if answered_failure:
            asked = Message.no_workspace_objects.create(
                conversation_id=message.conversation_id,
                role=Message.Role.ASSISTANT,
                kind=Message.Kind.CONFIRMATION,
                state=Message.State.COMPLETED,
                sequence=message.sequence + 2,
                content="Confirm this change: edit_scenario",
            )
            for offset, role, state, content, reply_to in (
                (3, Message.Role.USER, Message.State.COMPLETED, "Yes", asked.id),
                (
                    4,
                    Message.Role.ASSISTANT,
                    Message.State.FAILED,
                    "These changes were not saved: no.",
                    None,
                ),
            ):
                Message.no_workspace_objects.create(
                    conversation_id=message.conversation_id,
                    role=role,
                    state=state,
                    sequence=message.sequence + offset,
                    content=content,
                    reply_to=reply_to,
                )
        if later_failure:
            for offset, role, state in (
                (10, Message.Role.USER, Message.State.COMPLETED),
                (11, Message.Role.ASSISTANT, Message.State.FAILED),
            ):
                Message.no_workspace_objects.create(
                    conversation_id=message.conversation_id,
                    role=role,
                    state=state,
                    sequence=message.sequence + offset,
                    content="another request",
                )

    rework = auth_client.get(_url(environment)).json()["rework"]

    assert (rework["id"], rework["state"]) == (str(message.id), served)
    assert (rework["reason"] is None) == (served != "refused")
    if landed is not None and landed.get("first_name"):
        assert (
            rework["reason"]
            == "first_name is Priyanka in the scenario, but users rdr_priya is seeded with Priya."
        )
    if reply is not None:
        # The line is a short sentence of the server's own; the builder's words come whole, to show apart.
        assert (
            rework["reason"]
            == "The builder didn't rework this scenario to start from users rdr_priya. See its reply."
        )
        assert rework["builder_reply"] == reply and rework["reply_message_id"]


def test_a_rework_is_proven_only_by_the_record_at_the_path_it_asked_about(
    user, workspace, storage, auth_client
):
    # The asked rider is named by another fact, while the fact the rework asked about still names the old rider.
    environment = _rides(user, workspace, storage, "rework-path")
    assert _rework(auth_client, environment).status_code == 202
    message = _requests(environment).get()
    made = (message.created_at + timedelta(seconds=1)).isoformat()
    _author(
        environment,
        user,
        fixture={**FIXTURE, "caller": {"rider_id": "rdr_priya", "first_name": "Priya"}},
        extra={
            "proofs.json": json.dumps(
                {KEY: {"holds": True, "why": "", "proved_at": made}}
            )
        },
    )
    _turn_ends(environment, message)

    rework = auth_client.get(_url(environment)).json()["rework"]

    assert rework["state"] == "refused"


def test_a_second_request_waits_for_the_first(user, workspace, storage, auth_client):
    environment = _rides(user, workspace, storage, "rework-pending")
    assert _rework(auth_client, environment).status_code == 202

    again = _rework(auth_client, environment, {**PRIYA})

    assert again.status_code == 409
    assert _requests(environment).count() == 1


def test_proof_times_are_compared_as_times(user, workspace, storage, auth_client):
    # ALK writes UTC with a Z suffix; it must read as the same instant.
    environment = _rides(user, workspace, storage, "rework-utc")
    assert _rework(auth_client, environment).status_code == 202
    message = _requests(environment).get()
    later = (message.created_at + timedelta(seconds=1)).astimezone(UTC)
    _land(
        environment,
        user,
        starts_from="rdr_priya",
        proved_at=later.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _turn_ends(environment, message)

    assert auth_client.get(_url(environment)).json()["rework"]["state"] == "proven"


def test_a_rework_outside_the_caller_s_workspace_is_not_found_and_never_sent(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "rework-foreign")
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        workspace=Workspace.no_workspace_objects.create(
            name="Other workspace for reworks",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
    )

    response = _rework(auth_client, environment)

    assert response.status_code == 404
    assert not _requests(environment).exists()


@pytest.mark.parametrize(
    "first_name, seeded_as",
    [("Priyanka", None), (None, None), ("Invented", "Invented")],
    ids=["invented", "not a field", "seed row rewritten to match"],
)
def test_a_builder_save_with_the_record_not_as_seeded_never_reaches_the_scenario(
    user, workspace, storage, auth_client, first_name, seeded_as
):
    from simulate.services.harness_starting_records import scenario_files
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_conversation import (
        promote_conversation_checkpoint,
    )
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import (
        CONTRACT,
        SCENARIO,
        STORE,
    )
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario
    from simulate.tests.test_hosted_harness_conversation import (
        _put_conversation_checkpoint,
    )

    environment = _rides(user, workspace, storage, f"rework-save-{first_name}")
    assert _rework(auth_client, environment).status_code == 202
    message = _requests(environment).get()
    identity = {"rider_id": "rdr_priya"}
    if first_name is not None:
        identity["first_name"] = first_name
    else:
        identity["days_since_signup"] = 5
    document = _scenario(SCENARIO, fixture={**FIXTURE, "identity": identity})
    # The builder may also rewrite the seeded row to match what it wrote: the record it was asked for is the one
    # captured with the request, never the seed it submits.
    store = json.loads(json.dumps(STORE))
    if seeded_as is not None:
        for row in store["rows"]["users"]:
            if row["rider_id"] == "rdr_priya":
                row["first_name"] = seeded_as
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(store),
        },
    )
    key, digest = _put_conversation_checkpoint(message.conversation, storage, body)

    with pytest.raises(HostedHarnessError) as refused:
        promote_conversation_checkpoint(
            message.conversation,
            object_key=key,
            digest=digest,
            sequence=1,
            expected_revision=message.payload.get("_environment_revision") or "",
            command=message,
        )

    assert refused.value.code == "starting_record_not_as_seeded"
    environment.refresh_from_db()
    # Neither the saved scenario nor its unsaved changes hold the invented record: a run reads the original.
    assert scenario_files(environment, KEY).document["fixture"] == FIXTURE


def test_a_save_that_leaves_a_scenario_as_it_is_is_never_held_up_by_its_record(
    user, workspace, storage, auth_client
):
    # A mismatch made outside the builder (here, the scenario saved with an invented name) never refuses a builder
    # save that does not change that scenario.
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.harness_starting_records import scenario_files
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring

    environment = _rides(user, workspace, storage, "rework-unrelated")
    assert _rework(auth_client, environment).status_code == 202
    _author(
        environment,
        user,
        fixture={
            **FIXTURE,
            "identity": {"rider_id": "rdr_priya", "first_name": "Priyanka"},
        },
    )
    document = scenario_files(environment, KEY).document
    unchanged = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(STORE),
        },
    )

    refuse_invented(environment, unchanged, _requests(environment).get())


@pytest.mark.parametrize(
    "change, refused",
    [
        ("rewritten", True),
        ("removed", True),
        ("another row", False),
    ],
)
def test_a_save_that_leaves_the_scenario_as_it_is_still_keeps_the_asked_record_as_seeded(
    user, workspace, storage, auth_client, change, refused
):
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.harness_starting_records import scenario_files
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring

    environment = _rides(user, workspace, storage, f"rework-seed-only-{change}")
    assert _rework(auth_client, environment).status_code == 202
    document = scenario_files(environment, KEY).document
    store = json.loads(json.dumps(STORE))
    users = store["rows"]["users"]
    if change == "rewritten":
        next(one for one in users if one["rider_id"] == "rdr_priya")["first_name"] = (
            "Priyanka"
        )
    elif change == "removed":
        store["rows"]["users"] = [
            one for one in users if one["rider_id"] != "rdr_priya"
        ]
        store["rows"]["wallets"] = [
            one for one in store["rows"]["wallets"] if one["rider_id"] != "rdr_priya"
        ]
    else:
        store["rows"]["otp_codes"][0]["code"] = "111111"
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(store),
        },
    )

    command = _requests(environment).get()
    if not refused:
        refuse_invented(environment, body, command)
        return
    with pytest.raises(HostedHarnessError) as error:
        refuse_invented(environment, body, command)
    assert error.value.code == "starting_record_not_as_seeded"
    assert "users rdr_priya" in error.value.message


def test_a_save_that_changes_the_scenario_without_starting_it_from_the_asked_record_is_refused(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    environment = _rides(user, workspace, storage, "rework-record-absent")
    assert _rework(auth_client, environment).status_code == 202
    # The scenario changes, but still starts from the rider it started from, not the one the rework asked for.
    document = _scenario(
        SCENARIO, fixture={**FIXTURE, "pickup": "Indiranagar, Bengaluru"}
    )
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(STORE),
        },
    )

    with pytest.raises(HostedHarnessError) as error:
        refuse_invented(environment, body, _requests(environment).get())
    assert error.value.code == "starting_record_not_as_seeded"
    assert "users rdr_priya" in error.value.message


def test_a_rework_s_save_that_drops_its_scenario_is_refused(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    environment = _rides(user, workspace, storage, "rework-dropped")
    assert _rework(auth_client, environment).status_code == 202
    # The rework's save renames the scenario, so the scenario it was asked about is gone.
    renamed = {
        **_scenario(SCENARIO, fixture=FIXTURE),
        "scenario_key": "renamed",
        "name": "renamed",
    }
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([renamed]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(renamed),
            "store.json": json.dumps(STORE),
        },
    )

    with pytest.raises(HostedHarnessError) as error:
        refuse_invented(environment, body, _requests(environment).get())
    assert error.value.code == "starting_record_not_as_seeded"


def _later_command(rework, *, offset, reply_to=None):
    return Message.no_workspace_objects.create(
        conversation_id=rework.conversation_id,
        role=Message.Role.USER,
        state=Message.State.COMPLETED,
        sequence=rework.sequence + offset,
        content="Start it from Arjun again, and his name is Arjun K.",
        reply_to=reply_to,
    )


@pytest.mark.parametrize(
    "turn, refused",
    [("later request", False), ("answer to the rework's question", True)],
)
def test_only_the_rework_s_own_turn_is_held_to_the_asked_record(
    user, workspace, storage, auth_client, turn, refused
):
    # After the rework, an ordinary chat edit may start the scenario from another record and change that row; the
    # person's answer to the rework's own question is still the rework's turn.
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    environment = _rides(user, workspace, storage, f"rework-turn-{turn[:6]}")
    assert _rework(auth_client, environment).status_code == 202
    rework = _requests(environment).get()
    command = (
        _later_command(rework, offset=10)
        if turn == "later request"
        else _later_command(rework, offset=10, reply_to=rework.id)
    )
    document = _scenario(
        SCENARIO, fixture={**FIXTURE, "pickup": "Indiranagar, Bengaluru"}
    )
    store = json.loads(json.dumps(STORE))
    next(one for one in store["rows"]["users"] if one["rider_id"] == "rdr_priya")[
        "first_name"
    ] = "Priyanka"
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(store),
        },
    )

    if not refused:
        refuse_invented(environment, body, command)
        return
    with pytest.raises(HostedHarnessError) as error:
        refuse_invented(environment, body, command)
    assert error.value.code == "starting_record_not_as_seeded"


def test_a_request_filed_without_the_record_s_fields_holds_the_row_as_seeded_before_the_save(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_starting_record_rework import refuse_invented
    from simulate.services.harness_starting_records import scenario_files
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring

    environment = _rides(user, workspace, storage, "rework-no-fields")
    assert _rework(auth_client, environment).status_code == 202
    rework = _requests(environment).get()
    payload = dict(rework.payload)
    payload["_record"] = {k: v for k, v in payload["_record"].items() if k != "fields"}
    Message.no_workspace_objects.filter(id=rework.id).update(payload=payload)
    rework.refresh_from_db()
    document = scenario_files(environment, KEY).document
    store = json.loads(json.dumps(STORE))
    next(one for one in store["rows"]["users"] if one["rider_id"] == "rdr_priya")[
        "first_name"
    ] = "Priyanka"
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(store),
        },
    )

    with pytest.raises(HostedHarnessError) as error:
        refuse_invented(environment, body, rework)
    assert "first_name Priya" in error.value.message


def test_a_rework_waiting_on_the_person_says_so_with_the_chat_message_that_asks(
    user, workspace, storage, auth_client
):
    from simulate.models import HostedHarnessConversation

    environment = _rides(user, workspace, storage, "rework-waiting")
    assert _rework(auth_client, environment).status_code == 202
    message = _requests(environment).get()
    Message.no_workspace_objects.filter(id=message.id).update(
        state=Message.State.DELIVERED
    )
    asked = Message.no_workspace_objects.create(
        conversation_id=message.conversation_id,
        role=Message.Role.ASSISTANT,
        kind=Message.Kind.CONFIRMATION,
        state=Message.State.COMPLETED,
        sequence=message.sequence + 3,
        content="Confirm this change: edit_scenario",
    )
    HostedHarnessConversation.no_workspace_objects.filter(
        id=message.conversation_id
    ).update(
        state=HostedHarnessConversation.State.WAITING_FOR_USER,
        blocking_input={"message_id": str(asked.id), "kind": "confirmation_requested"},
    )

    served = auth_client.get(_url(environment)).json()
    waiting = served["rework"]
    assert (waiting["state"], waiting["confirm_message_id"]) == (
        "waiting",
        str(asked.id),
    )
    # No other record can be asked for while the builder waits on the person, and the panel is told why.
    assert (served["can_rework"], served["rework_blocked_reason"]) == (
        False,
        "The builder is waiting for you to confirm its change.",
    )

    # Answered, the builder works on: no longer waiting on the person.
    HostedHarnessConversation.no_workspace_objects.filter(
        id=message.conversation_id
    ).update(state=HostedHarnessConversation.State.RESPONDING, blocking_input=None)
    served = auth_client.get(_url(environment)).json()
    working = served["rework"]
    assert (working["state"], working["confirm_message_id"]) == ("pending", None)
    assert served["can_rework"] is False

    # Done, another record can be asked for.
    _turn_ends(environment, message, outcome="failure")
    assert auth_client.get(_url(environment)).json()["can_rework"] is True


def test_a_row_another_scenario_starts_from_is_offered_with_a_note_and_left_to_the_builder(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests.test_harness_starting_records import CONTRACT, SCENARIO, STORE
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    OTHER = "priya-asks-for-her-refund"
    environment = _rides(user, workspace, storage, "rework-taken")
    mine = _scenario(SCENARIO, fixture=FIXTURE)
    theirs = _scenario(
        OTHER,
        fixture={
            **FIXTURE,
            "identity": {"rider_id": "rdr_priya", "first_name": "Priya"},
        },
    )
    record_authoring(
        environment,
        _authoring(
            names=[SCENARIO, OTHER],
            contract=CONTRACT,
            **{
                "scenarios.json": json.dumps([mine, theirs]),
                f"scenarios/{SCENARIO}/scenario.json": json.dumps(mine),
                f"scenarios/{OTHER}/scenario.json": json.dumps(theirs),
                "store.json": json.dumps(STORE),
            },
        ),
        origin="manual",
        user=user,
    )

    [rider] = [
        one
        for one in auth_client.get(_url(environment)).json()["records"]
        if one["path"] == ["identity", "rider_id"]
    ]
    [priya] = [one for one in rider["choices"] if one["key"] == "rdr_priya"]
    asked = _rework(auth_client, environment)

    # Two scenarios may share a row with different callers: the builder applies its caller rule.
    assert OTHER in priya["note"]
    assert asked.status_code == 202, asked.content
    assert _requests(environment).count() == 1


@pytest.mark.parametrize(
    "fact, seeded, same",
    [
        (9007199254740993, 9007199254740992, False),
        ("9007199254740993", 9007199254740992, False),
        (9007199254740993, 9007199254740993, True),
        ("10.00", 10, True),
        (10, "10", True),
        ("0.1", 0.1, True),
        ("Priya", "Priyanka", False),
        ("02134", "2134", False),
        ("+15551234567", "15551234567", False),
        ("Priya ", "Priya", False),
        ("None", None, False),
        (None, None, True),
        (True, 1, False),
        ("10.00", "10.00", True),
    ],
    ids=[
        "large ints",
        "large int as text",
        "same large int",
        "money text",
        "int text",
        "decimal",
        "text",
        "zip leading zero",
        "phone plus",
        "trailing space",
        "None as text",
        "None",
        "true is not 1",
        "same text",
    ],
)
def test_a_fact_is_the_seeded_value_only_when_it_is_exactly_that_value(
    fact, seeded, same
):
    from simulate.services.harness_starting_record_rework import _same

    assert _same(fact, seeded) is same

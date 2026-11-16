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
        _turn_ends(environment, message, **turn)
        if reply is not None:
            Message.no_workspace_objects.create(
                conversation_id=message.conversation_id,
                role=Message.Role.ASSISTANT,
                state=Message.State.COMPLETED,
                sequence=message.sequence + 5,
                content=reply,
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
            == "The builder says this scenario can't start from users rdr_priya as seeded."
        )
        assert rework["builder_reply"] == reply and rework["reply_message_id"]


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
    "first_name", ["Priyanka", None], ids=["invented", "not a field"]
)
def test_a_builder_save_with_the_record_not_as_seeded_never_reaches_the_scenario(
    user, workspace, storage, auth_client, first_name
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
    body = _authoring(
        names=NAMES[:1],
        contract=CONTRACT,
        **{
            "scenarios.json": json.dumps([document]),
            f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
            "store.json": json.dumps(STORE),
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

    refuse_invented(environment, unchanged)


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

    waiting = auth_client.get(_url(environment)).json()["rework"]
    assert (waiting["state"], waiting["confirm_message_id"]) == (
        "waiting",
        str(asked.id),
    )

    # Answered, the builder works on: no longer waiting on the person.
    HostedHarnessConversation.no_workspace_objects.filter(
        id=message.conversation_id
    ).update(state=HostedHarnessConversation.State.RESPONDING, blocking_input=None)
    working = auth_client.get(_url(environment)).json()["rework"]
    assert (working["state"], working["confirm_message_id"]) == ("pending", None)

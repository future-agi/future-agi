"""The review of a draft: every change listed item by item, decided one by one, before Save."""

import pytest

from simulate.tests.test_harness_actors import BASE, MANAGER, _add
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _with_world(user, workspace, key, storage):
    """A versioned environment whose world has the CRM actions an actor can be given."""
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests.test_harness_actors import CRM_CONTRACT, NAMES
    from simulate.tests.test_harness_versioning_record import _authoring

    environment = _versioned(user, workspace, key, storage)
    record_authoring(
        environment,
        _authoring(names=NAMES[:1], contract=CRM_CONTRACT),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
    return environment


def _review(auth_client, environment):
    response = auth_client.get(f"{BASE}/{environment.id}/drafts/review/")
    assert response.status_code == 200, response.content
    return response.json().get("result", response.json())


def _decide(auth_client, environment, decisions, revisions):
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/review/decisions/",
        {"expected_revisions": revisions, "decisions": decisions},
        format="json",
    )


def test_an_added_actor_is_one_change_decided_once_and_undecided_again_when_it_changes(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "review-actor", storage)
    added = _add(auth_client, environment, MANAGER, revision=0)
    assert added.status_code == 200, added.content

    review = _review(auth_client, environment)
    [change] = [one for one in review["changes"] if one["kind"] == "actor"]
    assert (change["id"], change["action"], change["decision"]) == (
        "actor:manager",
        "added",
        None,
    )
    assert change["summary"] == "Add actor manager" and not review["can_save"]
    # No scenario includes the new actor yet, and it needs nothing else in the draft.
    assert (change["affects"], change["depends_on"]) == ([], [])
    # Who made the change, through what, at which draft revision.
    [source] = change["sources"]
    assert (source["agent"], source["draft_revision"]) == (
        {"kind": "user", "user_id": str(user.id)},
        1,
    )
    assert source["origin"]
    revision = review["drafts"]["environment"]["revision"]

    decided = _decide(
        auth_client,
        environment,
        [{"id": "actor:manager", "decision": "accepted"}],
        {"environment": revision},
    )
    assert decided.status_code == 200, decided.content
    body = decided.json().get("result", decided.json())
    assert (
        next(one for one in body["changes"] if one["id"] == "actor:manager")["decision"]
        == "accepted"
    )
    assert body["review_digest"] != review["review_digest"]

    # Another write to the draft keeps decisions on items it did not touch.
    second = _add(
        auth_client,
        environment,
        {**MANAGER, "id": "auditor", "name": "Sam Ortiz", "role": "reads refunds"},
        revision=revision,
    )
    assert second.status_code == 200, second.content
    kept = _review(auth_client, environment)
    assert (
        next(one for one in kept["changes"] if one["id"] == "actor:manager")["decision"]
        == "accepted"
    )
    assert (
        next(one for one in kept["changes"] if one["id"] == "actor:auditor")["decision"]
        is None
    )
    revision = kept["drafts"]["environment"]["revision"]

    # The same actor edited again is new content: the earlier decision no longer applies to it.
    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {
            "actor": {
                **MANAGER,
                "goal": "Approve only discounts the quarter's budget allows",
            },
            "expected_revision": revision,
        },
        format="json",
    )
    assert edited.status_code == 200, edited.content
    again = _review(auth_client, environment)
    manager = next(one for one in again["changes"] if one["id"] == "actor:manager")
    assert manager["decision"] is None
    # Each write that moved the item adds a source, latest first; the auditor write did not touch it.
    assert [one["draft_revision"] for one in manager["sources"]] == [3, 1]


def test_an_added_sub_actor_is_an_actor_change_and_its_rejection_is_flagged(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "review-sub-actor", storage)
    colleague = {
        "id": "colleague",
        "name": "Jordan Taylor",
        "role": "the caller's colleague",
        "side": "caller",
        "channel": "audio_mix",
    }
    added = _add(auth_client, environment, colleague, revision=0)
    assert added.status_code == 200, added.content

    review = _review(auth_client, environment)
    assert [(one["id"], one["action"]) for one in review["changes"]] == [
        ("actor:colleague", "added")
    ]
    [change] = review["changes"]
    assert change["patch"][0]["value"]["name"] == "Jordan Taylor"

    decided = _decide(
        auth_client,
        environment,
        [{"id": "actor:colleague", "decision": "rejected"}],
        {"environment": review["drafts"]["environment"]["revision"]},
    )
    assert decided.status_code == 200, decided.content
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    listed = listed.get("result", listed)
    assert {one["id"]: one["review_decision"] for one in listed["actors"]} == {
        "colleague": "rejected"
    }


def test_a_decision_on_a_draft_that_moved_or_on_an_unknown_change_is_refused(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "review-refusals", storage)
    added = _add(auth_client, environment, MANAGER, revision=0)
    assert added.status_code == 200, added.content
    revision = _review(auth_client, environment)["drafts"]["environment"]["revision"]

    stale = _decide(
        auth_client,
        environment,
        [{"remaining": True, "decision": "accepted"}],
        {"environment": revision + 1},
    )
    assert stale.status_code == 409 and stale.json()["error"] == "draft_changed"
    unknown = _decide(
        auth_client,
        environment,
        [{"id": "tool:nope", "decision": "rejected"}],
        {"environment": revision},
    )
    assert unknown.status_code == 404 and unknown.json()["error"] == "change_not_found"
    wrong = _decide(
        auth_client,
        environment,
        [{"id": "actor:manager", "decision": "maybe"}],
        {"environment": revision},
    )
    assert wrong.status_code == 400

    # Every remaining change at once, which is how "accept all remaining" works.
    everything = _decide(
        auth_client,
        environment,
        [{"remaining": True, "decision": "accepted"}],
        {"environment": revision},
    )
    body = everything.json().get("result", everything.json())
    assert body["counts"]["undecided"] == 0 and body["can_save"]


def test_with_no_draft_the_review_is_empty_and_deciding_is_refused(
    user, workspace, storage, auth_client
):
    environment = _versioned(user, workspace, "review-empty", storage)
    review = _review(auth_client, environment)
    assert (review["changes"], review["can_save"]) == ([], False)
    refused = _decide(
        auth_client, environment, [{"remaining": True, "decision": "accepted"}], {}
    )
    assert refused.status_code == 404 and refused.json()["error"] == "draft_not_found"


AUDITOR = {**MANAGER, "id": "auditor", "name": "Sam Ortiz", "role": "reads refunds"}

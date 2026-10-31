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
    environment = _versioned(user, workspace, "review-actor", storage)
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200

    review = _review(auth_client, environment)
    [change] = [one for one in review["changes"] if one["kind"] == "actor"]
    assert (change["id"], change["action"], change["decision"]) == ("actor:manager", "added", None)
    assert change["summary"] == "Add actor manager" and not review["can_save"]
    # No scenario includes the new actor yet, and it needs nothing else in the draft.
    assert (change["affects"], change["depends_on"]) == ([], [])
    # Who made the change, through what, at which draft revision.
    [source] = change["sources"]
    assert (source["agent"], source["draft_revision"]) == ({"kind": "user", "user_id": str(user.id)}, 1)
    assert source["origin"]
    revision = review["drafts"]["environment"]["revision"]

    decided = _decide(auth_client, environment, [{"id": "actor:manager", "decision": "accepted"}],
                      {"environment": revision})
    assert decided.status_code == 200, decided.content
    body = decided.json().get("result", decided.json())
    assert next(one for one in body["changes"] if one["id"] == "actor:manager")["decision"] == "accepted"
    assert body["review_digest"] != review["review_digest"]

    # Another write to the draft keeps decisions on items it did not touch.
    second = _add(auth_client, environment, {**MANAGER, "id": "auditor", "name": "Sam Ortiz", "role": "reads refunds"},
                  revision=revision)
    assert second.status_code == 200, second.content
    kept = _review(auth_client, environment)
    assert next(one for one in kept["changes"] if one["id"] == "actor:manager")["decision"] == "accepted"
    assert next(one for one in kept["changes"] if one["id"] == "actor:auditor")["decision"] is None
    revision = kept["drafts"]["environment"]["revision"]

    # The same actor edited again is new content: the earlier decision no longer applies to it.
    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**MANAGER, "goal": "Approve only discounts the quarter's budget allows"},
         "expected_revision": revision},
        format="json",
    )
    assert edited.status_code == 200, edited.content
    again = _review(auth_client, environment)
    manager = next(one for one in again["changes"] if one["id"] == "actor:manager")
    assert manager["decision"] is None
    # Each write that moved the item adds a source, latest first; the auditor write did not touch it.
    assert [one["draft_revision"] for one in manager["sources"]] == [3, 1]


def test_a_decision_on_a_draft_that_moved_or_on_an_unknown_change_is_refused(user, workspace, storage, auth_client):
    environment = _versioned(user, workspace, "review-refusals", storage)
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    revision = _review(auth_client, environment)["drafts"]["environment"]["revision"]

    stale = _decide(auth_client, environment, [{"remaining": True, "decision": "accepted"}],
                    {"environment": revision + 1})
    assert stale.status_code == 409 and stale.json()["error"] == "draft_changed"
    unknown = _decide(auth_client, environment, [{"id": "tool:nope", "decision": "rejected"}],
                      {"environment": revision})
    assert unknown.status_code == 404 and unknown.json()["error"] == "change_not_found"
    wrong = _decide(auth_client, environment, [{"id": "actor:manager", "decision": "maybe"}],
                    {"environment": revision})
    assert wrong.status_code == 400

    # Every remaining change at once, which is how "accept all remaining" works.
    everything = _decide(auth_client, environment, [{"remaining": True, "decision": "accepted"}],
                         {"environment": revision})
    body = everything.json().get("result", everything.json())
    assert body["counts"]["undecided"] == 0 and body["can_save"]


def test_with_no_draft_the_review_is_empty_and_deciding_is_refused(user, workspace, storage, auth_client):
    environment = _versioned(user, workspace, "review-empty", storage)
    review = _review(auth_client, environment)
    assert (review["changes"], review["can_save"]) == ([], False)
    refused = _decide(auth_client, environment, [{"remaining": True, "decision": "accepted"}], {})
    assert refused.status_code == 404 and refused.json()["error"] == "draft_not_found"


AUDITOR = {**MANAGER, "id": "auditor", "name": "Sam Ortiz", "role": "reads refunds"}


def _save(auth_client, environment, digest=None, name=None):
    draft = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()["environment"]
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/save/",
        {
            "drafts": [{"piece": "environment", "expected_parent_id": draft["parent_id"],
                        "expected_revision": draft["revision"]}],
            "idempotency_key": f"save-{digest or 'none'}"[:60].replace(":", "-"),
            **({"expected_review_digest": digest} if digest else {}),
            **({"name": name} if name else {}),
        },
        format="json",
    )


def _two_actors(auth_client, environment):
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    revision = _review(auth_client, environment)["drafts"]["environment"]["revision"]
    assert _add(auth_client, environment, AUDITOR, revision=revision).status_code == 200
    return _review(auth_client, environment)["drafts"]["environment"]["revision"]


def test_save_makes_only_the_accepted_changes_live_and_keeps_the_review_on_the_version(
    user, workspace, storage, auth_client, settings
):
    from simulate.tests.test_harness_actors import _saved_actors

    environment = _versioned(user, workspace, "review-partial-save", storage)
    revision = _two_actors(auth_client, environment)
    decided = _decide(auth_client, environment, [
        {"id": "actor:manager", "decision": "accepted"},
        {"id": "actor:auditor", "decision": "rejected"},
    ], {"environment": revision}).json()
    decided = decided.get("result", decided)
    assert decided["can_save"]

    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    saved = _save(auth_client, environment, decided["review_digest"], name="Manager approves discounts")
    assert saved.status_code == 200, saved.content
    from simulate.serializers.harness_environment import HarnessDraftSaveResponseSerializer

    declared = HarnessDraftSaveResponseSerializer(data=saved.json().get("result", saved.json()))
    assert declared.is_valid(), declared.errors
    assert [one["id"] for one in _saved_actors(environment)] == ["manager"]
    environment.refresh_from_db()
    version = environment.environment_version
    # The version reads as what the person approved, under the name they gave it.
    assert (version.name, version.summary) == ("Manager approves discounts", "Add actor manager")
    listed = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
    names = [one.get("name") for one in (listed.get("result", listed).get("environment") or [])]
    assert "Manager approves discounts" in names
    review = (environment.environment_version.documents or {}).get("review") or {}
    assert {key: value["decision"] for key, value in review["decisions"].items()} == {
        "actor:manager": "accepted", "actor:auditor": "rejected"}


def test_save_refuses_a_review_that_moved_or_a_rejection_with_changes_still_undecided(
    user, workspace, storage, auth_client, settings
):
    environment = _versioned(user, workspace, "review-save-refusals", storage)
    revision = _two_actors(auth_client, environment)
    before = _review(auth_client, environment)["review_digest"]
    _decide(auth_client, environment, [{"id": "actor:auditor", "decision": "rejected"}], {"environment": revision})
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

    stale = _save(auth_client, environment, before)
    assert stale.status_code == 409 and stale.json()["error"] == "review_stale"
    undecided = _save(auth_client, environment)
    assert undecided.status_code == 409 and undecided.json()["error"] == "review_undecided"
    assert undecided.json()["changes"] == ["actor:manager"]
    environment.refresh_from_db()
    assert not (environment.environment_version.documents or {}).get("review")


def test_a_reviewed_save_keeps_proofs_for_scenarios_no_accepted_change_touched(
    user, workspace, storage, auth_client, settings
):
    from django.utils import timezone

    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_versioning.review import carry_untouched_proofs

    environment = _versioned(user, workspace, "review-proofs", storage)
    parent, suite = environment.environment_version, environment.suite_version
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite, environment_version=parent,
        defaults={"status": "passed", "proofs": {"a": {"holds": True}, "b": {"holds": True}},
                  "proved_at": timezone.now()},
    )
    revision = _two_actors(auth_client, environment)
    _decide(auth_client, environment, [{"id": "actor:manager", "decision": "accepted"},
                                       {"id": "actor:auditor", "decision": "rejected"}], {"environment": revision})
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    assert _save(auth_client, environment).status_code == 200
    environment.refresh_from_db()
    carried = HarnessSuiteValidation.no_workspace_objects.get(
        suite_version=suite, environment_version=environment.environment_version)
    assert (carried.status, sorted(carried.proofs)) == ("passed", ["a", "b"])

    # A change touching one scenario drops only its proof; one that may touch every scenario carries nothing.
    target = environment.environment_version
    HarnessSuiteValidation.no_workspace_objects.filter(environment_version=target).delete()
    carry_untouched_proofs(parent, target, suite, [{"decision": "accepted", "affects": ["a"]}])
    narrowed = HarnessSuiteValidation.no_workspace_objects.get(suite_version=suite, environment_version=target)
    assert (narrowed.status, sorted(narrowed.proofs)) == ("partial", ["b"])
    narrowed.delete()
    carry_untouched_proofs(parent, target, suite, [{"decision": "accepted", "affects": "all"}])
    assert not HarnessSuiteValidation.no_workspace_objects.filter(environment_version=target).exists()

"""Validate, then Save: candidate versions built from a reviewed draft, certified, then promoted."""

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from simulate.models import (
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessScenario,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.assembly import CERTIFICATE, SEALED_MANIFEST
from simulate.services.harness_versioning.candidates import live_set
from simulate.services.harness_versioning.review import _rule_key
from simulate.tests.test_harness_actors import (
    CRM_CONTRACT,
    MANAGER,
    NAMES,
    _add,
    _saved_actors,
)
from simulate.tests.test_harness_review_api import (
    AUDITOR,
    _decide,
    _review,
    _with_world,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
RULE = "Never approve a discount above 25%"
AMENDMENT = f"rule added — {RULE}: the policy caps discounts"
RULE_ID = f"rule:{_rule_key(RULE)}"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


def _result(response):
    body = response.json()
    return body.get("result", body)


def _drafts(auth_client, environment):
    response = auth_client.get(f"{BASE}/{environment.id}/drafts/")
    assert response.status_code == 200, response.content
    return _result(response)


def _revisions(auth_client, environment):
    return {
        piece: one["revision"]
        for piece, one in _review(auth_client, environment)["drafts"].items()
    }


def _validate(auth_client, environment, digest=None, revisions=None):
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/validate/",
        {
            "expected_revisions": (
                revisions
                if revisions is not None
                else _revisions(auth_client, environment)
            ),
            **({"expected_review_digest": digest} if digest else {}),
        },
        format="json",
    )


def _certify(environment, *, state=HarnessAssembledBundle.State.READY, error=None):
    """The validation job's outcome for the live candidate set: a certified bundle, or a failed check."""
    environment.refresh_from_db()
    entry = live_set(environment)
    key = f"bundles/{uuid.uuid4().hex}.tar.gz"
    archives.put_archive(
        key,
        archives.pack_archive(
            {
                SEALED_MANIFEST: json.dumps({"files": []}).encode(),
                CERTIFICATE: json.dumps({"status": "passed"}).encode(),
            }
        ),
    )
    HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=entry["assembly_key"],
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": environment.agent_version_id,
            "environment_version_id": entry["environment_version_id"],
            "suite_version_id": entry["suite_version_id"],
            "state": state,
            "object_key": key if state == HarnessAssembledBundle.State.READY else None,
            "error": error,
        },
    )


def _save(
    auth_client, environment, candidates, *, digest=None, name=None, key="save-1"
):
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/save/",
        {
            "candidates": candidates,
            "idempotency_key": key,
            **({"expected_review_digest": digest} if digest else {}),
            **({"name": name} if name else {}),
        },
        format="json",
    )


def _plain_save(auth_client, environment, *, digest=None, key="plain-1"):
    draft = _drafts(auth_client, environment)["environment"]
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/save/",
        {
            "drafts": [
                {
                    "piece": "environment",
                    "expected_parent_id": draft["parent_id"],
                    "expected_revision": draft["revision"],
                }
            ],
            "idempotency_key": key,
            **({"expected_review_digest": digest} if digest else {}),
        },
        format="json",
    )


def _two_actors(auth_client, environment):
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    revision = _revisions(auth_client, environment)["environment"]
    assert _add(auth_client, environment, AUDITOR, revision=revision).status_code == 200


def _decided(auth_client, environment, decisions):
    response = _decide(
        auth_client,
        environment,
        [{"id": item, "decision": decision} for item, decision in decisions.items()],
        _revisions(auth_client, environment),
    )
    assert response.status_code == 200, response.content
    return _result(response)["review_digest"]


def _validated(auth_client, environment, decisions):
    """Decide, validate and certify; the candidates to save."""
    digest = _decided(auth_client, environment, decisions)
    validated = _validate(auth_client, environment, digest)
    assert validated.status_code == 202, validated.content
    _certify(environment)
    return _result(validated)["candidates"], digest


def _files(version):
    return archives.read_archive(archives.get_archive(version.archive_object_key))


def _chat_rule(environment, storage, user):
    """The builder chat adds a rule, recording it in the contract's amendment log as the harness does; returns the
    person's message the turn answered."""
    from simulate.models import HostedHarnessConversationMessage
    from simulate.tests.test_harness_versioning_drafts import _publish

    contract = {**CRM_CONTRACT, "hard_constraints": [RULE], "amendments": [AMENDMENT]}
    _publish(
        environment, storage, names=NAMES[:1], sequence=1, user=user, contract=contract
    )
    return (
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job=environment, role="user"
        )
        .order_by("-created_at")
        .first()
    )


# --- step 1: sources, amendments, restore, flags -----------------------------------------------------------------


def test_chat_change_cites_its_message(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-chat-cite", storage)
    message = _chat_rule(environment, storage, user)

    [change] = [
        one
        for one in _review(auth_client, environment)["changes"]
        if one["kind"] == "rule"
    ]
    [source] = change["sources"]
    assert source["agent"] == {"kind": "harness", "on_behalf_of": str(user.id)}
    assert source["used"] == [{"kind": "message", "id": str(message.id)}]


def test_rejected_actor_is_flagged(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-flag", storage)
    _two_actors(auth_client, environment)
    _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    listed = _result(auth_client.get(f"{BASE}/{environment.id}/actors/"))
    flags = {one["id"]: one["review_decision"] for one in listed["actors"]}
    assert flags == {"manager": "accepted", "auditor": "rejected"}
    # A flag is read-side only: sending the record back does not write it.
    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/auditor/",
        {
            "actor": {
                **next(one for one in listed["actors"] if one["id"] == "auditor"),
                "goal": "Read every refund",
            },
            "expected_revision": listed["revision"],
        },
        format="json",
    )
    assert edited.status_code == 200, edited.content
    files = drafts.edit_sides(environment)["environment"]
    assert all(
        "review_decision" not in one
        for one in json.loads(files["actors.json"])["actors"]
    )
    # The edited auditor is new content: its rejection no longer holds.
    after = _result(auth_client.get(f"{BASE}/{environment.id}/actors/"))
    assert {one["id"]: one["review_decision"] for one in after["actors"]}[
        "auditor"
    ] is None


def test_review_survives_restore_draft(user, workspace, storage, auth_client):
    from simulate.models import HostedHarnessJob

    environment = _with_world(user, workspace, "cand-restore", storage)
    _two_actors(auth_client, environment)
    _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    draft = drafts.open_draft(environment, "environment")
    kept = dict(draft.documents)
    drafts._discard(draft)

    locked = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
    restored = drafts.restore_draft(
        locked,
        "environment",
        draft_id=draft.id,
        archive_object_key=draft.archive_object_key,
        revision=draft.revision,
        last_edited_by_id=user.id,
    )
    assert restored.documents["review"] == kept["review"]
    assert restored.documents["provenance"] == kept["provenance"]


# --- step 2: the review rules at Validate ------------------------------------------------------------------------


def test_validate_needs_the_digest_once_decided(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-digest", storage)
    _two_actors(auth_client, environment)
    before = _review(auth_client, environment)["review_digest"]
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )

    missing = _validate(auth_client, environment)
    assert missing.status_code == 409 and missing.json()["error"] == "review_stale"
    old = _validate(auth_client, environment, before)
    assert old.status_code == 409 and old.json()["error"] == "review_stale"
    assert _validate(auth_client, environment, digest).status_code == 202


def test_undecided_refused_once_any_decision_exists(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-undecided", storage)
    _two_actors(auth_client, environment)
    # Accepting one is enough to make the other one's silence a refusal, with no rejection anywhere.
    digest = _decided(auth_client, environment, {"actor:manager": "accepted"})

    refused = _validate(auth_client, environment, digest)
    assert refused.status_code == 409 and refused.json()["error"] == "review_undecided"
    assert refused.json()["changes"] == ["actor:auditor"]


def test_validate_needs_every_expected_revision(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-revision", storage)
    _two_actors(auth_client, environment)
    revisions = _revisions(auth_client, environment)

    for wrong in ({}, {"environment": revisions["environment"] - 1}):
        refused = _validate(auth_client, environment, revisions=wrong)
        assert refused.status_code == 409 and refused.json()["error"] == "draft_changed"
    assert _validate(auth_client, environment, revisions=revisions).status_code == 202


# --- step 3: candidates --------------------------------------------------------------------------------------------


def test_candidates_leave_draft_and_registrations_untouched(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-untouched", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    draft = drafts.open_draft(environment, "environment")
    registrations = sorted(
        HostedHarnessScenario.no_workspace_objects.filter(job=environment).values_list(
            "id", "scenario_key"
        )
    )
    defaults = (environment.environment_version_id, environment.suite_version_id)

    validated = _validate(auth_client, environment, digest)
    assert validated.status_code == 202, validated.content
    _certify(environment)
    candidates = _result(validated)["candidates"]

    after = drafts.open_draft(environment, "environment")
    assert (after.id, after.revision, after.hash, after.documents) == (
        draft.id,
        draft.revision,
        draft.hash,
        draft.documents,
    )
    assert (
        sorted(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("id", "scenario_key")
        )
        == registrations
    )
    environment.refresh_from_db()
    assert (
        environment.environment_version_id,
        environment.suite_version_id,
    ) == defaults
    candidate = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=candidates["environment_version_id"]
    )
    assert (candidate.status, candidate.version_number, candidate.parent_id) == (
        "candidate",
        None,
        defaults[0],
    )
    assert [
        one["id"] for one in json.loads(_files(candidate)["actors.json"])["actors"]
    ] == ["manager"]
    # The suite was not drafted: the pairing validates the default suite, and no member row is written.
    assert candidates["suite_version_id"] == str(defaults[1])
    assert not HarnessSuiteMember.no_workspace_objects.filter(
        suite_version__status="candidate"
    ).exists()


def test_candidates_are_not_pickable_movable_or_twins(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.versions import move_default

    environment = _with_world(user, workspace, "cand-not-usable", storage)
    _two_actors(auth_client, environment)
    candidates, _ = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )
    candidate_id = candidates["environment_version_id"]

    with pytest.raises(drafts.HostedHarnessError) as moved:
        move_default(
            environment,
            "environment",
            candidate_id,
            expected_default_id=environment.environment_version_id,
        )
    assert moved.value.code == "version_not_pickable"
    from simulate.services.hosted_harness import VersionPicks, resolve_run_pins

    with pytest.raises(drafts.HostedHarnessError) as picked:
        resolve_run_pins(
            environment, picks=VersionPicks(environment_version_id=candidate_id)
        )
    assert picked.value.code == "version_not_pickable"
    listed = _result(auth_client.get(f"{BASE}/{environment.id}/versions/"))
    [shown] = [one for one in listed["environment"] if one["id"] == candidate_id]
    assert (
        shown["status"],
        shown["candidate_state"],
        shown["label"],
        shown["is_default"],
    ) == ("candidate", "ready", None, False)
    # A later draft with the same content is not saved onto the candidate.
    draft = drafts.open_draft(environment, "environment")
    assert drafts._twin(HarnessEnvironmentVersion, environment, draft) is None


def test_twin_skips_the_candidate(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-twin", storage)
    _two_actors(auth_client, environment)
    # Rejecting everything composes the saved version itself: no candidate row, the default stands in.
    _validated(
        auth_client,
        environment,
        {"actor:manager": "rejected", "actor:auditor": "rejected"},
    )

    environment.refresh_from_db()
    entry = live_set(environment)
    assert entry["environment_version_id"] == str(environment.environment_version_id)
    assert not HarnessEnvironmentVersion.no_workspace_objects.filter(
        job=environment, status="candidate"
    ).exists()


def test_piece_without_reject_uses_draft_files(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-draft-files", storage)
    _two_actors(auth_client, environment)
    candidates, _ = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )

    candidate = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=candidates["environment_version_id"]
    )
    assert candidate.hash == drafts.open_draft(environment, "environment").hash


def test_stale_draft_refused_at_validate(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests.test_harness_versioning_record import _authoring

    environment = _with_world(user, workspace, "cand-stale", storage)
    _two_actors(auth_client, environment)
    revisions = _revisions(auth_client, environment)
    record_authoring(
        environment,
        _authoring(names=NAMES[:1], contract={**CRM_CONTRACT, "agent": "moved"}),
        origin="manual",
        user=user,
    )

    refused = _validate(auth_client, environment, revisions=revisions)
    assert refused.status_code == 409 and refused.json()["error"] == "draft_stale"


def test_same_validate_returns_same_set(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-same", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    first = _result(_validate(auth_client, environment, digest))
    second = _result(_validate(auth_client, environment, digest))
    assert first == second
    assert (
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            job=environment, status="candidate"
        ).count()
        == 1
    )

    # Different decisions are different content: the older set is superseded.
    other = _decided(auth_client, environment, {"actor:auditor": "accepted"})
    third = _result(_validate(auth_client, environment, other))
    assert third["candidates"] != first["candidates"]
    older = HarnessEnvironmentVersion._base_manager.get(
        id=first["candidates"]["environment_version_id"]
    )
    assert (older.status, older.discard_reason) == ("discarded", "superseded")
    listed = _result(auth_client.get(f"{BASE}/{environment.id}/versions/"))
    assert (
        next(one for one in listed["environment"] if one["id"] == str(older.id))[
            "candidate_state"
        ]
        == "superseded"
    )


def test_candidates_carry_review_changes(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-review-changes", storage)
    _two_actors(auth_client, environment)
    candidates, _ = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    documents = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=candidates["environment_version_id"]
    ).documents
    environment.refresh_from_db()
    assert documents["base_defaults"] == {
        "agent": str(environment.agent_version_id),
        "environment": str(environment.environment_version_id),
        "suite": str(environment.suite_version_id),
    }
    assert {one["id"]: one["decision"] for one in documents["review_changes"]} == {
        "actor:manager": "accepted",
        "actor:auditor": "rejected",
    }
    assert set(documents["review"]["decisions"]) == {"actor:manager", "actor:auditor"}


# --- step 4: validation results ----------------------------------------------------------------------------------


def test_failed_validation_keeps_draft_and_names_affected_scenarios(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-failed", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    candidates = _result(_validate(auth_client, environment, digest))["candidates"]
    failed_actions = [
        {
            "action": "approve_discount",
            "reason": "the probe's call never reached the world",
        }
    ]
    _certify(
        environment,
        state=HarnessAssembledBundle.State.FAILED,
        error={
            "code": "authoring_runtime_validation_failed",
            "message": "certify failed",
            "failed_actions": failed_actions,
            "source": "validation",
        },
    )

    validation = _drafts(auth_client, environment)["validation"]
    assert validation["state"] == "failed"
    assert validation["error"] == {
        "code": "authoring_runtime_validation_failed",
        "message": "certify failed",
        "failed_actions": failed_actions,
    }
    assert "affected_scenarios" in validation
    refused = _save(auth_client, environment, candidates, digest=digest)
    assert refused.status_code == 409 and refused.json()["error"] == "not_validated"
    # The draft is as the person left it, decisions and all.
    review = _review(auth_client, environment)
    assert {one["id"]: one["decision"] for one in review["changes"]} == {
        "actor:manager": "accepted",
        "actor:auditor": "rejected",
    }
    listed = _result(auth_client.get(f"{BASE}/{environment.id}/versions/"))
    assert (
        next(
            one
            for one in listed["environment"]
            if one["id"] == candidates["environment_version_id"]
        )["candidate_state"]
        == "failed"
    )


# --- step 5: Save --------------------------------------------------------------------------------------------------


def test_save_refused_unless_validated(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-unvalidated", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )
    candidates = _result(_validate(auth_client, environment, digest))["candidates"]

    pending = _save(auth_client, environment, candidates, digest=digest)
    assert pending.status_code == 409 and pending.json()["error"] == "not_validated"
    unknown = _save(
        auth_client,
        environment,
        {**candidates, "environment_version_id": str(uuid.uuid4())},
        key="s2",
    )
    assert unknown.status_code == 409 and unknown.json()["error"] == "not_validated"
    environment.refresh_from_db()
    assert (
        str(environment.environment_version_id) != candidates["environment_version_id"]
    )


def test_save_refused_when_defaults_moved(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.versions import move_default

    environment = _with_world(user, workspace, "cand-moved", storage)
    previous = (
        HarnessSuiteVersion.no_workspace_objects.filter(
            job=environment, status="active"
        )
        .exclude(id=environment.suite_version_id)
        .order_by("-version_number")
        .first()
    )
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )
    environment.refresh_from_db()
    move_default(
        environment,
        "suite",
        previous.id,
        expected_default_id=environment.suite_version_id,
    )

    refused = _save(auth_client, environment, candidates, digest=digest)
    assert refused.status_code == 409 and refused.json()["error"] == "defaults_moved"
    candidate = HarnessEnvironmentVersion._base_manager.get(
        id=candidates["environment_version_id"]
    )
    assert (candidate.status, candidate.discard_reason) == ("discarded", "superseded")
    assert _drafts(auth_client, environment)["validation"] is None


def test_save_promotes_in_one_transaction(user, workspace, storage, auth_client):
    from unittest.mock import patch

    environment = _with_world(user, workspace, "cand-atomic", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    environment.refresh_from_db()
    defaults = (environment.environment_version_id, environment.suite_version_id)

    with patch(
        "simulate.services.harness_versioning.candidates.set_defaults",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            _save(auth_client, environment, candidates, digest=digest)
    environment.refresh_from_db()
    assert (
        environment.environment_version_id,
        environment.suite_version_id,
    ) == defaults
    candidate = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=candidates["environment_version_id"]
    )
    assert (candidate.status, candidate.version_number) == ("candidate", None)
    assert drafts.open_draft(environment, "environment") is not None
    assert live_set(environment) is not None


def test_save_with_digest_and_no_candidates_needs_validation(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-needs-validation", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )

    refused = _plain_save(auth_client, environment, digest=digest)
    assert (
        refused.status_code == 409 and refused.json()["error"] == "validation_required"
    )
    # Decisions alone, without the digest, need it too.
    refused = _plain_save(auth_client, environment, key="plain-2")
    assert (
        refused.status_code == 409 and refused.json()["error"] == "validation_required"
    )


def test_plain_drafts_save_works_as_today(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-plain", storage)
    _two_actors(auth_client, environment)
    # Nothing decided: Validate needs no digest, and a plain Save may still follow it.
    candidate_id = _result(_validate(auth_client, environment))["candidates"][
        "environment_version_id"
    ]

    saved = _plain_save(auth_client, environment)
    assert saved.status_code == 200, saved.content
    assert sorted(one["id"] for one in _saved_actors(environment)) == [
        "auditor",
        "manager",
    ]
    assert drafts.open_draft(environment, "environment") is None
    # The candidates built from those drafts are set aside with them.
    candidate = HarnessEnvironmentVersion._base_manager.get(id=candidate_id)
    assert candidate.status == "discarded"
    assert _drafts(auth_client, environment)["validation"] is None


def test_promotion_order_and_tail(user, workspace, storage, auth_client):
    from simulate.models import HarnessSuiteValidation

    environment = _with_world(user, workspace, "cand-tail", storage)
    parent, suite = environment.environment_version, environment.suite_version
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite,
        environment_version=parent,
        defaults={
            "status": "passed",
            "proofs": {"a": {"holds": True}},
            "proved_at": timezone.now(),
        },
    )
    _two_actors(auth_client, environment)
    draft = drafts.open_draft(environment, "environment")
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    saved = _save(
        auth_client,
        environment,
        candidates,
        digest=digest,
        name="Manager approves discounts",
    )
    assert saved.status_code == 200, saved.content
    from simulate.serializers.harness_environment import (
        HarnessDraftSaveResponseSerializer,
    )

    assert HarnessDraftSaveResponseSerializer(data=_result(saved)).is_valid()
    environment.refresh_from_db()
    version = environment.environment_version
    # The candidate itself became the default, numbered next and named, its summary the approved change.
    assert str(version.id) == candidates["environment_version_id"]
    assert (version.status, version.version_number) == (
        "active",
        parent.version_number + 1,
    )
    assert (version.name, version.summary) == (
        "Manager approves discounts",
        "Add actor manager",
    )
    assert _result(saved)["saved"]["environment"]["id"] == str(version.id)
    # name_saved ran after the move: proofs are carried against the new default suite.
    assert HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=suite, environment_version=version
    ).exists()
    # The tail: the pair's revision noted, the save remembered, the draft set aside, the set gone.
    metadata = environment.payload["metadata"]
    assert metadata["authoring_revision"] in version.documents["authoring_revisions"]
    assert metadata["draft_saves"]["save-1"]["pieces"] == {
        "environment": str(version.id)
    }
    assert drafts.open_draft(environment, "environment") is None
    draft.refresh_from_db()
    assert (draft.status, draft.documents["saved_version_id"]) == (
        "discarded",
        str(version.id),
    )
    assert "draft_validation" not in metadata
    assert (
        _save(auth_client, environment, candidates, digest=digest).json()
        == saved.json()
    )


def test_unavailable_validation_saves_as_today(
    user, workspace, storage, auth_client, settings
):
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    environment = _with_world(user, workspace, "cand-unavailable", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    validated = _validate(auth_client, environment, digest)
    assert validated.status_code == 200
    assert _result(validated) == {
        "validation": {"state": "unavailable"},
        "candidates": None,
    }
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    from simulate.services.harness_versioning import candidates as module

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(module, "validation_available", lambda environment: False)
        saved = _plain_save(auth_client, environment, digest=digest)
    assert saved.status_code == 200, saved.content
    assert [one["id"] for one in _saved_actors(environment)] == ["manager"]


# --- step 6: cleanup -----------------------------------------------------------------------------------------------


def test_old_candidates_are_discarded(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.candidates import (
        discard_expired_candidates,
    )

    environment = _with_world(user, workspace, "cand-expired", storage)
    _two_actors(auth_client, environment)
    candidates, _ = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    assert discard_expired_candidates(now=timezone.now() + timedelta(days=6)) == 0
    assert discard_expired_candidates(now=timezone.now() + timedelta(days=8)) == 1
    candidate = HarnessEnvironmentVersion._base_manager.get(
        id=candidates["environment_version_id"]
    )
    assert (candidate.status, candidate.discard_reason) == ("discarded", "expired")
    environment.refresh_from_db()
    assert live_set(environment) is None


def test_discarding_the_draft_drops_its_candidates(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-discard", storage)
    _two_actors(auth_client, environment)
    candidates, _ = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    assert (
        auth_client.delete(f"{BASE}/{environment.id}/drafts/environment/").status_code
        == 204
    )
    candidate = HarnessEnvironmentVersion._base_manager.get(
        id=candidates["environment_version_id"]
    )
    assert candidate.status == "discarded"
    assert _drafts(auth_client, environment)["validation"] is None


# --- step 7: the done-when, both orders ------------------------------------------------------------------------


def _done_when(auth_client, environment, storage, user, decisions):
    """A chat rule and a direct actor edit, both cited; decided, validated and saved."""
    message = _chat_rule(environment, storage, user)
    assert (
        _add(
            auth_client,
            environment,
            MANAGER,
            revision=_revisions(auth_client, environment)["environment"],
        ).status_code
        == 200
    )

    review = _review(auth_client, environment)
    changes = {one["id"]: one for one in review["changes"]}
    assert set(changes) == {"actor:manager", RULE_ID}
    assert changes["actor:manager"]["sources"][0]["agent"] == {
        "kind": "user",
        "user_id": str(user.id),
    }
    assert changes[RULE_ID]["sources"][0]["used"] == [
        {"kind": "message", "id": str(message.id)}
    ]
    members = sorted(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).values_list("scenario_key", flat=True)
    )
    # A rule may touch every scenario: all of them are flagged to prove again.
    from simulate.services.hosted_harness_gateway import _scenario_token

    assert changes[RULE_ID]["graded"]
    assert sorted(map(_scenario_token, changes[RULE_ID]["revalidate"])) == sorted(
        map(_scenario_token, members)
    )

    candidates, digest = _validated(auth_client, environment, decisions)
    environment.refresh_from_db()
    before = environment.environment_version_id
    saved = _save(
        auth_client, environment, candidates, digest=digest, name="v2: manager added"
    )
    assert saved.status_code == 200, saved.content
    environment.refresh_from_db()
    assert environment.environment_version_id != before
    assert drafts.open_draft(environment, "environment") is None
    return json.loads(_files(environment.environment_version)["contract.json"])


def test_reject_actor_keep_rule_saves_only_the_rule(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-done-rule", storage)
    contract = _done_when(
        auth_client,
        environment,
        storage,
        user,
        {RULE_ID: "accepted", "actor:manager": "rejected"},
    )

    assert contract["hard_constraints"] == [RULE]
    assert contract["amendments"] == [AMENDMENT]
    assert _saved_actors(environment) == []


def test_reject_rule_keep_actor_saves_only_the_actor(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-done-actor", storage)
    contract = _done_when(
        auth_client,
        environment,
        storage,
        user,
        {RULE_ID: "rejected", "actor:manager": "accepted"},
    )

    assert not contract.get("hard_constraints")
    assert not contract.get("amendments")
    assert [one["id"] for one in _saved_actors(environment)] == ["manager"]


def test_a_change_touching_one_scenario_drops_only_its_proof_and_one_touching_all_carries_none(
    user, workspace, storage
):
    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_versioning.review import carry_untouched_proofs

    environment = _with_world(user, workspace, "cand-proofs", storage)
    parent, suite = environment.environment_version, environment.suite_version
    HarnessSuiteValidation.no_workspace_objects.filter(suite_version=suite).delete()
    HarnessSuiteValidation.no_workspace_objects.create(
        suite_version=suite,
        environment_version=parent,
        status="passed",
        proofs={"a": {"holds": True}, "b": {"holds": True}},
        proved_at=timezone.now(),
    )
    other = HarnessEnvironmentVersion.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        status="discarded",
        origin="manual",
        hash="sha256:" + "9" * 64,
        archive_object_key="x",
        archive_sha256="0" * 64,
        archive_size=1,
        parent=parent,
    )
    carry_untouched_proofs(
        parent, other, suite, [{"decision": "accepted", "affects": ["a"]}]
    )
    narrowed = HarnessSuiteValidation.no_workspace_objects.get(
        suite_version=suite, environment_version=other
    )
    assert (narrowed.status, sorted(narrowed.proofs)) == ("partial", ["b"])
    narrowed.delete()
    carry_untouched_proofs(
        parent, other, suite, [{"decision": "accepted", "affects": "all"}]
    )
    assert not HarnessSuiteValidation.no_workspace_objects.filter(
        environment_version=other
    ).exists()

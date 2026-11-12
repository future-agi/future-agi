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
from simulate.services.harness_versioning.candidate_sets import live_set
from simulate.services.harness_versioning.review_items import rule_key
from simulate.tests.test_harness_actors import (
    CRM_CONTRACT,
    MANAGER,
    NAMES,
    _saved_actors,
)
from simulate.tests.test_harness_review_api import (
    AUDITOR,
    _add,
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
AMENDMENT = f"rule added \u2014 {RULE}: the policy caps discounts"
RULE_ID = f"rule:{rule_key(RULE)}"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


def _result(response):
    body = response.json()
    return body.get("result", body)


def _drafts(auth_client, environment):
    from simulate.serializers.harness_environment import HarnessDraftsSerializer

    response = auth_client.get(f"{BASE}/{environment.id}/drafts/")
    assert response.status_code == 200, response.content
    body = _result(response)
    # The read keeps to the shape its serializer and the generated contract declare.
    declared = HarnessDraftsSerializer(data=body)
    assert declared.is_valid(), declared.errors
    return body


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
    auth_client, environment, candidates, *, digest=None, name="v2", key="save-1"
):
    return auth_client.post(
        f"{BASE}/{environment.id}/drafts/save/",
        {
            "candidates": candidates,
            "idempotency_key": key,
            **({"expected_review_digest": digest} if digest else {}),
            **({"name": name} if name is not None else {}),
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
    drafts.discard_version(draft)

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
    assert drafts.active_twin(HarnessEnvironmentVersion, environment, draft) is None


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
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests.test_harness_amend_archive import _key
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    environment = _with_world(user, workspace, "cand-failed", storage)
    # One scenario needs approve_discount on every path; the other has a path without it.
    approve = [{"tool": "request_approval"}, {"tool": "approve_discount"}]
    documents = [
        _scenario(NAMES[0], solution=approve, alternatives=[approve]),
        _scenario(
            NAMES[2], solution=approve, alternatives=[[{"tool": "reject_discount"}]]
        ),
    ]
    scenarios = {"scenarios.json": json.dumps(documents)}
    for document in documents:
        scenarios[f"scenarios/{document['name']}/scenario.json"] = json.dumps(document)
    record_authoring(
        environment,
        _authoring(names=[NAMES[0], NAMES[2]], contract=CRM_CONTRACT, **scenarios),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
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
    assert validation["affected_scenarios"] == [_key(NAMES[0])]
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
        "simulate.services.harness_versioning.candidate_promotion.set_defaults",
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


def test_once_validate_before_save_is_required_a_plain_save_is_refused(
    user, workspace, storage, auth_client, settings
):
    """The cut-over for when the web review drawer ships: every save must go through Validate."""
    settings.HARNESS_REQUIRE_VALIDATE_BEFORE_SAVE = True
    environment = _with_world(user, workspace, "cand-required", storage)
    _two_actors(auth_client, environment)
    refused = _plain_save(auth_client, environment)
    assert (
        refused.status_code == 409 and refused.json()["error"] == "validation_required"
    )
    assert drafts.open_draft(environment, "environment") is not None


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
        _save(
            auth_client,
            environment,
            candidates,
            digest=digest,
            name="Manager approves discounts",
        ).json()
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
    from simulate.services.harness_versioning import drafts as module

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(module, "validation_available", lambda environment: False)
        saved = _plain_save(auth_client, environment, digest=digest)
    assert saved.status_code == 200, saved.content
    assert [one["id"] for one in _saved_actors(environment)] == ["manager"]


# --- step 6: cleanup -----------------------------------------------------------------------------------------------


def test_old_candidates_are_discarded(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.candidate_promotion import (
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
    from simulate.services.harness_versioning.review_saved import carry_untouched_proofs

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
    # A change names the scenario by its folder, the proof by its key: still the same scenario.
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=suite, environment_version=parent
    ).update(
        proofs={"duplicate-refund": {"holds": True}, "other-case": {"holds": True}}
    )
    third = HarnessEnvironmentVersion.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        status="discarded",
        origin="manual",
        hash="sha256:" + "8" * 64,
        archive_object_key="y",
        archive_sha256="0" * 64,
        archive_size=1,
        parent=parent,
    )
    carry_untouched_proofs(
        parent,
        third,
        suite,
        [{"decision": "accepted", "affects": ["duplicate_refund"]}],
    )
    assert sorted(
        HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=suite, environment_version=third
        ).proofs
    ) == ["other-case"]


def test_validate_returns_202_with_job(user, workspace, storage, auth_client):
    from simulate.models import HostedHarnessJob, TestExecution

    environment = _with_world(user, workspace, "cand-job", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    validated = _validate(auth_client, environment, digest)
    assert validated.status_code == 202, validated.content
    body = _result(validated)
    assert body["validation"]["state"] == "validating"
    job = HostedHarnessJob.no_workspace_objects.get(id=body["validation"]["job_id"])
    metadata = job.payload["metadata"]
    assert (metadata["certify_only"], metadata["validation_purpose"]) == (True, "save")
    # The job certifies the default agent with the candidate pairing, and nothing else.
    environment.refresh_from_db()
    assert (
        str(job.agent_version_id),
        str(job.environment_version_id),
        str(job.suite_version_id),
    ) == (
        str(environment.agent_version_id),
        body["candidates"]["environment_version_id"],
        body["candidates"]["suite_version_id"],
    )
    assert "draft_validation" not in metadata
    assert not TestExecution.objects.filter(id=job.test_execution_id).exists()
    # Validating the same content again returns the same job.
    again = _result(_validate(auth_client, environment, digest))
    assert again["validation"]["job_id"] == body["validation"]["job_id"]


def test_drafts_carry_the_candidates_as_save_takes_them(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-poll", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    polled = _drafts(auth_client, environment)["candidates"]

    assert polled == candidates
    saved = _save(auth_client, environment, polled, digest=digest)
    assert saved.status_code == 200, saved.content


def test_a_failed_pairing_is_checked_again_by_a_new_validate(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-retry", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    first = _result(_validate(auth_client, environment, digest))
    from simulate.models import HostedHarnessJob

    HostedHarnessJob.no_workspace_objects.filter(
        id=first["validation"]["job_id"]
    ).update(state="failed")
    _certify(
        environment,
        state=HarnessAssembledBundle.State.FAILED,
        error={
            "code": "certify_incomplete",
            "message": "no bundle",
            "source": "validation",
        },
    )
    assert _drafts(auth_client, environment)["validation"]["state"] == "failed"

    again = _validate(auth_client, environment, digest)
    assert again.status_code == 202, again.content
    body = _result(again)
    assert body["candidates"] == first["candidates"]
    assert body["validation"]["state"] == "validating"
    assert body["validation"]["job_id"] not in (None, first["validation"]["job_id"])


def test_a_job_ended_before_any_check_reads_retry_not_failed(
    user, workspace, storage, auth_client
):
    from simulate.models import HostedHarnessJob

    environment = _with_world(user, workspace, "cand-retry-state", storage)
    _two_actors(auth_client, environment)
    first = _result(_validate(auth_client, environment))
    # The sandbox was lost: the job ended and the pairing's bundle is still pending.
    HostedHarnessJob.no_workspace_objects.filter(
        id=first["validation"]["job_id"]
    ).update(state="failed")

    validation = _drafts(auth_client, environment)["validation"]
    assert (validation["state"], validation["error"]) == ("retry", None)


# --- review fixes: the live set follows the last Validate -------------------------------------------------------


def test_validating_again_after_deciding_lets_the_save_through(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-revalidate", storage)
    _two_actors(auth_client, environment)
    assert _validate(auth_client, environment).status_code == 202
    # Accepting everything composes the same content: the same candidates come back.
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )
    second = _validate(auth_client, environment, digest)
    assert second.status_code == 202, second.content
    environment.refresh_from_db()
    assert live_set(environment)["review_digest"] == digest
    _certify(environment)

    saved = _save(
        auth_client, environment, _result(second)["candidates"], digest=digest
    )
    assert saved.status_code == 200, saved.content


def test_an_undone_write_validated_again_lets_the_save_through(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-undone", storage)
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    first = _result(_validate(auth_client, environment))
    revision = _revisions(auth_client, environment)["environment"]
    assert _add(auth_client, environment, AUDITOR, revision=revision).status_code == 200
    revision = _revisions(auth_client, environment)["environment"]
    removed = auth_client.delete(
        f"{BASE}/{environment.id}/actors/auditor/?expected_revision={revision}"
    )
    assert removed.status_code == 200, removed.content
    # Same content at a later revision: the same candidates, now recorded at the new revision.
    second = _validate(auth_client, environment)
    assert _result(second)["candidates"] == first["candidates"]
    _certify(environment)

    saved = _save(auth_client, environment, first["candidates"])
    assert saved.status_code == 200, saved.content


def test_a_reused_candidate_carries_the_latest_decisions(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-reused-docs", storage)
    _two_actors(auth_client, environment)
    first = _result(_validate(auth_client, environment))
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "accepted"},
    )
    _validate(auth_client, environment, digest)

    candidate = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=first["candidates"]["environment_version_id"]
    )
    assert set(candidate.documents["review"]["decisions"]) == {
        "actor:manager",
        "actor:auditor",
    }


# --- review fixes: a save with nothing decided ------------------------------------------------------------------


def test_a_save_with_nothing_decided_keeps_its_summary(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-undecided-summary", storage)
    _two_actors(auth_client, environment)
    validated = _result(_validate(auth_client, environment))
    _certify(environment)

    saved = _save(auth_client, environment, validated["candidates"])
    assert saved.status_code == 200, saved.content
    version = HarnessEnvironmentVersion.no_workspace_objects.get(
        id=validated["candidates"]["environment_version_id"]
    )
    assert version.summary == "Add actor auditor; Add actor manager"


def test_an_undecided_rule_saved_carries_no_proofs_and_warns_the_next_run(
    user, workspace, storage, auth_client
):
    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_versioning.run_warnings import _needs_revalidation

    environment = _with_world(user, workspace, "cand-undecided-rule", storage)
    parent, suite = environment.environment_version, environment.suite_version
    HarnessSuiteValidation.no_workspace_objects.filter(suite_version=suite).delete()
    HarnessSuiteValidation.no_workspace_objects.create(
        suite_version=suite,
        environment_version=parent,
        status="passed",
        proofs={"a": {"holds": True}},
        proved_at=timezone.now(),
    )
    _chat_rule(environment, storage, user)
    validated = _result(_validate(auth_client, environment))
    _certify(environment)

    saved = _save(auth_client, environment, validated["candidates"])
    assert saved.status_code == 200, saved.content
    environment.refresh_from_db()
    # A rule may touch every scenario: nothing is carried, and every scenario is proved again.
    assert not HarnessSuiteValidation.no_workspace_objects.filter(
        environment_version=environment.environment_version, suite_version=suite
    ).exists()
    members = sorted(
        HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite).values_list(
            "scenario_key", flat=True
        )
    )
    assert (
        _needs_revalidation(environment.environment_version, environment.suite_version)
        == members
    )


# --- review fixes: what a Save refuses --------------------------------------------------------------------------


def test_a_discarded_candidate_is_never_promoted(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-discarded", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    default = environment.environment_version_id
    HarnessEnvironmentVersion.no_workspace_objects.filter(
        id=candidates["environment_version_id"]
    ).update(status="discarded", discard_reason="expired")

    refused = _save(auth_client, environment, candidates, digest=digest)
    assert refused.status_code == 409 and refused.json()["error"] == "not_validated"
    environment.refresh_from_db()
    assert environment.environment_version_id == default


def test_the_sweep_keeps_an_old_candidate_a_recent_validate_holds(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.candidate_promotion import (
        discard_expired_candidates,
    )

    environment = _with_world(user, workspace, "cand-sweep-live", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    # The row was built eight days ago; the Validate that holds it ran just now.
    HarnessEnvironmentVersion.no_workspace_objects.filter(
        id=candidates["environment_version_id"]
    ).update(created_at=timezone.now() - timedelta(days=8))

    assert discard_expired_candidates() == 0
    saved = _save(auth_client, environment, candidates, digest=digest)
    assert saved.status_code == 200, saved.content


def test_a_write_between_validate_and_save_is_refused(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-write-between", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    clerk = {**AUDITOR, "id": "clerk", "name": "Lee Park"}
    added = _add(
        auth_client,
        environment,
        clerk,
        revision=_revisions(auth_client, environment)["environment"],
    )
    assert added.status_code == 200, added.content

    refused = _save(auth_client, environment, candidates, digest=digest)
    assert refused.status_code == 409 and refused.json()["error"] == "draft_changed"


def test_a_decision_changed_after_validate_is_refused(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-decision-between", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    _decided(auth_client, environment, {"actor:auditor": "accepted"})

    refused = _save(auth_client, environment, candidates)
    assert refused.status_code == 409 and refused.json()["error"] == "review_stale"


def test_deciding_needs_the_revision_of_every_open_draft(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-decide-revisions", storage)
    _two_actors(auth_client, environment)

    refused = _decide(
        auth_client, environment, [{"remaining": True, "decision": "accepted"}], {}
    )
    assert refused.status_code == 409 and refused.json()["error"] == "draft_changed"


def test_a_side_row_lost_during_validate_reads_unavailable(
    user, workspace, storage, auth_client, monkeypatch
):
    from simulate.services.harness_versioning import validation

    environment = _with_world(user, workspace, "cand-lost-side-row", storage)
    _two_actors(auth_client, environment)
    monkeypatch.setattr(validation, "start_validation", lambda *args, **kwargs: None)

    validated = _validate(auth_client, environment)
    assert validated.status_code == 200, validated.content
    assert _result(validated) == {
        "validation": {"state": "unavailable"},
        "candidates": None,
    }
    assert not HarnessEnvironmentVersion.no_workspace_objects.filter(
        job=environment, status="candidate"
    ).exists()


def test_the_daily_sweep_is_scheduled():
    from tfc.temporal.schedules.simulate import SIMULATE_SCHEDULES

    [sweep] = [
        one
        for one in SIMULATE_SCHEDULES
        if one.schedule_id == "discard-expired-harness-candidates"
    ]
    assert (sweep.activity_name, sweep.interval_seconds, sweep.queue) == (
        "discard_expired_harness_candidates",
        86400,
        "default",
    )


def _check_unavailable_on_our_side(environment, monkeypatch, *, template="tpl:1"):
    """The last check ended on our side for a cause the same template repeats: its job ended, its bundle pending."""
    from simulate.models import HostedHarnessJob
    from simulate.services.harness_versioning import validation as pair_validation

    monkeypatch.setattr(pair_validation, "_template", lambda: template)
    environment.refresh_from_db()
    entry = live_set(environment)
    if entry.get("job_id"):
        HostedHarnessJob.no_workspace_objects.filter(id=entry["job_id"]).update(
            state="failed"
        )
    HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id, assembly_key=entry["assembly_key"]
    ).update(
        state=HarnessAssembledBundle.State.PENDING,
        error={
            "last_attempt": {
                "code": "check_unavailable",
                "cause": "sandbox_create_failed",
                "template": template,
            }
        },
    )


def test_a_check_unavailable_on_our_side_lets_the_validated_changes_save_uncertified(
    user, workspace, storage, auth_client, monkeypatch
):
    environment = _with_world(user, workspace, "cand-unavailable", storage)
    _two_actors(auth_client, environment)
    digest = _decided(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    first = _result(_validate(auth_client, environment, digest))
    _check_unavailable_on_our_side(environment, monkeypatch)
    # The set says so at once: no second Validate only to learn the check could not run.
    assert (
        _drafts(auth_client, environment)["validation"]["state"] == "check_unavailable"
    )

    saved = _save(auth_client, environment, first["candidates"], digest=digest)
    assert saved.status_code == 200, saved.content
    environment.refresh_from_db()
    assert (
        str(environment.environment_version_id)
        == (first["candidates"]["environment_version_id"])
    )


def test_a_check_that_failed_on_our_side_under_an_older_template_still_reads_retry(
    user, workspace, storage, auth_client, monkeypatch
):
    """A cause the current template would not repeat is worth trying again: retry, and Save waits."""
    from simulate.services.harness_versioning import validation as pair_validation

    environment = _with_world(user, workspace, "cand-unavailable-old", storage)
    _two_actors(auth_client, environment)
    first = _result(_validate(auth_client, environment))
    _check_unavailable_on_our_side(environment, monkeypatch, template="tpl:old")
    monkeypatch.setattr(pair_validation, "_template", lambda: "tpl:new")
    assert _drafts(auth_client, environment)["validation"]["state"] == "retry"

    refused = _save(auth_client, environment, first["candidates"])
    assert refused.status_code == 409, refused.content
    assert refused.json()["error"] == "not_validated"


def test_a_save_key_used_for_one_candidate_set_is_refused_for_another(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "cand-key-bound", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    assert _save(auth_client, environment, candidates, digest=digest).status_code == 200

    other = {**candidates, "environment_version_id": str(uuid.uuid4())}
    reused = _save(auth_client, environment, other, digest=digest)
    assert reused.status_code == 409, reused.content
    assert reused.json()["error"] == "idempotency_key_reused"


def test_a_candidate_save_needs_a_name(user, workspace, storage, auth_client):
    environment = _with_world(user, workspace, "cand-name-needed", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )

    for name in ("", "   "):
        refused = _save(auth_client, environment, candidates, digest=digest, name=name)
        assert refused.status_code == 400, refused.content
        assert refused.json()["error"] == "name_required"
    assert drafts.open_draft(environment, "environment") is not None


def test_a_repeated_save_answers_the_same_even_while_chat_works(
    user, workspace, storage, auth_client, monkeypatch
):
    from simulate.services.harness_versioning import candidate_promotion
    from simulate.services.hosted_harness import HostedHarnessError

    environment = _with_world(user, workspace, "cand-replay-first", storage)
    _two_actors(auth_client, environment)
    candidates, digest = _validated(
        auth_client,
        environment,
        {"actor:manager": "accepted", "actor:auditor": "rejected"},
    )
    saved = _save(auth_client, environment, candidates, digest=digest)
    assert saved.status_code == 200, saved.content

    def working(_):
        raise HostedHarnessError("chat_working", "busy", status_code=409)

    monkeypatch.setattr(candidate_promotion, "require_chat_idle", working)
    again = _save(auth_client, environment, candidates, digest=digest)
    assert (again.status_code, again.json()) == (200, saved.json())


def test_the_sweep_drops_an_old_set_that_named_only_active_versions(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.candidate_promotion import (
        discard_expired_candidates,
    )
    from simulate.services.harness_versioning.candidate_sets import (
        VALIDATION_KEY,
        store_set,
    )

    environment = _with_world(user, workspace, "cand-sweep-active", storage)
    store_set(
        environment,
        {
            "environment_version_id": str(environment.environment_version_id),
            "suite_version_id": str(environment.suite_version_id),
            "at": (timezone.now() - timedelta(days=8)).isoformat(),
        },
    )

    discard_expired_candidates()
    environment.refresh_from_db()
    assert VALIDATION_KEY not in environment.payload["metadata"]

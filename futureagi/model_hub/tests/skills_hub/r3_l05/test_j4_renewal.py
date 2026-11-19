"""J4: explicit reviewer renewal after precondition races (synthetic fixtures).

A race makes an approved packet stale. The assigned reviewer explicitly renews:
the next immutable revision keeps the author's candidate, label, target binding
and evidence selection, reports every changed precondition, starts pending and
can only execute after a new decision. Nothing is refreshed silently.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.models.skills_hub_r3 import HubSkillReleasePacket
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.r3.human_review import append_annotation
from model_hub.services.skills_hub.r3.release_requests import (
    release_renewal_preview,
    renew_release_request,
)
from model_hub.services.skills_hub.r3.review_inbox import (
    list_review_inbox,
    mark_review_seen,
)
from model_hub.services.skills_hub.v11.governance import set_label_protection
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

actors = fixtures.actors
candidate = fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)


def _binding(author, skill, config):
    return HubSkillContextBinding.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        context=skill,
        name="runtime",
        kind="runtime",
        selector={"label": "production"},
        update_mode="new_thread",
        profile="test",
        profile_version="1",
        configuration=config,
        configuration_digest=canonical_digest(config),
        expected_report_interval_seconds=60,
    )


def _approved(actors, skill, version, runs, binding=None):
    author, reviewer = actors
    target = (
        {
            "binding_id": str(binding.pk),
            "environment_id": None,
            "revision": binding.revision,
            "configuration_digest": binding.configuration_digest,
        }
        if binding
        else None
    )
    review = fixtures.preview(
        author,
        skill,
        version,
        target=target,
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
            for run in runs
        ],
    )
    request = fixtures.submit(author, reviewer, review)
    fixtures.approve(reviewer, request, review)
    return request, review


def _renew(reviewer, request_id):
    preview = release_renewal_preview(principal=reviewer, request_id=request_id)
    receipt = renew_release_request(
        principal=reviewer,
        request_id=request_id,
        revision=preview["revision"],
        expected_fingerprint=preview["fingerprint"],
        idempotency_key=uuid4(),
    )
    return preview, receipt


def _items(changes):
    return sorted(
        change["item"].split(".")[0] + "." + change["item"].split(".")[-1]
        for change in changes
    )


@pytest.mark.parametrize("race", ["annotation", "configuration", "policy"])
def test_reviewer_renewal_after_race_requires_new_decision(
    actors, hub_skill, candidate, hub_actor, race
):
    author, reviewer = actors
    run = fixtures.text_run(author, hub_skill, candidate[1])
    binding = _binding(author, hub_skill, {"fixture": "v1"})
    request, review = _approved(actors, hub_skill, candidate[1], [run], binding)
    request_id = UUID(request["id"])
    if race == "annotation":
        append_annotation(
            principal=author,
            source="skills_text",
            source_id=run.pk,
            annotation_revision=0,
            verdict="agree",
            check_id=None,
            attempt_id=None,
            cited_text="",
            comment="Late confirmation.",
        )
    elif race == "configuration":
        binding.configuration = {"fixture": "v2"}
        binding.configuration_digest = canonical_digest(binding.configuration)
        binding.revision += 1
        binding.save()
    else:
        hub_skill.refresh_from_db()
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="staging",
            version_number=candidate[0].number,
        )
    with pytest.raises(SkillsHubError):
        fixtures.execute(reviewer, request, review)
    preview, receipt = _renew(reviewer, request_id)
    expected = {
        "annotation": {"evidence.annotation_revision"},
        "configuration": {"transition.expected_impact_fingerprint", "target.target"},
        "policy": {"transition.expected_release_revision"},
    }[race]
    assert set(_items(preview["changes"])) == expected
    assert preview["removed_evidence"] == []
    assert receipt["revision"] == request["revision"] + 1
    assert receipt["status"] == "pending"
    assert receipt["renewed_by"] == str(reviewer.user_id)
    packet = HubSkillReleasePacket.objects.get(
        request_id=request_id, revision=receipt["revision"]
    )
    # The author's selections survive; only preconditions are rebased.
    assert packet.packet["candidate_version_id"] == str(candidate[1].pk)
    assert packet.packet["target"]["binding_id"] == str(binding.pk)
    assert [ref["source_id"] for ref in packet.packet["evidence"]] == [str(run.pk)]
    # The old approval never carries forward.
    with pytest.raises(SkillsHubError) as old:
        fixtures.execute(reviewer, request, review)
    assert old.value.code == "review_conflict"
    renewed = {"id": request["id"], "revision": receipt["revision"]}
    current = {"fingerprint": preview["fingerprint"]}
    with pytest.raises(SkillsHubError) as unapproved:
        fixtures.execute(reviewer, renewed, current)
    assert unapproved.value.code == "review_conflict"
    fixtures.approve(reviewer, renewed, current)
    result = fixtures.execute(reviewer, renewed, current)
    assert result["post_move_state"]["version"] == candidate[1].number
    assert (
        result["currentness_at_action"]["packet_fingerprint"] == preview["fingerprint"]
    )


def test_expiry_keeps_metadata_row_and_renewal_removes_only_expired_evidence(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    older = fixtures.text_run(author, hub_skill, candidate[1])
    newer = fixtures.text_run(author, hub_skill, candidate[1])
    request, review = _approved(actors, hub_skill, candidate[1], [older, newer])
    request_id = UUID(request["id"])
    older.payload_expires_at = timezone.now() - timedelta(seconds=1)
    older.save(update_fields=["payload_expires_at"])
    with pytest.raises(SkillsHubError) as refused:
        fixtures.execute(reviewer, request, review)
    assert refused.value.code == "evidence_expired"
    inbox = list_review_inbox(principal=reviewer)
    assert inbox["count"] == 1
    row = inbox["results"][0]
    assert row["renewal_required"] is True
    assert "packet" not in row
    mark_review_seen(principal=reviewer, request_id=request_id, revision=1)
    preview, receipt = _renew(reviewer, request_id)
    assert preview["removed_evidence"] == [
        {
            "source": "skills_text",
            "source_id": str(older.pk),
            "reason": "evidence_expired",
        }
    ]
    assert [ref["source_id"] for ref in preview["packet"]["evidence"]] == [
        str(newer.pk)
    ]
    assert (
        list_review_inbox(principal=reviewer)["results"][0]["renewal_required"] is False
    )
    renewed = {"id": request["id"], "revision": receipt["revision"]}
    current = {"fingerprint": preview["fingerprint"]}
    fixtures.approve(reviewer, renewed, current)
    assert fixtures.execute(reviewer, renewed, current)["post_move_state"][
        "version"
    ] == (candidate[1].number)


def test_inbox_marks_approved_request_paused_after_rule_change(
    actors, hub_skill, candidate
):
    _, reviewer = actors
    request, _ = _approved(actors, hub_skill, candidate[1], [])

    def assigned_row(status):
        [row] = list_review_inbox(principal=reviewer, status=status)["results"]
        assert row["id"] == request["id"]
        return row

    assert not assigned_row("approved")["renewal_required"]
    hub_skill.refresh_from_db()
    original = list(hub_skill.protected_label_names)
    changed = set_label_protection(
        principal=reviewer,
        skill_id=hub_skill.pk,
        protected_label_names=[*original, "inbox-guard"],
        expected_release_revision=hub_skill.release_revision,
    )
    set_label_protection(
        principal=reviewer,
        skill_id=hub_skill.pk,
        protected_label_names=original,
        expected_release_revision=changed["release_revision"],
    )
    # The visible rule is back at its old value, but the decision is not current.
    assert assigned_row("approved")["renewal_required"]
    _renew(reviewer, UUID(request["id"]))
    assert not assigned_row("pending")["renewal_required"]


def test_renewal_actor_currentness_and_cas(actors, hub_skill, candidate, hub_actor):
    author, reviewer = actors
    request, review = _approved(actors, hub_skill, candidate[1], [])
    request_id = UUID(request["id"])
    with pytest.raises(SkillsHubError) as current:
        release_renewal_preview(principal=reviewer, request_id=request_id)
    assert current.value.code == "review_current"
    hub_skill.refresh_from_db()
    set_label(skill=hub_skill, actor=hub_actor, name="staging", version_number=1)
    with pytest.raises(SkillsHubError) as own:
        release_renewal_preview(principal=author, request_id=request_id)
    assert own.value.code == "permission_denied"
    preview = release_renewal_preview(principal=reviewer, request_id=request_id)
    hub_skill.refresh_from_db()
    set_label(skill=hub_skill, actor=hub_actor, name="canary", version_number=1)
    with pytest.raises(SkillsHubError) as moved:
        renew_release_request(
            principal=reviewer,
            request_id=request_id,
            revision=preview["revision"],
            expected_fingerprint=preview["fingerprint"],
            idempotency_key=uuid4(),
        )
    assert moved.value.code == "review_stale"
    with pytest.raises(SkillsHubError) as revision:
        renew_release_request(
            principal=reviewer,
            request_id=request_id,
            revision=preview["revision"] + 1,
            expected_fingerprint=preview["fingerprint"],
            idempotency_key=uuid4(),
        )
    assert revision.value.code == "review_conflict"
    key = uuid4()
    again = release_renewal_preview(principal=reviewer, request_id=request_id)
    body = {
        "principal": reviewer,
        "request_id": request_id,
        "revision": again["revision"],
        "expected_fingerprint": again["fingerprint"],
    }
    first = renew_release_request(**body, idempotency_key=key)
    assert renew_release_request(**body, idempotency_key=key) == first
    assert HubSkillReleasePacket.objects.filter(request_id=request_id).count() == 2


def test_renewal_http_contract(actors, hub_skill, candidate, organization, workspace):
    author, reviewer = actors
    run = fixtures.text_run(author, hub_skill, candidate[1])
    request, _ = _approved(actors, hub_skill, candidate[1], [run])
    append_annotation(
        principal=author,
        source="skills_text",
        source_id=run.pk,
        annotation_revision=0,
        verdict="agree",
        check_id=None,
        attempt_id=None,
        cited_text="",
        comment="Late confirmation.",
    )
    key = OrgApiKey.objects.create(
        name="Synthetic reviewer",
        type="user",
        user_id=reviewer.user_id,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    path = f"/model-hub/skills/release-requests/{request['id']}/renewal/"
    shown = client.get(path)
    assert shown.status_code == 200, shown.data
    body = shown.data["result"]
    assert body["status"] == "approved"
    assert body["subject_id"] == str(hub_skill.pk)
    assert [change["after"] for change in body["changes"]] == [1]
    renewed = client.post(
        path,
        {"revision": body["revision"], "expected_fingerprint": body["fingerprint"]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert renewed.status_code == 200, renewed.data
    assert renewed.data["result"]["revision"] == body["revision"] + 1
    assert renewed.data["result"]["status"] == "pending"


def test_review_responses_are_never_cacheable(
    actors, hub_skill, candidate, organization, workspace
):
    # A heuristically cached 410 detail would hide the renewed revision from
    # the reviewer's browser; success and error responses are both no-store.
    author, reviewer = actors
    older = fixtures.text_run(author, hub_skill, candidate[1])
    newer = fixtures.text_run(author, hub_skill, candidate[1])
    request, _ = _approved(actors, hub_skill, candidate[1], [older, newer])
    key = OrgApiKey.objects.create(
        name="Synthetic reviewer",
        type="user",
        user_id=reviewer.user_id,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    path = f"/model-hub/skills/release-requests/{request['id']}/"
    current = client.get(path)
    assert current.status_code == 200, current.data
    assert current["Cache-Control"] == "private, no-store"
    older.payload_expires_at = timezone.now() - timedelta(seconds=1)
    older.save(update_fields=["payload_expires_at"])
    expired = client.get(path)
    assert expired.status_code == 410
    assert expired["Cache-Control"] == "private, no-store"
    renewal = client.get(path + "renewal/")
    assert renewal["Cache-Control"] == "private, no-store"
    body = renewal.data["result"]
    renewed = client.post(
        path + "renewal/",
        {"revision": body["revision"], "expected_fingerprint": body["fingerprint"]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert renewed.status_code == 200, renewed.data
    after = client.get(path)
    assert after.status_code == 200, after.data
    assert after["Cache-Control"] == "private, no-store"
    assert [ref["source_id"] for ref in after.data["result"]["packet"]["evidence"]] == [
        str(newer.pk)
    ]

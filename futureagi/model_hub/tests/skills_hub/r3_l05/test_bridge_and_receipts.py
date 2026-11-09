"""Shared existing-job bridge and durable recovery receipts."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub_v11 import HubSkillModelCall
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.spend_policy import (
    close_admission,
    mark_paid_call_sent,
    release_unsent_call,
    reserve_admission,
    reserve_paid_call,
    reserve_text_call,
    reserve_text_job,
    settle_paid_call,
    spend_summary,
    text_call_reservation_id,
    update_spend_policy,
)
from model_hub.services.skills_hub.types import FileInput
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as reviews

actors = reviews.actors
candidate = reviews.candidate
pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_text_job_and_call_use_same_authority(actors, hub_skill, candidate):
    author, _ = actors
    update_spend_policy(
        principal=author,
        revision=0,
        enabled=True,
        period_limit_usd="4",
        idempotency_key=uuid4(),
    )
    run = reviews.text_run(author, hub_skill, candidate[1])
    run.job.max_estimated_cost_usd = Decimal("3")
    run.job.save(update_fields=["max_estimated_cost_usd"])
    with transaction.atomic():
        root = reserve_text_job(job=run.job)
        call = HubSkillModelCall.objects.create(
            job=run.job,
            kind="task",
            sequence=1,
            request_digest="synthetic-call",
            reserved_tokens=100,
            reserved_estimate_usd=Decimal("2"),
        )
        row = reserve_text_call(call=call)
    assert root.operation_id == run.pk and row.operation_id == call.pk
    assert text_call_reservation_id(call=call) == row.pk
    with pytest.raises(SkillsHubError) as caught:
        reserve_admission(
            principal=author,
            source="simulation_native",
            source_id=uuid4(),
            operation_id=uuid4(),
            ceiling_usd="2",
        )
    assert caught.value.code == "budget_exhausted"
    release_unsent_call(scope=author.scope, reservation_id=row.pk, generation=0)
    close_admission(scope=author.scope, admission_id=root.pk)
    assert Decimal(spend_summary(principal=author)["committed_usd"]) == 0


def test_unknown_live_senders_keep_shared_capacity(actors):
    author, _ = actors
    update_spend_policy(
        principal=author,
        revision=0,
        enabled=True,
        period_limit_usd="10",
        idempotency_key=uuid4(),
    )
    root = reserve_admission(
        principal=author,
        source="simulation_native",
        source_id=uuid4(),
        operation_id=uuid4(),
        ceiling_usd="8",
    )
    calls = [
        reserve_paid_call(
            principal=author,
            admission_id=root.pk,
            operation_id=uuid4(),
            kind="tool",
            allowance_usd="1",
            generation=1,
        )
        for _ in range(5)
    ]
    for row in calls[:4]:
        mark_paid_call_sent(principal=author, reservation_id=row.pk, generation=1)
        settle_paid_call(
            scope=author.scope,
            reservation_id=row.pk,
            generation=1,
            actual_usd=None,
            sender_stopped=False,
        )
    with pytest.raises(SkillsHubError) as caught:
        mark_paid_call_sent(principal=author, reservation_id=calls[4].pk, generation=1)
    assert caught.value.code == "skills_capacity_exceeded"
    with pytest.raises(SkillsHubError):
        release_unsent_call(
            scope=author.scope, reservation_id=calls[0].pk, generation=1
        )
    settle_paid_call(
        scope=author.scope,
        reservation_id=calls[0].pk,
        generation=1,
        actual_usd=None,
        sender_stopped=True,
    )
    mark_paid_call_sent(principal=author, reservation_id=calls[4].pk, generation=1)
    assert spend_summary(principal=author)["active_senders"] == 4


def test_expired_payload_does_not_destroy_original_execution_receipt(
    actors, hub_skill, candidate
):
    from model_hub.services.skills_hub.r3.release_requests import get_release_receipt

    author, reviewer = actors
    run = reviews.text_run(author, hub_skill, candidate[1], candidate[0])
    review = reviews.preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    request = reviews.submit(author, reviewer, review)
    reviews.approve(reviewer, request, review)
    key = uuid4()
    receipt = reviews.execute(reviewer, request, review, idempotency_key=key)
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    assert reviews.execute(reviewer, request, review, idempotency_key=key) == receipt
    assert get_release_receipt(principal=author, request_id=request["id"]) == receipt


def test_same_origin_endpoint_change_is_detected_without_secret_echo():
    from model_hub.services.skills_hub.r3.capability_changes import capability_changes

    result = capability_changes(
        before=[
            {"path": "refs.md", "content": "https://api.example.test/read?token=old"}
        ],
        after=[
            {"path": "refs.md", "content": "https://api.example.test/write?token=new"}
        ],
    )
    assert len(result["changes"]) == 1
    assert result["changes"][0]["change"] == "modified"
    assert "token=" not in str(result)


def test_mixed_scalar_metadata_keys_do_not_crash_static_review():
    from model_hub.services.skills_hub.r3.capability_changes import capability_changes

    files: list[FileInput] = [
        {
            "path": "SKILL.md",
            "content": "---\nname: fixture\ndescription: Use for testing.\nhooks: {1: first, event: second}\n---\nBody",
        }
    ]
    result = capability_changes(before=[], after=files)
    assert result["changes"][0]["category"] == "metadata"


def test_late_seen_acknowledgement_cannot_hide_new_packet(actors, hub_skill, candidate):
    from model_hub.services.skills_hub.r3.review_inbox import (
        list_review_inbox,
        mark_review_seen,
    )

    author, reviewer = actors
    review = reviews.preview(author, hub_skill, candidate[1])
    request = reviews.submit(author, reviewer, review)
    mark_review_seen(principal=reviewer, request_id=request["id"], revision=1)
    second = reviews.submit(
        author, reviewer, review, request_id=request["id"], revision=1
    )
    mark_review_seen(principal=reviewer, request_id=request["id"], revision=1)
    assert second["revision"] == 2
    assert list_review_inbox(principal=reviewer)["unread_count"] == 1


def test_annotation_and_execution_serialize_on_the_candidate(
    actors, hub_skill, candidate
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    from model_hub.services.skills_hub.r3.human_review import append_annotation

    author, reviewer = actors
    run = reviews.text_run(author, hub_skill, candidate[1], candidate[0])
    review = reviews.preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    request = reviews.submit(author, reviewer, review)
    reviews.approve(reviewer, request, review)
    barrier = Barrier(2)

    def work(action):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if action == "annotate":
                append_annotation(
                    principal=author,
                    source="skills_text",
                    source_id=run.pk,
                    annotation_revision=0,
                    verdict="disagree",
                    check_id=None,
                    attempt_id=None,
                    cited_text="",
                    comment="A new objection.",
                )
                return "annotated"
            return reviews.execute(reviewer, request, review)
        except SkillsHubError as exc:
            return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        annotated, released = list(pool.map(work, ["annotate", "release"]))
    assert annotated == "annotated"
    if isinstance(released, dict):
        assert (
            released["currentness_at_action"]["evidence"][0]["annotation_revision"] == 0
        )
        assert hub_skill.labels.get(name="production").version_id == candidate[1].pk
    else:
        assert released == "annotation_conflict"
        assert hub_skill.labels.get(name="production").version_id == candidate[0].pk


def test_current_releaser_role_is_independent_of_an_approval(
    actors, hub_skill, candidate
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    author, reviewer = actors
    hub_skill.protected_label_names = ["production"]
    hub_skill.save(update_fields=["protected_label_names"])
    review = reviews.preview(author, hub_skill, candidate[1])
    request = reviews.submit(author, reviewer, review)
    reviews.approve(reviewer, request, review)
    member = OrganizationMembership.no_workspace_objects.get(
        user_id=reviewer.user_id, organization_id=reviewer.scope.organization_id
    )
    member.level, member.role = Level.MEMBER, OrganizationRoles.MEMBER
    member.save(update_fields=["level", "role"])
    WorkspaceMembership.no_workspace_objects.update_or_create(
        workspace_id=reviewer.scope.workspace_id,
        user_id=reviewer.user_id,
        defaults={
            "is_active": True,
            "level": Level.WORKSPACE_MEMBER,
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "organization_membership": member,
        },
    )
    with pytest.raises(SkillsHubError) as caught:
        reviews.execute(reviewer, request, review)
    assert caught.value.code == "protected_label_forbidden"
    assert hub_skill.labels.get(name="production").version_id == candidate[0].pk
    reviews.execute(author, request, review)


def test_wrong_job_kind_cannot_be_read_as_text_evidence(actors, hub_skill, candidate):
    from model_hub.services.skills_hub.r3.human_review import evidence_reference

    author, _ = actors
    run = reviews.text_run(author, hub_skill, candidate[1])
    run.job.kind = "assist"
    run.job.save(update_fields=["kind"])
    with pytest.raises(SkillsHubError) as caught:
        evidence_reference(principal=author, source="skills_text", source_id=run.pk)
    assert caught.value.code == "resource_not_found"


def test_quarantine_after_approval_denies_execution(actors, hub_skill, candidate):
    from model_hub.models.skills_hub_v12 import HubSkillContextQuarantine

    author, reviewer = actors
    review = reviews.preview(author, hub_skill, candidate[1])
    request = reviews.submit(author, reviewer, review)
    reviews.approve(reviewer, request, review)
    HubSkillContextQuarantine.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        version=candidate[1],
        revision=1,
        active=True,
        reason="Synthetic safety control",
    )
    with pytest.raises(SkillsHubError) as caught:
        reviews.execute(reviewer, request, review)
    assert caught.value.code == "context_quarantined"
    assert hub_skill.labels.get(name="production").version_id == candidate[0].pk
    assert hub_skill.label_events.count() == 1


def test_serializer_validated_packet_round_trips_through_submission(
    actors, hub_skill, candidate
):
    from model_hub.serializers.skills_hub_r3 import ReleasePacket

    author, reviewer = actors
    run = reviews.text_run(author, hub_skill, candidate[1])
    review = reviews.preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
        external_pipeline={
            "url": "https://ci.example.test/run/123",
            "configuration_identity": "fixture-commit",
            "decision": "Operator reviewed external regression output.",
            "trust": "operator_recorded_unimported",
        },
    )
    with timezone.override("Asia/Kolkata"):
        serializer = ReleasePacket(data=review["packet"])
        assert serializer.is_valid(), serializer.errors
        parsed = {
            "packet": serializer.validated_data,
            "fingerprint": review["fingerprint"],
        }
        request = reviews.submit(author, reviewer, parsed)
    reviews.approve(reviewer, request, review)
    reviews.execute(reviewer, request, review)


@pytest.mark.parametrize("label", ["latest", "UpperCase", "../escape"])
def test_invalid_labels_fail_before_creating_review(
    actors, hub_skill, candidate, label
):
    from model_hub.services.skills_hub.r3.release_requests import preview_release_packet

    author, _ = actors
    with pytest.raises(SkillsHubError) as caught:
        preview_release_packet(
            principal=author,
            resource_id=hub_skill.pk,
            kind="skill",
            candidate_version_id=candidate[1].pk,
            label=label,
        )
    assert caught.value.code == "invalid_label"

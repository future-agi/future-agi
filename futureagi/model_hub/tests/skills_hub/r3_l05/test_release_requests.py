"""Real author/reviewer handoff, CAS and exact evidence without paid execution."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from model_hub.models.skills_hub_r3 import (
    HubSkillReleaseDecision,
    HubSkillReleasePacket,
    HubSkillReleaseRequest,
)
from model_hub.models.skills_hub_v11 import HubSkillJob, HubSkillTestRun
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.r3.human_review import (
    append_annotation,
    evidence_reference,
)
from model_hub.services.skills_hub.r3.release_requests import (
    decide_release_request,
    execute_release_request,
    get_release_request,
    preview_release_packet,
    submit_release_request,
)
from model_hub.services.skills_hub.r3.review_inbox import (
    list_review_inbox,
    mark_review_seen,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def actors(organization, workspace, user):
    other = User.objects.create_user(
        email=f"reviewer-{uuid4()}@example.test",
        name="Reviewer",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=other,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    scope = SkillScope(organization.pk, workspace.pk)
    return SkillPrincipal(scope, user.pk, None, "human"), SkillPrincipal(
        scope, other.pk, None, "human"
    )


@pytest.fixture
def candidate(hub_skill, publish_skill, make_skill_files):
    old = publish_skill(hub_skill, labels=["production"]).version
    new = publish_skill(
        hub_skill, files=make_skill_files(hub_skill.name, body="Changed safe policy.")
    ).version
    return old, new


def text_run(principal, skill, candidate, baseline=None):
    job = HubSkillJob.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill=skill,
        owner_user_id=principal.user_id,
        kind="test",
        status="completed",
        principal_kind="human",
        idempotency_key=uuid4(),
        request_digest=canonical_digest("fixture"),
        workflow_id=f"synthetic-{uuid4()}",
        deadline_at=timezone.now(),
    )
    return HubSkillTestRun.objects.create(
        job=job,
        visibility="workspace",
        profile="quick",
        repetitions=1,
        candidate_digest=candidate.digest,
        candidate={"version_id": str(candidate.pk)},
        snapshot={"cases": []},
        execution_fingerprint=canonical_digest("execution"),
        plan_fingerprint=canonical_digest("plan"),
        execution_configuration={
            "model": {
                "model_id": "fixture",
                "temperature": 0,
                "max_output_tokens": 100,
            },
            "judge": None,
            "harness_version": "test",
            "adapter_version": "test",
            "evaluator_version": "test",
        },
        payload_expires_at=timezone.now() + timedelta(days=1),
        validity="reviewed",
        baseline_version=baseline,
        baseline_event=(
            skill.label_events.filter(label="production").latest("created_at")
            if baseline
            else None
        ),
    )


def preview(author, skill, version, **kwargs):
    return preview_release_packet(
        principal=author,
        resource_id=skill.pk,
        kind=skill.kind,
        candidate_version_id=version.pk,
        label="production",
        **kwargs,
    )


def submit(author, reviewer, review, **kwargs):
    return submit_release_request(
        principal=author,
        packet=review["packet"],
        expected_fingerprint=review["fingerprint"],
        recipient_ids=[reviewer.user_id],
        idempotency_key=kwargs.pop("idempotency_key", uuid4()),
        **kwargs,
    )


def approve(reviewer, request, review):
    return decide_release_request(
        principal=reviewer,
        request_id=UUID(request["id"]),
        revision=request["revision"],
        expected_fingerprint=review["fingerprint"],
        decision="approve",
        comment="Reviewed exact files.",
        idempotency_key=uuid4(),
    )


def execute(actor, request, review, **kwargs):
    return execute_release_request(
        principal=actor,
        request_id=UUID(request["id"]),
        revision=request["revision"],
        expected_fingerprint=review["fingerprint"],
        idempotency_key=kwargs.get("idempotency_key", uuid4()),
    )


def test_real_recipient_arrival_seen_and_authorized_release(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[1])
    request = submit(author, reviewer, review)
    inbox = list_review_inbox(principal=reviewer)
    assert inbox["count"] == inbox["unread_count"] == 1
    assert inbox["results"][0]["id"] == request["id"]
    assert HubSkillReleaseDecision.objects.count() == 0
    mark_review_seen(principal=reviewer, request_id=UUID(request["id"]), revision=1)
    assert list_review_inbox(principal=reviewer)["unread_count"] == 0
    assert HubSkillReleaseDecision.objects.count() == 0
    approve(reviewer, request, review)
    key = uuid4()
    receipt = execute(reviewer, request, review, idempotency_key=key)
    assert receipt["post_move_state"]["version"] == candidate[1].number
    assert (
        receipt["currentness_at_action"]["release_revision"]
        < receipt["post_move_state"]["release_revision"]
    )
    assert execute(reviewer, request, review, idempotency_key=key) == receipt
    assert hub_skill.label_events.count() == 2


def test_pre_move_baseline_currentness_is_not_recomputed_after_move(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    run = text_run(author, hub_skill, candidate[1], candidate[0])
    review = preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    result = execute(reviewer, request, review)
    assert (
        result["currentness_at_action"]["evidence"][0]["currentness"]["baseline"]
        is True
    )
    assert hub_skill.labels.get(name="production").version_id == candidate[1].pk


@pytest.mark.parametrize(
    "change", ["annotation", "expire", "visibility", "configuration"]
)
def test_new_evidence_or_target_invalidates_approval(
    actors, hub_skill, candidate, change
):
    author, reviewer = actors
    run = text_run(author, hub_skill, candidate[1])
    config = {"fixture": "v1"}
    binding = HubSkillContextBinding.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        context=hub_skill,
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
    target = {
        "binding_id": str(binding.pk),
        "environment_id": None,
        "revision": binding.revision,
        "configuration_digest": binding.configuration_digest,
    }
    review = preview(
        author,
        hub_skill,
        candidate[1],
        target=target,
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    if change == "annotation":
        append_annotation(
            principal=author,
            source="skills_text",
            source_id=run.pk,
            annotation_revision=0,
            verdict="disagree",
            check_id=None,
            attempt_id=None,
            cited_text="",
            comment="Needs human review.",
        )
    elif change == "expire":
        run.payload_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=["payload_expires_at"])
    elif change == "visibility":
        run.visibility = "private"
        run.save(update_fields=["visibility"])
    else:
        binding.configuration = {"fixture": "v2"}
        binding.configuration_digest = canonical_digest(binding.configuration)
        binding.revision += 1
        binding.save()
    with pytest.raises(SkillsHubError):
        execute(reviewer, request, review)
    assert hub_skill.labels.get(name="production").version_id == candidate[0].pk
    if change in {"expire", "visibility"}:
        assert list_review_inbox(principal=reviewer)["count"] == 0
        assert list_review_inbox(principal=reviewer)["unread_count"] == 0


def test_role_loss_invalidates_approval_without_granting_release(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[1])
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    OrganizationMembership.no_workspace_objects.filter(user_id=reviewer.user_id).update(
        is_active=False
    )
    with pytest.raises(SkillsHubError):
        execute(author, request, review)
    assert hub_skill.labels.get(name="production").version_id == candidate[0].pk


def test_aba_and_noop_recheck_packet_before_pointer_fast_path(
    actors, hub_skill, candidate, hub_actor
):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[0])
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=candidate[1].number,
    )
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=candidate[0].number,
    )
    with pytest.raises(SkillsHubError) as caught:
        execute(reviewer, request, review)
    assert caught.value.code == "release_conflict"


def test_changes_requested_is_not_approval(actors, hub_skill, candidate):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[1])
    request = submit(author, reviewer, review)
    decide_release_request(
        principal=reviewer,
        request_id=UUID(request["id"]),
        revision=1,
        expected_fingerprint=review["fingerprint"],
        decision="request_changes",
        comment="Revise the policy.",
        idempotency_key=uuid4(),
    )
    with pytest.raises(SkillsHubError):
        execute(author, request, review)
    with pytest.raises(SkillsHubError):
        approve(reviewer, request, review)
    revised = submit(
        author, reviewer, review, request_id=UUID(request["id"]), revision=1
    )
    assert revised["revision"] == 2
    assert HubSkillReleasePacket.objects.count() == 2
    approve(reviewer, revised, review)
    execute(reviewer, revised, review)


def test_supplied_authoritative_claims_cannot_be_forged(actors, hub_skill, candidate):
    author, reviewer = actors
    run = text_run(author, hub_skill, candidate[1])
    review = preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    forged = deepcopy(review)
    forged["packet"]["evidence"][0]["execution_fingerprint"] = canonical_digest(
        "forged"
    )
    forged["fingerprint"] = canonical_digest(forged["packet"])
    with pytest.raises(SkillsHubError) as caught:
        submit(author, reviewer, forged)
    assert caught.value.code == "review_stale"
    assert HubSkillReleaseRequest.objects.count() == 0


def test_submit_replay_returns_original_after_label_movement(
    actors, hub_skill, candidate, hub_actor
):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[1])
    key = uuid4()
    request = submit(author, reviewer, review, idempotency_key=key)
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=candidate[1].number,
    )
    assert submit(author, reviewer, review, idempotency_key=key) == request
    assert HubSkillReleaseRequest.objects.count() == 1


def test_unassigned_foreign_and_system_principals_do_not_leak(
    actors, hub_skill, candidate, hub_other_workspace
):
    author, reviewer = actors
    request = submit(author, reviewer, preview(author, hub_skill, candidate[1]))
    wrong = SkillPrincipal(
        SkillScope(author.scope.organization_id, hub_other_workspace.pk),
        reviewer.user_id,
        None,
        "human",
    )
    with pytest.raises(SkillsHubError) as caught:
        get_release_request(principal=wrong, request_id=UUID(request["id"]))
    assert caught.value.code == "resource_not_found"
    system = SkillPrincipal(author.scope, None, uuid4(), "system_key")
    with pytest.raises(SkillsHubError) as caught:
        list_review_inbox(principal=system)
    assert caught.value.code == "principal_not_supported"


def test_external_pipeline_is_attributed_and_never_imported(
    actors, hub_skill, candidate
):
    author, _ = actors
    review = preview(
        author,
        hub_skill,
        candidate[1],
        external_pipeline={
            "url": "https://ci.example.test/run/1",
            "configuration_identity": "commit-1",
            "decision": "Operator reviewed the retained checks.",
            "trust": "operator_recorded_unimported",
            "recorded_by": str(uuid4()),
        },
    )
    assert review["packet"]["external_pipeline"]["recorded_by"] == str(author.user_id)
    assert review["packet"]["evidence"] == []


def test_private_evidence_is_never_implicitly_shared(actors, hub_skill, candidate):
    author, _ = actors
    run = text_run(author, hub_skill, candidate[1])
    run.visibility = "private"
    run.save(update_fields=["visibility"])
    with pytest.raises(SkillsHubError) as caught:
        preview(
            author,
            hub_skill,
            candidate[1],
            evidence=[
                {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
            ],
        )
    assert caught.value.code == "evidence_share_required"
    run.refresh_from_db()
    assert run.visibility == "private"


def test_unrelated_binding_does_not_stale_review(actors, hub_skill, candidate):
    author, reviewer = actors
    review = preview(author, hub_skill, candidate[1])
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    HubSkillContextBinding.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        context=hub_skill,
        name="staging-runtime",
        kind="runtime",
        selector={"label": "staging"},
        update_mode="new_thread",
        profile="test",
        profile_version="1",
        expected_report_interval_seconds=60,
    )
    execute(reviewer, request, review)


def test_annotation_is_append_only_cas_and_machine_grades_unchanged(
    actors, hub_skill, candidate
):
    author, _ = actors
    run = text_run(author, hub_skill, candidate[1])
    initial = deepcopy(run.summary)
    append_annotation(
        principal=author,
        source="skills_text",
        source_id=run.pk,
        annotation_revision=0,
        verdict="cannot_assess",
        check_id=None,
        attempt_id=None,
        cited_text="",
        comment="Missing context.",
    )
    with pytest.raises(SkillsHubError) as caught:
        append_annotation(
            principal=author,
            source="skills_text",
            source_id=run.pk,
            annotation_revision=0,
            verdict="agree",
            check_id=None,
            attempt_id=None,
            cited_text="",
            comment="Second opinion.",
        )
    assert caught.value.code == "annotation_conflict"
    run.refresh_from_db()
    assert run.summary == initial
    assert (
        evidence_reference(principal=author, source="skills_text", source_id=run.pk)[
            "annotation_revision"
        ]
        == 1
    )

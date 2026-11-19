"""Native ownership, currentness, sharing and explicit alternate-read guards."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models import HubSkillEvidenceAnnotation, HubSkillNativeEvidence
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.native_evidence import (
    authorize_native_job_payload,
    get_native_evidence,
    ordinary_simulation_jobs,
    purge_native_control_payload,
    resolve_native_evidence_ref,
    share_native_evidence,
    visible_native_evidence,
)
from model_hub.services.skills_hub.v11.inventory import fingerprint
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from simulate.models.hosted_harness import HostedHarnessJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )


@pytest.fixture
def evidence(hub_skill, user, organization, workspace, native_configuration):
    job = HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid4(),
        idempotency_key=str(uuid4()),
        request_digest="sha256:" + "a" * 64,
        schema_version="test-only",
        payload={"synthetic_private_request": "private text"},
        seed=1,
        scenario_count=2,
        artifact_level="full",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(minutes=5),
        state="completed",
    )
    native = native_configuration
    return HubSkillNativeEvidence.objects.create(
        organization=organization,
        workspace=workspace,
        skill=hub_skill,
        owner=user,
        simulation_job=job,
        candidate_digest="sha256:" + "b" * 64,
        execution_fingerprint="sha256:" + "c" * 64,
        configuration={"schema": 1, "digest": fingerprint(native), "native": native},
        planned_matrix=[],
        payload_expires_at=timezone.now() + timedelta(days=1),
        protected_locations=[{"kind": "private_object", "id": "synthetic-location"}],
    )


def kwargs(principal, evidence):
    return {
        "principal": principal,
        "resource_id": evidence.skill_id,
        "kind": "skill",
        "evidence_id": evidence.id,
    }


def ref_kwargs(principal, evidence):
    return {
        **kwargs(principal, evidence),
        "candidate_digest": evidence.candidate_digest,
        "annotation_revision": 0,
    }


def test_private_alternate_reads_deny_other_member_and_system(
    principal, evidence, actor_for_workspace, workspace
):
    other = actor_for_workspace(workspace).user
    stranger = replace(principal, user_id=other.pk)
    assert (
        visible_native_evidence(
            principal=stranger, resource_id=evidence.skill_id, kind="skill"
        ).count()
        == 0
    )
    with pytest.raises(SkillsHubError) as error:
        authorize_native_job_payload(
            principal=stranger, job_id=evidence.simulation_job_id
        )
    assert error.value.code == "resource_not_found"
    with pytest.raises(SkillsHubError) as error:
        authorize_native_job_payload(
            principal=replace(principal, kind="system_key"),
            job_id=evidence.simulation_job_id,
        )
    assert error.value.code == "principal_not_supported"
    assert (
        authorize_native_job_payload(
            principal=principal, job_id=evidence.simulation_job_id
        ).pk
        == evidence.pk
    )


def test_shared_native_still_excluded_from_ordinary_lists_counts_and_joins(
    principal, evidence
):
    shared = share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    assert shared.visibility == "workspace"
    assert not HostedHarnessJob.no_workspace_objects.filter(
        ordinary_simulation_jobs(), pk=evidence.simulation_job_id
    ).exists()
    assert not HubSkillNativeEvidence.objects.filter(
        ordinary_simulation_jobs("simulation_job__"), pk=evidence.id
    ).exists()


def test_shared_read_is_scoped_and_current_membership_required(
    principal, evidence, actor_for_workspace, workspace, other_workspace
):
    from accounts.models.organization_membership import OrganizationMembership

    other = actor_for_workspace(workspace).user
    share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    stranger = replace(principal, user_id=other.id)
    assert (
        get_native_evidence(**kwargs(stranger, evidence), payload=True).id
        == evidence.id
    )
    with pytest.raises(SkillsHubError):
        get_native_evidence(
            **kwargs(
                replace(
                    stranger,
                    scope=SkillScope(
                        principal.scope.organization_id, other_workspace.id
                    ),
                ),
                evidence,
            )
        )
    OrganizationMembership.no_workspace_objects.filter(user=other).update(
        is_active=False
    )
    with pytest.raises(SkillsHubError):
        get_native_evidence(**kwargs(stranger, evidence), payload=True)


def test_release_rejects_private_expired_digest_annotation_and_configuration(
    principal, evidence, user
):
    with pytest.raises(SkillsHubError) as error:
        resolve_native_evidence_ref(**ref_kwargs(principal, evidence))
    assert error.value.code == "evidence_share_required"
    share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    reference = resolve_native_evidence_ref(**ref_kwargs(principal, evidence))
    assert reference["source"] == "simulation_native"
    assert reference["status"] == "completed"
    assert reference["candidate_digest"] == evidence.candidate_digest
    for changes in (
        {"candidate_digest": "sha256:" + "d" * 64},
        {"configuration_digest": "sha256:" + "e" * 64},
        {"annotation_revision": 1},
    ):
        with pytest.raises(SkillsHubError) as error:
            resolve_native_evidence_ref(
                **{**ref_kwargs(principal, evidence), **changes}
            )
        assert error.value.code == "evidence_mismatch"
    HubSkillEvidenceAnnotation.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source="simulation_native",
        source_id=evidence.id,
        revision=1,
        actor=user,
        verdict="disagree",
        cited_text="Synthetic cited result",
        comment="Wrong synthetic tool",
    )
    with pytest.raises(SkillsHubError):
        resolve_native_evidence_ref(**ref_kwargs(principal, evidence))
    assert (
        resolve_native_evidence_ref(
            **{**ref_kwargs(principal, evidence), "annotation_revision": 1}
        )["annotation_revision"]
        == 1
    )
    HubSkillNativeEvidence.objects.filter(pk=evidence.id).update(
        payload_expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(SkillsHubError) as error:
        resolve_native_evidence_ref(
            **{**ref_kwargs(principal, evidence), "annotation_revision": 1}
        )
    assert error.value.code == "evidence_expired"
    # Hash/config receipts remain readable, never the expired private payload.
    assert get_native_evidence(**kwargs(principal, evidence)).id == evidence.id


def test_sharing_does_not_extend_expiry_or_give_admin_ownership(
    principal, evidence, actor_for_workspace, workspace
):
    other = actor_for_workspace(workspace).user
    with pytest.raises(SkillsHubError):
        share_native_evidence(
            **kwargs(principal, evidence), acknowledge_snapshot_sharing=False
        )
    share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    with pytest.raises(SkillsHubError):
        share_native_evidence(
            **kwargs(replace(principal, user_id=other.id), evidence),
            acknowledge_snapshot_sharing=True,
        )
    evidence.refresh_from_db()
    old_expiry = evidence.payload_expires_at
    share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    evidence.refresh_from_db()
    assert evidence.payload_expires_at == old_expiry


def test_incomplete_purge_never_claims_all_projections_deleted(principal, evidence):
    HubSkillNativeEvidence.objects.filter(pk=evidence.id).update(
        payload_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert purge_native_control_payload(evidence_id=evidence.id) is False
    evidence.refresh_from_db()
    evidence.simulation_job.refresh_from_db()
    assert evidence.simulation_job.payload == {}
    assert evidence.payload_purged_at is None
    with pytest.raises(SkillsHubError) as error:
        authorize_native_job_payload(
            principal=principal, job_id=evidence.simulation_job_id
        )
    assert error.value.code == "evidence_expired"


def test_terminal_failure_remains_attached_as_failure(principal, evidence):
    share_native_evidence(
        **kwargs(principal, evidence), acknowledge_snapshot_sharing=True
    )
    HostedHarnessJob.no_workspace_objects.filter(pk=evidence.simulation_job_id).update(
        state="failed"
    )
    with transaction.atomic():
        reference = resolve_native_evidence_ref(
            **ref_kwargs(principal, evidence), lock=True
        )
    assert reference["status"] == "failed"

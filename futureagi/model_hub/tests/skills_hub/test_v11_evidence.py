"""Shared exact-content evidence and complete payload retention removal."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub_v11 import HubSkillModelCall, HubSkillTestEvidence
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.evidence import (
    attach_test_evidence_locked,
    currentness,
    list_test_evidence,
)
from model_hub.services.skills_hub.v11.jobs import finish_job, purge_expired_payloads
from model_hub.services.skills_hub.v11.test_runs import share_test_run
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.tests.skills_hub import test_v11_jobs as job_fixtures

execution = job_fixtures.execution
accepted = job_fixtures.accepted

pytestmark = pytest.mark.django_db(transaction=True)


def test_private_evidence_requires_explicit_share(execution, publish_skill):
    run, _, _ = accepted(execution)
    principal, skill, config, _ = execution
    version = publish_skill(
        skill, files=skill.drafts.get(user_id=principal.user_id).files
    ).version
    finish_job(scope=principal.scope, job_id=run.pk)
    with transaction.atomic(), pytest.raises(SkillsHubError) as caught:
        attach_test_evidence_locked(
            principal=principal, skill=skill, version=version, run_id=run.pk
        )
    assert caught.value.code == "evidence_share_required"
    share_test_run(
        principal=principal, run_id=run.pk, acknowledge_snapshot_sharing=True
    )
    with transaction.atomic():
        evidence = attach_test_evidence_locked(
            principal=principal, skill=skill, version=version, run_id=run.pk
        )
        replay = attach_test_evidence_locked(
            principal=principal, skill=skill, version=version, run_id=run.pk
        )
    assert evidence.pk == replay.pk and HubSkillTestEvidence.objects.count() == 1
    assert evidence.candidate_digest == version.digest
    assert evidence.currentness_at_action["content"] is True
    result = list_test_evidence(
        principal=principal,
        skill_id=skill.id,
        candidate={"kind": "version", "version": version.number},
    )
    assert result["runs"][0]["can_attach"] is True
    assert result["runs"][0]["status"] == "completed"


def test_mismatching_content_and_unrelated_private_run_rejected(
    execution, publish_skill, make_skill_files
):
    run, _, _ = accepted(execution)
    principal, skill, _, _ = execution
    version = publish_skill(
        skill, files=make_skill_files(skill.name, body="Different instructions")
    ).version
    finish_job(scope=principal.scope, job_id=run.pk)
    share_test_run(
        principal=principal, run_id=run.pk, acknowledge_snapshot_sharing=True
    )
    with transaction.atomic(), pytest.raises(SkillsHubError) as caught:
        attach_test_evidence_locked(
            principal=principal, skill=skill, version=version, run_id=run.pk
        )
    assert caught.value.code == "evidence_mismatch"
    with transaction.atomic(), pytest.raises(SkillsHubError) as caught:
        attach_test_evidence_locked(
            principal=principal, skill=skill, version=version, run_id=uuid4()
        )
    assert caught.value.code == "evidence_mismatch"


def test_retention_purges_all_nested_text_but_preserves_model_ids(execution):
    run, _, _ = accepted(execution)
    principal, skill, _, _ = execution
    finish_job(scope=principal.scope, job_id=run.pk)
    run.refresh_from_db()
    attempt = run.hub_skill_attempts.first()
    attempt.answer = "private answer"
    attempt.receipts = [{"path": "private-path"}]
    attempt.check_results = [
        {
            "check_id": "one",
            "category": "answer",
            "verdict": "fail",
            "expected": "private requirement",
            "actual": "private actual",
            "evidence": "private quote",
        }
    ]
    attempt.save()
    call = HubSkillModelCall.objects.create(
        job=run.job,
        attempt=attempt,
        kind="task",
        sequence=1,
        state="completed",
        request_digest="hash",
        requested_model="frozen-requested",
        returned_model="frozen-returned",
        assistant_message={"content": "private answer"},
        tool_receipts=[{"path": "private-path"}],
        finished_at=timezone.now(),
    )
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    assert purge_expired_payloads()["runs"] == 1
    run.refresh_from_db()
    attempt.refresh_from_db()
    call.refresh_from_db()
    assert run.snapshot is None and run.payload_purged_at
    assert attempt.answer is None and attempt.receipts == []
    assert set(attempt.check_results[0]) == {"check_id", "category", "verdict"}
    assert call.assistant_message is None and call.tool_receipts == []
    assert (
        call.requested_model == "frozen-requested"
        and call.returned_model == "frozen-returned"
    )
    assert run.execution_configuration["model"]["model_id"] == "gpt-4o-mini"
    assert (
        currentness(run=run, skill=skill, candidate_digest=run.candidate_digest)[
            "payload_available"
        ]
        is False
    )
    assert purge_expired_payloads()["runs"] == 0


def test_system_key_never_inherits_owner_for_evidence(execution):
    principal, skill, _, _ = execution
    system = SkillPrincipal(principal.scope, principal.user_id, uuid4(), "system_key")
    with pytest.raises(SkillsHubError) as caught:
        list_test_evidence(
            principal=system,
            skill_id=skill.id,
            candidate={"kind": "draft", "revision": 1},
        )
    assert caught.value.code == "principal_not_supported"

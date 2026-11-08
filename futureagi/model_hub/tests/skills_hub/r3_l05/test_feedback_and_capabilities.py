"""Capability-delta, native identity, citation and original-operation regressions."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub_r3 import HubSkillNativeEvidence
from model_hub.models.skills_hub_v11 import HubSkillAssistProposal, HubSkillTestAttempt
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.serializers.skills_hub_r3 import ReleasePacket
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.assist_recovery import (
    list_assist_operations,
    recover_assist_operation,
    require_qualified_assistance,
)
from model_hub.services.skills_hub.r3.capability_changes import capability_changes
from model_hub.services.skills_hub.r3.human_review import (
    append_annotation,
    list_annotations,
    purge_annotation_payload,
)
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as review_fixtures
from model_hub.tests.skills_hub.r3_l05.test_release_requests import (
    approve,
    execute,
    preview,
    submit,
    text_run,
)
from simulate.models.hosted_harness import HostedHarnessJob

actors = review_fixtures.actors
candidate = review_fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)


def test_capability_changes_are_deterministic_and_bind_exact_files(make_skill_files):
    before = make_skill_files()
    after = make_skill_files(
        extra=[{"path": "scripts/tool.py", "content": "# reviewed example"}]
    )
    after[0]["content"] = after[0]["content"].replace(
        "---\n\n", "allowed-tools: Read\n---\n\n"
    )
    after[0][
        "content"
    ] += "\n!`git diff`\nhttps://user:password@example.test/private?token=secret\n"
    result = capability_changes(before=before, after=after)
    assert {x["category"] for x in result["changes"]} == {
        "executable_file",
        "metadata",
        "external_destination",
        "dynamic_shell",
    }
    assert "password" not in str(result) and "token=secret" not in str(result)
    assert result == capability_changes(
        before=list(reversed(before)), after=list(reversed(after))
    )
    assert result["before_digest"] != result["after_digest"]
    assert capability_changes(before=after, after=after)["changes"] == []


def test_body_only_change_does_not_claim_metadata_widening(make_skill_files):
    before = make_skill_files(body="First instruction.")
    after = make_skill_files(body="Revised instruction.")
    assert capability_changes(before=before, after=after)["changes"] == []
    assert "not a sandbox" in capability_changes(before=before, after=after)["notice"]


def test_capability_removal_and_modified_script_are_visible(make_skill_files):
    before = make_skill_files(
        extra=[
            {"path": "scripts/a.sh", "content": "old"},
            {"path": "refs.md", "content": "https://example.test/old"},
        ]
    )
    after = make_skill_files(extra=[{"path": "scripts/a.sh", "content": "new"}])
    assert {
        x["change"] for x in capability_changes(before=before, after=after)["changes"]
    } == {"removed", "modified"}


def test_citations_validate_attempt_check_and_exact_text(actors, hub_skill, candidate):
    author, _ = actors
    run = text_run(author, hub_skill, candidate[1])
    check_id = uuid4()
    attempt = HubSkillTestAttempt.objects.create(
        run=run,
        case_id=uuid4(),
        variant="candidate",
        repeat_index=0,
        schedule_index=0,
        status="completed",
        answer="The result is incorrect.",
        check_results=[
            {
                "check_id": str(check_id),
                "verdict": "fail",
                "category": "answer",
                "evidence": "incorrect",
            }
        ],
    )
    base = {
        "principal": author,
        "source": "skills_text",
        "source_id": run.pk,
        "annotation_revision": 0,
        "verdict": "disagree",
        "attempt_id": attempt.pk,
        "check_id": check_id,
        "comment": "Review the boundary.",
    }
    with pytest.raises(SkillsHubError):
        append_annotation(**base, cited_text="invented quote")
    row = append_annotation(**base, cited_text="incorrect")
    attempt.refresh_from_db()
    assert attempt.check_results[0]["verdict"] == "fail"
    assert row.revision == 1
    assert (
        list_annotations(principal=author, source="skills_text", source_id=run.pk)[
            "count"
        ]
        == 1
    )
    with transaction.atomic():
        purge_annotation_payload(
            source="skills_text",
            source_id=run.pk,
            organization_id=author.scope.organization_id,
            workspace_id=author.scope.workspace_id,
        )
    row.refresh_from_db()
    assert row.verdict == "disagree" and row.cited_text == row.comment == ""


def native_configuration():
    digest = canonical_digest("fixture")
    return {
        "code_commit": "a" * 40,
        "code_digest": digest,
        "entrypoint": "example:run",
        "image_digest": digest,
        "runtime_version": "fixture-1",
        "packages": [{"name": "fixture", "version": "1", "sha256": "a" * 64}],
        "package_lock_digest": digest,
        "model": {"model_id": "fixture", "temperature": 0, "max_output_tokens": 100},
        "connection_id": str(uuid4()),
        "connection_revision": "1",
        "tools_digest": digest,
        "retrieval_fixture_digest": digest,
        "state_schema": "fixture-1",
        "checkpoint_digest": digest,
        "side_effect_policy": "fixture_only",
        "reset_entrypoint": "example:reset",
        "context_mount": "/fi_context/",
        "observable_boundaries": ["model_request", "tool_result", "checkpoint"],
    }


def test_native_only_packet_is_typed_and_requires_exact_runtime_target(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    config = native_configuration()
    job = HostedHarnessJob.no_workspace_objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        run_id=uuid4(),
        idempotency_key=str(uuid4()),
        request_digest=canonical_digest("request"),
        schema_version="fixture-1",
        payload={},
        state="failed",
        seed=1,
        scenario_count=1,
        artifact_level="minimal",
        max_artifact_bytes=1024,
        deadline_at=timezone.now(),
    )
    evidence = HubSkillNativeEvidence.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        skill=hub_skill,
        owner_id=author.user_id,
        simulation_job=job,
        visibility="workspace",
        candidate_digest=candidate[1].digest,
        execution_fingerprint=canonical_digest("native execution"),
        configuration=config,
        planned_matrix=[],
        payload_expires_at=timezone.now() + timedelta(days=1),
    )
    selection = [
        {
            "source": "simulation_native",
            "source_id": evidence.pk,
            "annotation_revision": 0,
        }
    ]
    with pytest.raises(SkillsHubError) as caught:
        preview(author, hub_skill, candidate[1], evidence=selection)
    assert caught.value.code == "target_configuration_changed"
    binding = HubSkillContextBinding.objects.create(
        organization_id=author.scope.organization_id,
        workspace_id=author.scope.workspace_id,
        context=hub_skill,
        name="qualified-runtime",
        kind="runtime",
        selector={"label": "production"},
        update_mode="new_thread",
        profile="fixture",
        profile_version="1",
        expected_report_interval_seconds=60,
        configuration=config,
        configuration_digest=canonical_digest(config),
    )
    target = {
        "binding_id": str(binding.pk),
        "environment_id": None,
        "revision": binding.revision,
        "configuration_digest": binding.configuration_digest,
    }
    review = preview(author, hub_skill, candidate[1], target=target, evidence=selection)
    serializer = ReleasePacket(data=review["packet"])
    assert serializer.is_valid(), serializer.errors
    assert review["packet"]["evidence"][0]["status"] == "failed"
    assert len(review["packet"]["evidence"]) == 1  # No compulsory second paid text run.
    request = submit(author, reviewer, review)
    approve(reviewer, request, review)
    result = execute(reviewer, request, review)
    assert (
        result["currentness_at_action"]["evidence"][0]["source"] == "simulation_native"
    )


def test_original_assist_operation_recovery_survives_payload_purge(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    run = text_run(author, hub_skill, candidate[1])
    run.job.kind = "assist"
    run.job.save(update_fields=["kind"])
    proposal = HubSkillAssistProposal.objects.create(
        job=run.job,
        skill=hub_skill,
        kind="draft",
        base_revision=1,
        base_digest=candidate[1].digest,
        input=None,
        input_fingerprint=canonical_digest("input"),
        prompt_version="fixture",
        model_config={},
        proposal=None,
        payload_purged_at=timezone.now(),
    )
    result = recover_assist_operation(
        principal=author, skill_id=hub_skill.pk, idempotency_key=run.job.idempotency_key
    )
    assert result["id"] == str(proposal.pk)
    assert "proposal" not in result and "input" not in result
    assert list_assist_operations(principal=author, skill_id=hub_skill.pk)["count"] == 1
    assert (
        list_assist_operations(principal=reviewer, skill_id=hub_skill.pk)["count"] == 0
    )
    with pytest.raises(SkillsHubError):
        recover_assist_operation(
            principal=reviewer,
            skill_id=hub_skill.pk,
            idempotency_key=run.job.idempotency_key,
        )
    with pytest.raises(SkillsHubError) as caught:
        require_qualified_assistance()
    assert caught.value.code == "skills_unavailable"

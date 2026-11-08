"""Exact pointer CAS, complete impact identity and scoped reverse references."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v11 import (
    HubSkillJob,
    HubSkillTestEvidence,
    HubSkillTestRun,
)
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextQuarantine,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.authoring import (
    save_context_draft,
    start_context_draft,
)
from model_hub.services.skills_hub.v12.releases import (
    context_dependents,
    context_release_state,
    move_context_label,
    release_impact,
    remove_context_label,
    review_context_release,
)

pytestmark = pytest.mark.django_db


def move_request(principal, context, version, label="production", evidence_run_id=None):
    review = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="move",
        target_version=version.number,
        label=label,
        evidence_run_id=evidence_run_id,
    )
    current = next(
        (row for row in review["release_state"]["labels"] if row["name"] == label), None
    )
    return {
        "version": version.number,
        "expected_version": current["version"] if current else None,
        "expected_release_revision": review["release_state"]["release_revision"],
        "expected_security_revision": review["release_state"]["security_revision"],
        "expected_digest": version.digest,
        "expected_impact_fingerprint": review["impact"]["fingerprint"],
        "note": "Reviewed",
        "evidence_run_id": evidence_run_id,
    }


def next_version(principal, context, publish_context):
    context.refresh_from_db()
    draft = start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=context.latest_version_number,
    )
    save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=draft.base_version,
        files=[{"path": "AGENTS.md", "content": f"Changed from {draft.base_version}"}],
        description="New purpose",
        note="",
    )
    return publish_context(context).version


def binding(context, principal, name, selector):
    return HubSkillContextBinding.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context=context,
        name=name,
        kind="runtime",
        selector=selector,
        update_mode="new_thread",
        profile="python",
        profile_version="fixture-1",
        expected_report_interval_seconds=60,
        configuration_digest="sha256:" + "a" * 64,
    )


def evidence(context, principal, version, *, visibility="workspace"):
    job = HubSkillJob.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill=context,
        owner_user_id=principal.user_id,
        kind="test",
        idempotency_key=uuid4(),
        request_digest="sha256:" + "b" * 64,
        workflow_id=f"fixture-{uuid4()}",
        deadline_at=timezone.now() + timedelta(minutes=5),
        status="completed",
    )
    return HubSkillTestRun.objects.create(
        job=job,
        visibility=visibility,
        profile="quick",
        repetitions=1,
        harness_version="contexts-text-v1.2",
        adapter_version="fixture",
        evaluator_version="fixture",
        execution_fingerprint="sha256:" + "c" * 64,
        candidate_digest=version.digest,
        candidate={"kind": "version", "version": version.number},
        snapshot={},
        payload_expires_at=timezone.now() + timedelta(days=1),
    )


def test_protected_move_noop_replay_aba_and_pointer_only_rollback(
    principal, context, publish_context
):
    first = publish_context(context).version
    second = next_version(principal, context, publish_context)
    req = move_request(principal, context, first)
    key = uuid4()
    result = move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=req,
        idempotency_key=key,
    )
    assert result["event_id"] and result["release_revision"] == 1
    assert (
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=req,
            idempotency_key=key,
        )
        == result
    )
    stale = move_request(principal, context, first)
    for target in [second, first]:
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=move_request(principal, context, target),
            idempotency_key=uuid4(),
        )
    with pytest.raises(SkillsHubError) as error:
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=stale,
            idempotency_key=uuid4(),
        )
    assert error.value.code == "release_conflict"
    result = move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=move_request(principal, context, first),
        idempotency_key=uuid4(),
    )
    assert result["event_id"] is None and result["release_revision"] == 3
    assert context.versions.count() == 2
    assert context.labels.get(name="production").version_id == first.pk


def test_all_context_labels_require_full_preconditions(
    principal, context, publish_context
):
    version = publish_context(context).version
    request = move_request(principal, context, version, label="unprotected")
    for field in [
        "expected_version",
        "expected_release_revision",
        "expected_security_revision",
        "expected_digest",
        "expected_impact_fingerprint",
    ]:
        missing = {key: value for key, value in request.items() if key != field}
        with pytest.raises(SkillsHubError) as error:
            move_context_label(
                principal=principal,
                context_id=context.pk,
                label="unprotected",
                request=missing,
                idempotency_key=uuid4(),
            )
        assert error.value.code == "context_precondition_required"
    assert not context.labels.exists()


def test_full_impact_detects_row_26_but_ignores_other_context_and_environment(
    principal, context, context_factory, publish_context
):
    version = publish_context(context).version
    rows = [
        binding(context, principal, f"target-{index}", {"label": "production"})
        for index in range(30)
    ]
    request = move_request(principal, context, version)
    review = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="move",
        target_version=1,
        label="production",
    )
    assert (
        len(review["impact"]["bindings"]) == 25 and review["impact"]["more_available"]
    )
    assert review["impact"]["following"] == 30
    binding(
        context_factory("other"), principal, "other-context", {"label": "production"}
    )
    binding(context, principal, "other-environment", {"label": "development"})
    same = move_request(principal, context, version)
    assert same["expected_impact_fingerprint"] == request["expected_impact_fingerprint"]
    shown = {row["id"] for row in review["impact"]["bindings"]}
    unseen = next(row for row in rows if str(row.pk) not in shown)
    unseen.configuration_digest = "sha256:" + "d" * 64
    unseen.revision += 1
    unseen.save(update_fields=["configuration_digest", "revision"])
    with pytest.raises(SkillsHubError) as error:
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=request,
            idempotency_key=uuid4(),
        )
    assert error.value.code == "impact_changed"
    assert not context.labels.exists()


def test_quarantine_blocks_move_even_when_noop(principal, context, publish_context):
    version = publish_context(context).version
    move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=move_request(principal, context, version),
        idempotency_key=uuid4(),
    )
    request = move_request(principal, context, version)
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=version,
        revision=1,
        active=True,
        reason="Quarantined fixture",
        actor_id=principal.user_id,
    )
    with pytest.raises(SkillsHubError) as error:
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=request,
            idempotency_key=uuid4(),
        )
    assert error.value.code == "context_quarantined"
    assert context.label_events.count() == 1


def test_evidence_expiry_private_and_mismatch_are_not_silently_dropped(
    principal, context, publish_context
):
    version = publish_context(context).version
    run = evidence(context, principal, version)
    request = move_request(principal, context, version, evidence_run_id=run.pk)
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    with pytest.raises(SkillsHubError) as error:
        move_context_label(
            principal=principal,
            context_id=context.pk,
            label="production",
            request=request,
            idempotency_key=uuid4(),
        )
    assert error.value.code == "evidence_expired"
    private = evidence(context, principal, version, visibility="private")
    with pytest.raises(SkillsHubError) as error:
        move_request(principal, context, version, evidence_run_id=private.pk)
    assert error.value.code == "evidence_share_required"
    with pytest.raises(SkillsHubError) as error:
        move_request(principal, context, version, evidence_run_id=uuid4())
    assert error.value.code == "evidence_mismatch"
    assert not context.labels.exists()


def test_label_event_evidence_records_pre_move_baseline_currentness(
    principal, context, publish_context
):
    first = publish_context(context).version
    promoted = move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=move_request(principal, context, first),
        idempotency_key=uuid4(),
    )
    second = next_version(principal, context, publish_context)
    run = evidence(context, principal, second)
    run.baseline_version = first
    run.baseline_event_id = promoted["event_id"]
    run.save(update_fields=["baseline_version", "baseline_event"])
    result = move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=move_request(principal, context, second, evidence_run_id=run.pk),
        idempotency_key=uuid4(),
    )
    assert result["currentness_at_action"]["baseline"] is True
    attached = HubSkillTestEvidence.objects.get(label_event_id=result["event_id"])
    assert attached.currentness_at_action["baseline"] is True
    assert result["post_move_state"]["version"] == 2


def test_removed_label_history_remains_and_protection_is_retained(
    principal, context, publish_context
):
    version = publish_context(context).version
    move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=move_request(principal, context, version),
        idempotency_key=uuid4(),
    )
    context.refresh_from_db()
    impact = release_impact(
        context=context, label="production", target_version=None, operation="remove"
    )
    remove_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request={
            "expected_version": 1,
            "expected_release_revision": 1,
            "expected_security_revision": 0,
            "expected_impact_fingerprint": impact["fingerprint"],
            "note": "Removed",
        },
    )
    state = context_release_state(principal=principal, context_id=context.pk)
    assert state["labels"] == [] and state["protected_label_names"] == ["production"]
    assert state["release_revision"] == 2
    assert list(
        context.label_events.order_by("release_revision").values_list(
            "to_version", flat=True
        )
    ) == [1, None]


def test_reverse_references_separate_pins_followers_and_history(
    principal,
    context_factory,
    hub_skill,
    publish_skill,
    make_skill_files,
    accept_links,
    publish_context,
):
    leaf = publish_skill(hub_skill, labels=["production"]).version
    contexts = [context_factory(f"context-{index}") for index in range(3)]
    for index, parent in enumerate(contexts):
        accept_links(
            parent,
            [
                {
                    "skill_id": str(hub_skill.pk),
                    "selector": (
                        {"version": leaf.number}
                        if index == 2
                        else {"label": "production"}
                    ),
                }
            ],
        )
        publish_context(parent)
    publish_skill(
        hub_skill, labels=["production"], files=make_skill_files(body="Updated policy")
    )
    result = context_dependents(
        principal=principal, skill_id=hub_skill.pk, page=1, page_size=2
    )
    assert result["count"] == 3 and result["total_pages"] == 2
    all_rows = (
        result["results"]
        + context_dependents(
            principal=principal, skill_id=hub_skill.pk, page=2, page_size=2
        )["results"]
    )
    assert sorted(row["update_state"] for row in all_rows) == [
        "pinned_as_intended",
        "update_available",
        "update_available",
    ]
    assert all(row["locked_version"] == 1 for row in all_rows)


def test_review_is_bounded_root_first_and_has_separate_origin_head_diffs(
    principal, context, publish_context
):
    first = publish_context(context).version
    next_version(principal, context, publish_context)
    draft = start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=first.number,
    )
    files = [{"path": "AGENTS.md", "content": "Historical repair"}] + [
        {"path": f"ref{index:02}.md", "content": "Supporting text"}
        for index in range(30)
    ]
    draft = save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=1,
        files=files,
        description="Repair purpose",
        note="",
    )
    review = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="publish",
        revision=draft.revision,
    )
    assert review["base_version"] == 1 and review["latest_version"] == 2
    assert review["changes"]["from"] == 1 and review["head_changes"]["from"] == 2
    assert len(review["changes"]["files"]) == 25
    assert review["changes"]["files"][0]["path"] == "AGENTS.md"
    assert all("to_text" not in row for row in review["changes"]["files"])

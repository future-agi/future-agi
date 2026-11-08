from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillLabel
from model_hub.models.skills_hub_v12 import (
    HubSkillContextCredential,
    HubSkillContextReceipt,
    HubSkillContextSession,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.consumers import (
    adoption_summary,
    append_receipt,
    check_session_access,
    create_binding,
    list_bindings,
    open_context_session,
    update_binding,
)
from model_hub.services.skills_hub.v12.consumption import read_consumption_manifest
from model_hub.services.skills_hub.v12.safety import set_quarantine

pytestmark = pytest.mark.django_db


def session_request():
    return {
        "client_session_key": str(uuid4()),
        "incarnation_id": str(uuid4()),
        "resume_session_id": None,
        "adapter_version": "test-1",
        "runtime_version": "test-runtime",
        "runtime_config_digest": "sha256:" + "a" * 64,
    }


def point(context, version):
    HubSkillLabel.objects.update_or_create(
        skill=context, name="production", defaults={"version": version}
    )


def receipt_for(version, *, stage="installed", sequence=0, instance=None, session=None):
    path = "AGENTS.md" if stage == "instructions_delivered" else None
    root = version.files.select_related("blob").get(path="AGENTS.md")
    return {
        "event_id": str(uuid4()),
        "session_id": session,
        "instance_id": instance or str(uuid4()),
        "sequence": sequence,
        "stage": stage,
        "version_id": str(version.pk),
        "digest": version.digest,
        "materialization_digest": None,
        "observed_at": timezone.now(),
        "details": {
            "path": path,
            "sha256": root.sha256 if path else None,
            "ranges": [[0, len(root.blob.content)]] if path else [],
            "runtime_version": "test-runtime",
            "adapter_version": "test-1",
            "diagnostic_codes": [],
            "trace_ref": None,
        },
    }


def test_session_replay_resume_and_immutable_lease(
    principal, context, context_version, binding, credential
):
    old = context_version(context)
    point(context, old)
    consumer = credential(old)
    request, key = session_request(), uuid4()
    opened = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=request,
        idempotency_key=key,
    )
    new = context_version(context, body="New policy for clean threads only.")
    point(context, new)
    replay = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=request,
        idempotency_key=key,
    )
    assert replay == opened
    resumed = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request={**request, "resume_session_id": opened["session_id"]},
        idempotency_key=uuid4(),
    )
    assert resumed["version_id"] == str(old.pk)
    assert resumed["history_mode"] == "resumed"
    assert resumed["resolution_id"] != opened["resolution_id"]
    manifest = read_consumption_manifest(
        principal=consumer,
        kind="agent_context",
        resource_id=context.pk,
        number=old.number,
        resolution_id=UUID(resumed["resolution_id"]),
    )
    assert manifest["digest"] == old.digest
    fresh = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=session_request(),
        idempotency_key=uuid4(),
    )
    assert fresh["version_id"] == str(new.pk)
    assert HubSkillContextSession.objects.count() == 2
    with pytest.raises(SkillsHubError, match="already exists"):
        open_context_session(
            principal=consumer,
            binding_id=binding["id"],
            request=request,
            idempotency_key=uuid4(),
        )


def test_unknown_resume_and_runtime_drift_never_reresolve(
    context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    principal = credential(version)
    request = session_request()
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=principal,
            binding_id=binding["id"],
            request={**request, "resume_session_id": str(uuid4())},
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "thread_context_unknown"
    assert HubSkillContextSession.objects.count() == 0
    opened = open_context_session(
        principal=principal,
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=principal,
            binding_id=binding["id"],
            request={
                **request,
                "resume_session_id": opened["session_id"],
                "runtime_config_digest": "sha256:" + "b" * 64,
            },
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "session_context_conflict"


def test_revocation_quarantine_and_expiry_at_boundary(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    consumer = credential(version)
    opened = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=session_request(),
        idempotency_key=uuid4(),
    )
    assert check_session_access(principal=consumer, session_id=opened["session_id"])[
        "allowed"
    ]
    set_quarantine(
        principal=principal,
        version_id=version.pk,
        revision=0,
        active=True,
        reason="Synthetic safety check",
        replacement_version_id=None,
    )
    with pytest.raises(SkillsHubError) as exc:
        check_session_access(principal=consumer, session_id=opened["session_id"])
    assert exc.value.code == "context_quarantined"
    set_quarantine(
        principal=principal,
        version_id=version.pk,
        revision=1,
        active=False,
        reason="Reviewed repair",
        replacement_version_id=None,
    )
    HubSkillContextCredential.objects.filter(pk=consumer.credential_id).update(
        revoked_at=timezone.now()
    )
    with pytest.raises(SkillsHubError) as exc:
        check_session_access(principal=consumer, session_id=opened["session_id"])
    assert exc.value.code == "context_credential_revoked"


def test_consume_cannot_report_and_report_cannot_open(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    report = credential(version, purpose="report", binding_id=binding["id"])
    consume = credential(version)
    with pytest.raises(SkillsHubError) as exc:
        append_receipt(
            principal=consume, binding_id=binding["id"], receipt=receipt_for(version)
        )
    assert exc.value.code == "credential_operation_forbidden"
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=report,
            binding_id=binding["id"],
            request=session_request(),
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "credential_operation_forbidden"
    accepted = append_receipt(
        principal=report, binding_id=binding["id"], receipt=receipt_for(version)
    )
    assert accepted["accepted"]
    with pytest.raises(SkillsHubError) as exc:
        append_receipt(
            principal=report, binding_id=uuid4(), receipt=receipt_for(version)
        )
    assert exc.value.code == "resource_not_found"


def test_receipt_replay_order_and_independent_stages(
    principal, context, context_version, binding
):
    old = context_version(context)
    new = context_version(context, body="Updated policy")
    point(context, new)
    instance = str(uuid4())
    loaded = receipt_for(
        new, stage="instructions_delivered", sequence=10, instance=instance
    )
    first = append_receipt(
        principal=principal, binding_id=binding["id"], receipt=loaded
    )
    assert (
        append_receipt(principal=principal, binding_id=binding["id"], receipt=loaded)
        == first
    )
    append_receipt(
        principal=principal,
        binding_id=binding["id"],
        receipt=receipt_for(
            old, stage="instructions_delivered", sequence=1, instance=instance
        ),
    )
    append_receipt(
        principal=principal,
        binding_id=binding["id"],
        receipt=receipt_for(old, stage="installed", sequence=11, instance=instance),
    )
    row = adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]
    assert row["loaded"]["version_id"] == str(new.pk)
    assert row["installed"]["version_id"] == str(old.pk)
    assert row["discovered"] is None
    assert row["loaded"]["origin"] == "client_reported"
    changed = {**loaded, "stage": "closed"}
    with pytest.raises(SkillsHubError) as exc:
        append_receipt(principal=principal, binding_id=binding["id"], receipt=changed)
    assert exc.value.code == "receipt_conflict"
    with pytest.raises(SkillsHubError):
        append_receipt(
            principal=principal,
            binding_id=binding["id"],
            receipt={**loaded, "event_id": str(uuid4())},
        )


def test_no_observation_is_unknown_and_fetch_or_install_never_load(
    principal, context, context_version, binding
):
    version = context_version(context)
    point(context, version)
    row = adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]
    assert row["states"] == ["unknown"]
    append_receipt(
        principal=principal, binding_id=binding["id"], receipt=receipt_for(version)
    )
    row = adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]
    assert row["loaded"] is None
    assert "unknown" in row["states"]


def test_mixed_sessions_lower_number_rollback_and_stale(
    principal, context, context_version, binding
):
    old = context_version(context)
    new = context_version(context, body="Changed policy")
    point(context, old)
    for version in (old, new):
        append_receipt(
            principal=principal,
            binding_id=binding["id"],
            receipt=receipt_for(version, stage="instructions_delivered"),
        )
    HubSkillContextReceipt.objects.update(
        created_at=timezone.now() - timedelta(minutes=2)
    )
    row = adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]
    assert {"mixed_sessions", "update_available", "stale_observation"} <= set(
        row["states"]
    )
    assert "matches_target" not in row["states"]


def test_retirement_and_binding_cas(principal, context, binding):
    listed = list_bindings(principal=principal, context_id=context.pk)
    assert listed["count"] == 1
    fields = {
        key: value
        for key, value in binding.items()
        if key not in {"id", "context_id", "revision", "retired_at"}
    }
    changed = update_binding(
        principal=principal,
        binding_id=binding["id"],
        revision=1,
        request={**fields, "name": "Renamed target"},
    )
    assert changed["revision"] == 2
    with pytest.raises(SkillsHubError) as exc:
        update_binding(
            principal=principal, binding_id=binding["id"], revision=1, request=None
        )
    assert exc.value.code == "binding_conflict"
    update_binding(
        principal=principal, binding_id=binding["id"], revision=2, request=None
    )
    assert list_bindings(principal=principal, context_id=context.pk)["count"] == 0


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../../b", "x\\y"])
def test_repository_scope_rejects_escapes(principal, context, path):
    with pytest.raises(SkillsHubError):
        create_binding(
            principal=principal,
            context_id=context.pk,
            idempotency_key=uuid4(),
            request={
                "name": "Synthetic repo",
                "kind": "repository",
                "selector": {"version": 1},
                "update_mode": "repository_lock",
                "profile": "codex",
                "profile_version": "1",
                "repository": {
                    "provider": "github",
                    "repository_id": "123",
                    "default_branch": "main",
                    "project_scope": path,
                },
                "expected_report_interval_seconds": 60,
            },
        )


def test_partial_delivery_cannot_match_target(
    principal, context, context_version, binding
):
    version = context_version(context)
    point(context, version)
    receipt = receipt_for(version, stage="instructions_delivered")
    receipt["details"]["ranges"] = [[0, 1]]
    append_receipt(principal=principal, binding_id=binding["id"], receipt=receipt)
    row = adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]
    assert "matches_target" not in row["states"]
    assert "unknown" in row["states"]


def test_receipt_rejects_transcript_and_wrong_hash(
    principal, context, context_version, binding
):
    version = context_version(context)
    receipt = receipt_for(version, stage="instructions_delivered")
    bad = deepcopy(receipt)
    bad["details"]["sha256"] = "0" * 64
    with pytest.raises(SkillsHubError):
        append_receipt(principal=principal, binding_id=binding["id"], receipt=bad)
    bad = deepcopy(receipt)
    bad["details"]["prompt"] = "A private prompt must never be accepted here"
    with pytest.raises(SkillsHubError):
        append_receipt(principal=principal, binding_id=binding["id"], receipt=bad)

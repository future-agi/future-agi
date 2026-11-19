"""Recovery is a cohort assertion, never the most successful reporter's state."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v12 import (
    HubSkillContextCredential,
    HubSkillContextOperation,
    HubSkillContextResolution,
    HubSkillContextSession,
)
from model_hub.services.skills_hub.v12.consumers import (
    adoption_summary,
    append_receipt,
    check_session_access,
    open_context_session,
)
from model_hub.tests.skills_hub.r3_l06.test_consumers import (
    point,
    receipt_for,
    session_request,
)

pytestmark = pytest.mark.django_db


def summary(principal, context):
    return adoption_summary(principal=principal, context_id=context.pk, window={})[
        "results"
    ][0]


def report(principal, binding, receipt):
    return append_receipt(
        principal=principal, binding_id=binding["id"], receipt=receipt
    )


def assert_unrecovered(row, state="unknown"):
    assert state in row["states"]
    assert "matches_target" not in row["states"]
    assert "pinned_as_intended" not in row["states"]


def test_one_loaded_instance_does_not_hide_an_install_only_instance(
    principal, context, context_version, binding
):
    version = context_version(context)
    point(context, version)
    report(principal, binding, receipt_for(version, stage="instructions_delivered"))
    assert "matches_target" in summary(principal, context)["states"]
    report(principal, binding, receipt_for(version, stage="installed"))
    row = summary(principal, context)
    assert row["loaded"]["version_id"] == str(version.pk)
    assert_unrecovered(row)


def test_unobserved_retained_thread_cannot_be_hidden_by_another_reporter(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    request = session_request()
    opened = open_context_session(
        principal=credential(version),
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    report(principal, binding, receipt_for(version, stage="instructions_delivered"))
    assert_unrecovered(summary(principal, context))
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="instructions_delivered",
            session=opened["session_id"],
            instance=request["incarnation_id"],
        ),
    )
    assert "matches_target" in summary(principal, context)["states"]


def test_expired_unobserved_mapping_is_not_proof_of_thread_closure(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    opened = open_context_session(
        principal=credential(version),
        binding_id=binding["id"],
        request=session_request(),
        idempotency_key=uuid4(),
    )
    report(principal, binding, receipt_for(version, stage="instructions_delivered"))
    HubSkillContextSession.objects.filter(pk=opened["session_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert_unrecovered(summary(principal, context))


def test_failure_after_delivery_is_not_recovered_until_new_delivery(
    principal, context, context_version, binding
):
    version = context_version(context)
    point(context, version)
    instance = str(uuid4())
    report(
        principal,
        binding,
        receipt_for(version, stage="instructions_delivered", instance=instance),
    )
    report(
        principal,
        binding,
        receipt_for(version, stage="failed", instance=instance, sequence=1),
    )
    assert_unrecovered(summary(principal, context))
    report(
        principal,
        binding,
        receipt_for(
            version, stage="instructions_delivered", instance=instance, sequence=2
        ),
    )
    assert "matches_target" in summary(principal, context)["states"]


def failure_with_trace(version, instance, sequence):
    row = receipt_for(version, stage="failed", instance=instance, sequence=sequence)
    row["details"]["diagnostic_codes"] = ["refund_window_regression"]
    row["details"]["trace_ref"] = {
        "project_id": str(uuid4()),
        "trace_id": "j6-incident-trace",
        "span_id": "refund-decision",
        "start_time": timezone.now().isoformat(),
    }
    return row


def test_retained_incident_is_a_pointer_not_a_verdict_and_survives_recovery(
    principal, context, context_version, binding
):
    version = context_version(context)
    point(context, version)
    assert summary(principal, context)["latest_failure"] is None
    instance = str(uuid4())
    report(
        principal,
        binding,
        receipt_for(version, stage="instructions_delivered", instance=instance),
    )
    report(principal, binding, failure_with_trace(version, instance, 1))
    row = summary(principal, context)
    failure = row["latest_failure"]
    # Only bounded codes and the physical trace reference, never message text.
    assert failure["diagnostic_codes"] == ["refund_window_regression"]
    assert failure["trace_ref"]["trace_id"] == "j6-incident-trace"
    assert failure["version_id"] == str(version.pk)
    assert_unrecovered(row)
    # A later delivery recovers the state, but the incident pointer is retained
    # evidence: recovery never erases what failed.
    report(
        principal,
        binding,
        receipt_for(
            version, stage="instructions_delivered", instance=instance, sequence=2
        ),
    )
    row = summary(principal, context)
    assert "matches_target" in row["states"]
    assert row["latest_failure"]["trace_ref"]["trace_id"] == "j6-incident-trace"


@pytest.mark.parametrize("same_instance", [True, False])
def test_new_heartbeat_cannot_refresh_old_delivery(
    principal, context, context_version, binding, same_instance
):
    version = context_version(context)
    point(context, version)
    instance = str(uuid4())
    old_delivery = receipt_for(
        version, stage="instructions_delivered", instance=instance
    )
    old_delivery["observed_at"] = timezone.now() - timedelta(minutes=5)
    report(principal, binding, old_delivery)
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="resolved" if same_instance else "instructions_delivered",
            instance=instance if same_instance else str(uuid4()),
            sequence=1,
        ),
    )
    assert_unrecovered(summary(principal, context), "stale_observation")


@pytest.mark.parametrize(
    "fault",
    ["expired_credential", "revoked_credential", "expired_mapping", "lost_origin"],
)
def test_historical_delivery_is_not_recovery_of_an_unresumable_thread(
    principal, context, context_version, binding, credential, fault
):
    version = context_version(context)
    point(context, version)
    consumer = credential(version)
    request = session_request()
    opened = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="instructions_delivered",
            session=opened["session_id"],
            instance=request["incarnation_id"],
        ),
    )
    assert "matches_target" in summary(principal, context)["states"]
    expired = timezone.now() - timedelta(seconds=1)
    if fault == "expired_credential":
        HubSkillContextCredential.objects.filter(pk=consumer.credential_id).update(
            expires_at=expired
        )
    elif fault == "revoked_credential":
        HubSkillContextCredential.objects.filter(pk=consumer.credential_id).update(
            revoked_at=expired
        )
    elif fault == "expired_mapping":
        HubSkillContextSession.objects.filter(pk=opened["session_id"]).update(
            expires_at=expired
        )
    else:
        HubSkillContextOperation.objects.filter(
            operation="open_session", result_resource_id=opened["session_id"]
        ).delete()
    row = summary(principal, context)
    # Preserve what was observed, without claiming the retained thread can run.
    assert row["loaded"]["version_id"] == str(version.pk)
    assert_unrecovered(row)
    assert row["next_action_code"] == "observe_runtime"


@pytest.mark.parametrize(
    "fault",
    [
        "lost_resolution",
        "wrong_resolution",
        "malformed_resolution",
        "missing_resolution",
        "removed_grant",
        "expanded_grant",
    ],
)
def test_original_grant_mapping_loss_suppresses_historical_target_match(
    principal, context, context_version, binding, credential, fault
):
    from model_hub.services.skills_hub.errors import SkillsHubError

    version = context_version(context)
    point(context, version)
    consumer = credential(version)
    request = session_request()
    opened = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="instructions_delivered",
            session=opened["session_id"],
            instance=request["incarnation_id"],
        ),
    )
    assert "matches_target" in summary(principal, context)["states"]
    if fault == "lost_resolution":
        HubSkillContextResolution.objects.filter(pk=opened["resolution_id"]).delete()
    elif fault in {"wrong_resolution", "malformed_resolution", "missing_resolution"}:
        origin = HubSkillContextOperation.objects.get(
            operation="open_session", result_resource_id=opened["session_id"]
        )
        if fault == "missing_resolution":
            del origin.response["resolution_id"]
        else:
            origin.response["resolution_id"] = (
                str(uuid4()) if fault == "wrong_resolution" else "not-a-uuid"
            )
        origin.save(update_fields=["response"])
    else:
        grants = []
        if fault == "expanded_grant":
            # JSONB containment accepts a superset. Session authority instead
            # requires the exact original grant still to be a member.
            original = HubSkillContextResolution.objects.get(pk=opened["resolution_id"])
            grants = [
                {
                    **original.grant,
                    "version_ids": [*original.grant["version_ids"], str(uuid4())],
                }
            ]
        HubSkillContextCredential.objects.filter(pk=consumer.credential_id).update(
            grants=grants
        )
    # The same retained session is unusable at the real managed boundary.
    with pytest.raises(SkillsHubError):
        check_session_access(
            principal=consumer,
            session_id=opened["session_id"],
            binding_id=binding["id"],
        )
    row = summary(principal, context)
    assert row["loaded"]["version_id"] == str(version.pk)
    assert_unrecovered(row)


def test_expired_resolution_lease_does_not_expire_an_eligible_retained_thread(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    consumer = credential(version)
    request = session_request()
    opened = open_context_session(
        principal=consumer,
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="instructions_delivered",
            session=opened["session_id"],
            instance=request["incarnation_id"],
        ),
    )
    HubSkillContextResolution.objects.filter(pk=opened["resolution_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    # Resolution lease expiry requires a new lease, not a new context/thread.
    check_session_access(
        principal=consumer, session_id=opened["session_id"], binding_id=binding["id"]
    )
    assert "matches_target" in summary(principal, context)["states"]


def test_closed_unobserved_thread_does_not_block_other_delivery(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    point(context, version)
    request = session_request()
    opened = open_context_session(
        principal=credential(version),
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    report(
        principal,
        binding,
        receipt_for(
            version,
            stage="closed",
            session=opened["session_id"],
            instance=request["incarnation_id"],
        ),
    )
    report(principal, binding, receipt_for(version, stage="instructions_delivered"))
    assert "matches_target" in summary(principal, context)["states"]

"""Recovery is a cohort assertion, never the most successful reporter's state."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.services.skills_hub.v12.consumers import (
    adoption_summary,
    append_receipt,
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

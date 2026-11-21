"""Synthetic heartbeat observations must not invalidate a reviewed parent move."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v12 import HubSkillContextReceipt
from model_hub.services.skills_hub.v12.releases import (
    move_context_label,
    review_context_release,
)
from model_hub.tests.skills_hub.r3_l03.test_releases import binding, move_request

pytestmark = pytest.mark.django_db


def test_j3_heartbeats_and_unrelated_bindings_preserve_authorized_transition(
    principal, context, context_factory, publish_context
):
    version = publish_context(context).version
    target = binding(context, principal, "follower", {"label": "production"})
    request = move_request(principal, context, version)
    before = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="move",
        target_version=version.number,
        label="production",
    )
    assert before["impact"]["unobserved"] == 1
    now = timezone.now()
    receipt = HubSkillContextReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        binding=target,
        reporter_key="synthetic-j3-heartbeat",
        event_id=uuid4(),
        instance_id=uuid4(),
        sequence=1,
        stage="resolved",
        version=version,
        digest=version.digest,
        request_digest="sha256:" + "a" * 64,
        observed_at=now,
        origin="client_reported",
        expires_at=now + timedelta(hours=1),
    )
    binding(
        context_factory("other-parent"), principal, "unrelated", {"label": "production"}
    )
    binding(context, principal, "other-environment", {"label": "staging"})
    after = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="move",
        target_version=version.number,
        label="production",
    )
    assert after["impact"]["unobserved"] == 0
    assert after["impact"]["fingerprint"] == request["expected_impact_fingerprint"]
    # Expiring an observation can change the displayed confidence, never the
    # selector/configuration preconditions on an already reviewed transition.
    receipt.expires_at = now - timedelta(seconds=1)
    receipt.save(update_fields=["expires_at"])
    expired = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="move",
        target_version=version.number,
        label="production",
    )
    assert expired["impact"]["unobserved"] == 1
    assert expired["impact"]["fingerprint"] == request["expected_impact_fingerprint"]
    moved = move_context_label(
        principal=principal,
        context_id=context.pk,
        label="production",
        request=request,
        idempotency_key=uuid4(),
    )
    assert moved["label"]["version_id"] == str(version.pk)
    assert context.label_events.count() == 1

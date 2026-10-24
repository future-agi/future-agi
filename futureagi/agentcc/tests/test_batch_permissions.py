"""Only an org Owner or Admin may submit or cancel a console batch.

A batch spends the org's credits and calls its providers as the org, so
submit-batch and cancel-batch are gateway admin actions. Every member reads a
batch's status, and health_check is open to every member (only its fleet re-push
is an admin's).
"""

import pytest

from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.tests.batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.batch_support import (
    BATCH_BODY,
    GATEWAY,
    user_with_level,
)
from agentcc.views.gateway import AgentccGatewayViewSet
from tfc.constants.levels import Level


def test_submit_and_cancel_are_gateway_admin_actions_and_health_check_is_not():
    actions = AgentccGatewayViewSet.gateway_admin_actions
    assert {"submit_batch", "cancel_batch"} <= actions
    assert "health_check" not in actions
    assert "get_batch" not in actions


@pytest.mark.integration
@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
def test_member_and_viewer_cannot_submit_or_cancel(
    level, auth_client, organization, workspace, make_client, fake_gateway
):
    # The Owner's batch, which the member tries to cancel.
    batch_id = auth_client.post(
        f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json"
    ).json()["result"]["batch_id"]
    user, _ = user_with_level(organization, level, workspace=workspace)
    member = make_client(user, workspace)
    calls_before = list(fake_gateway.calls)

    submitted = member.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert submitted.status_code == 403, submitted.content
    assert GATEWAY_ADMIN_MESSAGE in submitted.content.decode()

    cancelled = member.post(
        f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
    )
    assert cancelled.status_code == 403, cancelled.content
    assert fake_gateway.calls == calls_before, "a refused call reached the gateway"

    # Reading the org's batch and checking health stay open to every member.
    read = member.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    assert read.status_code == 200, read.content
    health = member.post(f"{GATEWAY}/health_check/")
    assert health.status_code == 200, health.content


@pytest.mark.integration
@pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
def test_admin_and_owner_submit_and_cancel(
    level, organization, workspace, make_client, fake_gateway, delivered
):
    user, _ = user_with_level(organization, level, workspace=workspace)
    client = make_client(user, workspace)
    submitted = client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert submitted.status_code == 200, submitted.content
    batch_id = submitted.json()["result"]["batch_id"]
    cancelled = client.post(
        f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
    )
    assert cancelled.status_code == 200, cancelled.content
    assert len(fake_gateway.named("submit_batch")) == 1
    assert len(fake_gateway.named("cancel_batch")) == 1

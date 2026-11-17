"""Reading a delivery does not authorize replaying it.

Exercise the registered API with real org/workspace memberships. Temporal
starts are mocked, so no delivery worker or receiver is contacted.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models.webhook import AgentccWebhookEvent
from agentcc.tests.r7_console_a_support import ClientFactory, user_with_level
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)
from tfc.constants.levels import Level

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]

EVENTS_URL = "/agentcc/webhook-events/"


@pytest.fixture
def clients():
    factory = ClientFactory()
    yield factory
    factory.close()


@pytest.fixture
def member_client(organization, workspace, clients):
    # This helper creates an organization Member AND a workspace Member:
    # ordinary workspace write access must not authorize gateway replay.
    member, member_workspace = user_with_level(
        organization, Level.MEMBER, workspace=workspace
    )
    return clients(member, member_workspace)


@pytest.fixture(
    params=[AgentccWebhookEvent.FAILED, AgentccWebhookEvent.DEAD_LETTER],
    ids=["failed", "dead_letter"],
)
def retry_event(organization, workspace, request):
    hook = make_webhook(
        organization=organization,
        name="retry-access-hook",
        url="https://hooks.example.test/retry-access",
        events=["request.completed"],
    )
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="request.completed",
        payload={"request_id": "retry-access-event"},
        status=request.param,
        attempts=5,
        max_attempts=5,
        next_retry_at=timezone.now() + timedelta(hours=1),
        last_error="HTTP 503",
        last_response_code=503,
    )


@pytest.fixture(autouse=True)
def start_delivery():
    with patch("agentcc.views.webhook_outbound._start_event_delivery") as start:
        yield start


def test_member_retry_is_forbidden_without_mutation_or_dispatch(
    member_client,
    retry_event,
    start_delivery,
    django_capture_on_commit_callbacks,
):
    events = AgentccWebhookEvent.no_workspace_objects.filter(pk=retry_event.pk)
    before = events.values().get()

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = member_client.post(f"{EVENTS_URL}{retry_event.pk}/retry/")

    assert response.status_code == 403, response.content
    assert events.values().get() == before
    assert callbacks == []
    start_delivery.assert_not_called()


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN], ids=["owner", "admin"])
def test_owner_and_admin_retry_resets_event_and_dispatches_once(
    organization,
    workspace,
    clients,
    retry_event,
    start_delivery,
    django_capture_on_commit_callbacks,
    level,
):
    user, user_workspace = user_with_level(organization, level, workspace=workspace)
    client = clients(user, user_workspace)
    payload = retry_event.payload
    webhook_id = retry_event.webhook_id

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = client.post(f"{EVENTS_URL}{retry_event.pk}/retry/")

    assert response.status_code == 200, response.content
    assert response.json()["result"]["status"] == AgentccWebhookEvent.PENDING
    retry_event.refresh_from_db()
    assert retry_event.status == AgentccWebhookEvent.PENDING
    assert retry_event.attempts == 0
    assert retry_event.next_retry_at is None
    assert retry_event.last_error == ""
    assert retry_event.payload == payload
    assert retry_event.webhook_id == webhook_id
    assert len(callbacks) == 1
    start_delivery.assert_called_once_with(str(retry_event.pk))


def test_member_can_still_list_and_retrieve_deliveries(
    member_client, retry_event, start_delivery
):
    response = member_client.get(EVENTS_URL)
    assert response.status_code == 200, response.content
    assert str(retry_event.pk) in {row["id"] for row in response.json()["result"]}

    response = member_client.get(f"{EVENTS_URL}{retry_event.pk}/")
    assert response.status_code == 200, response.content
    assert response.json()["result"]["id"] == str(retry_event.pk)
    start_delivery.assert_not_called()

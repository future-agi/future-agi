"""A uuid live update reaches only sessions of the organization it was sent for.

Sessions without a SAML scope subscribe to the bare ``uuid_<id>`` group, which
any organization's sender also reaches. The relay (``/call-websocket/``) accepts
any authenticated caller and sends to that bare group for the caller's own
organization, so without a receiver-side check a member of one organization
who knows another organization's object id could push live updates into that
organization's open sessions.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync

from model_hub.utils.utils import broadcast_to_uuid, send_message_to_uuid
from sockets.consumer import DataConsumer

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"
TARGET = "11111111-1111-1111-1111-111111111111"


class _RecordingLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, event):
        self.sent.append((group, event))


def _events_sent(send, *args):
    layer = _RecordingLayer()
    with patch("model_hub.utils.utils.get_channel_layer", return_value=layer):
        send(*args)
    return layer.sent


def _subscribed_session(org_ids, auth_scope=None):
    consumer = DataConsumer()
    consumer.scope = {"user": SimpleNamespace(id=1), "auth_scope": auth_scope}
    consumer.organization_ids = frozenset(org_ids)
    consumer.current_subscription = consumer._uuid_group_name(TARGET)
    consumer.send_json = AsyncMock()
    # SAML liveness is covered by saml2_auth's socket tests.
    consumer._guard.ensure_live = AsyncMock(return_value=True)
    return consumer


async def _deliver(consumer, events):
    for group, event in events:
        if group == consumer.current_subscription:
            await consumer.send_data(event)


@pytest.mark.parametrize("send", [send_message_to_uuid, broadcast_to_uuid])
def test_uuid_events_name_the_organization_they_were_sent_for(send):
    events = _events_sent(send, TARGET, {"uuid": TARGET, "status": "running"}, ORG_B)

    assert {group for group, _ in events} == {
        f"uuid_{TARGET}",
        f"uuid_org_{ORG_B}_{TARGET}",
    }
    assert all(event["organization_id"] == ORG_B for _, event in events)


def test_uuid_event_without_an_organization_is_untagged():
    events = _events_sent(send_message_to_uuid, TARGET, {"uuid": TARGET})

    assert [group for group, _ in events] == [f"uuid_{TARGET}"]
    assert "organization_id" not in events[0][1]


def test_another_organizations_uuid_update_is_not_delivered():
    victim = _subscribed_session({ORG_A})
    # What the relay sends for a caller authenticated as organization B.
    events = _events_sent(
        send_message_to_uuid, TARGET, {"uuid": TARGET, "status": "spoofed"}, ORG_B
    )

    async_to_sync(_deliver)(victim, events)

    victim.send_json.assert_not_awaited()


def test_own_organizations_uuid_update_is_delivered_once():
    member = _subscribed_session({ORG_B, ORG_A})
    message = {"uuid": TARGET, "status": "running"}
    events = _events_sent(send_message_to_uuid, TARGET, message, ORG_A)

    async_to_sync(_deliver)(member, events)

    member.send_json.assert_awaited_once_with(message)


def test_saml_session_gets_its_organizations_update_once():
    scoped = _subscribed_session({ORG_A}, auth_scope=SimpleNamespace(org_id=ORG_A))
    message = {"uuid": TARGET, "status": "running"}
    events = _events_sent(send_message_to_uuid, TARGET, message, ORG_A)

    async_to_sync(_deliver)(scoped, events)

    scoped.send_json.assert_awaited_once_with(message)


def test_untagged_in_process_update_is_still_delivered():
    member = _subscribed_session({ORG_A})
    message = {"uuid": TARGET, "status": "running"}
    events = _events_sent(send_message_to_uuid, TARGET, message)

    async_to_sync(_deliver)(member, events)

    member.send_json.assert_awaited_once_with(message)


def test_session_with_unknown_organizations_refuses_tagged_updates():
    consumer = _subscribed_session(set())
    del consumer.organization_ids
    events = _events_sent(send_message_to_uuid, TARGET, {"uuid": TARGET}, ORG_A)

    async_to_sync(_deliver)(consumer, events)

    consumer.send_json.assert_not_awaited()


# database_sync_to_async closes connections from its own thread.
@pytest.mark.django_db(transaction=True)
def test_socket_organizations_are_the_users_active_memberships(user, organization):
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from sockets.org_scope import resolve_socket_organization_ids

    other = Organization.objects.create(name="Second synthetic org")
    former = Organization.objects.create(name="Former synthetic org")
    OrganizationMembership.objects.create(
        user=user, organization=other, role="Member", is_active=True
    )
    OrganizationMembership.objects.create(
        user=user, organization=former, role="Member", is_active=False
    )

    org_ids = async_to_sync(resolve_socket_organization_ids)({}, user)

    assert str(organization.id) in org_ids
    assert str(other.id) in org_ids
    assert str(former.id) not in org_ids


def test_saml_socket_organizations_are_its_bound_organization_only():
    from sockets.org_scope import resolve_socket_organization_ids

    scope = {"auth_scope": SimpleNamespace(org_id=ORG_A)}

    assert async_to_sync(resolve_socket_organization_ids)(scope, None) == frozenset(
        {ORG_A}
    )

"""A webhook 0076 found with no address gets the reserved address
https://webhook-address-missing.invalid/ and is turned off. Nothing is ever
delivered to it: turned off, an event dead-letters with no request; turned
back on, the address check refuses the reserved name before any request. And
a real address is saved only with its header and secret values entered again.

Rows are made as 0076 leaves them, encrypted under the reserved address. Test
values only, not real credentials.
"""

import importlib
import socket
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from agentcc.models.webhook import AgentccWebhookEvent
from agentcc.services.url_safety import WEBHOOK_PRIVATE_URL_ERROR
from agentcc.services.webhook_credential_updates import (
    WEBHOOK_SECRETS_REQUIRED,
    update_webhook,
)
from agentcc.services.webhook_delivery import WEBHOOK_INACTIVE, deliver_webhook_events
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook, stored_value
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

MISSING_ADDRESS = importlib.import_module(
    "agentcc.migrations.0076_encrypt_webhook_credentials"
).MISSING_ADDRESS
REAL = "https://hooks.example.test/real"
DELIVERY = "agentcc.services.webhook_delivery"


def _left_by_0076(organization, *, is_active=False):
    return make_webhook(
        organization=organization,
        name="no address",
        url=MISSING_ADDRESS,
        headers={"X-Auth": "synthetic-private"},
        secret="synthetic-signing",
        is_active=is_active,
    )


def _event(organization, hook):
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="error.occurred",
        payload={"event": "error.occurred"},
    )


def _nxdomain(*args, **kwargs):
    # What a resolver answers for any .invalid name (RFC 6761).
    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")


def test_left_off_an_event_dead_letters_with_no_request(organization):
    hook = _left_by_0076(organization)
    event = _event(organization, hook)
    with (
        patch("agentcc.services.url_safety.socket.getaddrinfo") as resolve,
        patch(DELIVERY + ".build_ssrf_safe_session") as transport,
    ):
        assert deliver_webhook_events(event_ids=[event.pk])["delivered"] == 0
    resolve.assert_not_called()
    transport.assert_not_called()
    event.refresh_from_db()
    assert event.status == AgentccWebhookEvent.DEAD_LETTER
    assert event.last_error == WEBHOOK_INACTIVE


def test_turned_back_on_the_address_check_refuses_before_any_request(organization):
    hook = _left_by_0076(organization, is_active=True)
    event = _event(organization, hook)
    with (
        patch(
            "agentcc.services.url_safety.socket.getaddrinfo", side_effect=_nxdomain
        ) as resolve,
        patch(DELIVERY + ".build_ssrf_safe_session") as transport,
    ):
        assert deliver_webhook_events(event_ids=[event.pk]) == {
            "delivered": 0,
            "failed": 1,
        }
    resolve.assert_called()
    transport.assert_not_called()
    event.refresh_from_db()
    assert event.last_error == WEBHOOK_PRIVATE_URL_ERROR
    assert "synthetic" not in event.last_error


@pytest.mark.parametrize(
    "changes",
    [{"url": REAL}, {"url": REAL, "is_active": True}],
    ids=["address", "address-and-on"],
)
def test_a_real_address_without_the_values_again_is_refused(organization, changes):
    hook = _left_by_0076(organization)
    with pytest.raises(ValidationError) as caught:
        update_webhook(hook, dict(changes))
    assert WEBHOOK_SECRETS_REQUIRED in str(caught.value.detail)
    hook.refresh_from_db()
    assert (hook.url, hook.is_active) == (MISSING_ADDRESS, False)
    assert stored_value(hook, "secret") == "synthetic-signing"


def test_a_real_address_with_the_values_again_is_saved(organization):
    hook = _left_by_0076(organization)
    row = update_webhook(
        hook,
        {
            "url": REAL,
            "is_active": True,
            "headers": {"X-Auth": "synthetic-new"},
            "secret": "synthetic-new-signing",
        },
    )
    row.refresh_from_db()
    assert (row.url, row.is_active) == (REAL, True)
    assert stored_value(row, "headers") == {"X-Auth": "synthetic-new"}
    assert stored_value(row, "secret") == "synthetic-new-signing"

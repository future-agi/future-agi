"""Both credential fields authenticate before test or worker transport is built."""

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest
import requests

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.webhook_delivery import deliver_webhook_events
from agentcc.tests.test_webhook_encrypted_fixtures import (
    context,
    make_webhook,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def fixture(organization):
    hook = make_webhook(
        organization=organization,
        name="encrypted-delivery",
        url="https://hooks.example.test/in",
        headers={"X-Auth": "synthetic-private"},
        secret="synthetic-signing",
    )
    event = AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="error.occurred",
        payload={"event": "error.occurred"},
    )
    return hook, event


@pytest.mark.parametrize("field", ["headers", "secret"])
@pytest.mark.parametrize("fault", ["corrupt", "context", "preserved"])
@pytest.mark.parametrize("caller", ["worker", "test"])
def test_either_unusable_field_refuses_entire_request(
    auth_client,
    organization,
    field,
    fault,
    caller,
):
    hook, event = fixture(organization)
    value = {"X": "synthetic-private"} if field == "headers" else "synthetic-signing"
    if fault == "corrupt":
        blob = b"corrupt"
    elif fault == "context":
        wrong = codec.StorageContext(
            str(organization.pk), str(hook.pk), field, hook.url + "other"
        )
        blob = codec.encrypt_for_storage(wrong, value)
    else:
        blob = codec.preserve_for_migration(context(hook, field), value)
    AgentccWebhook.no_workspace_objects.filter(pk=hook.pk).update(
        **{"encrypted_" + field: blob}
    )
    target = (
        "agentcc.services.webhook_delivery"
        if caller == "worker"
        else "agentcc.views.webhook_outbound"
    )
    with (
        patch(target + ".ensure_public_http_url") as dns,
        patch(target + ".build_ssrf_safe_session") as transport,
        patch(target + ".logger") as logger,
    ):
        if caller == "worker":
            assert deliver_webhook_events(event_ids=[event.pk]) == {
                "delivered": 0,
                "failed": 1,
            }
            event.refresh_from_db()
            assert event.attempts == 1 and event.last_error
            assert "synthetic" not in event.last_error
        else:
            response = auth_client.post(f"/agentcc/webhooks/{hook.pk}/test/")
            assert response.status_code == 400
            assert "synthetic" not in response.content.decode()
        dns.assert_not_called()
        transport.assert_not_called()
        assert "synthetic-private" not in repr(logger.mock_calls)
        assert "synthetic-signing" not in repr(logger.mock_calls)
        assert "exc_info" not in repr(logger.mock_calls)


@pytest.mark.parametrize("caller", ["worker", "test"])
def test_missing_key_never_becomes_unsigned_delivery(
    auth_client, organization, settings, caller
):
    hook, event = fixture(organization)
    settings.INTEGRATION_ENCRYPTION_KEY = ""
    target = (
        "agentcc.services.webhook_delivery"
        if caller == "worker"
        else "agentcc.views.webhook_outbound"
    )
    with patch(target + ".build_ssrf_safe_session") as transport:
        if caller == "worker":
            assert deliver_webhook_events(event_ids=[event.pk])["failed"] == 1
        else:
            assert (
                auth_client.post(f"/agentcc/webhooks/{hook.pk}/test/").status_code
                == 400
            )
        transport.assert_not_called()


@pytest.mark.parametrize("caller", ["worker", "test"])
def test_signed_body_and_custom_headers_are_unchanged(
    auth_client, organization, caller
):
    hook, event = fixture(organization)
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=204)
    target = (
        "agentcc.services.webhook_delivery"
        if caller == "worker"
        else "agentcc.views.webhook_outbound"
    )
    with (
        patch(target + ".ensure_public_http_url"),
        patch(target + ".build_ssrf_safe_session", return_value=session),
    ):
        if caller == "worker":
            assert deliver_webhook_events(event_ids=[event.pk])["delivered"] == 1
        else:
            assert (
                auth_client.post(f"/agentcc/webhooks/{hook.pk}/test/").status_code
                == 200
            )
    sent = session.post.call_args.kwargs
    expected = hmac.new(
        b"synthetic-signing", sent["data"].encode(), hashlib.sha256
    ).hexdigest()
    assert sent["headers"] == {
        "Content-Type": "application/json",
        "X-Auth": "synthetic-private",
        "X-Agentcc-Signature": "sha256=" + expected,
    }
    session.close.assert_called_once()


@pytest.mark.parametrize("fault", ["transport", "receiver"])
def test_worker_logs_and_event_export_never_quote_receiver(
    auth_client, organization, fault
):
    hook, event = fixture(organization)
    session = MagicMock()
    if fault == "transport":
        session.post.side_effect = requests.exceptions.InvalidHeader(
            "synthetic-private"
        )
    else:
        session.post.return_value = MagicMock(status_code=400, text="synthetic-private")
    with (
        patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
        patch(
            "agentcc.services.webhook_delivery.build_ssrf_safe_session",
            return_value=session,
        ),
        patch("agentcc.services.webhook_delivery.logger") as logger,
    ):
        assert deliver_webhook_events(event_ids=[event.pk])["failed"] == 1
    assert "synthetic-private" not in repr(logger.mock_calls)
    assert "exc_info" not in repr(logger.mock_calls)
    response = auth_client.get(f"/agentcc/webhook-events/{event.pk}/")
    assert response.status_code == 200
    assert "synthetic-private" not in response.content.decode()
    event.refresh_from_db()
    assert "synthetic-private" not in event.last_error


@pytest.mark.parametrize("version", [None, 0, 2, True])
def test_loader_rejects_versions_before_body_construction(organization, version):
    from agentcc.services.webhook_delivery import signed_request

    hook, _ = fixture(organization)
    hook.credential_storage_version = version
    with patch("agentcc.services.webhook_delivery.json.dumps") as serialize:
        with pytest.raises(codec.WebhookCredentialStorageError):
            signed_request({}, hook)
        serialize.assert_not_called()


def test_db_memoryview_is_the_only_additional_ciphertext_adapter(organization):
    from agentcc.services.webhook_delivery import signed_request

    hook, _ = fixture(organization)
    hook.encrypted_headers = memoryview(hook.encrypted_headers)
    hook.encrypted_secret = memoryview(hook.encrypted_secret)
    _, headers = signed_request({}, hook)
    assert headers["X-Auth"] == "synthetic-private"
    hook.encrypted_secret = bytearray(hook.encrypted_secret)
    with pytest.raises(codec.WebhookCredentialStorageError):
        signed_request({}, hook)


@pytest.mark.parametrize("field", ["headers", "secret"])
@pytest.mark.parametrize("blob", [None, b""])
def test_missing_ciphertext_never_means_empty(organization, field, blob):
    from agentcc.services.webhook_delivery import signed_request

    hook, _ = fixture(organization)
    setattr(hook, "encrypted_" + field, blob)
    with patch("agentcc.services.webhook_delivery.json.dumps") as serialize:
        with pytest.raises(codec.WebhookCredentialStorageError):
            signed_request({}, hook)
        serialize.assert_not_called()


def test_arbitrary_bytes_conversion_hook_is_never_called(organization):
    from agentcc.services.webhook_delivery import signed_request

    calls = []

    class Blob:
        def __bytes__(self):
            calls.append("bytes")
            return b"ignored"

    hook, _ = fixture(organization)
    hook.encrypted_headers = Blob()
    with pytest.raises(codec.WebhookCredentialStorageError):
        signed_request({}, hook)
    assert calls == []

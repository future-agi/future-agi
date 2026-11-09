"""Synthetic encrypted ORM fixtures; no production credential/key source."""

import pytest
from cryptography.fernet import Fernet

from agentcc.models.webhook import AgentccWebhook
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.webhook_headers import UnsupportedHeaders, header_map


@pytest.fixture(autouse=True)
def webhook_encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


def context(hook, field):
    return codec.StorageContext(
        str(hook.organization_id), str(hook.id), field, hook.url
    )


def make_webhook(*, headers=None, secret="", manager=None, **fields):
    """Represent the migration result for a historical row, including reentry.

    An explicit manager or alias-bound queryset retains its own create semantics.
    """
    hook = AgentccWebhook(**fields)
    try:
        normalized = header_map(headers)
    except UnsupportedHeaders:
        hook.encrypted_headers = codec.preserve_for_migration(
            context(hook, "headers"), headers
        )
    else:
        hook.encrypted_headers = codec.encrypt_for_storage(
            context(hook, "headers"), normalized
        )
    hook.encrypted_secret = codec.encrypt_for_storage(context(hook, "secret"), secret)
    hook.credential_storage_version = codec.STORAGE_VERSION
    if manager is not None:
        fields.update(
            id=hook.id,
            encrypted_headers=hook.encrypted_headers,
            encrypted_secret=hook.encrypted_secret,
            credential_storage_version=hook.credential_storage_version,
        )
        return manager.create(**fields)
    hook.save()
    return hook


def stored_value(hook, field):
    """Independent test-only inspection; preserved originals never enter the app."""
    blob = getattr(hook, "encrypted_" + field)
    if type(blob) is memoryview:
        blob = blob.tobytes()
    kwargs = {"storage_version": hook.credential_storage_version, "ciphertext": blob}
    try:
        return codec.decrypt_for_delivery(context(hook, field), **kwargs)
    except codec.WebhookCredentialStorageError as exc:
        if exc.code != codec.ErrorCode.NEEDS_REENTRY:
            raise
    return codec.recover_for_migration(context(hook, field), **kwargs)

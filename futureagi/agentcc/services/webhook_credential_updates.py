"""Atomic webhook mutations after serializer validation and authorization.

No model hooks, signals, plaintext fallback or generic decryption. Preserving a
field copies its ciphertext under the same context; changing context requires
resupply, except for authenticated empty fields under the existing API rules.
"""

from django.db import router, transaction
from rest_framework.exceptions import ValidationError

from agentcc.models.webhook import AgentccWebhook
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.secret_mask import MASKED_SECRET
from agentcc.services.webhook_credential_access import _active_field, storage_context

WEBHOOK_SECRETS_REQUIRED = (
    "Enter the header values and the signing secret again (or an empty "
    "secret to remove it): saved ones are only sent to the URL they were "
    "saved for."
)
MASKED_HEADER_UNKNOWN = (
    "A masked header value keeps the value saved under the same header name, "
    "and there is none: enter the value."
)
_METADATA_FIELDS = frozenset({"name", "url", "events", "is_active", "description"})


def _unmask_headers(headers, stored, new_url):
    saved = {name.lower(): value for name, value in stored.items()}
    resolved = {}
    for name, value in headers.items():
        if value == MASKED_SECRET:
            if new_url:
                raise ValidationError({"headers": WEBHOOK_SECRETS_REQUIRED})
            if name.lower() not in saved:
                raise ValidationError({"headers": MASKED_HEADER_UNKNOWN})
            value = saved[name.lower()]
        resolved[name] = value
    return resolved


def create_webhook(validated_data):
    values = dict(validated_data)
    headers = _unmask_headers(values.pop("headers", {}), {}, False)
    secret = values.pop("secret", "")
    row = AgentccWebhook(**values)
    using = router.db_for_write(AgentccWebhook, instance=row)
    with transaction.atomic(using=using):
        row.encrypted_headers = codec.encrypt_for_storage(
            storage_context(row, "headers"), headers
        )
        row.encrypted_secret = codec.encrypt_for_storage(
            storage_context(row, "secret"), secret
        )
        row.credential_storage_version = codec.STORAGE_VERSION
        row.save(using=using, force_insert=True)
    return row


def _credential_changes(row, changes):
    """Resolve only values needed for this write, on the locked authoritative row."""
    destination = changes.get("url", row.url)
    new_url = destination != row.url
    supplied = {
        field: changes.pop(field) for field in ("headers", "secret") if field in changes
    }
    if "headers" in supplied:
        headers = supplied["headers"]
        has_mask = any(value == MASKED_SECRET for value in headers.values())
        stored = _active_field(row, "headers") if has_mask and not new_url else {}
        supplied["headers"] = _unmask_headers(headers, stored, new_url)
    if new_url:
        for field in ("headers", "secret"):
            if field in supplied:
                continue
            try:
                previous = _active_field(row, field)
            except codec.WebhookCredentialStorageError as failure:
                if failure.code != codec.ErrorCode.NEEDS_REENTRY:
                    raise
                raise ValidationError({field: WEBHOOK_SECRETS_REQUIRED}) from None
            if previous:
                raise ValidationError({field: WEBHOOK_SECRETS_REQUIRED})
            # Empty is carried to a new destination only after authentication.
            supplied[field] = previous
    if not supplied:
        return {}
    if len(supplied) != 2 and (
        type(row.credential_storage_version) is not int
        or row.credential_storage_version != codec.STORAGE_VERSION
    ):
        raise codec.WebhookCredentialStorageError(codec.ErrorCode.UNSUPPORTED_VERSION)
    encrypted = {
        "encrypted_" + field: codec.encrypt_for_storage(
            storage_context(row, field, destination=destination), value
        )
        for field, value in supplied.items()
    }
    encrypted["credential_storage_version"] = codec.STORAGE_VERSION
    return encrypted


def update_webhook(authorized_row, validated_data):
    """Reload and lock before preserving credentials or changing their context.

    db_for_write, not the possibly replica-read instance alias, selects BOTH the
    lock and save transaction. The organization's authorization scope survives
    the reload. Only submitted columns are saved, preserving budget dedupe state.
    """
    changes = dict(validated_data)
    if changes.keys() - (_METADATA_FIELDS | {"headers", "secret"}):
        raise ValueError("Unsupported webhook update")
    using = router.db_for_write(AgentccWebhook, instance=authorized_row)
    with transaction.atomic(using=using):
        row = (
            AgentccWebhook.no_workspace_objects.using(using)
            .select_for_update()
            .get(pk=authorized_row.pk, organization_id=authorized_row.organization_id)
        )
        changes.update(_credential_changes(row, changes))
        for name, value in changes.items():
            setattr(row, name, value)
        row.save(using=using, update_fields=[*changes, "updated_at"])
    return row

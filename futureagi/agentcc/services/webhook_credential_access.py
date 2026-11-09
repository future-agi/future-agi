"""Explicit credential boundaries for already-authorized webhook rows.

The API read boundary returns ONLY the established header-name mask. It never
reads the signing secret or recovers opaque migration originals. No accessor is
an ORM property and no decrypted material is cached on a model or queued.
"""

from dataclasses import dataclass
from uuid import UUID

from agentcc.models.webhook import AgentccWebhook
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.secret_mask import MASKED_SECRET

WEBHOOK_CREDENTIALS_UNAVAILABLE = "Webhook credentials could not be used."


@dataclass(frozen=True, repr=False)
class DeliveryMaterial:
    headers: dict
    secret: str


def storage_context(row, field, *, destination=None):
    """Context is taken from a scoped ORM row, never client-supplied bindings."""
    if (
        not isinstance(row, AgentccWebhook)
        or type(row.pk) is not UUID
        or type(row.organization_id) is not UUID
    ):
        raise codec.WebhookCredentialStorageError(codec.ErrorCode.INVALID_CONTEXT)
    return codec.StorageContext(
        str(row.organization_id),
        str(row.pk),
        field,
        row.url if destination is None else destination,
    )


def _active_field(row, field):
    """Only delivery, masked representation and locked updates may call this."""
    trusted = storage_context(row, field)
    ciphertext = getattr(row, "encrypted_" + field)
    # Django's PostgreSQL BinaryField adapter is the sole coercion boundary.
    # Never call bytes() on arbitrary objects or reinterpret NULL as empty.
    if type(ciphertext) is memoryview and not row._state.adding and row._state.db:
        ciphertext = ciphertext.tobytes()
    return codec.decrypt_for_delivery(
        trusted,
        storage_version=row.credential_storage_version,
        ciphertext=ciphertext,
    )


def masked_headers(authorized_row):
    """Restricted read DTO: header names and masks only, including legacy shapes.

    An opaque needs-reentry original retains the pre-migration unsupported-shape
    read contract ({}); this is NOT evidence that its credentials are empty or
    deliverable. Every other authentication/storage failure refuses the read.
    """
    try:
        headers = _active_field(authorized_row, "headers")
    except codec.WebhookCredentialStorageError as failure:
        if failure.code != codec.ErrorCode.NEEDS_REENTRY:
            raise
        return {}
    return dict.fromkeys(headers, MASKED_SECRET)


def load_delivery_material(authorized_row):
    """Authenticate BOTH fields before a body, headers, signature or transport."""
    headers = _active_field(authorized_row, "headers")
    secret = _active_field(authorized_row, "secret")
    return DeliveryMaterial(headers=headers, secret=secret)

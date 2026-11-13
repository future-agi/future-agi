"""Short-lived sealed source references (B07 secret envelopes).

A signed retrieval URL is a secret. It exists in plain text only inside a
trusted importer process: sealed with AES-256-GCM under a dedicated key (never
SECRET_KEY), additionally bound to its scope and envelope id, and kept for at
most 10 minutes or the source's own expiry, whichever is sooner. Jobs, queues,
history, retries and dead letters carry the envelope UUID only.

Creating an envelope is a server-internal trusted-resolver operation, not part
of the generic import body; the import request can only name an envelope id.
"""

import base64
import os
import uuid
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tracer.models.attachment_recording import AttachmentSourceEnvelope
from tracer.services.attachments.contract import AttachmentError

ENVELOPE_SECONDS = 600


def _keys():
    """``FI_ATTACHMENTS_ENVELOPE_KEYS`` = "id:base64url32[,id:base64url32...]".

    The first key seals; every listed key may open (rotation). Missing or
    malformed keys make envelope sources unavailable, never plaintext.
    """
    raw = getattr(settings, "FI_ATTACHMENTS_ENVELOPE_KEYS", "") or ""
    keys = {}
    order = []
    try:
        for item in raw.split(","):
            if not item.strip():
                continue
            key_id, encoded = item.strip().split(":", 1)
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if not key_id.isalnum() or len(key_id) > 16 or len(key) != 32:
                raise ValueError
            keys[key_id] = key
            order.append(key_id)
    except ValueError:
        raise AttachmentError("source_unavailable", 503) from None
    if not order:
        raise AttachmentError("source_unavailable", 503)
    return order[0], keys


def seal_bytes(plaintext, aad):
    key_id, keys = _keys()
    nonce = os.urandom(12)
    return key_id, nonce, AESGCM(keys[key_id]).encrypt(nonce, plaintext, aad)


def open_bytes(key_id, nonce, ciphertext, aad):
    _, keys = _keys()
    try:
        return AESGCM(keys[key_id]).decrypt(bytes(nonce), bytes(ciphertext), aad)
    except (InvalidTag, KeyError, ValueError, TypeError):
        # Wrong or rotated-out key id, malformed nonce or key, or a forged or
        # corrupted ciphertext: all indistinguishable to the caller.
        raise AttachmentError("source_unavailable", 403) from None


def _aad(scope_id, envelope_id):
    return f"fi.attachments.v1.envelope:{scope_id}:{envelope_id}".encode()


def seal(
    *,
    scope,
    url,
    creator_kind,
    creator_id,
    direction,
    source_bytes,
    source_expires_at=None,
):
    """Trusted resolver only. Returns the opaque envelope id.

    The resolver asserts the source is a terminal, immutable, range-capable
    object of exactly ``source_bytes``; the importer re-verifies every byte.
    """
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 4096:
        raise AttachmentError("source_unavailable", 403)
    if type(source_bytes) is not int or not 0 < source_bytes <= 1024**3:
        raise AttachmentError("too_large", 413)
    now = timezone.now()
    expires_at = now + timedelta(seconds=ENVELOPE_SECONDS)
    if source_expires_at is not None:
        expires_at = min(expires_at, source_expires_at)
    if expires_at <= now:
        raise AttachmentError("expired", 410)
    envelope_id = uuid.uuid4()
    key_id, nonce, ciphertext = seal_bytes(url.encode(), _aad(scope.id, envelope_id))
    AttachmentSourceEnvelope.objects.create(
        id=envelope_id,
        scope=scope,
        creator_kind=creator_kind,
        creator_id=creator_id,
        direction=direction,
        source_bytes=source_bytes,
        key_id=key_id,
        nonce=nonce,
        ciphertext=ciphertext,
        expires_at=expires_at,
    )
    return envelope_id


def describe(*, scope_id, envelope_id):
    """Non-secret facts for admission: owner, direction and expiry only."""
    row = AttachmentSourceEnvelope.objects.filter(
        pk=envelope_id, scope_id=scope_id, expires_at__gt=timezone.now()
    ).first()
    if row is None:
        raise AttachmentError("source_unavailable", 403)
    return row


def open_url(*, scope_id, envelope_id):
    """Importer only. The plaintext must not be stored, logged or raised."""
    row = describe(scope_id=scope_id, envelope_id=envelope_id)
    return open_bytes(
        row.key_id, row.nonce, row.ciphertext, _aad(scope_id, envelope_id)
    ).decode()


def destroy(*, scope_id, envelope_id):
    AttachmentSourceEnvelope.objects.filter(pk=envelope_id, scope_id=scope_id).delete()


@transaction.atomic
def sweep(*, limit=500):
    """Delete expired envelopes; scheduled by the attachment sweep workflow."""
    ids = list(
        AttachmentSourceEnvelope.objects.filter(
            expires_at__lte=timezone.now()
        ).values_list("pk", flat=True)[:limit]
    )
    AttachmentSourceEnvelope.objects.filter(pk__in=ids).delete()
    return len(ids)

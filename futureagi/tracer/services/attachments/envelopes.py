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
import json
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
from tracer.services.attachments.interfaces import SourceFacts
from tracer.services.attachments.safe_fetch import (
    parse_destination,
    validate_revision_query,
)

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
    source_expires_at,
    generation_kind,
    generation,
    mime_type,
    duration_ms,
    trace_id,
    span_id,
    capture_session_id,
    authority_generation=0,
):
    """Trusted producer only: exact metadata and original ancestry are mandatory.

    HTTP routes cannot supply a URL or assert these observations. The provider
    intake seals only after signature verification and metadata-only inspection.
    The encrypted document binds the locator to the same pinned revision.
    """
    if not isinstance(capture_session_id, uuid.UUID):
        raise AttachmentError("source_unavailable", 403)
    destination = parse_destination(
        url, approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ())
    )
    validate_revision_query(destination, generation_kind, generation)
    now = timezone.now()
    envelope_id = uuid.uuid4()
    try:
        expires_at = min(now + timedelta(seconds=ENVELOPE_SECONDS), source_expires_at)
        facts = SourceFacts(
            source_kind="RemoteUrlEnvelope",
            source_id=envelope_id,
            creator_kind=creator_kind,
            creator_id=creator_id,
            generation_kind=generation_kind,
            generation=generation,
            byte_length=source_bytes,
            mime_type=mime_type,
            duration_ms=duration_ms,
            expires_at=expires_at,
            authority_generation=authority_generation,
            parent_trace_id=trace_id,
            parent_span_id=span_id,
            direction=direction,
            connection_id=None,
        )
    except (TypeError, ValueError):
        raise AttachmentError("source_unavailable", 403) from None
    if facts.byte_length > 1024**3:
        raise AttachmentError("too_large", 413)
    if expires_at <= now:
        raise AttachmentError("expired", 410)
    document = json.dumps(
        {
            "url": url,
            "generation_kind": generation_kind,
            "generation": generation,
            "capture_session_id": str(capture_session_id),
        },
        separators=(",", ":"),
    ).encode()
    if len(document) > 4184:
        raise AttachmentError("source_unavailable", 403)
    key_id, nonce, ciphertext = seal_bytes(document, _aad(scope.id, envelope_id))
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
        source_expires_at=source_expires_at,
        generation_kind=generation_kind,
        generation=generation,
        mime_type=mime_type,
        duration_ms=duration_ms,
        trace_id=trace_id,
        span_id=span_id,
        authority_generation=authority_generation,
    )
    return envelope_id


def describe(*, scope_id, envelope_id):
    """Non-secret facts for admission: owner, direction and expiry only."""
    row = AttachmentSourceEnvelope.objects.filter(
        pk=envelope_id,
        scope_id=scope_id,
        expires_at__gt=timezone.now(),
        source_expires_at__gt=timezone.now(),
        revoked_at__isnull=True,
    ).first()
    if row is None:
        raise AttachmentError("source_unavailable", 403)
    return row


def _document(row):
    try:
        document = json.loads(
            open_bytes(
                row.key_id, row.nonce, row.ciphertext, _aad(row.scope_id, row.id)
            ).decode()
        )
        if (
            set(document)
            != {"url", "generation_kind", "generation", "capture_session_id"}
            or document["generation_kind"] != row.generation_kind
            or document["generation"] != row.generation
            or str(uuid.UUID(document["capture_session_id"]))
            != document["capture_session_id"]
        ):
            raise ValueError
        destination = parse_destination(
            document["url"],
            approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ()),
        )
        validate_revision_query(destination, row.generation_kind, row.generation)
        return document
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise AttachmentError("source_unavailable", 403) from None


def check_capture_session(row, capture_session_id):
    """Local sealed provenance check only; performs no source HTTP request."""
    if _document(row)["capture_session_id"] != str(capture_session_id):
        raise AttachmentError("source_unavailable", 403)


def open_url(*, scope_id, envelope_id):
    """Reserved importer only. Plaintext must never be stored/logged/raised."""
    row = describe(scope_id=scope_id, envelope_id=envelope_id)
    if row.scope_id != scope_id or row.id != envelope_id:
        raise AttachmentError("source_unavailable", 403)
    return _document(row)["url"]


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

"""Server-only credential for the internal websocket relay.

``model_hub.utils.call_websocket`` posts worker updates to ``CallWebsocketView``.
Besides the organization's system API key, each request carries a signature
derived from ``SECRET_KEY``. The signature covers the organization and the exact
request body, and it is never issued to users or clients, so only processes
that run with this install's settings can publish through the relay.
"""

from __future__ import annotations

import hmac

from django.utils.crypto import salted_hmac

RELAY_SIGNATURE_HEADER = "X-Relay-Signature"
_SIGNATURE_SALT = "tfc.websocket_relay.v1"


def relay_signature(organization_id, body: bytes) -> str:
    """Return the relay signature for ``body`` sent on behalf of an organization."""

    value = f"{organization_id}\n".encode() + bytes(body)
    return salted_hmac(_SIGNATURE_SALT, value, algorithm="sha256").hexdigest()


def relay_signature_is_valid(organization_id, body: bytes, signature) -> bool:
    """Whether ``signature`` was produced by :func:`relay_signature`."""

    if organization_id is None or not isinstance(signature, str) or not signature:
        return False
    expected = relay_signature(organization_id, body)
    return hmac.compare_digest(expected, signature)

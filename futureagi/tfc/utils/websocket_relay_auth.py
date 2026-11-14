"""Server-only credential for the internal websocket relay.

``model_hub.utils.call_websocket`` posts worker updates to ``CallWebsocketView``.
Besides the organization's system API key, each request carries a signature
derived from ``SECRET_KEY``. The signature covers the organization, the time it
was made and the exact request body. It is never issued to users or clients,
so only processes that run with this install's settings can publish through
the relay, and a request is refused once its time is outside the window.
"""

from __future__ import annotations

import hmac
import re
import time

from django.utils.crypto import salted_hmac

RELAY_SIGNATURE_HEADER = "X-Relay-Signature"
RELAY_TIMESTAMP_HEADER = "X-Relay-Timestamp"
# A signature is accepted this many seconds either side of the receiver's
# clock, so a captured request cannot be sent again later.
RELAY_MAX_SKEW_SECONDS = 300
_SIGNATURE_SALT = "tfc.websocket_relay.v2"
_TIMESTAMP = re.compile(r"[0-9]{1,12}")


def relay_signature(organization_id, body: bytes, timestamp: int) -> str:
    """Return the relay signature for ``body`` sent on behalf of an
    organization at ``timestamp`` (unix seconds)."""

    value = f"{organization_id}\n{int(timestamp)}\n".encode() + bytes(body)
    return salted_hmac(_SIGNATURE_SALT, value, algorithm="sha256").hexdigest()


def relay_headers(organization_id, body: bytes, *, now: float | None = None) -> dict:
    """The signature and timestamp headers for one relay request."""

    timestamp = int(time.time() if now is None else now)
    return {
        RELAY_TIMESTAMP_HEADER: str(timestamp),
        RELAY_SIGNATURE_HEADER: relay_signature(organization_id, body, timestamp),
    }


def relay_signature_is_valid(
    organization_id, body: bytes, signature, timestamp, *, now: float | None = None
) -> bool:
    """Whether ``signature`` was produced by :func:`relay_signature` for this
    body at ``timestamp``, and that time is within the accepted window. A
    missing or unparseable timestamp is refused like a wrong signature."""

    if organization_id is None or not isinstance(signature, str) or not signature:
        return False
    if not isinstance(timestamp, str) or not _TIMESTAMP.fullmatch(timestamp):
        return False
    signed_at = int(timestamp)
    current = time.time() if now is None else now
    if abs(current - signed_at) > RELAY_MAX_SKEW_SECONDS:
        return False
    expected = relay_signature(organization_id, body, signed_at)
    # compare_digest only accepts ASCII strings; compare bytes so a header
    # with other characters is a mismatch rather than an error.
    return hmac.compare_digest(
        expected.encode("ascii"), signature.encode("utf-8", "surrogatepass")
    )

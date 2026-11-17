"""Sign the FA -> Omega EE event body (READINESS 1.2a). No Django or database dependency.

The header is ``X-Omega-Host-Signature: t=<unix seconds>,kid=<key id>,v1=<hex>``, where
``v1`` is HMAC-SHA256 over ``"<t>." + body`` with the shared secret named by ``kid``.
EE refuses a missing, malformed or wrong signature, a ``t`` more than five minutes from
its clock, and an event id it has already applied. ``contract/omega-host-events.v1.json``
holds the test vectors both sides check.
"""

import hashlib
import hmac
import json
import re
import time

SIGNATURE_HEADER = "X-Omega-Host-Signature"
SCHEMA_VERSION = "omega-host-events/v1"
TOLERANCE_SECONDS = 300
MIN_SECRET_BYTES = 32
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)


def encode_body(issuer, events):
    """The exact bytes that are signed and sent: compact, key-sorted JSON."""
    document = {"schemaVersion": SCHEMA_VERSION, "issuer": issuer, "events": events}
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")


def signature(secret, timestamp, body):
    if not isinstance(secret, bytes) or len(secret) < MIN_SECRET_BYTES:
        raise ValueError("The event signing secret must be at least 32 bytes.")
    if type(timestamp) is not int or timestamp < 0:
        raise ValueError("Invalid signing timestamp.")
    message = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signature_header(secret, key_id, body, now=None):
    if not KEY_ID.fullmatch(key_id or ""):
        raise ValueError("Invalid signing key id.")
    timestamp = int(time.time()) if now is None else now
    return f"t={timestamp},kid={key_id},v1={signature(secret, timestamp, body)}"

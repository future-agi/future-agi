"""Standard Webhooks v1 signing and verification (DESIGN.md section 5)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re

SECRET_PREFIX = "whsec_"

TOLERANCE_S = 300


class SecretInvalid(ValueError):
    pass


def secret_key(secret: str) -> bytes:
    """Decode a ``whsec_`` secret. Raises SecretInvalid with a reason id."""
    if not secret.startswith(SECRET_PREFIX):
        raise SecretInvalid("missing_prefix")
    raw = secret[len(SECRET_PREFIX) :]
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SecretInvalid("not_base64") from None
    if base64.b64encode(key).decode() != raw:
        raise SecretInvalid("not_base64")
    if not 24 <= len(key) <= 64:
        raise SecretInvalid("bad_length")
    return key


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    key = secret_key(secret)
    signed = (
        msg_id.encode("ascii") + b"." + str(timestamp).encode("ascii") + b"." + body
    )
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode(
        "ascii"
    )


def signature_header(
    secrets_newest_first: list[str], msg_id: str, timestamp: int, body: bytes
) -> str:
    return " ".join(
        "v1," + sign(s, msg_id, timestamp, body) for s in secrets_newest_first
    )


def verify(
    secret: str,
    headers: dict[str, str],
    body: bytes,
    now: int,
    tolerance_s: int = TOLERANCE_S,
) -> str:
    """Returns "ok" or a reason id: missing_headers, invalid_timestamp, timestamp_too_old, timestamp_too_new,
    no_matching_signature. Header names are matched case-insensitively."""
    h = {k.lower(): v for k, v in headers.items()}
    if not all(
        n in h for n in ("webhook-id", "webhook-timestamp", "webhook-signature")
    ):
        return "missing_headers"
    ts = h["webhook-timestamp"]
    if not re.fullmatch(r"[0-9]{1,12}", ts):
        return "invalid_timestamp"
    t = int(ts)
    if now - t > tolerance_s:
        return "timestamp_too_old"
    if t - now > tolerance_s:
        return "timestamp_too_new"
    expected = sign(secret, h["webhook-id"], t, body)
    for item in h["webhook-signature"].split(" "):
        version, _, value = item.partition(",")
        if version == "v1" and hmac.compare_digest(value.encode(), expected.encode()):
            return "ok"
    return "no_matching_signature"


def attempt_headers(
    *, msg_id, timestamp, signature, event_type, delivery_id, attempt, sequence, test
) -> dict[str, str]:
    """The headers of one attempt, in send order (DESIGN.md section 5.1); custom headers excluded."""
    out = {
        "content-type": "application/json; charset=utf-8",
        "user-agent": "FutureAGI-Webhooks/1.0",
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": signature,
        "X-FutureAGI-Event": event_type,
        "X-FutureAGI-Delivery": delivery_id,
        "X-FutureAGI-Attempt": str(attempt),
    }
    if test:
        out["X-FutureAGI-Test"] = "1"
    else:
        out["X-FutureAGI-Sequence"] = str(sequence)
    return out

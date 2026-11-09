"""Standard Webhooks v1 signing and verification, secret format, rotation overlap and the headers of one attempt
(DESIGN.md sections 5.1-5.3; conformance ``signing.json`` and ``signing-v1.json``)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets as _secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

SECRET_PREFIX = "whsec_"
TOLERANCE_S = 300
KEY_BYTES_NEW = 32
KEY_BYTES_MIN = 24
KEY_BYTES_MAX = 64
USER_AGENT = "FutureAGI-Webhooks/1.0"
CONTENT_TYPE = "application/json; charset=utf-8"
DEFAULT_OVERLAP_S = 24 * 3600
MAX_OVERLAP_S = 7 * 24 * 3600

VERIFY_RESULTS = (
    "ok",
    "missing_headers",
    "invalid_timestamp",
    "timestamp_too_old",
    "timestamp_too_new",
    "no_matching_signature",
)


class SecretInvalid(ValueError):
    """A secret that is not ``whsec_`` + padded standard base64 of 24-64 bytes. ``args[0]`` is the reason id:
    ``missing_prefix``, ``not_base64`` or ``bad_length``."""


def secret_key(secret: str) -> bytes:
    """The HMAC key: the decoded bytes after ``whsec_``."""
    if not isinstance(secret, str) or not secret.startswith(SECRET_PREFIX):
        raise SecretInvalid("missing_prefix")
    raw = secret[len(SECRET_PREFIX) :]
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SecretInvalid("not_base64") from None
    if base64.b64encode(key).decode("ascii") != raw:
        raise SecretInvalid("not_base64")
    if not KEY_BYTES_MIN <= len(key) <= KEY_BYTES_MAX:
        raise SecretInvalid("bad_length")
    return key


def secret_from_key(key: bytes) -> str:
    if not KEY_BYTES_MIN <= len(key) <= KEY_BYTES_MAX:
        raise SecretInvalid("bad_length")
    return SECRET_PREFIX + base64.b64encode(key).decode("ascii")


def new_secret(token_bytes=_secrets.token_bytes) -> str:
    """A fresh ``whsec_`` secret of 32 random bytes (section 5.3)."""
    return secret_from_key(token_bytes(KEY_BYTES_NEW))


def secret_tail(secret: str) -> str:
    """The last 4 characters, the only part ever shown again."""
    return secret[-4:]


def _signed_content(msg_id: str, timestamp: int, body: bytes) -> bytes:
    if "." in msg_id:
        raise ValueError("webhook-id must not contain '.'")
    return (
        msg_id.encode("ascii")
        + b"."
        + str(int(timestamp)).encode("ascii")
        + b"."
        + body
    )


def sign_key(key: bytes, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(
        key, _signed_content(msg_id, timestamp, body), hashlib.sha256
    ).digest()
    return base64.b64encode(mac).decode("ascii")


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """base64(HMAC-SHA256(key, "<id>.<t>.<body>"))."""
    return sign_key(secret_key(secret), msg_id, timestamp, body)


def signature_header(
    secrets_newest_first: Iterable[str], msg_id: str, timestamp: int, body: bytes
) -> str:
    """``v1,<sig>`` per live secret, newest first, space-delimited (an overlap sends two)."""
    items = ["v1," + sign(s, msg_id, timestamp, body) for s in secrets_newest_first]
    if not items:
        raise ValueError("no signing secret")
    return " ".join(items)


def verify(
    secret: str,
    headers: Mapping[str, str],
    body: bytes,
    now: int,
    tolerance_s: int = TOLERANCE_S,
) -> str:
    """``"ok"`` or a reason id from :data:`VERIFY_RESULTS`. Header names match case-insensitively; any ``v1`` entry
    may match, entries with an unknown version are ignored; the comparison is constant time."""
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
    try:
        expected = sign(secret, h["webhook-id"], t, body).encode("ascii")
    except (ValueError, UnicodeEncodeError):
        return "no_matching_signature"
    ok = False
    for item in h["webhook-signature"].split(" "):
        version, _, value = item.partition(",")
        if version == "v1" and hmac.compare_digest(
            value.encode("utf-8", "replace"), expected
        ):
            ok = True
    return "ok" if ok else "no_matching_signature"


def attempt_headers(
    *,
    msg_id: str,
    timestamp: int,
    signature: str,
    event_type: str,
    delivery_id: str,
    attempt: int,
    sequence: int | None,
    test: bool,
) -> dict[str, str]:
    """The headers the core builds for one attempt, in send order (section 5.1). Custom headers follow; the
    Transport adds Host, Content-Length and Accept-Encoding: identity."""
    out = {
        "content-type": CONTENT_TYPE,
        "user-agent": USER_AGENT,
        "webhook-id": msg_id,
        "webhook-timestamp": str(int(timestamp)),
        "webhook-signature": signature,
        "X-FutureAGI-Event": event_type,
        "X-FutureAGI-Delivery": delivery_id,
        "X-FutureAGI-Attempt": str(int(attempt)),
    }
    if test:
        out["X-FutureAGI-Test"] = "1"
    else:
        if sequence is None:
            raise ValueError("a real delivery carries a sequence")
        out["X-FutureAGI-Sequence"] = str(int(sequence))
    return out


# ---- custom static headers (section 5.1, S10) -------------------------------------------------------------------

RESERVED_HEADER_NAMES = frozenset(
    {
        "content-type",
        "content-length",
        "host",
        "user-agent",
        "connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "expect",
        "keep-alive",
        "cookie",
        "accept-encoding",
        "proxy-authorization",
        "forwarded",
        "via",
        "x-real-ip",
    }
)
RESERVED_HEADER_PREFIXES = ("proxy-", "x-forwarded-", "webhook-", "x-futureagi-")
MAX_CUSTOM_HEADERS = 20
MAX_CUSTOM_HEADER_BYTES = 4096
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


@dataclass(frozen=True)
class HeaderProblem:
    code: str
    name: str | None


def custom_header_problems(headers: Mapping[str, str]) -> list[HeaderProblem]:
    """Every rule broken by an endpoint's static headers; empty when they may be saved. Values are literal: nothing
    is ever expanded (S4)."""
    out: list[HeaderProblem] = []
    if len(headers) > MAX_CUSTOM_HEADERS:
        out.append(HeaderProblem("too_many", None))
    total = 0
    for name, value in headers.items():
        low = name.lower()
        if not _TOKEN.fullmatch(name):
            out.append(HeaderProblem("bad_name", name))
        elif low in RESERVED_HEADER_NAMES or low.startswith(RESERVED_HEADER_PREFIXES):
            out.append(HeaderProblem("reserved", name))
        if not isinstance(value, str) or any(
            ord(c) < 0x20 and c != "\t" or ord(c) == 0x7F for c in value
        ):
            out.append(HeaderProblem("bad_value", name))
        total += len(name.encode("utf-8")) + len(str(value).encode("utf-8"))
    if total > MAX_CUSTOM_HEADER_BYTES:
        out.append(HeaderProblem("too_large", None))
    return out


def mask_signature_header(value: str) -> str:
    """``v1,…sHc=`` per entry: what the delivery log stores (section 8.1)."""
    return " ".join(
        (item.partition(",")[0] + ",…" + item[-4:]) if "," in item else "…"
        for item in value.split(" ")
    )


def mask_header_value(value: str) -> str:
    """Custom header values: ``••••`` + the last 4 characters, fully masked under 12 characters."""
    return "••••" if len(value) < 12 else "••••" + value[-4:]

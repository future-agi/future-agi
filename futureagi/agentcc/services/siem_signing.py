"""How a SIEM export identifies itself to the receiving collector.

A webhook is signed as Standard Webhooks specifies (standardwebhooks.com):
``webhook-id`` is the delivery's id (the same on every retry, so a receiver
can drop a repeat), ``webhook-timestamp`` the attempt's Unix time, and
``webhook-signature`` ``v1,`` plus the base64 HMAC-SHA256 of
``<id>.<timestamp>.<body>`` keyed with the secret's bytes (``whsec_`` + base64).
An OTLP/HTTP collector receives its configured auth header, if it has one.
A webhook whose secret is missing or is not a signing secret is never sent:
an empty HMAC key would sign it with a signature anyone can compute.
"""

import base64
import hashlib
import hmac
import json
import secrets

from agentcc.models.siem import AgentccSIEMDestination
from agentcc.services.credential_manager import decrypt_token

SECRET_PREFIX = "whsec_"
SECRET_BYTES = 32
USER_AGENT = "agentcc-siem-export/1"


class SigningSecretMissing(Exception):
    """A webhook destination has no usable signing secret."""


def new_signing_secret():
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(SECRET_BYTES)).decode()


def encode(body):
    return json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()


def _signing_key(secret):
    if not secret or not secret.startswith(SECRET_PREFIX):
        raise SigningSecretMissing()
    try:
        key = base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except ValueError:
        raise SigningSecretMissing() from None
    if not key:
        raise SigningSecretMissing()
    return key


def signature(secret, message_id, timestamp, raw):
    key = _signing_key(secret)
    signed = b".".join([message_id.encode(), str(timestamp).encode(), raw])
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def headers(destination, delivery, raw, now):
    out = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if destination.kind == AgentccSIEMDestination.Kind.WEBHOOK:
        try:
            secret = decrypt_token(destination.encrypted_secret or "")
        except Exception:  # a secret that no longer decrypts signs nothing
            raise SigningSecretMissing() from None
        message_id = str(delivery.id)
        timestamp = int(now.timestamp())
        out["webhook-id"] = message_id
        out["webhook-timestamp"] = str(timestamp)
        out["webhook-signature"] = signature(secret, message_id, timestamp, raw)
    elif destination.kind == AgentccSIEMDestination.Kind.HEC:
        try:
            secret = decrypt_token(destination.encrypted_secret or "")
        except Exception:
            raise SigningSecretMissing() from None
        if not secret:
            raise SigningSecretMissing()
        out["Authorization"] = "Splunk " + secret
    elif destination.auth_header:
        secret = decrypt_token(destination.encrypted_secret or "")
        if secret:
            out[destination.auth_header] = secret
    return out

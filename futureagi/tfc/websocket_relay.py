"""Deployment-internal signing for the single-process memory-channel relay.

Broker-backed workers publish directly. This signature is accepted nowhere
else and never represents a customer or permits creation of an API key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

AUDIENCE = "fa-websocket-relay-v1"
ROUTE = "/call-websocket/"
WINDOW_SECONDS = 60


class ConsumedNonces:
    """Atomic, bounded replay state for exactly one web process.

    Reject envelopes issued before this verifier started: restarting the web
    process must not make an already-consumed envelope reusable. Workers retry
    with a newly signed request after restart, not with the old envelope.
    """

    def __init__(self, *, started_at=None):
        self.started_at = time.time() if started_at is None else started_at
        self.pid = os.getpid()
        self.lock = threading.Lock()
        self.used = {}

    def consume(self, nonce, issued_at, now):
        with self.lock:
            if self.pid != os.getpid():
                self.pid = os.getpid()
                self.started_at = now
                self.used.clear()
            if (
                issued_at < self.started_at
                or issued_at > now
                or now - issued_at > WINDOW_SECONDS
            ):
                return False
            self.used = {n: expiry for n, expiry in self.used.items() if expiry >= now}
            if nonce in self.used or len(self.used) >= 100_000:
                return False
            # Do not admit future-dated envelopes: otherwise one accepted before
            # a restart could still be newer than the next process's start time.
            self.used[nonce] = issued_at + WINDOW_SECONDS
            return True


_nonces = ConsumedNonces()


def _key():
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"fa-relay-v1"
    ).derive(settings.SECRET_KEY.encode())


def _signature(method, route, audience, timestamp, nonce, org, body):
    text = "\n".join(
        (
            method,
            route,
            audience,
            timestamp,
            nonce,
            org,
            hashlib.sha256(body).hexdigest(),
        )
    )
    return hmac.new(_key(), text.encode(), hashlib.sha256).hexdigest()


def sign_relay(payload, url):
    """Return the exact body bytes and headers; callers must not re-encode it."""
    route = urlsplit(url)
    if route.path != ROUTE or route.query or route.fragment:
        raise ValueError("WEBSOCKET_ENDPOINT must use /call-websocket/ without a query")
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp, nonce = str(time.time()), uuid.uuid4().hex
    org = str(uuid.UUID(str(payload["organization_id"])))
    return body, {
        "Content-Type": "application/json",
        "X-Relay-Audience": AUDIENCE,
        "X-Relay-Timestamp": timestamp,
        "X-Relay-Nonce": nonce,
        "X-Relay-Organization": org,
        "X-Relay-Signature": _signature(
            "POST", ROUTE, AUDIENCE, timestamp, nonce, org, body
        ),
    }


def verify_relay(request):
    """Return the signed organization, or None. Never consult customer keys."""
    if (
        settings.CHANNEL_LAYER_BACKEND != "memory"
        or request.method != "POST"
        or request.path != ROUTE
        or request.META.get("QUERY_STRING")
    ):
        return None
    headers = request.headers
    audience = headers.get("X-Relay-Audience", "")
    timestamp = headers.get("X-Relay-Timestamp", "")
    nonce = headers.get("X-Relay-Nonce", "")
    org = headers.get("X-Relay-Organization", "")
    signature = headers.get("X-Relay-Signature", "")
    if (
        audience != AUDIENCE
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
        or len(timestamp) > 32
    ):
        return None
    try:
        issued_at = float(timestamp)
        if not math.isfinite(issued_at) or org != str(uuid.UUID(org)):
            return None
        body = request.body
        expected = _signature(
            request.method, request.path, audience, timestamp, nonce, org, body
        )
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("organization_id") != org:
            return None
    except (ValueError, TypeError, UnicodeError):
        return None
    if not _nonces.consume(nonce, issued_at, time.time()):
        return None
    return org

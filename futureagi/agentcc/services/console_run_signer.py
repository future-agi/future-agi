"""FA's console-run signer: POST /agentcc/console-run/sign/.

The agent service (EE) calls it for every model call of a Console run, with no
session and no API key. It authenticates by an Ed25519 client assertion in the
``X-Omega-EE-Assertion`` header and hands over the person's run authorization.
FA answers with the gateway grant and per-call envelope the gateway's delegated
chat route asks for (``X-FA-Grant`` and ``X-FA-Envelope``), signed for the body
digest EE sends; FA never sees the prompt and EE never holds the gateway admin
token.

fi-security rulings 1d8b0ce0; wire record lane-status
gwc2197-CONSOLE-RUN-SIGN-WIRE-1114.md (459ac8ed) section 1 and ADDENDUM-1.
Order: switch, body size, assertion (alg before anything else; signature before
the one-time jti is spent), body shape, run authorization (its registered row,
not revoked, not expired, handed to this deployment), live membership, route,
model cap, grant (re-derived while it has more than two minutes left, else
minted), envelope. Every refusal is typed and carries no detail; every decision
is audit-logged without tokens, assertions or content.
"""

import base64
import binascii
import hashlib
import json
import re
import time

import structlog
from cryptography.exceptions import InvalidSignature
from django.db import DatabaseError, transaction

from accounts.models.service_grant import ServiceGrant
from accounts.services.envelope_keys import current_kid, pair_keys
from accounts.services.envelope_principals import live_person
from accounts.services.envelope_store import DurableNonceStore
from accounts.services.service_grants import issue_person_grant
from agentcc.services.console_run_signer_keys import active_key
from agentcc.services.delegated_availability import console_run_enabled
from tfc.service_envelope import (
    EnvelopeRejected,
    derive_grant,
    sign_request,
    verify_grant,
)

audit = structlog.get_logger("agentcc.console_run.audit")

ASSERTION_HEADER = "X-Omega-EE-Assertion"
AUDIENCE = "fa-console-run-signer"
SIGN_ROUTE = "/agentcc/console-run/sign/"
CHAT_ROUTE = "/-/delegated/v1/chat/completions"
ASSERTION_TYP = "omega-ee-assertion+jwt"
ASSERTION_HEADER_KEYS = frozenset({"alg", "kid", "typ"})
ASSERTION_CLAIMS = frozenset(
    {"iss", "aud", "htu", "htm", "iat", "exp", "jti", "bodyhash"}
)
BODY_KEYS = frozenset({"run_authorization", "route", "method", "body_sha256", "model"})
MAX_BODY_BYTES = 8192
MAX_ASSERTION_LENGTH = 4096
ASSERTION_LIFE = 60
IAT_AHEAD = 30
GRANT_TTL = 3600
GRANT_REUSE_MARGIN = 120
ENVELOPE_LIFE = 60
_JTI = re.compile(r"[A-Za-z0-9_-]{22}")
# The durable nonce table every replica shares is keyed (aud, id) and its store
# admits one audience. An assertion's jti goes in under it with a prefix that no
# gateway eval envelope id (32 lowercase hex) can take.
NONCE_AUDIENCE = "fa-eval"
NONCE_PREFIX = "ccr:"
_HEX64 = re.compile(r"[0-9a-f]{64}")


class SignRefused(Exception):
    """A typed refusal: HTTP status and code; the sentence is fixed per code."""

    MESSAGES = {
        "assertion_rejected": "The agent service's assertion was not accepted.",
        "authorization_rejected": "The run authorization was not accepted.",
        "authorization_expired": "The run authorization has expired. Start a new chat.",
        "authorization_revoked": "The run authorization was revoked. Start a new chat.",
        "member_removed": "The person is no longer a member of this workspace.",
        "console_run_disabled": "Console model calls are off on this deployment.",
        "model_not_in_cap": "The model is not one this run may call.",
        "route_rejected": "Only the delegated chat route can be signed.",
        "sign_request_invalid": "The sign request is not valid.",
        "signer_unavailable": "The signer is temporarily unavailable. Please retry.",
    }

    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = self.MESSAGES[code]


def _unb64(segment):
    if not isinstance(segment, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        raise ValueError("not base64url")
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != segment:
        raise ValueError("not canonical base64url")
    return raw


def _canonical_jti(jti):
    if not isinstance(jti, str) or not _JTI.fullmatch(jti):
        return False
    try:
        return len(_unb64(jti)) == 16
    except (ValueError, binascii.Error):
        return False


def _assertion_deployment(assertion, raw_body, now):
    """The verified assertion's deployment id; SignRefused otherwise."""
    rejected = SignRefused(401, "assertion_rejected")
    if not isinstance(assertion, str) or len(assertion) > MAX_ASSERTION_LENGTH:
        raise rejected
    parts = assertion.split(".")
    if len(parts) != 3:
        raise rejected
    try:
        header = json.loads(_unb64(parts[0]))
    except (ValueError, binascii.Error, RecursionError):
        raise rejected from None
    # The algorithm is pinned before anything else is read.
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        raise rejected
    if set(header) != ASSERTION_HEADER_KEYS or header.get("typ") != ASSERTION_TYP:
        raise rejected
    registered = active_key(header.get("kid"))
    if registered is None:
        raise rejected
    public_key, deployment = registered
    try:
        signature = _unb64(parts[2])
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        claims = json.loads(_unb64(parts[1]))
    except (ValueError, binascii.Error, InvalidSignature, RecursionError):
        raise rejected from None
    if not isinstance(claims, dict) or set(claims) != ASSERTION_CLAIMS:
        raise rejected
    iat, exp = claims["iat"], claims["exp"]
    if not all(type(v) is int for v in (iat, exp)) or not all(
        isinstance(claims[k], str) for k in ("iss", "aud", "htu", "htm", "jti")
    ):
        raise rejected
    if (
        claims["iss"] != deployment
        or claims["aud"] != AUDIENCE
        or claims["htu"] != SIGN_ROUTE
        or claims["htm"] != "POST"
        or not 0 < exp - iat <= ASSERTION_LIFE
        or iat > now + IAT_AHEAD
        or exp <= now
        or not _canonical_jti(claims["jti"])
        or claims["bodyhash"] != hashlib.sha256(raw_body).hexdigest()
    ):
        raise rejected
    # Spent only after the signature verified, in the store every replica
    # shares, so a replay through another replica is refused too.
    try:
        fresh = DurableNonceStore().consume(
            NONCE_AUDIENCE, NONCE_PREFIX + claims["jti"], iat, exp, now
        )
    except DatabaseError:
        raise SignRefused(503, "signer_unavailable") from None
    if not fresh:
        raise rejected
    return deployment, header["kid"]


def _sign_body(raw_body):
    invalid = SignRefused(400, "sign_request_invalid")
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise invalid from None
    if not isinstance(body, dict) or set(body) != BODY_KEYS:
        raise invalid
    if not all(isinstance(body[k], str) for k in BODY_KEYS):
        raise invalid
    if not _HEX64.fullmatch(body["body_sha256"]) or not body["model"]:
        raise invalid
    return body


def _authorization_id(token):
    try:
        version, raw, _ = token.split(".")
        lines = _unb64(raw).decode("utf-8").split("\n")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return lines[9] if version == "fe1r" and len(lines) == 21 else None


def _run_authorization(token, deployment, now):
    rejected = SignRefused(401, "authorization_rejected")
    jti = _authorization_id(token)
    row = (
        ServiceGrant.objects.filter(pk=jti, kind="console-run-auth").first()
        if jti
        else None
    )
    if row is None:
        raise rejected
    # Authenticity first (MAC, structure, the row's claims, the deployment), so
    # a tampered or foreign token never learns whether a row is revoked or
    # expired: checked at the row's own iat and as if live.
    try:
        claims = verify_grant(
            token,
            keys=pair_keys("fa", AUDIENCE),
            audience=AUDIENCE,
            row={**row.registry_row(), "revoked_at": None},
            now=int(row.iat),
        )
    except EnvelopeRejected:
        raise rejected from None
    # Handed to one agent service deployment only.
    if claims["ee"] != deployment:
        raise rejected
    if row.revoked_at is not None:
        raise SignRefused(403, "authorization_revoked")
    if now > int(claims["exp"]):
        raise SignRefused(403, "authorization_expired")
    return claims


def _grant(claims, now):
    """The run's console-run grant: its registered row re-derived while it has
    more than two minutes left, else a new one for the authorization's remaining
    life (at most an hour), never beyond it. The authorization row is locked and
    re-checked first, so a revocation and a mint never interleave."""
    keys = pair_keys("fa", "fa-eval-grant")
    deadline = int(claims["exp"])
    with transaction.atomic():
        authorization = (
            ServiceGrant.objects.select_for_update()
            .filter(pk=claims["id"], kind="console-run-auth")
            .first()
        )
        if authorization is None or authorization.revoked_at is not None:
            raise SignRefused(403, "authorization_revoked")
        current = (
            ServiceGrant.objects.filter(
                kind="console-run",
                sub=claims["sub"],
                org=claims["org"],
                user=claims["user"],
                model_ids=claims["models"],
                revoked_at__isnull=True,
                exp__gt=now + GRANT_REUSE_MARGIN,
                exp__lte=deadline,
            )
            .order_by("-exp")
            .first()
        )
        if current is not None:
            return derive_grant(current.registry_row(), keys=keys), current.exp
        ttl = min(GRANT_TTL, deadline - now)
        if ttl <= 0:
            raise SignRefused(403, "authorization_expired")
        token, _, row = issue_person_grant(
            kind="console-run",
            org=claims["org"],
            user=claims["user"],
            workspace=None if claims["workspace"] == "-" else claims["workspace"],
            sub=claims["sub"],
            ttl=ttl,
            models=claims["models"].split(","),
            now=now,
        )
    return token, row.exp


def sign_console_run(request, now=None):
    """{"grant", "grant_exp", "envelope", "envelope_exp"} for one gateway call,
    or SignRefused."""
    now = int(time.time() if now is None else now)
    decision = {"code": "signed"}
    try:
        return _sign(request, now, decision)
    except DatabaseError:
        # The key registry, the jti store or the grant registry could not be
        # read or written: fail closed with the typed retryable refusal.
        decision["code"] = "signer_unavailable"
        audit.info("console_run_sign", **decision)
        raise SignRefused(503, "signer_unavailable") from None


def _sign(request, now, decision):
    try:
        if not console_run_enabled():
            raise SignRefused(403, "console_run_disabled")
        raw_body = request.body
        if len(raw_body) > MAX_BODY_BYTES:
            raise SignRefused(400, "sign_request_invalid")
        deployment, kid = _assertion_deployment(
            request.headers.get(ASSERTION_HEADER), raw_body, now
        )
        decision.update(kid=kid, deployment=deployment)
        body = _sign_body(raw_body)
        decision["body_sha256"] = body["body_sha256"]
        claims = _run_authorization(body["run_authorization"], deployment, now)
        decision.update(run=claims["sub"], org_id=claims["org"], user_id=claims["user"])
        try:
            live_person(claims["org"], claims["user"], claims["workspace"])
        except EnvelopeRejected:
            raise SignRefused(403, "member_removed") from None
        if body["route"] != CHAT_ROUTE or body["method"] != "POST":
            raise SignRefused(403, "route_rejected")
        if body["model"] not in claims["models"].split(","):
            raise SignRefused(403, "model_not_in_cap")
        grant, grant_exp = _grant(claims, now)
        kid_now = current_kid()
        try:
            gateway_keys = pair_keys("fa", "gw-delegated")
        except EnvelopeRejected:
            raise SignRefused(503, "signer_unavailable") from None
        grant_id = _grant_id(grant)
        envelope = sign_request(
            keys=gateway_keys,
            kid=kid_now,
            issuer="fa",
            audience="gw-delegated",
            method="POST",
            route=CHAT_ROUTE,
            body=None,
            body_sha256=body["body_sha256"],
            org=claims["org"],
            workspace=claims["workspace"],
            user=claims["user"],
            via="grant:" + grant_id,
            scope="console-run",
            grant=grant,
            now=now,
        )
    except SignRefused as refusal:
        decision["code"] = refusal.code
        audit.info("console_run_sign", **decision)
        raise
    audit.info("console_run_sign", **decision)
    return {
        "grant": grant,
        "grant_exp": int(grant_exp),
        "envelope": envelope,
        "envelope_exp": now + ENVELOPE_LIFE,
    }


def _grant_id(grant):
    _, raw, _ = grant.split(".")
    return _unb64(raw).decode("utf-8").split("\n")[9]

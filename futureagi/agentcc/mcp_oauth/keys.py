"""Access-token signing keys: ES256 (P-256), the private half stored encrypted
(``enc::``, the credential manager), the public half published.

``rotate`` makes a new active key and keeps the previous one published as
``retiring`` (so tokens it signed in the last 15 minutes still verify); the
one before that is retired. Every change pushes ``mcp-oauth-keys-push-v1`` to
the gateway; the version is the newest key's row id, so it grows with every
change. A push that fails never stops a sign-in: tokens are still issued,
the push is retried on the next issue after a short pause, and
``manage.py agentcc_mcp_oauth_keys --push`` repeats it on demand.
"""

import json
import secrets

import jwt
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from jwt.algorithms import ECAlgorithm

from agentcc.mcp_oauth import config
from agentcc.models.mcp_oauth import AgentccMCPOAuthSigningKey
from agentcc.services.credential_manager import decrypt_token, encrypt_token

logger = structlog.get_logger(__name__)

Status = AgentccMCPOAuthSigningKey.Status
_PUSHED_KEY = "agentcc:mcpoauth:keys-pushed:{}"
_PUSH_PAUSE_KEY = "agentcc:mcpoauth:keys-push-pause"
PUSHED_TTL_SECONDS = 300
PUSH_PAUSE_SECONDS = 60
PRIVATE_MEMBERS = {"d", "p", "q", "dp", "dq", "qi", "k", "oth"}


def _public_jwk(private_key, kid: str) -> dict:
    jwk = ECAlgorithm.to_jwk(private_key.public_key())
    jwk = json.loads(jwk) if isinstance(jwk, str) else dict(jwk)
    jwk = {k: v for k, v in jwk.items() if k not in PRIVATE_MEMBERS}
    jwk.update({"kid": kid, "use": "sig", "alg": config.SIGNING_ALG})
    return jwk


def create_key(status=Status.ACTIVE) -> AgentccMCPOAuthSigningKey:
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    kid = f"as-{timezone.now():%Y%m%d}-{secrets.token_hex(6)}"
    return AgentccMCPOAuthSigningKey.objects.create(
        kid=kid,
        alg=config.SIGNING_ALG,
        encrypted_private=encrypt_token(pem),
        public_jwk=_public_jwk(private_key, kid),
        status=status,
    )


def active_key() -> AgentccMCPOAuthSigningKey | None:
    return (
        AgentccMCPOAuthSigningKey.objects.filter(status=Status.ACTIVE)
        .order_by("-created_at", "-id")
        .first()
    )


def ensure_active_key() -> AgentccMCPOAuthSigningKey:
    key = active_key()
    if key is not None:
        return key
    key = create_key()
    push(raise_errors=False)
    return key


def rotate() -> AgentccMCPOAuthSigningKey:
    with transaction.atomic():
        AgentccMCPOAuthSigningKey.objects.select_for_update().filter(
            status=Status.RETIRING
        ).update(status=Status.RETIRED)
        AgentccMCPOAuthSigningKey.objects.select_for_update().filter(
            status=Status.ACTIVE
        ).update(status=Status.RETIRING)
        key = create_key()
    push(raise_errors=False)
    return key


def published() -> list[AgentccMCPOAuthSigningKey]:
    return list(
        AgentccMCPOAuthSigningKey.objects.filter(
            status__in=[Status.ACTIVE, Status.RETIRING]
        ).order_by("-created_at", "-id")
    )


def jwks() -> dict:
    return {"keys": [dict(row.public_jwk) for row in published()]}


def version() -> int:
    return AgentccMCPOAuthSigningKey.objects.aggregate(v=Max("id"))["v"] or 0


def push_payload() -> dict:
    return {"issuer": config.issuer(), "version": version(), **jwks()}


def sign(claims: dict) -> str:
    key = ensure_active_key()
    private_key = serialization.load_pem_private_key(
        decrypt_token(key.encrypted_private).encode(), password=None
    )
    return jwt.encode(
        claims, private_key, algorithm=config.SIGNING_ALG, headers={"kid": key.kid}
    )


def push(*, raise_errors=True) -> bool:
    """Send the published keys to the gateway. Returns whether it took them."""
    from agentcc.services.gateway_admin import mcp_oauth as gateway_keys
    from agentcc.services.gateway_client import GatewayClientError

    payload = push_payload()
    if not payload["keys"]:
        return False
    try:
        gateway_keys.push_keys(payload)
    except GatewayClientError as e:
        cache.set(_PUSH_PAUSE_KEY, 1, PUSH_PAUSE_SECONDS)
        logger.warning(
            "mcp_oauth_keys_push_failed",
            version=payload["version"],
            status_code=getattr(e, "status_code", None),
        )
        if raise_errors:
            raise
        return False
    cache.set(_PUSHED_KEY.format(payload["version"]), 1, PUSHED_TTL_SECONDS)
    cache.delete(_PUSH_PAUSE_KEY)
    logger.info("mcp_oauth_keys_pushed", version=payload["version"])
    return True


def push_if_due() -> None:
    """Re-send the keys at most every few minutes from the sign-in path, so a
    gateway that restarted without them gets them back."""
    if cache.get(_PUSH_PAUSE_KEY) or cache.get(_PUSHED_KEY.format(version())):
        return
    push(raise_errors=False)
